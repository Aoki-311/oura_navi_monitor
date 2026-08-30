#!/usr/bin/env python3
"""Register one LCS monitor.v2 revision from an exact candidate log sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery
from google.oauth2 import service_account

try:
    from scripts.credential_preflight import approved_credential_path
except ModuleNotFoundError:
    from credential_preflight import approved_credential_path


_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,1023}$")
_REVISION_RE = re.compile(r"^[a-z][a-z0-9-]{0,61}[a-z0-9]$")
_SPAN_RE = re.compile(r"^[0-9a-f]{16}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z$")
_ENDPOINT_CLASSES = frozenset({"ask", "ask_stream"})
_MAX_SAMPLE_WINDOW = timedelta(hours=2)
_REGISTRATION_SOURCE = "candidate_v2_exact_http_question_sample"
_RECEIPT_TYPE = "monitor_v2_revision_registration_v1"


def _parse_utc(value: str, *, field: str) -> datetime:
    text = str(value or "").strip()
    if not _UTC_RE.fullmatch(text):
        raise ValueError(f"{field} must be an exact UTC timestamp ending in Z")
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return parsed.astimezone(timezone.utc)


def _proof_hash(*, revision: str, trace: str, span: str, endpoint_class: str) -> str:
    value = "|".join((revision, trace, span, endpoint_class))
    # BigQuery TO_HEX returns lowercase hexadecimal text.
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _required_confirmation(*, project: str, dataset: str, revision: str, proof_hash: str) -> str:
    return (
        f"projects/{project}/datasets/{dataset}/monitor_contract_revision_ledger:"
        f"register-v2:{revision}:{proof_hash}"
    )


def _validate_contract(
    *,
    project: str,
    dataset: str,
    revision: str,
    trace: str,
    span: str,
    endpoint_class: str,
    window_start: datetime,
    window_end: datetime,
) -> None:
    if not _PROJECT_RE.fullmatch(project):
        raise ValueError("project is not a valid Google Cloud project id")
    if not _DATASET_RE.fullmatch(dataset):
        raise ValueError("dataset is not a valid BigQuery dataset id")
    if not _REVISION_RE.fullmatch(revision):
        raise ValueError("revision is not a valid Cloud Run revision name")
    expected_trace = re.compile(
        rf"^projects/{re.escape(project)}/traces/[0-9a-f]{{32}}$"
    )
    if not expected_trace.fullmatch(trace):
        raise ValueError("trace must be the exact full Cloud Trace resource name")
    if not _SPAN_RE.fullmatch(span):
        raise ValueError("span must be exactly 16 lowercase hexadecimal characters")
    if endpoint_class not in _ENDPOINT_CLASSES:
        raise ValueError("candidate registration requires ask or ask_stream")
    if window_end <= window_start:
        raise ValueError("sample window end must be later than its start")
    if window_end - window_start > _MAX_SAMPLE_WINDOW:
        raise ValueError("candidate sample window must not exceed two hours")


def render_registration_sql(*, project: str, dataset: str, apply: bool) -> str:
    """Return one bounded proof query; apply additionally performs the sole MERGE."""

    event_source = f"`{project}.{dataset}.monitor_event_source`"
    http_source = f"`{project}.{dataset}.http_request_source`"
    ledger = f"`{project}.{dataset}.monitor_contract_revision_ledger`"
    declarations = f"""
DECLARE exact_http_count INT64 DEFAULT (
  SELECT COUNT(DISTINCT COALESCE(
    NULLIF(insert_id, ''),
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(source_ts, method, request_url, status))))
  ))
  FROM {http_source}
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND revision_name = @revision
    AND method = 'POST'
    AND status BETWEEN 200 AND 299
    AND cloud_trace = @cloud_trace
    AND cloud_span_id = @cloud_span_id
    AND endpoint_class = @endpoint_class
);
DECLARE exact_question_count INT64 DEFAULT (
  SELECT COUNT(DISTINCT event_id)
  FROM {event_source}
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND revision_name = @revision
    AND monitor_contract_version = 'monitor.v2'
    AND event_family = 'question_received'
    AND NULLIF(event_id, '') IS NOT NULL
    AND cloud_trace = @cloud_trace
    AND cloud_span_id = @cloud_span_id
    AND endpoint_class = @endpoint_class
);
DECLARE tuple_http_count INT64 DEFAULT (
  SELECT COUNT(DISTINCT COALESCE(
    NULLIF(insert_id, ''),
    TO_HEX(SHA256(TO_JSON_STRING(STRUCT(source_ts, method, request_url, status))))
  ))
  FROM {http_source}
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND revision_name = @revision
    AND method = 'POST'
    AND status IS NOT NULL
    AND cloud_trace = @cloud_trace
    AND cloud_span_id = @cloud_span_id
    AND endpoint_class IN ('ask', 'ask_stream', 'debug_ask', 'debug_ask_stream')
);
DECLARE tuple_question_count INT64 DEFAULT (
  SELECT COUNT(DISTINCT event_id)
  FROM {event_source}
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND revision_name = @revision
    AND monitor_contract_version = 'monitor.v2'
    AND event_family = 'question_received'
    AND NULLIF(event_id, '') IS NOT NULL
    AND cloud_trace = @cloud_trace
    AND cloud_span_id = @cloud_span_id
);
DECLARE sample_source_ts TIMESTAMP DEFAULT (
  SELECT MIN(source_ts)
  FROM {http_source}
  WHERE source_ts >= @window_start AND source_ts < @window_end
    AND revision_name = @revision
    AND method = 'POST'
    AND status BETWEEN 200 AND 299
    AND cloud_trace = @cloud_trace
    AND cloud_span_id = @cloud_span_id
    AND endpoint_class = @endpoint_class
);
DECLARE proof_correlation_hash STRING DEFAULT TO_HEX(SHA256(CONCAT(
  @revision, '|', @cloud_trace, '|', @cloud_span_id, '|', @endpoint_class
)));
""".strip()
    assertions = """
ASSERT exact_http_count = 1
  AS 'candidate proof requires exactly one accepted automatic HTTP request log';
ASSERT exact_question_count = 1
  AS 'candidate proof requires exactly one monitor.v2 question event';
ASSERT tuple_http_count = 1
  AS 'candidate HTTP revision+trace+span tuple is duplicated or route-ambiguous';
ASSERT tuple_question_count = 1
  AS 'candidate event revision+trace+span tuple is duplicated or route-ambiguous';
""".strip()
    result_select = f"""
SELECT
  @revision AS revision_name,
  'monitor.v2' AS monitor_contract_version,
  @endpoint_class AS sample_endpoint_class,
  sample_source_ts,
  proof_correlation_hash AS sample_correlation_hash,
  exact_http_count,
  exact_question_count,
  tuple_http_count,
  tuple_question_count,
  {'TRUE' if apply else 'FALSE'} AS registered,
  '{_REGISTRATION_SOURCE}' AS registration_source;
""".strip()
    if not apply:
        return "\n".join((declarations, assertions, result_select))
    mutation = f"""
BEGIN TRANSACTION;
ASSERT NOT EXISTS (
  SELECT 1
  FROM {ledger}
  WHERE revision_name = @revision
    AND (
      monitor_contract_version != 'monitor.v2'
      OR sample_endpoint_class != @endpoint_class
      OR sample_cloud_trace != @cloud_trace
      OR sample_cloud_span_id != @cloud_span_id
      OR sample_correlation_hash != proof_correlation_hash
    )
) AS 'revision already has a conflicting monitor contract registration';
MERGE {ledger} target
USING (
  SELECT
    @revision AS revision_name,
    'monitor.v2' AS monitor_contract_version,
    sample_source_ts,
    @endpoint_class AS sample_endpoint_class,
    @cloud_trace AS sample_cloud_trace,
    @cloud_span_id AS sample_cloud_span_id,
    proof_correlation_hash AS sample_correlation_hash,
    '{_REGISTRATION_SOURCE}' AS registration_source
) source
ON target.revision_name = source.revision_name
WHEN NOT MATCHED THEN INSERT (
  revision_name, monitor_contract_version, sample_source_ts,
  sample_endpoint_class, sample_cloud_trace, sample_cloud_span_id,
  sample_correlation_hash, registration_source,
  registered_at, updated_at
) VALUES (
  source.revision_name, source.monitor_contract_version, source.sample_source_ts,
  source.sample_endpoint_class, source.sample_cloud_trace, source.sample_cloud_span_id,
  source.sample_correlation_hash,
  source.registration_source, CURRENT_TIMESTAMP(), CURRENT_TIMESTAMP()
);
COMMIT TRANSACTION;
""".strip()
    registered_result_select = f"""
SELECT
  target.revision_name,
  target.monitor_contract_version,
  target.sample_endpoint_class,
  target.sample_cloud_trace,
  target.sample_cloud_span_id,
  target.sample_source_ts,
  target.sample_correlation_hash,
  exact_http_count,
  exact_question_count,
  tuple_http_count,
  tuple_question_count,
  TRUE AS registered,
  target.registration_source
FROM {ledger} target
WHERE target.revision_name = @revision
  AND target.monitor_contract_version = 'monitor.v2'
  AND target.sample_endpoint_class = @endpoint_class
  AND target.sample_cloud_trace = @cloud_trace
  AND target.sample_cloud_span_id = @cloud_span_id
  AND target.sample_correlation_hash = proof_correlation_hash;
""".strip()
    return "\n".join(
        (declarations, assertions, mutation, registered_result_select)
    )


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Prove one exact candidate v2 HTTP/question tuple and register its revision"
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--dataset", default="oura_navi_monitor")
    parser.add_argument("--location", default="US")
    parser.add_argument("--revision", required=True)
    parser.add_argument("--trace", required=True)
    parser.add_argument("--span", required=True)
    parser.add_argument("--endpoint-class", choices=sorted(_ENDPOINT_CLASSES), required=True)
    parser.add_argument("--window-start", required=True)
    parser.add_argument("--window-end", required=True)
    parser.add_argument("--maximum-bytes-billed", type=int, default=5_000_000_000)
    parser.add_argument("--credential-file", required=True)
    parser.add_argument("--receipt-output")
    parser.add_argument("--confirm-register")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    window_start = _parse_utc(args.window_start, field="window-start")
    window_end = _parse_utc(args.window_end, field="window-end")
    _validate_contract(
        project=args.project,
        dataset=args.dataset,
        revision=args.revision,
        trace=args.trace,
        span=args.span,
        endpoint_class=args.endpoint_class,
        window_start=window_start,
        window_end=window_end,
    )
    if args.maximum_bytes_billed <= 0:
        raise SystemExit("--maximum-bytes-billed must be positive")
    proof_hash = _proof_hash(
        revision=args.revision,
        trace=args.trace,
        span=args.span,
        endpoint_class=args.endpoint_class,
    )
    required_confirmation = _required_confirmation(
        project=args.project,
        dataset=args.dataset,
        revision=args.revision,
        proof_hash=proof_hash,
    )
    if args.apply:
        if args.confirm_register != required_confirmation:
            raise SystemExit(f"--confirm-register must equal {required_confirmation}")
        if not args.receipt_output:
            raise SystemExit("--receipt-output is required with --apply")
        receipt_path = Path(args.receipt_output)
        if receipt_path.exists():
            raise SystemExit("registration receipt output already exists")
        if not receipt_path.parent.is_dir():
            raise SystemExit("registration receipt output parent does not exist")

    credentials = service_account.Credentials.from_service_account_file(
        str(approved_credential_path(args.credential_file))
    )
    client = bigquery.Client(project=args.project, credentials=credentials)
    parameters = [
        bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
        bigquery.ScalarQueryParameter("window_end", "TIMESTAMP", window_end),
        bigquery.ScalarQueryParameter("revision", "STRING", args.revision),
        bigquery.ScalarQueryParameter("cloud_trace", "STRING", args.trace),
        bigquery.ScalarQueryParameter("cloud_span_id", "STRING", args.span),
        bigquery.ScalarQueryParameter(
            "endpoint_class", "STRING", args.endpoint_class
        ),
    ]
    config = bigquery.QueryJobConfig(
        query_parameters=parameters,
        maximum_bytes_billed=args.maximum_bytes_billed,
        use_query_cache=False,
    )
    rows = list(
        client.query(
            render_registration_sql(
                project=args.project,
                dataset=args.dataset,
                apply=args.apply,
            ),
            job_config=config,
            location=args.location,
        ).result()
    )
    if len(rows) != 1:
        raise SystemExit("candidate registration query did not return one proof row")
    proof = _json_value(dict(rows[0].items()))
    if str(proof.get("sample_correlation_hash") or "") != proof_hash:
        raise SystemExit("candidate proof hash readback does not match the requested tuple")
    output = {
        "receipt_type": _RECEIPT_TYPE,
        "mode": "apply" if args.apply else "plan",
        "project": args.project,
        "dataset": args.dataset,
        "location": args.location,
        "revision": args.revision,
        "windowStart": args.window_start,
        "windowEnd": args.window_end,
        "cloudTrace": args.trace,
        "cloudSpanId": args.span,
        "endpointClass": args.endpoint_class,
        "requiredConfirmation": required_confirmation,
        "proof": proof,
        "capturedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }
    if args.apply:
        receipt_path = Path(args.receipt_output)
        with receipt_path.open("x", encoding="utf-8") as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
    print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
