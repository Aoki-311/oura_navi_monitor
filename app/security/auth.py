from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from fastapi import Depends, Header, HTTPException
from google.auth.transport import requests as google_auth_requests
from google.oauth2 import id_token

from app.settings import Settings, get_settings

_GOOGLE_ACCOUNT_PREFIX = "accounts.google.com:"
_IAP_CERTS_URL = "https://www.gstatic.com/iap/verify/public_key"
_IAP_ISSUER = "https://cloud.google.com/iap"


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


def verify_iap_assertion(assertion: str, *, expected_audience: str) -> Mapping[str, Any]:
    """Verify the signed IAP owner; unsigned identity headers are never trusted."""

    token = str(assertion or "").strip()
    audience = str(expected_audience or "").strip()
    if not token:
        raise ValueError("IAP assertion is required")
    if not audience:
        raise RuntimeError("IAP audience is not configured")
    claims = id_token.verify_token(
        token,
        google_auth_requests.Request(),
        audience=audience,
        certs_url=_IAP_CERTS_URL,
    )
    if str(claims.get("iss") or "") != _IAP_ISSUER:
        raise ValueError("IAP issuer is invalid")
    if not _normalize_email(str(claims.get("email") or "")):
        raise ValueError("IAP email claim is missing")
    if not str(claims.get("sub") or "").strip():
        raise ValueError("IAP subject claim is missing")
    return claims


def require_admin(
    x_goog_iap_jwt_assertion: str = Header(default=""),
    x_monitor_admin_email: str = Header(default=""),
    settings: Settings = Depends(get_settings),
) -> AdminIdentity:
    allowlist = settings.admin_allowlist
    if not allowlist:
        raise HTTPException(status_code=500, detail="admin allowlist is empty")

    assertion = str(x_goog_iap_jwt_assertion or "").strip()
    if assertion:
        if not str(settings.monitor_iap_audience or "").strip():
            raise HTTPException(status_code=500, detail="iap audience is not configured")
        try:
            claims = verify_iap_assertion(
                assertion,
                expected_audience=settings.monitor_iap_audience,
            )
        except Exception as exc:
            raise HTTPException(status_code=401, detail="iap assertion invalid") from exc
        verified_email = _normalize_email(str(claims.get("email") or ""))
        if verified_email not in allowlist:
            raise HTTPException(status_code=403, detail="admin not allowed")
        return AdminIdentity(email=verified_email, source="iap", verified=True)

    if not settings.monitor_allow_unverified_local:
        raise HTTPException(status_code=401, detail="iap identity required")

    local_email = _normalize_email(x_monitor_admin_email)
    if not local_email:
        raise HTTPException(status_code=401, detail="admin identity missing")
    if local_email not in allowlist:
        raise HTTPException(status_code=403, detail="admin not allowed")
    return AdminIdentity(email=local_email, source="local_header", verified=False)
