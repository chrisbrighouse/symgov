"""Stage 9 WP9.3 regression: browse-tier `ProductUsageEvent` emission
(preview, download, Favorite change, passive context resolution) against a
real disposable PostgreSQL container, driven through the real HTTP routes.

Per the Stage 9 plan
(`docs/plans/2026-09-03-symbol-set-management-stage9-implementation-plan.md`,
WP9.3) and `backend/symgov_backend/product_usage_events.py`'s own module
docstring, this package does **not** add a generic `POST /api/v1/catalog/usage`
self-report endpoint (the literal spec Appendix A line 936 wording) --
starting the implementation found that every event in this tier already has
a real, existing server-side observation point that the frontend already
calls for its own functional purpose, and I-13's own "server-derived" wording
favours wiring emission directly into those routes over a client-self-report
endpoint a browser could spoof or simply omit:

- `symbol_previewed`  -> `GET /published/symbols/{id}/preview` (routes/published.py)
- `symbol_downloaded` -> `POST /catalog/symbols/download` (routes/catalog.py), gated
  on the actor being a real authenticated browser session, not an API key
- `favorite_changed`  -> `PUT`/`DELETE /published/favourites/{symbol_ref}` (routes/published.py)
- `context_resolved`  -> `GET /org/me/symbol-context` (symbol_context_service.get_context) --
  the *passive* resolution path, distinct from WP9.2's already-committed
  `project_selected`/`set_selected` (explicit switches, governance tier)

Unlike WP9.2's governance tier (`record_governance_usage_event`, fixed
`session_mode='organization'`), this tier's `record_browse_usage_event`/
`record_browse_usage_event_for_session` derive `session_mode`/`organization_id`
from the acting user's own *literal* session state, since these describe
what the user actually did in their own session -- proven here by exercising
the very same public symbol from both a `personal`-mode session (no
organization at all) and an `organization`-mode session (an org-wide private
symbol, viewed by its own owning organization).

Attaching a real image asset through the real HTTP endpoint requires this
test to fake `RuntimePersistenceBridge`/`download_object_bytes` the same way
`tests/test_wp53_organization_symbol_drafts.py`'s own `_FakeStorageBridge`
does (object storage is a real external S3-compatible service; tests must
not perform network I/O). Separately -- and orthogonally to WP9.3's own
scope -- the organization-symbol-draft-to-publication pipeline does not
itself translate a draft's flat `assets` list into the `visual_assets`
structure `published.py`'s preview/download code reads; this test patches
that shape directly onto the real `symbol_revisions.payload_json` row after
promotion (referencing the same real `object_key`/`Attachment` row the
asset-upload step already created), to isolate and prove WP9.3's own
emission wiring rather than also re-proving or working around that
pre-existing, unrelated pipeline gap.
"""

from __future__ import annotations

import base64
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from fastapi.testclient import TestClient  # noqa: E402

import symgov_backend.organization_symbol_drafts as organization_symbol_drafts  # noqa: E402
import symgov_backend.routes.catalog as catalog_routes  # noqa: E402
import symgov_backend.routes.published as published_routes  # noqa: E402
from symgov_backend.app import create_app  # noqa: E402
from symgov_backend.dependencies import get_db_session  # noqa: E402
from symgov_backend.models import ProductUsageEvent, User  # noqa: E402
from symgov_backend.settings import SymgovAPISettings, get_settings  # noqa: E402

NEW_MIGRATION_HEAD = "20260904_0042"

psycopg = pytest.importorskip("psycopg")

# A real, minimal 1x1 PNG -- lifted from
# tests/test_wp53_organization_symbol_drafts.py's own known-good fixture,
# since `attach_asset` validates real image bytes, not just a declared
# content type.
PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d494844520000000100000001080600000"
    "01f15c4890000000a49444154789c6360000002000100e221bc330000"
    "0000494e44ae426082"
)


class _FakeStorageBridge:
    """Stand-in for `RuntimePersistenceBridge`'s storage calls -- object
    storage is a real external S3-compatible service; tests must not
    perform network I/O. `create_attachment` still performs a real insert
    against this test's own disposable Postgres container, so the
    `Attachment` row is genuinely exercised; only the outbound object-store
    PUT is faked. Mirrors `test_wp53_organization_symbol_drafts.py`'s own
    fixture of the same name."""

    def __init__(self, engine):
        self._engine = engine

    def create_attachment(self, *, parent_type, parent_id, filename, object_key, content_type, size_bytes, sha256=None):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        attachment_id = uuid.uuid4()
        with self._engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO attachments "
                    "(id,parent_type,parent_id,filename,object_key,content_type,size_bytes,sha256,created_at) "
                    "VALUES (:id,:parent_type,:parent_id,:filename,:object_key,:content_type,:size_bytes,:sha256,:now)"
                ),
                {
                    "id": attachment_id, "parent_type": parent_type, "parent_id": parent_id,
                    "filename": filename, "object_key": object_key, "content_type": content_type,
                    "size_bytes": size_bytes, "sha256": sha256, "now": now,
                },
            )
        return {"id": str(attachment_id), "object_key": object_key, "filename": filename}

    def upload_object_bytes(self, *, object_key, payload, content_type, env_file=None):
        return {"object_key": object_key, "size_bytes": len(payload)}


def _fake_download_object_bytes(*, object_key, env_file=None):
    return {"payload": PNG_BYTES, "content_type": "image/png"}


@pytest.fixture(scope="module")
def wp93_database():
    with _database("symgov-wp93") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        with psycopg.connect(raw_url, autocommit=True) as connection:
            for statement in (
                "GRANT SELECT, INSERT, UPDATE ON promotion_requests TO symgov_app",
                "GRANT SELECT, INSERT ON promotion_request_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON governed_symbols TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON symbol_revisions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_submissions TO symgov_app",
                "GRANT UPDATE (status, closed_at) ON organization_symbol_review_submissions TO symgov_app",
                "GRANT SELECT, INSERT ON organization_symbol_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON published_pages TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON pack_entries TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON publication_packs TO symgov_app",
                "GRANT SELECT, INSERT ON catalog_symbol_identifiers TO symgov_app",
                "GRANT USAGE, SELECT ON SEQUENCE catalog_symbol_id_seq TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_cases TO symgov_app",
                "GRANT SELECT, INSERT ON human_review_decisions TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON review_case_actions TO symgov_app",
                "GRANT SELECT, INSERT ON publication_approval_targets TO symgov_app",
                "GRANT SELECT, INSERT ON audit_events TO symgov_app",
                "GRANT SELECT ON active_public_symbol_projections TO symgov_app",
                "GRANT SELECT, INSERT, UPDATE ON attachments TO symgov_app",
                "GRANT SELECT, INSERT, DELETE ON catalog_favourites TO symgov_app",
            ):
                connection.execute(statement)
        yield engine, url, raw_url


def _client(engine):
    app = create_app()
    TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    settings = SymgovAPISettings(
        organizations_enabled=True,
        organization_symbols_enabled=True,
        platform_admin_enabled=True,
        symbol_sets_enabled=True,
        organization_pilot_codes=("acme", "symgov"),
    )

    def override_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db_session] = override_db
    app.dependency_overrides[get_settings] = lambda: settings
    return TestClient(app, headers={"origin": "http://testserver"}), TestingSessionLocal


def _login(client, email):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == 200, response.text
    return response.json()


def _email(Session, user_id) -> str:
    with Session() as session:
        return session.get(User, user_id).email


def _event(Session, *, event_type, **filters) -> ProductUsageEvent:
    with Session() as session:
        query = session.query(ProductUsageEvent).filter(ProductUsageEvent.event_type == event_type)
        for column, value in filters.items():
            query = query.filter(getattr(ProductUsageEvent, column) == value)
        return query.one()


def _create_org_symbol_with_real_asset(admin_client, engine, *, name):
    """Creates an organization-private draft, attaches a real (faked-storage)
    PNG asset to it while it's still a draft -- `attach_asset` only permits
    this while `lifecycle_state == 'draft'` -- and returns
    `(symbol_id, revision_id, object_key)`."""
    create = admin_client.post(
        "/api/v1/organization-symbols",
        json={"name": name, "category": "fire", "discipline": "civil", "summary": "A WP9.3 test symbol."},
    )
    assert create.status_code == 200, create.text
    symbol_id, revision_id = create.json()["id"], create.json()["currentRevisionId"]

    with patch.object(organization_symbol_drafts, "RuntimePersistenceBridge", lambda env_file=None: _FakeStorageBridge(engine)):
        asset = admin_client.post(
            f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/assets",
            json={
                "filename": "wp93.png",
                "contentType": "image/png",
                "contentBase64": base64.b64encode(PNG_BYTES).decode("ascii"),
                "role": "source",
            },
        )
    assert asset.status_code == 200, asset.text
    return symbol_id, revision_id, asset.json()["objectKey"]


def _patch_visual_assets(Session, revision_id: str, object_key: str) -> None:
    """Bridges the pre-existing gap (see module docstring) between a draft's
    flat `assets` list and the `visual_assets` shape `published.py`'s
    preview/download code actually reads, on the real row, referencing the
    real object_key/Attachment already created above."""
    with Session() as session:
        row = session.execute(
            text("SELECT payload_json FROM symbol_revisions WHERE id = :id"), {"id": revision_id}
        ).one()
        payload = dict(row.payload_json)
        payload["visual_assets"] = {
            "source_assets": [
                {"object_key": object_key, "filename": "wp93.png", "content_type": "image/png", "format": "png"}
            ]
        }
        session.execute(
            text("UPDATE symbol_revisions SET payload_json = :payload WHERE id = :id"),
            {"payload": json.dumps(payload), "id": revision_id},
        )
        session.commit()


def _promote_to_public(admin_client, reviewer_client, symbol_id, revision_id):
    submit = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit.status_code == 200, submit.text
    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text
    promo = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/promotion-requests",
        json={"reason": "Broadly useful.", "sharingAcknowledgment": True},
    )
    assert promo.status_code == 200, promo.text
    open_review = reviewer_client.post(f"/api/v1/organization-symbols/{symbol_id}/promotion-requests/{promo.json()['id']}/open-review")
    assert open_review.status_code == 200, open_review.text
    decision = reviewer_client.post(
        f"/api/v1/workspace/review-cases/{open_review.json()['reviewCaseId']}/decisions",
        json={"decisionCode": "approve"},
    )
    assert decision.status_code == 200, decision.text
    assert decision.json()["currentStage"] == "published"


def test_symbol_preview_and_download_emit_usage_events_for_personal_session(wp93_database):
    engine, _, _ = wp93_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp93admin-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.3 Admin", roles=[]
    )
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    reviewer_email = f"wp93reviewer-{uuid.uuid4().hex[:8]}@example.test"
    reviewer_id = _create_user_with_global_roles(Session, email=reviewer_email, display_name="WP9.3 Reviewer", roles=["reviewer"])
    reviewer_login = _login(reviewer_client, reviewer_email)
    assert reviewer_login["user"]["session"]["mode"] == "personal"

    symbol_id, revision_id, object_key = _create_org_symbol_with_real_asset(admin_client, engine, name="WP9.3 Preview Symbol")
    _promote_to_public(admin_client, reviewer_client, symbol_id, revision_id)
    _patch_visual_assets(Session, revision_id, object_key)

    with patch.object(published_routes, "download_object_bytes", _fake_download_object_bytes):
        preview = reviewer_client.get(f"/api/v1/published/symbols/{symbol_id}/preview")
    assert preview.status_code == 200, preview.text
    assert preview.content == PNG_BYTES

    preview_event = _event(Session, event_type="symbol_previewed", governed_symbol_id=uuid.UUID(symbol_id))
    assert preview_event.user_id == reviewer_id
    assert preview_event.session_mode == "personal"
    assert preview_event.organization_id is None
    assert preview_event.symbol_source == "public"
    assert preview_event.symbol_revision_id == uuid.UUID(revision_id)

    with patch.object(catalog_routes, "download_object_bytes", _fake_download_object_bytes):
        download = reviewer_client.post(
            "/api/v1/catalog/symbols/download", json={"symbolIds": [symbol_id], "format": "PNG"}
        )
    assert download.status_code == 200, download.text
    assert download.content == PNG_BYTES

    download_event = _event(Session, event_type="symbol_downloaded", governed_symbol_id=uuid.UUID(symbol_id))
    assert download_event.user_id == reviewer_id
    assert download_event.session_mode == "personal"
    assert download_event.organization_id is None
    assert download_event.symbol_source == "public"
    assert download_event.format == "png"


def test_favourite_add_and_remove_emit_usage_events(wp93_database):
    engine, _, _ = wp93_database
    admin_client, Session = _client(engine)
    reviewer_client, _ = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp93favadmin-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.3 Fav Admin", roles=[]
    )
    _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    reviewer_email = f"wp93favreviewer-{uuid.uuid4().hex[:8]}@example.test"
    reviewer_id = _create_user_with_global_roles(Session, email=reviewer_email, display_name="WP9.3 Fav Reviewer", roles=["reviewer"])
    _login(reviewer_client, reviewer_email)

    symbol_id, revision_id, _object_key = _create_org_symbol_with_real_asset(admin_client, engine, name="WP9.3 Favourite Symbol")
    _promote_to_public(admin_client, reviewer_client, symbol_id, revision_id)

    add = reviewer_client.put(f"/api/v1/published/favourites/{symbol_id}")
    assert add.status_code == 200, add.text
    assert add.json()["isFavourite"] is True

    added_event = _event(Session, event_type="favorite_changed", governed_symbol_id=uuid.UUID(symbol_id), favourite_action="added")
    assert added_event.user_id == reviewer_id
    assert added_event.session_mode == "personal"
    assert added_event.organization_id is None
    assert added_event.symbol_source == "public"

    remove = reviewer_client.delete(f"/api/v1/published/favourites/{symbol_id}")
    assert remove.status_code == 200, remove.text
    assert remove.json()["isFavourite"] is False

    removed_event = _event(Session, event_type="favorite_changed", governed_symbol_id=uuid.UUID(symbol_id), favourite_action="removed")
    assert removed_event.user_id == reviewer_id
    assert removed_event.session_mode == "personal"


def test_context_resolved_emits_usage_event_reflecting_real_resolution(wp93_database):
    engine, _, _ = wp93_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp93context-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.3 Context Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin")
    _login(admin_client, _email(Session, admin_id))

    context = admin_client.get("/api/v1/org/me/symbol-context")
    assert context.status_code == 200, context.text
    assert context.json()["reason"] == "none"

    none_event = _event(Session, event_type="context_resolved", user_id=admin_id, context_resolution_basis="none")
    assert none_event.organization_id == organization_id
    assert none_event.session_mode == "organization"
    assert none_event.project_id is None


def test_symbol_preview_in_organization_session_records_organization_scope(wp93_database):
    engine, _, _ = wp93_database
    admin_client, Session = _client(engine)

    admin_id = _create_user_with_global_roles(
        Session, email=f"wp93orgpreview-{uuid.uuid4().hex[:8]}@example.test", display_name="WP9.3 Org Preview Admin", roles=[]
    )
    organization_id = _add_membership(Session, admin_id, code="acme", base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _login(admin_client, _email(Session, admin_id))

    symbol_id, revision_id, object_key = _create_org_symbol_with_real_asset(
        admin_client, engine, name="WP9.3 Org-Wide Preview Symbol"
    )
    submit = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/revisions/{revision_id}/submit", json={})
    assert submit.status_code == 200, submit.text
    decide = admin_client.post(
        f"/api/v1/organization-symbols/{symbol_id}/review-submissions/{submit.json()['id']}/decision",
        json={"decision": "approved"},
    )
    assert decide.status_code == 200, decide.text
    toggle = admin_client.post(f"/api/v1/organization-symbols/{symbol_id}/organization-wide", json={"enabled": True})
    assert toggle.status_code == 200, toggle.text
    _patch_visual_assets(Session, revision_id, object_key)

    with patch.object(published_routes, "download_object_bytes", _fake_download_object_bytes):
        preview = admin_client.get(f"/api/v1/published/symbols/{symbol_id}/preview")
    assert preview.status_code == 200, preview.text

    event = _event(Session, event_type="symbol_previewed", governed_symbol_id=uuid.UUID(symbol_id))
    assert event.user_id == admin_id
    assert event.session_mode == "organization"
    assert event.organization_id == organization_id
    assert event.symbol_source == "organization_private"
    assert event.symbol_revision_id == uuid.UUID(revision_id)
