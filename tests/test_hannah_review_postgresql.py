"""The steward's Hannah routes on a real PostgreSQL (WP-H4).

Driven over HTTP as a Workspace admin, with storage, the image download and
the background search faked:

- the concept list puts steward work first and names symbols by catalogue ID;
- approving stores a validated copy and keeps the next best proposed; a
  third approval is refused; rejecting promotes the next candidate; removing
  deletes the stored copy; every decision is audited with the steward;
- a correction marks the decision `steward`, excludes a "no", and refuses
  bad search terms;
- "Search now" queues a running card and hands it to the background runner,
  and refuses an excluded concept or one already being searched.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pytest
from PIL import Image
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_catalog_browse_search_postgresql import search_database  # noqa: E402,F401  (fixture)
from test_hannah_photos_postgresql import FakeVision, _run, targets  # noqa: E402,F401  (fixtures)
from test_hannah_targets_postgresql import concepts  # noqa: E402,F401  (fixture)
from test_wp74_symbol_demotion_postgresql import _client, _create_user_with_global_roles  # noqa: E402
from test_wp81_catalog_organization_context_postgresql import _login  # noqa: E402

from symgov_backend.routes import workspace as workspace_routes  # noqa: E402

pytest.importorskip("psycopg")

ROOT = "/api/v1/workspace/hannah"


def _jpeg() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), (90, 120, 150)).save(buffer, format="JPEG")
    return buffer.getvalue()


@pytest.fixture(scope="module")
def admin(targets):
    engine, ids = targets
    _run(engine, FakeVision())
    client, Session = _client(engine)
    user_id = _create_user_with_global_roles(Session, email="hannah-steward@example.test", display_name="Steward", roles=["admin"])
    _login(client, "hannah-steward@example.test")
    return engine, client, user_id


@pytest.fixture()
def fakes(monkeypatch):
    stored, deleted, searched = {}, [], []
    monkeypatch.setattr(workspace_routes, "default_fetch_bytes", lambda url: (_jpeg(), "image/jpeg"))
    monkeypatch.setattr(
        workspace_routes, "upload_object_bytes",
        lambda *, object_key, payload, content_type, env_file: stored.update({object_key: content_type}),
    )
    monkeypatch.setattr(
        workspace_routes.RuntimePersistenceBridge, "delete_object",
        lambda self, *, object_key, env_file=None: deleted.append(object_key),
    )
    monkeypatch.setattr(
        workspace_routes.hannah_runner, "run_cards",
        lambda paths, *, runtime_root, db_env_file: searched.extend(paths),
    )
    monkeypatch.setattr(workspace_routes, "HANNAH_RUNTIME_ROOT", Path("/tmp") / "hannah-review-test-runtime")
    return {"stored": stored, "deleted": deleted, "searched": searched}


def _valve(client):
    body = client.get(f"{ROOT}/concepts", params={"q": "SGC-90000001"}).json()
    return body["items"][0]


def _photo(client, target_id, title):
    items = client.get(f"{ROOT}/concepts/{target_id}/photos").json()["items"]
    return next(item for item in items if item["title"] == title)


def _decide(client, photo_id, decision):
    return client.post(f"{ROOT}/photos/{photo_id}/decision", json={"decision": decision})


def test_the_list_puts_steward_work_first_and_names_symbols_by_catalogue_id(admin):
    _engine, client, _user = admin
    body = client.get(f"{ROOT}/concepts").json()
    assert body["items"][0]["conceptCode"] == "SGC-90000001"
    assert body["summary"]["has_proposals"] == 1 and body["summary"]["excluded"] == 1
    valve = body["items"][0]
    assert valve["name"] == "Gate valve" and valve["exampleSymbolIds"] == ["TST-10", "TST-2"]
    assert [photo["status"] for photo in valve["photos"]] == ["proposed", "proposed"]
    assert valve["photoCounts"]["filtered"] == 1
    assert client.get(f"{ROOT}/concepts", params={"status": "nonsense"}).status_code == 422


def test_decisions_store_audit_and_re_rank(admin, fakes):
    engine, client, user_id = admin
    target_id = _valve(client)["targetId"]
    first = _photo(client, target_id, "Gate valve photo 1.jpg")
    second = _photo(client, target_id, "Gate valve photo 2.jpg")
    third = _photo(client, target_id, "Gate valve photo 3.jpg")

    approved = _decide(client, first["id"], "approve")
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "has_proposals"
    assert list(fakes["stored"]) == [f"hannah/concept-photos/SGC-90000001/{first['id']}.jpg"]
    assert [(photo["title"], photo["status"]) for photo in approved.json()["photos"]] == [
        ("Gate valve photo 1.jpg", "approved"), ("Gate valve photo 2.jpg", "proposed"),
    ]

    rejected = _decide(client, second["id"], "reject").json()
    assert [(photo["title"], photo["status"]) for photo in rejected["photos"]] == [
        ("Gate valve photo 1.jpg", "approved"), ("Gate valve photo 3.jpg", "proposed"),
    ]
    assert _decide(client, second["id"], "reject").status_code == 409

    assert _decide(client, third["id"], "approve").json()["status"] == "has_photos"
    assert _decide(client, second["id"], "approve").status_code == 409

    removed = _decide(client, first["id"], "remove").json()
    assert fakes["deleted"] == [f"hannah/concept-photos/SGC-90000001/{first['id']}.jpg"]
    assert _photo(client, target_id, "Gate valve photo 1.jpg")["status"] == "removed"
    assert removed["status"] == "has_photos"

    with engine.connect() as connection:
        actions = connection.execute(
            text("SELECT action, actor_id FROM audit_events WHERE action LIKE 'hannah_photo_%' ORDER BY created_at, action")
        ).all()
    assert sorted(action for action, _ in actions) == [
        "hannah_photo_approved", "hannah_photo_approved", "hannah_photo_rejected", "hannah_photo_removed",
    ]
    assert {actor for _, actor in actions} == {user_id}


def test_a_correction_is_the_stewards_and_bad_terms_are_refused(admin):
    _engine, client, _user = admin
    pump = client.get(f"{ROOT}/concepts", params={"q": "SGC-90000002"}).json()["items"][0]

    assert client.patch(f"{ROOT}/concepts/{pump['targetId']}", json={"searchTerms": ["ab"]}).status_code == 422
    corrected = client.patch(f"{ROOT}/concepts/{pump['targetId']}", json={"searchTerms": ["Centrifugal  Pump", "process pump"]})
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["searchTerms"] == ["centrifugal pump", "process pump"]
    assert (corrected.json()["photographableBasis"], corrected.json()["status"]) == ("steward", "pending")

    excluded = client.patch(f"{ROOT}/concepts/{pump['targetId']}", json={"photographable": "no"}).json()
    assert excluded["status"] == "excluded"
    restored = client.patch(f"{ROOT}/concepts/{pump['targetId']}", json={"photographable": "yes"}).json()
    assert restored["status"] == "pending"


def test_search_now_queues_a_running_card_once(admin, fakes):
    engine, client, _user = admin
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO agent_definitions (id, slug, display_name, role, model, status, queue_family, created_at, updated_at) "
                "VALUES (gen_random_uuid(), 'hannah', 'Hannah', 'curation', 'none', 'active', 'curation', now(), now()) "
                "ON CONFLICT (slug) DO NOTHING"
            )
        )
        connection.execute(text("DELETE FROM agent_queue_items"))
    tag = client.get(f"{ROOT}/concepts", params={"q": "SGC-90000003"}).json()["items"][0]
    assert client.post(f"{ROOT}/concepts/{tag['targetId']}/search").status_code == 409

    pump = client.get(f"{ROOT}/concepts", params={"q": "SGC-90000002"}).json()["items"][0]
    started = client.post(f"{ROOT}/concepts/{pump['targetId']}/search")
    assert started.status_code == 200, started.text
    assert len(fakes["searched"]) == 1 and fakes["searched"][0].endswith(f"{started.json()['queueItemId']}.json")
    with engine.connect() as connection:
        status = connection.execute(
            text("SELECT status FROM agent_queue_items WHERE id = :id"), {"id": started.json()["queueItemId"]}
        ).scalar()
    assert status == "running"
    assert client.post(f"{ROOT}/concepts/{pump['targetId']}/search").status_code == 409
