"""WP5.3 regression: organization-private symbol draft/revision/intake/asset
service, against a real disposable PostgreSQL container (WP5.1's schema,
triggers, and `organization_symbol_review_submissions` binding constraints
are Postgres-only — a SQLite unit test cannot exercise them).

Proves, per the Stage 5 plan (§3) and programme plan §11:
- Only an active Organization Admin or a member with the `contributor`
  capability can create a draft, owner-bound to their active organization.
- Draft visibility is limited to the creator, active Organization Admins,
  and active appointed Organization Reviewers — an ordinary member and a
  cross-organization actor cannot see or enumerate it.
- Asset intake is deterministically validated (rejects non-image bytes)
  and produces a real `Attachment` row plus a payload_json asset entry.
- Submitting a draft revision for review creates a real
  `organization_symbol_review_submissions` row and advances the revision
  to `review`; a second submission of the same revision is rejected.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import (  # noqa: E402
    _alembic,
    _organization,
    _user,
    stage5_database,
)

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.auth import AuthenticatedUser  # noqa: E402
import symgov_backend.organization_symbol_drafts as organization_symbol_drafts  # noqa: E402
from symgov_backend.organization_symbol_drafts import (  # noqa: E402
    OrganizationSymbolDraftError,
    OrganizationSymbolDraftNotVisible,
    attach_asset,
    create_draft,
    get_draft,
    list_drafts,
    submit_for_review,
)


class _FakeStorageBridge:
    """Stand-in for RuntimePersistenceBridge's storage calls.

    Object storage is a real external S3-compatible service; tests must
    not perform network I/O. `create_attachment` still performs a real
    insert against this test's disposable Postgres container (via a
    short-lived connection to the same database), so the Attachment row
    and its uniqueness/FK behavior are genuinely exercised; only the
    outbound object-storage PUT is faked.
    """

    def __init__(self, engine):
        self._engine = engine

    def create_attachment(self, *, parent_type, parent_id, filename, object_key, content_type, size_bytes, sha256=None):
        now = datetime.now(timezone.utc).replace(microsecond=0)
        attachment_id = uuid.uuid4()
        with self._engine.begin() as connection:
            existing = connection.execute(
                text("SELECT id, object_key, filename FROM attachments WHERE object_key = :object_key"),
                {"object_key": object_key},
            ).one_or_none()
            if existing is not None:
                return {"id": str(existing.id), "object_key": existing.object_key, "filename": existing.filename}
            connection.execute(
                text(
                    "INSERT INTO attachments "
                    "(id,parent_type,parent_id,filename,object_key,content_type,size_bytes,sha256,created_at) "
                    "VALUES (:id,:parent_type,:parent_id,:filename,:object_key,:content_type,:size_bytes,:sha256,:now)"
                ),
                {
                    "id": attachment_id,
                    "parent_type": parent_type,
                    "parent_id": parent_id,
                    "filename": filename,
                    "object_key": object_key,
                    "content_type": content_type,
                    "size_bytes": size_bytes,
                    "sha256": sha256,
                    "now": now,
                },
            )
        return {"id": str(attachment_id), "object_key": object_key, "filename": filename}

    def upload_object_bytes(self, *, object_key, payload, content_type, env_file=None):
        return {"object_key": object_key, "size_bytes": len(payload)}


@pytest.fixture(autouse=True)
def _fake_storage_bridge(stage5_database, monkeypatch):
    engine, _, _ = stage5_database
    monkeypatch.setattr(
        organization_symbol_drafts,
        "RuntimePersistenceBridge",
        lambda env_file=None: _FakeStorageBridge(engine),
    )


def _membership(connection, organization_id, user_id, *, base_role="user", capabilities=()):
    now = datetime.now(timezone.utc).replace(microsecond=0)
    membership_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO organization_memberships "
            "(id,organization_id,user_id,status,created_at,updated_at) "
            "VALUES (:id,:organization,:user,'active',:now,:now)"
        ),
        {"id": membership_id, "organization": organization_id, "user": user_id, "now": now},
    )
    connection.execute(
        text(
            "INSERT INTO organization_role_assignments "
            "(id,membership_id,base_role,is_active,assigned_at) "
            "VALUES (:id,:membership,:role,true,:now)"
        ),
        {"id": uuid.uuid4(), "membership": membership_id, "role": base_role, "now": now},
    )
    for capability in capabilities:
        connection.execute(
            text(
                "INSERT INTO organization_member_capabilities "
                "(id,membership_id,capability,is_active,granted_at) "
                "VALUES (:id,:membership,:capability,true,:now)"
            ),
            {"id": uuid.uuid4(), "membership": membership_id, "capability": capability, "now": now},
        )
    return membership_id


def _actor(user_id, organization_id, *, base_role="user", capabilities=()):
    return AuthenticatedUser(
        id=str(user_id),
        email=f"{user_id}@example.test",
        display_name="Test actor",
        roles=("user",),
        must_change_pin=False,
        session_mode="organization",
        active_organization_id=str(organization_id),
        organization_base_role=base_role,
        organization_capabilities=tuple(capabilities),
    )


@pytest.fixture()
def wp53_fixtures(stage5_database):
    engine, url, _ = stage5_database
    # Stage 9 WP9.2 added ProductUsageEvent emission inside
    # organization_symbol_drafts.submit_for_review /
    # organization_symbol_review.decide_submission / set_organization_wide
    # (shared, widely-tested functions this file exercises directly) --
    # those calls now unconditionally need `product_usage_events` to
    # exist. Applied locally here, not by bumping the shared
    # `stage5_database` fixture itself, since that fixture is also used
    # by several other Stage 5 test files that must stay pinned to their
    # own original schema snapshot.
    _alembic(url, "upgrade", "20260904_0039")
    with engine.begin() as connection:
        organization = _organization(connection, "wp53")
        other_organization = _organization(connection, "wp53other")
        contributor_user = _user(connection, "contributor")
        admin_user = _user(connection, "admin")
        reviewer_user = _user(connection, "reviewer")
        ordinary_user = _user(connection, "ordinary")
        cross_org_user = _user(connection, "crossorg")

        _membership(connection, organization, contributor_user, base_role="user", capabilities=("contributor",))
        _membership(connection, organization, admin_user, base_role="admin")
        _membership(connection, organization, reviewer_user, base_role="user", capabilities=("symbol_reviewer",))
        _membership(connection, organization, ordinary_user, base_role="user")
        _membership(connection, other_organization, cross_org_user, base_role="admin")

    return {
        "engine": engine,
        "organization": organization,
        "other_organization": other_organization,
        "contributor": _actor(contributor_user, organization, base_role="user", capabilities=("contributor",)),
        "admin": _actor(admin_user, organization, base_role="admin"),
        "reviewer": _actor(reviewer_user, organization, base_role="user", capabilities=("symbol_reviewer",)),
        "ordinary": _actor(ordinary_user, organization, base_role="user"),
        "cross_org_admin": _actor(cross_org_user, other_organization, base_role="admin"),
    }


def test_contributor_can_create_a_private_draft_bound_to_active_organization(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        symbol, revision = create_draft(
            session,
            wp53_fixtures["contributor"],
            name="Emergency shutoff valve",
            category="valve",
            discipline="mechanical",
            summary="Manual emergency shutoff valve.",
            aliases=["ESV"],
            keywords=["safety", "shutoff"],
        )
        session.commit()
        assert symbol.visibility == "organization_private"
        assert symbol.owner_organization_id == wp53_fixtures["organization"]
        assert symbol.owner_id == uuid.UUID(wp53_fixtures["contributor"].id)
        assert symbol.current_revision_id == revision.id
        assert revision.lifecycle_state == "draft"
        assert revision.payload_json["name"] == "Emergency shutoff valve"
        assert revision.payload_json["aliases"] == ["ESV"]


def test_org_admin_can_also_create_a_draft(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        symbol, _ = create_draft(
            session, wp53_fixtures["admin"], name="Admin symbol", category="test", discipline="test", summary="s"
        )
        session.commit()
        assert symbol.owner_organization_id == wp53_fixtures["organization"]


def test_ordinary_member_without_contributor_capability_cannot_create_a_draft(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolDraftError):
            create_draft(
                session, wp53_fixtures["ordinary"], name="x", category="test", discipline="test", summary="s"
            )


def test_creator_admin_and_reviewer_can_see_the_draft_but_ordinary_member_and_cross_org_cannot(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        symbol, _ = create_draft(
            session, wp53_fixtures["contributor"], name="Visibility test", category="test", discipline="test", summary="s"
        )
        session.commit()
        symbol_id = symbol.id

    with Session(engine) as session:
        assert get_draft(session, wp53_fixtures["contributor"], symbol_id).id == symbol_id
        assert get_draft(session, wp53_fixtures["admin"], symbol_id).id == symbol_id
        assert get_draft(session, wp53_fixtures["reviewer"], symbol_id).id == symbol_id
        with pytest.raises(OrganizationSymbolDraftNotVisible):
            get_draft(session, wp53_fixtures["ordinary"], symbol_id)
        with pytest.raises(OrganizationSymbolDraftNotVisible):
            get_draft(session, wp53_fixtures["cross_org_admin"], symbol_id)


def test_list_drafts_scopes_contributor_to_own_but_admin_and_reviewer_see_all(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        create_draft(session, wp53_fixtures["contributor"], name="Mine", category="test", discipline="test", summary="s")
        create_draft(session, wp53_fixtures["admin"], name="Admin's", category="test", discipline="test", summary="s")
        session.commit()

    with Session(engine) as session:
        contributor_drafts = list_drafts(session, wp53_fixtures["contributor"])
        admin_drafts = list_drafts(session, wp53_fixtures["admin"])
        reviewer_drafts = list_drafts(session, wp53_fixtures["reviewer"])
        ordinary_drafts = list_drafts(session, wp53_fixtures["ordinary"])

    assert {s.canonical_name for s in contributor_drafts} == {"Mine"}
    assert {s.canonical_name for s in admin_drafts} >= {"Mine", "Admin's"}
    assert {s.canonical_name for s in reviewer_drafts} >= {"Mine", "Admin's"}
    assert ordinary_drafts == []


def test_attach_asset_validates_image_bytes_and_creates_a_real_attachment(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    png_bytes = bytes.fromhex(
        "89504e470d0a1a0a0000000d494844520000000100000001080600000"
        "01f15c4890000000a49444154789c6360000002000100e221bc330000"
        "0000494e44ae426082"
    )
    with Session(engine) as session:
        symbol, revision = create_draft(
            session, wp53_fixtures["contributor"], name="Asset test", category="test", discipline="test", summary="s"
        )
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id

    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolDraftError):
            attach_asset(
                session,
                wp53_fixtures["contributor"],
                symbol_id=symbol_id,
                revision_id=revision_id,
                filename="not-an-image.txt",
                declared_content_type="image/png",
                payload=b"this is not image bytes",
                storage_env_file="unused",
            )

    with Session(engine) as session:
        upload = attach_asset(
            session,
            wp53_fixtures["contributor"],
            symbol_id=symbol_id,
            revision_id=revision_id,
            filename="valve.png",
            declared_content_type="image/png",
            payload=png_bytes,
            storage_env_file="unused",
        )
        session.commit()
        assert upload.content_type == "image/png"
        assert upload.size_bytes == len(png_bytes)

    with engine.connect() as connection:
        attachment_row = connection.execute(
            text("SELECT parent_type, parent_id, object_key FROM attachments WHERE id = :id"),
            {"id": upload.id},
        ).one()
        assert attachment_row.parent_type == "symbol_revision"
        assert attachment_row.parent_id == revision_id
        assert attachment_row.object_key == upload.object_key

    with Session(engine) as session:
        from symgov_backend.models import SymbolRevision

        stored_revision = session.get(SymbolRevision, revision_id)
        assets = stored_revision.payload_json.get("assets")
        assert len(assets) == 1
        assert assets[0]["object_key"] == upload.object_key
        assert assets[0]["sha256"] == upload.sha256


def test_non_creator_non_admin_cannot_attach_an_asset(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        symbol, revision = create_draft(
            session, wp53_fixtures["contributor"], name="Guarded", category="test", discipline="test", summary="s"
        )
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id

    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolDraftNotVisible):
            attach_asset(
                session,
                wp53_fixtures["reviewer"],
                symbol_id=symbol_id,
                revision_id=revision_id,
                filename="x.png",
                declared_content_type="image/png",
                payload=b"\x89PNG\r\n\x1a\n0000",
                storage_env_file="unused",
            )


def test_submit_for_review_creates_a_real_submission_and_advances_lifecycle(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        symbol, revision = create_draft(
            session, wp53_fixtures["contributor"], name="Submit test", category="test", discipline="test", summary="s"
        )
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id

    with Session(engine) as session:
        submission = submit_for_review(
            session,
            wp53_fixtures["contributor"],
            symbol_id=symbol_id,
            revision_id=revision_id,
            rationale="Ready for organization review.",
        )
        session.commit()
        assert submission.organization_id == wp53_fixtures["organization"]
        assert submission.governed_symbol_id == symbol_id
        assert submission.symbol_revision_id == revision_id
        assert submission.status == "active"

    with engine.connect() as connection:
        lifecycle = connection.execute(
            text("SELECT lifecycle_state FROM symbol_revisions WHERE id = :id"), {"id": revision_id}
        ).scalar_one()
        assert lifecycle == "review"
        submission_count = connection.execute(
            text(
                "SELECT count(*) FROM organization_symbol_review_submissions "
                "WHERE symbol_revision_id = :id AND status = 'active'"
            ),
            {"id": revision_id},
        ).scalar_one()
        assert submission_count == 1

    # Re-submitting the same (now non-draft) revision is rejected.
    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolDraftError):
            submit_for_review(
                session,
                wp53_fixtures["contributor"],
                symbol_id=symbol_id,
                revision_id=revision_id,
            )


def test_cross_organization_actor_cannot_submit_a_draft_for_review(wp53_fixtures):
    from sqlalchemy.orm import Session

    engine = wp53_fixtures["engine"]
    with Session(engine) as session:
        symbol, revision = create_draft(
            session, wp53_fixtures["contributor"], name="Cross-org guard", category="test", discipline="test", summary="s"
        )
        session.commit()
        symbol_id, revision_id = symbol.id, revision.id

    with Session(engine) as session:
        with pytest.raises(OrganizationSymbolDraftNotVisible):
            submit_for_review(
                session,
                wp53_fixtures["cross_org_admin"],
                symbol_id=symbol_id,
                revision_id=revision_id,
            )
