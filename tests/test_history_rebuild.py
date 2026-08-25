from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from app.jobs.rebuild_history import (
    HistoryRebuildJob,
    HistoryRows,
    TelemetryIndex,
    compile_conversation_history,
    parse_telemetry_line,
)
from app.jobs.project_firestore import (
    ChatConversationRecord,
    ChatRootRecord,
    FullChatSnapshot,
)


def _messages(*, error: bool = False) -> list[dict]:
    return [
        {
            "id": "user-message",
            "role": "user",
            "timestamp": "2026-08-20T01:00:00Z",
            "turnId": "turn-1",
            "modeAtSend": "internal",
            "attachmentFileIds": ["attachment-1"],
            "content": "this content must never be projected",
        },
        {
            "id": "assistant-message",
            "role": "assistant",
            "timestamp": "2026-08-20T01:00:30Z",
            "turnId": "turn-1",
            "traceId": "trace-1",
            "requestId": "request-1",
            "status": "failed" if error else "done",
            "errorMessage": "generation failed" if error else "",
            "content": "this answer must never be projected",
        },
    ]


def test_telemetry_line_parser_rejects_malformed_and_indexes_join_keys() -> None:
    assert parse_telemetry_line("INFO no event") is None
    assert parse_telemetry_line("stream_terminal_json {bad") is None
    records = [
        parse_telemetry_line(
            'request_user_metric_json {"trace_id":"trace-1","device_class":"mobile"}',
            source_ts=datetime(2026, 8, 20, tzinfo=timezone.utc),
        ),
        parse_telemetry_line(
        'stream_terminal_json={"trace_id":"trace-1","request_id":"request-1","terminal_event":"final","latency_ms":30000}',
            source_ts=datetime(2026, 8, 20, 0, 1, tzinfo=timezone.utc),
        ),
    ]
    index = TelemetryIndex.from_records(item for item in records if item is not None)
    assert index.requests_by_trace["trace-1"]["device_class"] == "mobile"
    assert index.terminal("request-1", "trace-1")["terminal_event"] == "final"


def test_history_compiler_uses_runtime_truth_for_comparable_complete_delivery() -> None:
    telemetry = TelemetryIndex.from_records(
        [
            {
                "family": "request_user_metric_json",
                "payload": {"trace_id": "trace-1", "device_class": "desktop"},
                "source_ts": datetime(2026, 8, 20, tzinfo=timezone.utc),
            },
            {
                "family": "stream_terminal_json",
                "payload": {
                    "trace_id": "trace-1",
                    "request_id": "request-1",
                    "terminal_event": "final",
                    "latency_ms": 30000,
                    "revision_name": "lcs-rag-app-00241-hoh",
                    "git_sha": "ffca2d3",
                },
                "source_ts": datetime(2026, 8, 20, 0, 1, tzinfo=timezone.utc),
            },
            {
                "family": "tmcs_stage_latency_json",
                "payload": {
                    "trace_id": "trace-1",
                    "request_id": "request-1",
                    "status": "completed",
                    "demand_delivery_state_counts": {"delivered": 2, "partial": 0, "omitted": 0},
                    "demand_system_fault_counts": {},
                    "writer_error_code": "",
                    "stage_latency_ms": {"request_spec": 4000},
                },
                "source_ts": datetime(2026, 8, 20, 0, 1, 1, tzinfo=timezone.utc),
            },
        ]
    )

    questions, answers = compile_conversation_history(
        roster_id="roster-1",
        user_id="subject-1",
        conversation_id="conversation-1",
        conversation={},
        messages=_messages(),
        telemetry=telemetry,
        timezone_name="Asia/Tokyo",
    )

    assert len(questions) == 1
    assert questions[0]["event_id"] == "question:request-1"
    assert questions[0]["attachment_count"] == 1
    assert "content" not in questions[0]
    assert answers[0]["measurement_available"] is True
    assert answers[0]["complete_delivery"] is True
    assert answers[0]["measurement_profile"] == "runtime_truth_full"
    assert answers[0]["total_latency_ms"] == 30000
    assert "content" not in answers[0]


def test_history_compiler_preserves_unknown_success_and_explicit_failure() -> None:
    questions, unknown_answers = compile_conversation_history(
        roster_id="roster-1",
        user_id="subject-1",
        conversation_id="conversation-1",
        conversation={},
        messages=_messages(),
        telemetry=TelemetryIndex(),
        timezone_name="Asia/Tokyo",
    )
    assert questions[0]["measurement_profile"] == "firestore_usage_only"
    assert unknown_answers[0]["measurement_available"] is False
    assert unknown_answers[0]["complete_delivery"] is None

    _questions, failed_answers = compile_conversation_history(
        roster_id="roster-1",
        user_id="subject-1",
        conversation_id="conversation-1",
        conversation={},
        messages=_messages(error=True),
        telemetry=TelemetryIndex(),
        timezone_name="Asia/Tokyo",
    )
    assert failed_answers[0]["measurement_available"] is True
    assert failed_answers[0]["complete_delivery"] is False
    assert failed_answers[0]["primary_failure_reason"] == "stream_failed"


def test_history_partitions_follow_monitor_timezone_not_utc_date() -> None:
    messages = _messages()
    messages[0]["timestamp"] = "2026-08-20T15:30:00Z"
    messages[1]["timestamp"] = "2026-08-20T15:31:00Z"

    questions, answers = compile_conversation_history(
        roster_id="roster-1",
        user_id="subject-1",
        conversation_id="conversation-1",
        conversation={},
        messages=messages,
        telemetry=TelemetryIndex(),
        timezone_name="Asia/Tokyo",
    )

    assert questions[0]["question_date"].isoformat() == "2026-08-21"
    assert answers[0]["answer_date"].isoformat() == "2026-08-21"


def test_history_merge_has_no_parallel_table_or_plaintext_content_contract() -> None:
    sql = (Path(__file__).resolve().parents[1] / "sql" / "merge_history.sql").read_text(encoding="utf-8").lower()
    assert "create table" not in sql
    assert "question_events" in sql
    assert "answer_events" in sql
    assert "'firestore_history', 'legacy_audit_history'" in sql
    for forbidden in ("user_email", "answer_text", "raw_query", " content "):
        assert forbidden not in sql


class _Directory:
    def __init__(self, users: list[dict]) -> None:
        self._users = users

    def list_users(self, *, include_inactive: bool = True) -> list[dict]:
        assert include_inactive is True
        return list(self._users)


class _ChatReader:
    def __init__(self, snapshot: FullChatSnapshot) -> None:
        self._snapshot = snapshot

    def full_snapshot(self, *, progress=None) -> FullChatSnapshot:
        if progress is not None:
            progress("firestore_messages_read", 0)
        return self._snapshot


def _history_job(*, users: list[dict], snapshot: FullChatSnapshot) -> HistoryRebuildJob:
    job = HistoryRebuildJob.__new__(HistoryRebuildJob)
    job.settings = type("Settings", (), {"monitor_timezone": "Asia/Tokyo"})()
    job.directory = _Directory(users)
    job.chat_reader = _ChatReader(snapshot)
    job.telemetry = lambda **_kwargs: TelemetryIndex()
    job.legacy_audits = lambda **_kwargs: []
    return job


def test_history_identity_does_not_arbitrarily_choose_duplicate_verified_email_roots() -> None:
    user = {
        "roster_id": "roster-1",
        "email": "member@example.com",
        "department": "DM専任",
        "chat_user_id": "",
        "user_id": "",
    }
    snapshot = FullChatSnapshot(
        roots=[
            ChatRootRecord(
                "root-1",
                {"identityVerified": True, "userEmail": "member@example.com", "subject": "s1"},
            ),
            ChatRootRecord(
                "root-2",
                {"identityVerified": True, "userEmail": "member@example.com", "subject": "s2"},
            ),
        ]
    )

    rows = _history_job(users=[user], snapshot=snapshot).compile(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert rows.questions == []
    assert rows.unmatched_users == ["roster-1"]
    assert rows.issues["ambiguous_verified_roots"] == 1


def test_history_identity_uses_existing_exact_binding_and_reports_no_ambiguity() -> None:
    user = {
        "roster_id": "roster-1",
        "email": "member@example.com",
        "department": "DM専任",
        "chat_user_id": "root-1",
        "user_id": "subject-1",
    }
    snapshot = FullChatSnapshot(
        roots=[
            ChatRootRecord(
                "root-1",
                {"identityVerified": True, "userEmail": "member@example.com", "subject": "subject-1"},
            ),
            ChatRootRecord(
                "root-2",
                {"identityVerified": True, "userEmail": "member@example.com", "subject": "subject-2"},
            ),
        ],
        conversations=[
            ChatConversationRecord(
                root_id="root-1",
                conversation_id="conversation-1",
                conversation={"updatedAt": "2026-08-20T01:01:00Z"},
                messages=_messages(),
            )
        ],
    )

    rows = _history_job(users=[user], snapshot=snapshot).compile(
        start=datetime(2026, 8, 1, tzinfo=timezone.utc),
        end=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )

    assert len(rows.questions) == 1
    assert rows.unmatched_users == []
    assert rows.issues["ambiguous_verified_roots"] == 0


def test_legacy_audit_migration_preserves_exact_category_but_not_old_success_claim() -> None:
    job = HistoryRebuildJob.__new__(HistoryRebuildJob)
    job.settings = type("Settings", (), {"monitor_timezone": "Asia/Tokyo"})()
    user = {
        "roster_id": "roster-1",
        "user_id": "subject-1",
        "email": "member@example.com",
        "department": "DM専任",
    }
    rows = HistoryRows()
    telemetry = TelemetryIndex.from_records(
        [
            {
                "family": "request_user_metric_json",
                "payload": {
                    "trace_id": "trace-1",
                    "device_class": "desktop",
                    "mode": "internal",
                },
                "source_ts": datetime(2026, 8, 20, 1, 0, tzinfo=timezone.utc),
            }
        ]
    )

    job._merge_legacy_audits(
        rows=rows,
        audits=[
            {
                "event_ts": datetime(2026, 8, 20, 1, 1, tzinfo=timezone.utc),
                "trace_id": "trace-1",
                "request_id": "request-1",
                "user_id": "subject-1",
                "question_category": "product_explanation",
                "has_error": False,
                # Deliberately no old answer_success_flag: it is not selected or trusted.
            }
        ],
        telemetry=telemetry,
        eligible_identity_candidates={"subject-1": [user]},
        all_identity_candidates={"subject-1": [user]},
        users_by_email={"member@example.com": [user]},
    )

    assert rows.questions[0]["question_category"] == "product_information"
    assert rows.questions[0]["question_ts"] == datetime(
        2026, 8, 20, 1, 0, tzinfo=timezone.utc
    )
    assert rows.questions[0]["record_origin"] == "legacy_audit_history"
    assert rows.answers[0]["measurement_available"] is False
    assert rows.answers[0]["complete_delivery"] is None
    assert rows.answers[0]["message_persisted"] is None


def test_history_verification_marker_is_owned_by_pipeline_state() -> None:
    source = Path(__file__).resolve().parents[1] / "app" / "jobs" / "rebuild_history.py"
    text = source.read_text(encoding="utf-8")
    assert "source = 'history_rebuild'" in text
    assert "published_run_id" in text
    assert text.index('verification["passed"]') < text.index("job.record_verified_rebuild")
