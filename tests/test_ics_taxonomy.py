"""ICS parser contracts. Synthetic CSV below is not ISO data."""
import importlib
import importlib.util

import pytest
import httpx2, uuid
from datetime import datetime, timezone
from pathlib import Path


def importer():
    assert importlib.util.find_spec("symgov_backend.ics_taxonomy") is not None, "ICS importer missing"
    return importlib.import_module("symgov_backend.ics_taxonomy")


HEADER = "identifier,parent,titleEn,titleFr,scopeEn,scopeFr\n"
SAMPLE = (HEADER + "01,,Synthetic field,Champ,,\n01.080,01,Synthetic group,Groupe,Example scope,\n01.080.10,01.080,Synthetic subgroup,Sous-groupe,,\n").encode()


def test_parser_preserves_three_levels_labels_scope_and_leading_zero():
    rows = importer().parse_csv(b"\xef\xbb\xbf" + SAMPLE)
    assert [r["identifier"] for r in rows] == ["01", "01.080", "01.080.10"]
    assert rows[1]["parent"] == "01"
    assert rows[1]["scopeEn"] == "Example scope"
    assert rows[2]["titleFr"] == "Sous-groupe"


@pytest.mark.parametrize("bad", [
    SAMPLE + b"01,,Duplicate,Double,,\n",
    SAMPLE.replace(b"01.080,01,", b"01.080,02,"),
    SAMPLE.replace(b"01.080.10,01.080,", b"01.080.10,01,"),
    SAMPLE.replace(b"01.080", b"1.080"),
    SAMPLE.replace(b"Synthetic field", b""),
    SAMPLE.replace(b"01,,", b"01,01.080,"),
    SAMPLE.replace(b"scopeFr", b"unknownColumn"),
    SAMPLE + b"02,,Too,many,,,columns\n",
    SAMPLE + b'02,,Field,Champ,,"scope","extra"\n',
])
def test_parser_rejects_invalid_dataset(bad):
    with pytest.raises(ValueError):
        importer().parse_csv(bad)


def test_official_snapshot_provenance_and_complete_crosswalk():
    api = importer()
    assert hasattr(api, "prepare_import"), "provenance validation missing"
    content = (Path(api.__file__).parent / "data/ICS.csv").read_bytes()
    prepared = api.prepare_import(content, retrieved_at=datetime.now(timezone.utc), last_modified="Tue, 25 Mar 2025 09:50:52 GMT")
    assert prepared["counts"] == {"fields": 40, "groups": 401, "subgroups": 940}
    assert prepared["provenance"]["edition"] == 7
    assert prepared["provenance"]["publication_year"] == 2015
    assert prepared["provenance"]["source_update_year"] == 2025
    from symgov_backend.catalog_taxonomy import CATALOG_DISCIPLINE_ORDER
    mappings = prepared["crosswalk"]
    assert {m["domain"] for m in mappings} == set(CATALOG_DISCIPLINE_ORDER)
    codes = {r["identifier"] for r in prepared["rows"]}
    assert all(m["code"] in codes and m["reason"] for m in mappings)
    assert all(m["relation"] in {"broader", "candidate"} for m in mappings)
    assert all(m["review_status"] in {"initial_broader", "needs_review"} for m in mappings)
    assert "29.020.01" not in codes
    with pytest.raises(ValueError, match="review"):
        api.prepare_import(content + b"\n", retrieved_at=datetime.now(timezone.utc), last_modified=None)
    with pytest.raises(ValueError):
        api.prepare_import(content, retrieved_at=datetime.now(), last_modified=None)


@pytest.mark.parametrize("status,headers,body", [
    (302, {"location": "https://example.test/ICS.csv"}, b""),
    (200, {}, b"x" * 2_000_001),
    (200, {"content-length": "2000001"}, b"x"),
    (500, {}, b"private error content"),
], ids=["redirect", "stream-size", "declared-size", "http-error"])
def test_fetch_is_bounded_and_does_not_follow_redirects(status, headers, body):
    api = importer()
    assert hasattr(api, "fetch_official"), "bounded official fetch missing"
    requests = []
    def handler(request):
        requests.append(str(request.url))
        return httpx2.Response(status, headers=headers, content=body)
    with pytest.raises(ValueError):
        api.fetch_official(transport=httpx2.MockTransport(handler))
    assert requests == [api.SOURCE["source_url"]]
