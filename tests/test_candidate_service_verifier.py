from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.verify_candidate_service import verify_candidate


PROJECT = "lcs-developer-483404"
PROJECT_NUMBER = "643644246736"
REGION = "us-central1"
REVISION = "oura-navi-monitor-abcdef0-12345678"
SERVICE = "oura-navi-monitor"
IMAGE = "us-central1-docker.pkg.dev/project/repository/image@sha256:" + "a" * 64
IMAGE_DIGEST = "sha256:" + "a" * 64
SERVICE_ACCOUNT = "runtime@project.iam.gserviceaccount.com"
GIT_SHA = "b" * 40
SERVICE_FULL = f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}"
REVISION_FULL = f"{SERVICE_FULL}/revisions/{REVISION}"


def _revision() -> dict[str, Any]:
    return {
        "metadata": {
            "name": REVISION,
            "namespace": PROJECT_NUMBER,
            "generation": "1",
            "labels": {
                "git-sha": GIT_SHA,
                "cloud.googleapis.com/location": REGION,
            },
        },
        "spec": {
            "containers": [{"image": IMAGE}],
            "serviceAccountName": SERVICE_ACCOUNT,
        },
        "status": {
            "observedGeneration": 1,
            "conditions": [{"type": "Ready", "status": "True"}],
        },
    }


def _service(*, revision: str = REVISION, percent: int = 0) -> dict[str, Any]:
    desired_traffic = [
        {
            "revisionName": "oura-navi-monitor-live",
            "percent": 100,
        },
        {
            "revisionName": revision,
            "tag": "candidate",
            "percent": percent,
        },
    ]
    observed_traffic = [
        dict(desired_traffic[0]),
        {
            **desired_traffic[1],
            "url": "https://candidate---oura-navi-monitor.example.run.app",
        },
    ]
    return {
        "metadata": {
            "name": SERVICE,
            "namespace": PROJECT_NUMBER,
            "generation": "8",
            "labels": {"cloud.googleapis.com/location": REGION},
        },
        "spec": {"traffic": desired_traffic},
        "status": {
            "observedGeneration": 8,
            "conditions": [{"type": "Ready", "status": "True"}],
            "latestReadyRevisionName": REVISION,
            "traffic": observed_traffic,
        },
    }


def _verify(
    service: dict[str, Any], revision: dict[str, Any] | None = None
) -> dict[str, Any]:
    return verify_candidate(
        service=service,
        revision=revision if revision is not None else _revision(),
        expected_project_id=PROJECT,
        expected_project_number=PROJECT_NUMBER,
        expected_region=REGION,
        expected_service=SERVICE,
        expected_image=IMAGE,
        expected_revision=REVISION,
        expected_service_account=SERVICE_ACCOUNT,
        expected_git_sha=GIT_SHA,
        candidate_tag="candidate",
    )


def test_candidate_tag_binds_exact_revision_url_and_zero_traffic() -> None:
    receipt = _verify(_service())

    assert receipt["candidateRevision"] == REVISION
    assert receipt["candidateUrl"].startswith("https://candidate---")
    assert receipt["trafficPercent"] == 0


def test_candidate_accepts_omitted_protobuf_zero_percent_on_a_tagged_target() -> None:
    service = _service()
    service["status"]["traffic"][1].pop("percent")

    assert _verify(service)["trafficPercent"] == 0


def test_candidate_accepts_exact_bare_and_full_resource_names_together() -> None:
    service = _service()
    service["name"] = SERVICE_FULL
    revision = _revision()
    revision["name"] = REVISION_FULL

    receipt = _verify(service, revision)

    assert receipt["candidateRevision"] == REVISION


def test_candidate_accepts_cloud_run_v1_numeric_namespace() -> None:
    service = _service()
    revision = _revision()
    service["metadata"]["namespace"] = PROJECT_NUMBER
    revision["metadata"]["namespace"] = PROJECT_NUMBER

    assert _verify(service, revision)["candidateRevision"] == REVISION


def test_candidate_cli_uses_distinct_project_id_and_number(tmp_path: Path) -> None:
    service_path = tmp_path / "service.json"
    revision_path = tmp_path / "revision.json"
    service_path.write_text(json.dumps(_service()), encoding="utf-8")
    revision_path.write_text(json.dumps(_revision()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/verify_candidate_service.py",
            "--service-json",
            str(service_path),
            "--revision-json",
            str(revision_path),
            "--expected-project-id",
            PROJECT,
            "--expected-project-number",
            PROJECT_NUMBER,
            "--expected-region",
            REGION,
            "--expected-service",
            SERVICE,
            "--expected-image",
            IMAGE,
            "--expected-revision",
            REVISION,
            "--expected-service-account",
            SERVICE_ACCOUNT,
            "--expected-git-sha",
            GIT_SHA,
            "--candidate-tag",
            "candidate",
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["candidateRevision"] == REVISION


def test_candidate_rejects_bare_names_without_project_and_region_evidence() -> None:
    service = _service()
    revision = _revision()
    service["metadata"].pop("namespace")
    service["metadata"]["labels"].pop("cloud.googleapis.com/location")
    revision["metadata"].pop("namespace")
    revision["metadata"]["labels"].pop("cloud.googleapis.com/location")

    with pytest.raises(ValueError, match="exact project evidence"):
        _verify(service, revision)


def test_candidate_rejects_another_numeric_project_namespace() -> None:
    service = _service()
    service["metadata"]["namespace"] = "999999999999"

    with pytest.raises(ValueError, match="exact project evidence"):
        _verify(service)


def test_candidate_waits_for_revision_generation_to_be_observed() -> None:
    revision = _revision()
    revision["metadata"]["generation"] = "2"

    with pytest.raises(ValueError, match="generation has not been fully observed"):
        _verify(_service(), revision)


def test_candidate_rejects_terminal_revision_reconciliation_failure() -> None:
    revision = _revision()
    revision["status"]["conditions"] = [
        {
            "type": "Ready",
            "status": "False",
            "reason": "HealthCheckContainerError",
            "message": "container did not become ready",
        }
    ]

    with pytest.raises(
        ValueError,
        match="reconciliation failed: reason=HealthCheckContainerError",
    ):
        _verify(_service(), revision)


@pytest.mark.parametrize("image_digest", [IMAGE, IMAGE_DIGEST])
def test_candidate_accepts_matching_optional_status_image_digest(
    image_digest: str,
) -> None:
    revision = _revision()
    revision["status"]["imageDigest"] = image_digest

    assert _verify(_service(), revision)["image"] == IMAGE


def test_candidate_uses_spec_image_when_status_digest_is_absent() -> None:
    assert _verify(_service(), _revision())["image"] == IMAGE


def test_candidate_rejects_mutable_image_even_when_revision_matches_it() -> None:
    revision = _revision()
    mutable_image = "us-central1-docker.pkg.dev/project/repository/image:latest"
    revision["spec"]["containers"][0]["image"] = mutable_image

    with pytest.raises(ValueError, match="complete immutable"):
        verify_candidate(
            service=_service(),
            revision=revision,
            expected_project_id=PROJECT,
            expected_project_number=PROJECT_NUMBER,
            expected_region=REGION,
            expected_service=SERVICE,
            expected_image=mutable_image,
            expected_revision=REVISION,
            expected_service_account=SERVICE_ACCOUNT,
            expected_git_sha=GIT_SHA,
            candidate_tag="candidate",
        )


@pytest.mark.parametrize(
    ("service", "message"),
    [
        (_service(revision="oura-navi-monitor-stale"), "stale revision"),
        (_service(percent=1), "production traffic"),
        (
            {
                "metadata": {
                    "name": SERVICE,
                    "namespace": PROJECT_NUMBER,
                    "generation": "8",
                    "labels": {"cloud.googleapis.com/location": REGION},
                },
                "spec": {
                    "traffic": [
                        {"revisionName": "oura-navi-monitor-live", "percent": 100}
                    ]
                },
                "status": {
                    "observedGeneration": 8,
                    "conditions": [{"type": "Ready", "status": "True"}],
                    "latestReadyRevisionName": REVISION,
                    "traffic": [
                        {"revisionName": "oura-navi-monitor-live", "percent": 100}
                    ],
                },
            },
            "resolve exactly once",
        ),
    ],
)
def test_candidate_verifier_rejects_stale_missing_or_traffic_tag(
    service: dict[str, Any],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _verify(service)


def test_candidate_rejects_conflicting_service_name_sources() -> None:
    service = _service()
    service["name"] = (
        f"projects/another-project/locations/{REGION}/services/{SERVICE}"
    )

    with pytest.raises(ValueError, match="another service"):
        _verify(service)


@pytest.mark.parametrize(
    "observed",
    [
        f"projects/another-project/locations/{REGION}/services/{SERVICE}",
        f"projects/{PROJECT}/locations/another-region/services/{SERVICE}",
        f"projects/{PROJECT}/locations/{REGION}/services/another-service",
    ],
)
def test_candidate_rejects_wrong_full_service_resource(observed: str) -> None:
    service = _service()
    service["metadata"]["name"] = observed

    with pytest.raises(ValueError, match="another service"):
        _verify(service)


def test_candidate_rejects_conflicting_revision_name_sources() -> None:
    revision = _revision()
    revision["name"] = (
        f"projects/{PROJECT}/locations/{REGION}/services/another-service/"
        f"revisions/{REVISION}"
    )

    with pytest.raises(ValueError, match="another revision"):
        _verify(_service(), revision)


@pytest.mark.parametrize(
    "observed",
    [
        f"projects/another-project/locations/{REGION}/services/{SERVICE}/revisions/{REVISION}",
        f"projects/{PROJECT}/locations/another-region/services/{SERVICE}/revisions/{REVISION}",
        f"projects/{PROJECT}/locations/{REGION}/services/another-service/revisions/{REVISION}",
        f"projects/{PROJECT}/locations/{REGION}/revisions/{REVISION}",
        f"projects/{PROJECT}/locations/{REGION}/services/{SERVICE}/revisions/another-revision",
    ],
)
def test_candidate_rejects_wrong_or_legacy_full_revision_resource(
    observed: str,
) -> None:
    revision = _revision()
    revision["metadata"]["name"] = observed

    with pytest.raises(ValueError, match="another revision"):
        _verify(_service(), revision)


@pytest.mark.parametrize("payload", [{}, {"metadata": {}}, {"name": ""}])
def test_candidate_rejects_missing_service_identity(payload: dict[str, Any]) -> None:
    payload["status"] = _service()["status"]

    with pytest.raises(ValueError, match="no resource identity"):
        _verify(payload)


def test_candidate_rejects_missing_revision_identity() -> None:
    revision = _revision()
    del revision["metadata"]["name"]

    with pytest.raises(ValueError, match="no resource identity"):
        _verify(_service(), revision)


@pytest.mark.parametrize("bad_entry", ["traffic", 1, None, []])
def test_candidate_rejects_non_object_traffic_entry(bad_entry: Any) -> None:
    service = _service()
    service["status"]["traffic"].append(bad_entry)

    with pytest.raises(ValueError, match="traffic entry is not an object"):
        _verify(service)


@pytest.mark.parametrize("percent", [True, False, "0", 0.0, None])
def test_candidate_rejects_non_integer_traffic_percent(percent: Any) -> None:
    service = _service()
    service["status"]["traffic"][1]["percent"] = percent

    with pytest.raises(ValueError, match="percentage is not an integer"):
        _verify(service)


def test_candidate_rejects_missing_traffic_percent_on_untagged_target() -> None:
    service = _service()
    del service["status"]["traffic"][0]["percent"]

    with pytest.raises(ValueError, match="percentage is not an integer"):
        _verify(service)


@pytest.mark.parametrize("percent", [-1, 101])
def test_candidate_rejects_out_of_range_traffic_percent(percent: int) -> None:
    service = _service()
    service["status"]["traffic"][1]["percent"] = percent

    with pytest.raises(ValueError, match="outside 0..100"):
        _verify(service)


def test_candidate_rejects_duplicate_candidate_tag_rows() -> None:
    service = _service()
    service["status"]["traffic"].append(
        {
            "revisionName": REVISION,
            "tag": "candidate",
            "percent": 0,
            "url": "https://duplicate.example.run.app",
        }
    )

    with pytest.raises(ValueError, match="tag must resolve exactly once"):
        _verify(service)


def test_candidate_rejects_duplicate_target_revision_rows() -> None:
    service = _service()
    service["status"]["traffic"].append(
        {"revisionName": REVISION, "tag": "another-tag", "percent": 0}
    )

    with pytest.raises(ValueError, match="revision traffic must resolve exactly once"):
        _verify(service)


def test_candidate_rejects_positive_target_on_any_traffic_row() -> None:
    service = _service()
    service["status"]["traffic"].append(
        {"revisionName": REVISION, "tag": "another-tag", "percent": 1}
    )

    with pytest.raises(ValueError, match="production traffic"):
        _verify(service)


def test_candidate_rejects_positive_desired_target_hidden_by_zero_status() -> None:
    service = _service()
    service["spec"]["traffic"][1]["percent"] = 1

    with pytest.raises(ValueError, match="desired traffic.*production traffic"):
        _verify(service)


def test_candidate_requires_exact_candidate_row_in_desired_and_observed_traffic() -> None:
    service = _service()
    service["spec"]["traffic"][1]["revisionName"] = "oura-navi-monitor-stale"

    with pytest.raises(ValueError, match="desired traffic tag points to a stale revision"):
        _verify(service)


def test_candidate_desired_tag_cannot_use_latest_revision_indirection() -> None:
    service = _service()
    desired_candidate = service["spec"]["traffic"][1]
    del desired_candidate["revisionName"]
    desired_candidate["latestRevision"] = True

    with pytest.raises(ValueError, match="not pinned to the exact revision"):
        _verify(service)


def test_candidate_rejects_unreconciled_service_generation() -> None:
    service = _service()
    service["status"]["observedGeneration"] = 7

    with pytest.raises(ValueError, match="generation has not been fully observed"):
        _verify(service)


def test_candidate_rejects_desired_observed_positive_traffic_disagreement() -> None:
    service = _service()
    service["spec"]["traffic"][0]["revisionName"] = "oura-navi-monitor-other-live"

    with pytest.raises(ValueError, match="desired and observed positive traffic disagree"):
        _verify(service)


@pytest.mark.parametrize("containers", [[], [{}, {}], ["not-an-object"]])
def test_candidate_requires_exactly_one_object_container(
    containers: list[Any],
) -> None:
    revision = _revision()
    revision["spec"]["containers"] = containers

    message = "exactly one container" if len(containers) != 1 else "not an object"
    with pytest.raises(ValueError, match=message):
        _verify(_service(), revision)


def test_candidate_never_uses_status_digest_as_container_image_fallback() -> None:
    revision = _revision()
    revision["spec"]["containers"][0]["image"] = (
        "us-central1-docker.pkg.dev/project/repository/other@sha256:" + "a" * 64
    )
    revision["status"]["imageDigest"] = IMAGE

    with pytest.raises(ValueError, match="image digest readback failed"):
        _verify(_service(), revision)


@pytest.mark.parametrize(
    "image_digest",
    [
        None,
        "sha256:" + "c" * 64,
        "us-central1-docker.pkg.dev/project/repository/other@sha256:" + "a" * 64,
    ],
)
def test_candidate_rejects_conflicting_optional_status_image_digest(
    image_digest: Any,
) -> None:
    revision = _revision()
    revision["status"]["imageDigest"] = image_digest

    with pytest.raises(ValueError, match="status image digest readback failed"):
        _verify(_service(), revision)
