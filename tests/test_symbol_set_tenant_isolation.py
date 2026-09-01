"""Stage 6 WP6.1 — effective palette tenant isolation, against a real
disposable PostgreSQL container.

Per the Stage 6 plan (WP6.1 acceptance): "Treat any effective-palette
query as a new reader that must itself prove it can't leak a
cross-organization private symbol — the same rigor Stage 5's WP5.2/5.6
applied to public readers should apply here to organization-scoped
readers." The DB-level guarantees this proves against are Postgres-only
(the `organization_wide_scope`/`catalog_symbol_visibility_barrier` CHECK
constraints and the `trg_governed_symbols_organization_wide_eligibility`
deferred constraint trigger from WP5.1/5.4) — a SQLite unit test cannot
exercise them, which is why `tests/test_effective_palette.py` mocks
eligibility instead and this file is Postgres-only.

An organization-wide symbol is built through the real WP5.3/5.4 service
pipeline (create_draft -> submit_for_review -> decide_submission ->
set_organization_wide), the same path Stage 5's own tests use, so the
fixture itself is only reachable the way production would reach it.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _organization,
    _user,
    stage5_database,
)
from test_project_symbol_set_postgresql import _availability, _project, _symbol_set  # noqa: E402
from test_wp53_organization_symbol_drafts import _actor, _membership  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.auth import hash_session_token  # noqa: E402
from symgov_backend.effective_palette import effective_palette  # noqa: E402
from symgov_backend.organization_symbol_drafts import create_draft, submit_for_review  # noqa: E402
from symgov_backend.organization_symbol_review import OrganizationSymbolReviewError, decide_submission, set_organization_wide  # noqa: E402
from symgov_backend.symbol_set_service import replace_items  # noqa: E402


def _activate(connection, organization_id):
    connection.execute(text("UPDATE organizations SET is_active=true WHERE id=:id"), {"id": organization_id})


def _normalized_code(connection, organization_id) -> str:
    return connection.execute(
        text("SELECT normalized_code FROM organizations WHERE id=:id"), {"id": organization_id}
    ).scalar_one()


def _organization_wide_symbol(engine, actor) -> uuid.UUID:
    SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with SessionLocal() as session:
        symbol, revision = create_draft(
            session, actor,
            name="Fire alarm call point", category="fire", discipline="fire-safety",
            summary="Test organization-wide symbol.",
        )
        session.commit()
        submission = submit_for_review(session, actor, symbol_id=symbol.id, revision_id=revision.id)
        session.commit()
        decide_submission(session, actor, submission_id=submission.id, decision="approved")
        session.commit()
        set_organization_wide(session, actor, symbol_id=symbol.id, enabled=True)
        session.commit()
        return symbol.id


def _bound_session_request(engine, user_id, organization_id) -> Request:
    raw_token = uuid.uuid4().hex
    now = datetime.now(timezone.utc).replace(microsecond=0)
    with engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO user_sessions
            (id, auth_user_id, token_hash, created_at, expires_at, last_seen_at,
             purpose, session_mode, active_organization_id)
            VALUES (:id, :user, :token, :now, :expires, :now,
                    'application', 'organization', :organization)
        """), {
            "id": uuid.uuid4(), "user": user_id, "token": hash_session_token(raw_token),
            "now": now, "expires": now + timedelta(hours=1), "organization": organization_id,
        })
    return Request({"type": "http", "headers": [(b"cookie", f"symgov_session={raw_token}".encode())]})


@pytest.fixture()
def two_organizations(stage5_database):
    engine, _, _ = stage5_database
    with engine.begin() as connection:
        user_a = _user(connection, "tenant-a")
        user_b = _user(connection, "tenant-b")
        org_a = _organization(connection, "tenanta")
        org_b = _organization(connection, "tenantb")
        _activate(connection, org_a)
        _activate(connection, org_b)
        _membership(connection, org_a, user_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
        _membership(connection, org_b, user_b, base_role="admin")
        code_a = _normalized_code(connection, org_a)
        code_b = _normalized_code(connection, org_b)
        project_a = _project(connection, org_a, user_a, "PRJ-A")
        project_b = _project(connection, org_b, user_b, "PRJ-B")
    settings = SimpleNamespace(
        organizations_enabled=True,
        symbol_sets_enabled=True,
        organization_symbols_enabled=True,
        organization_pilot_codes=(code_a, code_b),
    )
    return SimpleNamespace(
        engine=engine, user_a=user_a, user_b=user_b, org_a=org_a, org_b=org_b,
        project_a=project_a, project_b=project_b, settings=settings,
    )


def test_effective_palette_never_leaks_a_cross_organization_organization_wide_symbol(two_organizations):
    fixtures = two_organizations
    actor_a = _actor(fixtures.user_a, fixtures.org_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    _organization_wide_symbol(fixtures.engine, actor_a)

    request_b = _bound_session_request(fixtures.engine, fixtures.user_b, fixtures.org_b)
    SessionLocal = sessionmaker(bind=fixtures.engine, autoflush=False, expire_on_commit=False)
    with SessionLocal.begin() as session:
        _, result = effective_palette(session, request_b, fixtures.settings, fixtures.project_b, page=1, page_size=50)

    assert result["items"] == []
    assert result["total"] == 0


def test_effective_palette_includes_the_owning_organizations_own_organization_wide_symbol(two_organizations):
    """Positive control: proves the isolation test above is not vacuous
    (e.g. from a fixture bug that stops the symbol becoming organization-wide
    at all) by proving the *owning* organization does see it."""
    fixtures = two_organizations
    actor_a = _actor(fixtures.user_a, fixtures.org_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    symbol_id = _organization_wide_symbol(fixtures.engine, actor_a)

    request_a = _bound_session_request(fixtures.engine, fixtures.user_a, fixtures.org_a)
    SessionLocal = sessionmaker(bind=fixtures.engine, autoflush=False, expire_on_commit=False)
    with SessionLocal.begin() as session:
        _, result = effective_palette(session, request_a, fixtures.settings, fixtures.project_a, page=1, page_size=50)

    assert result["total"] == 1
    assert result["items"][0]["governedSymbolId"] == symbol_id
    assert result["items"][0]["source"] == "organization_wide"


def test_a_cross_organization_private_symbol_cannot_become_a_symbol_set_item(two_organizations):
    """Structural claim in `effective_palette.py`'s module docstring,
    proven directly: a `SymbolSetItem` can only ever reference a
    `visibility='public'` governed symbol, so the set-sourced half of the
    palette union can never carry an organization-private symbol from
    any organization, including its own."""
    fixtures = two_organizations
    actor_a = _actor(fixtures.user_a, fixtures.org_a, base_role="admin", capabilities=("contributor", "symbol_reviewer"))
    symbol_id = _organization_wide_symbol(fixtures.engine, actor_a)

    with fixtures.engine.begin() as connection:
        set_id = _symbol_set(connection, fixtures.org_a, fixtures.user_a, "SET-A")
        _availability(connection, fixtures.project_a, set_id, fixtures.user_a, default=True)

    request_a = _bound_session_request(fixtures.engine, fixtures.user_a, fixtures.org_a)
    SessionLocal = sessionmaker(bind=fixtures.engine, autoflush=False, expire_on_commit=False)
    with pytest.raises(Exception) as caught:
        with SessionLocal.begin() as session:
            replace_items(
                session, request_a, fixtures.settings, set_id,
                SimpleNamespace(items=[SimpleNamespace(
                    governedSymbolId=symbol_id, sortOrder=0, groupName=None, displayLabel=None,
                    notes=None, preferredFormat=None, provenance={},
                )]),
            )
    assert "eligible" in str(caught.value).lower() or getattr(caught.value, "status_code", None) == 409
