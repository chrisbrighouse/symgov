"""Synthetic-only Phase 1 Langfuse POC contract tests.

These tests deliberately have no network dependency. They validate fixture content and
expected aggregate/reconciliation semantics before a live POC can ingest it.
"""

from __future__ import annotations

from pathlib import Path

from langfuse_poc_contract import (
    FORBIDDEN_VALUE_MARKERS,
    approved_metadata,
    build_utc_aggregates,
    load_fixture,
    validate_fixture,
)

FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "synthetic_events.json"


def test_fixture_only_uses_approved_metadata_and_required_contract_fields() -> None:
    fixture = load_fixture(FIXTURE_PATH)

    assert validate_fixture(fixture) == []
    assert approved_metadata() == {
        "environment",
        "service",
        "agent",
        "usecase",
        "provider",
        "model",
        "requestkind",
        "queueitemid",
        "agentrunid",
        "symbolid",
        "symboldisplayid",
        "feature",
        "initiatorkind",
        "initiatorpseudonym",
        "pricingversion",
        "costbasis",
        "release",
    }


def test_sensitive_probe_values_are_never_in_sanitized_events() -> None:
    fixture = load_fixture(FIXTURE_PATH)

    sanitized_text = repr(fixture["sanitized_events"])
    for forbidden_value in FORBIDDEN_VALUE_MARKERS:
        assert forbidden_value not in sanitized_text


def test_image_edit_event_has_image_units_without_invented_text_tokens() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    image_edit = next(event for event in fixture["sanitized_events"] if event["request_kind"] == "image_generation")

    assert image_edit["image_input_units"] == 1
    assert image_edit["image_output_units"] == 1
    assert "input_tokens" not in image_edit
    assert "output_tokens" not in image_edit
    assert image_edit["cost_basis"] == "price_snapshot"
    assert image_edit["calculated_cost_usd"] == "0.040000"


def test_retry_events_are_distinct_attempts_and_sum_all_attempt_costs() -> None:
    fixture = load_fixture(FIXTURE_PATH)
    retries = [event for event in fixture["sanitized_events"] if event["trace_id"] == "poc-trace-retry"]

    assert [event["attempt_number"] for event in retries] == [1, 2]
    assert len({event["observation_id"] for event in retries}) == 2
    assert sum(float(event["calculated_cost_usd"]) for event in retries) == 0.012


def test_utc_aggregates_match_week_month_and_reconciliation_fixture() -> None:
    fixture = load_fixture(FIXTURE_PATH)

    aggregates = build_utc_aggregates(fixture["sanitized_events"])
    assert aggregates == fixture["expected_utc_aggregates"]
    assert fixture["provider_statement_total_usd"] - fixture["expected_event_total_usd"] == 5.0
    assert fixture["provider_statement_total_usd"] - fixture["expected_event_total_usd"] <= 5.0


def test_ingestion_batch_contains_only_sanitized_trace_metadata_and_usage() -> None:
    from verify_poc import to_ingestion_batch

    fixture = load_fixture(FIXTURE_PATH)
    batch = to_ingestion_batch(fixture["sanitized_events"])
    rendered = repr(batch)

    assert len(batch) == 10  # one trace-create and one generation-create per event
    assert all("input" not in event["body"] and "output" not in event["body"] for event in batch)
    assert any(event["body"].get("usageDetails", {}).get("input") == 120 for event in batch)
    assert any(event["body"].get("usageDetails", {}).get("inputImage") == 1 for event in batch)
    for forbidden_value in FORBIDDEN_VALUE_MARKERS:
        assert forbidden_value not in rendered


def test_retry_observation_ids_are_deterministically_verified() -> None:
    from verify_poc import retry_observation_ids

    observations = {"data": [{"id": "poc-observation-retry-2"}, {"id": "poc-observation-retry-1"}]}
    assert retry_observation_ids(observations) == ["poc-observation-retry-1", "poc-observation-retry-2"]


def test_compose_uses_only_poc_prefixed_values_and_a_dedicated_named_network() -> None:
    compose_file = Path(__file__).parents[1] / "docker-compose.yml"
    compose_text = compose_file.read_text(encoding="utf-8")

    for variable in (
        "POC_POSTGRES_USER",
        "POC_POSTGRES_PASSWORD",
        "POC_CLICKHOUSE_PASSWORD",
        "POC_REDIS_AUTH",
        "POC_MINIO_ROOT_PASSWORD",
        "POC_NEXTAUTH_URL",
        "POC_SALT",
        "POC_ENCRYPTION_KEY",
        "POC_LANGFUSE_INIT_PROJECT_SECRET_KEY",
        "POC_LANGFUSE_INIT_USER_PASSWORD",
    ):
        assert f"${{{variable}}}" in compose_text

    for variable in (
        "POSTGRES_PASSWORD",
        "CLICKHOUSE_PASSWORD",
        "REDIS_AUTH",
        "MINIO_ROOT_PASSWORD",
        "NEXTAUTH_URL",
        "SALT",
        "ENCRYPTION_KEY",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY",
        "LANGFUSE_INIT_USER_PASSWORD",
    ):
        assert f"${{{variable}}}" not in compose_text

    assert "  poc-internal:\n    name: langfuse-poc-internal\n    driver: bridge" in compose_text
    assert "internal: true" not in compose_text

    for path in (
        Path(__file__).parents[1] / "scripts" / "create_poc_env.sh",
        Path(__file__).parents[1] / ".env.example",
    ):
        env_contract = path.read_text(encoding="utf-8")
        for variable in (
            "POC_POSTGRES_USER",
            "POC_POSTGRES_PASSWORD",
            "POC_CLICKHOUSE_PASSWORD",
            "POC_REDIS_AUTH",
            "POC_MINIO_ROOT_PASSWORD",
            "POC_NEXTAUTH_URL",
            "POC_SALT",
            "POC_ENCRYPTION_KEY",
            "POC_LANGFUSE_INIT_PROJECT_SECRET_KEY",
            "POC_LANGFUSE_INIT_USER_PASSWORD",
        ):
            assert f"{variable}=" in env_contract


def test_verifier_reads_only_poc_prefixed_api_credentials() -> None:
    import base64
    from verify_poc import basic_auth_token

    env = {
        "POC_LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "poc-public",
        "POC_LANGFUSE_INIT_PROJECT_SECRET_KEY": "poc-secret",
        "LANGFUSE_INIT_PROJECT_PUBLIC_KEY": "production-public",
        "LANGFUSE_INIT_PROJECT_SECRET_KEY": "production-secret",
    }

    assert base64.b64decode(basic_auth_token(env)) == b"poc-public:poc-secret"


def test_verifier_rejects_non_loopback_base_urls() -> None:
    import pytest
    from verify_poc import validate_poc_base_url

    assert validate_poc_base_url("http://127.0.0.1:13000") == "http://127.0.0.1:13000"
    with pytest.raises(ValueError, match="loopback"):
        validate_poc_base_url("https://langfuse.example.invalid")
