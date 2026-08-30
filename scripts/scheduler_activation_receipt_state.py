#!/usr/bin/env python3
"""Crash-safe intent/final receipt owner for Monitor Scheduler activation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


INTENT_TYPE = "monitor_scheduler_activation_intent_v1"
FINAL_TYPE = "monitor_scheduler_activation_v2"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha_path(path: str) -> str:
    return _sha_bytes(Path(path).read_bytes())


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: Any, *, label: str) -> None:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} is invalid") from exc
    if parsed.tzinfo is None:
        raise ValueError(f"{label} has no timezone")


def _environment_object(name: str, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(os.environ[name])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} is invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    return value


def _read_payload(path: Path) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("scheduler activation output is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("scheduler activation output is not a JSON object")
    return payload, raw


def _scheduler_contract(payload: Any, *, label: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} Scheduler readback is not an object")
    retry = payload.get("retryConfig") or {}
    target = payload.get("httpTarget") or {}
    oauth = target.get("oauthToken") or {}
    try:
        retry_count = int(retry.get("retryCount") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} Scheduler retry count is invalid") from exc
    contract = {
        "schedule": payload.get("schedule"),
        "timeZone": payload.get("timeZone"),
        "attemptDeadline": payload.get("attemptDeadline"),
        "retryCount": retry_count,
        "uri": target.get("uri"),
        "serviceAccountEmail": oauth.get("serviceAccountEmail"),
    }
    if any(value in (None, "") for value in contract.values()):
        raise ValueError(f"{label} Scheduler contract is incomplete")
    return contract


def _scheduler_state(payload: Any, *, label: str) -> str:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} Scheduler readback is not an object")
    state = str(payload.get("state") or "")
    if state not in {"PAUSED", "ENABLED"}:
        raise ValueError(f"{label} Scheduler state is invalid")
    return state


def _payload_hash(payload: dict[str, Any]) -> str:
    unsigned = dict(payload)
    unsigned.pop("state_payload_sha256", None)
    return _sha_bytes(_canonical(unsigned).encode("utf-8"))


def _validate_integrity(payload: dict[str, Any]) -> None:
    observed = str(payload.get("state_payload_sha256") or "")
    if not _SHA256_RE.fullmatch(observed) or observed != _payload_hash(payload):
        raise ValueError("scheduler activation state integrity mismatch")


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _encode(payload: dict[str, Any]) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def _write_new_intent(path: Path, payload: dict[str, Any]) -> bool:
    payload["state_payload_sha256"] = _payload_hash(payload)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.intent-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_encode(payload))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            # link() is the exclusive publication CAS: a competing exact state
            # can win, but a crash can never expose a partially written intent.
            os.link(temporary, path)
        except FileExistsError:
            return False
        _fsync_directory(path.parent)
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _atomic_finalize(
    path: Path, *, expected_raw: bytes, payload: dict[str, Any]
) -> None:
    payload["state_payload_sha256"] = _payload_hash(payload)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.final-",
        suffix=".tmp",
        dir=str(path.parent),
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(_encode(payload))
            handle.flush()
            os.fsync(handle.fileno())
        if path.read_bytes() != expected_raw:
            raise ValueError("scheduler activation intent changed during finalization")
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _expected_static(args: argparse.Namespace) -> dict[str, Any]:
    job_contract = _environment_object(
        "CURRENT_JOB_CONTRACT", label="current refresh Job contract"
    )
    return {
        "project": args.project,
        "region": args.region,
        "dataset": args.dataset,
        "location": args.location,
        "source_service": args.source_service,
        "job": args.job,
        "old_scheduler": args.old_scheduler,
        "new_scheduler": args.new_scheduler,
        "expected_job_service_account": args.expected_job_service_account,
        "expected_old_scheduler_service_account": (
            args.expected_old_scheduler_service_account
        ),
        "expected_new_scheduler_service_account": (
            args.expected_new_scheduler_service_account
        ),
        "image": args.image,
        "freeze_snapshot_sha256": _sha_path(args.snapshot),
        "backfill_receipt_sha256": _sha_path(args.backfill_receipt),
        "validated_job_contract": job_contract,
    }


def _validate_static(
    payload: dict[str, Any], *, expected: dict[str, Any]
) -> None:
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(
                "scheduler activation state does not match the exact cutover: " + key
            )


def _validate_recorded_pre_state(payload: dict[str, Any]) -> None:
    old_before = payload.get("old_scheduler_before")
    new_before = payload.get("new_scheduler_before")
    if (
        _scheduler_state(old_before, label="recorded old") != "PAUSED"
        or _scheduler_state(new_before, label="recorded new") != "PAUSED"
    ):
        raise ValueError("scheduler activation intent has no PAUSED/PAUSED pre-state")
    if payload.get("old_scheduler_contract") != _scheduler_contract(
        old_before, label="recorded old"
    ):
        raise ValueError("recorded old Scheduler contract is inconsistent")
    if payload.get("new_scheduler_contract") != _scheduler_contract(
        new_before, label="recorded new"
    ):
        raise ValueError("recorded new Scheduler contract is inconsistent")
    gate = payload.get("pipeline_gate_at_intent")
    if not isinstance(gate, list) or len(gate) != 1:
        raise ValueError("scheduler activation intent has no exact pipeline gate")
    _parse_timestamp(payload.get("captured_at"), label="activation intent timestamp")
    _parse_timestamp(
        payload.get("canonical_start_at"), label="activation canonical start"
    )


def _live_context() -> tuple[
    dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]
]:
    old = _environment_object(
        "CURRENT_OLD_SCHEDULER_JSON", label="current old Scheduler readback"
    )
    new = _environment_object(
        "CURRENT_NEW_SCHEDULER_JSON", label="current new Scheduler readback"
    )
    job = _environment_object("CURRENT_JOB_JSON", label="current refresh Job readback")
    job_contract = _environment_object(
        "CURRENT_JOB_CONTRACT", label="current refresh Job contract"
    )
    return old, new, job, job_contract


def _classify_existing(
    payload: dict[str, Any],
    *,
    expected: dict[str, Any],
    old: dict[str, Any],
    new: dict[str, Any],
) -> str:
    receipt_type = payload.get("receipt_type")
    if receipt_type not in {INTENT_TYPE, FINAL_TYPE}:
        raise ValueError("scheduler activation output has an unsupported receipt type")
    _validate_integrity(payload)
    _validate_static(payload, expected=expected)
    _validate_recorded_pre_state(payload)
    if _scheduler_contract(old, label="current old") != payload.get(
        "old_scheduler_contract"
    ):
        raise ValueError("old Scheduler contract drifted from the activation intent")
    if _scheduler_contract(new, label="current new") != payload.get(
        "new_scheduler_contract"
    ):
        raise ValueError("new Scheduler contract drifted from the activation intent")
    if _scheduler_state(old, label="current old") != "PAUSED":
        raise ValueError("old Scheduler no longer matches the activation intent")

    new_state = _scheduler_state(new, label="current new")
    if receipt_type == INTENT_TYPE:
        if payload.get("state") != "intent":
            raise ValueError("scheduler activation intent has an invalid state")
        return "pre" if new_state == "PAUSED" else "post"

    if payload.get("state") != "complete" or new_state != "ENABLED":
        raise ValueError("completed scheduler activation is not live-exact")
    _parse_timestamp(payload.get("completed_at"), label="activation completion timestamp")
    if not _SHA256_RE.fullmatch(str(payload.get("intent_sha256") or "")):
        raise ValueError("completed scheduler activation has no intent provenance")
    old_after = payload.get("old_scheduler_readback")
    new_after = payload.get("new_scheduler_readback")
    if (
        _scheduler_state(old_after, label="recorded final old") != "PAUSED"
        or _scheduler_state(new_after, label="recorded final new") != "ENABLED"
        or _scheduler_contract(old_after, label="recorded final old")
        != payload.get("old_scheduler_contract")
        or _scheduler_contract(new_after, label="recorded final new")
        != payload.get("new_scheduler_contract")
    ):
        raise ValueError("completed scheduler activation readback is not exact")
    return "final"


def _result(payload: dict[str, Any], *, state: str, created: bool) -> str:
    return json.dumps(
        {
            "state": state,
            "created": created,
            "canonical_start_at": payload["canonical_start_at"],
        },
        sort_keys=True,
    )


def prepare(args: argparse.Namespace) -> str:
    path = Path(args.path)
    expected = _expected_static(args)
    old, new, job, job_contract = _live_context()
    if job_contract != expected["validated_job_contract"]:
        raise ValueError("current refresh Job contract changed during preparation")
    if path.exists():
        payload, _ = _read_payload(path)
        return _result(
            payload,
            state=_classify_existing(
                payload, expected=expected, old=old, new=new
            ),
            created=False,
        )

    old_state = _scheduler_state(old, label="current old")
    new_state = _scheduler_state(new, label="current new")
    if new_state == "ENABLED":
        raise ValueError("new Scheduler is ENABLED without an activation intent")
    if old_state != "PAUSED" or new_state != "PAUSED":
        raise ValueError("new activation intent requires PAUSED/PAUSED live state")
    try:
        pipeline_gate = json.loads(os.environ["ACTIVATION_PIPELINE_GATE_JSON"])
    except (KeyError, json.JSONDecodeError) as exc:
        raise ValueError("activation pipeline gate is missing or invalid") from exc
    if not isinstance(pipeline_gate, list) or len(pipeline_gate) != 1:
        raise ValueError("activation pipeline gate must contain exactly one row")

    started_at = _utc_now()
    payload = {
        "receipt_type": INTENT_TYPE,
        "state": "intent",
        **expected,
        "canonical_start_at": started_at,
        "captured_at": started_at,
        "job_readback_at_intent": job,
        "old_scheduler_before": old,
        "new_scheduler_before": new,
        "old_scheduler_contract": _scheduler_contract(old, label="current old"),
        "new_scheduler_contract": _scheduler_contract(new, label="current new"),
        "pipeline_gate_at_intent": pipeline_gate,
    }
    if _write_new_intent(path, payload):
        return _result(payload, state="pre", created=True)

    # Another process won exclusive publication. Only its exact state may continue.
    published, _ = _read_payload(path)
    return _result(
        published,
        state=_classify_existing(
            published, expected=expected, old=old, new=new
        ),
        created=False,
    )


def finalize(args: argparse.Namespace) -> str:
    path = Path(args.path)
    payload, raw = _read_payload(path)
    expected = _expected_static(args)
    old, new, job, _ = _live_context()
    state = _classify_existing(payload, expected=expected, old=old, new=new)
    if state == "final":
        return _result(payload, state="final", created=False)
    if state != "post":
        raise ValueError("only a live post-resume activation intent can be finalized")

    final = dict(payload)
    final.update(
        {
            "receipt_type": FINAL_TYPE,
            "state": "complete",
            "intent_sha256": _sha_bytes(raw),
            # Preserve the lock owner across the intent -> final hash change.
            "lock_intent_payload_sha256": payload["state_payload_sha256"],
            "completed_at": _utc_now(),
            "resume_command_return_code": args.resume_command_return_code,
            "recovered_after_interruption": (
                args.resume_command_return_code is None
            ),
            "job_readback": job,
            "old_scheduler_readback": old,
            "new_scheduler_readback": new,
        }
    )
    _atomic_finalize(path, expected_raw=raw, payload=final)
    return _result(final, state="final", created=False)


def _common_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--path", required=True)
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--backfill-receipt", required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--location", required=True)
    parser.add_argument("--source-service", required=True)
    parser.add_argument("--job", required=True)
    parser.add_argument("--old-scheduler", required=True)
    parser.add_argument("--new-scheduler", required=True)
    parser.add_argument("--expected-job-service-account", required=True)
    parser.add_argument("--expected-old-scheduler-service-account", required=True)
    parser.add_argument("--expected-new-scheduler-service-account", required=True)
    parser.add_argument("--image", required=True)
    return parser


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    prepare_parser = subparsers.add_parser("prepare", parents=[_common_parser()])
    prepare_parser.set_defaults(handler=prepare)
    finalize_parser = subparsers.add_parser("finalize", parents=[_common_parser()])
    finalize_parser.add_argument("--resume-command-return-code", type=int)
    finalize_parser.set_defaults(handler=finalize)
    args = parser.parse_args()
    try:
        result = args.handler(args)
    except (KeyError, OSError, TypeError, ValueError) as exc:
        raise SystemExit("scheduler_activation_state_invalid: " + str(exc)) from exc
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
