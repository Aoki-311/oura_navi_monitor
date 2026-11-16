"""Routine updates fail closed without real cloud writes in this test suite."""
from argparse import Namespace
import copy
import json
from pathlib import Path

import pytest

from scripts import routine_release as r


def manifest(tmp_path):
    workbook = tmp_path / "roster.xlsx"
    workbook.write_bytes(b"approved workbook")
    dependency = tmp_path / "dependency.json"
    dependency.write_text("{}")
    sha, build = "a" * 40, "12345678-1234-1234-1234-123456789abc"
    sa = lambda name: f"{name}@{r.PROJECT}.iam.gserviceaccount.com"
    return {"protocol": r.PROTOCOL, "project": r.PROJECT, "projectNumber": "12345", "region": r.REGION,
            "service": r.SERVICE, "job": r.JOB, "gitSha": sha, "buildId": build,
            "revision": f"{r.SERVICE}-{sha[:7]}-{build[:8]}", "image": f"{r.REGION}-docker.pkg.dev/{r.PROJECT}/cloud-run-source-deploy/{r.SERVICE}@sha256:" + "b" * 64,
            "webServiceAccount": sa("web"), "jobServiceAccount": sa("writer"), "actor": sa("operator"),
            "previousRevision": f"{r.SERVICE}-old", "schedulerAccounts": {r.SCHEDULER: sa("scheduler"), r.LEGACY_SCHEDULER: sa("scheduler")},
            "legacyTransferResource": f"projects/{r.PROJECT}/locations/us/transferConfigs/existing",
            "workbook": str(workbook), "workbookSha256": r.file_digest(workbook),
            "legacyDependencyEvidence": str(dependency), "legacyDependencyEvidenceSha256": r.file_digest(dependency),
            "expectedRoster": {"total": 183, "active": 176, "inactive": 7, "deleted": 0, "global": 150, "userMap": 172}}


def inventory(m):
    schedulers = {}
    for name in (r.SCHEDULER, r.LEGACY_SCHEDULER):
        schedulers[name] = {"name": f"projects/{r.PROJECT}/locations/{r.REGION}/jobs/{name}",
                            "schedule": "5 * * * *", "timeZone": "Asia/Tokyo", "attemptDeadline": "60s",
                            "state": "PAUSED", "httpTarget": {"uri": r.JOB_URI, "oauthToken": {"serviceAccountEmail": m["schedulerAccounts"][name]}}}
    pipeline = {source: {"source": source, "published_run_id": source + "-run", "status": "succeeded", "scope_policy_version": "summary_department_v3", "lease_run_id": None, "lease_expires_at": None} for source in ("published", "news_usage")}
    pipeline["news_usage"]["roster_snapshot_run_id"] = "published-run"
    return {"schedulers": schedulers, "executions": [], "writers": [], "pipeline": pipeline,
            "service": {"status": {"url": "https://production.example", "traffic": [{"tag": "candidate", "url": "https://candidate.example"}]}},
            "revision": {"spec": {"containers": [{"env": [{"name": "MONITOR_ADMIN_ALLOWLIST", "value": "admin@example.com"}]}]}},
            "job": {"image": "old"}, "iam": {}, "legacyTransfer": {"disabled": True}}


def state_for(m, live, stage):
    baseline = copy.deepcopy(live)
    baseline["schedulers"][r.SCHEDULER]["state"] = "ENABLED"
    return {"protocol": r.PROTOCOL, "manifest": m, "manifestSha256": r.digest(m), "intentSha256": "c" * 64,
            "baseline": baseline, "completedStages": list(r.STAGES[:r.STAGES.index(stage)]), "pendingStage": None,
            "controllerCodeSha256": r.file_digest(r.__file__), "initialLabelsSha256": r.digest([]), "syncedRosterSha256": "roster"}


def args_for(state, stage, **extra):
    value = dict(stage=stage, apply=True, recover=False, confirm=r.confirmation(state, stage), acceptance_receipt=None,
                 expected_sync_digest="digest", execution_name="", natural_evidence=None)
    return Namespace(**(value | extra))


class FakeCloud:
    def __init__(self, m, live):
        self.m, self.live, self.commands = m, live, []
        self.permissions_ok = True
        self.fail_resume_readback = False
    def inventory(self):
        if self.fail_resume_readback and self.live["schedulers"][r.SCHEDULER]["state"] == "ENABLED":
            self.fail_resume_readback = False
            raise ValueError("readback interrupted")
        return copy.deepcopy(self.live)
    def gcloud(self, *args, **kwargs):
        self.commands.append(args)
        if args[:3] == ("scheduler", "jobs", "resume"):
            self.live["schedulers"][args[3]]["state"] = "ENABLED"
        if args[:3] == ("scheduler", "jobs", "pause"):
            self.live["schedulers"][args[3]]["state"] = "PAUSED"
        if args[:3] == ("run", "jobs", "execute"):
            return {"metadata": {"name": r.JOB + "-abcde"}}
        return {}
    def permission_preflight(self):
        if not self.permissions_ok:
            raise ValueError("required permissions missing")
        return {"checkedAt": r.now()}
    def roster_summary(self):
        return self.m["expectedRoster"]
    def roster_fingerprint(self):
        return "roster"
    def directory(self):
        return self
    def list_labels(self, **kwargs):
        return []
    def validate_data(self):
        return {"verified": True}
    def sync_plan(self):
        return None, {"syncDigest": "digest", "resultingRosterCounts": {"registered": 183, "active": 176, "inactive": 7, "deleted": 0}, "resultingScopeCounts": {"global": 150, "user_map": 172}}
    def sync(self, digest):
        self.commands.append(("sync", digest))
        return {"applied": True}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    m = manifest(tmp_path)
    live = inventory(m)
    cloud = FakeCloud(m, live)
    monkeypatch.setattr(r, "verify_operator_source", lambda sha: None)
    monkeypatch.setattr(r, "check_invariant", lambda *a, **kw: None)
    monkeypatch.setattr(r, "own_locks", lambda *a, **kw: None)
    monkeypatch.setattr(r, "validate_acceptance", lambda *a, **kw: None)
    acceptance = tmp_path / "acceptance.json"
    acceptance.write_text("{}")
    return m, live, cloud, tmp_path / "state.json", acceptance


def test_manifest_is_exact_and_binds_workbook(tmp_path):
    m = manifest(tmp_path)
    assert r.manifest_contract(m) == m
    Path(m["workbook"]).write_bytes(b"changed")
    with pytest.raises(ValueError, match="workbook"):
        r.manifest_contract(m)


@pytest.mark.parametrize("change", ["writer", "chat-lease", "news-lease", "execution", "scheduler", "inconsistent-lease"])
def test_quiescence_covers_both_owners_and_all_writers(tmp_path, change):
    live = inventory(manifest(tmp_path))
    if change == "writer": live["writers"] = [{"state": "RUNNING"}]
    elif change.endswith("lease"):
        row = live["pipeline"]["published" if change == "chat-lease" else "news_usage"]
        row["lease_expires_at"] = "2099-01-01T00:00:00Z"
        if change != "inconsistent-lease": row["lease_run_id"] = "owner"
    elif change == "execution": live["executions"] = [{"status": {}}]
    else: live["schedulers"][r.SCHEDULER]["state"] = "ENABLED"
    with pytest.raises(ValueError): r.validate_quiescence(live)


def test_unknown_refresh_trigger_rejected_unrelated_ignored(tmp_path):
    m = manifest(tmp_path)
    rows = list(inventory(m)["schedulers"].values())
    assert len(r.validate_schedulers(rows + [{"name": "unrelated", "httpTarget": {"uri": "https://other.example"}}], m)) == 2
    with pytest.raises(ValueError, match="unknown actual"):
        r.validate_schedulers(rows + [{"name": "unexpected", "httpTarget": {"uri": r.JOB_URI}}], m)


def test_order_and_confirm_before_mutation(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "freeze")
    with pytest.raises(ValueError, match="out of order"):
        r.run_stage(cloud, state, path, args_for(state, "update-job"))
    with pytest.raises(ValueError, match="confirmation"):
        r.run_stage(cloud, state, path, args_for(state, "freeze", confirm="yes"))
    assert not cloud.commands


def test_controller_change_stops_even_recovery(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "freeze")
    state["controllerCodeSha256"] = "wrong"
    with pytest.raises(ValueError, match="controller changed"):
        r.run_stage(cloud, state, path, args_for(state, "freeze", acceptance_receipt=receipt))
    assert not cloud.commands


def test_permissions_checked_before_pause(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "freeze")
    cloud.permissions_ok = False
    with pytest.raises(ValueError, match="permissions"):
        r.run_stage(cloud, state, path, args_for(state, "freeze", acceptance_receipt=receipt))
    assert not cloud.commands


def test_plan_never_mutates_or_creates_checkpoint(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "sync-roster")
    r.run_stage(cloud, state, path, args_for(state, "sync-roster", apply=False))
    assert not cloud.commands and not path.exists()


@pytest.mark.parametrize("drift", ["digest", "counts"])
def test_sync_requires_fresh_digest_and_expected_counts_before_write(setup, drift):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "sync-roster")
    if drift == "counts": m["expectedRoster"]["active"] = 175
    with pytest.raises(ValueError):
        r.run_stage(cloud, state, path, args_for(state, "sync-roster", expected_sync_digest="wrong" if drift == "digest" else "digest"))
    assert not cloud.commands


def test_refresh_submission_not_repeated_on_uncertain_recovery(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "refresh")
    with pytest.raises(r.Pending, match="submitted"):
        r.run_stage(cloud, state, path, args_for(state, "refresh"))
    assert state["refreshExecution"] == r.JOB + "-abcde"
    state.pop("refreshExecution")  # Simulate loss between provider success and checkpoint.
    with pytest.raises(ValueError, match="outcome unknown"):
        r.run_stage(cloud, state, path, args_for(state, "refresh", recover=True))
    assert len(cloud.commands) == 1


def test_restore_recovers_after_successful_resume_and_failed_readback(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "restore-schedulers")
    cloud.fail_resume_readback = True
    with pytest.raises(ValueError, match="interrupted"):
        r.run_stage(cloud, state, path, args_for(state, "restore-schedulers"))
    restored = state["restoredAt"]
    r.run_stage(cloud, state, path, args_for(state, "restore-schedulers", recover=True))
    assert len(cloud.commands) == 1
    assert state["restoredAt"] == restored
    assert live["schedulers"][r.LEGACY_SCHEDULER]["state"] == "PAUSED"


def test_restore_refuses_accidentally_enabled_originally_paused_trigger(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "restore-schedulers")
    state.update(pendingStage="restore-schedulers", restoredAt=r.now())
    live["schedulers"][r.LEGACY_SCHEDULER]["state"] = "ENABLED"
    with pytest.raises(ValueError, match="originally paused"):
        r.run_stage(cloud, state, path, args_for(state, "restore-schedulers", recover=True))
    assert not cloud.commands


def test_complete_recovers_partial_lock_release_only_after_final_checkpoint(setup, monkeypatch):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "complete")
    state["restoredAt"] = r.now()
    live["schedulers"][r.SCHEDULER]["state"] = "ENABLED"
    cloud.fs = object()
    calls = []
    monkeypatch.setattr(r, "validate_natural", lambda *a: {"verified": True})
    monkeypatch.setattr(r, "own_locks", lambda *a, **kw: calls.append(kw))
    monkeypatch.setattr(r, "release_operation_lock", lambda *a: calls.append("release-refresh"))
    def release(*a):
        if calls.count("release-refresh") == 1: raise ValueError("transport interrupted")
    monkeypatch.setattr(r, "release_release_lock", release)
    with pytest.raises(ValueError, match="transport"):
        r.run_stage(cloud, state, path, args_for(state, "complete", natural_evidence=receipt))
    assert state["finalVerifiedAt"]
    r.run_stage(cloud, state, path, args_for(state, "complete", recover=True, natural_evidence=receipt))
    assert {"releasing": True} in calls


def acceptance(tmp_path, m, live):
    artifact = tmp_path / "real-evidence.json"
    artifact.write_text('{"real":true}')
    live["pipeline"]["published"]["scope_policy_version"] = "summary_department_v2"
    return {"receiptType": "monitor.routine-acceptance.v1", "stage": "candidate-v2", **{key: m[key] for key in ("project", "region", "service", "revision", "image", "gitSha", "buildId")},
            "origin": "https://candidate.example", "capturedAt": r.now(), "verifiedBy": "operator", "authenticatedApiAcceptance": True, "loggedInBrowserAcceptance": True, "readOnlyAcceptance": True,
            "authenticatedIdentity": {"source": "iap", "verified": True, "email": "admin@example.com"},
            "apiEvidence": {"path": str(artifact), "sha256": r.file_digest(artifact)}, "browserEvidence": {"path": str(artifact), "sha256": r.file_digest(artifact)},
            "endpointStatus": {key: 200 for key in ("overview", "regions", "users", "userDetail", "news", "newsCsv", "management")},
            "chatPublishedRunId": "published-run", "newsPublishedRunId": "news_usage-run"}


@pytest.mark.parametrize("field", ["valid", "origin", "identity", "stale", "artifact", "publication", "endpoint"])
def test_real_acceptance_binds_identity_origin_artifacts_and_publication(tmp_path, field):
    m = manifest(tmp_path)
    live = inventory(m)
    receipt = acceptance(tmp_path, m, live)
    if field == "origin": receipt["origin"] = "https://production.example"
    elif field == "identity": receipt["authenticatedIdentity"]["email"] = m["actor"]
    elif field == "stale": receipt["capturedAt"] = "2020-01-01T00:00:00Z"
    elif field == "artifact": receipt["apiEvidence"]["sha256"] = "wrong"
    elif field == "publication": receipt["chatPublishedRunId"] = "different"
    elif field == "endpoint": receipt["endpointStatus"]["newsCsv"] = 401
    if field == "valid": r.validate_acceptance(receipt, m, live, "candidate-v2")
    else:
        with pytest.raises(ValueError): r.validate_acceptance(receipt, m, live, "candidate-v2")


def proof(m, live, trigger="scheduler_hourly"):
    name = r.JOB + "-abcde"
    creator = m["schedulerAccounts"][r.SCHEDULER] if trigger == "scheduler_hourly" else m["actor"]
    return {"capturedAt": "2026-11-17T00:10:00Z", "execution": {"metadata": {"name": name, "creationTimestamp": "2026-11-17T00:05:01Z", "labels": {"run.googleapis.com/job": r.JOB}, "annotations": {"run.googleapis.com/creator": creator}},
            "spec": {"serviceAccountName": m["jobServiceAccount"], "containers": [{"image": m["image"], "args": ["-m", "app.jobs.refresh_analytics", "--apply", "--trigger-source", trigger]}]}, "status": {"completionTime": "2026-11-17T00:08:00Z", "succeededCount": 1, "failedCount": 0, "conditions": [{"type": "Completed", "status": "True"}]}},
            "runs": [{"source": source, "run_id": row["published_run_id"], "status": "succeeded", "trigger_source": trigger, "started_at": "2026-11-17T00:05:02Z", "execution_id": name if source == "published" else "local-news-id"} for source, row in live["pipeline"].items()],
            "events": [{"timestamp": "2026-11-17T00:08:00Z", "insertId": "event", "resource": {"type": "cloud_run_job", "labels": {"project_id": r.PROJECT, "location": r.REGION, "job_name": r.JOB}}, "labels": {"run.googleapis.com/execution_name": name}, "jsonPayload": {"monitor_pipeline_event": True, "status": "succeeded", "runId": "published-run", "newsUsage": {"status": "succeeded", "publishedRunId": "news_usage-run", "rosterSnapshotRunId": "published-run"}}}],
            "audits": [{"timestamp": "2026-11-17T00:05:00Z", "protoPayload": {"serviceName": "run.googleapis.com", "methodName": "google.cloud.run.v2.Jobs.RunJob", "authenticationInfo": {"principalEmail": creator}, "resourceName": f"projects/{r.PROJECT}/locations/{r.REGION}/jobs/{r.JOB}", "response": {"metadata": {"target": f"projects/{r.PROJECT}/locations/{r.REGION}/jobs/{r.JOB}/executions/{name}"}}}}],
            "attempts": [{"timestamp": "2026-11-17T00:05:03Z", "insertId": "attempt", "severity": "INFO", "resource": {"type": "cloud_scheduler_job", "labels": {"project_id": r.PROJECT, "location_id": r.REGION, "job_id": r.SCHEDULER}}, "jsonPayload": {"@type": "type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished", "jobName": f"projects/{r.PROJECT}/locations/{r.REGION}/jobs/{r.SCHEDULER}", "targetUrl": r.JOB_URI, "status": "OK"}}]}


@pytest.mark.parametrize("drift", ["valid", "historic", "manual", "chat-execution", "news-pointer", "event", "audit", "attempt-time", "duplicate-attempt"])
def test_one_natural_run_requires_live_exact_provenance(tmp_path, drift):
    m = manifest(tmp_path)
    live = inventory(m)
    p = proof(m, live)
    cloud = FakeCloud(m, live)
    cloud.execution_proof = lambda *a, **kw: p
    state = {"manifest": m, "restoredAt": "2026-11-17T00:00:00Z"}
    if drift == "historic": p["execution"]["metadata"]["creationTimestamp"] = "2026-11-16T00:00:00Z"
    elif drift == "manual": p["execution"]["metadata"]["annotations"]["run.googleapis.com/creator"] = m["actor"]
    elif drift == "chat-execution": p["runs"][0]["execution_id"] = "other"
    elif drift == "news-pointer": live["pipeline"]["news_usage"]["roster_snapshot_run_id"] = "old"
    elif drift == "event": p["events"] = []
    elif drift == "audit": p["audits"][0]["protoPayload"]["authenticationInfo"]["principalEmail"] = m["actor"]
    elif drift == "attempt-time": p["attempts"][0]["timestamp"] = "2026-11-17T00:09:00Z"
    elif drift == "duplicate-attempt": p["attempts"].append(copy.deepcopy(p["attempts"][0]))
    if drift == "valid": assert r.validate_natural(cloud, state, live, {"executionName": r.JOB + "-abcde"}) == p
    else:
        with pytest.raises(ValueError): r.validate_natural(cloud, state, live, {"executionName": r.JOB + "-abcde"})


def test_old_reader_allocation_is_never_accepted_after_promotion(tmp_path, monkeypatch):
    m = manifest(tmp_path)
    live = inventory(m)
    state = state_for(m, live, "sync-roster")
    monkeypatch.setattr(r, "_validate_revision", lambda *a, **kw: None)
    monkeypatch.setattr(r, "require_reconciled_ready", lambda *a, **kw: None)
    monkeypatch.setattr(r, "allocation", lambda service: [{"revisionName": m["previousRevision"], "percent": 100}])
    with pytest.raises(ValueError, match="allocation drift"):
        r.check_invariant(state, live, reader_promoted=True, new_job=True)


def real_invariant_fixture(tmp_path, monkeypatch):
    m = manifest(tmp_path)
    live = inventory(m)
    ready = {"observedGeneration": 1, "conditions": [{"type": "Ready", "status": "True"}]}
    traffic = [{"revisionName": m["previousRevision"], "percent": 100}, {"revisionName": m["revision"], "percent": 0, "tag": "candidate"}]
    live["service"] = {"metadata": {"generation": "1", "annotations": {"run.googleapis.com/ingress": "internal-and-cloud-load-balancing"}}, "spec": {"traffic": copy.deepcopy(traffic)}, "status": {**ready, "traffic": copy.deepcopy(traffic)}}
    live["revision"] = {"metadata": {"name": m["revision"], "generation": "1", "labels": {"git-sha": m["gitSha"]}}, "spec": {"serviceAccountName": m["webServiceAccount"], "containers": [{"image": m["image"]}]}, "status": ready}
    env = {"MONITOR_PROJECT_ID": r.PROJECT, "MONITOR_BQ_DATASET": r.DATASET, "MONITOR_BQ_LOCATION": "US", "MONITOR_SOURCE_SERVICE": "lcs-rag-app", "MONITOR_ANALYTICS_START_AT": "2026-01-01T00:00:00Z", **r.news_usage_environment()}
    live["job"] = {"spec": {"serviceAccountName": m["jobServiceAccount"], "taskCount": 1, "parallelism": 1, "maxRetries": 1, "timeoutSeconds": 1800, "containers": [{"image": m["image"], "command": ["python"], "args": ["-m", "app.jobs.refresh_analytics", "--apply", "--trigger-source", "scheduler_hourly"], "env": [{"name": k, "value": v} for k, v in env.items()]}]}}
    query = "reviewed exact four-table SQL"
    monkeypatch.setattr(r, "LEGACY_QUERY_SHA256", r.hashlib.sha256(query.encode()).hexdigest())
    name = f"{r.PROJECT}.{r.DATASET}.pipeline_state"
    definition = {"kind": "TABLE", "definitionSha256": None, "schemaFields": ["source"], "references": [], "unresolvedQuotedReferences": []}
    evidence = {"readOnly": True, "transferResource": m["legacyTransferResource"], "transferQuerySha256": r.LEGACY_QUERY_SHA256, "legacyWriteTargets": sorted(r.LEGACY_TABLES), "canonicalRoots": [name], "canonicalDependencyClosure": {name: definition}}
    Path(m["legacyDependencyEvidence"]).write_text(json.dumps(evidence))
    m["legacyDependencyEvidenceSha256"] = r.file_digest(m["legacyDependencyEvidence"])
    live["legacyTransfer"] = {"name": m["legacyTransferResource"], "params": {"query": query}, "schedule": "every 15 minutes", "scheduleOptionsV2": {"timeBasedSchedule": {"schedule": "every 15 minutes"}}}
    live["dependencyDefinitions"] = {name: {key: definition[key] for key in ("kind", "definitionSha256", "schemaFields")}}
    live["iam"] = {"version": 1, "bindings": [{"role": "roles/run.invoker", "members": ["serviceAccount:caller@example.com"]}]}
    return m, live, state_for(m, live, "freeze")


@pytest.mark.parametrize("phase", ["candidate", "promoted"])
def test_real_invariant_accepts_candidate_zero_and_promoted_tag_hundred(tmp_path, monkeypatch, phase):
    m, live, state = real_invariant_fixture(tmp_path, monkeypatch)
    if phase == "promoted":
        traffic = [{"revisionName": m["revision"], "percent": 100, "tag": "candidate"}]
        live["service"]["spec"]["traffic"] = copy.deepcopy(traffic)
        live["service"]["status"]["traffic"] = copy.deepcopy(traffic)
    r.check_invariant(state, live, reader_promoted=phase == "promoted", new_job=phase == "promoted")


@pytest.mark.parametrize("drift", ["iam", "access", "job-env", "image", "schedule", "dts-enable", "dts-v2-schedule", "dts-query", "dependency"])
def test_real_invariant_refuses_mutation_on_config_or_dependency_drift(tmp_path, monkeypatch, drift):
    m, live, state = real_invariant_fixture(tmp_path, monkeypatch)
    if drift == "iam": live["iam"]["bindings"][0]["members"].append("allUsers")
    elif drift == "access": live["service"]["metadata"]["annotations"]["run.googleapis.com/ingress"] = "all"
    elif drift == "job-env": live["job"]["spec"]["containers"][0]["env"].append({"name": "UNEXPECTED", "value": "1"})
    elif drift == "image": live["job"]["spec"]["containers"][0]["image"] = "unapproved"
    elif drift == "schedule": live["schedulers"][r.SCHEDULER]["schedule"] = "* * * * *"
    elif drift == "dts-enable": live["legacyTransfer"]["disabled"] = True
    elif drift == "dts-v2-schedule": live["legacyTransfer"]["scheduleOptionsV2"]["timeBasedSchedule"]["schedule"] = "every 5 minutes"
    elif drift == "dts-query": live["legacyTransfer"]["params"]["query"] = "different owner"
    elif drift == "dependency": next(iter(live["dependencyDefinitions"].values()))["schemaFields"].append("changed")
    with pytest.raises(ValueError): r.check_invariant(state, live, reader_promoted=False, new_job=False)


@pytest.mark.parametrize("kind", ["valid", "missing", "other-intent", "retired", "release-recovery"])
def test_routine_checks_actual_lock_owner_schema(tmp_path, kind):
    from types import SimpleNamespace
    m = manifest(tmp_path)
    state = state_for(m, inventory(m), "sync-roster")
    service, refresh = r.identities(state)
    docs = {r.service_lock_document_id(service): r._expected_lock(service) | {"acquiredAt": "now"}, r.operation_lock_document_id(refresh): r.operation_expected(refresh) | {"acquiredAt": "now"}}
    if kind in {"missing", "release-recovery"}: docs.pop(r.operation_lock_document_id(refresh))
    elif kind == "other-intent": docs[r.service_lock_document_id(service)]["intentPayloadSha256"] = "other"
    elif kind == "retired": docs[r.service_lock_document_id(service)]["lockState"] = "retired"
    class FS:
        def collection(self, name): return self
        def document(self, key):
            return SimpleNamespace(get=lambda: SimpleNamespace(exists=key in docs, to_dict=lambda: docs[key]))
    cloud = SimpleNamespace(fs=FS())
    if kind in {"valid", "release-recovery"}: r.own_locks(cloud, state, releasing=kind == "release-recovery")
    else:
        with pytest.raises(ValueError): r.own_locks(cloud, state)


def test_equal_roster_counts_do_not_hide_metadata_drift(setup):
    m, live, cloud, path, receipt = setup
    state = state_for(m, live, "refresh")
    cloud.roster_fingerprint = lambda: "changed-department-same-counts"
    with pytest.raises(ValueError, match="roster metadata"):
        r.run_stage(cloud, state, path, args_for(state, "refresh"))
    assert not cloud.commands


def test_roster_fingerprint_preserves_labels_and_team_but_allows_identity_binding():
    from types import SimpleNamespace
    row = {"roster_id": "r1", "team": "HCS", "label_ids": ["b", "a"], "user_id": ""}
    cloud = SimpleNamespace(directory=lambda: SimpleNamespace(list_users=lambda **kw: [row]))
    original = r.Cloud.roster_fingerprint(cloud)
    row["label_ids"].reverse()
    row["user_id"] = "verified-new-binding"
    assert r.Cloud.roster_fingerprint(cloud) == original
    row["label_ids"].append("c")
    assert r.Cloud.roster_fingerprint(cloud) != original
    row["label_ids"] = ["a", "b"]
    row["team"] = "changed"
    assert r.Cloud.roster_fingerprint(cloud) != original
