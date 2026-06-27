from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

QIHOO_360_PRODUCT_ID = "qihoo-360"
QIHOO_360_MAIN_UI_PATH = Path(r"C:\Program Files (x86)\360\360Safe\360Safe.exe")


class ProductWarmupError(RuntimeError):
    """Raised when Desktop Worker cannot run a product warm-up action."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class ProductWarmupPlan:
    product_id: str
    action: str
    executable: Path


def supported_product_warmups() -> tuple[str, ...]:
    return (QIHOO_360_PRODUCT_ID,)


def resolve_product_warmup(product_id: str) -> ProductWarmupPlan:
    normalized = str(product_id or "").strip().casefold()
    if normalized == QIHOO_360_PRODUCT_ID:
        return ProductWarmupPlan(
            product_id=QIHOO_360_PRODUCT_ID,
            action="open_main_ui",
            executable=QIHOO_360_MAIN_UI_PATH,
        )
    raise ProductWarmupError(
        "product warm-up is not supported for this product",
        status_code=400,
    )


def warm_up_security_product(product_id: str) -> dict[str, Any]:
    plan = resolve_product_warmup(product_id)
    executable = plan.executable
    if not executable.is_file():
        raise ProductWarmupError(
            "product warm-up executable was not found",
            status_code=404,
        )

    try:
        process = subprocess.Popen(
            [str(executable)],
            cwd=str(executable.parent),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            close_fds=True,
            creationflags=_no_window_creationflags(),
            env=_minimal_child_env(),
        )
    except OSError as exc:
        raise ProductWarmupError(
            f"product warm-up failed to start: {type(exc).__name__}",
            status_code=400,
        ) from exc

    return {
        "product_id": plan.product_id,
        "action": plan.action,
        "warmup_state": "started",
        "pid": process.pid,
        "executable": str(executable),
        "client_supplied_path": False,
        "client_supplied_command": False,
        "client_supplied_args": False,
        "shell": False,
    }


def _no_window_creationflags() -> int:
    return int(
        getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000 if os.name == "nt" else 0)
    )


def _minimal_child_env() -> dict[str, str]:
    allowed = ("SystemRoot", "WINDIR", "TEMP", "TMP", "PATH")
    return {key: value for key, value in os.environ.items() if key in allowed and value}
