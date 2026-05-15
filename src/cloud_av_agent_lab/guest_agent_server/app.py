from __future__ import annotations

import platform
import socket
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request, status

from cloud_av_agent_lab.guest_agent_server.auth import (
    verify_bearer_token,
    verify_execution_token,
    verify_upload_token,
)
from cloud_av_agent_lab.guest_agent_server.workspace import (
    ExecutionRegistry,
    WorkspaceError,
    WorkspaceNotFoundError,
    prepare_case_workspace,
    read_case_execution_status,
    read_case_report,
    read_case_status,
    run_case_action,
    save_uploaded_sample,
)


def create_app(
    workdir: str | Path,
    token: str,
    upload_token: str,
    execution_enabled: bool = False,
    execution_token: str | None = None,
    execution_timeout_seconds: float = 30.0,
    app_version: str | None = None,
) -> FastAPI:
    workdir_path = Path(workdir)
    resolved_version = app_version or _package_version()
    app = FastAPI(title="Cloud AV Agent Lab Guest Agent", version=resolved_version)
    execution_registry = ExecutionRegistry()

    def authorize(authorization: str | None) -> None:
        verify_bearer_token(authorization, token)

    def authorize_upload(
        authorization: str | None, provided_upload_token: str | None
    ) -> None:
        verify_bearer_token(authorization, token)
        verify_upload_token(provided_upload_token, upload_token)

    def authorize_action(
        authorization: str | None,
        provided_execution_token: str | None,
    ) -> None:
        verify_bearer_token(authorization, token)
        if execution_enabled:
            verify_execution_token(provided_execution_token, execution_token or "")

    @app.get("/health")
    def health(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)
        return {
            "status": "ok",
            "message": "guest agent healthy",
            "data": {
                "agent": "cloud-av-agent-lab",
                "version": resolved_version,
                "time_utc": datetime.now(timezone.utc)
                .isoformat()
                .replace("+00:00", "Z"),
            },
        }

    @app.get("/system-info")
    def system_info(authorization: str | None = Header(default=None)) -> dict[str, Any]:
        authorize(authorization)
        return {
            "status": "ok",
            "message": "system info collected",
            "data": {
                "hostname": socket.gethostname(),
                "platform": platform.platform(),
                "python_version": platform.python_version(),
                "workdir": str(workdir_path),
            },
        }

    @app.post("/prepare-case")
    def prepare_case(
        payload: dict[str, Any] = Body(...),
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            case_id, workspace = prepare_case_workspace(workdir_path, payload)
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        return {
            "status": "ok",
            "message": "case workspace prepared",
            "data": {
                "case_id": case_id,
                "workspace": str(workspace),
            },
        }

    @app.post("/cases/{case_id:path}/sample")
    async def upload_sample(
        case_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
        x_upload_token: str | None = Header(default=None),
        x_sample_id: str = Header(...),
        x_sample_sha256: str = Header(default=""),
        x_original_filename: str = Header(default="sample.bin"),
    ) -> dict[str, Any]:
        authorize_upload(authorization, x_upload_token)
        content = await request.body()
        try:
            sample_path, metadata = save_uploaded_sample(
                workdir=workdir_path,
                case_id=case_id,
                content=content,
                sample_id=x_sample_id,
                sha256=x_sample_sha256,
                original_filename=x_original_filename,
            )
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        return {
            "status": "ok",
            "message": "sample uploaded",
            "data": {
                "case_id": metadata["case_id"],
                "sample_id": metadata["sample_id"],
                "transport_ok": True,
                "upload_state": metadata["upload_state"],
                "saved_once": metadata["saved_once"],
                "metadata_saved": metadata["metadata_saved"],
                "post_write_exists": metadata["post_write_exists"],
                "removed_after_save": metadata["removed_after_save"],
                "locked_or_busy": metadata["locked_or_busy"],
                "stable": metadata["stable"],
                "sha256": metadata["sha256"],
                "size": metadata["size"],
                "original_filename": metadata["original_filename"],
                "workspace": str(sample_path.parent.parent),
                "sample_dir": str(sample_path.parent),
            },
        }

    @app.get("/cases/{case_id:path}/status")
    def case_status(
        case_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = read_case_status(workdir_path, case_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {
            "status": "ok",
            "message": "case status loaded",
            "data": payload,
        }

    @app.get("/cases/{case_id:path}/report")
    def case_report(
        case_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = read_case_report(workdir_path, case_id)
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {
            "status": "ok",
            "message": "case report loaded",
            "data": payload,
        }

    @app.get("/cases/{case_id:path}/execution-status")
    def execution_status(
        case_id: str,
        authorization: str | None = Header(default=None),
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = read_case_execution_status(
                workdir_path,
                case_id,
                execution_registry=execution_registry,
                timeout_seconds=(timeout_seconds if mark_timeout else None),
            )
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {
            "status": "ok",
            "message": "execution status observed",
            "data": payload,
        }

    @app.post("/cases/{case_id:path}/actions")
    def case_action(
        case_id: str,
        payload: dict[str, Any] = Body(...),
        authorization: str | None = Header(default=None),
        x_execution_token: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize_action(authorization, x_execution_token)
        try:
            action_result = run_case_action(
                workdir=workdir_path,
                case_id=case_id,
                payload=payload,
                execution_enabled=execution_enabled,
                execution_registry=execution_registry,
            )
        except WorkspaceNotFoundError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(exc),
            ) from exc
        except WorkspaceError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        return {
            "status": "ok",
            "message": str(action_result.get("message", "case action handled")),
            "data": {
                **action_result,
                "execution_enabled": execution_enabled,
                "execution_timeout_seconds": execution_timeout_seconds,
            },
        }

    return app


def _package_version() -> str:
    try:
        return version("cloud-av-agent-lab")
    except PackageNotFoundError:
        return "0.1.0"
