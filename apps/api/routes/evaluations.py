"""Version-pinned evaluation batches backed by a durable queue."""

import json
import os
import time
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from apps.api.agent_connections import AgentUnavailable, probe_agent
from apps.api.identity import identity, tenant_id
from apps.api.jobs import load_checkpoint
from apps.api.runner import run_evaluation_task
from apps.api.settings import saas_mode
from apps.api.storage import audit, database
from apps.api.usage import AllowanceExceeded, reserve
from envforge.agents import AgentConfig
from envforge.labs.postgres import ID as LAB_ID
from envforge.labs.postgres import VERSION as LAB_VERSION
from envforge.private_scenarios import load_private_scenarios
from envforge.protocol.integrity import digest
from envforge.protocol.types import DifficultyTier, SuiteName
from envforge.provenance import implementation_digest
from envforge.worlds.sre.environment import SREIncidentResponseEnvironment
from envforge.worlds.sre.scenarios.generator import SREScenarioGenerator
from envforge.worlds.sre.verifier.verifier import SREVerifier

router = APIRouter(prefix="/v1/evaluations", tags=["Evaluations"])


class EvaluationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="SRE Release Gate Eval", min_length=1, max_length=160)
    environment_id: str = SREIncidentResponseEnvironment.ENVIRONMENT_ID
    environment_version: str = SREIncidentResponseEnvironment.VERSION
    suite: SuiteName = SuiteName.STANDARD
    tier: DifficultyTier = DifficultyTier.EASY
    agent_id: str
    episodes_count: int = Field(default=10, ge=1, le=500)
    base_seed: int = Field(default=42, ge=0, le=2**31 - 501)


def evaluation_row(row) -> dict:
    data = dict(row)
    data["manifest"] = json.loads(data.pop("manifest_json"))
    data.pop("worker_id", None)
    data.pop("lease_until", None)
    data.pop("checkpoint_json", None)
    return data


def find_evaluation(eval_id: str) -> dict:
    with database() as conn:
        row = conn.execute(
            "SELECT * FROM evaluations WHERE id=? AND tenant_id=?",
            (eval_id, tenant_id()),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Evaluation not found")
    return evaluation_row(row)


@router.post("")
def create_evaluation(payload: EvaluationCreate, background_tasks: BackgroundTasks):
    live = payload.environment_id == LAB_ID
    private = (
        load_private_scenarios()
        if payload.suite == SuiteName.HELDOUT and not live
        else None
    )
    if private:
        try:
            for seed in range(
                payload.base_seed, payload.base_seed + payload.episodes_count
            ):
                private.case(seed, payload.tier)
        except ValueError as error:
            raise HTTPException(422, str(error)) from None
    lab_runtime = None
    if live:
        from apps.api.labs import availability

        lab_runtime = availability()
        if not lab_runtime["enabled"]:
            raise HTTPException(503, lab_runtime["reason"])
        if (
            payload.environment_version != LAB_VERSION
            or payload.suite not in {SuiteName.SMOKE, SuiteName.STANDARD}
            or payload.tier != DifficultyTier.EASY
            or payload.episodes_count > 3
        ):
            raise HTTPException(
                422,
                "Live preview supports smoke/standard, easy tier and up to three episodes",
            )
    elif (
        payload.environment_id != SREIncidentResponseEnvironment.ENVIRONMENT_ID
        or payload.environment_version != SREIncidentResponseEnvironment.VERSION
    ):
        raise HTTPException(422, "Environment/version is not installed")
    with database() as conn:
        agent = conn.execute(
            "SELECT * FROM agents WHERE id=? AND tenant_id=?",
            (payload.agent_id, tenant_id()),
        ).fetchone()
    if agent is None:
        raise HTTPException(422, "Register an agent before creating an evaluation")
    config = AgentConfig.model_validate_json(agent["config_json"])
    if live and config.metadata.get("integration"):
        raise HTTPException(
            422,
            "Native SRE bridges use simulator tools; connect an HTTP agent supporting the live lab's tools",
        )
    try:
        readiness = probe_agent(config)
    except AgentUnavailable as exc:
        raise HTTPException(503, str(exc)) from None
    if (
        "runtime" in readiness
        and config.metadata.get("runtime") != readiness["runtime"]
    ):
        raise HTTPException(
            409,
            "Native agent runtime changed or is not pinned. Test its connection before launching a run.",
        )
    now, eval_id = time.time(), "eval_" + uuid.uuid4().hex
    with database() as conn:
        # Serialize admissions to enforce a bounded durable queue even under concurrent requests.
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT COUNT(*) FROM evaluations WHERE status IN ('QUEUED','RUNNING','RETRY_WAIT') AND tenant_id=?",
            (tenant_id(),),
        ).fetchone()[0]
        if active >= 20:
            raise HTTPException(429, "Evaluation queue is full")
        try:
            reserve(conn, tenant_id(), episodes=payload.episodes_count)
        except AllowanceExceeded as exc:
            raise HTTPException(429, str(exc)) from None
        manifest = {
            "schema_version": "1",
            "environment_id": payload.environment_id,
            "environment_version": payload.environment_version,
            "scenario_version": LAB_VERSION if live else SREScenarioGenerator.VERSION,
            "verifier_version": LAB_VERSION if live else SREVerifier.VERSION,
            "implementation_digest": implementation_digest(),
            "suite": payload.suite.value,
            "tier": payload.tier.value,
            "seeds": list(
                range(payload.base_seed, payload.base_seed + payload.episodes_count)
            ),
            "agent_config": json.loads(agent["config_json"]),
            "agent_name": agent["name"],
            "baseline_uses_private_truth": agent["adapter"] == "reference_solver",
            "private_scenario_bundle": private.provenance() if private else None,
            **(
                {
                    "runtime_image_digest": lab_runtime["image_digest"],
                    "execution": "live_container",
                    "replayable": False,
                }
                if lab_runtime
                else {}
            ),
        }
        manifest["digest"] = digest(manifest)
        conn.execute(
            """INSERT INTO evaluations
                        (id,name,environment_id,suite,tier,agent_id,status,total_episodes,completed_episodes,
                         success_count,hard_failures_count,created_at,updated_at,manifest_json,tenant_id,created_by)
                        VALUES (?,?,?,?,?,?,'QUEUED',?,0,0,0,?,?,?,?,?)""",
            (
                eval_id,
                payload.name,
                payload.environment_id,
                payload.suite.value,
                payload.tier.value,
                payload.agent_id,
                payload.episodes_count,
                now,
                now,
                json.dumps(manifest),
                tenant_id(),
                identity.get().actor_id,
            ),
        )
        audit(conn, "evaluation_queued", eval_id)
    if not saas_mode() and os.environ.get("ENVFORGE_EXTERNAL_WORKER") != "1":
        background_tasks.add_task(run_evaluation_task, eval_id)
    return find_evaluation(eval_id)


@router.get("")
def list_evaluations(
    limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0)
):
    with database() as conn:
        return [
            evaluation_row(row)
            for row in conn.execute(
                "SELECT * FROM evaluations WHERE tenant_id=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (tenant_id(), limit, offset),
            )
        ]


@router.get("/{eval_id}")
def get_evaluation(eval_id: str):
    return find_evaluation(eval_id)


@router.post("/{eval_id}/cancel")
def cancel_evaluation(eval_id: str):
    find_evaluation(eval_id)
    with database() as conn:
        conn.execute(
            "UPDATE evaluations SET status='CANCELLED',updated_at=? WHERE id=? AND tenant_id=? AND status IN ('QUEUED','RUNNING','RETRY_WAIT')",
            (time.time(), eval_id, tenant_id()),
        )
        audit(conn, "cancellation_requested", eval_id)
    return find_evaluation(eval_id)


@router.get("/{eval_id}/episodes")
def list_evaluation_episodes(
    eval_id: str, limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)
):
    find_evaluation(eval_id)
    with database() as conn:
        rows = conn.execute(
            "SELECT id,task_id,seed,status,success,reward,created_at FROM episodes WHERE evaluation_id=? AND tenant_id=? ORDER BY seed LIMIT ? OFFSET ?",
            (eval_id, tenant_id(), limit, offset),
        )
        return [{**dict(row), "success": bool(row["success"])} for row in rows]


@router.get("/{eval_id}/attempts")
def list_attempts(eval_id: str):
    find_evaluation(eval_id)
    with database() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,seed,status,error,created_at FROM evaluation_attempts WHERE evaluation_id=? AND tenant_id=? ORDER BY created_at",
                (eval_id, tenant_id()),
            )
        ]


@router.get("/{eval_id}/progress")
def live_progress(
    eval_id: str,
    episode_id: str = Query("", max_length=100),
    after_step: int = Query(0, ge=0, le=10000),
):
    """Public tool events from the durable checkpoint; never a provisional score."""
    run = find_evaluation(eval_id)
    with database() as conn:
        row = conn.execute(
            "SELECT checkpoint_json FROM evaluations WHERE id=? AND tenant_id=?",
            (eval_id, tenant_id()),
        ).fetchone()
    result: dict = {
        "evaluation_id": eval_id,
        "status": run["status"],
        "updated_at": run["updated_at"],
        "scored": False,
        "episode": None,
    }
    try:
        checkpoint = load_checkpoint(row["checkpoint_json"], run["manifest"]["digest"])
    except (ValueError, KeyError, TypeError):
        return {**result, "trace_unavailable": True}
    if not checkpoint:
        return result
    history = checkpoint["history"]
    cursor = after_step if episode_id == checkpoint["episode_id"] else 0
    sources = {
        item["action_id"]: item["source"]
        for item in (checkpoint.get("runner") or {}).get("decisions", [])
    }
    result["episode"] = {
        "id": checkpoint["episode_id"],
        "seed": checkpoint["seed"],
        "started_at": checkpoint["started"],
        "last_step": max((entry["step"] for entry in history), default=0),
        "actions": [
            {
                **{
                    key: entry[key]
                    for key in ("step", "action_id", "tool", "arguments", "output")
                },
                "source": sources.get(
                    entry["action_id"],
                    "reference_oracle"
                    if run["manifest"].get("baseline_uses_private_truth")
                    else "agent",
                ),
            }
            for entry in history
            if entry["step"] > cursor
        ],
        "pending_action": checkpoint.get("planned_action"),
    }
    return result


@router.get("/{eval_id}/attempts/{attempt_id}")
def attempt_details(eval_id: str, attempt_id: str):
    find_evaluation(eval_id)
    with database() as conn:
        row = conn.execute(
            "SELECT trace_json FROM evaluation_attempts WHERE id=? AND evaluation_id=? AND tenant_id=?",
            (attempt_id, eval_id, tenant_id()),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Attempt not found")
    return json.loads(row["trace_json"])
