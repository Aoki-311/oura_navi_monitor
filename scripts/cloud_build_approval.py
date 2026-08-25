from __future__ import annotations

import argparse
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from google.auth.transport.requests import AuthorizedSession
from google.oauth2 import service_account


_RESOURCE_COMPONENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_DECISIONS = {"approve": "APPROVED", "reject": "REJECTED"}
_CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"


class CloudBuildApprovalError(RuntimeError):
    """Sanitized Cloud Build approval failure safe for operator output."""


def _component(name: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not _RESOURCE_COMPONENT.fullmatch(normalized):
        raise ValueError(f"invalid {name}")
    return normalized


def submit_build_decision(
    session: Any,
    *,
    project_id: str,
    region: str,
    build_id: str,
    action: str,
) -> dict[str, str | None]:
    project = _component("project", project_id)
    location = _component("region", region)
    build = _component("build ID", build_id)
    try:
        decision = _DECISIONS[action]
    except KeyError as exc:
        raise ValueError("action must be approve or reject") from exc

    url = (
        "https://cloudbuild.googleapis.com/v1/"
        f"projects/{project}/locations/{location}/builds/{build}:approve"
    )
    response = session.post(
        url,
        json={
            "approvalResult": {
                "decision": decision,
                "comment": f"Monitor release workflow {action}",
            }
        },
        timeout=60,
    )
    if int(response.status_code) >= 400:
        api_status = "UNKNOWN"
        try:
            payload = response.json()
            api_status = str(payload.get("error", {}).get("status") or "UNKNOWN")
        except (AttributeError, TypeError, ValueError):
            pass
        raise CloudBuildApprovalError(
            f"Cloud Build approval failed: HTTP {response.status_code} ({api_status})"
        )

    payload = response.json()
    operation_name = payload.get("name") if isinstance(payload, dict) else None
    return {
        "buildId": build,
        "decision": decision,
        "operationName": str(operation_name) if operation_name else None,
        "status": "submitted",
    }


def _credential_path() -> Path:
    configured = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    path = Path(configured)
    if not configured or not path.is_file() or path.is_symlink():
        raise CloudBuildApprovalError("approved credential is not a regular file")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise CloudBuildApprovalError("approved credential metadata is unsafe")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Approve or reject one exact regional Cloud Build"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--action", choices=tuple(_DECISIONS), required=True)
    args = parser.parse_args()

    try:
        credentials = service_account.Credentials.from_service_account_file(
            str(_credential_path()), scopes=[_CLOUD_PLATFORM_SCOPE]
        )
    except CloudBuildApprovalError:
        raise
    except Exception as exc:
        raise CloudBuildApprovalError(
            f"approved credential initialization failed ({type(exc).__name__})"
        ) from exc

    session = AuthorizedSession(credentials)
    try:
        result = submit_build_decision(
            session,
            project_id=args.project,
            region=args.region,
            build_id=args.build_id,
            action=args.action,
        )
    finally:
        session.close()
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (CloudBuildApprovalError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
