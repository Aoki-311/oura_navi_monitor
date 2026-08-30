from __future__ import annotations

import pytest

from scripts.verify_service_access_contract import verify


def _service(
    *,
    ingress: str = "internal-and-cloud-load-balancing",
    live_revision: str = "oura-navi-monitor-00001-live",
    generation: int = 7,
    latest_ready_revision: str | None = None,
) -> dict:
    return {
        "metadata": {
            "generation": str(generation),
            "annotations": {
                "run.googleapis.com/ingress": ingress,
                "run.googleapis.com/custom-audiences": '["monitor"]',
                "run.googleapis.com/invoker-iam-disabled": "false",
            }
        },
        "spec": {
            "defaultUriDisabled": False,
            "traffic": [{"revisionName": live_revision, "percent": 100}],
        },
        "status": {
            "observedGeneration": generation,
            "latestReadyRevisionName": latest_ready_revision or live_revision,
            "traffic": [
                {"revisionName": live_revision, "percent": 100},
            ]
        },
    }


def _iam(*, member: str = "serviceAccount:caller@example.com") -> dict:
    return {
        "version": 1,
        "etag": "changes-without-semantic-drift",
        "bindings": [{"role": "roles/run.invoker", "members": [member]}],
    }


def test_candidate_deploy_preserves_access_and_semantic_iam() -> None:
    before_iam = _iam()
    after_iam = _iam()
    after_iam["etag"] = "new-etag"
    candidate_revision = "oura-navi-monitor-00002-candidate"
    after_service = _service(
        generation=8,
        latest_ready_revision=candidate_revision,
    )
    after_service["spec"]["traffic"].append(
        {
            "revisionName": candidate_revision,
            "tag": "candidate",
            "percent": 0,
        }
    )
    after_service["status"]["traffic"].append(
        {
            "revisionName": candidate_revision,
            "tag": "candidate",
            "percent": 0,
        }
    )
    verify(_service(), after_service, before_iam, after_iam)


@pytest.mark.parametrize(
    ("after_service", "after_iam", "message"),
    [
        (_service(ingress="all"), _iam(), "access configuration"),
        (_service(), _iam(member="allUsers"), "IAM policy"),
    ],
)
def test_candidate_deploy_rejects_any_access_or_iam_drift(
    after_service: dict, after_iam: dict, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        verify(_service(), after_service, _iam(), after_iam)


def test_candidate_deploy_rejects_positive_traffic_drift() -> None:
    with pytest.raises(ValueError, match="positive Cloud Run traffic"):
        verify(
            _service(),
            _service(live_revision="oura-navi-monitor-00003-other"),
            _iam(),
            _iam(),
        )


def test_candidate_deploy_rejects_desired_traffic_drift_hidden_by_old_status() -> None:
    before = _service()
    after = _service(generation=8)
    after["spec"]["traffic"] = [
        {"revisionName": "oura-navi-monitor-00002-candidate", "percent": 100}
    ]

    with pytest.raises(ValueError, match="desired and observed positive traffic disagree"):
        verify(before, after, _iam(), _iam())


def test_candidate_deploy_rejects_unobserved_service_generation() -> None:
    after = _service(generation=8)
    after["status"]["observedGeneration"] = 7

    with pytest.raises(ValueError, match="generation has not been fully observed"):
        verify(_service(), after, _iam(), _iam())


@pytest.mark.parametrize("field", ["generation", "observedGeneration"])
def test_candidate_deploy_requires_both_generation_fields(field: str) -> None:
    after = _service(generation=8)
    container = after["metadata"] if field == "generation" else after["status"]
    del container[field]

    with pytest.raises(ValueError, match="is not a positive integer"):
        verify(_service(), after, _iam(), _iam())


def test_latest_revision_desired_traffic_is_resolved_before_comparison() -> None:
    before = _service()
    before["spec"]["traffic"] = [{"latestRevision": True, "percent": 100}]
    after = _service(generation=8)

    verify(before, after, _iam(), _iam())
