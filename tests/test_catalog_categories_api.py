"""The Catalog's category picklist (2026-10-25): Public Reviewers add to it,
everyone signed in reads it, and a Platform Admin reviews additions and the
values reviewers typed before it."""

from __future__ import annotations

import sys
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import CheckConstraint

sys.path.insert(0, str(Path(__file__).resolve().parent))

from symgov_backend.auth import upsert_user  # noqa: E402
from symgov_backend.catalog_taxonomy import CATALOG_CATEGORY_ORDER  # noqa: E402
from symgov_backend.models import (  # noqa: E402
    CatalogCategoryAddition,
    GovernedSymbol,
    ReviewSymbolPropertyOption,
    UserSubscription,
)
from test_platform_organizations_api import (  # noqa: E402
    _build_client,
    _login_and_select_org,
    _seed_symgov_org_with_platform_admin,
    _step_up,
)

URL = "/api/v1/catalog-categories"


@pytest.fixture(autouse=True)
def _stub_emit_audit():
    # AuditEvent's JSONB default cannot be created on SQLite; PostgreSQL tests cover the audit row.
    with patch("symgov_backend.catalog_categories._emit_audit") as emitted:
        yield emitted


def _plus_user(Session, email, display_name, roles):
    """Global roles only apply on a `plus` subscription, so upgrade first, then grant them."""
    with Session() as session:
        user = upsert_user(session, email=email, display_name=display_name, roles=[], pin="1234", must_change_pin=False)
        session.commit()
        user_id = user.id
    with Session() as session:
        subscription = session.get(UserSubscription, user_id)
        subscription.tier = "plus"
        subscription.expires_on = date.today().replace(year=date.today().year + 1)
        session.commit()
    with Session() as session:
        upsert_user(session, email=email, display_name=display_name, roles=roles, pin="1234", must_change_pin=False)
        session.commit()


def _world():
    client, Session, admin_id, _, _ = _build_client()
    engine = Session.kw["bind"]
    for table in (CatalogCategoryAddition.__table__, ReviewSymbolPropertyOption.__table__, GovernedSymbol.__table__):
        # Some checks use PostgreSQL-only functions; the PostgreSQL suites cover them.
        original = table.constraints
        table.constraints = {c for c in original if not isinstance(c, CheckConstraint)}
        try:
            table.create(engine, checkfirst=True)
        finally:
            table.constraints = original
    _plus_user(Session, "reviewer@example.test", "Rita Reviewer", ["reviewer"])
    _plus_user(Session, "submitter@example.test", "Sam Submitter", ["submitter"])
    symgov_id = _seed_symgov_org_with_platform_admin(Session, admin_id)
    return client, Session, symgov_id


def _login(client, email):
    response = client.post("/api/v1/auth/login", json={"email": email, "pin": "1234"})
    assert response.status_code == 200, response.text


def _names(response):
    return [item["name"] for item in response.json()["items"]]


def test_everyone_signed_in_reads_the_built_in_list_with_unclassified_last():
    client, _, _ = _world()
    assert client.get(URL).status_code == 401
    _login(client, "submitter@example.test")
    names = _names(client.get(URL))
    assert names == [n for n in CATALOG_CATEGORY_ORDER if n != "Miscellaneous / Unclassified"] + ["Miscellaneous / Unclassified"]
    assert "Doors" in names


def test_a_public_reviewer_adds_a_category_that_everyone_then_sees(_stub_emit_audit):
    client, _, _ = _world()
    _login(client, "reviewer@example.test")
    added = client.post(URL, json={"name": "  cable   trays "})
    assert added.status_code == 200, added.text
    assert (added.json()["name"], added.json()["created"]) == ("cable trays", True)
    assert _stub_emit_audit.call_args.kwargs["action"] == "catalog_category.added"
    assert _stub_emit_audit.call_args.kwargs["payload"]["effective_authority"] == "public_reviewer"

    names = _names(added)
    # Additions sit after the built-in names, before Unclassified.
    assert names[-2:] == ["cable trays", "Miscellaneous / Unclassified"]
    entry = next(item for item in added.json()["items"] if item["name"] == "cable trays")
    assert entry["builtIn"] is False and entry["addedByName"] == "Rita Reviewer"

    # The same name again, in any spelling, is the listed one.
    again = client.post(URL, json={"name": "Cable-Trays"})
    assert (again.json()["name"], again.json()["created"]) == ("cable trays", False)

    client.post("/api/v1/auth/logout")
    _login(client, "submitter@example.test")
    assert "cable trays" in _names(client.get(URL))


def test_a_name_close_to_a_listed_one_needs_confirming():
    client, _, _ = _world()
    _login(client, "reviewer@example.test")
    close = client.post(URL, json={"name": "Pump"})
    assert close.status_code == 409
    assert close.json()["similarTo"] == "Pumps"
    assert "Pump" not in _names(client.get(URL))

    confirmed = client.post(URL, json={"name": "Pump", "allowSimilar": True})
    assert confirmed.status_code == 200 and confirmed.json()["created"] is True


def test_only_a_public_reviewer_or_platform_admin_adds_and_a_bad_name_is_refused():
    client, _, symgov_id = _world()
    _login(client, "submitter@example.test")
    assert client.post(URL, json={"name": "Cable trays"}).status_code == 403

    client.post("/api/v1/auth/logout")
    _login(client, "reviewer@example.test")
    assert client.post(URL, json={"name": "x"}).status_code == 422
    assert client.post(URL, json={"name": "1234"}).status_code == 422
    assert client.post(URL, json={"name": "Cable trays", "reviewCaseId": "nope"}).status_code == 422

    client.post("/api/v1/auth/logout")
    _login_and_select_org(client, "platform-admin@example.test", symgov_id)
    assert client.post(URL, json={"name": "Cable trays"}).status_code == 200


def test_a_platform_admin_sees_usage_and_old_values_and_retires_an_addition(_stub_emit_audit):
    client, Session, symgov_id = _world()
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with Session() as session:
        for value, uses in (("Fire", 7), ("Valves", 3), ("cable trays", 1)):
            session.add(ReviewSymbolPropertyOption(
                id=uuid.uuid4(), field_name="category", display_value=value, normalized_key=value.lower(),
                use_count=uses, created_at=now, updated_at=now, last_used_at=now,
            ))
        session.commit()

    _login(client, "reviewer@example.test")
    client.post(URL, json={"name": "Cable trays"})
    client.post("/api/v1/auth/logout")

    _login_and_select_org(client, "platform-admin@example.test", symgov_id)
    report = client.get("/api/v1/platform/catalog-categories")
    assert report.status_code == 200, report.text
    [addition] = report.json()["additions"]
    assert (addition["name"], addition["addedByName"], addition["retiredAt"]) == ("Cable trays", "Rita Reviewer", None)
    # Only values on no list are offered for cleanup.
    assert [(item["value"], item["useCount"]) for item in report.json()["legacyValues"]] == [("Fire", 7)]

    retire = f"/api/v1/platform/catalog-categories/{addition['id']}/retire"
    assert client.post(retire).status_code == 403
    _step_up(client)
    retired = client.post(retire)
    assert retired.status_code == 200, retired.text
    assert retired.json()["additions"][0]["retiredAt"] is not None
    assert _stub_emit_audit.call_args.kwargs["action"] == "catalog_category.retired"
    assert "Cable trays" not in _names(client.get(URL))

    client.post("/api/v1/auth/logout")
    _login(client, "reviewer@example.test")
    again = client.post(URL, json={"name": "cable trays"})
    assert again.status_code == 422
    assert "retired" in again.json()["detail"]


def test_the_platform_report_is_refused_to_others():
    client, _, _ = _world()
    _login(client, "reviewer@example.test")
    assert client.get("/api/v1/platform/catalog-categories").status_code == 403


def test_a_reviewer_save_stores_a_listed_name_or_leaves_an_old_value_alone():
    """The public review editor's save (`PATCH .../symbol-properties`) goes through this."""
    from symgov_backend.catalog_categories import CatalogCategoryError, listed_category_or_current

    _, Session, _ = _world()
    with Session() as session:
        assert listed_category_or_current(session, "  vessels/tanks ", current=None) == "Vessels / Tanks"
        assert listed_category_or_current(session, "", current="Fire") is None
        # Typed before the picklist and left alone: kept.
        assert listed_category_or_current(session, "Fire", current="Fire") == "Fire"
        with pytest.raises(CatalogCategoryError, match="Unknown category 'Firefighting'"):
            listed_category_or_current(session, "Firefighting", current="Fire")
