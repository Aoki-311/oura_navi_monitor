#!/usr/bin/env python3
"""Capture one converged Cloud Run candidate readback and verify its contract."""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any

try:
    from scripts.verify_candidate_service import verify_candidate
    from scripts.verify_service_access_contract import (
        ReconciliationPending,
        require_reconciled_ready,
        verify as verify_access,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from verify_candidate_service import verify_candidate
    from verify_service_access_contract import (
        ReconciliationPending,
        require_reconciled_ready,
        verify as verify_access,
    )


class ReadbackCommandError(RuntimeError):
    pass


def _load_json(path: str, *, label: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(label + " is not one JSON object")
    return payload


def _run_json(command: list[str], *, label: str) -> dict[str, Any]:
    result = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "no command output"
        raise ReadbackCommandError(f"{label} failed: {detail}")
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(label + " did not return JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError(label + " did not return one JSON object")
    return payload


def _write_json(path: str, payload: dict[str, Any]) -> None:
    Path(path).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def capture_and_verify(
    *,
    before_service: dict[str, Any],
    before_iam: dict[str, Any],
    project_id: str,
    project_number: str,
    region: str,
    service: str,
    revision: str,
    expected_image: str,
    expected_service_account: str,
    expected_git_sha: str,
    candidate_tag: str,
    max_attempts: int,
    poll_seconds: float,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
    if max_attempts <= 0:
        raise ValueError("max attempts must be positive")
    if poll_seconds < 0:
        raise ValueError("poll seconds must not be negative")

    last_pending = "candidate readback has not started"
    candidate_service: dict[str, Any] | None = None
    candidate_revision: dict[str, Any] | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            candidate_service = _run_json(
                [
                    "gcloud",
                    "run",
                    "services",
                    "describe",
                    service,
                    "--project",
                    project_id,
                    "--region",
                    region,
                    "--format=json",
                ],
                label="candidate service readback",
            )
            candidate_revision = _run_json(
                [
                    "gcloud",
                    "run",
                    "revisions",
                    "describe",
                    revision,
                    "--project",
                    project_id,
                    "--region",
                    region,
                    "--format=json",
                ],
                label="candidate revision readback",
            )
            require_reconciled_ready(
                candidate_service, label="candidate service"
            )
            require_reconciled_ready(
                candidate_revision, label="candidate revision"
            )
        except (ReadbackCommandError, ReconciliationPending) as exc:
            last_pending = str(exc)
            if attempt == max_attempts:
                raise TimeoutError(
                    "candidate readback did not converge after "
                    f"{max_attempts} attempts: {last_pending}"
                ) from exc
            time.sleep(poll_seconds)
            continue
        break

    if candidate_service is None or candidate_revision is None:
        raise RuntimeError("candidate readback loop produced no evidence")

    candidate_iam = _run_json(
        [
            "gcloud",
            "run",
            "services",
            "get-iam-policy",
            service,
            "--project",
            project_id,
            "--region",
            region,
            "--format=json",
        ],
        label="candidate IAM readback",
    )
    verify_access(before_service, candidate_service, before_iam, candidate_iam)
    receipt = verify_candidate(
        service=candidate_service,
        revision=candidate_revision,
        expected_project_id=project_id,
        expected_project_number=project_number,
        expected_region=region,
        expected_service=service,
        expected_image=expected_image,
        expected_revision=revision,
        expected_service_account=expected_service_account,
        expected_git_sha=expected_git_sha,
        candidate_tag=candidate_tag,
    )
    return receipt, candidate_service, candidate_revision, candidate_iam


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before-service", required=True)
    parser.add_argument("--before-iam", required=True)
    parser.add_argument("--service-output", required=True)
    parser.add_argument("--revision-output", required=True)
    parser.add_argument("--iam-output", required=True)
    parser.add_argument("--expected-project-id", required=True)
    parser.add_argument("--expected-project-number", required=True)
    parser.add_argument("--expected-region", required=True)
    parser.add_argument("--expected-service", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--candidate-tag", default="candidate")
    parser.add_argument("--max-attempts", type=int, default=30)
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    args = parser.parse_args()

    try:
        receipt, service, revision, iam = capture_and_verify(
            before_service=_load_json(
                args.before_service, label="predeploy service readback"
            ),
            before_iam=_load_json(args.before_iam, label="predeploy IAM readback"),
            project_id=args.expected_project_id,
            project_number=args.expected_project_number,
            region=args.expected_region,
            service=args.expected_service,
            revision=args.expected_revision,
            expected_image=args.expected_image,
            expected_service_account=args.expected_service_account,
            expected_git_sha=args.expected_git_sha,
            candidate_tag=args.candidate_tag,
            max_attempts=args.max_attempts,
            poll_seconds=args.poll_seconds,
        )
    except (
        OSError,
        ValueError,
        RuntimeError,
        TimeoutError,
        json.JSONDecodeError,
    ) as exc:
        raise SystemExit("candidate_readback_invalid: " + str(exc)) from exc

    _write_json(args.service_output, service)
    _write_json(args.revision_output, revision)
    _write_json(args.iam_output, iam)
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
