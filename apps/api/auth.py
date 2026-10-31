"""Workspace access keys, short-lived browser sessions and role enforcement."""

import hashlib
import secrets
import time

from fastapi import HTTPException, Request

from apps.api.identity import Identity
from apps.api.settings import public_origin
from apps.api.storage import audit, database

COOKIE = "__Host-envforge_session"
ROLES = {"viewer": 0, "evaluator": 1, "admin": 2}


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_key(
    conn, tenant: str, name: str, role: str, days: int = 30
) -> tuple[str, str]:
    if role not in ROLES or not 1 <= days <= 90:
        raise ValueError("Invalid key role or lifetime")
    key_id, token = "key_" + secrets.token_hex(16), "ef_" + secrets.token_urlsafe(32)
    now = time.time()
    conn.execute(
        "INSERT INTO access_keys(id,tenant_id,name,role,token_hash,created_at,expires_at) VALUES (?,?,?,?,?,?,?)",
        (key_id, tenant, name, role, token_hash(token), now, now + days * 86400),
    )
    return key_id, token


def key_identity(token: str) -> Identity | None:
    if not token.startswith("ef_") or len(token) > 128:
        return None
    now = time.time()
    with database() as conn:
        row = conn.execute(
            """SELECT k.* FROM access_keys k JOIN workspaces w ON w.id=k.tenant_id
                WHERE k.token_hash=? AND k.revoked_at IS NULL AND k.expires_at>? AND w.disabled=0""",
            (token_hash(token), now),
        ).fetchone()
        if row is None:
            return None
        conn.execute(
            "UPDATE access_keys SET last_used_at=? WHERE id=?", (now, row["id"])
        )
        return Identity(row["tenant_id"], row["id"], row["role"], row["id"])


def session_identity(token: str) -> Identity | None:
    if len(token) > 128:
        return None
    now = time.time()
    with database() as conn:
        row = conn.execute(
            """SELECT s.token_hash,s.csrf,k.id,k.tenant_id,k.role FROM browser_sessions s
                JOIN access_keys k ON k.id=s.key_id JOIN workspaces w ON w.id=k.tenant_id
                WHERE s.token_hash=? AND s.expires_at>? AND k.expires_at>?
                AND k.revoked_at IS NULL AND w.disabled=0""",
            (token_hash(token), now, now),
        ).fetchone()
        if row is None:
            return None
        return Identity(
            row["tenant_id"],
            row["id"],
            row["role"],
            row["id"],
            row["csrf"],
            row["token_hash"],
        )


def authenticate(request: Request) -> Identity:
    header = request.headers.get("Authorization", "")
    user = (
        key_identity(header[7:])
        if header.startswith("Bearer ")
        else session_identity(request.cookies.get(COOKIE, ""))
    )
    if user is None:
        raise HTTPException(401, "Sign in with a valid workspace access key")
    if (
        user.session_hash
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and (
            request.headers.get("origin") != public_origin()
            or not secrets.compare_digest(
                request.headers.get("x-csrf-token", ""), user.csrf
            )
        )
    ):
        raise HTTPException(403, "Invalid session origin or CSRF token")
    required = (
        "viewer"
        if request.method in {"GET", "HEAD", "OPTIONS"}
        or request.url.path in {"/v1/comparisons", "/auth/logout"}
        else "evaluator"
    )
    if request.url.path.startswith("/v1/workspace/keys") and request.method != "GET":
        required = "admin"
    if ROLES.get(user.role, -1) < ROLES[required]:
        raise HTTPException(403, f"The {required} role is required for this action")
    return user


def login_session(token: str) -> tuple[str, Identity]:
    user = key_identity(token)
    if user is None:
        raise HTTPException(401, "Invalid or expired workspace access key")
    session, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with database() as conn:
        conn.execute("DELETE FROM browser_sessions WHERE expires_at<?", (time.time(),))
        conn.execute(
            "INSERT INTO browser_sessions(token_hash,key_id,csrf,expires_at) VALUES (?,?,?,?)",
            (token_hash(session), user.key_id, csrf, time.time() + 8 * 3600),
        )
        audit(
            conn,
            "session_created",
            user.key_id,
            tenant=user.tenant_id,
            actor=user.actor_id,
        )
    return session, Identity(
        user.tenant_id, user.actor_id, user.role, user.key_id, csrf, token_hash(session)
    )
