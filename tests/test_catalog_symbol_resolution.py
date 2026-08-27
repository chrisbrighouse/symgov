from __future__ import annotations

import uuid

import pytest

from symgov_backend import catalog_symbol_resolution as resolution


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return list(self._rows)


class ResolverSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def execute(self, statement, params):
        self.calls.append((str(statement), params))
        return _Rows(self.responses.pop(0) if self.responses else [])


def _row(symbol_id: uuid.UUID, catalog_symbol_id: str = "S-000001") -> dict:
    return {"symbol_id": symbol_id, "catalog_symbol_id": catalog_symbol_id}


@pytest.mark.parametrize(
    "reference",
    [
        " S-000001",
        "S-000001 ",
        "S%252D000001",
        "S/000001",
        "S\\000001",
        "S?000001",
        "S#000001",
        "S%0A000001",
        "x" * 129,
        "%FF",
    ],
)
def test_resolver_rejects_unsafe_references_before_database_lookup(reference: str) -> None:
    session = ResolverSession([])

    assert resolution.resolve_catalog_symbol(session, reference) is None
    assert session.calls == []


def test_resolver_prefers_canonical_identifier_and_normalizes_case() -> None:
    symbol_id = uuid.uuid4()
    session = ResolverSession([[_row(symbol_id)]])

    result = resolution.resolve_catalog_symbol(session, "s-000001")

    assert result == resolution.ResolvedCatalogSymbol(
        symbol_id=symbol_id,
        catalog_symbol_id="S-000001",
        matched_by="canonical",
    )
    assert session.calls[0][1] == {
        "identifier": "S-000001",
        "role": "canonical",
        "symbol_ref": "s-000001",
    }


def test_resolver_uses_uuid_before_slug() -> None:
    symbol_id = uuid.uuid4()
    session = ResolverSession([[_row(symbol_id)]])

    result = resolution.resolve_catalog_symbol(session, str(symbol_id))

    assert result is not None
    assert result.matched_by == "uuid"
    assert session.calls[0][1] == {"symbol_id": symbol_id, "symbol_ref": str(symbol_id)}


def test_resolver_uses_exact_slug_then_historical_alias_then_page_code() -> None:
    symbol_id = uuid.uuid4()
    slug_session = ResolverSession([[], [_row(symbol_id)]])
    assert resolution.resolve_catalog_symbol(slug_session, "check-valve").matched_by == "slug"

    alias_session = ResolverSession([[], [], [_row(symbol_id)]])
    assert resolution.resolve_catalog_symbol(alias_session, "legacy-42").matched_by == "historical_alias"

    page_session = ResolverSession([[], [], [], [_row(symbol_id)]])
    assert resolution.resolve_catalog_symbol(page_session, "PKG-CHECK-VALVE-R1").matched_by == "page_code"


def test_same_symbol_duplicate_compatibility_rows_resolve_but_cross_symbol_ambiguity_fails_closed() -> None:
    symbol_id = uuid.uuid4()
    same_symbol = ResolverSession([[], [], [_row(symbol_id), _row(symbol_id)]])
    assert resolution.resolve_catalog_symbol(same_symbol, "legacy-42").symbol_id == symbol_id

    ambiguous_alias = ResolverSession([[], [], [_row(uuid.uuid4()), _row(uuid.uuid4(), "S-000002")]])
    assert resolution.resolve_catalog_symbol(ambiguous_alias, "legacy-42") is None

    ambiguous_page = ResolverSession([[], [], [], [_row(uuid.uuid4()), _row(uuid.uuid4(), "S-000002")]])
    assert resolution.resolve_catalog_symbol(ambiguous_page, "PAGE-CODE") is None


def test_resolver_fails_closed_when_resolved_symbol_has_no_canonical_identifier() -> None:
    session = ResolverSession([[], [{"symbol_id": uuid.uuid4(), "catalog_symbol_id": None}]])

    assert resolution.resolve_catalog_symbol(session, "published-slug") is None


def test_resolver_limits_each_lookup_query_and_emits_bounded_telemetry(caplog) -> None:
    symbol_id = uuid.uuid4()
    session = ResolverSession([[_row(symbol_id)]])

    with caplog.at_level("INFO"):
        result = resolution.resolve_catalog_symbol(
            session,
            "s-000001",
            route_family="published.symbol_detail",
        )

    assert result is not None
    assert result.catalog_symbol_id == "S-000001"
    sql, params = session.calls[0]
    assert "LIMIT 2" in sql
    assert params["identifier"] == "S-000001"
    telemetry = [record.message for record in caplog.records if "catalog_symbol_resolution" in record.message]
    assert telemetry
    assert "route_family=published.symbol_detail" in telemetry[0]
    assert "match_type=canonical" in telemetry[0]
    assert "outcome=resolved" in telemetry[0]
    assert "catalog_symbol_id=S-000001" in telemetry[0]
    assert "s-000001" not in telemetry[0]


class _FailureSession:
    def execute(self, _statement, _params):
        raise RuntimeError("database error with token=secret and ref=S-000001")


def test_resolver_failure_emits_bounded_failure_telemetry_without_reference(caplog) -> None:
    with caplog.at_level("INFO"):
        with pytest.raises(RuntimeError):
            resolution.resolve_catalog_symbol(
                _FailureSession(),
                "s-000001",
                route_family="catalog.symbol_detail",
            )

    telemetry = [record.message for record in caplog.records if "catalog_symbol_resolution" in record.message]
    assert telemetry
    assert "route_family=catalog.symbol_detail" in telemetry[0]
    assert "outcome=failure" in telemetry[0]
    assert "match_type=canonical" in telemetry[0]
    assert "s-000001" not in telemetry[0]
    assert "secret" not in telemetry[0]
