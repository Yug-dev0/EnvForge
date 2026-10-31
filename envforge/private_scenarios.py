"""Operator-held scenario selections. Agent-facing manifests expose only provenance."""

import hmac
import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

from envforge.protocol.integrity import digest
from envforge.protocol.types import DifficultyTier, FaultFamily


class PrivateCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seed: int = Field(ge=0, le=2**31 - 1)
    tier: DifficultyTier
    family: FaultFamily
    secondary_fault: FaultFamily | None = None
    prompt: str | None = Field(default=None, min_length=10, max_length=10000)

    @model_validator(mode="after")
    def valid_faults(self):
        if (self.tier == DifficultyTier.HARD) != (self.secondary_fault is not None):
            raise ValueError("Hard cases require two faults; other tiers require one")
        if self.secondary_fault == self.family or {
            self.family,
            self.secondary_fault,
        } == {FaultFamily.FAULTY_ENV_CONFIG, FaultFamily.EXPIRED_CREDENTIAL}:
            raise ValueError("Faults must be distinct and compatible")
        return self


class PrivateScenarios(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bundle_id: str = Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9._-]+$")
    secret: SecretStr = Field(min_length=64, max_length=128)
    cases: list[PrivateCase] = Field(min_length=1, max_length=1000)

    @model_validator(mode="after")
    def unique_cases(self):
        if len({(case.seed, case.tier) for case in self.cases}) != len(self.cases):
            raise ValueError("Duplicate private case")
        return self

    def case(self, seed: int, tier: DifficultyTier) -> PrivateCase:
        for case in self.cases:
            if case.seed == seed and case.tier == tier:
                return case
        raise ValueError("Requested heldout case is not present in the private bundle")

    def provenance(self) -> dict:
        return {
            "bundle_id": self.bundle_id,
            "digest": "hmac-sha256:"
            + hmac.new(
                self.secret.get_secret_value().encode(),
                digest(self.model_dump(mode="json", exclude={"secret"})).encode(),
                "sha256",
            ).hexdigest(),
            "cases": len(self.cases),
            "disclosure": "Operator-held selections; generated observations and resulting traces remain visible to the evaluated customer",
        }


def load_private_scenarios() -> PrivateScenarios | None:
    value = os.environ.get("ENVFORGE_PRIVATE_SCENARIOS")
    if not value:
        return None
    try:
        path = Path(value).expanduser()
        if path.stat().st_size > 1048576:
            raise ValueError("Oversized private bundle")
        return PrivateScenarios.model_validate(json.loads(path.read_text()))
    except (OSError, ValueError):
        raise RuntimeError("Private scenario bundle is invalid or unreadable") from None
