from __future__ import annotations

import ctypes
import getpass
import os
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class WorkerHealth:
    status: str
    worker_pid: int
    worker_session_id: int | None
    interactive_session: bool
    desktop_session_state: str
    username: str
    bind_host: str
    version: str
    busy: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "worker_pid": self.worker_pid,
            "worker_session_id": self.worker_session_id,
            "interactive_session": self.interactive_session,
            "desktop_session_state": self.desktop_session_state,
            "username": self.username,
            "bind_host": self.bind_host,
            "version": self.version,
            "busy": self.busy,
        }


def build_worker_health(
    *,
    bind_host: str,
    version: str,
    busy: bool = False,
) -> WorkerHealth:
    session_id = current_process_session_id()
    active_console_session_id = active_desktop_session_id()
    interactive = bool(session_id is not None and session_id != 0)
    return WorkerHealth(
        status="ok",
        worker_pid=os.getpid(),
        worker_session_id=session_id,
        interactive_session=interactive,
        desktop_session_state=_desktop_session_state(
            session_id,
            active_console_session_id,
        ),
        username=_username(),
        bind_host=bind_host,
        version=version,
        busy=busy,
    )


def current_process_session_id() -> int | None:
    if os.name != "nt":
        return None
    session_id = wintypes.DWORD()
    ok = ctypes.windll.kernel32.ProcessIdToSessionId(
        os.getpid(),
        ctypes.byref(session_id),
    )
    if not ok:
        return None
    return int(session_id.value)


def active_desktop_session_id() -> int | None:
    if os.name != "nt":
        return None
    value = ctypes.windll.kernel32.WTSGetActiveConsoleSessionId()
    if value == 0xFFFFFFFF:
        return None
    return int(value)


def _desktop_session_state(
    session_id: int | None,
    active_console_session_id: int | None,
) -> str:
    if session_id is None or session_id == 0:
        return "unknown"
    if (
        active_console_session_id is not None
        and session_id == active_console_session_id
    ):
        return "active"
    return "unknown"


def _username() -> str:
    try:
        return getpass.getuser()
    except OSError:
        return ""
