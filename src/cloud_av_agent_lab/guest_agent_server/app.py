from __future__ import annotations

import platform
import socket
from datetime import datetime, timezone
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, Header, HTTPException, Request, status
from fastapi.responses import FileResponse

from cloud_av_agent_lab.desktop_worker.lease import issue_execution_lease
from cloud_av_agent_lab.guest_agent_server.desktop_worker_client import (
    DesktopWorkerClient,
    DesktopWorkerClientError,
)
from cloud_av_agent_lab.guest_agent_server.auth import (
    verify_bearer_token,
    verify_execution_token,
    verify_upload_token,
)
from cloud_av_agent_lab.guest_agent_server.workspace import (
    ExecutionRegistry,
    WorkspaceError,
    WorkspaceNotFoundError,
    check_and_record_case_security_product_readiness,
    collect_case_logs,
    export_case_evidence_bundle,
    prepare_worker_execute_request,
    prepare_case_workspace,
    read_case_collection_status,
    read_case_execution_status,
    read_case_report,
    read_case_security_product_readiness_status,
    read_case_summary,
    read_case_status,
    record_worker_execution_observed,
    record_worker_execution_started,
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
    desktop_worker_enabled: bool = False,
    desktop_worker_base_url: str = "http://127.0.0.1:8001",
    desktop_worker_token: str | None = None,
    desktop_worker_timeout_seconds: float = 5.0,
    desktop_worker_required_for_execution: bool = True,
    desktop_worker_expected_user: str = "",
    desktop_worker_require_interactive_session: bool = True,
    app_version: str | None = None,
) -> FastAPI:
    workdir_path = Path(workdir)
    resolved_version = app_version or _package_version()
    app = FastAPI(title="Cloud AV Agent Lab Guest Agent", version=resolved_version)
    execution_registry = ExecutionRegistry()
    desktop_worker_client = (
        DesktopWorkerClient(
            base_url=desktop_worker_base_url,
            token=desktop_worker_token or "",
            timeout_seconds=desktop_worker_timeout_seconds,
        )
        if desktop_worker_enabled
        else None
    )

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

    @app.get("/worker/status")
    def worker_status(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        payload = _worker_status_payload(
            enabled=desktop_worker_enabled,
            client=desktop_worker_client,
            expected_user=desktop_worker_expected_user,
            require_interactive_session=desktop_worker_require_interactive_session,
            required_for_execution=desktop_worker_required_for_execution,
        )
        return {
            "status": "ok",
            "message": "desktop worker status loaded",
            "data": payload,
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

    @app.post("/cases/{case_id:path}/collection/{product_id}")
    def collect_logs(
        case_id: str,
        product_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = collect_case_logs(workdir_path, case_id, product_id)
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
            "message": "collection completed",
            "data": payload,
        }

    @app.get("/cases/{case_id:path}/collection/status")
    def collection_status(
        case_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = read_case_collection_status(workdir_path, case_id)
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
            "message": "collection status loaded",
            "data": payload,
        }

    @app.post("/cases/{case_id:path}/security-product-readiness/{product_id}")
    def check_security_product_readiness(
        case_id: str,
        product_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = check_and_record_case_security_product_readiness(
                workdir_path,
                case_id,
                product_id,
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
            "message": "security product readiness checked",
            "data": payload,
        }

    @app.get("/cases/{case_id:path}/security-product-readiness/status")
    def security_product_readiness_status(
        case_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = read_case_security_product_readiness_status(
                workdir_path,
                case_id,
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
            "message": "security product readiness status loaded",
            "data": payload,
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

    @app.get("/cases/{case_id:path}/summary")
    def case_summary(
        case_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            payload = read_case_summary(workdir_path, case_id)
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
            "message": "case summary loaded",
            "data": payload,
        }

    @app.get("/cases/{case_id:path}/evidence-bundle")
    def evidence_bundle(
        case_id: str,
        authorization: str | None = Header(default=None),
    ) -> FileResponse:
        authorize(authorization)
        try:
            payload = export_case_evidence_bundle(workdir_path, case_id)
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
        return FileResponse(
            payload["bundle_path"],
            media_type="application/zip",
            filename=payload["filename"],
            headers={
                "X-Evidence-Bundle-Sha256": str(payload.get("sha256", "")),
            },
        )

    @app.get("/cases/{case_id:path}/execution-status")
    def execution_status(
        case_id: str,
        authorization: str | None = Header(default=None),
        mark_timeout: bool = False,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        authorize(authorization)
        try:
            if desktop_worker_enabled and desktop_worker_client is not None:
                worker_response = desktop_worker_client.execution_status(
                    case_id,
                    mark_timeout=mark_timeout,
                    timeout_seconds=timeout_seconds,
                )
                if worker_response.data.get("execution_state") != "not_started":
                    record_worker_execution_observed(
                        workdir_path,
                        case_id,
                        worker_response.data,
                    )
                payload = {
                    **worker_response.data,
                    "recent_events": read_case_report(workdir_path, case_id).get(
                        "recent_events",
                        [],
                    ),
                }
            else:
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
        except DesktopWorkerClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY
                if exc.source == "network"
                else status.HTTP_400_BAD_REQUEST,
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
            if (
                str(payload.get("action", "")).strip() == "execute_uploaded_sample"
                and execution_enabled
                and desktop_worker_required_for_execution
            ):
                action_result = _run_desktop_worker_execute_action(
                    workdir_path=workdir_path,
                    case_id=case_id,
                    payload=payload,
                    desktop_worker_enabled=desktop_worker_enabled,
                    desktop_worker_client=desktop_worker_client,
                    desktop_worker_token=desktop_worker_token or "",
                    desktop_worker_expected_user=desktop_worker_expected_user,
                    desktop_worker_require_interactive_session=(
                        desktop_worker_require_interactive_session
                    ),
                    desktop_worker_required_for_execution=(
                        desktop_worker_required_for_execution
                    ),
                )
            else:
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
        except DesktopWorkerClientError as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY
                if exc.source == "network"
                else status.HTTP_400_BAD_REQUEST,
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


def _worker_status_payload(
    *,
    enabled: bool,
    client: DesktopWorkerClient | None,
    expected_user: str,
    require_interactive_session: bool,
    required_for_execution: bool,
) -> dict[str, Any]:
    base_payload: dict[str, Any] = {
        "control_agent_ready": True,
        "desktop_worker_enabled": enabled,
        "desktop_worker_ready": False,
        "required_for_execution": required_for_execution,
        "expected_user": expected_user,
        "require_interactive_session": require_interactive_session,
        "reason": "",
    }
    if not enabled:
        return {
            **base_payload,
            "reason": "desktop worker integration is disabled",
        }
    if client is None:
        return {
            **base_payload,
            "reason": "desktop worker client is not configured",
        }

    try:
        status_payload = client.health().data
    except DesktopWorkerClientError as exc:
        return {
            **base_payload,
            "reason": str(exc),
            "worker_error_source": exc.source,
            "worker_error_status_code": exc.status_code,
        }

    worker_user = str(status_payload.get("username", ""))
    interactive_session = bool(status_payload.get("interactive_session", False))
    desktop_session_state = str(status_payload.get("desktop_session_state", "unknown"))
    worker_session_id = status_payload.get("worker_session_id")

    checks = {
        "token_ok": True,
        "interactive_session": interactive_session,
        "session_not_zero": worker_session_id != 0,
        "desktop_session_state_active": desktop_session_state == "active",
        "expected_user": not expected_user
        or worker_user.casefold() == expected_user.casefold(),
        "not_busy": not bool(status_payload.get("busy", False)),
    }
    ready = (
        checks["token_ok"]
        and checks["not_busy"]
        and checks["expected_user"]
        and (
            not require_interactive_session
            or (
                checks["interactive_session"]
                and checks["session_not_zero"]
                and checks["desktop_session_state_active"]
            )
        )
    )
    reason = (
        "" if ready else _worker_not_ready_reason(checks, require_interactive_session)
    )
    return {
        **base_payload,
        **status_payload,
        "desktop_worker_ready": ready,
        "desktop_session_ready": checks["interactive_session"]
        and checks["session_not_zero"]
        and checks["desktop_session_state_active"],
        "checks": checks,
        "reason": reason,
    }


def _worker_not_ready_reason(
    checks: dict[str, bool],
    require_interactive_session: bool,
) -> str:
    if not checks["not_busy"]:
        return "desktop worker is busy"
    if not checks["expected_user"]:
        return "desktop worker user does not match expected_user"
    if require_interactive_session and not checks["interactive_session"]:
        return "desktop worker is not running in an interactive session"
    if require_interactive_session and not checks["session_not_zero"]:
        return "desktop worker is running in Session 0"
    if require_interactive_session and not checks["desktop_session_state_active"]:
        return "desktop session state is not active"
    return "desktop worker is not ready"


def _run_desktop_worker_execute_action(
    *,
    workdir_path: Path,
    case_id: str,
    payload: dict[str, Any],
    desktop_worker_enabled: bool,
    desktop_worker_client: DesktopWorkerClient | None,
    desktop_worker_token: str,
    desktop_worker_expected_user: str,
    desktop_worker_require_interactive_session: bool,
    desktop_worker_required_for_execution: bool,
) -> dict[str, Any]:
    worker_status = _worker_status_payload(
        enabled=desktop_worker_enabled,
        client=desktop_worker_client,
        expected_user=desktop_worker_expected_user,
        require_interactive_session=desktop_worker_require_interactive_session,
        required_for_execution=desktop_worker_required_for_execution,
    )
    if not worker_status.get("desktop_worker_ready"):
        raise WorkspaceError(
            "desktop worker is required for real execution but is not ready: "
            + str(worker_status.get("reason") or "unknown")
        )
    if desktop_worker_client is None or not desktop_worker_token:
        raise WorkspaceError("desktop worker client is not configured")

    worker_payload = prepare_worker_execute_request(workdir_path, case_id, payload)
    worker_payload["execution_lease"] = issue_execution_lease(
        secret=desktop_worker_token,
        case_id=worker_payload["case_id"],
        sample_id=worker_payload["sample_id"],
        run_id=worker_payload["run_id"],
        expected_sha256=worker_payload["expected_sha256"],
        ttl_seconds=60.0,
    )
    worker_response = desktop_worker_client.execute(worker_payload)
    record_worker_execution_started(workdir_path, case_id, worker_response.data)
    return worker_response.data
