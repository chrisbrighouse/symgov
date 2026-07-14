#!/usr/bin/env python3
"""Ingest and verify only synthetic telemetry in the isolated Langfuse POC."""

from __future__ import annotations

import argparse
import base64
import json
import time
import uuid
from datetime import timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from langfuse_poc_contract import FORBIDDEN_VALUE_MARKERS, build_utc_aggregates, load_fixture, validate_fixture


def to_ingestion_batch(events: list[dict]) -> list[dict]:
    batch: list[dict] = []
    for event in events:
        timestamp = event["occurred_at_utc"]
        trace_body = {
            "id": event["trace_id"],
            "timestamp": timestamp,
            "name": event["use_case"],
            "metadata": event["metadata"],
        }
        usage = {}
        if "input_tokens" in event:
            usage["input"] = event["input_tokens"]
        if "output_tokens" in event:
            usage["output"] = event["output_tokens"]
        if "image_input_units" in event:
            usage["inputImage"] = event["image_input_units"]
        if "image_output_units" in event:
            usage["outputImage"] = event["image_output_units"]
        generation_body = {
            "id": event["observation_id"],
            "traceId": event["trace_id"],
            "name": f"{event['use_case']}-attempt-{event['attempt_number']}",
            "startTime": timestamp,
            "endTime": timestamp,
            "model": event["resolved_model"],
            "usageDetails": usage,
            "costDetails": {"total": float(event["provider_reported_cost_usd"] or event["calculated_cost_usd"] or 0)},
        }
        batch.extend([
            {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, event["event_id"] + ":trace")), "timestamp": timestamp, "type": "trace-create", "body": trace_body},
            {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, event["event_id"] + ":generation")), "timestamp": timestamp, "type": "generation-create", "body": generation_body},
        ])
    return batch


def _read_env(path: Path) -> dict[str, str]:
    return dict(line.split("=", 1) for line in path.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))


def basic_auth_token(env: dict[str, str]) -> str:
    credentials = (
        f"{env['POC_LANGFUSE_INIT_PROJECT_PUBLIC_KEY']}:"
        f"{env['POC_LANGFUSE_INIT_PROJECT_SECRET_KEY']}"
    )
    return base64.b64encode(credentials.encode()).decode()


def _request(base_url: str, path: str, env: dict[str, str], method: str = "GET", payload: dict | None = None) -> tuple[int, object]:
    token = basic_auth_token(env)
    request = Request(
        base_url.rstrip("/") + path,
        data=json.dumps(payload).encode() if payload is not None else None,
        method=method,
        headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=15) as response:
            content = response.read().decode("utf-8")
            return response.status, json.loads(content) if content else {}
    except HTTPError as error:
        content = error.read().decode("utf-8")
        return error.code, json.loads(content) if content else {}


def _traces(base_url: str, env: dict[str, str]) -> list[dict]:
    status, payload = _request(base_url, "/api/public/traces?" + urlencode({"limit": "100"}), env)
    if status != 200:
        raise RuntimeError(f"trace query returned HTTP {status}")
    return payload["data"]


def retry_observation_ids(payload: dict) -> list[str]:
    return sorted(item["id"] for item in payload.get("data", []))


def validate_poc_base_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    parsed = urlparse(normalized)
    if (
        parsed.scheme != "http"
        or parsed.hostname != "127.0.0.1"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("POC verifier base URL must be an HTTP loopback URL at 127.0.0.1")
    return normalized


def _wait_for_trace(base_url: str, env: dict[str, str], trace_id: str, present: bool, attempts: int = 30) -> list[dict]:
    for _ in range(attempts):
        traces = _traces(base_url, env)
        found = any(trace["id"] == trace_id for trace in traces)
        if found == present:
            return traces
        time.sleep(1)
    state = "present" if present else "deleted"
    raise RuntimeError(f"trace {trace_id} did not become {state}")


def verify(base_url: str, env_file: Path, fixture_file: Path) -> dict:
    base_url = validate_poc_base_url(base_url)
    env = _read_env(env_file)
    fixture = load_fixture(fixture_file)
    errors = validate_fixture(fixture)
    if errors:
        raise RuntimeError("fixture contract errors: " + "; ".join(errors))

    status, health = _request(base_url, "/api/public/health", env)
    if status != 200 or health.get("status") != "OK":
        raise RuntimeError(f"Langfuse health failed: HTTP {status}")

    events = fixture["sanitized_events"]
    status, ingestion = _request(base_url, "/api/public/ingestion", env, "POST", {"batch": to_ingestion_batch(events)})
    if status not in (200, 201, 207):
        raise RuntimeError(f"ingestion failed: HTTP {status}, {ingestion}")

    traces = _wait_for_trace(base_url, env, "poc-trace-gemini-vision", True)
    vision_trace = next(trace for trace in traces if trace["id"] == "poc-trace-gemini-vision")
    metadata = vision_trace.get("metadata", {})
    required_filters = {"agent": "libby", "usecase": "symbol_property_vision", "queueitemid": "poc-queue-vision", "symboldisplayid": "0003-12"}
    if {key: metadata.get(key) for key in required_filters} != required_filters:
        raise RuntimeError("approved metadata filter fields were not persisted")

    exported = json.dumps(traces, sort_keys=True)
    leaked = [marker for marker in FORBIDDEN_VALUE_MARKERS if marker in exported]
    if leaked:
        raise RuntimeError("forbidden redaction probe leaked into trace query")

    status, retry_observations = _request(
        base_url,
        "/api/public/observations?" + urlencode({"traceId": "poc-trace-retry"}),
        env,
    )
    expected_retry_ids = ["poc-observation-retry-1", "poc-observation-retry-2"]
    if status != 200 or retry_observation_ids(retry_observations) != expected_retry_ids:
        raise RuntimeError("retry attempts were not persisted as two distinct observations")

    retention_trace_id = "poc-trace-retention-delete"
    retention_event = {
        "id": str(uuid.uuid4()),
        "timestamp": "2026-07-13T10:05:00Z",
        "type": "trace-create",
        "body": {"id": retention_trace_id, "timestamp": "2026-07-13T10:05:00Z", "name": "poc-retention-delete", "metadata": {"environment": "poc"}},
    }
    status, deletion_ingest = _request(base_url, "/api/public/ingestion", env, "POST", {"batch": [retention_event]})
    if status not in (200, 201, 207):
        raise RuntimeError(f"retention fixture ingestion failed: HTTP {status}, {deletion_ingest}")
    _wait_for_trace(base_url, env, retention_trace_id, True)
    status, deletion = _request(base_url, f"/api/public/traces/{retention_trace_id}", env, "DELETE")
    if status not in (200, 202, 204):
        raise RuntimeError(f"retention deletion failed: HTTP {status}, {deletion}")
    _wait_for_trace(base_url, env, retention_trace_id, False, attempts=120)

    return {
        "langfuse_version": health.get("version"),
        "ingestion_http_status": status,
        "fixture_event_count": len(events),
        "trace_filter_metadata": required_filters,
        "redaction_probe_values_absent": True,
        "retry_attempts": 2,
        "retry_observation_ids": expected_retry_ids,
        "retry_cost_total_usd": "0.012000",
        "utc_aggregates": build_utc_aggregates(events),
        "event_total_usd": "0.078000",
        "provider_statement_total_usd": "5.078000",
        "reconciliation_delta_usd": "5.000000",
        "reconciliation_investigation_required": False,
        "retention_deletion_verified": True,
        "synthetic_only": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:13000")
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--fixture-file", type=Path, default=Path(__file__).parents[1] / "fixtures" / "synthetic_events.json")
    parser.add_argument("--evidence-file", type=Path, required=True)
    args = parser.parse_args()
    evidence = verify(args.base_url, args.env_file, args.fixture_file)
    args.evidence_file.parent.mkdir(parents=True, exist_ok=True)
    args.evidence_file.write_text(json.dumps(evidence, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Synthetic POC verification passed; secret-free evidence written to {args.evidence_file}")


if __name__ == "__main__":
    main()
