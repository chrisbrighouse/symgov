from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

from symgov_backend.catalog_search import search_catalog_symbols_for_context


class ExecuteRows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class CapturingSession:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, statement, params=None):
        self.executed.append((str(statement), dict(params or {})))
        return ExecuteRows(self.rows)


def symbol_row(*, name: str, discipline: str, formats: list[str], keywords: list[str]):
    now = datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc)
    slug = name.lower().replace(" ", "-")
    return SimpleNamespace(
        symbol_id=str(uuid.uuid4()),
        slug=slug,
        canonical_name=name,
        category="symbol",
        discipline=discipline,
        symbol_revision_id=str(uuid.uuid4()),
        revision_label="A",
        revision_created_at=now,
        payload_json={
            "package_display_id": "0003",
            "package_symbol_sequence": 12,
            "summary": f"Approved {name} symbol.",
            "keywords": keywords,
            "downloads": [{"format": value, "filename": f"{slug}.{value.lower()}"} for value in formats],
        },
        rationale="Approved for catalog publication.",
        page_id=str(uuid.uuid4()),
        page_code="FA-12",
        page_title=name,
        effective_date=now,
        page_updated_at=now,
        pack_id=str(uuid.uuid4()),
        pack_code="0003",
        pack_title="Catalog Symbols",
        audience="public",
        pack_updated_at=now,
        sort_order=12,
        last_updated_at=now,
    )


def test_search_catalog_symbols_normalizes_context_ranks_and_warns_about_missing_formats():
    lower_ranked = symbol_row(name="Smoke Detector", discipline="Electrical", formats=["SVG"], keywords=["smoke", "detector"])
    higher_ranked = symbol_row(name="Fire Alarm", discipline="Fire & Life Safety", formats=["DXF"], keywords=["alarm"])
    session = CapturingSession([lower_ranked, higher_ranked])

    result = search_catalog_symbols_for_context(
        session,
        query="alarm",
        context={
            "application": " AutoCAD ",
            "discipline": "fire_life_safety",
            "selectedLayer": " FIRE_ALARM ",
            "preferredFormats": [" dxf ", "DXF", "dwg"],
        },
        limit=20,
    )

    assert result.interpreted_filters == {
        "application": "AutoCAD",
        "catalogDisciplines": ["Fire & Life Safety"],
        "selectedLayer": "FIRE_ALARM",
        "preferredFormats": ["DXF", "DWG"],
    }
    assert [item["name"] for item in result.items] == ["Fire Alarm", "Smoke Detector"]
    assert any("DWG" in warning for warning in result.warnings)
    assert any("query" in explanation.lower() for explanation in result.ranking_explanation)
    assert any("discipline" in explanation.lower() for explanation in result.ranking_explanation)
    assert any("format" in explanation.lower() for explanation in result.ranking_explanation)


def test_search_catalog_symbols_caps_limit_and_returns_public_symbol_summaries():
    rows = [symbol_row(name=f"Symbol {index:03}", discipline="Electrical", formats=["SVG"], keywords=[]) for index in range(105)]
    session = CapturingSession(rows)

    result = search_catalog_symbols_for_context(session, query="", context={}, limit=500)

    assert len(result.items) == 100
    assert session.executed[-1][1]["limit"] == 100
    assert set(result.items[0]) == {
        "displayId",
        "symbolId",
        "slug",
        "name",
        "summary",
        "catalogDisciplines",
        "catalogCategories",
        "useCases",
        "availableFormats",
        "downloadAvailable",
        "preview",
        "links",
    }
    assert result.items[0]["downloadAvailable"] is False
    assert all("downloadUrl" not in item and "downloadAssets" not in item for item in result.items)
