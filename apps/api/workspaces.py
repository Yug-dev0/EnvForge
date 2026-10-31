"""Operator data lifecycle: private exports and explicit deletion of disabled tenants."""

import json
import os
from pathlib import Path

from apps.api.settings import database_url
from apps.api.storage import audit, database

_DATA_TABLES = (
    "agents",
    "evaluations",
    "episodes",
    "evaluation_attempts",
    "usage_daily",
    "audit_events",
)


def export_workspace(tenant: str, destination: Path) -> None:
    """Export a consistent snapshot, excluding credentials, keys and sessions."""
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w") as output, database() as conn:
            conn.execute(
                "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
                if database_url()
                else "BEGIN IMMEDIATE"
            )
            workspace = conn.execute(
                "SELECT id,name,created_at FROM workspaces WHERE id=?", (tenant,)
            ).fetchone()
            if workspace is None:
                raise ValueError("Workspace does not exist")
            output.write('{"schema_version":"1","workspace":')
            json.dump(dict(workspace), output)
            for table in _DATA_TABLES:
                output.write(',"' + table + '":[')
                for index, row in enumerate(
                    conn.execute(f"SELECT * FROM {table} WHERE tenant_id=?", (tenant,))
                ):
                    if index:
                        output.write(",")
                    json.dump(dict(row), output)
                output.write("]")
            output.write("}\n")
            output.flush()
            os.fsync(output.fileno())
        with database() as conn:
            audit(conn, "workspace_exported", tenant, tenant=tenant, actor="operator")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise


def delete_workspace(tenant: str, confirmation: str | None = None) -> dict:
    """Default to a dry run. Real deletion requires a disabled tenant and exact ID."""
    if tenant == "local":
        raise ValueError("The local operator workspace cannot be deleted")
    with database() as conn:
        conn.execute("BEGIN IMMEDIATE")
        workspace = conn.execute(
            "SELECT disabled FROM workspaces WHERE id=?", (tenant,)
        ).fetchone()
        if workspace is None:
            raise ValueError("Workspace does not exist")
        tables = (*_DATA_TABLES, "access_keys", "agent_secrets")
        counts = {
            table: conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE tenant_id=?", (tenant,)
            ).fetchone()[0]
            for table in tables
        }
        counts["browser_sessions"] = conn.execute(
            "SELECT COUNT(*) FROM browser_sessions WHERE key_id IN (SELECT id FROM access_keys WHERE tenant_id=?)",
            (tenant,),
        ).fetchone()[0]
        if confirmation is None:
            return {"workspace_id": tenant, "dry_run": True, "rows": counts}
        if confirmation != tenant or not workspace["disabled"]:
            raise ValueError("Disable the workspace first and confirm its exact ID")
        conn.execute(
            "DELETE FROM browser_sessions WHERE key_id IN (SELECT id FROM access_keys WHERE tenant_id=?)",
            (tenant,),
        )
        for table in (
            "evaluation_attempts",
            "episodes",
            "evaluations",
            "agent_secrets",
            "agents",
            "usage_daily",
            "access_keys",
            "audit_events",
        ):
            conn.execute(f"DELETE FROM {table} WHERE tenant_id=?", (tenant,))
        conn.execute("DELETE FROM workspaces WHERE id=?", (tenant,))
        # Retain only an opaque workspace ID in the operator's deletion receipt.
        audit(conn, "workspace_deleted", tenant, tenant="local", actor="operator")
        return {"workspace_id": tenant, "dry_run": False, "rows": counts}
