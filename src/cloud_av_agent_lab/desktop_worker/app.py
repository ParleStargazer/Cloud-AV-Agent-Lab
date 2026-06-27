from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException

from cloud_av_agent_lab.desktop_worker.auth import verify_worker_token
from cloud_av_agent_lab.desktop_worker.execution import (
    WorkerExecutionError,
    WorkerExecutionRegistry,
)
from cloud_av_agent_lab.desktop_worker.product_warmup import (
    ProductWarmupError,
    warm_up_security_product,
)
from cloud_av_agent_lab.desktop_worker.status import build_worker_health

FORBIDDEN_PRODUCT_WARMUP_FIELDS = {
    "path",
    "sample_path",
    "cmd",
    "command",
    "args",
    "arguments",
    "shell",
    "interpreter",
    "powershell",
}


def create_app(
    *,
    token: str,
    workdir: str | Path = r"C:\CloudAvAgentLab",
    bind_host: str = "127.0.0.1",
    app_version: str | None = None,
) -> FastAPI:
    resolved_version = app_version or _package_version()
    app = FastAPI(title="Cloud AV Agent Lab Desktop Worker", version=resolved_version)
    execution_registry = WorkerExecutionRegistry(
        workdir=Path(workdir),
        lease_secret=token,
    )

    def authorize(authorization: str | None) -> None:
        verify_worker_token(authorization, token)

    @app.get("/health")
    def health(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)
        payload = build_worker_health(
            bind_host=bind_host,
            version=resolved_version,
            busy=execution_registry.busy,
        ).to_dict()
        return {
            "status": "ok",
            "message": "desktop worker healthy",
            "data": payload,
        }

    @app.post("/execute")
    def execute(
        payload: dict[str, Any] = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            data = execution_registry.execute(payload)
        except WorkerExecutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            raise _internal_worker_error("desktop worker execute failed", exc) from exc
        return {
            "status": "ok",
            "message": str(data.get("message", "desktop worker execution handled")),
            "data": data,
        }

    @app.get("/execution-status/{case_id:path}")
    def execution_status(
        case_id: str,
        authorization: str | None = Header(default=None),
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            data = execution_registry.execution_status(
                case_id,
                mark_timeout=mark_timeout,
                timeout_seconds=timeout_seconds,
            )
        except WorkerExecutionError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            raise _internal_worker_error(
                "desktop worker execution status failed", exc
            ) from exc
        return {
            "status": "ok",
            "message": "desktop worker execution status observed",
            "data": data,
        }

    @app.post("/product-actions/warm-up/{product_id}")
    def product_warmup(
        product_id: str,
        payload: dict[str, Any] | None = Body(default=None),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        forbidden = _forbidden_warmup_fields(payload or {})
        if forbidden:
            joined = ", ".join(forbidden)
            raise HTTPException(
                status_code=400,
                detail=f"unsupported product warm-up fields: {joined}",
            )
        try:
            data = warm_up_security_product(product_id)
        except ProductWarmupError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
        except Exception as exc:
            raise _internal_worker_error(
                "desktop worker product warm-up failed",
                exc,
            ) from exc
        return {
            "status": "ok",
            "message": "desktop worker product warm-up handled",
            "data": data,
        }

    return app


def _package_version() -> str:
    try:
        return version("cloud-av-agent-lab")
    except PackageNotFoundError:
        return "0.1.0"


def _internal_worker_error(message: str, exc: Exception) -> HTTPException:
    return HTTPException(
        status_code=500,
        detail={
            "status": "error",
            "reason_code": "desktop_worker_internal_error",
            "message": message,
            "error_type": type(exc).__name__,
        },
    )


def _forbidden_warmup_fields(payload: dict[str, Any]) -> list[str]:
    return sorted(
        key for key in payload if str(key).casefold() in FORBIDDEN_PRODUCT_WARMUP_FIELDS
    )
