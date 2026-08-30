#!/usr/bin/env python3
"""Validate and classify the crash-safe legacy DTS pause receipt state."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


INTENT_TYPE = "monitor_legacy_dts_pause_intent_v1"
FINAL_TYPE = "monitor_legacy_dts_pause_v2"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha_path(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _load_object(path: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _parse_time(value: Any, *, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} has no timezone")
    return parsed.astimezone(timezone.utc)


def _stable_transfer(
    value: Any, *, transfer_config: str
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("DTS transfer readback is not an object")
    if value.get("name") != transfer_config:
        raise ValueError("DTS transfer readback has another resource identity")
    if not isinstance(value.get("disabled"), bool):
        raise ValueError("DTS transfer readback has no exact disabled state")
    keys = (
        "name",
        "displayName",
        "dataSourceId",
        "destinationDatasetId",
        "schedule",
        "serviceAccountName",
        "ownerInfo",
        "userId",
        "params",
    )
    return {key: value.get(key) for key in keys}


def _validate_integrity(state: dict[str, Any]) -> None:
    observed = str(state.get("intent_payload_sha256") or "")
    unsigned = dict(state)
    unsigned.pop("intent_payload_sha256", None)
    expected = hashlib.sha256(_canonical(unsigned).encode("utf-8")).hexdigest()
    if observed != expected:
        raise ValueError("DTS pause state integrity mismatch")


def _validate_preflight_at_intent(
    *, state: dict[str, Any], preflight: dict[str, Any]
) -> None:
    captured_at = _parse_time(
        preflight.get("captured_at"), label="preflight receipt timestamp"
    )
    intent_at = _parse_time(state.get("captured_at"), label="DTS pause intent timestamp")
    if captured_at > intent_at + timedelta(minutes=1):
        raise ValueError("preflight receipt postdates the DTS pause intent")
    if intent_at - captured_at > timedelta(minutes=60):
        raise ValueError("preflight receipt was already stale when intent was created")


def _validate_final_tables(state: dict[str, Any]) -> None:
    before_rows = state.get("legacy_tables_before")
    after_rows = state.get("legacy_tables_after")
    if not isinstance(before_rows, list) or not isinstance(after_rows, list):
        raise ValueError("completed DTS pause has no table inventories")
    try:
        before = {str(item["table"]): item for item in before_rows}
        after = {str(item["table"]): item for item in after_rows}
    except (KeyError, TypeError) as exc:
        raise ValueError("completed DTS pause table inventory is invalid") from exc
    if before.keys() != after.keys() or not before:
        raise ValueError("completed DTS pause table inventory changed")
    for table in before:
        for field in ("lastModifiedTime", "numRows"):
            if before[table].get(field) != after[table].get(field):
                raise ValueError(f"legacy table {table} changed during DTS pause")


def classify(args: argparse.Namespace) -> str:
    state = _load_object(args.path, label="DTS pause state")
    receipt_type = state.get("receipt_type")
    if receipt_type not in {INTENT_TYPE, FINAL_TYPE}:
        raise ValueError("DTS pause state has an unsupported receipt type")
    _validate_integrity(state)

    dependency = _load_object(args.dependency_receipt, label="dependency receipt")
    activation = _load_object(args.activation_receipt, label="activation receipt")
    preflight = _load_object(args.preflight_receipt, label="preflight receipt")
    expected = {
        "project": args.project,
        "dataset": args.dataset,
        "location": args.location,
        "region": args.region,
        "transfer_config_resource": args.transfer_config,
        "canonical_start_at": args.canonical_start_at,
        "expected_query_sha256": args.expected_query_sha256,
        "expected_dts_service_account": args.expected_dts_service_account,
        "expected_scheduler_service_account": args.expected_scheduler_service_account,
        "activation_image": args.activation_image,
        "activation_job_service_account": args.activation_job_service_account,
        "dependency_receipt_sha256": _sha_path(args.dependency_receipt),
        "activation_receipt_sha256": _sha_path(args.activation_receipt),
        "preflight_receipt_sha256": _sha_path(args.preflight_receipt),
    }
    if any(state.get(key) != value for key, value in expected.items()):
        raise ValueError("DTS pause state does not match this exact retirement chain")
    if state.get("dependency_receipt") != dependency:
        raise ValueError("DTS pause state embeds another dependency receipt")
    if state.get("activation_receipt") != activation:
        raise ValueError("DTS pause state embeds another activation receipt")
    if state.get("preflight_receipt") != preflight:
        raise ValueError("DTS pause state embeds another preflight receipt")
    _validate_preflight_at_intent(state=state, preflight=preflight)

    before = state.get("transfer_config_before")
    before_contract = _stable_transfer(
        before, transfer_config=args.transfer_config
    )
    if before.get("disabled") is not False:
        raise ValueError("DTS pause intent does not preserve the enabled pre-state")
    try:
        current = json.loads(os.environ["CURRENT_TRANSFER_JSON"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError("current DTS transfer readback is invalid") from exc
    if _stable_transfer(
        current, transfer_config=args.transfer_config
    ) != before_contract:
        raise ValueError("DTS transfer contract drifted from the pause intent")

    if receipt_type == FINAL_TYPE:
        after = state.get("transfer_config_after")
        if (
            state.get("state") != "complete"
            or not state.get("paused_at")
            or not isinstance(after, dict)
            or after.get("disabled") is not True
            or _stable_transfer(
                after, transfer_config=args.transfer_config
            ) != before_contract
            or current.get("disabled") is not True
        ):
            raise ValueError("completed DTS pause receipt is not exact or live-disabled")
        _parse_time(state["paused_at"], label="DTS pause completion timestamp")
        after_runs = state.get("legacy_transfer_runs_after")
        if not isinstance(after_runs, list) or any(
            isinstance(item, dict)
            and str(item.get("state") or "").upper() in {"PENDING", "RUNNING"}
            for item in after_runs
        ):
            raise ValueError("completed DTS pause contains an in-flight transfer run")
        _validate_final_tables(state)
        return "final"
    if state.get("state") != "intent":
        raise ValueError("DTS pause intent has an invalid state")
    if current.get("disabled") is False:
        return "pre"
    if current.get("disabled") is True:
        return "post"
    raise ValueError("DTS pause live state is invalid")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--transfer-config", required=True)
    parser.add_argument("--canonical-start-at", required=True)
    parser.add_argument("--expected-query-sha256", required=True)
    parser.add_argument("--expected-dts-service-account", required=True)
    parser.add_argument("--expected-scheduler-service-account", required=True)
    parser.add_argument("--activation-image", required=True)
    parser.add_argument("--activation-job-service-account", required=True)
    parser.add_argument("--dependency-receipt", required=True)
    parser.add_argument("--activation-receipt", required=True)
    parser.add_argument("--preflight-receipt", required=True)
    args = parser.parse_args()
    try:
        result = classify(args)
    except (OSError, TypeError, ValueError) as exc:
        raise SystemExit("dts_pause_state_invalid: " + str(exc)) from exc
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
