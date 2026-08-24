from __future__ import annotations

import hashlib
import hmac

from app.contracts.admin import normalize_email


def build_user_key(email: str, *, secret: str) -> str:
    normalized = normalize_email(email)
    key = str(secret or "").strip()
    if not key:
        raise ValueError("identity HMAC key is required")
    return "user_" + hmac.new(
        key.encode("utf-8"),
        normalized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def roster_id_for_email(email: str) -> str:
    normalized = normalize_email(email)
    return "roster_" + hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:20]
