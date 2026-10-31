"""Bounded readiness probes shared by connection tests and run admission."""

import os

import httpx

from apps.api.identity import tenant_id
from apps.api.secrets import endpoint_credential
from envforge.agents import AgentConfig, request_agent
from envforge.gemini_runner import GeminiProviderError
from envforge.gemini_runner import test_connection as test_gemini_connection

NATIVE_AGENTS = {"holmesgpt": "HolmesGPT", "kubectl_ai": "kubectl-ai"}


class AgentUnavailable(RuntimeError):
    """Safe setup guidance; never includes arbitrary endpoint responses."""


def probe_agent(config: AgentConfig) -> dict:
    integration = config.metadata.get("integration", "")
    if not isinstance(integration, str):
        raise AgentUnavailable(
            "Agent integration metadata is invalid; register the agent again."
        )
    name = NATIVE_AGENTS.get(integration, "HTTP agent")
    if config.token_env and not os.environ.get(config.token_env):
        raise AgentUnavailable(
            f"Cannot connect: credential variable {config.token_env} is not configured on the API/worker. "
            + (
                f"Start the {name} bridge with the same credential, or select EnvForge Gemini."
                if integration in NATIVE_AGENTS
                else "Set the credential before launching a run."
            )
        )
    if config.adapter == "reference_solver":
        return {"status": "healthy"}
    try:
        if config.adapter == "gemini":
            return test_gemini_connection(config, timeout=5)
        result = request_agent(
            config,
            {"protocol_version": "1", "operation": "health"},
            timeout=5,
            token=endpoint_credential(config.credential_ref, tenant_id()),
        )
        if not isinstance(result, dict) or result.get("status") != "healthy":
            raise ValueError("Invalid handshake")
        if integration in NATIVE_AGENTS:
            runtime = {
                key: result.get(key)
                for key in ("integration", "model", "provider", "image_id")
            }
            if runtime["integration"] != integration or not all(
                isinstance(value, str) and value for value in runtime.values()
            ):
                raise ValueError("Native runtime identity is invalid")
            return {"status": "healthy", "runtime": runtime}
        return {"status": "healthy"}
    except GeminiProviderError as exc:
        raise AgentUnavailable(str(exc)) from None
    except (httpx.HTTPError, ValueError, RuntimeError, TimeoutError):
        message = (
            "Cannot connect to Gemini; check model access and the server credential."
            if config.adapter == "gemini"
            else f"Cannot connect to {name}. Start its bridge and check the endpoint and shared credential, or select EnvForge Gemini."
            if integration in NATIVE_AGENTS
            else "Cannot connect to the HTTP agent; start it and check its endpoint, credential and health protocol."
        )
        raise AgentUnavailable(message) from None
