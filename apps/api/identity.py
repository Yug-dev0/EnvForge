"""Verified request identity; workers use explicit persisted workspace IDs."""

from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(frozen=True)
class Identity:
    tenant_id: str = "local"
    actor_id: str = "local"
    role: str = "admin"
    key_id: str = ""
    csrf: str = ""
    session_hash: str = ""


LOCAL_IDENTITY = Identity()
identity: ContextVar[Identity] = ContextVar("envforge_identity", default=LOCAL_IDENTITY)


def tenant_id() -> str:
    return identity.get().tenant_id
