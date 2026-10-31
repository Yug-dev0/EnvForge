"""Encrypted workspace-scoped endpoint credentials; manifests contain only references."""

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from apps.api.storage import database


def cipher() -> MultiFernet:
    try:
        return MultiFernet(
            [
                Fernet(key.strip().encode())
                for key in os.environ.get("ENVFORGE_SECRET_KEY", "").split(",")
            ]
        )
    except (ValueError, TypeError):
        raise RuntimeError(
            "Configure ENVFORGE_SECRET_KEY before storing agent credentials"
        ) from None


def save_credential(conn, agent_id: str, tenant: str, token: str) -> None:
    conn.execute(
        "INSERT INTO agent_secrets(agent_id,tenant_id,ciphertext) VALUES (?,?,?)",
        (agent_id, tenant, cipher().encrypt(token.encode()).decode()),
    )


def endpoint_credential(reference: str | None, tenant: str) -> str | None:
    if reference is None:
        return None
    with database() as conn:
        row = conn.execute(
            "SELECT ciphertext FROM agent_secrets WHERE agent_id=? AND tenant_id=?",
            (reference, tenant),
        ).fetchone()
    if row is None:
        raise RuntimeError("Agent credential is unavailable for this workspace")
    try:
        return cipher().decrypt(row["ciphertext"].encode()).decode()
    except InvalidToken:
        raise RuntimeError(
            "Agent credential cannot be decrypted; check the configured encryption keys"
        ) from None
