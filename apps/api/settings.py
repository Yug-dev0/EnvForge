"""Environment-based configuration, shared by API and worker."""

import os
from pathlib import Path
from urllib.parse import urlparse


def saas_mode() -> bool:
    return os.environ.get("ENVFORGE_MODE", "local") == "saas"


def database_url() -> str:
    return os.environ.get("ENVFORGE_DATABASE_URL", "")


def max_active_runs() -> int:
    value = int(os.environ.get("ENVFORGE_MAX_ACTIVE_RUNS", "2"))
    if not 1 <= value <= 16:
        raise RuntimeError("ENVFORGE_MAX_ACTIVE_RUNS must be between 1 and 16")
    return value


def public_origin() -> str:
    return os.environ.get("ENVFORGE_PUBLIC_ORIGIN", "").rstrip("/")


def validate_deployment() -> None:
    mode = os.environ.get("ENVFORGE_MODE", "local")
    if mode not in {"local", "saas"}:
        raise RuntimeError("ENVFORGE_MODE must be local or saas")
    if not saas_mode():
        return
    origin = urlparse(public_origin())
    if (
        origin.scheme != "https"
        or not origin.netloc
        or origin.path
        or origin.username
        or origin.password
        or origin.query
        or origin.fragment
    ):
        raise RuntimeError(
            "SaaS mode requires ENVFORGE_PUBLIC_ORIGIN to be an HTTPS origin"
        )
    if not database_url().startswith(("postgresql://", "postgres://")):
        raise RuntimeError("SaaS mode requires a PostgreSQL ENVFORGE_DATABASE_URL")
    from cryptography.fernet import Fernet

    try:
        for key in os.environ.get("ENVFORGE_SECRET_KEY", "").split(","):
            Fernet(key.strip().encode())
    except (ValueError, TypeError):
        raise RuntimeError(
            "SaaS mode requires a valid ENVFORGE_SECRET_KEY for encrypted agent credentials"
        ) from None


def database_path() -> Path:
    return (
        Path(os.environ.get("ENVFORGE_DB_PATH", "data/envforge.sqlite3"))
        .expanduser()
        .resolve()
    )


def api_token() -> str:
    return os.environ.get("ENVFORGE_API_TOKEN", "")


def allowed_origins() -> list[str]:
    if saas_mode():
        return [public_origin()]
    return [
        x.strip()
        for x in os.environ.get(
            "ENVFORGE_CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
        ).split(",")
        if x.strip()
    ]
