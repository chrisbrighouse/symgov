"""X-01: Catalog preferences, saved views and clipboard are stored server-side.

They used to live in localStorage, so every account signed in on one browser
shared them. Preferences and saved views now follow the account; the clipboard
follows the account within its session scope (personal, or one organization).
"""
from __future__ import annotations

from datetime import datetime, timezone
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from symgov_backend.app import create_app
from symgov_backend.auth import AuthenticatedUser
from symgov_backend.catalog_workbench import MAX_SAVED_VIEWS, save_catalog_workbench_section
from symgov_backend.dependencies import get_current_user, get_db_session
from symgov_backend.models import CatalogWorkbenchClipboard, CatalogWorkbenchState


@compiles(JSONB, "sqlite")
def _compile_jsonb_for_sqlite(element, compiler, **kw):
    # The workbench columns are Postgres JSONB; map them to SQLite's JSON for
    # this in-memory route test.
    return "JSON"


def authenticated_user(user_id: uuid.UUID, organization_id: uuid.UUID | None = None) -> AuthenticatedUser:
    return AuthenticatedUser(
        id=str(user_id),
        email=f"{user_id}@symgov.local",
        display_name=f"User {user_id}",
        roles=("reviewer",),
        must_change_pin=False,
        session_mode="organization" if organization_id else "personal",
        active_organization_id=str(organization_id) if organization_id else None,
    )


def make_engine():
    engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    CatalogWorkbenchState.__table__.create(engine)
    CatalogWorkbenchClipboard.__table__.create(engine)
    return engine


def client_for(engine, user: AuthenticatedUser | None) -> TestClient:
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    app = create_app()

    def override_db_session():
        with Session() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_db_session
    app.dependency_overrides[get_current_user] = lambda: user
    return TestClient(app)


SAVED_VIEW = {
    "id": "view-1",
    "name": "Fire alarm DXF",
    "query": "detector",
    "facetFilters": {"availableFormats": ["DXF"], "catalogDisciplines": ["Fire"]},
    "preferredFormats": ["DXF"],
    "createdAt": "2026-09-25T10:00:00.000Z",
}
CLIPBOARD_ITEM = {
    "id": "0003-12",
    "displayName": "0003-12",
    "name": "Smoke Detector",
    "availableFormats": ["DXF", "SVG"],
}


def test_workbench_routes_require_an_authenticated_account():
    client = TestClient(create_app())

    responses = (
        client.get("/api/v1/published/workbench"),
        client.put("/api/v1/published/workbench/preferences", json={}),
        client.put("/api/v1/published/workbench/saved-views", json={"items": []}),
        client.put("/api/v1/published/workbench/clipboard", json={"items": []}),
    )

    for response in responses:
        assert response.status_code == 401


def test_a_new_account_starts_with_an_empty_workbench():
    engine = make_engine()
    response = client_for(engine, authenticated_user(uuid.uuid4())).get("/api/v1/published/workbench")

    assert response.status_code == 200
    assert response.json() == {
        "preferences": {"disciplines": [], "categories": [], "formats": [], "useCases": []},
        "savedViews": [],
        "clipboard": [],
        "updatedAt": None,
    }


def test_workbench_state_follows_the_account_and_never_another_account_on_the_same_browser():
    """The X-01 scenario: Connie's DXF preference must not reach Sacha."""
    engine = make_engine()
    connie = authenticated_user(uuid.uuid4())
    sacha = authenticated_user(uuid.uuid4())

    connie_client = client_for(engine, connie)
    saved = connie_client.put(
        "/api/v1/published/workbench/preferences",
        json={"disciplines": [], "categories": [], "formats": ["DXF"], "useCases": []},
    )
    assert saved.status_code == 200
    assert saved.json()["preferences"]["formats"] == ["DXF"]
    assert connie_client.put("/api/v1/published/workbench/saved-views", json={"items": [SAVED_VIEW]}).status_code == 200
    assert connie_client.put("/api/v1/published/workbench/clipboard", json={"items": [CLIPBOARD_ITEM]}).status_code == 200

    # A fresh client for the same account: another browser, or after sign-out.
    connie_elsewhere = client_for(engine, connie).get("/api/v1/published/workbench").json()
    assert connie_elsewhere["preferences"]["formats"] == ["DXF"]
    assert connie_elsewhere["savedViews"] == [SAVED_VIEW]
    assert connie_elsewhere["clipboard"] == [CLIPBOARD_ITEM]
    assert connie_elsewhere["updatedAt"]

    sacha_state = client_for(engine, sacha).get("/api/v1/published/workbench", params={"userId": connie.id}).json()
    assert sacha_state["preferences"]["formats"] == []
    assert sacha_state["savedViews"] == []
    assert sacha_state["clipboard"] == []


def test_each_section_is_saved_without_touching_the_others():
    engine = make_engine()
    client = client_for(engine, authenticated_user(uuid.uuid4()))
    client.put(
        "/api/v1/published/workbench/preferences",
        json={"disciplines": ["Fire"], "categories": [], "formats": ["DXF"], "useCases": []},
    )
    client.put("/api/v1/published/workbench/saved-views", json={"items": [SAVED_VIEW]})

    cleared = client.put("/api/v1/published/workbench/clipboard", json={"items": []}).json()

    assert cleared["preferences"]["disciplines"] == ["Fire"]
    assert cleared["savedViews"] == [SAVED_VIEW]
    assert cleared["clipboard"] == []


def test_workbench_payloads_are_bounded_and_strict():
    engine = make_engine()
    client = client_for(engine, authenticated_user(uuid.uuid4()))

    too_many_views = [{**SAVED_VIEW, "id": f"view-{index}"} for index in range(MAX_SAVED_VIEWS + 1)]
    rejected = (
        client.put("/api/v1/published/workbench/saved-views", json={"items": too_many_views}),
        client.put("/api/v1/published/workbench/saved-views", json={"items": [SAVED_VIEW, SAVED_VIEW]}),
        client.put("/api/v1/published/workbench/clipboard", json={"items": [CLIPBOARD_ITEM, CLIPBOARD_ITEM]}),
        client.put("/api/v1/published/workbench/preferences", json={"formats": ["DXF"], "userId": str(uuid.uuid4())}),
        client.put(
            "/api/v1/published/workbench/saved-views",
            json={"items": [{**SAVED_VIEW, "facetFilters": {"bad key!": ["x"]}}]},
        ),
    )

    for response in rejected:
        assert response.status_code == 422, response.text
    assert client.get("/api/v1/published/workbench").json()["savedViews"] == []


def test_the_clipboard_is_scoped_to_the_organization_session():
    """An organization's clipboard stays in that organization's sessions; the
    personal session and any other organization each have their own."""
    engine = make_engine()
    user_id = uuid.uuid4()
    acme = uuid.uuid4()
    bsco = uuid.uuid4()
    acme_item = {**CLIPBOARD_ITEM, "id": "ACME-0001-1", "name": "Acme private valve"}

    acme_session = client_for(engine, authenticated_user(user_id, acme))
    acme_session.put(
        "/api/v1/published/workbench/preferences",
        json={"disciplines": [], "categories": [], "formats": ["DXF"], "useCases": []},
    )
    assert acme_session.put("/api/v1/published/workbench/clipboard", json={"items": [acme_item]}).status_code == 200

    personal = client_for(engine, authenticated_user(user_id))
    assert personal.get("/api/v1/published/workbench").json()["clipboard"] == []
    personal.put("/api/v1/published/workbench/clipboard", json={"items": [CLIPBOARD_ITEM]})

    other_organization = client_for(engine, authenticated_user(user_id, bsco)).get("/api/v1/published/workbench").json()
    assert other_organization["clipboard"] == []
    # Preferences and saved views still follow the person everywhere.
    assert other_organization["preferences"]["formats"] == ["DXF"]

    assert client_for(engine, authenticated_user(user_id, acme)).get(
        "/api/v1/published/workbench"
    ).json()["clipboard"] == [acme_item]
    assert client_for(engine, authenticated_user(user_id)).get(
        "/api/v1/published/workbench"
    ).json()["clipboard"] == [CLIPBOARD_ITEM]

    # Saving the personal clipboard again updates its one row, not a second one.
    personal.put("/api/v1/published/workbench/clipboard", json={"items": []})
    with engine.connect() as connection:
        rows = connection.execute(CatalogWorkbenchClipboard.__table__.select()).all()
    assert len(rows) == 2


def test_a_personal_session_never_reads_an_organization_scope_from_request_input():
    engine = make_engine()
    user_id = uuid.uuid4()
    acme = uuid.uuid4()
    client_for(engine, authenticated_user(user_id, acme)).put(
        "/api/v1/published/workbench/clipboard", json={"items": [CLIPBOARD_ITEM]}
    )

    personal = client_for(engine, authenticated_user(user_id))
    assert personal.get(
        "/api/v1/published/workbench", params={"organizationId": str(acme)}
    ).json()["clipboard"] == []
    assert personal.put(
        "/api/v1/published/workbench/clipboard",
        json={"items": [], "organizationId": str(acme)},
    ).status_code == 422


def test_first_write_race_updates_the_row_the_other_request_created():
    user_id = uuid.uuid4()
    existing = CatalogWorkbenchState(
        user_id=user_id,
        preferences_json={"disciplines": [], "categories": [], "formats": ["SVG"], "useCases": []},
        saved_views_json=[],
        created_at=None,
        updated_at=None,
    )

    class RaceSession:
        def __init__(self):
            self.get_calls = 0
            self.commits = 0

        def get(self, _model, _identity, **_kwargs):
            self.get_calls += 1
            return None if self.get_calls == 1 else existing

        def add(self, _row):
            return None

        def commit(self):
            self.commits += 1
            if self.commits == 1:
                raise IntegrityError("INSERT INTO catalog_workbench_states ...", {}, Exception("duplicate key"))

        def rollback(self):
            return None

    race_session = RaceSession()
    save_catalog_workbench_section(race_session, user_id, "savedViews", [SAVED_VIEW])

    assert race_session.commits == 2
    assert existing.saved_views_json == [SAVED_VIEW]
    assert existing.preferences_json["formats"] == ["SVG"]


def test_the_database_allows_one_clipboard_per_scope():
    engine = make_engine()
    user_id = uuid.uuid4()
    organization_id = uuid.uuid4()

    def insert(organization):
        with engine.begin() as connection:
            connection.execute(
                CatalogWorkbenchClipboard.__table__.insert().values(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    organization_id=organization,
                    items_json=[],
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                )
            )

    insert(None)
    insert(organization_id)
    for duplicate_scope in (None, organization_id):
        try:
            insert(duplicate_scope)
        except IntegrityError:
            continue
        raise AssertionError(f"a second clipboard was allowed for scope {duplicate_scope}")
