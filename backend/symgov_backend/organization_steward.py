"""Stage 10 WP10.2 -- Organization Steward deterministic finding-generation
service (programme plan §16, spec FR-AGT-002). Per Stage 10 plan §4 Q4/Q6,
this is entirely deterministic application logic over existing tables --
no LLM call anywhere in this module -- and is invoked on-demand only (no
scheduler is wired anywhere in this repository; a caller, e.g. an admin
route in WP10.4, decides when to run it).

`run_organization_steward` computes exactly five in-scope finding
categories confirmed for v1 (Stage 10 plan §4 Q2/Q7 and this package's own
design round): `reviewer_coverage_gap`, `review_backlog_stale`,
`project_health_issue`, `symbol_set_health_issue`, `unresolved_reference`.
`icon_generation_missing` is not computed here -- deferred, see
`AgentFinding`'s own docstring in `models/schema.py`.

The caller must pass an `AgentConfiguration` that is already confirmed
`enabled` and `scope_type == 'organization'` -- this module does not itself
check enablement or re-validate scope, matching the existing convention
that service-layer functions trust their caller's own authorization/gating
(the route layer owns that check, WP10.4). The caller is responsible for
committing the session."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from .agent_finding_support import compute_fingerprint, upsert_active_finding
from .models import (
    AgentConfiguration,
    AgentFinding,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationSymbolReviewSubmission,
    Project,
    ProjectSymbolSet,
    SymbolSet,
    SymbolSetItem,
)

POLICY_VERSION = "org_steward_v1"
REVIEW_BACKLOG_STALE_DAYS = 14


def run_organization_steward(session: Session, config: AgentConfiguration, *, now: datetime | None = None) -> list[AgentFinding]:
    """Compute and upsert this organization's current Organization Steward
    findings. Returns every `AgentFinding` row touched (created or
    re-touched) during this run -- not every finding that has ever existed
    for this organization."""
    if config.scope_type != "organization" or config.scope_id is None:
        raise ValueError("run_organization_steward requires an organization-scoped AgentConfiguration")

    reference = now or datetime.now(timezone.utc)
    organization_id = config.scope_id
    touched: list[AgentFinding] = []

    touched.extend(_check_reviewer_coverage(session, config, organization_id, reference))
    touched.extend(_check_review_backlog(session, config, organization_id, reference))
    touched.extend(_check_project_health(session, config, organization_id, reference))
    touched.extend(_check_symbol_set_health(session, config, organization_id, reference))
    touched.extend(_check_unresolved_references(session, config, organization_id, reference))
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


def _check_reviewer_coverage(session, config, organization_id: uuid.UUID, now: datetime) -> list[AgentFinding]:
    active_submission_count = session.execute(
        select(func.count())
        .select_from(OrganizationSymbolReviewSubmission)
        .where(
            OrganizationSymbolReviewSubmission.organization_id == organization_id,
            OrganizationSymbolReviewSubmission.status == "active",
        )
    ).scalar_one()
    if active_submission_count == 0:
        return []

    active_reviewer_count = session.execute(
        select(func.count())
        .select_from(OrganizationMemberCapability)
        .join(OrganizationMembership, OrganizationMemberCapability.membership_id == OrganizationMembership.id)
        .where(
            OrganizationMembership.organization_id == organization_id,
            OrganizationMemberCapability.capability == "symbol_reviewer",
            OrganizationMemberCapability.is_active.is_(True),
        )
    ).scalar_one()
    if active_reviewer_count > 0:
        return []

    finding = _upsert(
        session,
        config,
        finding_type="reviewer_coverage_gap",
        entity_type="organization",
        entity_id=organization_id,
        severity="high",
        summary=(
            f"No active reviewer capability exists in this organization while "
            f"{active_submission_count} symbol review submission(s) await decision."
        ),
        evidence={"active_submission_count": active_submission_count, "active_reviewer_count": 0},
        now=now,
    )
    return [finding]


def _check_review_backlog(session, config, organization_id: uuid.UUID, now: datetime) -> list[AgentFinding]:
    cutoff = now - timedelta(days=REVIEW_BACKLOG_STALE_DAYS)
    stale_submissions = session.execute(
        select(OrganizationSymbolReviewSubmission).where(
            OrganizationSymbolReviewSubmission.organization_id == organization_id,
            OrganizationSymbolReviewSubmission.status == "active",
            OrganizationSymbolReviewSubmission.submitted_at <= cutoff,
        )
    ).scalars().all()

    touched = []
    for submission in stale_submissions:
        pending_days = (now - submission.submitted_at).days
        touched.append(
            _upsert(
                session,
                config,
                finding_type="review_backlog_stale",
                entity_type="organization_symbol_review_submission",
                entity_id=submission.id,
                severity="medium",
                summary=f"Symbol review submission has been pending for {pending_days} day(s) without a decision.",
                evidence={"submitted_at": submission.submitted_at.isoformat(), "pending_days": pending_days, "threshold_days": REVIEW_BACKLOG_STALE_DAYS},
                now=now,
            )
        )
    return touched


def _check_project_health(session, config, organization_id: uuid.UUID, now: datetime) -> list[AgentFinding]:
    has_active_set = (
        exists()
        .where(
            ProjectSymbolSet.project_id == Project.id,
            ProjectSymbolSet.status == "active",
        )
    )
    unhealthy_projects = session.execute(
        select(Project).where(
            Project.organization_id == organization_id,
            Project.status == "active",
            ~has_active_set,
        )
    ).scalars().all()

    touched = []
    for project in unhealthy_projects:
        touched.append(
            _upsert(
                session,
                config,
                finding_type="project_health_issue",
                entity_type="project",
                entity_id=project.id,
                severity="low",
                summary="Active project has no active symbol set attached.",
                evidence={"project_code": project.code},
                now=now,
            )
        )
    return touched


def _check_symbol_set_health(session, config, organization_id: uuid.UUID, now: datetime) -> list[AgentFinding]:
    has_available_item = (
        exists()
        .where(
            SymbolSetItem.symbol_set_id == SymbolSet.id,
            SymbolSetItem.availability_status == "active",
        )
    )
    unhealthy_sets = session.execute(
        select(SymbolSet).where(
            SymbolSet.owner_organization_id == organization_id,
            SymbolSet.status == "active",
            ~has_available_item,
        )
    ).scalars().all()

    touched = []
    for symbol_set in unhealthy_sets:
        touched.append(
            _upsert(
                session,
                config,
                finding_type="symbol_set_health_issue",
                entity_type="symbol_set",
                entity_id=symbol_set.id,
                severity="medium",
                summary="Active symbol set has no available items.",
                evidence={"symbol_set_code": symbol_set.code},
                now=now,
            )
        )
    return touched


def _check_unresolved_references(session, config, organization_id: uuid.UUID, now: datetime) -> list[AgentFinding]:
    unresolved_items = session.execute(
        select(SymbolSetItem)
        .join(SymbolSet, SymbolSetItem.symbol_set_id == SymbolSet.id)
        .where(
            SymbolSet.owner_organization_id == organization_id,
            SymbolSetItem.availability_status == "unavailable",
        )
    ).scalars().all()

    touched = []
    for item in unresolved_items:
        evidence = {"symbol_set_id": str(item.symbol_set_id), "governed_symbol_id": str(item.governed_symbol_id)}
        if item.availability_reason:
            evidence["availability_reason"] = item.availability_reason
        touched.append(
            _upsert(
                session,
                config,
                finding_type="unresolved_reference",
                entity_type="symbol_set_item",
                entity_id=item.id,
                severity="low",
                summary="Symbol set item references a symbol that is no longer available.",
                evidence=evidence,
                now=now,
            )
        )
    return touched
