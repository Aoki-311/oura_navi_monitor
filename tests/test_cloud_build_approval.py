from __future__ import annotations

import pytest

from scripts.cloud_build_approval import (
    CloudBuildApprovalError,
    submit_build_decision,
)


class _Response:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _Session:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict, int]] = []

    def post(self, url: str, *, json: dict, timeout: int) -> _Response:
        self.calls.append((url, json, timeout))
        return self.response


@pytest.mark.parametrize(
    ("action", "decision"), (("approve", "APPROVED"), ("reject", "REJECTED"))
)
def test_submit_build_decision_uses_exact_regional_resource(
    action: str, decision: str
) -> None:
    session = _Session(_Response(200, {"name": "operations/build-approval"}))

    result = submit_build_decision(
        session,
        project_id="lcs-developer-483404",
        region="us-central1",
        build_id="fe50c4fb-202f-417b-a3d1-eb9824baf7be",
        action=action,
    )

    assert session.calls == [
        (
            "https://cloudbuild.googleapis.com/v1/projects/"
            "lcs-developer-483404/locations/us-central1/builds/"
            "fe50c4fb-202f-417b-a3d1-eb9824baf7be:approve",
            {
                "approvalResult": {
                    "decision": decision,
                    "comment": f"Monitor release workflow {action}",
                }
            },
            60,
        )
    ]
    assert result["buildId"] == "fe50c4fb-202f-417b-a3d1-eb9824baf7be"
    assert result["decision"] == decision
    assert result["status"] == "submitted"


def test_submit_build_decision_rejects_an_unresolved_resource() -> None:
    session = _Session(_Response(200, {}))

    with pytest.raises(ValueError, match="invalid build ID"):
        submit_build_decision(
            session,
            project_id="lcs-developer-483404",
            region="us-central1",
            build_id="../latest",
            action="approve",
        )

    assert session.calls == []


def test_submit_build_decision_never_exposes_provider_error_content() -> None:
    session = _Session(
        _Response(
            403,
            {
                "error": {
                    "status": "PERMISSION_DENIED",
                    "message": "provider-sensitive-detail",
                }
            },
        )
    )

    with pytest.raises(CloudBuildApprovalError) as error:
        submit_build_decision(
            session,
            project_id="lcs-developer-483404",
            region="us-central1",
            build_id="fe50c4fb-202f-417b-a3d1-eb9824baf7be",
            action="reject",
        )

    assert "HTTP 403 (PERMISSION_DENIED)" in str(error.value)
    assert "provider-sensitive-detail" not in str(error.value)
