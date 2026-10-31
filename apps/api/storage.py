"""Portable SQLite storage with idempotent schema migrations and durable jobs."""

import sqlite3
import threading
from contextlib import contextmanager

from apps.api.identity import identity
from apps.api.settings import database_path, database_url, saas_mode

_migration_lock = threading.Lock()
_initialized: set[str] = set()


def _connect():
    if database_url():
        from apps.api.postgres import Connection

        return Connection(database_url())
    path = database_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    return conn


def init_db() -> None:
    key = database_url() or str(database_path())
    with _migration_lock:
        if key in _initialized:
            return
        conn = _connect()
        try:
            if not database_url():
                conn.execute("PRAGMA journal_mode=WAL")
            with conn:
                schema = """
                    CREATE TABLE IF NOT EXISTS agents (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, adapter TEXT NOT NULL,
                        config_json TEXT NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS evaluations (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, environment_id TEXT NOT NULL,
                        suite TEXT NOT NULL, tier TEXT NOT NULL, agent_id TEXT NOT NULL,
                        status TEXT NOT NULL, total_episodes INTEGER NOT NULL,
                        completed_episodes INTEGER NOT NULL, success_count INTEGER NOT NULL,
                        hard_failures_count INTEGER NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS episodes (
                        id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
                        task_id TEXT NOT NULL, seed INTEGER NOT NULL, status TEXT NOT NULL,
                        success INTEGER NOT NULL, reward REAL NOT NULL, trajectory_json TEXT NOT NULL,
                        verification_json TEXT NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE INDEX IF NOT EXISTS episodes_evaluation ON episodes(evaluation_id);
                    CREATE TABLE IF NOT EXISTS audit_events (
                        id INTEGER PRIMARY KEY, event TEXT NOT NULL, resource_id TEXT NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS workspaces (
                        id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at REAL NOT NULL,
                        daily_model_calls INTEGER NOT NULL DEFAULT 500, disabled INTEGER NOT NULL DEFAULT 0
                    );
                    CREATE TABLE IF NOT EXISTS access_keys (
                        id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES workspaces(id),
                        name TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','evaluator','viewer')),
                        token_hash TEXT NOT NULL UNIQUE, created_at REAL NOT NULL, expires_at REAL NOT NULL,
                        revoked_at REAL, last_used_at REAL
                    );
                    CREATE TABLE IF NOT EXISTS browser_sessions (
                        token_hash TEXT PRIMARY KEY, key_id TEXT NOT NULL REFERENCES access_keys(id),
                        csrf TEXT NOT NULL, expires_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS agent_secrets (
                        agent_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                        ciphertext TEXT NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS usage_daily (
                        tenant_id TEXT NOT NULL, day TEXT NOT NULL, model_calls INTEGER NOT NULL DEFAULT 0,
                        prompt_tokens INTEGER NOT NULL DEFAULT 0, completion_tokens INTEGER NOT NULL DEFAULT 0,
                        PRIMARY KEY(tenant_id,day)
                    );
                    CREATE TABLE IF NOT EXISTS worker_heartbeats (
                        id TEXT PRIMARY KEY, last_seen REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS evaluation_attempts (
                        id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
                        tenant_id TEXT NOT NULL, seed INTEGER, status TEXT NOT NULL,
                        error TEXT, trace_json TEXT NOT NULL, created_at REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS provider_cooldowns (
                        bucket TEXT PRIMARY KEY, next_allowed REAL NOT NULL
                    );
                    CREATE TABLE IF NOT EXISTS request_windows (
                        bucket TEXT PRIMARY KEY, window_start REAL NOT NULL, requests INTEGER NOT NULL
                    );
                """
                conn.execute("BEGIN IMMEDIATE")
                if database_url():
                    schema = schema.replace(
                        "id INTEGER PRIMARY KEY, event",
                        "id BIGSERIAL PRIMARY KEY, event",
                    ).replace(" REAL", " DOUBLE PRECISION")
                for statement in schema.split(";"):
                    if statement.strip():
                        conn.execute(statement)
                migrations = {
                    "evaluations": {
                        "manifest_json": "TEXT NOT NULL DEFAULT '{}'",
                        "error": "TEXT",
                        "worker_id": "TEXT",
                        "lease_until": "REAL",
                        "retry_count": "INTEGER NOT NULL DEFAULT 0",
                        "next_attempt_at": "REAL NOT NULL DEFAULT 0",
                        "checkpoint_json": "TEXT",
                        "created_by": "TEXT NOT NULL DEFAULT 'local'",
                    }
                }
                migrations["workspaces"] = {
                    "daily_episodes": "INTEGER NOT NULL DEFAULT 500"
                }
                migrations["usage_daily"] = {"episodes": "INTEGER NOT NULL DEFAULT 0"}
                for table in ("agents", "evaluations", "episodes", "audit_events"):
                    migrations.setdefault(table, {})["tenant_id"] = (
                        "TEXT NOT NULL DEFAULT 'local'"
                    )
                migrations["audit_events"]["actor_id"] = "TEXT NOT NULL DEFAULT 'local'"
                for table, additions in migrations.items():
                    columns = (
                        set()
                        if database_url()
                        else {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                    )
                    for name, definition in additions.items():
                        if database_url():
                            conn.execute(
                                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {definition.replace('REAL', 'DOUBLE PRECISION')}"
                            )
                        elif name not in columns:
                            conn.execute(
                                f"ALTER TABLE {table} ADD COLUMN {name} {definition}"
                            )
                conn.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS episodes_seed ON episodes(evaluation_id,seed)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS evaluations_tenant ON evaluations(tenant_id,created_at)"
                )
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS agents_tenant ON agents(tenant_id,created_at)"
                )
                conn.execute(
                    "INSERT INTO workspaces(id,name,created_at) VALUES ('local','Local workspace',0) ON CONFLICT(id) DO NOTHING"
                )
                if database_url() and saas_mode():
                    if not conn.execute(
                        "SELECT 1 FROM pg_roles WHERE rolname='envforge_request'"
                    ).fetchone():
                        raise RuntimeError(
                            "Run the PostgreSQL bootstrap before starting SaaS mode"
                        )
                    permissions = {
                        "workspaces": "SELECT",
                        "agents": "SELECT,INSERT,UPDATE",
                        "evaluations": "SELECT,INSERT,UPDATE",
                        "episodes": "SELECT",
                        "audit_events": "SELECT,INSERT",
                        "access_keys": "SELECT,INSERT,UPDATE",
                        "agent_secrets": "SELECT,INSERT",
                        "usage_daily": "SELECT,INSERT,UPDATE",
                        "evaluation_attempts": "SELECT",
                    }
                    conn.execute("GRANT USAGE ON SCHEMA public TO envforge_request")
                    conn.execute(
                        "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO envforge_request"
                    )
                    for table, grants in permissions.items():
                        column = "id" if table == "workspaces" else "tenant_id"
                        conn.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
                        conn.execute(f"GRANT {grants} ON {table} TO envforge_request")
                        conn.execute(
                            f"DROP POLICY IF EXISTS workspace_isolation ON {table}"
                        )
                        conn.execute(
                            f"CREATE POLICY workspace_isolation ON {table} USING ({column}=current_setting('envforge.tenant_id',true)) WITH CHECK ({column}=current_setting('envforge.tenant_id',true))"
                        )
                if not database_url():
                    conn.execute("PRAGMA user_version=2")
                conn.execute("""UPDATE evaluations SET status='INFRA_FAILED', error='Run has no worker lease; start a new evaluation'
                                WHERE status='RUNNING' AND lease_until IS NULL""")
        finally:
            conn.close()
        _initialized.add(key)


def get_db_connection():
    init_db()
    return _connect()


@contextmanager
def database():
    conn = get_db_connection()
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def audit(
    conn,
    event: str,
    resource_id: str,
    *,
    tenant: str | None = None,
    actor: str | None = None,
) -> None:
    import time

    user = identity.get()
    conn.execute(
        "INSERT INTO audit_events(event,resource_id,created_at,tenant_id,actor_id) VALUES (?,?,?,?,?)",
        (
            event,
            resource_id,
            time.time(),
            tenant or user.tenant_id,
            actor or user.actor_id,
        ),
    )
