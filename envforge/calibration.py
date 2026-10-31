"""Compare verifier outcomes to supplied external labels without claiming an audit."""

import json
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, StrictBool

from envforge.metrics import outcome_groups
from envforge.protocol.integrity import digest


class Expected(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recovery: StrictBool
    safety: StrictBool
    report_quality: StrictBool


class Label(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evaluation_id: str = Field(pattern=r"^eval_[0-9a-f]{32}$")
    seed: int = Field(ge=0)
    expected: Expected


class Labels(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer: str = Field(min_length=1, max_length=200)
    independence_statement: str = Field(min_length=20, max_length=2000)
    labels: list[Label] = Field(min_length=1, max_length=10000)


def compare_labels(labels_path: Path, report_paths: list[Path]) -> dict:
    labels = Labels.model_validate_json(labels_path.read_text())
    if len({(row.evaluation_id, row.seed) for row in labels.labels}) != len(
        labels.labels
    ):
        raise ValueError("Duplicate external labels")
    reports = [json.loads(path.read_text()) for path in report_paths]
    if len({report["evaluation_id"] for report in reports}) != len(reports):
        raise ValueError("Duplicate evaluation reports")
    keys = (
        "environment_id",
        "environment_version",
        "verifier_version",
        "implementation_digest",
        "private_scenario_bundle",
        "runtime_image_digest",
    )
    manifests = [report["provenance"]["manifest"] for report in reports]
    if not manifests or any(
        any(manifest.get(key) != manifests[0].get(key) for key in keys)
        for manifest in manifests
    ):
        raise ValueError(
            "Calibration reports must share environment, verifier, implementation and case-bundle provenance"
        )
    observed = {}
    for report in reports:
        if report["status"] != "COMPLETED":
            raise ValueError("Calibration requires completed evaluations")
        for episode in report["episodes"]:
            observed[report["evaluation_id"], episode["seed"]] = outcome_groups(
                episode["verification"]
            )
    matrices = {
        group: {
            "true_positive": 0,
            "false_positive": 0,
            "true_negative": 0,
            "false_negative": 0,
        }
        for group in ("recovery", "safety", "report_quality")
    }
    disagreements = []
    for row in labels.labels:
        actual = observed.get((row.evaluation_id, row.seed))
        if actual is None:
            raise ValueError("A labelled case is missing from the supplied reports")
        for group, expected in row.expected.model_dump().items():
            predicted = actual[group]
            key = ("true_" if predicted == expected else "false_") + (
                "positive" if predicted else "negative"
            )
            matrices[group][key] += 1
            if predicted != expected:
                disagreements.append(
                    {
                        "evaluation_id": row.evaluation_id,
                        "seed": row.seed,
                        "group": group,
                        "expected": expected,
                        "observed": predicted,
                    }
                )
    return {
        "schema_version": "1",
        "status": "LABELS_COMPARED",
        "reviewer": labels.reviewer,
        "label_digest": digest(labels.model_dump(mode="json")),
        "independence_assurance": "operator_attested_not_verified",
        "independence_statement": labels.independence_statement,
        "cases_reviewed": len(labels.labels),
        "agents": len({report["agent_id"] for report in reports}),
        "provenance": {key: manifests[0].get(key) for key in keys},
        "matrices": matrices,
        "disagreements": disagreements,
        "limitations": [
            "External label origin and independence are not authenticated by this tool",
            "Label agreement is not certification or proof of general agent reliability",
            "Independent model calibration remains pending until suitable external review and coverage are supplied",
        ],
    }
