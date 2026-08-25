from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any, Iterable
from zoneinfo import ZoneInfo

from google.cloud import bigquery, firestore
from google.api_core.exceptions import NotFound

from app.contracts.admin import normalize_email
from app.domain.analysis_scopes import AnalysisScope, department_in_scope
from app.domain.question_categories import (
    QuestionCategory,
    migrate_legacy_question_category,
)
from app.jobs.project_firestore import (
    CITATION_SCHEMA,
    CONVERSATION_SCHEMA,
    FirestoreChatReader,
    ProgressCallback,
    ProjectionDataError,
    project_citations,
    project_conversation,
    struct_array_parameter,
)
from app.repositories.user_directory import UserDirectoryRepository
from app.settings import Settings, get_settings


SQL_PATH = Path(__file__).resolve().parents[2] / "sql" / "merge_history.sql"
_TELEMETRY_LINE = re.compile(
    r"(?:^|\s)(request_user_metric_json|stream_terminal_json|tmcs_stage_latency_json)(?:=|\s+)(\{.*\})\s*$"
)
_FINISHED_MESSAGE_STATUSES = frozenset({"done", "completed", "final"})
_FAILED_MESSAGE_STATUSES = frozenset({"error", "failed", "cancelled"})
_FAILED_TERMINALS = frozenset({"error", "cancelled"})
_FAILED_RUNTIME = frozenset({"failed", "cancelled", "error"})


QUESTION_SCHEMA = [
    ("event_id", "STRING"),
    ("question_ts", "TIMESTAMP"),
    ("question_date", "DATE"),
    ("user_id", "STRING"),
    ("roster_id", "STRING"),
    ("request_id", "STRING"),
    ("trace_id", "STRING"),
    ("conversation_id", "STRING"),
    ("turn_id", "STRING"),
    ("message_id", "STRING"),
    ("mode", "STRING"),
    ("device_class", "STRING"),
    ("attachment_count", "INT64"),
    ("producer_revision", "STRING"),
    ("producer_git_sha", "STRING"),
    ("question_category", "STRING"),
    ("classification_status", "STRING"),
    ("record_origin", "STRING"),
    ("measurement_profile", "STRING"),
]

ANSWER_SCHEMA = [
    ("event_id", "STRING"),
    ("answer_ts", "TIMESTAMP"),
    ("answer_date", "DATE"),
    ("user_id", "STRING"),
    ("roster_id", "STRING"),
    ("request_id", "STRING"),
    ("trace_id", "STRING"),
    ("conversation_id", "STRING"),
    ("turn_id", "STRING"),
    ("message_id", "STRING"),
    ("mode", "STRING"),
    ("device_class", "STRING"),
    ("terminal", "STRING"),
    ("runtime_status", "STRING"),
    ("failure_code", "STRING"),
    ("demand_total", "INT64"),
    ("delivered_demand_count", "INT64"),
    ("partial_demand_count", "INT64"),
    ("omitted_demand_count", "INT64"),
    ("system_fault_count", "INT64"),
    ("total_latency_ms", "INT64"),
    ("stage_latency_json", "STRING"),
    ("writer_error_code", "STRING"),
    ("message_persisted", "BOOL"),
    ("assistant_error_present", "BOOL"),
    ("measurement_available", "BOOL"),
    ("complete_delivery", "BOOL"),
    ("primary_failure_reason", "STRING"),
    ("revision_name", "STRING"),
    ("git_sha", "STRING"),
    ("record_origin", "STRING"),
    ("measurement_profile", "STRING"),
]


def _timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _stable_id(*parts: str) -> str:
    import hashlib

    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:32]


def _nested_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def parse_telemetry_line(text: str, *, source_ts: datetime | None = None) -> dict[str, Any] | None:
    match = _TELEMETRY_LINE.search(str(text or "").strip())
    if match is None:
        return None
    try:
        payload = json.loads(match.group(2))
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    return {"family": match.group(1), "payload": payload, "source_ts": source_ts}


@dataclass
class TelemetryIndex:
    requests_by_trace: dict[str, dict[str, Any]] = field(default_factory=dict)
    request_source_ts_by_trace: dict[str, datetime] = field(default_factory=dict)
    terminals_by_request: dict[str, dict[str, Any]] = field(default_factory=dict)
    terminals_by_trace: dict[str, dict[str, Any]] = field(default_factory=dict)
    truths_by_request: dict[str, dict[str, Any]] = field(default_factory=dict)
    truths_by_trace: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_records(cls, records: Iterable[dict[str, Any]]) -> "TelemetryIndex":
        index = cls()
        ordered = sorted(records, key=lambda item: _timestamp(item.get("source_ts")) or datetime.min.replace(tzinfo=timezone.utc))
        for record in ordered:
            family = str(record.get("family") or "")
            payload = _nested_dict(record.get("payload"))
            trace_id = str(payload.get("trace_id") or "").strip()
            request_id = str(payload.get("request_id") or "").strip()
            if family == "request_user_metric_json" and trace_id:
                index.requests_by_trace[trace_id] = payload
                source_ts = _timestamp(record.get("source_ts"))
                if source_ts is not None:
                    index.request_source_ts_by_trace[trace_id] = source_ts
            elif family == "stream_terminal_json":
                if request_id:
                    index.terminals_by_request[request_id] = payload
                if trace_id:
                    index.terminals_by_trace[trace_id] = payload
            elif family == "tmcs_stage_latency_json":
                if request_id:
                    index.truths_by_request[request_id] = payload
                if trace_id:
                    index.truths_by_trace[trace_id] = payload
        return index

    def terminal(self, request_id: str, trace_id: str) -> dict[str, Any]:
        return self.terminals_by_request.get(request_id) or self.terminals_by_trace.get(trace_id) or {}

    def truth(self, request_id: str, trace_id: str) -> dict[str, Any]:
        return self.truths_by_request.get(request_id) or self.truths_by_trace.get(trace_id) or {}


def _count_map(value: Any) -> tuple[int, bool]:
    if not isinstance(value, dict):
        return 0, False
    total = 0
    for item in value.values():
        try:
            total += max(0, int(item or 0))
        except (TypeError, ValueError):
            return 0, False
    return total, True


def _nonnegative_int(value: Any) -> int | None:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _runtime_measurement(
    *,
    terminal: str,
    runtime_status: str,
    assistant_error: bool,
    message_persisted: bool | None,
    truth: dict[str, Any],
) -> dict[str, Any]:
    delivery = _nested_dict(truth.get("demand_delivery_state_counts"))
    delivered = _nonnegative_int(delivery.get("delivered")) if delivery else None
    partial = _nonnegative_int(delivery.get("partial")) if delivery else None
    omitted = _nonnegative_int(delivery.get("omitted")) if delivery else None
    delivery_available = all(value is not None for value in (delivered, partial, omitted))
    demand_total = sum((delivered, partial, omitted)) if delivery_available else None
    system_faults, faults_available = _count_map(truth.get("demand_system_fault_counts"))
    writer_error = str(truth.get("writer_error_code") or "").strip()
    failed_stream = terminal in _FAILED_TERMINALS or runtime_status in _FAILED_RUNTIME
    full = (
        bool(truth)
        and demand_total is not None
        and demand_total > 0
        and faults_available
        and message_persisted is True
    )
    if failed_stream:
        measured, complete, reason, profile = True, False, "stream_failed", "terminal_outcome"
    elif assistant_error:
        measured, complete, reason, profile = True, False, "assistant_error", "message_outcome"
    elif message_persisted is False:
        measured, complete, reason, profile = True, False, "not_persisted", "message_outcome"
    elif not full:
        measured, complete, reason, profile = False, None, "measurement_missing", "firestore_usage_only"
    else:
        complete = (
            terminal == "final"
            and runtime_status == "completed"
            and partial == 0
            and omitted == 0
            and system_faults == 0
            and not writer_error
        )
        measured = True
        if terminal != "final":
            reason = "not_final"
        elif writer_error:
            reason = "writer_error"
        elif system_faults > 0:
            reason = "system_fault"
        elif omitted > 0:
            reason = "demand_omitted"
        elif partial > 0:
            reason = "demand_partial"
        else:
            reason = "" if complete else "measurement_missing"
        profile = "runtime_truth_full"
    return {
        "demand_total": demand_total,
        "delivered_demand_count": delivered,
        "partial_demand_count": partial,
        "omitted_demand_count": omitted,
        "system_fault_count": system_faults if faults_available else None,
        "writer_error_code": writer_error,
        "measurement_available": measured,
        "complete_delivery": complete,
        "primary_failure_reason": reason,
        "measurement_profile": profile,
    }


def compile_conversation_history(
    *,
    roster_id: str,
    user_id: str,
    conversation_id: str,
    conversation: dict[str, Any],
    messages: list[dict[str, Any]],
    telemetry: TelemetryIndex,
    timezone_name: str,
    issues: Counter[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    del conversation
    issue_counts = issues if issues is not None else Counter()
    ordered = sorted(
        messages,
        key=lambda item: (_timestamp(item.get("timestamp") or item.get("updatedAt")) or datetime.max.replace(tzinfo=timezone.utc), str(item.get("id") or item.get("messageId") or "")),
    )
    questions: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    for index, message in enumerate(ordered):
        if str(message.get("role") or "").strip().lower() != "user":
            continue
        question_ts = _timestamp(message.get("timestamp") or message.get("updatedAt"))
        message_id = str(message.get("id") or message.get("messageId") or "").strip()
        if question_ts is None or not message_id:
            issue_counts["user_message_missing_identity_or_timestamp"] += 1
            continue
        assistant: dict[str, Any] | None = None
        for candidate in ordered[index + 1 :]:
            role = str(candidate.get("role") or "").strip().lower()
            if role == "user":
                break
            if role == "assistant":
                assistant = candidate
                break
        grounded = _nested_dict((assistant or {}).get("grounded"))
        request_id = str(
            (assistant or {}).get("requestId")
            or grounded.get("requestId")
            or message.get("requestId")
            or ""
        ).strip()
        if not request_id:
            request_id = f"history:{_stable_id(roster_id, conversation_id, message_id)}"
        trace_id = str((assistant or {}).get("traceId") or message.get("traceId") or "").strip()
        turn_id = str(message.get("turnId") or (assistant or {}).get("turnId") or "").strip()
        mode = str(message.get("modeAtSend") or (assistant or {}).get("modeAtSend") or "").strip().lower()
        request_telemetry = telemetry.requests_by_trace.get(trace_id, {})
        device = str(request_telemetry.get("device_class") or "unknown").strip().lower()
        terminal_payload = telemetry.terminal(request_id, trace_id)
        truth = telemetry.truth(request_id, trace_id)
        revision = str(terminal_payload.get("revision_name") or truth.get("revision_name") or "").strip()
        git_sha = str(terminal_payload.get("git_sha") or truth.get("git_sha") or "").strip()
        questions.append({
            "event_id": f"question:{request_id}",
            "question_ts": question_ts,
            "question_date": question_ts.astimezone(ZoneInfo(timezone_name)).date(),
            "user_id": user_id,
            "roster_id": roster_id,
            "request_id": request_id,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "message_id": message_id,
            "mode": mode or str(request_telemetry.get("mode") or "unknown").strip().lower(),
            "device_class": device,
            "attachment_count": len(message.get("attachmentFileIds"))
            if isinstance(message.get("attachmentFileIds"), list)
            else 0,
            "producer_revision": revision,
            "producer_git_sha": git_sha,
            "question_category": QuestionCategory.UNCLASSIFIED.value,
            "classification_status": "not_measured",
            "record_origin": "firestore_history",
            "measurement_profile": "firestore_usage_only",
        })
        if assistant is None:
            continue
        answer_ts = _timestamp(assistant.get("timestamp") or assistant.get("updatedAt"))
        assistant_message_id = str(assistant.get("id") or assistant.get("messageId") or assistant.get("assistantMessageId") or "").strip()
        if answer_ts is None or not assistant_message_id:
            issue_counts["assistant_message_missing_identity_or_timestamp"] += 1
            continue
        status = str(assistant.get("status") or "").strip().lower()
        error_present = bool(str(assistant.get("errorMessage") or "").strip()) or status in _FAILED_MESSAGE_STATUSES
        terminal = str(terminal_payload.get("terminal_event") or "").strip().lower()
        if not terminal:
            terminal = "error" if error_present else ("final" if status in _FINISHED_MESSAGE_STATUSES else "")
        runtime_status = str(truth.get("status") or "").strip().lower()
        if not runtime_status:
            runtime_status = "failed" if error_present else ("completed" if status in _FINISHED_MESSAGE_STATUSES else "")
        measurement = _runtime_measurement(
            terminal=terminal,
            runtime_status=runtime_status,
            assistant_error=error_present,
            message_persisted=True,
            truth=truth,
        )
        stage_latency = truth.get("stage_latency_ms")
        answers.append({
            "event_id": f"answer:{request_id}",
            "answer_ts": answer_ts,
            "answer_date": answer_ts.astimezone(ZoneInfo(timezone_name)).date(),
            "user_id": user_id,
            "roster_id": roster_id,
            "request_id": request_id,
            "trace_id": trace_id,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "message_id": assistant_message_id,
            "mode": mode or str(terminal_payload.get("mode") or "unknown").strip().lower(),
            "device_class": device,
            "terminal": terminal,
            "runtime_status": runtime_status,
            "failure_code": str(terminal_payload.get("missing_reason") or ("assistant_error" if error_present else "")),
            **measurement,
            "total_latency_ms": _nonnegative_int(terminal_payload.get("latency_ms")),
            "stage_latency_json": json.dumps(stage_latency, ensure_ascii=False, sort_keys=True) if isinstance(stage_latency, dict) else "",
            "message_persisted": True,
            "assistant_error_present": error_present,
            "revision_name": revision,
            "git_sha": git_sha,
            "record_origin": "firestore_history",
        })
    return questions, answers


@dataclass
class HistoryRows:
    questions: list[dict[str, Any]] = field(default_factory=list)
    answers: list[dict[str, Any]] = field(default_factory=list)
    conversations: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)
    unmatched_users: list[str] = field(default_factory=list)
    issues: Counter[str] = field(default_factory=Counter)
    exclusions: Counter[str] = field(default_factory=Counter)

    def summary(self) -> dict[str, Any]:
        dates = [row["question_date"] for row in self.questions]
        return {
            "questions": len(self.questions),
            "answers": len(self.answers),
            "conversations": len(self.conversations),
            "citations": len(self.citations),
            "unmatchedUsers": len(self.unmatched_users),
            "issueCount": sum(self.issues.values()),
            "issues": dict(sorted((key, value) for key, value in self.issues.items() if value)),
            "exclusionCount": sum(self.exclusions.values()),
            "exclusions": dict(
                sorted((key, value) for key, value in self.exclusions.items() if value)
            ),
            "questionOrigins": dict(
                sorted(Counter(row["record_origin"] for row in self.questions).items())
            ),
            "measurement": {
                "measured": sum(row["measurement_available"] is True for row in self.answers),
                "total": len(self.answers),
            },
            "dateStart": min(dates).isoformat() if dates else "",
            "dateEnd": max(dates).isoformat() if dates else "",
        }


class HistoryRebuildJob:
    def __init__(
        self,
        settings: Settings,
        *,
        bigquery_client: Any | None = None,
        firestore_client: Any | None = None,
        directory: UserDirectoryRepository | None = None,
        chat_reader: FirestoreChatReader | None = None,
    ) -> None:
        self.settings = settings
        self.bigquery = bigquery_client or bigquery.Client(project=settings.monitor_project_id)
        database = str(settings.monitor_firestore_database or "(default)").strip() or "(default)"
        self.firestore = firestore_client or firestore.Client(project=settings.monitor_project_id, database=database)
        self.directory = directory or UserDirectoryRepository(settings, client=self.firestore)
        self.chat_reader = chat_reader or FirestoreChatReader(
            self.firestore,
            root_collection=settings.monitor_firestore_chat_collection,
            read_timeout_seconds=settings.monitor_firestore_read_timeout_seconds,
            read_page_size=settings.monitor_firestore_read_page_size,
        )
        self.dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"

    def _table_exists(self, name: str) -> bool:
        try:
            self.bigquery.get_table(f"{self.dataset}.{name}")
            return True
        except NotFound:
            return False

    def telemetry(self, *, start: datetime, end: datetime) -> TelemetryIndex:
        tables = [name for name in ("run_googleapis_com_stdout", "run_googleapis_com_stderr") if self._table_exists(name)]
        if not tables:
            return TelemetryIndex()
        sources = "\nUNION ALL\n".join(
            f"""SELECT timestamp AS source_ts, CAST(textPayload AS STRING) AS text_payload,
              TO_JSON_STRING(raw) AS row_json
            FROM `{self.dataset}.{name}` raw
            WHERE timestamp >= @start AND timestamp < @end"""
            for name in tables
        )
        sql = f"""SELECT source_ts, text_payload FROM ({sources})
        WHERE JSON_VALUE(row_json, '$.resource.labels.service_name') = @source_service
          AND REGEXP_CONTAINS(text_payload, r'(request_user_metric_json|stream_terminal_json|tmcs_stage_latency_json)')
        ORDER BY source_ts"""
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
                bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
                bigquery.ScalarQueryParameter("source_service", "STRING", self.settings.monitor_source_service),
            ],
            maximum_bytes_billed=self.settings.monitor_query_maximum_bytes,
            use_query_cache=True,
        )
        records = []
        for row in self.bigquery.query(sql, job_config=config, location=self.settings.monitor_bq_location).result():
            parsed = parse_telemetry_line(row.get("text_payload"), source_ts=row.get("source_ts"))
            if parsed is not None:
                records.append(parsed)
        return TelemetryIndex.from_records(records)

    def legacy_audits(self, *, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """Read the retired materialization once, without selecting raw content."""

        if not self._table_exists("monitor_answer_events"):
            return []
        sql = f"""
        SELECT
          event_ts, trace_id, request_id, conversation_id, turn_id, message_id,
          assistant_message_id, user_id, mode, question_category, has_error,
          error_code
        FROM `{self.dataset}.monitor_answer_events`
        WHERE event_date BETWEEN DATE(@start, @timezone_name)
          AND DATE_SUB(DATE(@end, @timezone_name), INTERVAL 0 DAY)
          AND event_ts >= @start AND event_ts < @end
          AND NULLIF(trace_id, '') IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY COALESCE(NULLIF(request_id, ''), trace_id)
          ORDER BY event_ts DESC, materialized_at DESC
        ) = 1
        ORDER BY event_ts
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start", "TIMESTAMP", start),
                bigquery.ScalarQueryParameter("end", "TIMESTAMP", end),
                bigquery.ScalarQueryParameter(
                    "timezone_name", "STRING", self.settings.monitor_timezone
                ),
            ],
            maximum_bytes_billed=self.settings.monitor_query_maximum_bytes,
            use_query_cache=True,
        )
        return [
            dict(row.items())
            for row in self.bigquery.query(
                sql,
                job_config=config,
                location=self.settings.monitor_bq_location,
            ).result()
        ]

    def _merge_legacy_audits(
        self,
        *,
        rows: HistoryRows,
        audits: list[dict[str, Any]],
        telemetry: TelemetryIndex,
        eligible_identity_candidates: dict[str, list[dict[str, Any]]],
        all_identity_candidates: dict[str, list[dict[str, Any]]],
        users_by_email: dict[str, list[dict[str, Any]]],
    ) -> None:
        questions_by_request = {
            str(row.get("request_id") or ""): row
            for row in rows.questions
            if str(row.get("request_id") or "")
        }
        questions_by_trace = {
            str(row.get("trace_id") or ""): row
            for row in rows.questions
            if str(row.get("trace_id") or "")
        }
        answers_by_request = {
            str(row.get("request_id") or ""): row
            for row in rows.answers
            if str(row.get("request_id") or "")
        }
        answers_by_trace = {
            str(row.get("trace_id") or ""): row
            for row in rows.answers
            if str(row.get("trace_id") or "")
        }
        zone = ZoneInfo(self.settings.monitor_timezone)

        for audit in audits:
            source_user_id = str(audit.get("user_id") or "").strip()
            candidates = eligible_identity_candidates.get(source_user_id, [])
            all_candidates = all_identity_candidates.get(source_user_id, [])
            if not candidates:
                try:
                    source_email = normalize_email(source_user_id)
                except ValueError:
                    source_email = ""
                if source_email:
                    email_candidates = users_by_email.get(source_email, [])
                    candidates = [
                        item
                        for item in email_candidates
                        if department_in_scope(
                            str(item.get("department") or ""), AnalysisScope.USER_MAP
                        )
                    ]
                    all_candidates = email_candidates
            if len(candidates) != 1:
                if not candidates and len(all_candidates) == 1:
                    rows.exclusions["legacy_audit_out_of_scope"] += 1
                elif not candidates and not all_candidates:
                    rows.exclusions["legacy_audit_not_in_roster"] += 1
                else:
                    rows.issues["legacy_audit_identity_unmatched_or_ambiguous"] += 1
                continue
            user = candidates[0]
            roster_id = str(user.get("roster_id") or "").strip()
            canonical_user_id = str(user.get("user_id") or source_user_id).strip()
            event_ts = _timestamp(audit.get("event_ts"))
            trace_id = str(audit.get("trace_id") or "").strip()
            if event_ts is None or not trace_id or not roster_id or not canonical_user_id:
                rows.issues["legacy_audit_missing_required_identity_or_timestamp"] += 1
                continue
            raw_request_id = str(audit.get("request_id") or "").strip()
            request_id = raw_request_id or f"legacy:{trace_id}"
            request_telemetry = telemetry.requests_by_trace.get(trace_id, {})
            question_ts = telemetry.request_source_ts_by_trace.get(trace_id) or event_ts
            category = migrate_legacy_question_category(audit.get("question_category"))
            classification_status = (
                "classified"
                if category is not QuestionCategory.UNCLASSIFIED
                else "not_measured"
            )
            existing_question = questions_by_request.get(request_id) or questions_by_trace.get(
                trace_id
            )
            if existing_question is not None:
                if existing_question.get("roster_id") != roster_id:
                    rows.issues["legacy_question_identity_conflict"] += 1
                    continue
                if category is not QuestionCategory.UNCLASSIFIED:
                    existing_question["question_category"] = category.value
                    existing_question["classification_status"] = classification_status
                    existing_question["measurement_profile"] = (
                        "firestore_history_with_legacy_category"
                    )
            else:
                terminal_payload = telemetry.terminal(request_id, trace_id)
                truth = telemetry.truth(request_id, trace_id)
                question = {
                    "event_id": f"question:{request_id}",
                    "question_ts": question_ts,
                    "question_date": question_ts.astimezone(zone).date(),
                    "user_id": canonical_user_id,
                    "roster_id": roster_id,
                    "request_id": request_id,
                    "trace_id": trace_id,
                    "conversation_id": str(audit.get("conversation_id") or "").strip(),
                    "turn_id": str(audit.get("turn_id") or "").strip(),
                    "message_id": str(audit.get("message_id") or "").strip(),
                    "mode": str(
                        audit.get("mode") or request_telemetry.get("mode") or "unknown"
                    ).strip().lower(),
                    "device_class": str(
                        request_telemetry.get("device_class") or "unknown"
                    ).strip().lower(),
                    "attachment_count": None,
                    "producer_revision": str(
                        terminal_payload.get("revision_name")
                        or truth.get("revision_name")
                        or ""
                    ).strip(),
                    "producer_git_sha": str(
                        terminal_payload.get("git_sha") or truth.get("git_sha") or ""
                    ).strip(),
                    "question_category": category.value,
                    "classification_status": classification_status,
                    "record_origin": "legacy_audit_history",
                    "measurement_profile": "legacy_audit_exact_fields",
                }
                rows.questions.append(question)
                questions_by_request[request_id] = question
                questions_by_trace[trace_id] = question

            existing_answer = answers_by_request.get(request_id) or answers_by_trace.get(
                trace_id
            )
            if existing_answer is not None:
                if existing_answer.get("roster_id") != roster_id:
                    rows.issues["legacy_answer_identity_conflict"] += 1
                continue
            terminal_payload = telemetry.terminal(request_id, trace_id)
            truth = telemetry.truth(request_id, trace_id)
            explicit_error = bool(audit.get("has_error")) or bool(
                str(audit.get("error_code") or "").strip()
            )
            terminal = str(terminal_payload.get("terminal_event") or "").strip().lower()
            if not terminal:
                terminal = "error" if explicit_error else "final"
            runtime_status = str(truth.get("status") or "").strip().lower()
            if not runtime_status:
                runtime_status = "failed" if explicit_error else "completed"
            measurement = _runtime_measurement(
                terminal=terminal,
                runtime_status=runtime_status,
                assistant_error=explicit_error,
                message_persisted=None,
                truth=truth,
            )
            stage_latency = truth.get("stage_latency_ms")
            answer = {
                "event_id": f"answer:{request_id}",
                "answer_ts": event_ts,
                "answer_date": event_ts.astimezone(zone).date(),
                "user_id": canonical_user_id,
                "roster_id": roster_id,
                "request_id": request_id,
                "trace_id": trace_id,
                "conversation_id": str(audit.get("conversation_id") or "").strip(),
                "turn_id": str(audit.get("turn_id") or "").strip(),
                "message_id": str(
                    audit.get("assistant_message_id") or audit.get("message_id") or ""
                ).strip(),
                "mode": str(
                    audit.get("mode") or terminal_payload.get("mode") or "unknown"
                ).strip().lower(),
                "device_class": str(
                    request_telemetry.get("device_class") or "unknown"
                ).strip().lower(),
                "terminal": terminal,
                "runtime_status": runtime_status,
                "failure_code": str(
                    audit.get("error_code")
                    or terminal_payload.get("missing_reason")
                    or ""
                ).strip(),
                **measurement,
                "total_latency_ms": _nonnegative_int(terminal_payload.get("latency_ms")),
                "stage_latency_json": json.dumps(
                    stage_latency, ensure_ascii=False, sort_keys=True
                )
                if isinstance(stage_latency, dict)
                else "",
                "message_persisted": None,
                "assistant_error_present": explicit_error,
                "revision_name": str(
                    terminal_payload.get("revision_name") or truth.get("revision_name") or ""
                ).strip(),
                "git_sha": str(
                    terminal_payload.get("git_sha") or truth.get("git_sha") or ""
                ).strip(),
                "record_origin": "legacy_audit_history",
            }
            rows.answers.append(answer)
            answers_by_request[request_id] = answer
            answers_by_trace[trace_id] = answer

    def compile(
        self,
        *,
        start: datetime,
        end: datetime,
        progress: ProgressCallback | None = None,
    ) -> HistoryRows:
        telemetry = self.telemetry(start=start, end=end)
        if progress is not None:
            progress(
                "telemetry_join_keys_indexed",
                len(telemetry.requests_by_trace)
                + len(telemetry.terminals_by_request)
                + len(telemetry.truths_by_request),
            )
        legacy_audits = self.legacy_audits(start=start, end=end)
        if progress is not None:
            progress("legacy_audits_deduplicated", len(legacy_audits))

        all_users = self.directory.list_users(include_inactive=True)
        users = []
        for user in all_users:
            try:
                eligible = department_in_scope(
                    str(user.get("department") or ""), AnalysisScope.USER_MAP
                )
            except ValueError:
                # The roster importer owns the allowed department vocabulary.
                # A malformed stored row is omitted and reported, never guessed.
                eligible = False
            if eligible:
                users.append(user)
        if progress is not None:
            progress("eligible_roster_users", len(users))

        chat_snapshot = self.chat_reader.full_snapshot(progress=progress)
        rows = HistoryRows(issues=Counter(chat_snapshot.issues))
        eligible_roster_ids = {
            str(user.get("roster_id") or "").strip() for user in users
        }
        eligible_identity_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        all_identity_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
        users_by_email: dict[str, list[dict[str, Any]]] = defaultdict(list)

        def add_identity(
            target: dict[str, list[dict[str, Any]]],
            identity: str,
            user: dict[str, Any],
        ) -> None:
            key = str(identity or "").strip()
            roster_id = str(user.get("roster_id") or "").strip()
            if not key or not roster_id:
                return
            if all(
                str(item.get("roster_id") or "").strip() != roster_id
                for item in target[key]
            ):
                target[key].append(user)

        for user in all_users:
            roster_id = str(user.get("roster_id") or "").strip()
            add_identity(
                all_identity_candidates,
                str(user.get("user_id") or ""),
                user,
            )
            if roster_id in eligible_roster_ids:
                add_identity(
                    eligible_identity_candidates,
                    str(user.get("user_id") or ""),
                    user,
                )
            try:
                users_by_email[normalize_email(str(user.get("email") or ""))].append(user)
            except ValueError:
                rows.issues["roster_user_invalid_email"] += 1
        roots_by_id: dict[str, Any] = {}
        roots_by_email: dict[str, list[Any]] = defaultdict(list)
        for root in chat_snapshot.roots:
            payload = root.payload
            if payload.get("identityVerified") is not True:
                continue
            try:
                email = normalize_email(str(payload.get("userEmail") or ""))
            except ValueError:
                rows.issues["verified_root_invalid_email"] += 1
                continue
            roots_by_id[root.root_id] = root
            roots_by_email[email].append(root)

        conversations_by_root: dict[str, list[Any]] = defaultdict(list)
        for conversation in chat_snapshot.conversations:
            conversations_by_root[conversation.root_id].append(conversation)

        processed_users = 0
        for user in users:
            processed_users += 1
            try:
                user_email = normalize_email(str(user.get("email") or ""))
            except ValueError:
                rows.issues["roster_user_invalid_email"] += 1
                rows.unmatched_users.append(str(user.get("roster_id") or ""))
                continue
            bound_root_id = str(user.get("chat_user_id") or "").strip()
            root_record = roots_by_id.get(bound_root_id) if bound_root_id else None
            if root_record is not None:
                try:
                    bound_email = normalize_email(str(root_record.payload.get("userEmail") or ""))
                except ValueError:
                    bound_email = ""
                if bound_email != user_email:
                    rows.issues["bound_identity_email_conflict"] += 1
                    rows.unmatched_users.append(str(user.get("roster_id") or ""))
                    continue
            elif bound_root_id:
                rows.issues["bound_chat_root_missing"] += 1
                rows.unmatched_users.append(str(user.get("roster_id") or ""))
                continue
            else:
                candidates = roots_by_email.get(user_email, [])
                if len(candidates) == 1:
                    root_record = candidates[0]
                elif len(candidates) > 1:
                    rows.issues["ambiguous_verified_roots"] += 1
            if root_record is None:
                rows.unmatched_users.append(str(user.get("roster_id") or ""))
                continue
            root_payload = root_record.payload
            directory_user_id = str(user.get("user_id") or "").strip()
            root_user_id = str(root_payload.get("subject") or root_payload.get("userId") or "").strip()
            if directory_user_id and root_user_id and directory_user_id != root_user_id:
                rows.issues["bound_subject_conflict"] += 1
                rows.unmatched_users.append(str(user.get("roster_id") or ""))
                continue
            user_id = directory_user_id or root_user_id
            if not user_id:
                rows.issues["verified_root_subject_missing"] += 1
                rows.unmatched_users.append(str(user.get("roster_id") or ""))
                continue
            resolved_user = {**user, "user_id": user_id}
            add_identity(eligible_identity_candidates, user_id, resolved_user)
            add_identity(all_identity_candidates, user_id, resolved_user)
            for conversation_record in conversations_by_root.get(root_record.root_id, []):
                conversation = conversation_record.conversation
                messages = conversation_record.messages
                if not messages:
                    rows.exclusions["conversation_without_messages"] += 1
                    continue
                questions, answers = compile_conversation_history(
                    roster_id=str(user["roster_id"]),
                    user_id=user_id,
                    conversation_id=conversation_record.conversation_id,
                    conversation=conversation,
                    messages=messages,
                    telemetry=telemetry,
                    timezone_name=self.settings.monitor_timezone,
                    issues=rows.issues,
                )
                rows.questions.extend(row for row in questions if start <= row["question_ts"] < end)
                rows.answers.extend(row for row in answers if start <= row["answer_ts"] < end)
                try:
                    projected_conversation = project_conversation(
                        roster_id=str(user["roster_id"]), user_id=user_id,
                        conversation_id=conversation_record.conversation_id,
                        conversation=conversation, messages=messages,
                        timezone_name=self.settings.monitor_timezone,
                    )
                except ProjectionDataError as exc:
                    rows.issues[exc.code] += 1
                    continue
                if start <= projected_conversation["last_active_at"] < end:
                    rows.conversations.append(projected_conversation)
                rows.citations.extend(
                    row for row in project_citations(
                        roster_id=str(user["roster_id"]), user_id=user_id,
                        conversation_id=conversation_record.conversation_id, messages=messages,
                        timezone_name=self.settings.monitor_timezone,
                    ) if start <= row["answer_ts"] < end
                )
            if progress is not None and (processed_users % 10 == 0 or processed_users == len(users)):
                progress("roster_users_processed", processed_users)
        self._merge_legacy_audits(
            rows=rows,
            audits=legacy_audits,
            telemetry=telemetry,
            eligible_identity_candidates=eligible_identity_candidates,
            all_identity_candidates=all_identity_candidates,
            users_by_email=users_by_email,
        )
        if progress is not None:
            progress("history_questions_compiled", len(rows.questions))
        return rows

    def apply(self, rows: HistoryRows) -> list[dict[str, Any]]:
        for name, values in (
            ("questions", rows.questions),
            ("answers", rows.answers),
            ("conversations", rows.conversations),
            ("citations", rows.citations),
        ):
            event_ids = [str(row.get("event_id") or "") for row in values]
            if len(event_ids) != len(set(event_ids)):
                raise ValueError(f"duplicate compiled history event_id in {name}")
        template = SQL_PATH.read_text(encoding="utf-8")
        sql = template.replace("${PROJECT_ID}", self.settings.monitor_project_id).replace(
            "${DATASET_ID}", self.settings.monitor_bq_dataset
        )
        by_date: dict[date, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for row in rows.questions:
            by_date[row["question_date"]]["questions"].append(row)
        for row in rows.answers:
            by_date[row["answer_date"]]["answers"].append(row)
        for row in rows.conversations:
            by_date[row["updated_date"]]["conversations"].append(row)
        for row in rows.citations:
            by_date[row["answer_date"]]["citations"].append(row)
        results = []
        for activity_date, values in sorted(by_date.items()):
            parameters = [
                struct_array_parameter("history_questions", QUESTION_SCHEMA, values["questions"]),
                struct_array_parameter("history_answers", ANSWER_SCHEMA, values["answers"]),
                struct_array_parameter("history_conversations", CONVERSATION_SCHEMA, values["conversations"]),
                struct_array_parameter("history_citations", CITATION_SCHEMA, values["citations"]),
            ]
            config = bigquery.QueryJobConfig(
                query_parameters=parameters,
                maximum_bytes_billed=self.settings.monitor_query_maximum_bytes,
                use_query_cache=False,
            )
            self.bigquery.query(
                f"BEGIN TRANSACTION;\n{sql}\nCOMMIT TRANSACTION;",
                job_config=config,
                location=self.settings.monitor_bq_location,
            ).result()
            results.append({"date": activity_date.isoformat(), **{key: len(value) for key, value in values.items()}})
        return results

    def verify(self, rows: HistoryRows) -> dict[str, Any]:
        dates = [row["question_date"] for row in rows.questions]
        dates.extend(row["answer_date"] for row in rows.answers)
        dates.extend(row["updated_date"] for row in rows.conversations)
        dates.extend(row["answer_date"] for row in rows.citations)
        if not dates:
            return {"checks": [], "passed": True}
        sql = f"""
        WITH questions AS (
          SELECT * FROM `{self.dataset}.question_events`
          WHERE question_date BETWEEN @start_date AND @end_date
        ), answers AS (
          SELECT * FROM `{self.dataset}.answer_events`
          WHERE answer_date BETWEEN DATE_SUB(@start_date, INTERVAL 1 DAY)
            AND DATE_ADD(@end_date, INTERVAL 1 DAY)
        ), conversations AS (
          SELECT * FROM `{self.dataset}.conversation_events`
          WHERE updated_date BETWEEN @start_date AND @end_date
        ), citations AS (
          SELECT * FROM `{self.dataset}.citation_events`
          WHERE answer_date BETWEEN @start_date AND @end_date
        ), checks AS (
          SELECT 'duplicate_question_event_id' AS name,
            COUNT(*) - COUNT(DISTINCT event_id) AS failures FROM questions
          UNION ALL
          SELECT 'duplicate_answer_event_id',
            COUNT(*) - COUNT(DISTINCT event_id) FROM answers
          UNION ALL
          SELECT 'duplicate_question_request_id',
            COUNT(*) - COUNT(DISTINCT request_id) FROM questions
            WHERE request_id IS NOT NULL AND request_id != ''
          UNION ALL
          SELECT 'history_answer_without_question', COUNTIF(question.event_id IS NULL)
          FROM answers answer
          LEFT JOIN questions question USING (request_id)
          WHERE answer.record_origin IN ('firestore_history', 'legacy_audit_history')
          UNION ALL
          SELECT 'missing_compiled_questions', COUNTIF(question.event_id IS NULL)
          FROM UNNEST(@expected_question_ids) expected_id
          LEFT JOIN questions question ON question.event_id = expected_id
          UNION ALL
          SELECT 'missing_compiled_answers', COUNTIF(answer.event_id IS NULL)
          FROM UNNEST(@expected_answer_ids) expected_id
          LEFT JOIN answers answer ON answer.event_id = expected_id
          UNION ALL
          SELECT 'missing_compiled_conversations', COUNTIF(conversation.event_id IS NULL)
          FROM UNNEST(@expected_conversation_ids) expected_id
          LEFT JOIN conversations conversation ON conversation.event_id = expected_id
          UNION ALL
          SELECT 'missing_compiled_citations', COUNTIF(citation.event_id IS NULL)
          FROM UNNEST(@expected_citation_ids) expected_id
          LEFT JOIN citations citation ON citation.event_id = expected_id
        )
        SELECT name, failures FROM checks ORDER BY name
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("start_date", "DATE", min(dates)),
                bigquery.ScalarQueryParameter("end_date", "DATE", max(dates)),
                bigquery.ArrayQueryParameter(
                    "expected_question_ids", "STRING",
                    [row["event_id"] for row in rows.questions],
                ),
                bigquery.ArrayQueryParameter(
                    "expected_answer_ids", "STRING",
                    [row["event_id"] for row in rows.answers],
                ),
                bigquery.ArrayQueryParameter(
                    "expected_conversation_ids", "STRING",
                    [row["event_id"] for row in rows.conversations],
                ),
                bigquery.ArrayQueryParameter(
                    "expected_citation_ids", "STRING",
                    [row["event_id"] for row in rows.citations],
                ),
            ],
            maximum_bytes_billed=self.settings.monitor_query_maximum_bytes,
            use_query_cache=False,
        )
        checks = [
            dict(row.items())
            for row in self.bigquery.query(
                sql, job_config=config, location=self.settings.monitor_bq_location
            ).result()
        ]
        return {
            "checks": checks,
            "passed": all(int(item.get("failures") or 0) == 0 for item in checks),
        }

    def record_verified_rebuild(
        self,
        *,
        confirmation: str,
        data_through: datetime,
    ) -> None:
        """Publish the deletion prerequisite only after exact ID verification."""

        sql = f"""
        BEGIN TRANSACTION;
        DELETE FROM `{self.dataset}.pipeline_state`
        WHERE source = 'history_rebuild';
        INSERT INTO `{self.dataset}.pipeline_state`
          (source, data_through, published_run_id, status, updated_at)
        VALUES (
          'history_rebuild', @data_through, @confirmation, 'succeeded',
          CURRENT_TIMESTAMP()
        );
        COMMIT TRANSACTION;
        """
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(
                    "confirmation", "STRING", confirmation
                ),
                bigquery.ScalarQueryParameter(
                    "data_through", "TIMESTAMP", data_through
                ),
            ],
            maximum_bytes_billed=self.settings.monitor_query_maximum_bytes,
            use_query_cache=False,
        )
        self.bigquery.query(
            sql,
            job_config=config,
            location=self.settings.monitor_bq_location,
        ).result()


def _credential_guard() -> None:
    path = str(os.environ.get("CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE") or "").strip()
    if not path or not Path(path).is_file():
        raise SystemExit("approved CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE is required")
    configured = str(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") or "").strip()
    if configured and Path(configured).resolve() != Path(path).resolve():
        raise SystemExit("GOOGLE_APPLICATION_CREDENTIALS must use the same approved credential")
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan or run the one-time canonical Monitor history rebuild")
    parser.add_argument("--apply", action="store_true", help="write the compiled rows into the canonical fact tables")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    _credential_guard()
    settings = get_settings()
    start = _timestamp(settings.monitor_analytics_start_at)
    if start is None:
        raise SystemExit("MONITOR_ANALYTICS_START_AT is required")
    end = datetime.now(timezone.utc)
    job = HistoryRebuildJob(settings)
    started_at = monotonic()

    def progress(stage: str, count: int) -> None:
        print(
            json.dumps(
                {
                    "history_rebuild_progress": True,
                    "stage": stage,
                    "count": count,
                    "elapsedSeconds": round(monotonic() - started_at, 1),
                },
                sort_keys=True,
            ),
            file=sys.stderr,
            flush=True,
        )

    rows = job.compile(start=start, end=end, progress=progress)
    summary = rows.summary()
    confirmation = (
        f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}:"
        f"{summary['dateStart']}:{summary['dateEnd']}:{summary['questions']}:"
        f"{summary['answers']}:{summary['issueCount']}"
    )
    output = {"mode": "apply" if args.apply else "plan", **summary, "requiredConfirmation": confirmation}
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    if not args.apply:
        return 0
    if args.confirm != confirmation:
        raise SystemExit(f"--confirm must equal {confirmation}")
    partitions = job.apply(rows)
    verification = job.verify(rows)
    print(json.dumps({"status": "applied", "partitions": partitions, "verification": verification}, ensure_ascii=False, sort_keys=True))
    if verification["passed"] is not True:
        raise SystemExit("history rebuild verification failed")
    job.record_verified_rebuild(
        confirmation=confirmation,
        data_through=end,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
