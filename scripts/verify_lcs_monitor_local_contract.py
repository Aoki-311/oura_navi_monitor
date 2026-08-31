#!/usr/bin/env python3
"""Verify the real LCS producer and Monitor consumer contract without cloud access."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
import re
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType
from typing import Any


MONITOR_ROOT = Path(__file__).resolve().parents[1]
EXPECTED_BATCH_BLOCKERS = {
    "run_manifest_accounting_mismatch",
    "duplicate_question_event_id",
    "duplicate_answer_event_id",
    "duplicate_answer_request_id",
}


def _read(path: Path) -> str:
    if not path.is_file():
        raise ValueError(f"required contract file is missing: {path}")
    return path.read_text(encoding="utf-8")


def _yaml_scalar(text: str, key: str) -> str:
    match = re.search(
        rf"^[ \t]*{re.escape(key)}:[ \t]*([^#\r\n]+)",
        text,
        flags=re.MULTILINE,
    )
    if match is None:
        raise ValueError(f"required YAML field is missing: {key}")
    return match.group(1).strip().strip("'\"")


def _load_lcs_telemetry(lcs_root: Path) -> ModuleType:
    module_path = (
        lcs_root
        / "backend"
        / "src"
        / "lcs_core"
        / "governance"
        / "monitor_telemetry.py"
    )
    if not module_path.is_file():
        raise ValueError(f"LCS telemetry owner is missing: {module_path}")
    spec = importlib.util.spec_from_file_location(
        "lcs_monitor_telemetry_local_contract",
        module_path,
    )
    if spec is None or spec.loader is None:
        raise ValueError("LCS telemetry owner could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _answer_payload() -> dict[str, Any]:
    demand = {
        "demand_id": "demand-1",
        "analytics_contract_version": "request_spec_analytics_v2",
        "question_category": "unclassified",
        "analytics_task": "unclassified",
        "classification_reason_codes": [],
        "product_names": [],
        "product_keys": [],
        "product_resolution_status": "not_applicable",
        "product_resolution_reason_codes": [],
        "requirement": "required",
        "delivery_state": "delivered",
        "evidence_state": "supported",
        "system_fault": False,
        "reason_codes": [],
    }
    return {
        "runtime_status": "completed",
        "terminal": "final",
        "failure_stage": "",
        "failure_code": "",
        "total_latency_ms": 100,
        "stage_latency_ms": {"answer": 100},
        "primary_question_category": "unclassified",
        "question_categories": ["unclassified"],
        "classification_status": "unclassified",
        "analytics_contract_version": "request_spec_analytics_v2",
        "classification_reason_codes": [],
        "is_multi_intent": False,
        "analytics_tasks": ["unclassified"],
        "primary_product_key": "",
        "primary_product_name": "",
        "product_keys": [],
        "product_names": [],
        "product_candidate_count": 0,
        "product_resolved_count": 0,
        "product_resolution_status": "not_applicable",
        "product_resolution_reason_codes": [],
        "demand_total": 1,
        "delivered_demand_count": 1,
        "partial_demand_count": 0,
        "omitted_demand_count": 0,
        "system_fault_count": 0,
        "demands": [demand],
        "citation_count": 1,
        "supported_claim_count": 1,
        "unsupported_claim_count": 0,
        "writer_error_code": "",
        "retry_count": 0,
    }


def _emit_real_events(module: ModuleType, *, project_id: str) -> list[dict[str, Any]]:
    original = {
        key: os.environ.get(key)
        for key in ("K_REVISION", "APP_GIT_SHA", "APP_BUILD_ID", "GOOGLE_CLOUD_PROJECT")
    }
    os.environ.update(
        {
            "K_REVISION": "lcs-rag-app-local-contract",
            "APP_GIT_SHA": "0123456789abcdef",
            "APP_BUILD_ID": "local-contract",
            "GOOGLE_CLOUD_PROJECT": project_id,
        }
    )
    common = {
        "user_id": "contract-user",
        "trace_id": "trace-1",
        "request_id": "request-1",
        "conversation_id": "conversation-1",
        "turn_id": "turn-1",
        "message_id": "message-1",
        "mode": "internal",
        "device_class": "desktop",
        "endpoint_class": "ask",
        "event_ts": "2026-08-31T00:00:00Z",
        "cloud_trace_id": "1" * 32,
        "cloud_span_id": "2" * 16,
    }
    payloads = {
        "question_received": {"valid_question": True, "attachment_count": 0},
        "answer_completed": _answer_payload(),
        "message_persisted": {
            "persisted": True,
            "role": "assistant",
            "error_code": "",
            "assistant_error_present": False,
            "answer_ts": "2026-08-31T00:00:00Z",
        },
        "answer_action": {
            "action": "feedback_submitted",
            "feedback": "good",
            "target_message_id": "message-1",
            "request_mode": "internal",
            "client_origin": "typed",
        },
    }
    emitted: list[dict[str, Any]] = []
    try:
        for family, payload in payloads.items():
            stream = io.StringIO()
            with redirect_stdout(stream):
                module.emit_monitor_event(
                    event_family=family,
                    event_id=f"{family}:local-contract",
                    payload=payload,
                    **common,
                )
            lines = [line for line in stream.getvalue().splitlines() if line.strip()]
            if len(lines) != 1:
                raise ValueError(f"{family} did not emit exactly one JSON line")
            event = json.loads(lines[0])
            if event.get("event_family") != family:
                raise ValueError(f"{family} event family changed during serialization")
            if event.get("monitor_contract_version") != "monitor.v2":
                raise ValueError(f"{family} is not monitor.v2")
            if event.get("request_id") != common["request_id"]:
                raise ValueError(f"{family} lost request correlation")
            emitted.append(event)
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    return emitted


def _disposition_checks(sql: str, disposition: str) -> set[str]:
    resolved: set[str] = set()
    for clause, value in re.findall(
        r"WHEN\s+check_name\s+IN\s*\((.*?)\)\s+THEN\s+'([^']+)'",
        sql,
        flags=re.IGNORECASE | re.DOTALL,
    ):
        if value.lower() == disposition.lower():
            resolved.update(re.findall(r"'([^']+)'", clause))
    return resolved


def verify(lcs_root: Path) -> dict[str, Any]:
    lcs_root = lcs_root.expanduser().resolve()
    lcs_build = _read(lcs_root / "cloudbuild.app.yaml")
    lcs_env = _read(lcs_root / "backend" / "deploy" / "cloudrun.env.yaml")
    monitor_build = _read(MONITOR_ROOT / "cloudbuild.yaml")
    monitor_env = _read(MONITOR_ROOT / "deploy" / "cloudrun.env.yaml")
    merge_sql = _read(MONITOR_ROOT / "sql" / "merge_incremental.sql")
    quality_sql = _read(MONITOR_ROOT / "sql" / "check_data_quality.sql")

    compute_project = _yaml_scalar(lcs_build, "_COMPUTE_PROJECT_ID")
    search_project = _yaml_scalar(lcs_build, "_SEARCH_RESOURCE_PROJECT_ID")
    lcs_service = _yaml_scalar(lcs_build, "_SERVICE_NAME")
    firestore_database = _yaml_scalar(lcs_build, "_FIRESTORE_DATABASE_ID")
    firestore_collection = _yaml_scalar(lcs_build, "_FIRESTORE_CHAT_COLLECTION")

    expected_pairs = {
        "runtime project": (
            compute_project,
            _yaml_scalar(monitor_env, "MONITOR_PROJECT_ID"),
        ),
        "source service": (
            lcs_service,
            _yaml_scalar(monitor_env, "MONITOR_SOURCE_SERVICE"),
        ),
        "Firestore database": (
            firestore_database,
            _yaml_scalar(monitor_env, "MONITOR_FIRESTORE_DATABASE"),
        ),
        "Firestore collection": (
            firestore_collection,
            _yaml_scalar(monitor_env, "MONITOR_FIRESTORE_CHAT_COLLECTION"),
        ),
    }
    for name, (producer, consumer) in expected_pairs.items():
        if producer != consumer:
            raise ValueError(f"{name} differs between LCS and Monitor")
    if _yaml_scalar(lcs_env, "VERTEX_CLIENT_PROJECT_ID") != compute_project:
        raise ValueError("LCS runtime compute project differs from Cloud Build")
    if _yaml_scalar(lcs_env, "SEARCH_RESOURCE_PROJECT_ID") != search_project:
        raise ValueError("LCS search resource project differs from Cloud Build")
    if _yaml_scalar(lcs_env, "FIRESTORE_PROJECT_ID") != compute_project:
        raise ValueError("LCS Firestore project differs from runtime project")
    if search_project == compute_project:
        raise ValueError("search resource project must remain separate from runtime project")
    if not _yaml_scalar(monitor_env, "MONITOR_ANALYTICS_START_AT"):
        raise ValueError("Monitor analytics start is missing")
    if "--no-traffic" not in lcs_build or "--no-traffic" not in monitor_build:
        raise ValueError("both existing Cloud Builds must remain candidate-only")

    telemetry = _load_lcs_telemetry(lcs_root)
    events = _emit_real_events(telemetry, project_id=compute_project)
    families = {event["event_family"] for event in events}
    for family in families:
        if f"'{family}'" not in merge_sql:
            raise ValueError(f"Monitor merge does not recognize {family}")
    for json_path in (
        "$.valid_question",
        "$.terminal",
        "$.runtime_status",
        "$.analytics_contract_version",
        "$.persisted",
        "$.answer_ts",
        "$.action",
        "$.feedback",
    ):
        if json_path not in merge_sql:
            raise ValueError(f"Monitor merge does not read {json_path}")

    if _disposition_checks(quality_sql, "batch_blocking") != EXPECTED_BATCH_BLOCKERS:
        raise ValueError("Monitor batch blockers exceed structural fact integrity")
    axis_checks = _disposition_checks(quality_sql, "axis_unmeasured")
    if not {
        "current_final_without_persistence_measurement",
        "invalid_classification_producer",
        "invalid_task_producer",
        "invalid_product_producer",
    } <= axis_checks:
        raise ValueError("Monitor does not preserve legal unmeasured axes")
    coverage_checks = _disposition_checks(quality_sql, "coverage")
    if not {
        "question_without_terminal",
        "unexpected_monitor_v2_revision_after_enforcement",
    } <= coverage_checks:
        raise ValueError("Monitor still treats lifecycle or revision gaps as global blockers")

    return {
        "status": "verified",
        "monitorContractVersion": "monitor.v2",
        "eventFamilies": sorted(families),
        "runtimeProject": compute_project,
        "searchResourceProject": search_project,
        "sourceService": lcs_service,
        "firestoreDatabase": firestore_database,
        "firestoreCollection": firestore_collection,
        "candidateOnlyBuilds": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--lcs-repo", required=True, type=Path)
    args = parser.parse_args()
    try:
        receipt = verify(args.lcs_repo)
    except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
        print(f"contract=failed reason={type(exc).__name__}:{exc}")
        return 1
    print(json.dumps(receipt, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
