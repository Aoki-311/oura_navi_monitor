from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

try:
    from scripts.verify_service_access_contract import (
        require_exact_tagged_revision,
        require_reconciled_traffic,
        traffic_planes,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from verify_service_access_contract import (
        require_exact_tagged_revision,
        require_reconciled_traffic,
        traffic_planes,
    )


def _load(path: str) -> dict[str, Any]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain one JSON object")
    return payload


def _require_resource_segment(label: str, value: str) -> None:
    if not isinstance(value, str) or not re.fullmatch(r"[^/\s]+", value):
        raise ValueError(f"expected {label} must be one exact resource segment")


def _observed_resource_names(payload: dict[str, Any], *, label: str) -> set[str]:
    metadata = payload.get("metadata")
    if metadata is not None and not isinstance(metadata, dict):
        raise ValueError(f"candidate {label} metadata is not an object")
    observed: set[str] = set()
    for value in ((metadata or {}).get("name"), payload.get("name")):
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise ValueError(f"candidate {label} resource name is not a string")
        observed.add(value)
    if not observed:
        raise ValueError(f"candidate {label} readback has no resource identity")
    return observed


def _require_scope_evidence(
    payload: dict[str, Any], *, project: str, region: str, full_name: str, label: str
) -> None:
    names = _observed_resource_names(payload, label=label)
    if full_name in names:
        return
    metadata = payload.get("metadata") or {}
    labels = metadata.get("labels") or {}
    if metadata.get("namespace") != project:
        raise ValueError(f"candidate {label} readback has no exact project evidence")
    if labels.get("cloud.googleapis.com/location") != region:
        raise ValueError(f"candidate {label} readback has no exact region evidence")


def _require_service_identity(
    payload: dict[str, Any],
    *,
    project: str,
    region: str,
    service: str,
) -> None:
    allowed = {
        service,
        f"projects/{project}/locations/{region}/services/{service}",
    }
    if not _observed_resource_names(payload, label="service") <= allowed:
        raise ValueError("candidate service readback returned another service")
    _require_scope_evidence(
        payload,
        project=project,
        region=region,
        full_name=f"projects/{project}/locations/{region}/services/{service}",
        label="service",
    )


def _require_revision_identity(
    payload: dict[str, Any],
    *,
    project: str,
    region: str,
    service: str,
    revision: str,
) -> None:
    allowed = {
        revision,
        (
            f"projects/{project}/locations/{region}/services/{service}/"
            f"revisions/{revision}"
        ),
    }
    if not _observed_resource_names(payload, label="revision") <= allowed:
        raise ValueError("candidate revision readback returned another revision")
    _require_scope_evidence(
        payload,
        project=project,
        region=region,
        full_name=(
            f"projects/{project}/locations/{region}/services/{service}/"
            f"revisions/{revision}"
        ),
        label="revision",
    )


def _candidate_traffic_rows(
    planes: dict[str, list[dict[str, Any]]],
    *,
    expected_revision: str,
    candidate_tag: str,
) -> dict[str, dict[str, Any]]:
    tagged = require_exact_tagged_revision(
        planes,
        label="candidate",
        tag=candidate_tag,
        revision=expected_revision,
    )
    for plane, rows in planes.items():
        target_rows = [
            item for item in rows if item.get("revisionName") == expected_revision
        ]
        if any(item["percent"] > 0 for item in target_rows):
            raise ValueError(
                f"candidate {plane} traffic unexpectedly gives the revision production traffic"
            )
        if len(target_rows) != 1:
            raise ValueError(
                f"candidate {plane} revision traffic must resolve exactly once"
            )
    return tagged


def verify_candidate(
    *,
    service: dict[str, Any],
    revision: dict[str, Any],
    expected_project: str,
    expected_region: str,
    expected_service: str,
    expected_image: str,
    expected_revision: str,
    expected_service_account: str,
    expected_git_sha: str,
    candidate_tag: str,
) -> dict[str, Any]:
    for label, value in (
        ("project", expected_project),
        ("region", expected_region),
        ("service", expected_service),
        ("revision", expected_revision),
    ):
        _require_resource_segment(label, value)
    _require_service_identity(
        service,
        project=expected_project,
        region=expected_region,
        service=expected_service,
    )
    _require_revision_identity(
        revision,
        project=expected_project,
        region=expected_region,
        service=expected_service,
        revision=expected_revision,
    )
    if not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", expected_image):
        raise ValueError("expected image must be one complete immutable @sha256 URI")
    spec = revision.get("spec")
    if spec is None:
        spec = {}
    if not isinstance(spec, dict):
        raise ValueError("candidate revision spec is not an object")
    containers = spec.get("containers")
    if not isinstance(containers, list) or len(containers) != 1:
        raise ValueError("candidate revision must have exactly one container")
    container = containers[0]
    if not isinstance(container, dict):
        raise ValueError("candidate revision container is not an object")
    if container.get("image") != expected_image:
        raise ValueError("candidate revision image digest readback failed")
    if spec.get("serviceAccountName") != expected_service_account:
        raise ValueError("candidate revision service account readback failed")
    metadata = revision.get("metadata") or {}
    labels = metadata.get("labels") or {}
    if not isinstance(labels, dict):
        raise ValueError("candidate revision labels are not an object")
    if labels.get("git-sha") != expected_git_sha:
        raise ValueError("candidate revision full Git SHA label is missing")
    status = revision.get("status")
    if status is None:
        status = {}
    if not isinstance(status, dict):
        raise ValueError("candidate revision status is not an object")
    if "imageDigest" in status:
        expected_digest = expected_image.rsplit("@", 1)[-1]
        if status.get("imageDigest") not in {expected_image, expected_digest}:
            raise ValueError("candidate revision status image digest readback failed")
    conditions = status.get("conditions") or []
    if not isinstance(conditions, list):
        raise ValueError("candidate revision conditions are not a list")
    if not any(
        item.get("type") == "Ready" and str(item.get("status")).lower() == "true"
        for item in conditions
        if isinstance(item, dict)
    ):
        raise ValueError("candidate revision is not Ready")

    planes = traffic_planes(service, label="candidate service")
    candidate_rows = _candidate_traffic_rows(
        planes,
        expected_revision=expected_revision,
        candidate_tag=candidate_tag,
    )
    require_reconciled_traffic(planes, label="candidate service")
    candidate_url = candidate_rows["status"].get("url")
    if not isinstance(candidate_url, str):
        raise ValueError("candidate traffic tag has no valid HTTPS URL")
    parsed_url = urlparse(candidate_url)
    if parsed_url.scheme != "https" or not parsed_url.netloc:
        raise ValueError("candidate traffic tag has no valid HTTPS URL")
    return {
        "candidateRevision": expected_revision,
        "candidateTag": candidate_tag,
        "candidateUrl": candidate_url,
        "gitSha": expected_git_sha,
        "image": expected_image,
        "ready": True,
        "trafficPercent": 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify one immutable zero-traffic Monitor candidate and its tag"
    )
    parser.add_argument("--service-json", required=True)
    parser.add_argument("--revision-json", required=True)
    parser.add_argument("--expected-project", required=True)
    parser.add_argument("--expected-region", required=True)
    parser.add_argument("--expected-service", required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-revision", required=True)
    parser.add_argument("--expected-service-account", required=True)
    parser.add_argument("--expected-git-sha", required=True)
    parser.add_argument("--candidate-tag", default="candidate")
    args = parser.parse_args()
    try:
        receipt = verify_candidate(
            service=_load(args.service_json),
            revision=_load(args.revision_json),
            expected_project=args.expected_project,
            expected_region=args.expected_region,
            expected_service=args.expected_service,
            expected_image=args.expected_image,
            expected_revision=args.expected_revision,
            expected_service_account=args.expected_service_account,
            expected_git_sha=args.expected_git_sha,
            candidate_tag=args.candidate_tag,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
