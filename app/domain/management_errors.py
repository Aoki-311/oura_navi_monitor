from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ManagementError(ValueError):
    """A stable management-domain error shared by service, repository, and API."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def revision_text(value: Any) -> str:
    if isinstance(value, datetime):
        resolved = value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
        return resolved.astimezone(timezone.utc).isoformat()
    return str(value or "").strip()
