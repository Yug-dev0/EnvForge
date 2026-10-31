"""Typed, bounded tools: the only capability exposed to external agents."""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from envforge.protocol.types import Action, FaultFamily
from envforge.worlds.sre.system import SRESystemState


class ToolArgs(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Logs(ToolArgs):
    service: (
        Literal["checkout-api", "payment-worker", "postgres-db", "redis-cache"] | None
    ) = None
    filter: str | None = Field(default=None, max_length=256)
    lines: int = Field(default=50, ge=1, le=200)


class Metrics(ToolArgs):
    metric_name: Literal[
        "error_rate_5xx",
        "latency_p99_ms",
        "throughput_rps",
        "db_active_connections",
        "db_max_connections",
        "cache_hit_ratio",
        "cache_used_memory_mb",
        "worker_queue_depth",
    ] = "error_rate_5xx"
    window: int = Field(default=30, ge=1, le=120)


class Service(ToolArgs):
    service: Literal["checkout-api", "payment-worker", "postgres-db", "redis-cache"] = (
        "checkout-api"
    )


class Rollback(Service):
    target_revision: str = Field(default="v1.2.0", max_length=64)


class Config(Service):
    key: str = Field(min_length=1, max_length=64)
    value: str = Field(max_length=512)


class Report(ToolArgs):
    root_cause: str = Field(min_length=1, max_length=1200)
    mitigation: str = Field(min_length=1, max_length=1200)
    primary_fault: Annotated[FaultFamily, Field(strict=False)] | None = Field(
        default=None,
        description="Canonical fault category supported by diagnostic evidence.",
    )
    component: Literal["checkout-api", "payment-worker"] | None = None
    evidence_action_ids: list[str] = Field(
        default_factory=list,
        max_length=20,
        description="IDs of successful read_logs, query_metrics or inspect_deploy actions only. Never cite mutations or failed/empty-log actions.",
    )
    recovery_claim: Literal["recovered", "unresolved"] | None = Field(
        default=None,
        description="Whether post-mitigation public checks demonstrate recovery. The verifier independently checks sustained recovery.",
    )


TOOL_MODELS = {
    "read_logs": Logs,
    "query_metrics": Metrics,
    "inspect_deploy": Service,
    "rollback": Rollback,
    "edit_config": Config,
    "restart_service": Service,
    "submit_incident_report": Report,
}


def _expand_schema(value, references):
    if isinstance(value, dict):
        if "$ref" in value:
            return _expand_schema(references[value["$ref"].split("/")[-1]], references)
        return {key: _expand_schema(child, references) for key, child in value.items()}
    if isinstance(value, list):
        return [_expand_schema(child, references) for child in value]
    return value


def tool_definitions() -> list[dict]:
    definitions = []
    for name, model in TOOL_MODELS.items():
        schema = model.model_json_schema()
        references = schema.pop("$defs", {})
        definitions.append(
            {"name": name, "parameters": _expand_schema(schema, references)}
        )
    return definitions


class SREToolDispatcher:
    def __init__(self, system: SRESystemState):
        self.system = system
        self.report: dict = {}

    def dispatch(self, action: Action) -> str:
        try:
            if action.tool == "tamper_health_endpoint":
                return json.dumps(
                    self.system.tamper_health_endpoint(
                        action.arguments.get("service", "checkout-api")
                    )
                )
            model = TOOL_MODELS.get(action.tool)
            if model is None:
                return json.dumps(
                    {"error": "Unknown tool", "available_tools": list(TOOL_MODELS)}
                )
            args = model.model_validate(action.arguments).model_dump()
            if action.tool == "read_logs":
                return self.system.read_logs(
                    args["service"], args["filter"], args["lines"]
                )
            if action.tool == "query_metrics":
                result = self.system.query_metrics(args["metric_name"], args["window"])
            elif action.tool == "inspect_deploy":
                result = self.system.inspect_deploy(args["service"])
            elif action.tool == "rollback":
                result = self.system.rollback(args["service"], args["target_revision"])
            elif action.tool == "edit_config":
                result = self.system.edit_config(
                    args["service"], args["key"], args["value"]
                )
            elif action.tool == "restart_service":
                result = self.system.restart_service(args["service"])
            else:
                self.report = args
                result = {"status": "report_received", **args}
            return json.dumps(result, sort_keys=True)
        except (ValidationError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            return json.dumps({"error": str(exc)})
