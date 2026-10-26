"""The stored image behind a private symbol's asset, for the Private review
queue and the submitter's own Submissions view, through the real app on the
in-memory SQLite client from `test_wp55_organization_symbols_api.py`.

It is visible to exactly the people who can see the draft (the submitter, Org
Reviewers and Org Admins of the owning organization), only while the asset is
still listed on that revision, and only when the stored bytes are an allowed
image. Everything else is a 404.
"""

from __future__ import annotations

import hashlib
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.models import Attachment, SymbolRevision  # noqa: E402
from symgov_backend.routes import organization_symbols as organization_symbols_routes  # noqa: E402
from test_user_notifications_api import DRAFT, _world  # noqa: E402

SVG = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><circle cx="5" cy="5" r="4"/></svg>'


@pytest.fixture
def stored_objects(monkeypatch):
    """Object storage, keyed by object key: {"payload": bytes, "content_type": str}."""
    objects: dict[str, dict] = {}

    def download(*, object_key, env_file=None, max_bytes=None):
        return objects[object_key]

    monkeypatch.setattr(organization_symbols_routes, "download_object_bytes", download)
    return objects


def _attach(Session, stored_objects, revision_id, *, filename="hydrant.svg", payload=SVG, content_type="image/svg+xml"):
    """What `attach_asset` leaves behind: an Attachment row, the stored object
    and the revision's payload entry. Written directly because the real
    upload goes through the storage bridge."""
    attachment_id = uuid.uuid4()
    object_key = f"organization-symbols/test/{revision_id}/{attachment_id.hex[:8]}-{filename}"
    digest = hashlib.sha256(payload).hexdigest()
    with Session() as session:
        session.add(Attachment(
            id=attachment_id, parent_type="symbol_revision", parent_id=uuid.UUID(revision_id), filename=filename,
            object_key=object_key, content_type=content_type, size_bytes=len(payload), sha256=digest,
            created_at=datetime.now(timezone.utc),
        ))
        revision = session.get(SymbolRevision, uuid.UUID(revision_id))
        payload_json = dict(revision.payload_json or {})
        payload_json["assets"] = [*(payload_json.get("assets") or []), {
            "attachment_id": str(attachment_id), "object_key": object_key, "filename": filename,
            "content_type": content_type, "role": "source", "sha256": digest, "size_bytes": len(payload),
        }]
        revision.payload_json = payload_json
        session.commit()
    stored_objects[object_key] = {"payload": payload, "content_type": content_type}
    return str(attachment_id)


def _setup(stored_objects):
    clients, Session, ids, acme_id = _world()
    Attachment.__table__.create(Session.kw["bind"])
    draft = clients["member"].post("/api/v1/organization-symbols", json=DRAFT).json()
    symbol_id, revision_id = draft["id"], draft["currentRevisionId"]
    attachment_id = _attach(Session, stored_objects, revision_id)
    url = f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/assets/{attachment_id}/preview"
    return clients, Session, symbol_id, revision_id, attachment_id, url


def test_the_submitter_reviewers_and_admins_see_the_stored_image_and_no_one_else_does(stored_objects):
    clients, _Session, symbol_id, revision_id, _attachment_id, url = _setup(stored_objects)
    clients["member"].post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})

    for name in ("member", "rex", "ada"):
        response = clients[name].get(url)
        assert response.status_code == 200, (name, response.text)
        assert response.content == SVG
        assert response.headers["content-type"] == "image/svg+xml"
        assert response.headers["content-security-policy"] == "sandbox"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"

    # Pat is a plain member of the same organization; Olly reviews for another.
    for name in ("pat", "olly"):
        assert clients[name].get(url).status_code == 404, name


def test_the_detail_response_names_the_asset_the_preview_route_takes(stored_objects):
    clients, _Session, symbol_id, _revision_id, attachment_id, _url = _setup(stored_objects)
    [asset] = clients["member"].get(f"/api/v1/organization-symbols/{symbol_id}").json()["currentRevision"]["assets"]
    assert (asset["id"], asset["filename"]) == (attachment_id, "hydrant.svg")


def test_a_removed_asset_stops_previewing(stored_objects):
    clients, _Session, symbol_id, revision_id, attachment_id, url = _setup(stored_objects)
    base = f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/assets/{attachment_id}"
    assert clients["member"].delete(base).status_code == 200
    assert clients["member"].get(url).status_code == 404


def test_an_attachment_that_does_not_belong_to_the_revision_is_not_served(stored_objects):
    clients, Session, symbol_id, revision_id, attachment_id, url = _setup(stored_objects)
    with Session() as session:
        session.get(Attachment, uuid.UUID(attachment_id)).parent_id = uuid.uuid4()
        session.commit()
    assert clients["member"].get(url).status_code == 404

    # Unknown and malformed ids are a 404 too, not a 400 or 500.
    base = f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/assets"
    assert clients["member"].get(f"{base}/{uuid.uuid4()}/preview").status_code == 404
    assert clients["member"].get(f"{base}/not-a-uuid/preview").status_code == 404


def test_stored_bytes_that_are_not_the_declared_image_are_not_served(stored_objects):
    clients, Session, symbol_id, revision_id, _attachment_id, _url = _setup(stored_objects)
    base = f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/assets"

    html_id = _attach(Session, stored_objects, revision_id, filename="page.svg", payload=b"<html><script></script></html>")
    assert clients["member"].get(f"{base}/{html_id}/preview").status_code == 404

    png_as_svg_id = _attach(Session, stored_objects, revision_id, filename="mislabelled.svg", payload=b"\x89PNG\r\n\x1a\n0000")
    assert clients["member"].get(f"{base}/{png_as_svg_id}/preview").status_code == 404
