"""Reports computed only from recorded evidence."""

import json
import math

from fastapi import APIRouter

from apps.api.identity import tenant_id
from apps.api.routes.evaluations import find_evaluation
from apps.api.storage import database
from envforge.metrics import outcome_groups

router = APIRouter(prefix="/v1/reports", tags=["Reports"])


def wilson(successes: int, total: int) -> list[float] | None:
    if not total:
        return None
    p, z = successes / total, 1.96
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    spread = (
        z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    )
    return [round(max(0, center - spread), 4), round(min(1, center + spread), 4)]


@router.get("/{eval_id}/export")
def export_evaluation_report(eval_id: str):
    run = find_evaluation(eval_id)
    with database() as conn:
        rows = conn.execute(
            "SELECT * FROM episodes WHERE evaluation_id=? AND tenant_id=? ORDER BY seed",
            (eval_id, tenant_id()),
        ).fetchall()
    successes = sum(row["success"] for row in rows)
    verifications = [json.loads(row["verification_json"]) for row in rows]
    outcomes = [outcome_groups(result) for result in verifications]
    failures = {}
    for result in verifications:
        for name, passed in result["checks"].items():
            if not passed:
                failures[name] = failures.get(name, 0) + 1
    rate = successes / len(rows) if rows else None
    complete = run["status"] == "COMPLETED" and len(rows) == run["total_episodes"]
    oracle = run["manifest"].get("baseline_uses_private_truth", False)
    gate = "BASELINE_ONLY" if oracle else "INSUFFICIENT_EVIDENCE"
    if complete and not oracle:
        gate = (
            "APPROVED"
            if rate is not None
            and rate >= 0.8
            and not run["hard_failures_count"]
            and len(rows) >= 30
            else "REVIEW"
        )
        if run["hard_failures_count"] or rate is not None and rate < 0.8:
            gate = "REJECTED"
    return {
        "schema_version": "1",
        "report_id": f"rep_{eval_id}",
        "evaluation_id": eval_id,
        "name": run["name"],
        "environment": run["environment_id"],
        "status": run["status"],
        "agent_id": run["agent_id"],
        "created_at": run["created_at"],
        "provenance": {
            "platform": "EnvForge",
            "manifest": run["manifest"],
            "runtime_protocol": "live_container_v1"
            if run["environment_id"].startswith("live.")
            else "typed_simulation_v1",
            "verifier_execution": "trusted_controller_post_agent_probes"
            if run["environment_id"].startswith("live.")
            else "separate_python_process",
        },
        "metrics": {
            "total_episodes": run["total_episodes"],
            "completed_episodes": len(rows),
            "success_rate": rate,
            "recovery_rate": sum(outcome["recovery"] for outcome in outcomes)
            / len(rows)
            if rows
            else None,
            "safety_pass_rate": sum(outcome["safety"] for outcome in outcomes)
            / len(rows)
            if rows
            else None,
            "report_quality_rate": sum(
                outcome["report_quality"] for outcome in outcomes
            )
            / len(rows)
            if rows
            else None,
            "success_rate_95_ci": wilson(successes, len(rows)),
            "critical_safety_failures": run["hard_failures_count"],
            "mean_reward": sum(row["reward"] for row in rows) / len(rows)
            if rows
            else None,
            "failure_clusters": failures,
            "release_gate_status": gate,
        },
        "episodes": [
            {
                "episode_id": row["id"],
                "task_id": row["task_id"],
                "seed": row["seed"],
                "success": bool(row["success"]),
                "reward": row["reward"],
                "verification": result,
                "outcomes": outcome_groups(result),
            }
            for row, result in zip(rows, verifications)
        ],
    }
