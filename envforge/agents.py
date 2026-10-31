"""Explicit reference baseline and bounded HTTP action adapters."""

import logging
import os
from typing import Literal
from urllib.parse import urlparse

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from envforge.outbound import InvalidAgentResponse, post_agent_json
from envforge.protocol.types import Action


class AgentProtocolError(ValueError):
    """An endpoint responded, but did not follow the action protocol."""


class AgentPending(Exception):
    """The native agent is still reasoning; poll within the episode deadline."""


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter: Literal["reference_solver", "http_endpoint", "gemini"] = "reference_solver"
    model: str | None = Field(default=None, pattern=r"^gemini-[a-zA-Z0-9.-]{1,100}$")
    endpoint_url: str | None = Field(default=None, max_length=2048)
    token_env: str | None = Field(default=None, pattern=r"^[A-Z][A-Z0-9_]{0,100}$")
    credential_ref: str | None = Field(default=None, pattern=r"^agent_[0-9a-f]{32}$")
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_endpoint(self):
        if self.adapter == "http_endpoint":
            validate_endpoint(self.endpoint_url or "")
            if self.model:
                raise ValueError("Configure native bridge models on the bridge")
            if self.token_env and self.credential_ref:
                raise ValueError("Choose one endpoint credential source")
        elif self.adapter == "gemini":
            if self.endpoint_url or self.credential_ref:
                raise ValueError("Gemini uses the fixed Google API endpoint")
            self.model = self.model or "gemini-3.5-flash-lite"
            self.token_env = self.token_env or "GEMINI_API_KEY"
        elif self.endpoint_url or self.token_env or self.model or self.credential_ref:
            raise ValueError("Reference baseline does not use an endpoint or token")
        return self


def validate_endpoint(url: str) -> None:
    parsed = urlparse(url)
    hosts = {
        h.strip().lower()
        for h in os.environ.get(
            "ENVFORGE_AGENT_HOSTS", "localhost,127.0.0.1,::1"
        ).split(",")
    }
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in hosts
        or parsed.username
        or parsed.password
        or parsed.fragment
        or parsed.query
    ):
        raise ValueError(
            "Agent URL must use HTTP(S), have no embedded credentials, and match ENVFORGE_AGENT_HOSTS"
        )
    if parsed.scheme != "https" and parsed.hostname not in {
        "localhost",
        "127.0.0.1",
        "::1",
        "example-agent",
    }:
        raise ValueError("Remote agents require HTTPS")
    if os.environ.get("ENVFORGE_MODE") == "saas" and (
        parsed.scheme != "https" or parsed.port not in {None, 443}
    ):
        raise ValueError("SaaS agents require approved HTTPS endpoints on port 443")


def request_agent(
    config: AgentConfig, payload: dict, timeout: float = 20, *, token: str | None = None
) -> dict:
    validate_endpoint(config.endpoint_url or "")
    headers = {}
    if config.credential_ref and not token:
        raise RuntimeError("An endpoint credential must be resolved by its workspace")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    elif config.token_env:
        token = os.environ.get(config.token_env)
        if not token:
            raise RuntimeError(
                f"Agent credential variable {config.token_env} is not configured"
            )
        headers["Authorization"] = f"Bearer {token}"
    try:
        return post_agent_json(
            config.endpoint_url or "",
            payload,
            headers,
            timeout,
            public_only=os.environ.get("ENVFORGE_MODE") == "saas",
        )
    except InvalidAgentResponse as exc:
        raise AgentProtocolError(str(exc)) from None


def http_action(
    config: AgentConfig,
    episode_id: str,
    observation: dict,
    tools: list[dict],
    timeout: float = 20,
    *,
    token: str | None = None,
) -> Action | None:
    data = request_agent(
        config,
        {
            "protocol_version": "1",
            "operation": "act",
            "episode_id": episode_id,
            "observation": observation,
            "tools": tools,
        },
        timeout,
        token=token,
    )
    if isinstance(data, dict) and data == {"pending": True}:
        raise AgentPending
    if not isinstance(data, dict) or "action" not in data:
        raise AgentProtocolError(
            "Agent must return an object containing action (or null)"
        )
    try:
        return (
            Action.model_validate(data["action"])
            if data["action"] is not None
            else None
        )
    except ValueError:
        raise AgentProtocolError(
            "Agent action does not match the documented schema"
        ) from None


def close_agent(
    config: AgentConfig, episode_id: str, *, token: str | None = None
) -> None:
    """Release an HTTP agent's episode resources without changing its score."""
    if config.adapter == "http_endpoint":
        try:
            request_agent(
                config,
                {
                    "protocol_version": "1",
                    "operation": "close",
                    "episode_id": episode_id,
                },
                10,
                token=token,
            )
        except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError):
            # Remote services must also expire sessions when workers disappear.
            logging.getLogger(__name__).warning(
                "Agent cleanup failed; remote session expiry is required"
            )
