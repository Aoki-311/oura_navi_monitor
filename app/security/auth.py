from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Header, HTTPException

from app.settings import Settings, get_settings

_GOOGLE_ACCOUNT_PREFIX = "accounts.google.com:"


@dataclass(frozen=True)
class AdminIdentity:
    email: str
    source: str
    verified: bool


def _normalize_email(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith(_GOOGLE_ACCOUNT_PREFIX):
        text = text[len(_GOOGLE_ACCOUNT_PREFIX) :]
    return text


def require_admin(
    x_goog_authenticated_user_email: str = Header(default=""),
    x_monitor_admin_email: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> AdminIdentity:
    allowlist = settings.admin_allowlist
    if not allowlist:
        raise HTTPException(status_code=500, detail="admin allowlist is empty")

    verified_email = _normalize_email(x_goog_authenticated_user_email)
    if verified_email:
        if verified_email not in allowlist:
            raise HTTPException(status_code=403, detail="admin not allowed")
        return AdminIdentity(email=verified_email, source="iap", verified=True)

    if settings.monitor_iap_strict and not settings.monitor_allow_unverified_local:
        raise HTTPException(status_code=401, detail="iap identity required")

    local_email = _normalize_email(x_monitor_admin_email)
    if not local_email:
        raise HTTPException(status_code=401, detail="admin identity missing")
    if local_email not in allowlist:
        raise HTTPException(status_code=403, detail="admin not allowed")
    return AdminIdentity(email=local_email, source="local_header", verified=False)
