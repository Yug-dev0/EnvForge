"""Operator-only, out-of-band provisioning. Never exposes an unauthenticated signup API."""

import argparse
import json
import secrets
import time
from pathlib import Path

from apps.api.auth import create_access_key
from apps.api.settings import validate_deployment
from apps.api.storage import audit, database
from apps.api.workspaces import delete_workspace, export_workspace


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subcommands = parser.add_subparsers(dest="command", required=True)
    create = subcommands.add_parser("create-workspace")
    create.add_argument("--name", required=True)
    create.add_argument(
        "--owner", required=True, help="Name for the owner's individual access key"
    )
    issue = subcommands.add_parser(
        "issue-key", help="Recover or rotate an individual owner's key"
    )
    issue.add_argument("--workspace", required=True)
    issue.add_argument("--name", required=True)
    issue.add_argument(
        "--role", choices=("viewer", "evaluator", "admin"), default="admin"
    )
    disable = subcommands.add_parser(
        "disable-workspace",
        help="Immediately block keys, sessions and new provider requests",
    )
    disable.add_argument("--workspace", required=True)
    limits = subcommands.add_parser("set-allowance")
    limits.add_argument("--workspace", required=True)
    limits.add_argument("--episodes", type=int, required=True)
    limits.add_argument("--model-calls", type=int, required=True)
    export = subcommands.add_parser(
        "export-workspace", help="Write a private data export without credentials"
    )
    export.add_argument("--workspace", required=True)
    export.add_argument("--output", type=Path, required=True)
    delete = subcommands.add_parser(
        "delete-workspace",
        help="Preview deletion; requires disabled workspace and exact confirmation to apply",
    )
    delete.add_argument("--workspace", required=True)
    delete.add_argument(
        "--confirm", help="Exact workspace ID; omission performs a dry run"
    )
    args = parser.parse_args()
    validate_deployment()
    if args.command in {"export-workspace", "delete-workspace"}:
        try:
            if args.command == "export-workspace":
                export_workspace(args.workspace, args.output)
                print(
                    json.dumps({"workspace_id": args.workspace, "status": "exported"})
                )
            else:
                print(json.dumps(delete_workspace(args.workspace, args.confirm)))
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        return
    tenant = (
        "ws_" + secrets.token_hex(16)
        if args.command == "create-workspace"
        else args.workspace
    )
    with database() as conn:
        if args.command == "create-workspace":
            conn.execute(
                "INSERT INTO workspaces(id,name,created_at) VALUES (?,?,?)",
                (tenant, args.name, time.time()),
            )
        elif not conn.execute(
            "SELECT id FROM workspaces WHERE id=?", (tenant,)
        ).fetchone():
            parser.error("Workspace does not exist")
        if args.command == "disable-workspace":
            conn.execute("UPDATE workspaces SET disabled=1 WHERE id=?", (tenant,))
            conn.execute(
                "UPDATE evaluations SET status='CANCELLED',worker_id=NULL,lease_until=NULL WHERE tenant_id=? AND status IN ('QUEUED','RUNNING','RETRY_WAIT')",
                (tenant,),
            )
        elif args.command == "set-allowance":
            if (
                not 0 <= args.episodes <= 1000000
                or not 0 <= args.model_calls <= 1000000
            ):
                parser.error("Allowances must be between zero and 1,000,000")
            conn.execute(
                "UPDATE workspaces SET daily_episodes=?,daily_model_calls=? WHERE id=?",
                (args.episodes, args.model_calls, tenant),
            )
        else:
            key_id, token = create_access_key(
                conn,
                tenant,
                args.owner if args.command == "create-workspace" else args.name,
                "admin" if args.command == "create-workspace" else args.role,
                90,
            )
        audit(
            conn,
            args.command.replace("-", "_"),
            tenant,
            tenant=tenant,
            actor="operator",
        )
    if args.command in {"disable-workspace", "set-allowance"}:
        print(json.dumps({"workspace_id": tenant, "status": "updated"}))
        return
    print(
        json.dumps(
            {
                "workspace_id": tenant,
                "owner_key_id": key_id,
                "access_key": token,
                "notice": "Store securely. This key is shown once and expires in 90 days.",
            }
        )
    )


if __name__ == "__main__":
    main()
