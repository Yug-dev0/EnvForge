"""Tenant-scoped control plane with secure sessions and local development defaults."""

import logging
import os
import secrets
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from apps.api.auth import authenticate
from apps.api.identity import identity
from apps.api.jobs import worker_heartbeat
from apps.api.routes import (
    agents,
    comparisons,
    environments,
    episodes,
    evaluations,
    reports,
)
from apps.api.routes.accounts import auth_router
from apps.api.routes.accounts import router as workspace_router
from apps.api.runner import recover_expired_jobs, worker_once
from apps.api.settings import (
    allowed_origins,
    api_token,
    max_active_runs,
    saas_mode,
    validate_deployment,
)
from apps.api.storage import init_db
from apps.api.usage import request_allowed


def _worker(stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            if not worker_once():
                stop.wait(1)
        except Exception:  # noqa: BLE001 -- recover after temporary storage outages without leaking database credentials
            logging.getLogger(__name__).warning("Worker storage unavailable; retrying")
            stop.wait(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_deployment()
    init_db()
    recover_expired_jobs()
    stop = threading.Event()
    if os.environ.get("ENVFORGE_EXTERNAL_WORKER") != "1":
        with worker_heartbeat():
            for _ in range(max_active_runs()):
                threading.Thread(target=_worker, args=(stop,), daemon=True).start()
            try:
                yield
            finally:
                stop.set()
    else:
        yield


app = FastAPI(title="EnvForge Control Plane", version="0.2.0", lifespan=lifespan)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exception: RequestValidationError):
    # Never echo a rejected access key or endpoint credential in a JSON error.
    return JSONResponse(
        {
            "detail": [
                {key: error[key] for key in ("loc", "msg", "type")}
                for error in exception.errors()
            ]
        },
        status_code=422,
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=allowed_origins(),
    allow_credentials=saas_mode(),
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def access_control(request: Request, call_next):
    context = None
    user = None
    if request.url.path.startswith("/v1"):
        # Browser preflights do not carry a bearer token; CORS evaluates their origin.
        if request.method == "OPTIONS":
            return await call_next(request)
        if saas_mode():
            try:
                user = await run_in_threadpool(authenticate, request)
                if not await run_in_threadpool(
                    request_allowed, "api:" + user.key_id, 600
                ):
                    return JSONResponse(
                        {"detail": "Workspace access key request limit reached"},
                        status_code=429,
                        headers={"Retry-After": "60"},
                    )
            except HTTPException as exc:
                return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
        token = "" if saas_mode() else api_token()
        if token and not secrets.compare_digest(
            request.headers.get("Authorization", ""), "Bearer " + token
        ):
            return JSONResponse({"detail": "Valid API token required"}, status_code=401)
        if (
            not saas_mode()
            and not token
            and request.client
            and request.client.host
            not in {"127.0.0.1", "::1", "localhost", "testclient"}
        ):
            return JSONResponse(
                {"detail": "Configure ENVFORGE_API_TOKEN for remote access"},
                status_code=403,
            )
        if (
            not saas_mode()
            and not token
            and request.url.hostname
            not in {
                "localhost",
                "127.0.0.1",
                "::1",
                "testserver",
            }
        ):
            return JSONResponse(
                {"detail": "Local access requires a loopback host"}, status_code=403
            )
    if request.method in {"POST", "PUT", "PATCH"}:
        body = bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body) > 262144:
                return JSONResponse(
                    {"detail": "Request body exceeds 256 KiB"}, status_code=413
                )
        request._body = bytes(body)
    if user is not None:
        context = identity.set(user)
    try:
        response = await call_next(request)
    finally:
        if context is not None:
            identity.reset(context)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    if saas_mode():
        response.headers["Strict-Transport-Security"] = (
            "max-age=31536000; includeSubDomains"
        )
    return response


for module in (environments, agents, evaluations, episodes, comparisons, reports):
    app.include_router(module.router)
app.include_router(auth_router)
app.include_router(workspace_router)


@app.get("/health")
def health_check():
    init_db()
    return {
        "status": "healthy",
        "service": "envforge-control-plane",
        "version": "0.2.0",
    }


@app.get("/ready")
def readiness():
    import time

    from apps.api.storage import database

    try:
        with database() as conn:
            worker = conn.execute(
                "SELECT COUNT(*) FROM worker_heartbeats WHERE last_seen>?",
                (time.time() - 30,),
            ).fetchone()[0]
        return JSONResponse(
            {
                "status": "ready" if worker else "unavailable",
                "storage": "ok",
                "workers_alive": worker,
            },
            status_code=200 if worker else 503,
        )
    except Exception:  # noqa: BLE001 -- health responses must not expose connection details
        return JSONResponse(
            {"status": "unavailable", "storage": "unavailable"}, status_code=503
        )


def main() -> None:
    import uvicorn

    uvicorn.run("apps.api.main:app", host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
