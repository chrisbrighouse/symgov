"""Stage 10 WP10.3 -- Platform Governance deterministic finding-generation
service (programme plan §16, spec FR-AGT-003). Per Stage 10 plan §4 Q4/Q6/Q7,
entirely deterministic, on-demand only, reading only existing Platform Admin
data (`Organization`, `PlatformRoleAssignment`) -- no new report-generation
entity is built, and no LLM call anywhere in this module.

`run_platform_governance` computes exactly the two in-scope finding
categories confirmed for v1: `platform_admin_continuity_risk` and
`duplicate_organization_suspected`. `cross_tenant_authorization_failure`
and `unresolved_governance_exception` are not computed here -- deferred,
see `AgentFinding`'s own docstring in `models/schema.py`.

The caller must pass an `AgentConfiguration` that is already confirmed
`enabled` and `scope_type == 'platform'` -- this module does not itself
check enablement or re-validate scope, matching `organization_steward.py`'s
own convention. The caller is responsible for committing the session."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .agent_finding_support import compute_fingerprint, upsert_active_finding
from .models import AgentConfiguration, AgentFinding, Organization, PlatformRoleAssignment

POLICY_VERSION = "platform_governance_v1"


def run_platform_governance(session: Session, config: AgentConfiguration, *, now: datetime | None = None) -> list[AgentFinding]:
    """Compute and upsert the platform's current Platform Governance
    findings. Returns every `AgentFinding` row touched (created or
    re-touched) during this run."""
    if config.scope_type != "platform" or config.scope_id is not None:
        raise ValueError("run_platform_governance requires a platform-scoped AgentConfiguration")

    reference = now or datetime.now(timezone.utc)
    touched: list[AgentFinding] = []
    touched.extend(_check_platform_admin_continuity(session, config, reference))
    touched.extend(_check_duplicate_organizations(session, config, reference))
    return touched


def _upsert(session, config, *, finding_type, entity_type, entity_id, severity, summary, evidence, now):
    fingerprint = compute_fingerprint(
        logical_agent_name=config.logical_agent_name,
        scope_type=config.scope_type,
        scope_id=config.scope_id,
        finding_type=finding_type,
        entity_type=entity_type,
        entity_id=entity_id,
        policy_version=POLICY_VERSION,
    )
    return upsert_active_finding(
        session,
        agent_config_id=config.id,
        severity=severity,
        finding_type=finding_type,
        entity_type=entity_type,
        entity_id=entity_id,
        summary=summary,
        evidence=evidence,
        policy_version=POLICY_VERSION,
        fingerprint=fingerprint,
        now=now,
    )


def _platform_anchor_organization_id(session):
    """`PlatformRoleAssignment` has no organization scoping of its own
    (platform roles are user-scoped, not organization-scoped) -- the
    reserved `symgov` Organization's own id is used as this finding's
    stable anchor entity, since `AgentFinding.entity_id` is not nullable."""
    return session.execute(select(Organization.id).where(Organization.normalized_code == "symgov")).scalar_one()


def _check_platform_admin_continuity(session, config, now: datetime) -> list[AgentFinding]:
    active_admin_count = session.execute(
        select(func.count())
        .select_from(PlatformRoleAssignment)
        .where(PlatformRoleAssignment.role == "platform_admin", PlatformRoleAssignment.is_active.is_(True))
    ).scalar_one()
    if active_admin_count >= 2:
        return []

    # A database trigger already enforces at least one active Platform
    # Administrator at every commit (see `_add_membership`'s own docstring
    # precedent in the Postgres test suite), so `active_admin_count == 0`
    # should be unreachable in practice -- still handled defensively as
    # `critical`, in case that invariant is ever weakened or bypassed.
    severity = "critical" if active_admin_count == 0 else "high"
    anchor_id = _platform_anchor_organization_id(session)
    finding = _upsert(
        session,
        config,
        finding_type="platform_admin_continuity_risk",
        entity_type="platform",
        entity_id=anchor_id,
        severity=severity,
        summary=(
            "No active Platform Administrator exists." if active_admin_count == 0
            else "Exactly one active Platform Administrator exists -- a single point of failure."
        ),
        evidence={"active_platform_admin_count": active_admin_count},
        now=now,
    )
    return [finding]


def _check_duplicate_organizations(session, config, now: datetime) -> list[AgentFinding]:
    duplicate_name_keys = session.execute(
        select(Organization.name_key)
        .where(Organization.is_active.is_(True))
        .group_by(Organization.name_key)
        .having(func.count() > 1)
    ).scalars().all()

    touched = []
    for name_key in duplicate_name_keys:
        organizations = session.execute(
            select(Organization)
            .where(Organization.name_key == name_key, Organization.is_active.is_(True))
            .order_by(Organization.created_at.asc())
        ).scalars().all()
        earliest = organizations[0]
        for duplicate in organizations[1:]:
            touched.append(
                _upsert(
                    session,
                    config,
                    finding_type="duplicate_organization_suspected",
                    entity_type="organization",
                    entity_id=duplicate.id,
                    severity="medium",
                    summary=f"Organization display name collides with an existing organization ({earliest.code}).",
                    evidence={"name_key": name_key, "colliding_organization_id": str(earliest.id), "colliding_organization_code": earliest.code},
                    now=now,
                )
            )
    return touched
