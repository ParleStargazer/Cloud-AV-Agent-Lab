from __future__ import annotations


class WorkspaceError(ValueError):
    """Raised when a workspace request is unsafe or malformed."""


class WorkspaceNotFoundError(WorkspaceError):
    """Raised when a prepared case workspace does not exist yet."""
