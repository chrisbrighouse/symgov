from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend import runtime
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.routes import published as published_routes


def authenticated_user(user_id: uuid.UUID) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=str(user_id),
        email=f"{user_id}@symgov.local",
        display_name=f"User {user_id}",
        roles=("reviewer",),
        must_change_pin=False,
    )


def symbol_row(symbol_id: uuid.UUID | None = None):
    now = datetime(2026, 7, 18, 10, 0, tzinfo=timezone.utc)
    return SimpleNamespace(
        symbol_id=symbol_id or uuid.uuid4(),
        catalog_symbol_id="0003-12",
        slug="smoke-detector",
        canonical_name="Smoke Detector",
        category="symbol",
        discipline="Electrical",
        symbol_revision_id=uuid.uuid4(),
        revision_label="A",
        revision_created_at=now,
        payload_json={"package_display_id": "0003", "package_symbol_sequence": 12},
        rationale="Approved.",
        page_id=uuid.uuid4(),
        page_code="FA-12",
        page_title="Smoke Detector",
        effective_date=now.date(),
        page_updated_at=now,
        pack_id=uuid.uuid4(),
        pack_code="0003",
        pack_title="Fire Alarm Symbols",
        audience="public",
        pack_updated_at=now,
        sort_order=12,
        last_updated_at=now,
    )


def build_persistent_client(engine, user: AuthenticatedUser | None, row):
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


class CommitRaceSession:
    def __init__(self, row_after_rollback):
        self.row_after_rollback = row_after_rollback
        self.get_calls = 0
        self.get_identities = []
        self.rollback_calls = 0

    def get(self, _model, identity):
        self.get_calls += 1
        self.get_identities.append(identity)
        return None if self.get_calls == 1 else self.row_after_rollback

    def add(self, _row):
        return None

    def commit(self):
        raise IntegrityError(
            "INSERT INTO catalog_favourites ...",
            {},
            Exception('duplicate key value violates unique constraint "pk_catalog_favourites"'),
        )

    def rollback(self):
        self.rollback_calls += 1


def require_favourites_feature():
    try:
        from symgov_backend.catalog_favourites import add_catalog_favourite
        from symgov_backend.models import CatalogFavourite
    except (ImportError, ModuleNotFoundError) as exc:
        pytest.fail(f"Catalog Favourites backend is not implemented: {exc}")
    return add_catalog_favourite, CatalogFavourite


def test_concurrent_add_accepts_uniqueness_race_when_exact_favourite_now_exists():
    add_catalog_favourite, CatalogFavourite = require_favourites_feature()
    user_id = uuid.uuid4()
    symbol_id = uuid.uuid4()
    session = CommitRaceSession(
        CatalogFavourite(user_id=user_id, symbol_id=symbol_id, created_at=datetime.now(timezone.utc))
    )

    add_catalog_favourite(session, user_id, symbol_id)

    assert session.rollback_calls == 1
    assert session.get_identities == [
        {"user_id": user_id, "symbol_id": symbol_id},
        {"user_id": user_id, "symbol_id": symbol_id},
    ]


def test_add_does_not_swallow_unrelated_integrity_failure():
    add_catalog_favourite, _CatalogFavourite = require_favourites_feature()
    user_id = uuid.uuid4()
    symbol_id = uuid.uuid4()
    session = CommitRaceSession(None)

    with pytest.raises(IntegrityError):
        add_catalog_favourite(session, user_id, symbol_id)

    assert session.rollback_calls == 1
    assert session.get_calls == 2


def test_favourite_routes_require_an_authenticated_account():
    client = TestClient(create_app())

    responses = (
        client.get("/api/v1/published/favourites"),
        client.put("/api/v1/published/favourites/smoke-detector"),
        client.delete("/api/v1/published/favourites/smoke-detector"),
    )

    for response in responses:
        assert response.status_code == 401
        assert response.json()["detail"] == "Authentication required."


def test_favourites_are_persisted_isolated_idempotent_and_cannot_be_retargeted(monkeypatch):
    _add_catalog_favourite, CatalogFavourite = require_favourites_feature()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CatalogFavourite.__table__.create(engine)
    row = symbol_row()
    monkeypatch.setattr(
        published_routes,
        "_load_published_symbol_row",
        lambda _session, _ref: (row, "canonical"),
        raising=False,
    )

    user_a_id = uuid.uuid4()
    user_b_id = uuid.uuid4()
    user_a = authenticated_user(user_a_id)
    user_b = authenticated_user(user_b_id)

    first_session = build_persistent_client(engine, user_a, row)
    first_add = first_session.put(
        "/api/v1/published/favourites/smoke-detector",
        params={"userId": str(user_b_id)},
        json={"userId": str(user_b_id)},
    )
    assert first_add.status_code == 200
    assert first_add.json()["isFavourite"] is True
    assert first_session.put("/api/v1/published/favourites/smoke-detector").json()["isFavourite"] is True

    new_session_same_account = build_persistent_client(engine, user_a, row)
    assert new_session_same_account.get("/api/v1/published/favourites").json() == {
        "items": [{"symbolId": str(row.symbol_id)}]
    }

    other_account = build_persistent_client(engine, user_b, row)
    assert other_account.get(
        "/api/v1/published/favourites",
        params={"userId": str(user_a_id)},
    ).json() == {"items": []}
    crafted_remove = other_account.delete(
        "/api/v1/published/favourites/smoke-detector",
        params={"userId": str(user_a_id)},
    )
    assert crafted_remove.status_code == 200
    assert crafted_remove.json()["isFavourite"] is False
    assert new_session_same_account.get("/api/v1/published/favourites").json()["items"] == [
        {"symbolId": str(row.symbol_id)}
    ]

    direct_account_path = other_account.delete(
        f"/api/v1/published/favourites/{user_a_id}/smoke-detector"
    )
    assert direct_account_path.status_code in {404, 405}
    assert new_session_same_account.get("/api/v1/published/favourites").json()["items"] == [
        {"symbolId": str(row.symbol_id)}
    ]

    assert new_session_same_account.delete("/api/v1/published/favourites/smoke-detector").json()["isFavourite"] is False
    assert new_session_same_account.delete("/api/v1/published/favourites/smoke-detector").json()["isFavourite"] is False
    assert new_session_same_account.get("/api/v1/published/favourites").json() == {"items": []}


def test_published_catalog_results_expose_favourite_state_for_current_user(monkeypatch):
    row = symbol_row()

    class Rows:
        def all(self):
            return [row]

    class Session:
        def execute(self, _statement, _params=None):
            return Rows()

    monkeypatch.setattr(published_routes, "load_supplemental_photos", lambda _session, _rows: {})
    monkeypatch.setattr(published_routes, "load_comment_counts", lambda _session, _rows: {})
    monkeypatch.setattr(
        published_routes,
        "load_favourite_symbol_ids",
        lambda _session, user_id, _symbol_ids=None: {row.symbol_id} if user_id == str(row.symbol_id) else set(),
        raising=False,
    )

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: Session()
    app.dependency_overrides[get_current_user] = lambda: authenticated_user(row.symbol_id)
    client = TestClient(app)

    response = client.get("/api/v1/published/symbols")

    assert response.status_code == 200
    assert response.json()["items"][0]["isFavourite"] is True


def test_published_catalog_row_matches_uuid_favourite_when_sql_returns_text_id():
    symbol_id = uuid.uuid4()
    row = symbol_row()
    row.symbol_id = str(symbol_id)

    payload = published_routes.published_symbol_row(
        row,
        favourite_symbol_ids={symbol_id},
    )

    assert payload["isFavourite"] is True
    assert payload["links"] == {"web": "/#/s/0003-12"}


def test_owner_can_delete_saved_uuid_after_symbol_is_unpublished(monkeypatch):
    _add_catalog_favourite, CatalogFavourite = require_favourites_feature()
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CatalogFavourite.__table__.create(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    owner_id = uuid.uuid4()
    other_id = uuid.uuid4()
    stale_symbol_id = uuid.uuid4()
    with Session() as session:
        session.add(CatalogFavourite(user_id=owner_id, symbol_id=stale_symbol_id, created_at=datetime.now(timezone.utc)))
        session.commit()

    def unpublished(_session, _ref):
        raise published_routes.HTTPException(status_code=404, detail="Published symbol was not found.")

    monkeypatch.setattr(published_routes, "_load_published_symbol_row", unpublished)
    other_client = build_persistent_client(engine, authenticated_user(other_id), None)
    owner_client = build_persistent_client(engine, authenticated_user(owner_id), None)

    assert other_client.delete(f"/api/v1/published/favourites/{stale_symbol_id}").status_code == 404
    assert owner_client.put(f"/api/v1/published/favourites/{stale_symbol_id}").status_code == 404
    assert owner_client.get("/api/v1/published/favourites").json()["items"] == [
        {"symbolId": str(stale_symbol_id)}
    ]
    response = owner_client.delete(f"/api/v1/published/favourites/{stale_symbol_id}")

    assert response.status_code == 200
    assert response.json() == {"symbolId": str(stale_symbol_id), "isFavourite": False}
    assert owner_client.get("/api/v1/published/favourites").json() == {"items": []}


def test_published_symbol_detail_returns_resolved_by_for_canonical_and_slug_refs(monkeypatch):
    row = symbol_row(symbol_id=uuid.uuid4())

    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class Session:
        def __init__(self):
            self.calls = []

        def execute(self, _statement, params=None):
            self.calls.append(dict(params or {}))
            return Rows([row])

    def resolver(_session, symbol_ref, **_kwargs):
        if symbol_ref == row.catalog_symbol_id:
            matched_by = "canonical"
        else:
            matched_by = "slug"
        return SimpleNamespace(symbol_id=row.symbol_id, catalog_symbol_id=row.catalog_symbol_id, matched_by=matched_by)

    monkeypatch.setattr(published_routes, "resolve_catalog_symbol", resolver)
    monkeypatch.setattr(published_routes, "load_supplemental_photos", lambda _session, _rows: {})
    monkeypatch.setattr(published_routes, "load_comment_counts", lambda _session, _rows: {})
    monkeypatch.setattr(published_routes, "load_favourite_symbol_ids", lambda _session, _user_id, _symbol_ids=None: set())

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: Session()
    app.dependency_overrides[get_current_user] = lambda: authenticated_user(uuid.uuid4())
    client = TestClient(app)

    canonical = client.get(f"/api/v1/published/symbols/{row.catalog_symbol_id}")
    slug = client.get(f"/api/v1/published/symbols/{row.slug}")

    assert canonical.status_code == 200
    assert canonical.json()["resolvedBy"] == "canonical"
    assert canonical.json()["item"]["catalogSymbolId"] == row.catalog_symbol_id
    assert canonical.json()["item"]["links"] == {"web": f"/#/s/{row.catalog_symbol_id}"}
    assert slug.status_code == 200
    assert slug.json()["resolvedBy"] == "slug"


def test_published_detail_not_found_and_unavailable_preserve_code_and_detail(monkeypatch):
    class Rows:
        def all(self):
            return []

    class Session:
        def execute(self, _statement, _params=None):
            return Rows()

    symbol_id = uuid.uuid4()
    monkeypatch.setattr(
        published_routes,
        "resolve_catalog_symbol",
        lambda _session, symbol_ref, **_kwargs: None if symbol_ref == "missing" else SimpleNamespace(symbol_id=symbol_id, catalog_symbol_id="S-000001", matched_by="canonical"),
    )

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: Session()
    app.dependency_overrides[get_current_user] = lambda: authenticated_user(uuid.uuid4())
    client = TestClient(app)

    missing = client.get("/api/v1/published/symbols/missing")
    unavailable = client.get("/api/v1/published/symbols/S-000001")

    assert missing.status_code == 404
    assert missing.json() == {
        "error": "not_found",
        "detail": "Published symbol was not found.",
        "code": "catalog_symbol_not_found",
    }
    assert unavailable.status_code == 404
    assert unavailable.json() == {
        "error": "not_found",
        "detail": "This Catalog symbol is not currently available.",
        "code": "catalog_symbol_unavailable",
    }


def test_published_supplemental_photo_preview_uses_resolver_and_published_projection(monkeypatch):
    row = symbol_row(symbol_id=uuid.uuid4())
    attachment_id = uuid.uuid4()
    photo = SimpleNamespace(
        id=uuid.uuid4(),
        symbol_id=row.symbol_id,
        symbol_revision_id=row.symbol_revision_id,
        status="attached",
        attachment_id=attachment_id,
        object_key="photos/pump.png",
    )

    class Rows:
        def __init__(self, rows):
            self._rows = rows

        def all(self):
            return list(self._rows)

    class PhotoQuery:
        def __init__(self, photo_row):
            self.photo_row = photo_row

        def join(self, *_args, **_kwargs):
            return self

        def filter(self, *_args, **_kwargs):
            return self

        def one_or_none(self):
            return self.photo_row

    class AttachmentQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def one_or_none(self):
            return SimpleNamespace(
                id=attachment_id,
                object_key=photo.object_key,
                parent_type="symbol_revision",
                parent_id=row.symbol_revision_id,
                content_type="image/png",
            )

    class Session:
        def __init__(self):
            self.resolved_symbol_ids = []

        def execute(self, _statement, params=None):
            payload = dict(params or {})
            self.resolved_symbol_ids.append(payload.get("symbol_id"))
            return Rows([row])

        def query(self, model):
            if model is published_routes.HannahPhotoCandidate:
                return PhotoQuery(photo)
            if model is published_routes.Attachment:
                return AttachmentQuery()
            raise AssertionError(f"unexpected query model {model}")

    monkeypatch.setattr(
        published_routes,
        "resolve_catalog_symbol",
        lambda _session, _ref, **_kwargs: SimpleNamespace(symbol_id=row.symbol_id, catalog_symbol_id=row.catalog_symbol_id, matched_by="canonical"),
    )
    monkeypatch.setattr(
        published_routes,
        "download_object_bytes",
        lambda **_kwargs: {"payload": b"\x89PNG\r\n\x1a\nimage", "content_type": "image/png"},
    )

    session = Session()
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: session
    app.dependency_overrides[get_current_user] = lambda: authenticated_user(uuid.uuid4())
    client = TestClient(app)

    response = client.get(
        f"/api/v1/published/symbols/{row.catalog_symbol_id}/supplemental-photos/{photo.id}/preview"
    )

    assert response.status_code == 200
    assert response.content == b"\x89PNG\r\n\x1a\nimage"
    assert response.headers["content-type"].startswith("image/png")
    assert response.headers["content-security-policy"] == "sandbox"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert session.resolved_symbol_ids and str(session.resolved_symbol_ids[0]) == str(row.symbol_id)


def _supplemental_preview_boundary_client(monkeypatch, *, attachment_overrides=None, payload=None):
    row = symbol_row(symbol_id=uuid.uuid4())
    attachment_id = uuid.uuid4()
    candidate = SimpleNamespace(
        id=uuid.uuid4(),
        symbol_id=row.symbol_id,
        symbol_revision_id=row.symbol_revision_id,
        status="attached",
        attachment_id=attachment_id,
        object_key="photos/boundary.png",
    )
    attachment_values = {
        "id": attachment_id,
        "object_key": candidate.object_key,
        "parent_type": "symbol_revision",
        "parent_id": row.symbol_revision_id,
        "content_type": "image/png",
    }
    attachment_values.update(attachment_overrides or {})
    attachment = SimpleNamespace(**attachment_values)

    class Rows:
        def all(self):
            return [row]

    class BoundQuery:
        def __init__(self, record):
            self.record = record
            self.criteria = []

        def filter(self, *criteria):
            self.criteria.extend(criteria)
            return self

        def one_or_none(self):
            for criterion in self.criteria:
                key = getattr(getattr(criterion, "left", None), "key", None)
                if key is None or not hasattr(self.record, key):
                    continue
                actual = getattr(self.record, key)
                expected = getattr(getattr(criterion, "right", None), "value", None)
                operator_name = getattr(getattr(criterion, "operator", None), "__name__", "")
                if operator_name == "eq" and str(actual) != str(expected):
                    return None
                if operator_name in {"is_not", "is_not_distinct_from"} and expected is None and actual is None:
                    return None
            return self.record

    class Session:
        def execute(self, *_args, **_kwargs):
            return Rows()

        def query(self, model):
            if model is published_routes.HannahPhotoCandidate:
                return BoundQuery(candidate)
            if model is published_routes.Attachment:
                return BoundQuery(attachment)
            raise AssertionError(f"unexpected query model {model}")

    monkeypatch.setattr(
        published_routes,
        "resolve_catalog_symbol",
        lambda *_args, **_kwargs: SimpleNamespace(
            symbol_id=row.symbol_id,
            catalog_symbol_id=row.catalog_symbol_id,
            matched_by="canonical",
        ),
    )
    downloads = []

    def download(**kwargs):
        downloads.append(kwargs["object_key"])
        return payload or {"payload": b"\x89PNG\r\n\x1a\nimage", "content_type": "image/png"}

    monkeypatch.setattr(published_routes, "download_object_bytes", download)
    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: Session()
    app.dependency_overrides[get_current_user] = lambda: authenticated_user(uuid.uuid4())
    client = TestClient(app)
    url = f"/api/v1/published/symbols/{row.catalog_symbol_id}/supplemental-photos/{candidate.id}/preview"
    return client, url, downloads


@pytest.mark.parametrize(
    "attachment_overrides",
    [
        {"id": uuid.uuid4()},
        {"object_key": "private/unrelated.png"},
        {"parent_type": "governed_symbol"},
        {"parent_id": uuid.uuid4()},
    ],
)
def test_supplemental_preview_rejects_cross_object_and_parent_mismatches(
    monkeypatch,
    attachment_overrides,
):
    client, url, downloads = _supplemental_preview_boundary_client(
        monkeypatch,
        attachment_overrides=attachment_overrides,
    )

    response = client.get(url)

    assert response.status_code == 404
    assert downloads == []


def test_supplemental_preview_rejects_invalid_or_mislabeled_active_content(monkeypatch):
    client, url, _downloads = _supplemental_preview_boundary_client(
        monkeypatch,
        payload={
            "payload": b"<html><script>alert(1)</script></html>",
            "content_type": "image/png",
        },
    )

    response = client.get(url)

    assert response.status_code == 404


def test_hannah_attached_candidate_persistence_rejects_independent_attachment_fields():
    symbol_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    attachment_id = uuid.uuid4()
    attachment = SimpleNamespace(
        id=attachment_id,
        object_key="private/unrelated.png",
        parent_type="symbol_revision",
        parent_id=revision_id,
    )
    revision = SimpleNamespace(id=revision_id, symbol_id=symbol_id)

    class Session:
        def get(self, model, identity):
            if model is published_routes.Attachment and identity == attachment_id:
                return attachment
            if model is runtime.SymbolRevision and identity == revision_id:
                return revision
            return None

    with pytest.raises(RuntimeError, match="attachment ownership"):
        runtime.validate_hannah_attached_photo(
            Session(),
            symbol_id=symbol_id,
            symbol_revision_id=revision_id,
            attachment_id=attachment_id,
            object_key="photos/claimed.png",
        )


def test_published_symbol_lookup_failure_maps_to_bounded_503_without_leaks(monkeypatch):
    class Session:
        def execute(self, *_args, **_kwargs):
            raise AssertionError("must fail before projection query")

    monkeypatch.setattr(
        published_routes,
        "resolve_catalog_symbol",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("database timeout password=secret")),
    )

    app = create_app()
    app.dependency_overrides[get_db_session] = lambda: Session()
    app.dependency_overrides[get_current_user] = lambda: authenticated_user(uuid.uuid4())
    client = TestClient(app)

    response = client.get("/api/v1/published/symbols/0003-12")

    assert response.status_code == 503
    assert response.json() == {
        "error": "request_error",
        "detail": "Catalog symbol lookup is temporarily unavailable. Please retry.",
        "code": "catalog_symbol_lookup_unavailable",
    }
