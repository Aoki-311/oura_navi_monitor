#!/usr/bin/env python3
"""Firestore CAS owner for Monitor refresh-chain mutations."""

from __future__ import annotations

import argparse
import hashlib
import json
from typing import Any, Callable

from google.cloud import firestore
from google.oauth2 import service_account

try:
    from scripts.credential_preflight import approved_credential_path
except ModuleNotFoundError:  # Direct execution from scripts/.
    from credential_preflight import approved_credential_path


LOCK_CONTRACT_VERSION = "monitor.release-operation-lock.v1"
LOCK_DATABASE = "lcs-user-data"
LOCK_COLLECTION = "monitor_release_locks"
_FIELDS = (
    "contractVersion",
    "project",
    "region",
    "resourceKey",
    "operationKind",
    "targetKey",
    "intentPayloadSha256",
    "staticContractSha256",
    "firestoreDatabase",
    "releaseLockCollection",
)


class OperationLockConflict(ValueError):
    """Another exact intent owns the refresh-chain lock."""


def operation_lock_document_id(identity: dict[str, str]) -> str:
    owner = "|".join(
        (identity["project"], identity["region"], identity["resourceKey"])
    )
    return hashlib.sha256(owner.encode("utf-8")).hexdigest()


def _expected(identity: dict[str, str]) -> dict[str, str]:
    result = {key: identity[key] for key in _FIELDS if key != "contractVersion"}
    result["contractVersion"] = LOCK_CONTRACT_VERSION
    if result["firestoreDatabase"] != LOCK_DATABASE:
        raise ValueError("release lock database is not the governed named database")
    if result["releaseLockCollection"] != LOCK_COLLECTION:
        raise ValueError("release lock collection is not the governed collection")
    return result


def _matches(observed: Any, expected: dict[str, str]) -> bool:
    if not isinstance(observed, dict):
        return False
    if set(observed) - (set(_FIELDS) | {"acquiredAt"}):
        return False
    return all(observed.get(key) == value for key, value in expected.items())


def _snapshot(transaction: Any, document: Any) -> Any:
    snapshots = iter(transaction.get(document))
    snapshot = next(snapshots, None)
    if snapshot is None or next(snapshots, None) is not None:
        raise RuntimeError("Firestore transaction returned an invalid snapshot set")
    return snapshot


def _transaction(client: Any, callback: Callable[[Any], str]) -> str:
    transaction = client.transaction()

    @firestore.transactional
    def run(active_transaction: Any) -> str:
        return callback(active_transaction)

    return run(transaction)


def acquire_operation_lock(client: Any, identity: dict[str, str]) -> str:
    expected = _expected(identity)
    document = client.collection(LOCK_COLLECTION).document(
        operation_lock_document_id(identity)
    )

    def mutate(transaction: Any) -> str:
        snapshot = _snapshot(transaction, document)
        if not snapshot.exists:
            transaction.create(
                document, expected | {"acquiredAt": firestore.SERVER_TIMESTAMP}
            )
            return "acquired"
        if _matches(snapshot.to_dict(), expected):
            return "recovered"
        raise OperationLockConflict(
            "refresh-chain lock is owned by another local receipt or intent"
        )

    return _transaction(client, mutate)


def release_operation_lock(client: Any, identity: dict[str, str]) -> str:
    expected = _expected(identity)
    document = client.collection(LOCK_COLLECTION).document(
        operation_lock_document_id(identity)
    )

    def mutate(transaction: Any) -> str:
        snapshot = _snapshot(transaction, document)
        if not snapshot.exists:
            return "already_released"
        if not _matches(snapshot.to_dict(), expected):
            raise OperationLockConflict(
                "refresh-chain lock cannot be released by another intent"
            )
        transaction.delete(document)
        return "released"

    return _transaction(client, mutate)


def _identity(args: argparse.Namespace) -> dict[str, str]:
    return {
        "project": args.project,
        "region": args.region,
        "resourceKey": args.resource_key,
        "operationKind": args.operation_kind,
        "targetKey": args.target_key,
        "intentPayloadSha256": args.intent_payload_sha256,
        "staticContractSha256": args.static_contract_sha256,
        "firestoreDatabase": args.firestore_database,
        "releaseLockCollection": args.release_lock_collection,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Acquire or release a Monitor operation lock")
    parser.add_argument("action", choices=("acquire", "release"))
    parser.add_argument("--project", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--resource-key", required=True)
    parser.add_argument("--operation-kind", required=True)
    parser.add_argument("--target-key", required=True)
    parser.add_argument("--intent-payload-sha256", required=True)
    parser.add_argument("--static-contract-sha256", required=True)
    parser.add_argument("--firestore-database", default=LOCK_DATABASE)
    parser.add_argument("--release-lock-collection", default=LOCK_COLLECTION)
    parser.add_argument("--credential-file", required=True)
    args = parser.parse_args()
    try:
        credentials = service_account.Credentials.from_service_account_file(
            str(approved_credential_path(args.credential_file))
        )
        identity = _identity(args)
        client = firestore.Client(
            project=identity["project"],
            database=identity["firestoreDatabase"],
            credentials=credentials,
        )
        result = (
            acquire_operation_lock(client, identity)
            if args.action == "acquire"
            else release_operation_lock(client, identity)
        )
    except OperationLockConflict as exc:
        raise SystemExit("release_operation_lock_conflict: " + str(exc)) from None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise SystemExit("release_operation_lock_invalid: contract rejected") from None
    except Exception:
        raise SystemExit("release_operation_lock_error: provider operation failed") from None
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
