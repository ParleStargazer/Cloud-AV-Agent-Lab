from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from cloud_av_agent_lab.guest_agent_server.app import create_app
from cloud_av_agent_lab.guest_agent_server.auth import (
    EXECUTION_TOKEN_ENV,
    TOKEN_ENV,
    GuestAgentServerConfigError,
    load_required_execution_token,
    load_required_upload_token,
    load_required_token,
)
from cloud_av_agent_lab.desktop_worker.auth import (
    TOKEN_ENV as DESKTOP_WORKER_TOKEN_ENV,
)
from cloud_av_agent_lab.desktop_worker.auth import (
    DesktopWorkerConfigError,
    load_required_token as load_required_desktop_worker_token,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="guest-agent",
        description="Run the Cloud AV Agent Lab Guest Agent MVP server.",
    )
    parser.add_argument("--host", default="127.0.0.1", help="bind host")
    parser.add_argument("--port", type=int, default=8080, help="bind port")
    parser.add_argument(
        "--workdir",
        default=r"C:\CloudAvAgentLab",
        help="guest work directory",
    )
    parser.add_argument(
        "--enable-execution-actions",
        action="store_true",
        help=(
            "enable controlled execution actions; requires an execution token and "
            "only permits the current case's registered uploaded file"
        ),
    )
    parser.add_argument(
        "--execution-token-env",
        default=EXECUTION_TOKEN_ENV,
        help="environment variable that stores the execution action token",
    )
    parser.add_argument(
        "--execution-timeout-seconds",
        type=float,
        default=30.0,
        help="reported timeout window for controlled execution actions",
    )
    parser.add_argument(
        "--enable-desktop-worker",
        action="store_true",
        help=(
            "enable localhost Desktop Worker readiness checks; requires a worker "
            "token and does not expose Worker to the network"
        ),
    )
    parser.add_argument(
        "--desktop-worker-url",
        default="http://127.0.0.1:8001",
        help="Control Agent localhost URL for Desktop Worker",
    )
    parser.add_argument(
        "--desktop-worker-token-env",
        default=DESKTOP_WORKER_TOKEN_ENV,
        help="environment variable that stores the Desktop Worker token",
    )
    parser.add_argument(
        "--desktop-worker-timeout-seconds",
        type=float,
        default=5.0,
        help="timeout for Control Agent to query Desktop Worker health",
    )
    parser.add_argument(
        "--desktop-worker-expected-user",
        default="Administrator",
        help="expected interactive administrator account for Desktop Worker",
    )
    parser.add_argument(
        "--allow-worker-session-0",
        action="store_true",
        help="developer diagnostic escape hatch; real execution should require session != 0",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        token = load_required_token(token_env=TOKEN_ENV)
        upload_token = load_required_upload_token()
        execution_token = (
            load_required_execution_token(token_env=args.execution_token_env)
            if args.enable_execution_actions
            else None
        )
        desktop_worker_token = (
            load_required_desktop_worker_token(token_env=args.desktop_worker_token_env)
            if args.enable_desktop_worker
            else None
        )
    except (GuestAgentServerConfigError, DesktopWorkerConfigError) as exc:
        parser.exit(2, f"error: {exc}\n")

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    try:
        import uvicorn
    except ModuleNotFoundError as exc:
        parser.exit(
            2,
            "error: uvicorn is not installed; install the guest-agent extra first\n",
        )
        raise exc

    app = create_app(
        workdir=Path(args.workdir),
        token=token,
        upload_token=upload_token,
        execution_enabled=args.enable_execution_actions,
        execution_token=execution_token,
        execution_timeout_seconds=args.execution_timeout_seconds,
        desktop_worker_enabled=args.enable_desktop_worker,
        desktop_worker_base_url=args.desktop_worker_url,
        desktop_worker_token=desktop_worker_token,
        desktop_worker_timeout_seconds=args.desktop_worker_timeout_seconds,
        desktop_worker_expected_user=args.desktop_worker_expected_user,
        desktop_worker_require_interactive_session=not args.allow_worker_session_0,
    )
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
