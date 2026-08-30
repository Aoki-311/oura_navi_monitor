#!/usr/bin/env python3
"""Firestore transaction owner for one Monitor service promotion lock."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from typing import Any, Callable

from google.cloud import firestore
from google.oauth2 import service_account

try:
    from scripts.credential_preflight import approved_credential_path
    from scripts.promotion_receipt_state import (
        read_release_lock_contract,
    )
except ModuleNotFoundError:  # Direct execution from scripts/.
    from credential_preflight import approved_credential_path
    from promotion_receipt_state import read_release_lock_contract


LOCK_CONTRACT_VERSION = "monitor.promotion-lock.v1"
LOCK_TOMBSTONE_STATE = "retired"
INTENT_DISPOSITIONS = frozenset(("aborted_pre", "authorized_post_recovery"))
_LOCK_IDENTITY_FIELDS = (
    "contractVersion",
    "project",
    "region",
    "service",
    "targetRevision",
    "intentPayloadSha256",
    "staticContractSha256",
    "firestoreDatabase",
    "releaseLockCollection",
)


class ReleaseLockConflict(ValueError):
    """Another exact promotion intent owns the service lock."""


def service_lock_document_id(identity: dict[str, str]) -> str:
    owner = "|".join(
        (identity["project"], identity["region"], identity["service"])
    )
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _expected_lock(identity: dict[str, str]) -> dict[str, str]:
    return {
        "contractVersion": LOCK_CONTRACT_VERSION,
        "project": identity["project"],
        "region": identity["region"],
        "service": identity["service"],
        "targetRevision": identity["targetRevision"],
        "intentPayloadSha256": identity["intentPayloadSha256"],
        "staticContractSha256": identity["staticContractSha256"],
        "firestoreDatabase": identity["firestoreDatabase"],
        "releaseLockCollection": identity["releaseLockCollection"],
    }


def _matches_exact_lock(observed: Any, expected: dict[str, str]) -> bool:
    if not isinstance(observed, dict):
        return False
    allowed = set(_LOCK_IDENTITY_FIELDS) | {"acquiredAt"}
    if set(observed) - allowed:
        return False
    return (
        "acquiredAt" in observed
        and observed.get("acquiredAt") is not None
        and all(observed.get(key) == value for key, value in expected.items())
    )


def _validated_tombstone_disposition(
    observed: Any,
    *,
    expected_owner: dict[str, str],
) -> str | None:
    if not isinstance(observed, dict) or observed.get("lockState") != LOCK_TOMBSTONE_STATE:
        return None
    allowed = set(_LOCK_IDENTITY_FIELDS) | {"lockState", "disposition", "releasedAt"}
    if set(observed) - allowed:
        return None
    if "releasedAt" not in observed or observed.get("releasedAt") is None:
        return None
    for key in (
        "contractVersion",
        "project",
        "region",
        "service",
        "firestoreDatabase",
        "releaseLockCollection",
    ):
        if observed.get(key) != expected_owner.get(key):
            return None
    if not isinstance(observed.get("targetRevision"), str) or not observed.get(
        "targetRevision"
    ):
        return None
    if not all(
        isinstance(observed.get(key), str)
        and re.fullmatch(r"[0-9a-f]{64}", observed[key])
        for key in ("intentPayloadSha256", "staticContractSha256")
    ):
        return None
    disposition = observed.get("disposition")
    return disposition if disposition in INTENT_DISPOSITIONS else None


def _tombstone_matches_identity(
    observed: dict[str, Any], expected: dict[str, str]
) -> bool:
    return all(observed.get(key) == value for key, value in expected.items())


def _retired_lock(expected: dict[str, str], disposition: str) -> dict[str, Any]:
    if disposition not in INTENT_DISPOSITIONS:
        raise ValueError("promotion lock disposition is invalid")
    return expected | {
        "lockState": LOCK_TOMBSTONE_STATE,
        "disposition": disposition,
        "releasedAt": firestore.SERVER_TIMESTAMP,
    }


def _run_transaction(client: Any, callback: Callable[[Any], str]) -> str:
    transaction = client.transaction()

    @firestore.transactional
    def run(active_transaction: Any) -> str:
        return callback(active_transaction)

    return run(transaction)


def _transaction_snapshot(transaction: Any, document: Any) -> Any:
    snapshots = iter(transaction.get(document))
    snapshot = next(snapshots, None)
    if snapshot is None:
        raise RuntimeError("Firestore transaction returned no document snapshot")
    if next(snapshots, None) is not None:
        raise RuntimeError("Firestore transaction returned duplicate document snapshots")
    return snapshot


def acquire_release_lock(
    client: Any,
    identity: dict[str, str],
    *,
    allow_final_recovery: bool = False,
    allow_post_recovery: bool = False,
) -> str:
    document = client.collection(identity["releaseLockCollection"]).document(
        service_lock_document_id(identity)
    )
    expected = _expected_lock(identity)

    def mutate(transaction: Any) -> str:
        snapshot = _transaction_snapshot(transaction, document)
        if not snapshot.exists:
            if allow_post_recovery:
                raise ReleaseLockConflict(
                    "post recovery requires an authorized intent tombstone"
                )
            transaction.create(document, expected | {"acquiredAt": firestore.SERVER_TIMESTAMP})
            return "acquired"
        if _matches_exact_lock(snapshot.to_dict(), expected):
            if allow_final_recovery:
                return "recovered"
            raise ReleaseLockConflict(
                "promotion service lock is already active for this exact intent"
            )
        observed = snapshot.to_dict()
        disposition = _validated_tombstone_disposition(
            observed,
            expected_owner=expected,
        )
        if disposition == "aborted_pre":
            if _tombstone_matches_identity(observed, expected):
                raise ReleaseLockConflict(
                    "promotion intent was retired and cannot be acquired again"
                )
            transaction.set(
                document,
                expected | {"acquiredAt": firestore.SERVER_TIMESTAMP},
            )
            return "acquired_after_retired_intent"
        if (
            disposition == "authorized_post_recovery"
            and allow_post_recovery
            and _tombstone_matches_identity(observed, expected)
        ):
            transaction.set(
                document,
                expected | {"acquiredAt": firestore.SERVER_TIMESTAMP},
            )
            return "recovered_post_intent"
        raise ReleaseLockConflict("promotion service lock is owned by another intent")

    return _run_transaction(client, mutate)


def release_release_lock(client: Any, identity: dict[str, str]) -> str:
    document = client.collection(identity["releaseLockCollection"]).document(
        service_lock_document_id(identity)
    )
    expected = _expected_lock(identity)

    def mutate(transaction: Any) -> str:
        snapshot = _transaction_snapshot(transaction, document)
        if not snapshot.exists:
            return "already_released"
        if not _matches_exact_lock(snapshot.to_dict(), expected):
            raise ReleaseLockConflict(
                "promotion service lock cannot be released by another intent"
            )
        transaction.delete(document)
        return "released"

    return _run_transaction(client, mutate)


def retire_release_lock(
    client: Any,
    identity: dict[str, str],
    *,
    disposition: str,
) -> str:
    document = client.collection(identity["releaseLockCollection"]).document(
        service_lock_document_id(identity)
    )
    expected = _expected_lock(identity)
    retired = _retired_lock(expected, disposition)

    def mutate(transaction: Any) -> str:
        snapshot = _transaction_snapshot(transaction, document)
        if not snapshot.exists:
            raise ReleaseLockConflict(
                "promotion intent lock is missing and cannot be retired"
            )
        observed = snapshot.to_dict()
        if _matches_exact_lock(observed, expected):
            transaction.set(document, retired)
            return "retired_" + disposition
        existing_disposition = _validated_tombstone_disposition(
            observed,
            expected_owner=expected,
        )
        if (
            existing_disposition == disposition
            and _tombstone_matches_identity(observed, expected)
        ):
            return "already_retired_" + disposition
        raise ReleaseLockConflict(
            "promotion intent lock cannot be retired by another intent or disposition"
        )

    return _run_transaction(client, mutate)


def intent_release_confirmation(identity: dict[str, str], disposition: str) -> str:
    if disposition not in INTENT_DISPOSITIONS:
        raise ValueError("promotion lock disposition is invalid")
    return ":".join(
        (
            "release-intent-lock",
            identity["project"],
            identity["region"],
            identity["service"],
            identity["targetRevision"],
            identity["intentPayloadSha256"],
            disposition,
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire or release a Monitor promotion lock")
    parser.add_argument("action", choices=("acquire", "release"))
    parser.add_argument("--promotion-state", required=True)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--allow-final-recovery", action="store_true")
    parser.add_argument("--allow-post-recovery", action="store_true")
    parser.add_argument("--allow-intent-release", action="store_true")
    parser.add_argument("--intent-disposition", choices=sorted(INTENT_DISPOSITIONS))
    parser.add_argument("--confirm-intent-release", default="")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        identity, final_state = read_release_lock_contract(args.promotion_state)
        if args.allow_final_recovery and args.allow_post_recovery:
            raise ValueError("only one recovery mode may be selected")
        if args.allow_final_recovery and args.action != "acquire":
            raise ValueError("final recovery applies only to lock acquisition")
        if args.allow_final_recovery and not final_state:
            raise ValueError("final recovery requires a completed promotion receipt")
        if args.allow_post_recovery and (args.action != "acquire" or final_state):
            raise ValueError("post recovery requires an intent lock acquisition")
        if (args.allow_intent_release or args.confirm_intent_release or args.apply) and (
            args.action != "release"
        ):
            raise ValueError("intent release controls apply only to lock release")
        if args.intent_disposition and args.action != "release":
            raise ValueError("intent disposition applies only to lock release")
        if args.action == "release" and not final_state:
            if not args.intent_disposition:
                raise ValueError("intent release requires an audited disposition")
            required_confirmation = intent_release_confirmation(
                identity,
                args.intent_disposition,
            )
            if not args.apply:
                print("mode=plan")
                print("intent_disposition=" + args.intent_disposition)
                print("required_confirmation=" + required_confirmation)
                print("mutation=none")
                return 0
            if not args.allow_intent_release:
                raise ValueError("intent release requires explicit authorization")
            if args.confirm_intent_release != required_confirmation:
                raise ValueError("intent release confirmation mismatch")
        credentials = service_account.Credentials.from_service_account_file(
            str(approved_credential_path(args.credential_file))
        )
        client = firestore.Client(
            project=identity["project"],
            database=identity["firestoreDatabase"],
            credentials=credentials,
        )
        if args.action == "acquire":
            result = acquire_release_lock(
                client,
                identity,
                allow_final_recovery=args.allow_final_recovery,
                allow_post_recovery=args.allow_post_recovery,
            )
        elif final_state:
            result = release_release_lock(client, identity)
        else:
            result = retire_release_lock(
                client,
                identity,
                disposition=args.intent_disposition,
            )
    except ReleaseLockConflict as exc:
        raise SystemExit("promotion_release_lock_conflict: " + str(exc)) from None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SystemExit("promotion_release_lock_invalid: release contract rejected") from None
    except Exception:
        # Provider diagnostics can contain local credential paths. Keep the public
        # failure stable while preserving the durable lock for an exact retry.
        raise SystemExit("promotion_release_lock_error: provider operation failed") from None
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
