"""Legend sheet: the SVG builder (pure) and the two routes (SQLite harness).

The routes reuse `test_effective_palette`'s fixtures; storage is stubbed
because SQLite has no object store. Tenant isolation of the underlying
membership is the palette's and `get_set`'s own, covered in their suites.
"""

from __future__ import annotations

import uuid
import xml.etree.ElementTree as ET

import pytest

import symgov_backend.legend_sheet as legend_sheet
from symgov_backend.legend_sheet import (
    COLUMNS, LegendCell, LegendSheet, LegendSvgError, MAX_SYMBOLS, build_legend_svg, nested_symbol,
)

from test_effective_palette import _eligibility, _make_default_set, _symbol
from test_projects_api import _stage4_client
from test_symbol_set_items import _ensure_symbol_tables, _replace_items

NS = {"s": "http://www.w3.org/2000/svg"}
GOOD = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 20"><defs><linearGradient id="g"/></defs><rect id="r" width="10" height="20" fill="url(#g)"/></svg>'


def _texts(svg: bytes) -> list[str]:
    return [t.text for t in ET.fromstring(svg).iter("{http://www.w3.org/2000/svg}text")]


def test_nested_symbol_prefixes_ids_and_references():
    root = nested_symbol(GOOD, "c3-")
    ids = [el.get("id") for el in root.iter() if el.get("id")]
    assert ids == ["c3-g", "c3-r"]
    rect = next(el for el in root.iter() if el.get("id") == "c3-r")
    assert rect.get("fill") == "url(#c3-g)"


def test_nested_symbol_strips_active_content_and_external_references():
    hostile = (
        b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 1 1" onload="x()">'
        b'<script>alert(1)</script><foreignObject/><rect onclick="x()" fill="url(http://evil/x)"/>'
        b'<image xlink:href="http://evil/a.png"/><use xlink:href="#a"/><a id="a"/></svg>'
    )
    root = nested_symbol(hostile, "p-")
    tags = {el.tag.rsplit("}", 1)[-1] for el in root.iter()}
    assert not tags & {"script", "foreignObject"}
    assert "onload" not in root.attrib
    for el in root.iter():
        assert not any(name.rsplit("}", 1)[-1].startswith("on") for name in el.attrib)
        assert "evil" not in "".join(el.attrib.values())
    use = next(el for el in root.iter() if el.tag.endswith("use"))
    assert use.get("{http://www.w3.org/1999/xlink}href") == "#p-a" or use.get("href") == "#p-a"


@pytest.mark.parametrize("payload", [
    b'<!DOCTYPE svg [<!ENTITY x "y">]><svg xmlns="http://www.w3.org/2000/svg"/>',
    b"<svg",
    b"<html/>",
])
def test_nested_symbol_refuses_unsafe_documents(payload):
    with pytest.raises(LegendSvgError):
        nested_symbol(payload, "p-")


def test_sheet_is_a3_landscape_with_caption_and_attribution():
    sheet = LegendSheet(title="Plant legend", subtitle="ACME · 2 symbols", cells=[
        LegendCell("S-000001", "Gate valve", "GateValve", "Valves", GOOD, "Drawn by ChemSim"),
        LegendCell("S-000002", "Broken", None, None, b"<svg"),
    ])
    svg = build_legend_svg(sheet)
    root = ET.fromstring(svg)
    assert root.get("width") == "420mm" and root.get("height") == "297mm"
    texts = _texts(svg)
    assert {"Plant legend", "S-000001", "Gate valve", "GateValve", "Valves", "Drawn by ChemSim", "No drawing available"} <= set(texts)
    assert "S-000002" in texts


def test_many_symbols_stack_pages_and_ids_stay_unique():
    cells = [LegendCell(f"S-{i:06d}", f"Symbol {i}", svg=GOOD) for i in range(COLUMNS * 12)]
    svg = build_legend_svg(LegendSheet(title="Big", cells=cells))
    root = ET.fromstring(svg)
    pages = root.findall("s:g[@class='legend-page']", NS)
    assert len(pages) >= 2
    assert int(root.get("height").removesuffix("mm")) == 297 * len(pages)
    ids = [el.get("id") for el in root.iter() if el.get("id")]
    assert len(ids) == len(set(ids)) == 2 * len(cells)
    assert any("Page 2 of" in (t or "") for t in _texts(svg))


def test_titles_are_escaped():
    svg = build_legend_svg(LegendSheet(title='A <b> & "c"', cells=[]))
    assert b"<b>" not in svg
    assert 'A <b> & "c"' in _texts(svg)


# --- routes -------------------------------------------------------------------


def _stub_svgs(monkeypatch, by_symbol=None):
    monkeypatch.setattr(legend_sheet, "_svg_bytes", lambda session, revision_id, payload, settings: GOOD)
    monkeypatch.setattr(legend_sheet, "_rights_attribution", lambda session, revision_id, payload: "Credit line")


def test_set_legend_route_lists_eligible_items_in_order(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    _project_id, set_id = _make_default_set(client, Session)
    a = _symbol(Session, "legend-a", catalog_symbol_id="S-000001")
    b = _symbol(Session, "legend-b", catalog_symbol_id="S-000002")
    gone = _symbol(Session, "legend-gone", catalog_symbol_id="S-000003")
    _eligibility(monkeypatch, {a: uuid.uuid4(), b: uuid.uuid4(), gone: uuid.uuid4()})
    assert _replace_items(client, set_id, {"items": [
        {"governedSymbolId": str(a), "sortOrder": 2, "groupName": "Valves"},
        {"governedSymbolId": str(b), "sortOrder": 1},
        {"governedSymbolId": str(gone), "sortOrder": 3},
    ]}).status_code == 200
    _eligibility(monkeypatch, {a: uuid.uuid4(), b: uuid.uuid4()})  # `gone` loses eligibility
    _stub_svgs(monkeypatch)

    response = client.get(f"/api/v1/org/me/symbol-sets/{set_id}/legend.svg")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/svg+xml")
    assert response.headers["x-content-type-options"] == "nosniff"
    texts = _texts(response.content)
    assert texts.index("S-000002") < texts.index("S-000001")
    assert "S-000003" not in texts
    assert "Valves" in texts and "Credit line" in texts


def test_set_legend_route_404s_for_an_unknown_set(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    assert client.get(f"/api/v1/org/me/symbol-sets/{uuid.uuid4()}/legend.svg").status_code == 404


def test_project_legend_route_uses_the_effective_palette(monkeypatch):
    client, Session = _stage4_client()
    _ensure_symbol_tables(Session)
    project_id, set_id = _make_default_set(client, Session)
    a = _symbol(Session, "legend-pa", catalog_symbol_id="S-000010")
    _eligibility(monkeypatch, {a: uuid.uuid4()})
    assert _replace_items(client, set_id, {"items": [{"governedSymbolId": str(a), "sortOrder": 1}]}).status_code == 200
    _stub_svgs(monkeypatch)

    response = client.get(f"/api/v1/org/me/projects/{project_id}/legend.svg")
    assert response.status_code == 200
    assert "S-000010" in _texts(response.content)
    assert client.get(f"/api/v1/org/me/projects/{uuid.uuid4()}/legend.svg").status_code == 404


def test_legend_is_capped():
    governed = type("G", (), {"catalog_symbol_id": "S-1", "slug": "s", "canonical_name": "n"})()
    entries = [{"governed": governed, "revisionId": None, "displayId": f"S-{i}"} for i in range(MAX_SYMBOLS + 7)]

    class Query:
        def filter(self, *a):
            return self

        def all(self):
            return []

    class Session:
        def query(self, *a):
            return Query()

    sheet = legend_sheet.load_cells(Session(), None, entries)
    assert len(sheet.cells) == MAX_SYMBOLS and sheet.omitted == 7
