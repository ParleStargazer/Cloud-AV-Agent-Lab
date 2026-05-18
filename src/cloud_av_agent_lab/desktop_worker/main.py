from __future__ import annotations

import argparse
import logging
from typing import Sequence

from cloud_av_agent_lab.desktop_worker.app import create_app
from cloud_av_agent_lab.desktop_worker.auth import (
    TOKEN_ENV,
    DesktopWorkerConfigError,
    load_required_token,
)

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="desktop-worker",
        description="Run the Cloud AV Agent Lab Desktop Worker MVP.",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind host; Desktop Worker must stay loopback-only",
    )
    parser.add_argument("--port", type=int, default=8001, help="bind port")
    parser.add_argument(
        "--workdir",
        default=r"C:\CloudAvAgentLab",
        help="shared Cloud AV Agent Lab workdir used by Control Agent",
    )
    parser.add_argument(
        "--token-env",
        default=TOKEN_ENV,
        help="environment variable that stores the Desktop Worker token",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.host not in LOOPBACK_HOSTS:
        parser.exit(
            2,
            "error: Desktop Worker must bind to 127.0.0.1/localhost only; "
            "do not expose it to the network\n",
        )

    try:
        token = load_required_token(args.token_env)
    except DesktopWorkerConfigError as exc:
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
            "error: uvicorn is not installed; install the desktop-worker extra first\n",
        )
        raise exc

    app = create_app(token=token, bind_host=args.host, workdir=args.workdir)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
