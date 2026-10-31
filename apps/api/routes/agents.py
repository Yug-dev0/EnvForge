"""Agent registry: real handshake, explicit adapters, server-side credentials."""

import ipaddress
import json
import time
import uuid
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException
from pydantic import Field, SecretStr

from apps.api.agent_connections import AgentUnavailable, probe_agent
from apps.api.identity import tenant_id
from apps.api.secrets import save_credential
from apps.api.settings import saas_mode
from apps.api.storage import audit, database
from envforge.agents import AgentConfig
from envforge.gemini_runner import VERSION as GEMINI_RUNNER_VERSION

router = APIRouter(prefix="/v1/agents", tags=["Agents"])


class AgentCreate(AgentConfig):
    name: str = Field(min_length=1, max_length=160)
    credential: SecretStr | None = Field(default=None, max_length=4096, exclude=True)


def agent_row(row) -> dict:
    return {
        "id": row["id"],
        "name": row["name"],
        "adapter": row["adapter"],
        "config": json.loads(row["config_json"]),
        "created_at": row["created_at"],
    }


@router.post("")
def register_agent(payload: AgentCreate):
    agent_id, now = "agent_" + uuid.uuid4().hex, time.time()
    if payload.credential_ref:
        raise HTTPException(422, "Credential references are assigned by the server")
    if payload.credential and (payload.adapter != "http_endpoint" or payload.token_env):
        raise HTTPException(
            422,
            "An endpoint credential requires an HTTP agent without a server variable",
        )
    if saas_mode():
        if payload.token_env and (
            payload.adapter != "gemini" or payload.token_env != "GEMINI_API_KEY"
        ):
            raise HTTPException(
                422, "Customer agents cannot reference server credential variables"
            )
        if payload.adapter == "http_endpoint":
            target = urlparse(payload.endpoint_url or "")
            try:
                private = ipaddress.ip_address(target.hostname or "").is_private
            except ValueError:
                private = target.hostname in {"localhost", "host.docker.internal"}
            if target.scheme != "https" or private:
                raise HTTPException(
                    422, "SaaS customer endpoints require approved public HTTPS hosts"
                )
    config = payload.model_dump(exclude={"name", "credential"})
    if payload.credential:
        config["credential_ref"] = agent_id
    if payload.adapter == "gemini":
        config["metadata"] = {
            **payload.metadata,
            "runner_version": GEMINI_RUNNER_VERSION,
        }
    with database() as conn:
        if payload.credential:
            try:
                save_credential(
                    conn, agent_id, tenant_id(), payload.credential.get_secret_value()
                )
            except RuntimeError as exc:
                raise HTTPException(503, str(exc)) from None
        conn.execute(
            "INSERT INTO agents(id,name,adapter,config_json,created_at,tenant_id) VALUES (?, ?, ?, ?, ?, ?)",
            (
                agent_id,
                payload.name,
                payload.adapter,
                json.dumps(config),
                now,
                tenant_id(),
            ),
        )
        audit(conn, "agent_registered", agent_id)
    return {
        "id": agent_id,
        "name": payload.name,
        "adapter": payload.adapter,
        "config": config,
        "created_at": now,
    }


@router.get("")
def list_agents(limit: int = 100, offset: int = 0):
    if not 1 <= limit <= 200 or offset < 0:
        raise HTTPException(422, "Invalid pagination")
    with database() as conn:
        return [
            agent_row(row)
            for row in conn.execute(
                "SELECT * FROM agents WHERE tenant_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (tenant_id(), limit, offset),
            )
        ]


@router.post("/{agent_id}/test-connection")
def test_agent_connection(agent_id: str):
    with database() as conn:
        row = conn.execute(
            "SELECT * FROM agents WHERE id=? AND tenant_id=?", (agent_id, tenant_id())
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Agent not found")
    config = AgentConfig.model_validate_json(row["config_json"])
    start = time.monotonic()
    try:
        result = probe_agent(config)
    except AgentUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    if "runtime" in result:
        config.metadata["runtime"] = result["runtime"]
        with database() as conn:
            conn.execute(
                "UPDATE agents SET config_json=? WHERE id=? AND tenant_id=?",
                (config.model_dump_json(), agent_id, tenant_id()),
            )
    return {
        "agent_id": agent_id,
        "status": "healthy",
        "adapter": config.adapter,
        "roundtrip_latency_ms": round((time.monotonic() - start) * 1000, 2),
    }
