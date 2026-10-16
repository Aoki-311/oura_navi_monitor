"""Bounded, insert-only history recovery for explicitly selected roster users.

This does not publish a snapshot, move a watermark, or certify a global rebuild.
Run the normal refresh after a successful recovery to publish the new roster.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from google.cloud import bigquery, firestore
from google.oauth2 import service_account

from app.contracts.admin import normalize_email
from app.domain.analysis_scopes import AnalysisScope
from app.domain.roster_records import read_canonical_roster_collection
from app.jobs.project_firestore import (
    CITATION_SCHEMA, CONVERSATION_SCHEMA, NO_INTERNAL_RETRY,
    project_citations, project_conversation, struct_array_parameter,
)
from app.jobs.rebuild_history import (
    ANSWER_SCHEMA, QUESTION_SCHEMA, SQL_PATH, HistoryRows, TelemetryIndex,
    compile_conversation_history,
)
from app.jobs.refresh_analytics import AnalyticsRefreshJob
from app.refresh_policy import REFRESH_POLICY
from app.repositories.user_directory import UserDirectoryRepository
from app.settings import get_settings
from scripts.credential_preflight import approved_credential_path

FAMILIES = {
    "questions": ("question_events", "question_date", QUESTION_SCHEMA),
    "answers": ("answer_events", "answer_date", ANSWER_SCHEMA),
    "conversations": ("conversation_events", "updated_date", CONVERSATION_SCHEMA),
    "citations": ("citation_events", "answer_date", CITATION_SCHEMA),
}
MAX_USERS = 20
MAX_ROWS = 10000


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamps must include a timezone")
    return parsed.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, default=lambda item: item.isoformat(), sort_keys=True, separators=(",", ":"))


def select_users(directory: Any, roster_ids: list[str]) -> list[dict[str, Any]]:
    if not roster_ids or len(roster_ids) > MAX_USERS or len(set(roster_ids)) != len(roster_ids):
        raise ValueError("select between 1 and 20 distinct roster IDs")
    # Validate against the whole collection; selecting first could hide an
    # inactive duplicate email, subject, or chat root owned by another row.
    records = read_canonical_roster_collection(directory.list_users(include_inactive=True))
    selected = {record.value["roster_id"]: record for record in records if record.value["roster_id"] in roster_ids}
    if set(selected) != set(roster_ids):
        raise ValueError("a selected roster ID does not exist")
    users = []
    for roster_id in sorted(roster_ids):
        record = selected[roster_id]
        if not record.identity_eligible or not record.projection_eligible or not record.evaluation.membership.includes(AnalysisScope.USER_MAP):
            raise ValueError("a selected roster row is ambiguous or outside user analysis")
        if not record.value.get("chat_user_id") or not record.value.get("user_id"):
            raise ValueError("all selected users must already have verified bound identities")
        users.append(record.value)
    return users


def check_root(user: dict[str, Any], root: dict[str, Any]) -> None:
    if (root.get("identityVerified") is not True
        or normalize_email(str(root.get("userEmail") or "")) != user["email"]
        or str(root.get("subject") or "").strip() != user["user_id"]):
        raise ValueError("selected chat root does not match its verified roster identity")


def compile_selected(settings: Any, client: Any, users: list[dict[str, Any]], start: datetime, end: datetime) -> HistoryRows:
    rows = HistoryRows()
    read_args = {"retry": NO_INTERNAL_RETRY, "timeout": settings.monitor_firestore_read_timeout_seconds}
    for user in users:
        root = client.collection(settings.monitor_firestore_chat_collection).document(user["chat_user_id"])
        snapshot = root.get(**read_args)
        if not snapshot.exists:
            raise ValueError("selected chat root is missing")
        check_root(user, snapshot.to_dict() or {})
        # Intentionally no updatedAt filter: dormant users' old conversations
        # are precisely what the incremental refresh cannot discover.
        for document in root.collection("conversations").stream(**read_args):
            messages = [{**(item.to_dict() or {}), "id": item.id}
                        for item in document.reference.collection("messages").stream(**read_args)]
            if not messages:
                continue
            context = dict(roster_id=user["roster_id"], user_id=user["user_id"],
                           conversation_id=document.id, messages=messages, timezone_name=settings.monitor_timezone)
            questions, answers = compile_conversation_history(
                **context, conversation=document.to_dict() or {}, telemetry=TelemetryIndex(), issues=rows.issues)
            rows.questions.extend(row for row in questions if start <= row["question_ts"] < end)
            rows.answers.extend(row for row in answers if start <= row["answer_ts"] < end)
            conversation = project_conversation(**context, conversation=document.to_dict() or {})
            if start <= conversation["last_active_at"] < end:
                rows.conversations.append(conversation)
            rows.citations.extend(row for row in project_citations(**context) if start <= row["answer_ts"] < end)
            if sum(len(getattr(rows, name)) for name in FAMILIES) > MAX_ROWS:
                raise ValueError("history recovery exceeds the 10000-row bound; use smaller selections")
    if rows.issues:
        raise ValueError("selected history contains invalid source records; audit them before recovery")
    return rows


def _keys(family: str, row: dict[str, Any]) -> set[tuple[Any, ...]]:
    keys = {("event", row["event_id"])}
    user_id = row.get("user_id")
    if family in {"questions", "answers"}:
        if row.get("request_id"):
            keys.add(("request", row["request_id"]))
        if row.get("conversation_id") and row.get("message_id"):
            keys.add(("message", user_id, row["conversation_id"], row["message_id"]))
    elif family == "conversations":
        keys.add(("conversation", user_id, row["conversation_id"]))
    elif row.get("message_id"):
        keys.add(("citation", user_id, row["message_id"], row.get("citation_order"), row.get("document_key")))
    return keys


def missing_rows(family: str, expected: list[dict[str, Any]], existing: list[dict[str, Any]]) -> list[dict[str, Any]]:
    index: dict[tuple[Any, ...], set[int]] = defaultdict(set)
    for position, row in enumerate(existing):
        for key in _keys(family, row):
            index[key].add(position)
    if any(len(values) != 1 for values in index.values()):
        raise ValueError("canonical history contains duplicate logical identities")
    seen: set[tuple[Any, ...]] = set()
    missing = []
    for row in expected:
        keys = _keys(family, row)
        if seen & keys:
            raise ValueError("compiled history contains duplicate logical identities")
        seen.update(keys)
        matches = set().union(*(index.get(key, set()) for key in keys))
        if len(matches) > 1:
            raise ValueError("compiled history matches conflicting canonical identities")
        if matches:
            current = existing[next(iter(matches))]
            if (current.get("user_id") != row.get("user_id") or current.get("roster_id") != row.get("roster_id")
                or current["event_id"] != row["event_id"]):
                raise ValueError("canonical identity conflicts with compiled history; existing facts are preserved")
            for key in ("request_id", "conversation_id", "message_id"):
                if current.get(key) and row.get(key) and current[key] != row[key]:
                    raise ValueError("canonical fact disagrees with its source identity")
            if family == "conversations":
                if (not current.get("last_active_at") or current["last_active_at"] < row["last_active_at"]
                    or any(current.get(key) is None or current[key] < row[key]
                           for key in ("user_message_count", "assistant_message_count"))):
                    raise ValueError("canonical conversation is stale; explicit repair is required")
            continue
        missing.append(row)
    return missing


def insert_only_sql() -> str:
    # Reuse the canonical column/schema owner. Only its four MATCHED update
    # branches are removed; no current fact can be replaced by history data.
    sql, count = re.subn(r"WHEN MATCHED\b.*?(?=WHEN NOT MATCHED)", "", SQL_PATH.read_text(encoding="utf-8"), flags=re.S)
    if count != 4 or "WHEN MATCHED" in sql or "DELETE FROM" in sql or "UPDATE SET" in sql:
        raise ValueError("canonical history SQL changed; review the insert-only adapter")
    return sql


class RosterHistoryBackfill:
    def __init__(self, settings: Any, *, bigquery_client: Any, firestore_client: Any):
        self.settings, self.bigquery, self.firestore = settings, bigquery_client, firestore_client
        self.dataset = f"{settings.monitor_project_id}.{settings.monitor_bq_dataset}"
        self.directory = UserDirectoryRepository(settings, client=firestore_client)

    def query(self, sql: str, parameters: list[Any]) -> list[dict[str, Any]]:
        config = bigquery.QueryJobConfig(query_parameters=parameters, maximum_bytes_billed=self.settings.monitor_query_maximum_bytes, use_query_cache=False)
        return [dict(row.items()) for row in self.bigquery.query(sql, job_config=config, location=self.settings.monitor_bq_location).result()]

    def publication(self) -> dict[str, Any]:
        rows = self.query(f"SELECT data_through, published_run_id, status FROM `{self.dataset}.pipeline_state` WHERE source = 'published'", [])
        if len(rows) != 1 or rows[0].get("status") != "succeeded" or not rows[0].get("data_through") or not rows[0].get("published_run_id"):
            raise ValueError("history recovery requires an existing successful publication")
        return rows[0]

    def assert_idle_writers(self) -> None:
        location = self.settings.monitor_bq_location.lower()
        active = self.query(f"""SELECT job_id FROM
          `{self.settings.monitor_project_id}.region-{location}.INFORMATION_SCHEMA.JOBS_BY_PROJECT`
          WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY)
            AND state IN ('PENDING', 'RUNNING')
            AND statement_type IN ('SCRIPT', 'INSERT', 'UPDATE', 'DELETE', 'MERGE', 'CREATE_TABLE_AS_SELECT')
            AND REGEXP_CONTAINS(IFNULL(query, ''), @dataset_pattern)""", [
                bigquery.ScalarQueryParameter("dataset_pattern", "STRING",
                    re.escape(self.settings.monitor_project_id) + r"[.:]" + re.escape(self.settings.monitor_bq_dataset))])
        if active:
            raise ValueError("canonical BigQuery writers are still in flight")

    def existing(self, family: str, users: list[dict[str, Any]], expected: list[dict[str, Any]], coverage_start: date, coverage_end: date) -> list[dict[str, Any]]:
        table, partition, _schema = FAMILIES[family]
        columns = "event_id, user_id, roster_id"
        columns += ", message_id, request_id, conversation_id" if family in {"questions", "answers"} else (
            ", conversation_id, last_active_at, user_message_count, assistant_message_count" if family == "conversations"
            else ", message_id, citation_order, document_key")
        request_clause = " OR request_id IN UNNEST(@requests)" if family in {"questions", "answers"} else ""
        parameters = [
            bigquery.ScalarQueryParameter("coverage_start", "DATE", coverage_start),
            bigquery.ScalarQueryParameter("coverage_end", "DATE", coverage_end),
            bigquery.ArrayQueryParameter("users", "STRING", [user["user_id"] for user in users]),
            bigquery.ArrayQueryParameter("events", "STRING", [row["event_id"] for row in expected]),
        ]
        if request_clause:
            parameters.append(bigquery.ArrayQueryParameter("requests", "STRING", [row["request_id"] for row in expected]))
        return self.query(f"""SELECT {columns}, {partition} FROM `{self.dataset}.{table}`
          WHERE {partition} BETWEEN @coverage_start AND @coverage_end
          AND (user_id IN UNNEST(@users) OR event_id IN UNNEST(@events){request_clause})""", parameters)

    def plan(self, roster_ids: list[str], start: datetime, end: datetime) -> dict[str, Any]:
        analytics_start = timestamp(self.settings.monitor_analytics_start_at)
        now = datetime.now(timezone.utc)
        if not analytics_start <= start < end <= now:
            raise ValueError("require analytics start <= start < end <= current time")
        zone = ZoneInfo(self.settings.monitor_timezone)
        users = select_users(self.directory, roster_ids)
        rows = compile_selected(self.settings, self.firestore, users, start, end)
        # Include all current canonical partitions, not merely the requested
        # source window, to catch a conversation already saved on another day.
        coverage_start = analytics_start.astimezone(zone).date()
        coverage_end = now.astimezone(zone).date() + timedelta(days=1)
        existing, missing = {}, {}
        for family in FAMILIES:
            expected = getattr(rows, family)
            existing[family] = self.existing(family, users, expected, coverage_start, coverage_end)
            missing[family] = missing_rows(family, expected, existing[family])
        question_identity = lambda row: (row.get("request_id"), row.get("user_id"), row.get("roster_id"))
        available_requests = {question_identity(row) for row in rows.questions}
        available_requests.update(question_identity(row) for row in existing["questions"])
        if any(question_identity(row) not in available_requests for row in missing["answers"]):
            raise ValueError("bounded recovery would create an answer without a question; widen the start")
        binding_fields = ("roster_id", "user_id", "chat_user_id", "email", "department", "role", "is_active", "updated_at")
        material = {
            "contract": "monitor.roster-history.v1", "dataset": self.dataset,
            "firestoreDatabase": self.settings.monitor_firestore_database,
            "chatCollection": self.settings.monitor_firestore_chat_collection,
            "start": start, "end": end, "publication": self.publication(),
            "bindings": [{key: user.get(key) for key in binding_fields} for user in users],
            "coverageStart": coverage_start, "coverageEnd": coverage_end,
            "existing": {key: sorted(value, key=_json) for key, value in existing.items()},
            "compiled": {family: sorted([{key: value for key, value in row.items() if key != "materialized_at"}
                                        for row in getattr(rows, family)], key=_json) for family in FAMILIES},
        }
        digest = hashlib.sha256(_json(material).encode()).hexdigest()
        source_digest = hashlib.sha256(_json({key: value for key, value in material.items()
                                             if key not in {"existing", "publication", "coverageStart", "coverageEnd"}}).encode()).hexdigest()
        return {"planDigest": digest, "sourceDigest": source_digest, "publication": material["publication"], "users": users,
                "rows": rows, "missing": missing,
                "summary": {"selectedUsers": len(users), "start": start.isoformat(), "end": end.isoformat(),
                            "source": {name: len(getattr(rows, name)) for name in FAMILIES},
                            "missing": {name: len(value) for name, value in missing.items()}}}

    def apply_sql(self, plan: dict[str, Any], lease_id: str) -> None:
        sql = [f"""BEGIN TRANSACTION;
ASSERT (SELECT COUNT(*) = 1 FROM `{self.dataset}.pipeline_state`
  WHERE source = 'published' AND lease_run_id = @lease_id
    AND lease_expires_at > CURRENT_TIMESTAMP() AND data_through = @watermark
    AND published_run_id = @published_run_id AND status = 'succeeded')
  AS 'canonical publisher lease or publication changed';"""]
        parameters = [bigquery.ScalarQueryParameter("lease_id", "STRING", lease_id),
                      bigquery.ScalarQueryParameter("watermark", "TIMESTAMP", plan["publication"]["data_through"]),
                      bigquery.ScalarQueryParameter("published_run_id", "STRING", plan["publication"]["published_run_id"])]
        partitions: dict[date, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: defaultdict(list))
        for family, values in plan["missing"].items():
            for row in values:
                partitions[row[FAMILIES[family][1]]][family].append(row)
        template = insert_only_sql().replace("${PROJECT_ID}", self.settings.monitor_project_id).replace("${DATASET_ID}", self.settings.monitor_bq_dataset)
        for index, (partition, values) in enumerate(sorted(partitions.items())):
            suffix = f"_{index}"
            statement = template
            names = ["history_partition_date"] + [f"history_{family}" for family in FAMILIES]
            for name in names:
                statement = statement.replace(f"@{name}", f"@{name}{suffix}")
            sql.append(statement)
            parameters.append(bigquery.ScalarQueryParameter("history_partition_date" + suffix, "DATE", partition))
            parameters.extend(struct_array_parameter(f"history_{family}{suffix}", schema, values[family])
                              for family, (_table, _date, schema) in FAMILIES.items())
        sql.append("COMMIT TRANSACTION;")
        # One transaction means a failed partition cannot leave half a plan.
        if len(_json([parameter.to_api_repr() for parameter in parameters]).encode()) > 5_000_000:
            raise ValueError("history recovery request exceeds 5 MB; use smaller selections")
        self.query("\n".join(sql), parameters)


def verify_freeze(path: Path, settings: Any, region: str, credential_file: str) -> None:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    expected = {"project": settings.monitor_project_id, "region": region, "dataset": settings.monitor_bq_dataset,
                "location": settings.monitor_bq_location, "source_service": settings.monitor_source_service,
                "old_scheduler": REFRESH_POLICY.legacy_scheduler_name, "new_scheduler": REFRESH_POLICY.scheduler_name}
    if (any(receipt.get(key) != value for key, value in expected.items())
        or not receipt.get("freeze_started_at") or not receipt.get("freeze_verified_at")
        or receipt.get("active_bigquery_writers_at_freeze") != []
        or receipt.get("activation_started_at")):
        raise ValueError("freeze receipt does not establish this inactive refresh chain")
    if not timestamp(receipt["freeze_started_at"]) <= timestamp(receipt["freeze_verified_at"]) <= datetime.now(timezone.utc):
        raise ValueError("freeze receipt timestamps are invalid")
    env = {**os.environ, "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": credential_file}
    def read(*arguments: str) -> Any:
        result = subprocess.run(["gcloud", f"--project={settings.monitor_project_id}", *arguments, "--format=json"],
                                check=True, capture_output=True, text=True, env=env)
        return json.loads(result.stdout)
    expected_uri = f"https://{region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/{settings.monitor_project_id}/jobs/{REFRESH_POLICY.job_name}:run"
    for label, name in (("old", REFRESH_POLICY.legacy_scheduler_name), ("new", REFRESH_POLICY.scheduler_name)):
        scheduler = read("scheduler", "jobs", "describe", name, f"--location={region}")
        target = scheduler.get("httpTarget") or {}
        account = receipt.get(f"expected_{label}_scheduler_service_account")
        if (scheduler.get("state") != "PAUSED" or target.get("uri") != expected_uri or not account
            or (target.get("oauthToken") or {}).get("serviceAccountEmail") != account):
            raise ValueError("live scheduler is not frozen with the expected refresh binding")
    executions = read("run", "jobs", "executions", "list", f"--job={REFRESH_POLICY.job_name}", f"--region={region}")
    for execution in executions:
        status = execution.get("status") or execution
        if not (status.get("completionTime") or execution.get("completionTime")):
            raise ValueError("a refresh execution is still in flight")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roster-id", action="append", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--expected-plan-digest", default="")
    parser.add_argument("--freeze-snapshot", type=Path)
    parser.add_argument("--region", default="us-central1")
    parser.add_argument("--receipt-output", type=Path)
    args = parser.parse_args()
    if args.apply and (not args.expected_plan_digest or not args.freeze_snapshot or not args.receipt_output
                       or args.receipt_output.exists() or not args.receipt_output.parent.is_dir()):
        raise SystemExit("apply requires expected plan digest, freeze snapshot, and a new receipt path")
    credential_file = str(approved_credential_path(args.credential_file))
    credentials = service_account.Credentials.from_service_account_file(credential_file)
    settings = get_settings()
    job = RosterHistoryBackfill(settings, bigquery_client=bigquery.Client(project=settings.monitor_project_id, credentials=credentials),
                               firestore_client=firestore.Client(project=settings.monitor_project_id, database=settings.monitor_firestore_database, credentials=credentials))
    start, end = timestamp(args.start), timestamp(args.end)
    if not args.apply:
        plan = job.plan(args.roster_id, start, end)
        print(_json({"mode": "plan", "planDigest": plan["planDigest"], **plan["summary"]}))
        return 0
    verify_freeze(args.freeze_snapshot, settings, args.region, credential_file)
    job.assert_idle_writers()
    owner = AnalyticsRefreshJob(settings, client=job.bigquery, projector=object(), execution_id=f"roster-history-{uuid4().hex}")
    lease_id = owner._lease_id()
    owner._acquire_lease(lease_id)
    safe_release = True
    try:
        plan = job.plan(args.roster_id, start, end)
        if plan["planDigest"] != args.expected_plan_digest:
            raise ValueError("source, roster, canonical coverage, or publication changed; generate a new plan")
        verify_freeze(args.freeze_snapshot, settings, args.region, credential_file)
        owner._renew_lease(lease_id)
        job.assert_idle_writers()
        if select_users(job.directory, args.roster_id) != plan["users"]:
            raise ValueError("roster bindings changed before apply; generate a new plan")
        safe_release = False  # A timeout may have committed; let the lease expire.
        job.apply_sql(plan, lease_id)
        safe_release = True
        verification = job.plan(args.roster_id, start, end)
        if (any(verification["missing"].values()) or verification["publication"] != plan["publication"]
            or verification["sourceDigest"] != plan["sourceDigest"]):
            raise ValueError("post-write coverage or unchanged-publication verification failed")
        receipt = {"receiptType": "monitor.roster-history.v1", "planDigest": plan["planDigest"],
                   "verificationDigest": verification["planDigest"], "sourceDigest": plan["sourceDigest"], "publication": plan["publication"],
                   "verifiedAt": datetime.now(timezone.utc).isoformat(), **plan["summary"],
                   "coverageVerified": True, "requiresNormalRefresh": True}
        with args.receipt_output.open("x", encoding="utf-8") as handle:
            handle.write(_json(receipt) + "\n")
        print(_json({"mode": "applied", **receipt}))
    finally:
        if safe_release:
            owner._release_lease(lease_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
