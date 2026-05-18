from __future__ import annotations

import os
import secrets

from fastapi import HTTPException, status

TOKEN_ENV = "CLOUD_AV_DESKTOP_WORKER_TOKEN"


class DesktopWorkerConfigError(RuntimeError):
    """Raised when Desktop Worker startup configuration is unsafe."""


def load_required_token(token_env: str = TOKEN_ENV) -> str:
    token = os.environ.get(token_env, "").strip()
    if not token:
        raise DesktopWorkerConfigError(
            f"Desktop Worker token environment variable {token_env!r} is not set"
        )
    return token


def verify_worker_token(authorization: str | None, expected_token: str) -> None:
    prefix = "Bearer "
    provided = authorization or ""
    if not provided.startswith(prefix):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    token = provided[len(prefix) :].strip()
    if not token or not secrets.compare_digest(token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
        )
