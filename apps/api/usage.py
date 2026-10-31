"""Atomic workspace allowances and request windows; attempts are charged before dispatch."""

import time
from datetime import UTC, datetime

from apps.api.settings import saas_mode
from apps.api.storage import database
from envforge.agents import AgentProtocolError
from envforge.gemini_runner import GeminiProviderError
from envforge.protocol.integrity import digest


class AllowanceExceeded(RuntimeError):
    """A workspace has exhausted its configured allowance."""


def reserve(conn, tenant: str, *, episodes: int = 0, model_calls: int = 0) -> None:
    if not saas_mode():
        return
    day = datetime.now(UTC).date().isoformat()
    workspace = conn.execute(
        "SELECT daily_model_calls,daily_episodes,disabled FROM workspaces WHERE id=?",
        (tenant,),
    ).fetchone()
    if workspace is None or workspace["disabled"]:
        raise AllowanceExceeded("Workspace is unavailable")
    conn.execute(
        "INSERT INTO usage_daily(tenant_id,day) VALUES (?,?) ON CONFLICT(tenant_id,day) DO NOTHING",
        (tenant, day),
    )
    changed = conn.execute(
        """UPDATE usage_daily SET model_calls=model_calls+?,episodes=episodes+?
        WHERE tenant_id=? AND day=? AND model_calls+?<=? AND episodes+?<=?""",
        (
            model_calls,
            episodes,
            tenant,
            day,
            model_calls,
            workspace["daily_model_calls"],
            episodes,
            workspace["daily_episodes"],
        ),
    ).rowcount
    if not changed:
        raise AllowanceExceeded(
            "Workspace daily allowance reached; contact the workspace operator"
        )


def call_provider(tenant: str, request, *args, **kwargs):
    usage_day = datetime.now(UTC).date().isoformat()
    config = args[0]
    bucket = digest({"model": config.model, "credential_variable": config.token_env})
    with database() as conn:
        cooldown = conn.execute(
            "SELECT next_allowed FROM provider_cooldowns WHERE bucket=?", (bucket,)
        ).fetchone()
        if cooldown and cooldown["next_allowed"] > time.time():
            raise GeminiProviderError(429, cooldown["next_allowed"] - time.time())
        reserve(conn, tenant, model_calls=1)
    try:
        result = request(*args, **kwargs)
    except GeminiProviderError as exc:
        if exc.status_code == 429:
            delay = max(60, min(exc.retry_after or 60, 900))
            due = time.time() + delay
            with database() as conn:
                conn.execute(
                    "INSERT INTO provider_cooldowns(bucket,next_allowed) VALUES (?,?) ON CONFLICT(bucket) DO UPDATE SET next_allowed=CASE WHEN provider_cooldowns.next_allowed>excluded.next_allowed THEN provider_cooldowns.next_allowed ELSE excluded.next_allowed END",
                    (bucket, due),
                )
        raise
    usage = result.get("usage", {})
    if not isinstance(usage, dict):
        raise AgentProtocolError("Provider returned invalid usage metadata")
    prompt, completion = (
        usage.get(key, 0) for key in ("prompt_tokens", "completion_tokens")
    )
    if (
        isinstance(prompt, int)
        and isinstance(completion, int)
        and prompt >= 0
        and completion >= 0
    ):
        with database() as conn:
            conn.execute(
                "UPDATE usage_daily SET prompt_tokens=prompt_tokens+?,completion_tokens=completion_tokens+? WHERE tenant_id=? AND day=?",
                (prompt, completion, tenant, usage_day),
            )
    return result


def request_allowed(bucket: str, maximum: int) -> bool:
    now = time.time()
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM request_windows WHERE window_start<?", (now - 120,))
        row = conn.execute(
            "SELECT window_start,requests FROM request_windows WHERE bucket=?",
            (bucket,),
        ).fetchone()
        if row is None or now - row["window_start"] >= 60:
            conn.execute(
                "INSERT INTO request_windows(bucket,window_start,requests) VALUES (?,?,1) ON CONFLICT(bucket) DO UPDATE SET window_start=excluded.window_start,requests=1",
                (bucket, now),
            )
            return True
        if row["requests"] >= maximum:
            return False
        conn.execute(
            "UPDATE request_windows SET requests=requests+1 WHERE bucket=?", (bucket,)
        )
        return True
