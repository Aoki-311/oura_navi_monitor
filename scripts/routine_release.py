#!/usr/bin/env python3
"""Staged, reader-first updates of an existing Monitor installation.

This is not the initial migration workflow. No DTS, IAM, schema, source,
measurement boundary or Scheduler configuration is changed here. Every cloud
mutation requires an exact plan confirmation; interrupted stages retain their
locks and must be resumed explicitly after readback.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.credential_preflight import approved_credential_path
from scripts.promotion_release_lock import (
    acquire_release_lock, release_release_lock, service_lock_document_id,
    _expected_lock, _matches_exact_lock,
)
from scripts.release_operation_lock import (
    acquire_operation_lock, release_operation_lock, operation_lock_document_id,
    _expected as operation_expected, _matches as operation_matches,
)
from scripts.promotion_receipt_state import _validate_revision
from scripts.verify_candidate_service import verify_candidate
from scripts.verify_service_access_contract import (
    _access, _iam, require_reconciled_ready, traffic_planes,
    require_reconciled_traffic, require_exact_tagged_revision,
)
from scripts.validate_refresh_job import validate_refresh_job, validate_refresh_execution
from scripts.render_runtime_env import news_usage_environment

PROTOCOL = "monitor.routine-update.v1"
STAGES = ("freeze", "promote-reader", "update-job", "sync-roster", "refresh",
          "verify-v3", "restore-schedulers", "complete")
PROJECT = "lcs-developer-483404"
REGION = "us-central1"
SERVICE = "oura-navi-monitor"
JOB = "oura-navi-monitor-refresh"
DATASET = "oura_navi_monitor"
SCHEDULER = "oura-navi-monitor-refresh-three-hour"
LEGACY_SCHEDULER = "oura-navi-monitor-refresh-quarter-hour"
JOB_URI = f"https://{REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/{PROJECT}/jobs/{JOB}:run"
REFRESH_KEY = f"refresh-chain:{JOB}:{LEGACY_SCHEDULER}:{SCHEDULER}"
LEGACY_QUERY_SHA256 = "8fa7ca443652060121c26c566caeb585ea1166769cb2e3804aeb7da7a0751024"
LEGACY_TABLES = {f"{PROJECT}.{DATASET}.{name}" for name in (
    "monitor_answer_events", "monitor_user_daily", "monitor_system_hourly", "monitor_dashboard_snapshots")}
PERMISSIONS = (
    "run.services.get", "run.services.update", "run.jobs.get", "run.jobs.update",
    "run.jobs.run", "run.jobs.runWithOverrides", "run.executions.get", "run.executions.list",
    "cloudscheduler.jobs.get", "cloudscheduler.jobs.list", "cloudscheduler.jobs.pause",
    "cloudscheduler.jobs.enable", "bigquery.jobs.create", "bigquery.jobs.listAll",
    "bigquery.tables.getData", "datastore.entities.get", "datastore.entities.list",
    "datastore.entities.create", "datastore.entities.update", "datastore.entities.delete",
)


class Pending(ValueError):
    """A recorded stage needs a later readback, not a second blind mutation."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stamp(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":"), default=str).encode()).hexdigest()


def file_digest(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_operator_source(sha: str) -> None:
    """Local imports must implement the image's rules, not an unreviewed tree."""
    paths = ["app", "sql", "deploy/cloudrun.env.yaml", "scripts/import_monitor_users.py",
             "scripts/verify_monitor_data_contract.py", "scripts/render_runtime_env.py",
             "scripts/validate_refresh_job.py", "scripts/credential_preflight.py",
             "scripts/promotion_release_lock.py", "scripts/release_operation_lock.py",
             "scripts/promotion_receipt_state.py", "scripts/verify_candidate_service.py",
             "scripts/verify_service_access_contract.py", "scripts/scheduler_execution_provenance.py"]
    result = subprocess.run(["git", "-c", "core.fileMode=false", "diff", "--quiet", sha, "--", *paths],
                            cwd=ROOT, capture_output=True)
    extra = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "--", *paths],
                           cwd=ROOT, capture_output=True, text=True)
    if result.returncode or extra.returncode or extra.stdout.strip():
        raise ValueError("local runtime/import/validation source differs from the candidate Git SHA")


def load(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_new(path: Path, payload: Any) -> None:
    with path.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def save_state(path: Path, state: dict) -> None:
    # The sibling process lock protects local checkpoints; cloud locks protect
    # the actual resources across independent release entry points.
    state["updatedAt"] = now()
    temporary = path.with_name(path.name + ".next")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2, default=str)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def manifest_contract(value: dict) -> dict:
    required = {"protocol", "project", "projectNumber", "region", "service", "job",
                "gitSha", "buildId", "revision", "image", "webServiceAccount",
                "jobServiceAccount", "previousRevision", "schedulerAccounts",
                "legacyTransferResource", "workbook", "workbookSha256", "actor",
                "expectedRoster", "legacyDependencyEvidence", "legacyDependencyEvidenceSha256"}
    if set(value) != required:
        raise ValueError("manifest fields differ from the routine release contract")
    for key, expected in {"protocol": PROTOCOL, "project": PROJECT, "region": REGION,
                          "service": SERVICE, "job": JOB}.items():
        if value[key] != expected:
            raise ValueError(f"unsupported manifest {key}")
    if not re.fullmatch(r"[0-9a-f]{40}", value["gitSha"]):
        raise ValueError("full Git SHA required")
    if not re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", value["buildId"]):
        raise ValueError("exact Build UUID required")
    if value["revision"] != f"{SERVICE}-{value['gitSha'][:7]}-{value['buildId'][:8]}":
        raise ValueError("revision does not bind the Build and Git SHA")
    if not re.fullmatch(rf"{REGION}-docker\.pkg\.dev/{PROJECT}/cloud-run-source-deploy/{SERVICE}@sha256:[0-9a-f]{{64}}", value["image"]):
        raise ValueError("exact Monitor image digest required")
    if not re.fullmatch(r"[1-9][0-9]*", value["projectNumber"]):
        raise ValueError("project number required")
    if set(value["schedulerAccounts"]) != {SCHEDULER, LEGACY_SCHEDULER}:
        raise ValueError("both existing Scheduler identities must be explicit")
    for account in [value["webServiceAccount"], value["jobServiceAccount"], *value["schedulerAccounts"].values()]:
        if not re.fullmatch(rf"[^@\s]+@{PROJECT}\.iam\.gserviceaccount\.com", account):
            raise ValueError("explicit project service account required")
    if value["jobServiceAccount"] == value["schedulerAccounts"][SCHEDULER]:
        raise ValueError("writer and Scheduler invoker identities must differ")
    if not value["previousRevision"].startswith(SERVICE + "-") or value["previousRevision"] == value["revision"]:
        raise ValueError("explicit previous production revision required")
    if not re.fullmatch(rf"projects/({PROJECT}|{value['projectNumber']})/locations/us/transferConfigs/[^/\s]+", value["legacyTransferResource"]):
        raise ValueError("exact existing US legacy transfer resource required")
    workbook = Path(value["workbook"])
    if not workbook.is_absolute() or not workbook.is_file() or file_digest(workbook) != value["workbookSha256"]:
        raise ValueError("workbook bytes differ from the approved manifest")
    if set(value["expectedRoster"]) != {"total", "active", "inactive", "deleted", "global", "userMap"}:
        raise ValueError("complete expected roster counts required")
    if any(type(number) is not int or number < 0 for number in value["expectedRoster"].values()):
        raise ValueError("roster counts must be nonnegative integers")
    dependency = Path(value["legacyDependencyEvidence"])
    if not dependency.is_absolute() or not dependency.is_file() or file_digest(dependency) != value["legacyDependencyEvidenceSha256"]:
        raise ValueError("reviewed legacy dependency evidence is missing or changed")
    return value


def allocation(service: dict) -> list[dict]:
    require_reconciled_ready(service, label="Monitor service")
    return require_reconciled_traffic(traffic_planes(service, label="Monitor service"), label="Monitor service")


def service_config(service: dict) -> dict:
    spec = copy.deepcopy(service["spec"])
    spec.pop("traffic", None)
    return {"access": _access(service), "spec": spec}


def job_spec(job: dict, *, omit_image: bool = False) -> dict:
    spec = copy.deepcopy(job["spec"])
    def clean(value: Any) -> None:
        if isinstance(value, dict):
            if omit_image and "containers" in value:
                for container in value["containers"]:
                    container.pop("image", None)
            metadata = value.get("metadata")
            if isinstance(metadata, dict):
                for key in ("creationTimestamp", "generation", "resourceVersion", "uid"):
                    metadata.pop(key, None)
                annotations = metadata.get("annotations", {})
                for key in ("run.googleapis.com/client-name", "run.googleapis.com/client-version", "run.googleapis.com/operation-id"):
                    annotations.pop(key, None)
            for item in value.values():
                clean(item)
        elif isinstance(value, list):
            for item in value:
                clean(item)
    clean(spec)
    return spec


def job_image(job: dict) -> str:
    from scripts.validate_refresh_job import _container
    return str(_container(job)["image"])


def scheduler_contract(value: dict) -> dict:
    return {key: value.get(key) for key in ("name", "schedule", "timeZone", "attemptDeadline", "retryConfig", "httpTarget")}


def transfer_contract(value: dict) -> dict:
    return {key: value.get(key) for key in ("name", "disabled", "displayName", "dataSourceId", "destinationDatasetId", "schedule", "scheduleOptions", "scheduleOptionsV2", "serviceAccountName", "ownerInfo", "userId", "params")}


def validate_legacy_dependencies(manifest: dict, transfer: dict, definitions: dict) -> None:
    evidence = load(manifest["legacyDependencyEvidence"])
    closure = evidence.get("canonicalDependencyClosure", {})
    if (evidence.get("readOnly") is not True or evidence.get("transferResource") != manifest["legacyTransferResource"]
            or evidence.get("transferQuerySha256") != LEGACY_QUERY_SHA256
            or set(evidence.get("legacyWriteTargets", [])) != LEGACY_TABLES
            or not closure or set(closure) & LEGACY_TABLES
            or not set(evidence.get("canonicalRoots", [])).issubset(closure)
            or set(definitions) != set(closure)):
        raise ValueError("legacy/canonical dependency separation is not proven")
    query = transfer.get("params", {}).get("query", "")
    if hashlib.sha256(query.encode()).hexdigest() != LEGACY_QUERY_SHA256:
        raise ValueError("legacy transfer query differs from the reviewed four-table owner")
    for name, expected in closure.items():
        if expected.get("unresolvedQuotedReferences") or not set(expected.get("references", [])).issubset(closure):
            raise ValueError("legacy dependency evidence contains unresolved references")
        actual = definitions[name]
        if actual != {key: expected[key] for key in ("kind", "definitionSha256", "schemaFields")}:
            raise ValueError("live canonical dependency definition drift")


def validate_schedulers(rows: list[dict], manifest: dict) -> dict[str, dict]:
    selected = {}
    for row in rows:
        uri = str(row.get("httpTarget", {}).get("uri", ""))
        name = str(row.get("name", "")).rsplit("/", 1)[-1]
        if JOB in uri or name in {SCHEDULER, LEGACY_SCHEDULER}:
            if name not in manifest["schedulerAccounts"] or uri != JOB_URI:
                raise ValueError("unknown actual Scheduler target; routine update refused")
            if row.get("state") not in {"ENABLED", "PAUSED"}:
                raise ValueError("unexpected Scheduler state")
            if row.get("httpTarget", {}).get("oauthToken", {}).get("serviceAccountEmail") != manifest["schedulerAccounts"][name]:
                raise ValueError("Scheduler invoker identity drift")
            if name in selected:
                raise ValueError("duplicate Scheduler resource")
            selected[name] = row
    if set(selected) != {SCHEDULER, LEGACY_SCHEDULER}:
        raise ValueError("existing Scheduler inventory is incomplete")
    canonical = selected[SCHEDULER]
    if canonical.get("schedule") != "5 * * * *" or canonical.get("timeZone") != "Asia/Tokyo":
        raise ValueError("canonical Scheduler cadence drift")
    if str(canonical.get("attemptDeadline")) != "60s" or int(canonical.get("retryConfig", {}).get("retryCount", 0)) != 0:
        raise ValueError("canonical Scheduler deadline/retry drift")
    return selected


def validate_quiescence(inventory: dict) -> None:
    if any(row.get("state") != "PAUSED" for row in inventory["schedulers"].values()):
        raise Pending("all actual refresh triggers must be paused")
    for execution in inventory["executions"]:
        status = execution.get("status", execution)
        if not status.get("completionTime"):
            raise Pending("a Cloud Run execution is still in flight")
    if inventory["writers"]:
        raise Pending("BigQuery writes are still in flight")
    if set(inventory["pipeline"]) != {"published", "news_usage"}:
        raise ValueError("both Chat and News publication owners are required")
    for row in inventory["pipeline"].values():
        if bool(row.get("lease_run_id")) != bool(row.get("lease_expires_at")):
            raise ValueError("inconsistent publisher lease metadata")
        if row.get("lease_run_id") and (not row.get("lease_expires_at") or stamp(row["lease_expires_at"]) > datetime.now(timezone.utc)):
            raise Pending("Chat or News still owns an active publisher lease")


def validate_acceptance(receipt: dict, manifest: dict, inventory: dict, stage: str) -> None:
    expected = {"receiptType": "monitor.routine-acceptance.v1", "stage": stage,
                **{key: manifest[key] for key in ("project", "region", "service", "revision", "image", "gitSha", "buildId")}}
    if any(receipt.get(key) != value for key, value in expected.items()):
        raise ValueError("acceptance receipt belongs to a different exact candidate/stage")
    service_status = inventory["service"].get("status", {})
    origins = [row.get("url") for row in service_status.get("traffic", []) if row.get("tag") == "candidate"] if stage == "candidate-v2" else [service_status.get("url")]
    if len(origins) != 1 or not origins[0] or receipt.get("origin") != origins[0]:
        raise ValueError("acceptance origin is not the live candidate/production URL for this stage")
    age = datetime.now(timezone.utc) - stamp(receipt.get("capturedAt"))
    if age < -timedelta(minutes=1) or age > timedelta(minutes=60):
        raise ValueError("real acceptance receipt is stale or future-dated")
    for field in ("authenticatedApiAcceptance", "loggedInBrowserAcceptance", "readOnlyAcceptance"):
        if receipt.get(field) is not True:
            raise ValueError(f"real acceptance is missing {field}")
    if not str(receipt.get("verifiedBy", "")).strip():
        raise ValueError("acceptance operator is missing")
    identity = receipt.get("authenticatedIdentity", {})
    env = {row.get("name"): row.get("value") for row in inventory["revision"]["spec"]["containers"][0].get("env", [])}
    admins = {item.strip().lower() for item in str(env.get("MONITOR_ADMIN_ALLOWLIST", "")).split(",") if item.strip()}
    if (identity.get("source") != "iap" or identity.get("verified") is not True
            or str(identity.get("email", "")).lower() not in admins
            or str(identity.get("email", "")).endswith(".gserviceaccount.com")):
        raise ValueError("real authenticated IAP administrator identity is required")
    for kind in ("apiEvidence", "browserEvidence"):
        artifact = receipt.get(kind, {})
        source = Path(artifact.get("path", ""))
        if not source.is_absolute() or not source.is_file() or file_digest(source) != artifact.get("sha256"):
            raise ValueError(f"missing or changed real {kind}")
    statuses = receipt.get("endpointStatus", {})
    for key in ("overview", "regions", "users", "userDetail", "news", "newsCsv", "management"):
        if statuses.get(key) != 200:
            raise ValueError(f"real acceptance missing successful {key}")
    for source, key in (("published", "chatPublishedRunId"), ("news_usage", "newsPublishedRunId")):
        if receipt.get(key) != inventory["pipeline"][source].get("published_run_id"):
            raise ValueError("acceptance publication changed; capture a new read-only receipt")
    if stage in {"candidate-v2", "production-v2"} and inventory["pipeline"]["published"].get("scope_policy_version") not in {"summary_role_v1", "summary_department_v2"}:
        raise ValueError("candidate-first proof must read the existing supported publication")


class Cloud:
    """Explicit-credential adapter; commands are arrays, never shell strings."""
    def __init__(self, manifest: dict, credential_file: str, evidence_dir: Path):
        from google.oauth2 import service_account
        from google.cloud import bigquery, firestore
        self.m = manifest
        self.evidence_dir = evidence_dir
        self.credential = str(approved_credential_path(credential_file))
        self.credentials = service_account.Credentials.from_service_account_file(self.credential, scopes=["https://www.googleapis.com/auth/cloud-platform"])
        if manifest["actor"] != self.credentials.service_account_email:
            raise ValueError("sync actor must be the actual pinned credential identity")
        self.bq = bigquery.Client(project=PROJECT, credentials=self.credentials)
        self.fs = firestore.Client(project=PROJECT, database="lcs-user-data", credentials=self.credentials)
        self.env = {**os.environ, "CLOUDSDK_AUTH_CREDENTIAL_FILE_OVERRIDE": self.credential,
                    "GOOGLE_APPLICATION_CREDENTIALS": self.credential, "PYTHONDONTWRITEBYTECODE": "1"}

    def permission_preflight(self) -> dict:
        from google.auth.transport.requests import AuthorizedSession
        session = AuthorizedSession(self.credentials)
        checks = [(f"https://cloudresourcemanager.googleapis.com/v1/projects/{PROJECT}:testIamPermissions", list(PERMISSIONS)),
                  (f"https://iam.googleapis.com/v1/projects/-/serviceAccounts/{self.m['jobServiceAccount']}:testIamPermissions", ["iam.serviceAccounts.actAs"])]
        for url, required in checks:
            response = session.post(url, json={"permissions": required}, timeout=30)
            if response.status_code != 200 or set(required) - set(response.json().get("permissions", [])):
                raise ValueError("required routine release permissions missing; do not freeze writers")
        return {"checkedAt": now(), "principal": self.credentials.service_account_email, "permissions": list(PERMISSIONS) + ["iam.serviceAccounts.actAs"]}

    def command(self, args: list[str], *, timeout: int = 60) -> Any:
        try:
            result = subprocess.run(args, env=self.env, cwd=ROOT, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            artifact = self.command_failure(args, exc.stdout, exc.stderr, "timeout")
            raise ValueError(f"provider command timed out; inspect {artifact}") from None
        if result.returncode:
            artifact = self.command_failure(args, result.stdout, result.stderr, result.returncode)
            raise ValueError(f"provider command failed (exit {result.returncode}); inspect {artifact}")
        return json.loads(result.stdout) if result.stdout.strip() else {}

    def command_failure(self, args: list[str], stdout: Any, stderr: Any, outcome: Any) -> Path:
        def redact(value: Any) -> str:
            value = value.decode(errors="replace") if isinstance(value, bytes) else str(value or "")
            value = value.replace(self.credential, "[credential path]")
            value = re.sub(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", "[redacted private key]", value, flags=re.S)
            value = re.sub(r"(?i)(Bearer\s+|(?:access_token|id_token|refresh_token|private_key)\s*[=:]\s*[\"']?)[^\s\"',}]+", r"\1[redacted]", value)
            return re.sub(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b", "[redacted token]", value)
        target = self.evidence_dir / f"provider-failure-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.json"
        write_new(target, {"capturedAt": now(), "command": [redact(arg) for arg in args], "outcome": outcome, "stdout": redact(stdout), "stderr": redact(stderr)})
        return target

    def gcloud(self, *args: str, timeout: int = 60) -> Any:
        return self.command(["gcloud", f"--project={PROJECT}", *args, "--format=json", "--quiet"], timeout=timeout)

    def query(self, sql: str) -> list[dict]:
        from google.cloud import bigquery
        result = self.bq.query(sql, location="US", job_config=bigquery.QueryJobConfig(
            maximum_bytes_billed=1_073_741_824, use_query_cache=False)).result(timeout=60)
        return [dict(row.items()) for row in result]

    def inventory(self) -> dict:
        service = self.gcloud("run", "services", "describe", SERVICE, f"--region={REGION}")
        revision = self.gcloud("run", "revisions", "describe", self.m["revision"], f"--region={REGION}")
        schedulers = validate_schedulers(self.gcloud("scheduler", "jobs", "list", f"--location={REGION}"), self.m)
        pipeline = self.query(f"SELECT * FROM `{PROJECT}.{DATASET}.pipeline_state` WHERE source IN ('published','news_usage')")
        if len(pipeline) != 2:
            raise ValueError("publication owner rows missing or duplicated")
        transfer = self.command(["bq", f"--project_id={PROJECT}", "--location=US", "show", "--transfer_config", "--format=prettyjson", self.m["legacyTransferResource"]])
        definitions = self.dependency_definitions()
        validate_legacy_dependencies(self.m, transfer, definitions)
        return {
            "capturedAt": now(), "service": service, "revision": revision,
            "iam": self.gcloud("run", "services", "get-iam-policy", SERVICE, f"--region={REGION}"),
            "job": self.gcloud("run", "jobs", "describe", JOB, f"--region={REGION}"),
            "schedulers": schedulers,
            "executions": self.gcloud("run", "jobs", "executions", "list", f"--job={JOB}", f"--region={REGION}", "--limit=100"),
            "writers": self.query(f"SELECT job_id, state FROM `{PROJECT}.region-us.INFORMATION_SCHEMA.JOBS_BY_PROJECT` WHERE creation_time >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 2 DAY) AND state IN ('PENDING','RUNNING') AND (job_type IN ('LOAD','COPY') OR statement_type IS NULL OR statement_type != 'SELECT') AND ((destination_table.project_id = '{PROJECT}' AND destination_table.dataset_id = '{DATASET}') OR REGEXP_CONTAINS(IFNULL(query,''), r'({PROJECT}[.:])?{DATASET}[.`]'))"),
            "pipeline": {row["source"]: row for row in pipeline},
            "legacyTransfer": transfer, "dependencyDefinitions": definitions,
        }

    def dependency_definitions(self) -> dict:
        evidence = load(self.m["legacyDependencyEvidence"])
        definitions = {}
        for name, expected in evidence["canonicalDependencyClosure"].items():
            if expected["kind"] == "ROUTINE":
                obj = self.bq.get_routine(name)
                definition, kind, schema = obj.body or "", "ROUTINE", []
            else:
                obj = self.bq.get_table(name)
                definition, kind, schema = obj.view_query or "", obj.table_type, [field.name for field in obj.schema]
            definitions[name] = {"kind": kind, "definitionSha256": hashlib.sha256(definition.encode()).hexdigest() if definition else None, "schemaFields": schema}
        return definitions

    def directory(self):
        from app.repositories.user_directory import UserDirectoryRepository
        from app.settings import Settings
        return UserDirectoryRepository(Settings(_env_file=None, monitor_project_id=PROJECT, monitor_firestore_database="lcs-user-data"), client=self.fs)

    def sync_plan(self) -> tuple[Any, dict]:
        from scripts.import_monitor_users import load_roster_plan, _sync_diff
        plan = load_roster_plan(Path(self.m["workbook"]))
        diff = _sync_diff(plan, self.directory().list_users(include_inactive=True, include_deleted=True))
        return plan, diff["summary"]

    def sync(self, expected_digest: str) -> dict:
        from scripts.import_monitor_users import _apply_sync
        plan, _ = self.sync_plan()
        return _apply_sync(plan, directory=self.directory(), actor=self.m["actor"], expected_sync_digest=expected_digest)

    def roster_summary(self) -> dict:
        from app.domain.roster_records import read_canonical_roster_collection
        rows = self.directory().list_users(include_inactive=True, include_deleted=True)
        records = read_canonical_roster_collection(rows)
        if any(record.issues for record in records):
            raise ValueError("roster has unresolved canonical issues")
        current = [r for r in records if r.value.get("is_deleted") is not True]
        return {"total": len(current), "active": sum(r.value["is_active"] is True for r in current),
                "inactive": sum(r.value["is_active"] is False for r in current),
                "deleted": len(records) - len(current),
                "global": sum(r.evaluation.membership.global_enabled for r in current),
                "userMap": sum(r.evaluation.membership.user_map_enabled for r in current)}

    def roster_fingerprint(self) -> str:
        fields = ("roster_id", "name", "email", "area", "area_key", "workplace", "department", "role", "team", "mr_experience", "label_ids", "is_active", "is_deleted")
        rows = []
        for row in self.directory().list_users(include_inactive=True, include_deleted=True):
            value = {key: row.get(key) for key in fields}
            value["label_ids"] = sorted(value["label_ids"] or [])
            rows.append(value)
        return digest(sorted(rows, key=lambda row: row["roster_id"]))

    def validate_data(self) -> dict:
        from scripts.verify_monitor_data_contract import verify_data_contract
        return verify_data_contract(self.bq, project=PROJECT, dataset=DATASET, location="US",
                                    git_sha=self.m["gitSha"], image=self.m["image"], expected_news_environment=news_usage_environment())

    def publication_runs(self, start: str) -> list[dict]:
        bound = stamp(start).isoformat()
        return self.query(f"SELECT run_id, execution_id, trigger_source, source, status, started_at, finished_at FROM `{PROJECT}.{DATASET}.pipeline_runs` WHERE DATE(started_at) BETWEEN DATE(TIMESTAMP('{bound}')) AND CURRENT_DATE() AND started_at >= TIMESTAMP('{bound}') AND source IN ('published','news_usage') ORDER BY started_at DESC LIMIT 100")

    def logs(self, query: str) -> list[dict]:
        return self.gcloud("logging", "read", query, "--limit=100", "--order=asc")

    def execution_proof(self, name: str, start: str, *, natural: bool = False) -> dict:
        name = execution_short_name(name)
        start = stamp(start).isoformat()
        end = now()
        bounded = f'timestamp>="{start}" AND timestamp<="{end}"'
        proof = {
            "capturedAt": end,
            "execution": self.gcloud("run", "jobs", "executions", "describe", name, f"--region={REGION}"),
            "runs": self.publication_runs(start),
            "events": self.logs(f'{bounded} AND resource.type="cloud_run_job" AND resource.labels.project_id="{PROJECT}" AND resource.labels.location="{REGION}" AND resource.labels.job_name="{JOB}" AND labels."run.googleapis.com/execution_name"="{name}" AND jsonPayload.monitor_pipeline_event=true AND jsonPayload.status="succeeded"'),
        }
        if natural:
            proof["attempts"] = self.logs(f'{bounded} AND resource.type="cloud_scheduler_job" AND resource.labels.project_id="{PROJECT}" AND resource.labels.location_id="{REGION}" AND resource.labels.job_id="{SCHEDULER}" AND jsonPayload."@type"="type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished"')
            proof["audits"] = self.logs(f'{bounded} AND protoPayload.serviceName="run.googleapis.com" AND protoPayload.authenticationInfo.principalEmail="{self.m["schedulerAccounts"][SCHEDULER]}" AND (protoPayload.methodName="/Jobs.RunJob" OR protoPayload.methodName="google.cloud.run.v2.Jobs.RunJob")')
        return proof


def identities(state: dict) -> tuple[dict, dict]:
    m = state["manifest"]
    shared = {"project": PROJECT, "region": REGION, "intentPayloadSha256": state["intentSha256"],
              "staticContractSha256": state["manifestSha256"], "firestoreDatabase": "lcs-user-data",
              "releaseLockCollection": "monitor_release_locks"}
    return ({**shared, "service": SERVICE, "targetRevision": m["revision"]},
            {**shared, "resourceKey": REFRESH_KEY, "operationKind": "routine-update", "targetKey": m["revision"]})


def own_locks(cloud: Cloud, state: dict, *, acquire: bool = False, recover: bool = False, releasing: bool = False) -> None:
    service, refresh = identities(state)
    for identity, locator, acquire_fn, expected_fn, matches_fn in (
        (service, service_lock_document_id, acquire_release_lock, _expected_lock, _matches_exact_lock),
        (refresh, operation_lock_document_id, acquire_operation_lock, operation_expected, operation_matches),
    ):
        snapshot = cloud.fs.collection("monitor_release_locks").document(locator(identity)).get()
        if snapshot.exists:
            observed = snapshot.to_dict()
            if not matches_fn(observed, expected_fn(identity)):
                raise ValueError("release lock belongs to a different intent")
            if acquire and not recover:
                raise ValueError("release intent is already active; explicit recovery required")
        elif acquire:
            acquire_fn(cloud.fs, identity)
        elif not releasing:
            raise ValueError("required release lock is missing")


def check_invariant(state: dict, live: dict, *, reader_promoted: bool, new_job: bool) -> None:
    m, base = state["manifest"], state["baseline"]
    _validate_revision(live["revision"], project=PROJECT, region=REGION, service=SERVICE,
                       target_revision=m["revision"], image=m["image"], git_sha=m["gitSha"],
                       build_id=m["buildId"], service_account=m["webServiceAccount"])
    require_reconciled_ready(live["revision"], label="target revision")
    expected = [{"revisionName": m["revision"] if reader_promoted else m["previousRevision"], "percent": 100}]
    if allocation(live["service"]) != expected:
        raise ValueError("production allocation drift; refusing traffic mutation or rollback")
    require_exact_tagged_revision(traffic_planes(live["service"], label="Monitor service"), label="candidate", tag="candidate", revision=m["revision"], percent=100 if reader_promoted else 0)
    if service_config(live["service"]) != service_config(base["service"]) or _iam(live["iam"]) != _iam(base["iam"]):
        raise ValueError("service configuration or IAM drift")
    if job_spec(live["job"], omit_image=True) != job_spec(base["job"], omit_image=True):
        raise ValueError("refresh Job changed beyond its image")
    expected_image = m["image"] if new_job else job_image(base["job"])
    validate_refresh_job(live["job"], expected_image=expected_image,
                         expected_service_account=m["jobServiceAccount"], project_id=PROJECT,
                         dataset_id=DATASET, location="US", source_service="lcs-rag-app",
                         timeout_minutes=30, expected_news_environment=news_usage_environment())
    if transfer_contract(live["legacyTransfer"]) != transfer_contract(base["legacyTransfer"]):
        raise ValueError("legacy DTS changed during a routine release")
    validate_legacy_dependencies(m, live["legacyTransfer"], live["dependencyDefinitions"])
    for name, scheduler in live["schedulers"].items():
        if scheduler_contract(scheduler) != scheduler_contract(base["schedulers"][name]):
            raise ValueError("Scheduler configuration drift")


def confirmation(state: dict, stage: str) -> str:
    return f"{PROJECT}/{SERVICE}/{state['manifest']['revision']}:{stage}:{state['manifestSha256']}"


def prepare(manifest: dict, cloud: Cloud, directory: Path) -> dict:
    manifest_contract(manifest)
    verify_operator_source(manifest["gitSha"])
    build = cloud.gcloud("builds", "describe", manifest["buildId"], f"--region={REGION}")
    if build.get("status") != "SUCCESS" or build.get("substitutions", {}).get("COMMIT_SHA") != manifest["gitSha"]:
        raise ValueError("exact candidate Build has not succeeded")
    image_digest = manifest["image"].rsplit("@", 1)[1]
    source = build.get("sourceProvenance", {}).get("resolvedGitSource", {})
    if source.get("url") != "https://github.com/Aoki-311/oura_navi_monitor.git" or source.get("revision") != manifest["gitSha"]:
        raise ValueError("Build resolved source does not prove the exact repository and Git SHA")
    if not any(row.get("digest") == image_digest and str(row.get("name", "")).split(":", 1)[0] == manifest["image"].split("@", 1)[0] for row in build.get("results", {}).get("images", [])):
        raise ValueError("Build result does not prove the candidate image digest")
    live = cloud.inventory()
    verify_candidate(service=live["service"], revision=live["revision"], expected_project_id=PROJECT,
                     expected_project_number=manifest["projectNumber"], expected_region=REGION,
                     expected_service=SERVICE, expected_image=manifest["image"], expected_revision=manifest["revision"],
                     expected_service_account=manifest["webServiceAccount"], expected_git_sha=manifest["gitSha"],
                     candidate_tag="candidate", expected_news_environment=news_usage_environment())
    if live["schedulers"][SCHEDULER]["state"] != "ENABLED" or live["schedulers"][LEGACY_SCHEDULER]["state"] != "PAUSED":
        raise ValueError("routine release requires the established canonical Scheduler and paused legacy trigger")
    state = {"protocol": PROTOCOL, "manifest": manifest, "manifestSha256": digest(manifest),
             "baseline": live, "createdAt": now(), "completedStages": [], "pendingStage": None,
             "evidenceDir": str(directory.resolve()), "build": build,
             "controllerCodeSha256": file_digest(__file__)}
    state["intentSha256"] = digest({key: state[key] for key in ("protocol", "manifestSha256", "baseline", "createdAt")})
    check_invariant(state, live, reader_promoted=False, new_job=False)
    state["initialRoster"] = cloud.roster_summary()
    state["initialLabelsSha256"] = digest(cloud.directory().list_labels(include_inactive=True))
    state["permissionPreflight"] = cloud.permission_preflight()
    write_new(directory / "routine-state.json", state)
    return state


def run_stage(cloud: Cloud, state: dict, path: Path, args: argparse.Namespace) -> None:
    stage, m = args.stage, state["manifest"]
    done = state["completedStages"]
    if stage in done:
        raise ValueError("stage already complete; do not repeat its mutations")
    if stage != STAGES[len(done)]:
        raise ValueError("stage is out of order")
    if state.get("pendingStage") and (state["pendingStage"] != stage or not args.recover):
        raise ValueError("interrupted stage requires explicit --recover")
    if args.confirm != confirmation(state, stage) and args.apply:
        raise ValueError("exact stage confirmation required")
    manifest_contract(m)
    if state.get("controllerCodeSha256") != file_digest(__file__):
        raise ValueError("routine controller changed after planning")
    verify_operator_source(m["gitSha"])
    live = cloud.inventory()
    promoted = "promote-reader" in done
    new_job = "update-job" in done
    # Recovery recognises only the recorded stage's exact post-state. No old
    # image or old-reader restoration is offered after data work has begun.
    if args.recover and stage == "promote-reader" and allocation(live["service"]) == [{"revisionName": m["revision"], "percent": 100}]:
        promoted = True
    if args.recover and stage == "update-job" and job_image(live["job"]) == m["image"]:
        new_job = True
    check_invariant(state, live, reader_promoted=promoted, new_job=new_job)
    if stage != "freeze":
        own_locks(cloud, state, releasing=bool(stage == "complete" and args.recover and state.get("finalVerifiedAt")))
    if stage not in {"freeze", "complete", "restore-schedulers"}:
        validate_quiescence(live)
    if "sync-roster" in done:
        if (cloud.roster_summary() != m["expectedRoster"] or cloud.roster_fingerprint() != state.get("syncedRosterSha256")
                or digest(cloud.directory().list_labels(include_inactive=True)) != state["initialLabelsSha256"]):
            raise ValueError("roster metadata or labels changed after the approved sync")
    acceptance_stage = {"freeze": "candidate-v2", "promote-reader": "candidate-v2", "update-job": "production-v2", "verify-v3": "production-v3"}.get(stage)
    if acceptance_stage:
        if not args.acceptance_receipt:
            raise ValueError("this stage requires a fresh real API/browser acceptance receipt")
        validate_acceptance(load(args.acceptance_receipt), m, live, acceptance_stage)
    sync_summary = None
    if stage == "sync-roster":
        _, sync_summary = cloud.sync_plan()
        counts, scopes = sync_summary["resultingRosterCounts"], sync_summary["resultingScopeCounts"]
        proposed = {"total": counts["registered"], "active": counts["active"], "inactive": counts["inactive"], "deleted": counts["deleted"], "global": scopes["global"], "userMap": scopes["user_map"]}
        if proposed != m["expectedRoster"]:
            raise ValueError("proposed roster counts differ from the approved manifest; no sync writes made")
        if args.apply and args.expected_sync_digest != sync_summary["syncDigest"]:
            raise ValueError("sync plan changed; obtain and confirm the current digest")
    if not args.apply:
        print(json.dumps({"mode": "plan", "stage": stage, "requiredConfirmation": confirmation(state, stage), "syncPlan": sync_summary}, ensure_ascii=False))
        return
    if not state.get("pendingStage"):
        state["pendingStage"] = stage
        state["stageStartedAt"] = now()
        save_state(path, state)
    if args.acceptance_receipt:
        state.setdefault("acceptance", {})[acceptance_stage] = load(args.acceptance_receipt)
        save_state(path, state)
    if stage == "freeze":
        state["permissionPreflight"] = cloud.permission_preflight()
        save_state(path, state)
        own_locks(cloud, state, acquire=True, recover=args.recover)
        # Recheck after both locks; a prior operator must not slip through the
        # gap between the preflight and CAS acquisition.
        live = cloud.inventory()
        check_invariant(state, live, reader_promoted=False, new_job=False)
        for name, original in state["baseline"]["schedulers"].items():
            current = live["schedulers"][name]
            if original["state"] == "PAUSED" and current["state"] != "PAUSED":
                raise ValueError("an originally paused Scheduler was enabled")
            if original["state"] == "ENABLED" and current["state"] == "ENABLED":
                cloud.gcloud("scheduler", "jobs", "pause", name, f"--location={REGION}")
        live = cloud.inventory()
        validate_quiescence(live)
    elif stage == "promote-reader":
        if not promoted:
            cloud.gcloud("run", "services", "update-traffic", SERVICE, f"--region={REGION}", f"--to-revisions={m['revision']}=100")
        live = cloud.inventory()
        check_invariant(state, live, reader_promoted=True, new_job=False)
    elif stage == "update-job":
        if not new_job:
            cloud.gcloud("run", "jobs", "update", JOB, f"--region={REGION}", f"--image={m['image']}")
        live = cloud.inventory()
        check_invariant(state, live, reader_promoted=True, new_job=True)
    elif stage == "sync-roster":
        state["syncReceipt"] = cloud.sync(args.expected_sync_digest)
        if cloud.roster_summary() != m["expectedRoster"]:
            raise ValueError("live roster counts differ from the approved plan")
        if digest(cloud.directory().list_labels(include_inactive=True)) != state["initialLabelsSha256"]:
            raise ValueError("label catalog changed; this routine update does not modify labels")
        state["syncedRosterSha256"] = cloud.roster_fingerprint()
    elif stage == "refresh":
        if args.execution_name:
            if not args.recover or state.get("refreshExecution"):
                raise ValueError("execution selection is only for ambiguous submission recovery")
            state["refreshExecution"] = execution_short_name(args.execution_name)
            save_state(path, state)
        if not state.get("refreshExecution"):
            if args.recover:
                raise ValueError("submission outcome unknown; supply the exact verified --execution-name")
            execution = cloud.gcloud("run", "jobs", "execute", JOB, f"--region={REGION}",
                                     "--args=-m,app.jobs.refresh_analytics,--apply,--trigger-source,manual", "--async")
            state["refreshExecution"] = execution.get("metadata", {}).get("name") or execution.get("name")
            save_state(path, state)
            raise Pending("refresh submitted; resume this stage after execution readback")
        proof = cloud.execution_proof(state["refreshExecution"], state["stageStartedAt"])
        validate_publication_proof(proof, m, live, start=state["stageStartedAt"], trigger="manual", creator=m["actor"])
        state["refreshExecutionReadback"] = proof
        state["dataReceipt"] = cloud.validate_data()
    elif stage == "verify-v3":
        if cloud.roster_summary() != m["expectedRoster"]:
            raise ValueError("roster changed after sync")
        state["dataReceipt"] = cloud.validate_data()
        if live["pipeline"]["published"].get("scope_policy_version") != "summary_department_v3":
            raise ValueError("Chat v3 publication is missing")
        if live["pipeline"]["news_usage"].get("scope_policy_version") != "summary_department_v3":
            raise ValueError("News v3 publication is missing")
        if live["pipeline"]["news_usage"].get("roster_snapshot_run_id") != live["pipeline"]["published"].get("published_run_id"):
            raise ValueError("News is not bound to the current Chat v3 roster")
    elif stage == "restore-schedulers":
        if not state.get("restoredAt"):
            validate_quiescence(live)
        for name, original in state["baseline"]["schedulers"].items():
            if original["state"] == "PAUSED" and live["schedulers"][name]["state"] != "PAUSED":
                raise ValueError("an originally paused Scheduler was enabled")
        if cloud.roster_summary() != m["expectedRoster"]:
            raise ValueError("roster changed before Scheduler restoration")
        state["dataReceipt"] = cloud.validate_data()
        # Persist before the first resume; a timeout may mean it succeeded.
        if not state.get("restoredAt"):
            state["restoredAt"] = now()
            save_state(path, state)
        for name, original in state["baseline"]["schedulers"].items():
            if original["state"] == "ENABLED" and live["schedulers"][name]["state"] == "PAUSED":
                cloud.gcloud("scheduler", "jobs", "resume", name, f"--location={REGION}")
        live = cloud.inventory()
        for name, original in state["baseline"]["schedulers"].items():
            if live["schedulers"][name]["state"] != original["state"]:
                raise ValueError("Scheduler restoration readback failed")
    elif stage == "complete":
        if not args.natural_evidence:
            raise Pending("one natural scheduled success must be independently verified")
        # Implemented below using the existing Scheduler provenance owner.
        state["naturalProof"] = validate_natural(cloud, state, live, load(args.natural_evidence))
        state["dataReceipt"] = cloud.validate_data()
        if cloud.roster_summary() != m["expectedRoster"]:
            raise ValueError("final roster differs from the approved plan")
        for name, original in state["baseline"]["schedulers"].items():
            if live["schedulers"][name]["state"] != original["state"]:
                raise ValueError("final Scheduler state drift")
        state["finalVerifiedAt"] = now()
        save_state(path, state)
        service, refresh = identities(state)
        release_operation_lock(cloud.fs, refresh)
        release_release_lock(cloud.fs, service)
    state["completedStages"].append(stage)
    state["pendingStage"] = None
    state.pop("lastError", None)
    state.setdefault("stageReceipts", {})[stage] = {"completedAt": now(), "inventorySha256": digest(live)}
    save_state(path, state)
    write_new(path.parent / f"{len(state['completedStages']):02d}-{stage}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%f')}.json", live)
    print(json.dumps({"status": "complete", "stage": stage, "state": str(path)}))


def execution_short_name(value: str) -> str:
    # Only the immutable child of this exact Job can be selected for recovery.
    full = str(value)
    short = full.rsplit("/", 1)[-1]
    allowed = {short, f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}/executions/{short}", f"namespaces/{PROJECT}/executions/{short}"}
    if full not in allowed or not re.fullmatch(rf"{JOB}-[a-z0-9]+", short):
        raise ValueError("execution is not an exact child of the refresh Job")
    return short


def validate_publication_proof(proof: dict, manifest: dict, inventory: dict, *, start: str, trigger: str, creator: str) -> str:
    from scripts.scheduler_execution_provenance import _execution_creator
    from scripts.validate_refresh_job import _container
    execution = proof["execution"]
    metadata, status = execution.get("metadata", {}), execution.get("status", execution)
    name = execution_short_name(execution.get("name") or metadata.get("name", ""))
    if metadata.get("labels", {}).get("run.googleapis.com/job") != JOB or _execution_creator(execution) != creator:
        raise ValueError("execution job/creator identity does not match this stage")
    created = stamp(metadata.get("creationTimestamp") or execution.get("createTime"))
    if created < stamp(start) or created > stamp(proof["capturedAt"]):
        raise ValueError("execution is outside this release stage")
    if not status.get("completionTime"):
        raise Pending("refresh execution is not terminal")
    validate_refresh_execution(execution, expected_image=manifest["image"], expected_service_account=manifest["jobServiceAccount"], require_terminal_success=True)
    if _container(execution).get("args") != ["-m", "app.jobs.refresh_analytics", "--apply", "--trigger-source", trigger]:
        raise ValueError("execution arguments do not match the bounded refresh stage")
    chat, news = inventory["pipeline"]["published"], inventory["pipeline"]["news_usage"]
    if any(row.get("status") != "succeeded" or row.get("scope_policy_version") != "summary_department_v3" for row in (chat, news)):
        raise ValueError("both current publications must be successful v3 snapshots")
    if news.get("roster_snapshot_run_id") != chat.get("published_run_id"):
        raise ValueError("News publication is not bound to the current Chat roster")
    for source, pointer in (("published", chat), ("news_usage", news)):
        runs = [row for row in proof["runs"] if row.get("source") == source and row.get("run_id") == pointer.get("published_run_id")]
        if len(runs) != 1 or runs[0].get("status") != "succeeded" or runs[0].get("trigger_source") != trigger or stamp(runs[0]["started_at"]) < created:
            raise ValueError("current publication has no exact successful run in this execution window")
        # News's current runtime uses local-UUID. Its Cloud Run stdout binds
        # that run to the immutable execution; do not invent an execution ID.
        if source == "published" and runs[0].get("execution_id", "").rsplit("/", 1)[-1] != name:
            raise ValueError("Chat publication belongs to a different execution")
    events = []
    for event in proof["events"]:
        payload, resource = event.get("jsonPayload", {}), event.get("resource", {})
        labels = resource.get("labels", {})
        if (resource.get("type") == "cloud_run_job" and labels.get("project_id") == PROJECT
                and labels.get("location") == REGION and labels.get("job_name") == JOB
                and event.get("labels", {}).get("run.googleapis.com/execution_name") == name
                and payload.get("monitor_pipeline_event") is True and payload.get("status") == "succeeded"
                and payload.get("runId") == chat.get("published_run_id")
                and payload.get("newsUsage", {}).get("status") == "succeeded"
                and payload.get("newsUsage", {}).get("publishedRunId") == news.get("published_run_id")
                and payload.get("newsUsage", {}).get("rosterSnapshotRunId") == chat.get("published_run_id")
                and created <= stamp(event.get("timestamp")) <= stamp(proof["capturedAt"])
                and event.get("insertId")):
            events.append(event)
    if len(events) != 1:
        raise ValueError("one exact execution success log must bind Chat and News publications")
    return name


def validate_natural(cloud: Cloud, state: dict, inventory: dict, evidence: dict) -> dict:
    """Selection is local; all success/provenance evidence is read live here."""
    from scripts.scheduler_execution_provenance import _audit_execution_name, _audit_succeeded
    if set(evidence) != {"executionName"}:
        raise ValueError("natural evidence selects exactly one executionName; proof is fetched live")
    m = state["manifest"]
    proof = cloud.execution_proof(execution_short_name(evidence["executionName"]), state["restoredAt"], natural=True)
    name = validate_publication_proof(proof, m, inventory, start=state["restoredAt"], trigger="scheduler_hourly", creator=m["schedulerAccounts"][SCHEDULER])
    start, end = stamp(state["restoredAt"]), stamp(proof["capturedAt"])
    audits = []
    for entry in proof["audits"]:
        proto = entry.get("protoPayload", {})
        if (proto.get("serviceName") == "run.googleapis.com" and proto.get("methodName") in {"/Jobs.RunJob", "google.cloud.run.v2.Jobs.RunJob"}
                and proto.get("authenticationInfo", {}).get("principalEmail") == m["schedulerAccounts"][SCHEDULER]
                and proto.get("resourceName") in {f"projects/{PROJECT}/locations/{REGION}/jobs/{JOB}", f"namespaces/{PROJECT}/jobs/{JOB}"}
                and _audit_succeeded(proto) and start <= stamp(entry.get("timestamp")) <= end
                and _audit_execution_name(entry) == name):
            audits.append(entry)
    if len(audits) != 1:
        raise ValueError("natural run requires one exact successful Scheduler RunJob audit")
    audit_at = stamp(audits[0]["timestamp"])
    attempts = []
    for entry in proof["attempts"]:
        payload, resource = entry.get("jsonPayload", {}), entry.get("resource", {})
        labels, result = resource.get("labels", {}), payload.get("status")
        ok = result.get("code") == 0 if isinstance(result, dict) else str(result).upper() in {"OK", "SUCCESS"}
        if (resource.get("type") == "cloud_scheduler_job" and labels.get("project_id") == PROJECT
                and labels.get("location_id") == REGION and labels.get("job_id") == SCHEDULER
                and payload.get("@type") == "type.googleapis.com/google.cloud.scheduler.logging.AttemptFinished"
                and payload.get("jobName") == f"projects/{PROJECT}/locations/{REGION}/jobs/{SCHEDULER}"
                and (payload.get("url") or payload.get("targetUrl")) == JOB_URI and ok
                and entry.get("severity") in {"DEFAULT", "INFO", "NOTICE"} and entry.get("insertId")
                and start <= stamp(entry.get("timestamp")) <= end
                and abs(stamp(entry["timestamp"]) - audit_at) <= timedelta(minutes=2)):
            attempts.append(entry)
    if len(attempts) != 1:
        raise ValueError("natural run requires one unique successful Scheduler attempt in its RunJob window")
    return proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("--manifest", required=True, type=Path)
    plan.add_argument("--evidence-dir", required=True, type=Path)
    step = sub.add_parser("step")
    step.add_argument("--state", required=True, type=Path)
    step.add_argument("--stage", required=True, choices=STAGES)
    step.add_argument("--apply", action="store_true")
    step.add_argument("--confirm", default="")
    step.add_argument("--recover", action="store_true")
    step.add_argument("--acceptance-receipt", type=Path)
    step.add_argument("--expected-sync-digest", default="")
    step.add_argument("--execution-name", default="")
    step.add_argument("--natural-evidence", type=Path)
    for command in (plan, step):
        command.add_argument("--credential-file", required=True)
    args = parser.parse_args()
    if args.action == "plan":
        if not args.evidence_dir.is_dir():
            raise ValueError("use the already prepared external evidence directory")
        manifest = manifest_contract(load(args.manifest))
        state = prepare(manifest, Cloud(manifest, args.credential_file, args.evidence_dir), args.evidence_dir)
        print(json.dumps({"mode": "plan", "state": str(args.evidence_dir / 'routine-state.json'), "nextStage": "freeze", "requiredConfirmation": confirmation(state, "freeze")}))
        return 0
    path = args.state.resolve()
    with path.with_suffix(".process-lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = load(path)
        if state.get("protocol") != PROTOCOL or digest(state["manifest"]) != state.get("manifestSha256"):
            raise ValueError("routine state integrity failed")
        expected_intent = digest({key: state[key] for key in ("protocol", "manifestSha256", "baseline", "createdAt")})
        if state.get("intentSha256") != expected_intent:
            raise ValueError("routine baseline integrity failed")
        try:
            run_stage(Cloud(state["manifest"], args.credential_file, path.parent), state, path, args)
        except (ValueError, TimeoutError, subprocess.TimeoutExpired) as exc:
            if args.apply and state.get("pendingStage"):
                state["lastError"] = str(exc)
                save_state(path, state)
            raise
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Pending as exc:
        print(json.dumps({"status": "pending", "reason": str(exc)}))
        raise SystemExit(3)
    except (ValueError, OSError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "stopped", "reason": str(exc)}))
        raise SystemExit(2)
