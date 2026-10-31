"""Durable retry scheduling, owner fencing, checkpoint integrity and worker readiness."""

import json
import math
import threading
import time
import uuid
from contextlib import contextmanager
from email.utils import parsedate_to_datetime

import httpx

from apps.api.storage import audit, database
from apps.api.usage import AllowanceExceeded
from envforge.gemini_runner import GeminiProviderError
from envforge.protocol.integrity import digest
from verifiers.v1.utils.retries import backoff

MAX_RETRIES = 3
LEASE_SECONDS = 120


class LeaseLost(RuntimeError):
    """Another owner or cancellation fenced this worker out."""


def save_checkpoint(eval_id: str, owner: str, checkpoint: dict | None) -> None:
    envelope = None
    if checkpoint is not None:
        envelope = json.dumps({"data": checkpoint, "digest": digest(checkpoint)})
    with database() as conn:
        changed = conn.execute(
            "UPDATE evaluations SET checkpoint_json=?,lease_until=?,updated_at=? WHERE id=? AND worker_id=? AND status='RUNNING'",
            (envelope, time.time() + LEASE_SECONDS, time.time(), eval_id, owner),
        ).rowcount
        if not changed:
            raise LeaseLost("Evaluation ownership was lost")


def load_checkpoint(value: str | None, manifest_digest: str) -> dict | None:
    if not value:
        return None
    envelope = json.loads(value)
    checkpoint = envelope["data"]
    if (
        digest(checkpoint) != envelope["digest"]
        or checkpoint["manifest_digest"] != manifest_digest
    ):
        raise ValueError("Checkpoint integrity or manifest mismatch")
    return checkpoint


def retry_delay(error: Exception, retry_count: int) -> float | None:
    if retry_count >= MAX_RETRIES:
        return None
    code = (
        error.status_code
        if isinstance(error, GeminiProviderError)
        else error.response.status_code
        if isinstance(error, httpx.HTTPStatusError)
        else None
    )
    retryable = isinstance(
        error, (httpx.TimeoutException, httpx.ConnectError, TimeoutError)
    ) or code in {429, 500, 502, 503, 504}
    if not retryable:
        return None
    delay = max(60 if code == 429 else 1, backoff(retry_count))
    hint = getattr(error, "retry_after", None)
    if isinstance(error, httpx.HTTPStatusError):
        raw_hint = error.response.headers.get("retry-after")
        if raw_hint:
            try:
                hint = float(raw_hint)
            except ValueError:
                try:
                    hint = max(
                        0, parsedate_to_datetime(raw_hint).timestamp() - time.time()
                    )
                except (ValueError, TypeError, OverflowError):
                    hint = None
    if hint is not None:
        if not math.isfinite(hint) or hint > 900:
            return None
        delay = max(delay, hint)
    return delay


def safe_error(error: Exception) -> str:
    if isinstance(error, AllowanceExceeded):
        return str(error)
    if isinstance(error, GeminiProviderError):
        return str(error)
    if isinstance(error, httpx.HTTPStatusError):
        return f"Agent service returned HTTP {error.response.status_code}; check its availability"
    if isinstance(error, (httpx.TimeoutException, httpx.ConnectError, TimeoutError)):
        return "Agent/provider connection timed out or became unavailable"
    return (
        f"{type(error).__name__}: evaluation interrupted; inspect the attempt history"
    )


def finish_attempt(row, owner: str, error: Exception, checkpoint: dict | None) -> bool:
    delay = retry_delay(error, row["retry_count"])
    if row["environment_id"].startswith("live."):
        delay = (
            None  # Never replay real shell/database side effects after interruption.
        )
    if (
        checkpoint
        and delay is not None
        and time.time() + delay >= checkpoint["deadline"]
    ):
        delay = None
    message = safe_error(error)
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        changed = conn.execute(
            "UPDATE evaluations SET status=?,error=?,retry_count=retry_count+?,next_attempt_at=?,lease_until=NULL,worker_id=CASE WHEN ? THEN NULL ELSE worker_id END,updated_at=? WHERE id=? AND worker_id=? AND status='RUNNING'",
            (
                "RETRY_WAIT" if delay is not None else "INFRA_FAILED",
                message,
                1 if delay is not None else 0,
                time.time() + (delay or 0),
                delay is not None,
                time.time(),
                row["id"],
                owner,
            ),
        ).rowcount
        if not changed:
            return False
        conn.execute(
            "INSERT INTO evaluation_attempts(id,evaluation_id,tenant_id,seed,status,error,trace_json,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                "attempt_" + uuid.uuid4().hex,
                row["id"],
                row["tenant_id"],
                checkpoint.get("seed") if checkpoint else None,
                "RETRY_WAIT" if delay is not None else "INFRA_FAILED",
                message,
                json.dumps(
                    {
                        "scored": False,
                        "actions": checkpoint.get("history", []) if checkpoint else [],
                        "agent_run": checkpoint.get("runner") if checkpoint else None,
                    }
                ),
                time.time(),
            ),
        )
        audit(
            conn,
            "evaluation_retry_scheduled"
            if delay is not None
            else "evaluation_infrastructure_failed",
            row["id"],
            tenant=row["tenant_id"],
            actor="worker",
        )
    return delay is not None


def recover_expired_jobs() -> None:
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM evaluations WHERE status='RUNNING' AND lease_until<?",
            (time.time(),),
        ).fetchall()
        for row in rows:
            retry = row["retry_count"] < MAX_RETRIES and not row[
                "environment_id"
            ].startswith("live.")
            conn.execute(
                "UPDATE evaluations SET status=?,retry_count=retry_count+1,next_attempt_at=?,worker_id=NULL,lease_until=NULL,error=?,updated_at=? WHERE id=? AND status='RUNNING' AND worker_id=?",
                (
                    "RETRY_WAIT" if retry else "INFRA_FAILED",
                    time.time(),
                    "Worker interrupted; resuming from its durable checkpoint"
                    if retry
                    else "Worker recovery attempts exhausted",
                    time.time(),
                    row["id"],
                    row["worker_id"],
                ),
            )
            audit(
                conn,
                "worker_lease_recovered",
                row["id"],
                tenant=row["tenant_id"],
                actor="worker",
            )


@contextmanager
def worker_heartbeat():
    worker = "worker_" + uuid.uuid4().hex
    stop = threading.Event()

    def beat():
        while not stop.is_set():
            try:
                with database() as conn:
                    conn.execute(
                        "DELETE FROM worker_heartbeats WHERE last_seen<?",
                        (time.time() - 300,),
                    )
                    conn.execute(
                        "INSERT INTO worker_heartbeats(id,last_seen) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen",
                        (worker, time.time()),
                    )
            except Exception:  # noqa: BLE001 -- heartbeat failures must degrade readiness, not kill the supervisor
                # Readiness becomes unhealthy until the database is reachable again.
                import logging

                logging.getLogger(__name__).warning(
                    "Worker heartbeat could not reach storage"
                )
            stop.wait(10)

    thread = threading.Thread(target=beat, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=6)
