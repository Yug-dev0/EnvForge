"""Workspace sign-in, access-key management and tenant audit records."""

import time
from datetime import UTC

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from apps.api.auth import COOKIE, authenticate, create_access_key, login_session
from apps.api.identity import identity, tenant_id
from apps.api.settings import public_origin, saas_mode
from apps.api.storage import audit, database
from apps.api.usage import request_allowed

auth_router = APIRouter(prefix="/auth", tags=["Account"])
router = APIRouter(prefix="/v1/workspace", tags=["Workspace"])


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    access_key: SecretStr = Field(min_length=1, max_length=128)


class NewKey(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(pattern="^(viewer|evaluator|admin)$")
    expires_in_days: int = Field(default=30, ge=1, le=90)


@auth_router.get("/config")
def configuration():
    return {
        "mode": "saas" if saas_mode() else "local",
        "onboarding": "operator_invitation",
    }


@auth_router.get("/me")
def me(request: Request):
    user = authenticate(request) if saas_mode() else identity.get()
    with database() as conn:
        workspace = conn.execute(
            "SELECT name FROM workspaces WHERE id=?", (user.tenant_id,)
        ).fetchone()
    return {
        "mode": "saas" if saas_mode() else "local",
        "workspace": user.tenant_id,
        "workspace_name": workspace["name"] if workspace else user.tenant_id,
        "role": user.role,
        "csrf_token": user.csrf,
    }


@auth_router.post("/session")
def login(payload: Login, request: Request, response: Response):
    if not saas_mode():
        raise HTTPException(404, "Workspace sessions are available in SaaS mode")
    if request.headers.get("origin") != public_origin():
        raise HTTPException(403, "Sign-in origin is not allowed")
    if not request_allowed(
        "login:" + (request.client.host if request.client else "unknown"), 20
    ):
        raise HTTPException(429, "Too many sign-in attempts; wait before trying again")
    token, _ = login_session(payload.access_key.get_secret_value())
    response.set_cookie(
        COOKIE,
        token,
        httponly=True,
        secure=True,
        samesite="strict",
        max_age=8 * 3600,
        path="/",
    )
    return {"status": "signed_in"}


@auth_router.post("/logout")
def logout(request: Request, response: Response):
    user = authenticate(request)
    with database() as conn:
        conn.execute(
            "DELETE FROM browser_sessions WHERE token_hash=?", (user.session_hash,)
        )
        audit(
            conn,
            "session_revoked",
            user.key_id,
            tenant=user.tenant_id,
            actor=user.actor_id,
        )
    response.delete_cookie(
        COOKIE, secure=True, httponly=True, samesite="strict", path="/"
    )
    return {"status": "signed_out"}


@router.get("/keys")
def list_keys():
    if identity.get().role != "admin":
        raise HTTPException(403, "Administrator access is required")
    with database() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT id,name,role,created_at,expires_at,revoked_at,last_used_at FROM access_keys WHERE tenant_id=? ORDER BY created_at DESC",
                (tenant_id(),),
            )
        ]


@router.post("/keys")
def issue_key(payload: NewKey):
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if (
            conn.execute(
                "SELECT COUNT(*) FROM access_keys WHERE tenant_id=? AND revoked_at IS NULL AND expires_at>?",
                (tenant_id(), time.time()),
            ).fetchone()[0]
            >= 100
        ):
            raise HTTPException(
                429,
                "Workspace active access-key limit reached; revoke unused keys first",
            )
        key_id, token = create_access_key(
            conn, tenant_id(), payload.name, payload.role, payload.expires_in_days
        )
        audit(conn, "access_key_created", key_id)
    return {
        "id": key_id,
        "access_key": token,
        "notice": "Copy this key now. It cannot be retrieved again.",
    }


@router.post("/keys/{key_id}/revoke")
def revoke_key(key_id: str):
    with database() as conn:
        changed = conn.execute(
            "UPDATE access_keys SET revoked_at=? WHERE id=? AND tenant_id=? AND revoked_at IS NULL",
            (time.time(), key_id, tenant_id()),
        ).rowcount
        if not changed:
            raise HTTPException(404, "Access key not found")
        audit(conn, "access_key_revoked", key_id)
    return {"status": "revoked"}


@router.get("/audit")
def audit_events():
    if identity.get().role != "admin":
        raise HTTPException(403, "Administrator access is required")
    with database() as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT event,resource_id,created_at,actor_id FROM audit_events WHERE tenant_id=? ORDER BY created_at DESC LIMIT 200",
                (tenant_id(),),
            )
        ]


@router.get("/usage")
def workspace_usage():
    from datetime import datetime

    with database() as conn:
        limits = conn.execute(
            "SELECT daily_model_calls,daily_episodes FROM workspaces WHERE id=?",
            (tenant_id(),),
        ).fetchone()
        usage = conn.execute(
            "SELECT model_calls,prompt_tokens,completion_tokens,episodes FROM usage_daily WHERE tenant_id=? AND day=?",
            (tenant_id(), datetime.now(UTC).date().isoformat()),
        ).fetchone()
    return {
        "limits": dict(limits) if limits else {},
        "usage": dict(usage) if usage else {},
        "period": "UTC calendar day",
    }
