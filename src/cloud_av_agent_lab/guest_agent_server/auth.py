from __future__ import annotations

import os
from collections.abc import Mapping

from fastapi import HTTPException, status

TOKEN_ENV = "CLOUD_AV_GUEST_AGENT_TOKEN"
UPLOAD_TOKEN_ENV = "CLOUD_AV_GUEST_AGENT_UPLOAD_TOKEN"


class GuestAgentServerConfigError(RuntimeError):
    """Raised when the Guest Agent server cannot start safely."""


def load_required_token(
    env: Mapping[str, str] | None = None,
    token_env: str = TOKEN_ENV,
) -> str:
    values = env if env is not None else os.environ
    token = values.get(token_env, "").strip()
    if not token:
        raise GuestAgentServerConfigError(
            f"Guest Agent token environment variable {token_env!r} is not set"
        )
    return token


def load_required_upload_token(
    env: Mapping[str, str] | None = None,
    token_env: str = UPLOAD_TOKEN_ENV,
) -> str:
    values = env if env is not None else os.environ
    token = values.get(token_env, "").strip()
    if not token:
        raise GuestAgentServerConfigError(
            f"Guest Agent upload token environment variable {token_env!r} is not set"
        )
    return token


def verify_bearer_token(authorization: str | None, expected_token: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
        )
    provided_token = authorization.removeprefix("Bearer ").strip()
    if not provided_token or provided_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid bearer token",
        )


def verify_upload_token(upload_token: str | None, expected_token: str) -> None:
    if not upload_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing upload token",
        )
    if upload_token != expected_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid upload token",
        )
