import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.security import auth
from app.settings import get_settings


def _settings(**updates):
    return get_settings().model_copy(update=updates)


def test_iap_verifier_uses_iap_keys_audience_and_issuer(monkeypatch) -> None:
    captured = {}

    def _verify(token, _request, *, audience, certs_url):
        captured.update(token=token, audience=audience, certs_url=certs_url)
        return {
            "iss": "https://cloud.google.com/iap",
            "sub": "accounts.google.com:123",
            "email": "admin@example.com",
        }

    monkeypatch.setattr(auth.id_token, "verify_token", _verify)
    claims = auth.verify_iap_assertion(
        "signed-token",
        expected_audience="/projects/123/global/backendServices/456",
    )
    assert claims["email"] == "admin@example.com"
    assert captured == {
        "token": "signed-token",
        "audience": "/projects/123/global/backendServices/456",
        "certs_url": "https://www.gstatic.com/iap/verify/public_key",
    }

    monkeypatch.setattr(
        auth.id_token,
        "verify_token",
        lambda *_args, **_kwargs: {
            "iss": "https://accounts.google.com",
            "sub": "accounts.google.com:123",
            "email": "admin@example.com",
        },
    )
    with pytest.raises(ValueError, match="issuer"):
        auth.verify_iap_assertion(
            "wrong-issuer",
            expected_audience="/projects/123/global/backendServices/456",
        )


def test_iap_identity_is_signed_required_and_allowlisted(monkeypatch) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(
        monitor_admin_allowlist="admin@example.com",
        monitor_iap_audience="/projects/123/global/backendServices/456",
        monitor_allow_unverified_local=False,
    )
    monkeypatch.setattr(
        auth,
        "verify_iap_assertion",
        lambda assertion, *, expected_audience: {
            "iss": "https://cloud.google.com/iap",
            "sub": "accounts.google.com:123",
            "email": "admin@example.com" if assertion == "allowed" else "other@example.com",
            "aud": expected_audience,
        },
    )
    client = TestClient(app)
    try:
        assert client.get("/dashboard").status_code == 401
        assert client.get(
            "/dashboard",
            headers={"x-monitor-admin-email": "admin@example.com"},
        ).status_code == 401
        assert client.get(
            "/dashboard",
            headers={"x-goog-authenticated-user-email": "accounts.google.com:admin@example.com"},
        ).status_code == 401
        assert client.get(
            "/dashboard",
            headers={"x-goog-iap-jwt-assertion": "other"},
        ).status_code == 403
        assert client.get(
            "/dashboard",
            headers={"x-goog-iap-jwt-assertion": "allowed"},
        ).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_empty_admin_allowlist_fails_closed() -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(
        monitor_admin_allowlist="",
        monitor_iap_audience="/projects/123/global/backendServices/456",
    )
    client = TestClient(app)
    try:
        response = client.get(
            "/dashboard",
            headers={"x-goog-iap-jwt-assertion": "anything"},
        )
        assert response.status_code == 500
    finally:
        app.dependency_overrides.clear()


def test_missing_iap_audience_fails_closed_before_verification(monkeypatch) -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(
        monitor_admin_allowlist="admin@example.com",
        monitor_iap_audience="",
        monitor_allow_unverified_local=False,
    )
    called = False

    def _unexpected(*_args, **_kwargs):
        nonlocal called
        called = True
        return {}

    monkeypatch.setattr(auth, "verify_iap_assertion", _unexpected)
    client = TestClient(app)
    try:
        response = client.get(
            "/dashboard",
            headers={"x-goog-iap-jwt-assertion": "signed"},
        )
        assert response.status_code == 500
        assert called is False
    finally:
        app.dependency_overrides.clear()
