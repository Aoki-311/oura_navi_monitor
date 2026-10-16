from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import sys
from types import SimpleNamespace

import pytest

from app.jobs import backfill_roster_history as recovery
from app.jobs.rebuild_history import HistoryRows


START = datetime(2026, 3, 16, tzinfo=timezone.utc)
END = datetime(2026, 8, 21, tzinfo=timezone.utc)


def user(roster_id="r1", **changes):
    return {"_document_id": roster_id, "roster_id": roster_id, "name": "利用者", "email": f"{roster_id}@example.com",
            "area": "北海道東北", "area_key": "北海道東北", "workplace": "新潟", "role": "社員MR",
            "department": "MR(HCS)", "mr_experience": "5年未満", "team": "HCS新潟医療器･医薬品",
            "is_active": True, "label_ids": [], "user_id": f"subject-{roster_id}", "chat_user_id": f"root-{roster_id}", **changes}


class Directory:
    def __init__(self, users):
        self.users = users

    def list_users(self, *, include_inactive):
        assert include_inactive is True
        return self.users


def settings():
    return SimpleNamespace(monitor_project_id="project", monitor_bq_dataset="dataset", monitor_bq_location="US",
                           monitor_query_maximum_bytes=1_000_000, monitor_firestore_read_timeout_seconds=120,
                           monitor_firestore_chat_collection="chat_users", monitor_firestore_database="lcs-user-data",
                           monitor_timezone="Asia/Tokyo", monitor_analytics_start_at=START.isoformat())


def test_selection_validates_full_collection_before_filtering():
    first = user()
    assert [row["roster_id"] for row in recovery.select_users(Directory([first]), ["r1"])] == ["r1"]
    duplicate = user("r2", email=first["email"], is_active=False)
    with pytest.raises(ValueError, match="ambiguous"):
        recovery.select_users(Directory([first, duplicate]), ["r1"])


@pytest.mark.parametrize("ids", [[], ["r1", "r1"], [f"r{x}" for x in range(21)], ["missing"]])
def test_selection_rejects_empty_duplicate_missing_or_unbounded_targets(ids):
    with pytest.raises(ValueError):
        recovery.select_users(Directory([user()]), ids)


def test_selection_requires_bound_identity():
    with pytest.raises(ValueError, match="bound"):
        recovery.select_users(Directory([user(chat_user_id="")]), ["r1"])


@pytest.mark.parametrize("changes", [{"identityVerified": False}, {"userEmail": "other@example.com"}, {"subject": "wrong"}])
def test_root_identity_conflicts_fail_closed(changes):
    root = {"identityVerified": True, "userEmail": "r1@example.com", "subject": "subject-r1", **changes}
    with pytest.raises(ValueError):
        recovery.check_root(user(), root)


class Document:
    def __init__(self, identifier, payload, children=None):
        self.id, self.payload, self.children = identifier, payload, children or {}
        self.exists, self.reference = True, self

    def get(self, **_kwargs):
        return self

    def to_dict(self):
        return self.payload

    def collection(self, name):
        return Collection(self.children.get(name, []))


class Collection:
    def __init__(self, documents):
        self.documents = documents

    def stream(self, **_kwargs):
        return iter(self.documents)

    def document(self, name):
        return next(document for document in self.documents if document.id == name)


class Firestore:
    def __init__(self):
        self.requested = []
        messages = [Document("m1", {"role": "user", "timestamp": "2026-08-20T01:00:00Z", "content": "private question"}),
                    Document("m2", {"role": "assistant", "timestamp": "2026-08-20T01:00:30Z", "requestId": "req1", "status": "done", "content": "private answer"})]
        conversation = Document("c1", {"updatedAt": "2026-08-20T01:00:30Z"}, {"messages": messages})
        self.root = Document("root-r1", {"identityVerified": True, "userEmail": "r1@example.com", "subject": "subject-r1"}, {"conversations": [conversation]})

    def collection(self, name):
        self.requested.append(name)
        assert name == "chat_users"
        return Collection([self.root])


def test_dormant_target_compiles_without_recent_activity_or_global_scan():
    client = Firestore()
    rows = recovery.compile_selected(settings(), client, [user()], START, END)
    assert client.requested == ["chat_users"]
    assert [row["event_id"] for row in rows.questions] == ["question:req1"]
    assert len(rows.answers) == len(rows.conversations) == 1
    assert rows.answers[0]["measurement_available"] is False
    assert "content" not in rows.questions[0] and "content" not in rows.answers[0]
    assert rows.conversations[0]["user_message_count"] == 1


def test_no_activity_produces_empty_history_without_inventing_usage():
    client = Firestore()
    client.root.children.clear()
    rows = recovery.compile_selected(settings(), client, [user()], START, END)
    assert all(not getattr(rows, family) for family in recovery.FAMILIES)


def question(event_id="question:req1", **changes):
    return {"event_id": event_id, "user_id": "subject-r1", "roster_id": "r1", "request_id": "req1",
            "conversation_id": "c1", "message_id": "m1", "question_date": date(2026, 8, 20), **changes}


def test_existing_canonical_measurement_is_never_overwritten():
    source = question(record_origin="firestore_history", classification_status="not_measured")
    current = question(record_origin="telemetry", classification_status="measured")
    assert recovery.missing_rows("questions", [source], [current]) == []
    assert current["classification_status"] == "measured"


def test_existing_conversation_on_another_date_is_covered():
    source = {"event_id": "conv:r1:c1", "user_id": "subject-r1", "conversation_id": "c1", "updated_date": date(2026, 8, 20),
              "last_active_at": END, "user_message_count": 3, "assistant_message_count": 3}
    current = {**source, "updated_date": date(2026, 8, 21)}
    assert recovery.missing_rows("conversations", [source], [current]) == []


@pytest.mark.parametrize("changes", [{"user_message_count": 1}, {"assistant_message_count": 1}, {"last_active_at": START}])
def test_existing_stale_conversation_requires_repair_instead_of_false_coverage(changes):
    source = {"event_id": "conv:r1:c1", "user_id": "subject-r1", "conversation_id": "c1", "updated_date": date(2026, 8, 20),
              "last_active_at": END, "user_message_count": 3, "assistant_message_count": 3}
    with pytest.raises(ValueError, match="stale"):
        recovery.missing_rows("conversations", [source], [{**source, **changes}])


@pytest.mark.parametrize("existing", [
    [question(), question()],
    [question("another-event")],
    [question(user_id="someone-else")],
    [question(roster_id="stale-roster")],
    [question(message_id="another-message")],
])
def test_duplicate_or_conflicting_current_facts_are_blocked(existing):
    with pytest.raises(ValueError):
        recovery.missing_rows("questions", [question()], existing)


def test_duplicate_source_identity_is_blocked():
    with pytest.raises(ValueError, match="compiled history"):
        recovery.missing_rows("questions", [question(), question()], [])


def test_insert_only_adapter_preserves_current_schema_without_updates():
    sql = recovery.insert_only_sql()
    assert sql.count("WHEN NOT MATCHED THEN INSERT") == 4
    assert "UPDATE SET" not in sql and "DELETE FROM" not in sql and "WHEN MATCHED" not in sql
    assert "pipeline_state" not in sql and "history_rebuild" not in sql


def job():
    instance = recovery.RosterHistoryBackfill.__new__(recovery.RosterHistoryBackfill)
    instance.settings, instance.dataset = settings(), "project.dataset"
    instance.directory, instance.firestore = Directory([user()]), Firestore()
    return instance


def test_plan_digest_is_stable_and_sensitive_to_identity_publication_and_facts():
    instance = job()
    instance.existing = lambda *_args: []
    publication = {"data_through": END, "published_run_id": "run1", "status": "succeeded"}
    instance.publication = lambda: dict(publication)
    first = instance.plan(["r1"], START, END)
    assert instance.plan(["r1"], START, END)["planDigest"] == first["planDigest"]
    assert instance.plan(["r1"], START, END)["sourceDigest"] == first["sourceDigest"]
    publication["published_run_id"] = "run2"
    assert instance.plan(["r1"], START, END)["planDigest"] != first["planDigest"]
    publication["published_run_id"] = "run1"
    instance.directory.users[0]["updated_at"] = "2026-08-20T00:00:00Z"
    assert instance.plan(["r1"], START, END)["planDigest"] != first["planDigest"]
    assert instance.plan(["r1"], START, END)["sourceDigest"] != first["sourceDigest"]
    assert "email" not in recovery._json(first["summary"])


def test_source_digest_changes_when_message_source_disappears():
    instance = job()
    instance.existing = lambda *_args: []
    instance.publication = lambda: {"data_through": END, "published_run_id": "run1", "status": "succeeded"}
    before = instance.plan(["r1"], START, END)
    instance.firestore.root.children.clear()
    after = instance.plan(["r1"], START, END)
    assert after["sourceDigest"] != before["sourceDigest"]


@pytest.mark.parametrize("changes", [{"user_id": "another-user"}, {"roster_id": "another-roster"}])
def test_answer_cannot_link_to_another_identity_question_outside_window(changes):
    instance = job()
    instance.publication = lambda: {"data_through": END, "published_run_id": "run1", "status": "succeeded"}
    instance.existing = lambda family, *_args: [question(**changes)] if family == "questions" else []
    after_question = datetime(2026, 8, 20, 1, 0, 15, tzinfo=timezone.utc)
    with pytest.raises(ValueError, match="answer without a question"):
        instance.plan(["r1"], after_question, END)


def test_existing_coverage_is_bounded_and_checks_global_request_collisions():
    instance = job()
    calls = []
    instance.query = lambda sql, parameters: calls.append((sql, parameters)) or []
    instance.existing("questions", [user()], [question()], START.date(), END.date())
    sql, parameters = calls[0]
    assert "question_date BETWEEN @coverage_start AND @coverage_end" in sql
    assert "OR request_id IN UNNEST(@requests)" in sql
    assert "OR trace_id IN UNNEST(@traces)" in sql
    assert {parameter.name for parameter in parameters} == {"coverage_start", "coverage_end", "users", "events", "requests", "traces"}


def test_all_partitions_apply_in_one_atomic_insert_only_transaction_with_publication_cas():
    instance = job()
    calls = []
    instance.query = lambda sql, parameters: calls.append((sql, parameters)) or []
    plan = {"publication": {"data_through": END, "published_run_id": "run1"},
            "missing": {"questions": [question(), question("question:req2", request_id="req2", message_id="m3", question_date=date(2026, 8, 19))],
                        "answers": [], "conversations": [], "citations": []}}
    instance.apply_sql(plan, "lease1")
    assert len(calls) == 1
    sql, parameters = calls[0]
    assert sql.count("BEGIN TRANSACTION") == sql.count("COMMIT TRANSACTION") == 1
    assert sql.count("WHEN NOT MATCHED THEN INSERT") == 8
    assert "lease_expires_at > CURRENT_TIMESTAMP()" in sql
    assert "data_through = @watermark" in sql and "published_run_id = @published_run_id" in sql
    assert "UPDATE SET" not in sql and "DELETE FROM" not in sql
    assert "history_rebuild" not in sql
    assert len({parameter.name for parameter in parameters}) == len(parameters)


def test_timezone_required_and_future_or_unbounded_history_rejected():
    with pytest.raises(ValueError, match="timezone"):
        recovery.timestamp("2026-08-20T00:00:00")
    instance = job()
    with pytest.raises(ValueError, match="analytics start"):
        instance.plan(["r1"], START - timedelta(days=1), END)
    with pytest.raises(ValueError, match="analytics start"):
        instance.plan(["r1"], START, datetime.now(timezone.utc) + timedelta(days=1))


def cli(monkeypatch, tmp_path, *, apply=False, stale=False, write_error=False, changed_source=False):
    events = []
    selected = recovery.select_users(Directory([user()]), ["r1"])
    initial = {"planDigest": "approved", "sourceDigest": "source", "publication": {"data_through": END, "published_run_id": "run1"},
               "users": selected, "missing": {name: [] for name in recovery.FAMILIES},
               "summary": {"selectedUsers": 1}}
    class FakeJob:
        bigquery = object()
        directory = Directory([user()])
        reads = 0
        def __init__(self, *_args, **_kwargs):
            pass
        def plan(self, *_args):
            self.reads += 1
            result = dict(initial)
            if stale:
                result["planDigest"] = "changed"
            if changed_source and self.reads > 1:
                result["sourceDigest"] = "changed-source"
            events.append("plan")
            return result
        def assert_idle_writers(self):
            events.append("idle")
        def apply_sql(self, *_args):
            events.append("write")
            if write_error:
                raise TimeoutError("unknown commit outcome")
    class Lease:
        def __init__(self, *_args, **_kwargs):
            pass
        def _lease_id(self):
            return "lease1"
        def _acquire_lease(self, *_args):
            events.append("acquire")
        def _renew_lease(self, *_args):
            events.append("renew")
        def _release_lease(self, *_args):
            events.append("release")
    monkeypatch.setattr(recovery, "RosterHistoryBackfill", FakeJob)
    monkeypatch.setattr(recovery, "AnalyticsRefreshJob", Lease)
    monkeypatch.setattr(recovery, "get_settings", settings)
    monkeypatch.setattr(recovery, "approved_credential_path", lambda _path: tmp_path / "credential.json")
    monkeypatch.setattr(recovery.service_account.Credentials, "from_service_account_file", lambda *_args, **_kwargs: object())
    monkeypatch.setattr(recovery.bigquery, "Client", lambda **_kwargs: object())
    monkeypatch.setattr(recovery.firestore, "Client", lambda **_kwargs: object())
    monkeypatch.setattr(recovery, "verify_freeze", lambda *_args: events.append("freeze"))
    output = tmp_path / "receipt.json"
    arguments = ["backfill", "--roster-id", "r1", "--start", START.isoformat(), "--end", END.isoformat(), "--credential-file", "credential.json"]
    if apply:
        arguments.extend(["--apply", "--expected-plan-digest", "approved", "--freeze-snapshot", "freeze.json", "--receipt-output", str(output)])
    monkeypatch.setattr(sys, "argv", arguments)
    return events, output


def test_cli_default_plan_has_no_lease_or_cloud_writes(monkeypatch, tmp_path):
    events, output = cli(monkeypatch, tmp_path)
    assert recovery.main() == 0
    assert events == ["plan"] and not output.exists()


def test_cli_stale_plan_releases_lease_before_any_fact_write(monkeypatch, tmp_path):
    events, output = cli(monkeypatch, tmp_path, apply=True, stale=True)
    with pytest.raises(ValueError, match="generate a new plan"):
        recovery.main()
    assert "write" not in events and events[-1] == "release" and not output.exists()


def test_cli_unknown_write_outcome_retains_lease_and_no_success_receipt(monkeypatch, tmp_path):
    events, output = cli(monkeypatch, tmp_path, apply=True, write_error=True)
    with pytest.raises(TimeoutError):
        recovery.main()
    assert "write" in events and "release" not in events and not output.exists()


def test_cli_changed_source_cannot_be_certified_after_write(monkeypatch, tmp_path):
    events, output = cli(monkeypatch, tmp_path, apply=True, changed_source=True)
    with pytest.raises(ValueError, match="verification failed"):
        recovery.main()
    assert "write" in events and events[-1] == "release" and not output.exists()


def test_cli_success_verifies_coverage_before_local_receipt(monkeypatch, tmp_path):
    events, output = cli(monkeypatch, tmp_path, apply=True)
    assert recovery.main() == 0
    receipt = json.loads(output.read_text())
    assert receipt["coverageVerified"] is True and receipt["requiresNormalRefresh"] is True
    assert events.index("write") < len(events) - 1 and events[-1] == "release"
    assert receipt["publication"]["published_run_id"] == "run1"


def freeze_receipt(tmp_path):
    configured = settings()
    configured.monitor_source_service = "lcs-rag-app"
    receipt = {"project": "project", "region": "us-central1", "dataset": "dataset", "location": "US", "source_service": "lcs-rag-app",
               "old_scheduler": recovery.REFRESH_POLICY.legacy_scheduler_name, "new_scheduler": recovery.REFRESH_POLICY.scheduler_name,
               "freeze_started_at": "2026-08-20T00:00:00Z", "freeze_verified_at": "2026-08-20T01:00:00Z", "active_bigquery_writers_at_freeze": [],
               "expected_old_scheduler_service_account": "old@example.com", "expected_new_scheduler_service_account": "new@example.com"}
    path = tmp_path / "freeze.json"
    path.write_text(json.dumps(receipt))
    return configured, path


@pytest.mark.parametrize("hazard", ["scheduler", "execution", "retrying_execution"])
def test_live_freeze_checks_reject_active_or_retrying_writers(monkeypatch, tmp_path, hazard):
    configured, path = freeze_receipt(tmp_path)
    def run(arguments, **kwargs):
        assert kwargs["env"]["CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE"] == "pinned.json"
        if "scheduler" in arguments:
            name = arguments[5]
            old = name == recovery.REFRESH_POLICY.legacy_scheduler_name
            payload = {"state": "ENABLED" if hazard == "scheduler" else "PAUSED",
                       "httpTarget": {"uri": "https://us-central1-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/project/jobs/oura-navi-monitor-refresh:run",
                                      "oauthToken": {"serviceAccountEmail": "old@example.com" if old else "new@example.com"}}}
        else:
            payload = [{"status": {"failedCount": 1}}] if hazard == "retrying_execution" else [{"status": {}}]
        return SimpleNamespace(stdout=json.dumps(payload))
    monkeypatch.setattr(recovery.subprocess, "run", run)
    with pytest.raises(ValueError, match="scheduler|execution"):
        recovery.verify_freeze(path, configured, "us-central1", "pinned.json")


def test_active_bigquery_writer_guard_is_read_only_and_fails_closed():
    instance = job()
    calls = []
    instance.query = lambda sql, params: calls.append(sql) or [{"job_id": "active"}]
    with pytest.raises(ValueError, match="writers are still"):
        instance.assert_idle_writers()
    assert "JOBS_BY_PROJECT" in calls[0] and "PENDING" in calls[0] and "SCRIPT" in calls[0]
    assert not calls[0].lstrip().startswith(("UPDATE", "INSERT", "DELETE", "MERGE"))


def test_freeze_requires_recorded_empty_writer_inventory(monkeypatch, tmp_path):
    configured, path = freeze_receipt(tmp_path)
    receipt = json.loads(path.read_text())
    del receipt["active_bigquery_writers_at_freeze"]
    path.write_text(json.dumps(receipt))
    monkeypatch.setattr(recovery.subprocess, "run", lambda *_args, **_kwargs: pytest.fail("invalid receipt must fail before live reads"))
    with pytest.raises(ValueError, match="freeze receipt"):
        recovery.verify_freeze(path, configured, "us-central1", "pinned.json")


def selection(**overrides):
    return {family: overrides.get(family, []) for family in recovery.FAMILIES}


@pytest.mark.parametrize('manifest', [
    {}, selection(), selection(questions=['']), selection(questions=['question:req1', 'question:req1']),
    selection(questions='question:req1'), {**selection(questions=['question:req1']), 'unknown': []},
])
def test_exact_event_manifest_rejects_invalid_or_empty_scope(manifest):
    with pytest.raises(ValueError, match='event manifest'):
        recovery.event_selection(manifest)


def test_manifest_filters_families_and_requires_every_source_identity():
    manifest = selection(questions=['question:req1'])
    rows = recovery.compile_selected(settings(), Firestore(), [user()], START, END, manifest)
    assert len(rows.questions) == 1
    assert not rows.answers and not rows.conversations and not rows.citations
    with pytest.raises(ValueError, match='unique source facts'):
        recovery.compile_selected(settings(), Firestore(), [user()], START, END,
                                  selection(questions=['question:absent']))


def test_manifest_preserves_unrelated_legacy_duplicates_but_default_stays_strict():
    instance = job()
    unrelated = question('question:old', request_id='old', message_id='old-message')
    instance.existing = lambda family, *_args: [unrelated, dict(unrelated)] if family == 'questions' else []
    instance.publication = lambda: {'data_through': END, 'published_run_id': 'run1', 'status': 'succeeded'}
    with pytest.raises(ValueError, match='duplicate logical'):
        instance.plan(['r1'], START, END)
    plan = instance.plan(['r1'], START, END, selection(questions=['question:req1']))
    assert len(plan['missing']['questions']) == 1 and not plan['missing']['answers']
    # A concurrent change even to preserved facts still invalidates approval.
    instance.existing = lambda family, *_args: [unrelated] if family == 'questions' else []
    assert instance.plan(['r1'], START, END, selection(questions=['question:req1']))['planDigest'] != plan['planDigest']


@pytest.mark.parametrize('current', [
    question(user_id='other-owner'), question(roster_id='other-roster'),
    question('other-event'), question(message_id='conflicting-message'),
])
def test_manifest_never_bypasses_target_collision_or_ownership_guards(current):
    instance = job()
    instance.existing = lambda family, *_args: [current] if family == 'questions' else []
    instance.publication = lambda: {'data_through': END, 'published_run_id': 'run1', 'status': 'succeeded'}
    with pytest.raises(ValueError):
        instance.plan(['r1'], START, END, selection(questions=['question:req1']))


def test_answer_only_manifest_requires_same_owner_question_and_binds_selection_digest():
    instance = job()
    instance.existing = lambda *_args: []
    instance.publication = lambda: {'data_through': END, 'published_run_id': 'run1', 'status': 'succeeded'}
    with pytest.raises(ValueError, match='answer without a question'):
        instance.plan(['r1'], START, END, selection(answers=['answer:req1']))
    instance.existing = lambda family, *_args: [question()] if family == 'questions' else []
    first = instance.plan(['r1'], START, END, selection(answers=['answer:req1']))
    assert len(first['missing']['answers']) == 1 and not first['missing']['questions']
    second = instance.plan(['r1'], START, END, selection(questions=['question:req1'], answers=['answer:req1']))
    assert first['sourceDigest'] != second['sourceDigest']


def test_conversation_stream_is_exhausted_before_child_message_reads():
    client = Firestore()
    original = client.root.collection
    state = {'active': False}
    class StreamCollection(Collection):
        def stream(self, **_kwargs):
            state['active'] = True
            yield from self.documents
            state['active'] = False
    conversation = client.root.children['conversations'][0]
    child_collection = conversation.collection
    def guarded(name):
        assert not state['active'], 'outer stream held open during child RPC'
        return child_collection(name)
    conversation.collection = guarded
    client.root.collection = lambda name: StreamCollection(original(name).documents)
    assert len(recovery.compile_selected(settings(), client, [user()], START, END).questions) == 1


def fallback_answer(**changes):
    return {"event_id": "answer:history:source", "request_id": "history:source", "trace_id": "trace1",
            "user_id": "subject-r1", "roster_id": "r1", "conversation_id": "c1", "message_id": "m2",
            "measurement_available": False, "measurement_profile": "firestore_usage_only", **changes}


def trace_question(**changes):
    return question("question:trace1", request_id="trace1", trace_id="trace1", message_id="m2",
                    question_ts=datetime(2026, 8, 20, 1, tzinfo=timezone.utc), **changes)


def test_trace_answer_binding_changes_only_request_and_event_and_preserves_source():
    source = fallback_answer()
    current = trace_question()
    resolved, proofs = recovery.resolve_trace_linked_answers([source], [current])
    assert resolved == [{**source, "event_id": "answer:trace1", "request_id": "trace1"}]
    assert source["event_id"] == "answer:history:source"
    assert resolved[0]["measurement_available"] is False
    assert proofs[0]["sourceAnswerEventId"] == source["event_id"]
    assert proofs[0]["question"]["event_id"] == current["event_id"]


@pytest.mark.parametrize("changes", [{"request_id": "producer-request"}, {"trace_id": ""}])
def test_trace_binding_never_changes_explicit_request_or_trace_free_answer(changes):
    source = fallback_answer(**changes)
    assert recovery.resolve_trace_linked_answers([source], [trace_question()]) == ([source], [])


def test_trace_answer_without_canonical_reference_preserves_fallback():
    source = fallback_answer()
    assert recovery.resolve_trace_linked_answers([source], []) == ([source], [])


def test_trace_answer_ambiguous_global_question_reference_fails_closed():
    current = trace_question()
    with pytest.raises(ValueError, match="ambiguous canonical question"):
        recovery.resolve_trace_linked_answers([fallback_answer()], [current, dict(current)])


@pytest.mark.parametrize("changes", [
    {"user_id": "other-user"}, {"roster_id": "other-roster"}, {"conversation_id": "other-conversation"},
    {"message_id": "m1"}, {"trace_id": "other-trace"}, {"request_id": "other-request"}, {"event_id": ""},
])
def test_trace_answer_wrong_owner_or_incompatible_question_identity_is_rejected(changes):
    with pytest.raises(ValueError, match="conflicts with its canonical question identity"):
        recovery.resolve_trace_linked_answers([fallback_answer()], [{**trace_question(), **changes}])


def test_trace_question_reference_query_is_global_and_partition_bounded():
    instance = job()
    calls = []
    instance.query = lambda sql, parameters: calls.append((sql, parameters)) or []
    instance.answer_reference_questions([fallback_answer()], START.date(), END.date())
    sql, parameters = calls[0]
    assert "question_date BETWEEN @coverage_start AND @coverage_end" in sql
    assert "trace_id IN UNNEST(@answer_traces) OR request_id IN UNNEST(@answer_traces)" in sql
    assert "user_id IN" not in sql
    assert {parameter.name for parameter in parameters} == {"coverage_start", "coverage_end", "answer_traces"}
    assert next(parameter for parameter in parameters if parameter.name == "answer_traces").values == ["trace1"]


def trace_answer_job():
    instance = job()
    messages = instance.firestore.root.children["conversations"][0].children["messages"]
    messages[1].payload.pop("requestId")
    messages[1].payload["traceId"] = "trace1"
    source = recovery.compile_selected(settings(), instance.firestore, [user()], START, END)
    manifest = selection(answers=[source.answers[0]["event_id"]])
    reference = trace_question()
    instance.publication = lambda: {"data_through": END, "published_run_id": "run1", "status": "succeeded"}
    instance.query = lambda *_args: [dict(reference)]
    instance.existing = lambda family, *_args: [dict(reference)] if family == "questions" else []
    return instance, manifest, reference


def test_manifest_trace_answer_recovery_uses_validated_source_id_and_is_idempotent():
    instance, manifest, reference = trace_answer_job()
    first = instance.plan(["r1"], START, END, manifest)
    assert not first["missing"]["questions"]
    assert first["summary"]["traceLinkedAnswers"] == 1
    assert first["missing"]["answers"][0]["event_id"] == "answer:trace1"
    assert first["answerTraceLinks"][0]["sourceAnswerEventId"] == manifest["answers"][0]
    saved_answer = dict(first["missing"]["answers"][0])
    instance.existing = lambda family, *_args: ([dict(reference)] if family == "questions" else
                                              [saved_answer] if family == "answers" else [])
    after = instance.plan(["r1"], START, END, manifest)
    assert all(not values for values in after["missing"].values())
    assert after["sourceDigest"] == first["sourceDigest"]
    assert after["planDigest"] != first["planDigest"]
    assert after["answerTraceLinks"] == first["answerTraceLinks"]
    with pytest.raises(ValueError, match="unique source facts"):
        instance.plan(["r1"], START, END, selection(answers=["answer:trace1"]))


def test_trace_reference_proof_changes_invalidate_source_digest_even_with_same_answer():
    instance, manifest, reference = trace_answer_job()
    first = instance.plan(["r1"], START, END, manifest)
    reference["question_ts"] += timedelta(seconds=1)
    after = instance.plan(["r1"], START, END, manifest)
    assert after["rows"].answers == first["rows"].answers
    assert after["sourceDigest"] != first["sourceDigest"]
    assert after["planDigest"] != first["planDigest"]


def test_manifest_trace_answer_no_reference_still_obeys_orphan_guard():
    instance, manifest, _reference = trace_answer_job()
    instance.query = lambda *_args: []
    instance.existing = lambda *_args: []
    with pytest.raises(ValueError, match="answer without a question"):
        instance.plan(["r1"], START, END, manifest)


def test_default_recovery_does_not_resolve_trace_linked_answers():
    instance, _manifest, _reference = trace_answer_job()
    instance.existing = lambda *_args: []
    instance.answer_reference_questions = lambda *_args: pytest.fail("default mode must not resolve trace aliases")
    plan = instance.plan(["r1"], START, END)
    assert plan["rows"].answers[0]["request_id"].startswith("history:")
    assert plan["answerTraceLinks"] == []


def test_normalized_trace_answer_still_rejects_existing_answer_with_wrong_owner():
    instance, manifest, reference = trace_answer_job()
    conflicting = {**fallback_answer(), "event_id": "answer:trace1", "request_id": "trace1", "user_id": "other-user"}
    instance.existing = lambda family, *_args: ([reference] if family == "questions" else
                                              [conflicting] if family == "answers" else [])
    with pytest.raises(ValueError, match="canonical identity conflicts"):
        instance.plan(["r1"], START, END, manifest)


def test_fallback_question_answer_pair_remains_idempotent_after_its_question_is_inserted():
    instance, manifest, _reference = trace_answer_job()
    source = recovery.compile_selected(settings(), instance.firestore, [user()], START, END)
    manifest["questions"] = [source.questions[0]["event_id"]]
    saved = {family: [] for family in recovery.FAMILIES}
    instance.query = lambda *_args: list(saved["questions"])
    instance.existing = lambda family, *_args: list(saved[family])
    before = instance.plan(["r1"], START, END, manifest)
    assert len(before["missing"]["questions"]) == len(before["missing"]["answers"]) == 1
    assert before["answerTraceLinks"] == []
    for family in recovery.FAMILIES:
        saved[family] = list(before["missing"][family])
    after = instance.plan(["r1"], START, END, manifest)
    assert all(not values for values in after["missing"].values())
    assert after["sourceDigest"] == before["sourceDigest"]
    assert after["answerTraceLinks"] == []
    assert after["rows"].answers[0]["request_id"].startswith("history:")


@pytest.mark.parametrize("changes", [{"user_id": "other"}, {"roster_id": "other"}, {"conversation_id": "other"}])
def test_existing_fallback_question_does_not_relax_ownership_or_conversation(changes):
    source = fallback_answer()
    current = {**trace_question(), "request_id": source["request_id"], **changes}
    with pytest.raises(ValueError, match="conflicts with its canonical question identity"):
        recovery.resolve_trace_linked_answers([source], [current])


@pytest.mark.parametrize('alias', ['paired_message', 'trace'])
def test_manifest_blocks_known_legacy_representations_without_counting_them_again(alias):
    source = question(message_id='1787000000000-user', trace_id='source-trace')
    current = question('old-event', request_id='old-request', message_id='1787000000000-assistant', trace_id='other-trace')
    if alias == 'trace':
        current.update(message_id='different-message', trace_id='source-trace')
    with pytest.raises(ValueError, match='legacy identity alias'):
        recovery.reject_legacy_aliases('questions', [source], [current])


def test_legacy_alias_guard_leaves_exact_identity_to_normal_ownership_checks():
    source = question(trace_id='trace')
    current = question(user_id='wrong-owner', trace_id='trace')
    recovery.reject_legacy_aliases('questions', [source], [current])
    with pytest.raises(ValueError, match='identity conflicts'):
        recovery.missing_rows('questions', [source], [current])


def test_manifest_blocks_cross_owner_trace_alias_and_answer_paired_id_alias():
    source = question(trace_id='source-trace')
    other = question('other-event', request_id='other-request', message_id='other-message',
                     user_id='other-owner', trace_id='source-trace')
    with pytest.raises(ValueError, match='legacy identity alias'):
        recovery.reject_legacy_aliases('questions', [source], [other])
    source = question('answer:new', message_id='1787000000000-assistant')
    old = question('answer:old', request_id='old', message_id='1787000000000-user')
    with pytest.raises(ValueError, match='legacy identity alias'):
        recovery.reject_legacy_aliases('answers', [source], [old])
