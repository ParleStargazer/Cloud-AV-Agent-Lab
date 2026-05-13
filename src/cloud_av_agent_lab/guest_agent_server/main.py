from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Sequence

from cloud_av_agent_lab.guest_agent_server.app import create_app
from cloud_av_agent_lab.guest_agent_server.auth import (
    TOKEN_ENV,
    GuestAgentServerConfigError,
    load_required_upload_token,
    load_required_token,
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        token = load_required_token(token_env=TOKEN_ENV)
        upload_token = load_required_upload_token()
    except GuestAgentServerConfigError as exc:
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

    app = create_app(workdir=Path(args.workdir), token=token, upload_token=upload_token)
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
