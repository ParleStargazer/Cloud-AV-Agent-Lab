from __future__ import annotations

import re
from pathlib import Path

from .errors import WorkspaceError

SAFE_CASE_ID = re.compile(r"^[A-Za-z0-9_.-]+$")
SAFE_FILENAME = re.compile(r"^[A-Za-z0-9_. -]+$")


def safe_case_id(raw_case_id: object) -> str:
    case_id = str(raw_case_id or "").strip()
    if not case_id:
        raise WorkspaceError("case.id is required")
    if not SAFE_CASE_ID.fullmatch(case_id):
        raise WorkspaceError("case.id contains unsafe characters")
    if case_id in {".", ".."}:
        raise WorkspaceError("case.id cannot be a relative path segment")
    return case_id


def safe_original_filename(raw_filename: object) -> str:
    filename = Path(str(raw_filename or "sample.bin")).name.strip()
    if not filename:
        filename = "sample.bin"
    if filename in {".", ".."}:
        raise WorkspaceError("original filename cannot be a relative path segment")
    if not SAFE_FILENAME.fullmatch(filename):
        raise WorkspaceError("original filename contains unsafe characters")
    return filename


def _case_workspace(workdir: str | Path, case_id: str) -> Path:
    root = Path(workdir).resolve()
    cases_root = (root / "cases").resolve()
    workspace = (cases_root / case_id).resolve()
    if not _is_relative_to(workspace, cases_root):
        raise WorkspaceError("case workspace escapes the configured workdir")
    return workspace


def _is_relative_to(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
    except ValueError:
        return False
    return True
