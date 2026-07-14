"""Offline contract helpers for the isolated synthetic Langfuse POC."""

from __future__ import annotations

import json
import uuid
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

FORBIDDEN_VALUE_MARKERS = (
    "poc.person@example.invalid",
    "Bearer poc-not-a-real-token-1234567890",
    "Synthetic source note that must never be sent",
)

_REQUIRED_FIELDS = {
    "event_id",
    "occurred_at_utc",
    "trace_id",
    "observation_id",
    "use_case",
    "service_name",
    "agent_slug",
    "provider",
    "requested_model",
    "resolved_model",
    "request_kind",
    "attempt_number",
    "status",
    "latency_ms",
    "cost_currency",
    "cost_basis",
    "pricing_version",
    "provider_reported_cost_usd",
    "calculated_cost_usd",
    "metadata",
}

_FORBIDDEN_KEYS = {
    "prompt",
    "completion",
    "input",
    "output",
    "image",
    "document",
    "object_url",
    "api_key",
    "bearer_token",
    "authorization",
    "email",
    "source_note",
    "filename",
    "ip_address",
    "raw_response",
    "raw_request",
}


def approved_metadata() -> set[str]:
    return {
        "environment", "service", "agent", "usecase", "provider", "model",
        "requestkind", "queueitemid", "agentrunid", "symbolid", "symboldisplayid",
        "feature", "initiatorkind", "initiatorpseudonym", "pricingversion",
        "costbasis", "release",
    }


def load_fixture(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_fixture(fixture: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    for event in fixture["sanitized_events"]:
        missing = _REQUIRED_FIELDS - event.keys()
        if missing:
            errors.append(f"{event.get('event_id', 'unknown')}: missing {sorted(missing)}")
        if _FORBIDDEN_KEYS & event.keys():
            errors.append(f"{event['event_id']}: forbidden event field")
        try:
            uuid.UUID(event["event_id"])
        except (ValueError, KeyError):
            errors.append(f"{event.get('event_id', 'unknown')}: invalid UUID")
        if not event.get("occurred_at_utc", "").endswith("Z"):
            errors.append(f"{event['event_id']}: timestamp is not UTC")
        if set(event["metadata"]) != approved_metadata():
            errors.append(f"{event['event_id']}: metadata is not exactly approved")
        if any(not isinstance(value, str) or len(value) > 200 for value in event["metadata"].values()):
            errors.append(f"{event['event_id']}: metadata value invalid")
        if event["request_kind"] == "image_generation" and ({"input_tokens", "output_tokens"} & event.keys()):
            errors.append(f"{event['event_id']}: image event invents text tokens")
        if event["cost_currency"] != "USD":
            errors.append(f"{event['event_id']}: currency is not USD")
    return errors


def _cost(event: dict[str, Any]) -> Decimal:
    return Decimal(event["provider_reported_cost_usd"] or event["calculated_cost_usd"] or "0")


def build_utc_aggregates(events: list[dict[str, Any]]) -> dict[str, list[list[str]]]:
    weekly: defaultdict[tuple[str, str, str, str, str], Decimal] = defaultdict(Decimal)
    monthly: defaultdict[tuple[str, str, str, str, str], Decimal] = defaultdict(Decimal)
    for event in events:
        timestamp = datetime.fromisoformat(event["occurred_at_utc"].replace("Z", "+00:00"))
        iso_year, iso_week, _ = timestamp.isocalendar()
        fields = (
            event["agent_slug"] or "none",
            event["use_case"],
            event["resolved_model"],
            event["metadata"]["initiatorpseudonym"],
        )
        weekly[(f"{iso_year}-W{iso_week:02d}", *fields)] += _cost(event)
        monthly[(timestamp.strftime("%Y-%m"), *fields)] += _cost(event)

    def render(source: defaultdict[tuple[str, str, str, str, str], Decimal]) -> list[list[str]]:
        return [list(key) + [f"{value:.6f}"] for key, value in sorted(source.items())]

    return {"weekly": render(weekly), "monthly": render(monthly)}
