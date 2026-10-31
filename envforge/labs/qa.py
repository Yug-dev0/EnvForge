"""Operator acceptance probes for the opt-in live lab; no model results implied."""

import asyncio
import json
import os

from envforge.labs.postgres import IMAGE, VERSION, command, postgres_lab
from envforge.provenance import implementation_digest
from verifiers.v1.runtimes.backends.docker import cli


async def live_qa() -> dict:
    if os.environ.get("ENVFORGE_MODE") == "saas":
        raise RuntimeError("Live lab QA is local-only")
    inspected = await cli("docker", "image", "inspect", "--format", "{{.Id}}", IMAGE)
    if inspected.exit_code or not inspected.stdout.strip().startswith("sha256:"):
        raise RuntimeError("Build envforge-postgres-lab:local first")
    image = inspected.stdout.strip()
    results: list[dict] = []
    for seed in range(3):
        async with postgres_lab(seed, image) as lab:
            uid = await command(lab.engineer, ["id", "-u"])
            pids = await command(lab.root, ["cat", "/sys/fs/cgroup/pids.max"])
            protected = await command(
                lab.engineer, ["sh", "-c", "printf tamper >> /opt/lab/server.py"]
            )
            database = await command(
                lab.engineer,
                ["psql", "-U", "postgres", "-d", "checkout", "-Atc", "SELECT 1"],
            )
            egress = await command(
                lab.engineer,
                ["curl", "--noproxy", "*", "--max-time", "2", "-s", "http://1.1.1.1/"],
            )
            before = await lab.verify()
            for _ in range(4):
                await lab.step(lab.reference_action())
            positive = await lab.verify()
            original = lab.report["primary_fault"]
            lab.report["primary_fault"] = (
                "wrong_table" if original != "wrong_table" else "wrong_port"
            )
            wrong_report = await lab.verify()
            lab.report["primary_fault"] = original
            browser = next(entry for entry in lab.history if entry["tool"] == "browse")
            recorded = browser["output"]
            browser["output"] = json.dumps(
                {
                    "exit_code": 124,
                    "stdout": "",
                    "stderr": "Synthetic failed-browser negative control",
                }
            )
            failed_browser = await lab.verify()
            browser["output"] = recorded
            checks = {
                "oracle_recovery": positive["success"],
                "unrepaired_rejected": not before["success"],
                "wrong_diagnosis_rejected": not wrong_report["success"],
                "failed_browser_evidence_rejected": not failed_browser["success"],
                "unprivileged_agent": uid["stdout"].strip() == "10001",
                "pids_cap": pids["stdout"].strip() == "256",
                "application_code_protected": protected["exit_code"] != 0,
                "postgres_admin_access_denied": database["exit_code"] != 0,
                "direct_egress_denied": egress["exit_code"] != 0,
            }
            results.append({"seed": seed, "checks": checks, "verification": positive})
    return {
        "schema_version": "1",
        "environment_version": VERSION,
        "implementation_digest": implementation_digest(),
        "runtime_image_digest": image,
        "status": "PASS"
        if all(all(row["checks"].values()) for row in results)
        else "FAIL",
        "reference_episodes": len(results),
        "results": results,
        "limitations": [
            "Reference oracle and negative controls, not a model benchmark",
            "Failed-browser evidence control deliberately edits a captured test trace",
            "Docker is not VM isolation for hostile customers",
            "No live Kubernetes workload or interactive browser automation",
            "Independent external calibration is still pending",
        ],
    }


def run_live_qa() -> dict:
    return asyncio.run(live_qa())
