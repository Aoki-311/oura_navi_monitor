#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.render_runtime_env import validate_iap_audience


def _service_account_resource(project_id: str, service_account: str) -> str:
    value = str(service_account or "").strip()
    if value.startswith("projects/"):
        return value
    return f"projects/{project_id}/serviceAccounts/{value}"


def _csv_items(value: str) -> list[str]:
    return [item for item in str(value or "").split(",") if item]


def verify_trigger_contract(
    trigger: dict[str, Any],
    *,
    project_id: str,
    trigger_name: str,
    repo_owner: str,
    repo_name: str,
    branch_pattern: str,
    service_account: str,
    iap_audience: str,
    included_files: str,
    ignored_files: str,
) -> None:
    expected_audience = validate_iap_audience(iap_audience)
    substitutions = trigger.get("substitutions") or {}
    actual_audience = substitutions.get("_IAP_AUDIENCE")

    mismatches: list[str] = []
    if trigger.get("name") != trigger_name:
        mismatches.append("trigger name")
    if trigger.get("filename") != "cloudbuild.yaml":
        mismatches.append("build config")
    if trigger.get("description") != "CI creates a no-traffic OurA Navi Monitor candidate":
        mismatches.append("description")
    if trigger.get("serviceAccount") != _service_account_resource(
        project_id, service_account
    ):
        mismatches.append("service account")

    github = trigger.get("github") or {}
    push = github.get("push") or {}
    if github.get("owner") != repo_owner:
        mismatches.append("GitHub owner")
    if github.get("name") != repo_name:
        mismatches.append("GitHub repository")
    if push.get("branch") != branch_pattern:
        mismatches.append("branch pattern")

    approval = trigger.get("approvalConfig") or {}
    if approval.get("approvalRequired") is not True:
        mismatches.append("manual approval")
    if trigger.get("includeBuildLogs") != "INCLUDE_BUILD_LOGS_WITH_STATUS":
        mismatches.append("GitHub build status logs")
    if trigger.get("disabled") is True:
        mismatches.append("trigger disabled state")

    if trigger.get("includedFiles") != _csv_items(included_files):
        mismatches.append("included files")
    if trigger.get("ignoredFiles") != _csv_items(ignored_files):
        mismatches.append("ignored files")
    try:
        normalized_actual = validate_iap_audience(str(actual_audience or ""))
    except ValueError:
        normalized_actual = None
    if normalized_actual != expected_audience:
        mismatches.append("_IAP_AUDIENCE substitution")

    if mismatches:
        raise ValueError("trigger contract mismatch: " + ", ".join(mismatches))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify the deployed GitHub trigger against its release contract"
    )
    parser.add_argument("--trigger-json", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--repo-owner", required=True)
    parser.add_argument("--repo-name", required=True)
    parser.add_argument("--branch-pattern", required=True)
    parser.add_argument("--service-account", required=True)
    parser.add_argument("--iap-audience", required=True)
    parser.add_argument("--included-files", required=True)
    parser.add_argument("--ignored-files", required=True)
    args = parser.parse_args()

    payload = json.loads(args.trigger_json.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("trigger payload must be a JSON object")
    verify_trigger_contract(
        payload,
        project_id=args.project,
        trigger_name=args.name,
        repo_owner=args.repo_owner,
        repo_name=args.repo_name,
        branch_pattern=args.branch_pattern,
        service_account=args.service_account,
        iap_audience=args.iap_audience,
        included_files=args.included_files,
        ignored_files=args.ignored_files,
    )
    print("trigger_contract_verified=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
