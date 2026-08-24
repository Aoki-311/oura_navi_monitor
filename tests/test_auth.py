from fastapi.testclient import TestClient

from app.main import app
from app.settings import get_settings


def _settings(**updates):
    return get_settings().model_copy(update=updates)


def test_iap_authenticated_email_header_is_required_and_allowlisted() -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(
        monitor_admin_allowlist="admin@example.com",
        monitor_allow_unverified_local=False,
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
            headers={
                "x-goog-authenticated-user-email": (
                    "accounts.google.com:other@example.com"
                )
            },
        ).status_code == 403
        assert client.get(
            "/dashboard",
            headers={
                "x-goog-authenticated-user-email": (
                    "accounts.google.com:ADMIN@EXAMPLE.COM"
                )
            },
        ).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_empty_admin_allowlist_fails_closed() -> None:
    app.dependency_overrides[get_settings] = lambda: _settings(
        monitor_admin_allowlist="",
    )
    client = TestClient(app)
    try:
        response = client.get(
            "/dashboard",
            headers={
                "x-goog-authenticated-user-email": (
                    "accounts.google.com:admin@example.com"
                )
            },
        )
        assert response.status_code == 500
    finally:
        app.dependency_overrides.clear()


def test_local_admin_header_requires_explicit_local_mode() -> None:
    client = TestClient(app)
    try:
        app.dependency_overrides[get_settings] = lambda: _settings(
            monitor_admin_allowlist="admin@example.com",
            monitor_allow_unverified_local=False,
        )
        assert client.get(
            "/dashboard",
            headers={"x-monitor-admin-email": "admin@example.com"},
        ).status_code == 401

        app.dependency_overrides[get_settings] = lambda: _settings(
            monitor_admin_allowlist="admin@example.com",
            monitor_allow_unverified_local=True,
        )
        assert client.get(
            "/dashboard",
            headers={"x-monitor-admin-email": "admin@example.com"},
        ).status_code == 200
    finally:
        app.dependency_overrides.clear()
