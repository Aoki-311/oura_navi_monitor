from __future__ import annotations

import hashlib

from app.contracts.admin import normalize_email


def roster_id_for_email(email: str) -> str:
    normalized = normalize_email(email)
    return "roster_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
