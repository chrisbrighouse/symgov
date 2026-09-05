"""Stage 10 WP10.1 regression: the `agent_configurations` and
`agent_findings` schema, their frozen vocabularies and cross-column
constraints, against a real disposable PostgreSQL container.

Per the Stage 10 plan
(`docs/plans/2026-09-05-symbol-set-management-stage10-implementation-plan.md`,
WP10.1/§4 Q2/Q3):
- `logical_agent_name` is frozen to exactly `organization_steward` and
  `platform_governance` (O4 -- these are logical capability names, not
  final Hermes agent identities).
- `scope_id` must be null iff `scope_type = 'platform'`, and non-null iff
  `scope_type = 'organization'`.
- At most one configuration row may exist per (capability, scope): a
  partial unique index for platform scope, another for organization scope.
- `finding_type` is frozen to exactly the six Organization Steward slugs
  plus the two Platform Governance slugs this stage's v1 actually
  populates -- `cross_tenant_authorization_failure` and
  `unresolved_governance_exception` are deliberately absent (§4 Q2).
- `severity`/`status` are frozen small vocabularies; `status`'s own
  cross-column constraint requires the matching actor/timestamp pair for
  each terminal transition, and a partial unique index on `fingerprint`
  enforces the one-active-finding-per-fingerprint rule (only one row with
  `status in ('open', 'acknowledged')` may share a fingerprint at a time).

This package adds no service/route wiring -- rows are inserted directly via
the ORM in these tests, exactly as WP10.2/WP10.3 will later do from real
finding-generation code.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_organization_symbol_postgresql import _alembic, _database  # noqa: E402
from test_wp74_symbol_demotion_postgresql import _add_membership, _create_user_with_global_roles  # noqa: E402

BACKEND = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from symgov_backend.models import AgentConfiguration, AgentFinding  # noqa: E402

NEW_MIGRATION_HEAD = "20260905_0044"

psycopg = pytest.importorskip("psycopg")


@pytest.fixture(scope="module")
def wp101_database():
    with _database("symgov-wp101") as (engine, url, raw_url):
        _alembic(url, "upgrade", NEW_MIGRATION_HEAD)
        yield engine, url, raw_url


@pytest.fixture()
def wp101_session(wp101_database):
    engine, _, _ = wp101_database
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return Session


def _seed_user_and_organization(Session):
    user_id = _create_user_with_global_roles(Session, email=f"wp101-{uuid.uuid4().hex[:8]}@example.test", display_name=f"WP10.1 User {uuid.uuid4().hex[:8]}", roles=[])
    organization_id = _add_membership(Session, user_id, code=f"wp101{uuid.uuid4().hex[:6]}", base_role="admin")
    return user_id, organization_id


def _valid_fingerprint(seed: str = "") -> str:
    import hashlib

    return hashlib.sha256((seed or uuid.uuid4().hex).encode("utf-8")).hexdigest()


def _make_finding(agent_config_id, **overrides):
    now = datetime.now(timezone.utc)
    defaults = dict(
        agent_config_id=agent_config_id,
        severity="medium",
        finding_type="reviewer_coverage_gap",
        entity_type="organization",
        entity_id=uuid.uuid4(),
        summary="No active reviewer is assigned in this organization.",
        policy_version="org_steward_v1",
        fingerprint=_valid_fingerprint(),
        first_seen_at=now,
        last_seen_at=now,
        created_at=now,
    )
    defaults.update(overrides)
    return AgentFinding(**defaults)


def test_valid_platform_and_organization_configs_insert_successfully(wp101_session):
    _, organization_id = _seed_user_and_organization(wp101_session)
    now = datetime.now(timezone.utc)
    rows = [
        AgentConfiguration(logical_agent_name="platform_governance", scope_type="platform", scope_id=None, created_at=now, updated_at=now),
        AgentConfiguration(logical_agent_name="organization_steward", scope_type="organization", scope_id=organization_id, created_at=now, updated_at=now),
    ]
    with wp101_session() as session:
        session.add_all(rows)
        session.commit()

    with wp101_session() as session:
        assert session.query(AgentConfiguration).count() >= 2


def test_unknown_logical_agent_name_is_rejected(wp101_session):
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(AgentConfiguration(logical_agent_name="bogus_agent", scope_type="platform", scope_id=None, created_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(scope_type="platform", scope_id_from_org=True),  # platform scope must not carry a scope_id
        dict(scope_type="organization", scope_id_from_org=False),  # organization scope requires a scope_id
    ],
)
def test_scope_type_scope_id_mismatch_is_rejected(wp101_session, kwargs):
    _, organization_id = _seed_user_and_organization(wp101_session)
    now = datetime.now(timezone.utc)
    scope_id = organization_id if kwargs["scope_id_from_org"] else None
    with wp101_session() as session:
        session.add(
            AgentConfiguration(
                logical_agent_name="organization_steward",
                scope_type=kwargs["scope_type"],
                scope_id=scope_id,
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_platform_scoped_config_is_rejected(wp101_session):
    # Uses `organization_steward` (not `platform_governance`, already claimed
    # by test_valid_platform_and_organization_configs_insert_successfully
    # above) since only one platform-scoped row may ever exist per logical
    # agent name for the lifetime of this module-scoped database.
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(AgentConfiguration(logical_agent_name="organization_steward", scope_type="platform", scope_id=None, created_at=now, updated_at=now))
        session.commit()
    with wp101_session() as session:
        session.add(AgentConfiguration(logical_agent_name="organization_steward", scope_type="platform", scope_id=None, created_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            session.commit()


def test_duplicate_organization_scoped_config_is_rejected(wp101_session):
    _, organization_id = _seed_user_and_organization(wp101_session)
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(AgentConfiguration(logical_agent_name="organization_steward", scope_type="organization", scope_id=organization_id, created_at=now, updated_at=now))
        session.commit()
    with wp101_session() as session:
        session.add(AgentConfiguration(logical_agent_name="organization_steward", scope_type="organization", scope_id=organization_id, created_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            session.commit()


def test_same_capability_different_organizations_is_allowed(wp101_session):
    _, org_a = _seed_user_and_organization(wp101_session)
    _, org_b = _seed_user_and_organization(wp101_session)
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add_all(
            [
                AgentConfiguration(logical_agent_name="organization_steward", scope_type="organization", scope_id=org_a, created_at=now, updated_at=now),
                AgentConfiguration(logical_agent_name="organization_steward", scope_type="organization", scope_id=org_b, created_at=now, updated_at=now),
            ]
        )
        session.commit()


@pytest.mark.parametrize("model_alias", ["Fast", "fast profile", "-fast", "a" * 65, ""])
def test_invalid_model_alias_format_is_rejected(wp101_session, model_alias):
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(AgentConfiguration(logical_agent_name="platform_governance", scope_type="platform", scope_id=None, model_alias=model_alias, created_at=now, updated_at=now))
        with pytest.raises(IntegrityError):
            session.commit()


def test_valid_model_alias_is_accepted(wp101_session):
    # Organization-scoped (not platform) so this doesn't compete with the
    # module's only two platform-scope slots, which other tests above
    # already claim for their own uniqueness proofs.
    _, organization_id = _seed_user_and_organization(wp101_session)
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(AgentConfiguration(logical_agent_name="organization_steward", scope_type="organization", scope_id=organization_id, model_alias="deep_reasoning", created_at=now, updated_at=now))
        session.commit()


def test_allowed_capabilities_json_must_be_an_array(wp101_session):
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(
            AgentConfiguration(
                logical_agent_name="platform_governance",
                scope_type="platform",
                scope_id=None,
                allowed_capabilities_json={"not": "an array"},
                created_at=now,
                updated_at=now,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


def _seed_config(Session, *, logical_agent_name="organization_steward"):
    # Always organization-scoped with a freshly seeded organization, so
    # repeated calls across many finding-focused tests never collide with
    # the module's only two platform-scope slots (each logical_agent_name
    # may have at most one platform-scoped row for the whole module).
    _, organization_id = _seed_user_and_organization(Session)
    now = datetime.now(timezone.utc)
    config = AgentConfiguration(logical_agent_name=logical_agent_name, scope_type="organization", scope_id=organization_id, created_at=now, updated_at=now)
    with Session() as session:
        session.add(config)
        session.commit()
        session.refresh(config)
        return config.id


def test_valid_finding_inserts_successfully(wp101_session):
    config_id = _seed_config(wp101_session)
    with wp101_session() as session:
        session.add(_make_finding(config_id))
        session.commit()

    with wp101_session() as session:
        assert session.query(AgentFinding).filter(AgentFinding.agent_config_id == config_id).count() == 1


def test_unknown_finding_type_is_rejected(wp101_session):
    config_id = _seed_config(wp101_session)
    with wp101_session() as session:
        session.add(_make_finding(config_id, finding_type="cross_tenant_authorization_failure"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_unknown_severity_is_rejected(wp101_session):
    config_id = _seed_config(wp101_session)
    with wp101_session() as session:
        session.add(_make_finding(config_id, severity="catastrophic"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_malformed_fingerprint_is_rejected(wp101_session):
    config_id = _seed_config(wp101_session)
    with wp101_session() as session:
        session.add(_make_finding(config_id, fingerprint="not-a-hash"))
        with pytest.raises(IntegrityError):
            session.commit()


def test_empty_summary_is_rejected(wp101_session):
    config_id = _seed_config(wp101_session)
    with wp101_session() as session:
        session.add(_make_finding(config_id, summary="   "))
        with pytest.raises(IntegrityError):
            session.commit()


def test_evidence_json_must_be_an_object(wp101_session):
    config_id = _seed_config(wp101_session)
    with wp101_session() as session:
        session.add(_make_finding(config_id, evidence_json=["not", "an", "object"]))
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.parametrize(
    "status_kwargs",
    [
        dict(status="acknowledged"),  # missing acknowledged_at/by
        dict(status="dismissed"),  # missing dismissed_at
        dict(status="resolved"),  # missing resolved_at
        dict(status="superseded"),  # missing superseded_by_finding_id
    ],
)
def test_status_consistency_constraint_rejects_incomplete_transitions(wp101_session, status_kwargs):
    config_id = _seed_config(wp101_session)
    with wp101_session() as session:
        session.add(_make_finding(config_id, **status_kwargs))
        with pytest.raises(IntegrityError):
            session.commit()


def test_acknowledged_at_without_actor_is_rejected(wp101_session):
    config_id = _seed_config(wp101_session)
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(_make_finding(config_id, status="acknowledged", acknowledged_at=now))
        with pytest.raises(IntegrityError):
            session.commit()


def test_resolved_transition_with_full_pair_succeeds(wp101_session):
    user_id, _ = _seed_user_and_organization(wp101_session)
    config_id = _seed_config(wp101_session)
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(_make_finding(config_id, status="resolved", resolved_at=now, resolved_by_user_id=user_id))
        session.commit()


def test_duplicate_active_fingerprint_is_rejected(wp101_session):
    config_id = _seed_config(wp101_session)
    fingerprint = _valid_fingerprint()
    with wp101_session() as session:
        session.add(_make_finding(config_id, fingerprint=fingerprint))
        session.commit()
    with wp101_session() as session:
        session.add(_make_finding(config_id, fingerprint=fingerprint))
        with pytest.raises(IntegrityError):
            session.commit()


def test_new_active_finding_allowed_once_prior_fingerprint_is_resolved(wp101_session):
    user_id, _ = _seed_user_and_organization(wp101_session)
    config_id = _seed_config(wp101_session)
    fingerprint = _valid_fingerprint()
    now = datetime.now(timezone.utc)
    with wp101_session() as session:
        session.add(_make_finding(config_id, fingerprint=fingerprint, status="resolved", resolved_at=now, resolved_by_user_id=user_id))
        session.commit()
    with wp101_session() as session:
        session.add(_make_finding(config_id, fingerprint=fingerprint))
        session.commit()

    with wp101_session() as session:
        assert session.query(AgentFinding).filter(AgentFinding.fingerprint == fingerprint).count() == 2
