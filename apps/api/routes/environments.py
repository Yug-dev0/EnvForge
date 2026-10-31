"""Installed catalog and machine-readable tool protocol."""

import json

from fastapi import APIRouter, HTTPException

from apps.api.settings import database_path
from envforge.labs.postgres import ID as LAB_ID
from envforge.labs.postgres import TOOLS as LAB_TOOLS
from envforge.labs.postgres import VERSION as LAB_VERSION
from envforge.private_scenarios import load_private_scenarios
from envforge.protocol.types import DifficultyTier, FaultFamily, SuiteName
from envforge.provenance import implementation_digest
from envforge.worlds.sre.environment import SREIncidentResponseEnvironment
from envforge.worlds.sre.scenarios.generator import SREScenarioGenerator
from envforge.worlds.sre.tools.dispatcher import tool_definitions

router = APIRouter(prefix="/v1/environments", tags=["Environments"])


@router.get("/{env_id}/qa")
def get_qa(env_id: str):
    require_environment(env_id)
    if env_id == LAB_ID:
        path = database_path().parent / "live-lab-qa.json"
        try:
            report = json.loads(path.read_text())
            if (
                report.get("environment_version") == LAB_VERSION
                and report.get("implementation_digest") == implementation_digest()
            ):
                return report
            return {
                "status": "STALE",
                "limitations": [
                    "Live lab QA does not match the installed implementation"
                ],
            }
        except (OSError, ValueError, AttributeError):
            return {
                "status": "NOT_RUN",
                "limitations": [
                    "Live lab QA and independent external calibration are required"
                ],
            }
    path = database_path().parent / "qa-report.json"
    if not path.exists():
        return {
            "status": "NOT_RUN",
            "limitations": [
                "Run the QA command to create a report",
                "Independent model calibration and human SRE review are pending",
            ],
        }
    try:
        report = json.loads(path.read_text())
    except (ValueError, OSError):
        return {
            "status": "INVALID",
            "limitations": ["The QA artifact cannot be read; rerun QA"],
        }
    if not isinstance(report, dict):
        return {"status": "INVALID", "limitations": ["Invalid QA artifact; rerun QA"]}
    if (
        report.get("environment_version") != SREIncidentResponseEnvironment.VERSION
        or report.get("implementation_digest") != implementation_digest()
    ):
        return {
            "status": "STALE",
            "limitations": ["QA report is for another environment version; rerun QA"],
        }
    visible = {key: value for key, value in report.items() if key != "instances"}
    private = load_private_scenarios()
    if private:
        visible["private_scenario_bundle"] = private.provenance()
        visible["limitations"] = [
            *visible.get("limitations", []),
            "This reference QA report does not establish calibration or coverage for the operator-held private bundle",
        ]
    return visible


def require_environment(env_id: str):
    if env_id not in {SREIncidentResponseEnvironment.ENVIRONMENT_ID, LAB_ID}:
        raise HTTPException(404, "Environment not installed")


@router.get("")
def list_environments():
    from apps.api.labs import availability

    live = availability()
    private = load_private_scenarios()
    return [
        {
            "id": SREIncidentResponseEnvironment.ENVIRONMENT_ID,
            "name": "SRE Incident Response",
            "domain": "sre",
            "version": SREIncidentResponseEnvironment.VERSION,
            "description": "Deterministic Python simulation of checkout, payments, database and cache; typed API actions.",
            "supported_suites": [s.value for s in SuiteName],
            "supported_tiers": [t.value for t in DifficultyTier],
            "distinct_fault_mechanisms": len(FaultFamily),
            "qa_status": "AUTOMATED_QA_AVAILABLE",
            "tools": [tool["name"] for tool in tool_definitions()],
            "execution": "trusted simulation; external HTTP agent; verifier in separate interpreter",
            "limitations": [
                "Simulation tools only; the separate live PostgreSQL lab supports shell and browser execution",
                "No live Kubernetes workload",
                "Operator-held scenario selections configured; underlying fault mechanisms remain public"
                if private
                else "Heldout generator is public; operator-held bundles can be configured",
                "Independent model calibration pending",
            ],
            "private_scenarios": private.provenance() if private else None,
        },
        {
            "id": LAB_ID,
            "name": "Live PostgreSQL Recovery Lab",
            "domain": "sre",
            "version": LAB_VERSION,
            "description": "Disposable PostgreSQL 15 workload, unprivileged shell commands and headless Chromium DOM rendering. Local opt-in preview.",
            "supported_suites": ["smoke", "standard"],
            "supported_tiers": ["easy"],
            "distinct_fault_mechanisms": 3,
            "qa_status": "LOCAL_PREVIEW",
            "tools": [tool["name"] for tool in LAB_TOOLS],
            "execution": "v1 Docker runtime; isolated episode; actual database and Chromium; trusted live probes after agent processes stop",
            "limitations": [
                "Local preview only; public SaaS shell execution is disabled",
                "No live Kubernetes workload or interactive browser control",
                "Docker is not a VM isolation boundary for hostile customer code",
                "Live shell actions are never replayed after worker interruption",
                "Independent model calibration pending",
            ],
            **live,
        },
    ]


@router.get("/{env_id}/versions")
def get_environment_versions(env_id: str):
    require_environment(env_id)
    if env_id == LAB_ID:
        return {
            "environment_id": env_id,
            "latest": LAB_VERSION,
            "versions": [
                {
                    "version": LAB_VERSION,
                    "scenario_version": LAB_VERSION,
                    "status": "LOCAL_PREVIEW",
                }
            ],
        }
    return {
        "environment_id": env_id,
        "latest": SREIncidentResponseEnvironment.VERSION,
        "versions": [
            {
                "version": SREIncidentResponseEnvironment.VERSION,
                "scenario_version": SREScenarioGenerator.VERSION,
                "status": "DEVELOPMENT",
            }
        ],
    }


@router.get("/{env_id}/suites")
def get_environment_suites(env_id: str):
    require_environment(env_id)
    if env_id == LAB_ID:
        return {
            "environment_id": env_id,
            "suites": [
                {"id": "smoke", "description": "Live configuration recovery"},
                {
                    "id": "standard",
                    "description": "Three real PostgreSQL connection/query faults",
                },
            ],
            "tiers": [{"id": "easy", "description": "One live workload fault"}],
        }
    descriptions = {
        "smoke": "Connection-pool sanity check",
        "standard": "Balanced ten-family distribution",
        "adversarial": "Ten-family distribution with injected untrusted log instructions",
        "heldout": "Separate seeded parameter distribution (public generator, not a secret benchmark)",
    }
    return {
        "environment_id": env_id,
        "suites": [
            {"id": s.value, "description": descriptions[s.value]} for s in SuiteName
        ],
        "tiers": [
            {
                "id": t.value,
                "description": {
                    "easy": "Single fault",
                    "medium": "Single fault with distracting logs",
                    "hard": "Two faults with distracting logs",
                }[t.value],
            }
            for t in DifficultyTier
        ],
    }


@router.get("/{env_id}/tools")
def get_tools(env_id: str):
    require_environment(env_id)
    if env_id == LAB_ID:
        return LAB_TOOLS
    return tool_definitions()
