"""Hannah's concept photos in the public Catalog on a real PostgreSQL (WP-H5).

Photos belong to a semantic concept and reach a symbol through the verified
primary assignment on its served revision. Driven over HTTP as a reader with
no roles, with object storage faked:

- a public symbol shows its concept's approved photos, at most two, in the
  steward's order, with the credit fields;
- two symbols on one concept share them, and the Catalog photo column counts
  by concept;
- a proposed assignment, and a rejected, removed or proposed photo, show
  nothing and are never served;
- a concept seen only on an unpublished revision shows nothing, and its
  preview returns 404.
"""

from __future__ import annotations

import io
import sys
import uuid
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import SEARCH, search_database  # noqa: E402,F401  (fixture)
from test_hannah_targets_postgresql import NOW, concepts  # noqa: E402,F401  (fixture)
from test_wp74_symbol_demotion_postgresql import _client, _create_user_with_global_roles  # noqa: E402
from test_wp81_catalog_organization_context_postgresql import _login  # noqa: E402

from symgov_backend.routes import published as published_routes  # noqa: E402

pytest.importorskip("psycopg")


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 120, 150)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _photo(connection, concept_id, title, *, status, rank=None, score=0.8, stored=True, author="Ann Example"):
    photo_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO hannah_concept_photos (id, semantic_concept_id, source, source_url, image_url, title, author, "
            "license_label, license_url, score, status, rank, object_key, content_type, decided_at, first_seen_at, last_seen_at) "
            "VALUES (:id, :concept, 'commons_category', :source_url, :image_url, :title, :author, 'CC BY-SA 4.0', "
            "'https://creativecommons.org/licenses/by-sa/4.0', :score, :status, :rank, :object_key, 'image/jpeg', "
            "CASE WHEN :status IN ('approved', 'rejected', 'removed') THEN CAST(:now AS timestamptz) END, :now, :now)"
        ),
        {
            "id": photo_id, "concept": concept_id, "title": title, "author": author, "score": score,
            "status": status, "rank": rank, "now": NOW,
            "source_url": f"https://commons.wikimedia.org/wiki/File:{title.replace(' ', '_')}",
            "image_url": f"https://upload.wikimedia.org/{photo_id}.jpg",
            "object_key": f"hannah/concept-photos/test/{photo_id}.jpg" if stored else None,
        },
    )
    return str(photo_id)


@pytest.fixture(scope="module")
def photos(concepts):
    engine, ids = concepts
    with engine.begin() as connection:
        seeded = {
            "valve second": _photo(connection, ids["valve"], "Gate valve second.jpg", status="approved", rank=2, score=0.95, author=None),
            "valve first": _photo(connection, ids["valve"], "Gate valve first.jpg", status="approved", rank=1, score=0.6),
            "valve third": _photo(connection, ids["valve"], "Gate valve third.jpg", status="approved", rank=3),
            "valve rejected": _photo(connection, ids["valve"], "Gate valve rejected.jpg", status="rejected"),
            "valve removed": _photo(connection, ids["valve"], "Gate valve removed.jpg", status="removed"),
            "valve proposed": _photo(connection, ids["valve"], "Gate valve proposed.jpg", status="proposed", rank=1, stored=False),
            "private": _photo(connection, ids["private"], "Fire hydrant.jpg", status="approved", rank=1),
            "proposed concept": _photo(connection, ids["proposed"], "Smoke detector.jpg", status="approved", rank=1),
        }
    yield engine, seeded
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM hannah_concept_photos"))


@pytest.fixture(scope="module")
def reader(photos):
    engine, seeded = photos
    client, Session = _client(engine)
    _create_user_with_global_roles(Session, email="hannah-reader@example.test", display_name="Reader", roles=[])
    _login(client, "hannah-reader@example.test")
    return client, seeded


@pytest.fixture()
def storage(monkeypatch):
    fetched = []

    def download(*, object_key, env_file):
        fetched.append(object_key)
        return {"payload": _jpeg(), "content_type": "image/jpeg"}

    monkeypatch.setattr(published_routes, "download_object_bytes", download)
    return fetched


def _photos_by_catalog_id(client):
    items = client.get("/api/v1/published/symbols").json()["items"]
    return {item["catalogSymbolId"]: item["supplementalPhotos"] for item in items}


def test_symbols_on_one_concept_share_its_approved_photos_in_the_stewards_order(reader):
    client, seeded = reader
    by_symbol = _photos_by_catalog_id(client)

    gate = by_symbol["TST-2"]
    assert [photo["id"] for photo in gate] == [seeded["valve first"], seeded["valve second"]]
    assert by_symbol["TST-10"] == gate
    assert gate[0] == {
        "id": seeded["valve first"],
        "title": "Gate valve first.jpg",
        "sourceUrl": "https://commons.wikimedia.org/wiki/File:Gate_valve_first.jpg",
        "sourceDomain": "commons.wikimedia.org",
        "author": "Ann Example",
        "licenseLabel": "CC BY-SA 4.0",
        "licenseUrl": "https://creativecommons.org/licenses/by-sa/4.0",
        "score": 0.6,
        "previewUrl": f"/api/v1/published/concept-photos/{seeded['valve first']}/preview",
    }
    assert gate[1]["author"] is None

    # A proposed assignment carries no photos; neither does a concept without any.
    assert by_symbol["TST-1"] == [] and by_symbol["TST-3"] == [] and by_symbol["TST-20"] == []

    detail = client.get("/api/v1/published/symbols/TST-10").json()["item"]
    assert detail["supplementalPhotos"] == gate


def test_search_rows_and_the_photo_column_count_by_concept(reader):
    client, _seeded = reader
    body = client.get(SEARCH, params={"column.photoStatus": "2 added", "sort": "id"}).json()
    assert [item["catalogSymbolId"] for item in body["items"]] == ["TST-2", "TST-10"]
    assert all(len(item["supplementalPhotos"]) == 2 for item in body["items"])
    assert client.get(SEARCH, params={"column.photoStatus": "1 added"}).json()["total"] == 0


def test_the_preview_serves_only_approved_photos_of_a_public_concept(reader, storage):
    client, seeded = reader
    served = client.get(f"/api/v1/published/concept-photos/{seeded['valve first']}/preview")
    assert served.status_code == 200, served.text
    assert served.headers["content-type"] == "image/jpeg"
    assert storage == [f"hannah/concept-photos/test/{seeded['valve first']}.jpg"]

    # A third approved photo is past the display cap but still a public photo.
    assert client.get(f"/api/v1/published/concept-photos/{seeded['valve third']}/preview").status_code == 200

    for key in ("valve rejected", "valve removed", "valve proposed", "private", "proposed concept"):
        response = client.get(f"/api/v1/published/concept-photos/{seeded[key]}/preview")
        assert response.status_code == 404, key
    assert client.get("/api/v1/published/concept-photos/not-a-uuid/preview").status_code == 404
    assert client.get(f"/api/v1/published/concept-photos/{uuid.uuid4()}/preview").status_code == 404
    assert len(storage) == 2


def test_the_preview_needs_a_session(photos):
    engine, seeded = photos
    client, _Session = _client(engine)
    assert client.get(f"/api/v1/published/concept-photos/{seeded['valve first']}/preview").status_code == 401


def test_the_preview_refuses_bytes_that_are_not_the_image_they_claim(reader, monkeypatch):
    client, seeded = reader
    monkeypatch.setattr(
        published_routes, "download_object_bytes",
        lambda *, object_key, env_file: {"payload": b"<html><script>alert(1)</script></html>", "content_type": "image/png"},
    )
    response = client.get(f"/api/v1/published/concept-photos/{seeded['valve first']}/preview")
    assert response.status_code == 404
