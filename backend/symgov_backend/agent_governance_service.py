"""Stage 10 WP10.4 -- `AgentConfiguration` management and `AgentFinding`
human-response lifecycle (acknowledge/dismiss/resolve/escalate). This is
the route-facing service layer; `organization_steward.py`/
`platform_governance.py` own finding *generation*, this module owns what a
human does with a finding afterward (FR-AGT-007: recommendations require a
human to execute or confirm) and Platform Admin's own configuration
authority over `AgentConfiguration` rows (programme plan §16: "Platform
Admin configures... in active Symgov context with step-up and audit").

Every mutation here emits an `AuditEvent`, per spec §8.1's own instruction
to extend `AuditEvent` coverage to "agent finding resolution." No mutation
in this module ever performs a governed action itself (publish, approve,
demote, grant/revoke a role) -- FR-AGT-005."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AgentConfiguration, AgentFinding, AuditEvent, User

ACTIONABLE_STATUSES = ("open", "acknowledged")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _emit_audit(session: Session, *, entity_type: str, entity_id: uuid.UUID, action: str, actor_id: uuid.UUID | None, payload: dict) -> None:
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type=entity_type,
            entity_id=entity_id,
            action=action,
            actor_id=actor_id,
            payload_json=payload,
            created_at=_utc_now(),
        )
    )


# --- AgentConfiguration ---

def list_agent_configurations(session: Session, *, logical_agent_name: str | None = None) -> list[AgentConfiguration]:
    query = select(AgentConfiguration)
    if logical_agent_name is not None:
        query = query.where(AgentConfiguration.logical_agent_name == logical_agent_name)
    return list(session.execute(query.order_by(AgentConfiguration.logical_agent_name, AgentConfiguration.scope_type)).scalars().all())


def get_organization_agent_configuration(session: Session, *, logical_agent_name: str, organization_id: uuid.UUID) -> AgentConfiguration | None:
    return session.execute(
        select(AgentConfiguration).where(
            AgentConfiguration.logical_agent_name == logical_agent_name,
            AgentConfiguration.scope_type == "organization",
            AgentConfiguration.scope_id == organization_id,
        )
    ).scalar_one_or_none()


def get_platform_agent_configuration(session: Session, *, logical_agent_name: str) -> AgentConfiguration | None:
    return session.execute(
        select(AgentConfiguration).where(
            AgentConfiguration.logical_agent_name == logical_agent_name,
            AgentConfiguration.scope_type == "platform",
        )
    ).scalar_one_or_none()


def create_agent_configuration(
    session: Session,
    *,
    logical_agent_name: str,
    scope_type: str,
    scope_id: uuid.UUID | None,
    enabled: bool,
    actor_user_id: uuid.UUID,
) -> AgentConfiguration:
    if scope_type == "platform" and scope_id is not None:
        raise ValueError("A platform-scoped configuration must not carry a scope_id.")
    if scope_type == "organization" and scope_id is None:
        raise ValueError("An organization-scoped configuration requires a scope_id.")

    existing = (
        get_platform_agent_configuration(session, logical_agent_name=logical_agent_name)
        if scope_type == "platform"
        else get_organization_agent_configuration(session, logical_agent_name=logical_agent_name, organization_id=scope_id)
    )
    if existing is not None:
        raise ValueError("A configuration for this capability and scope already exists.")

    now = _utc_now()
    config = AgentConfiguration(
        id=uuid.uuid4(),
        logical_agent_name=logical_agent_name,
        scope_type=scope_type,
        scope_id=scope_id,
        enabled=enabled,
        created_at=now,
        updated_at=now,
        updated_by_user_id=actor_user_id,
    )
    session.add(config)
    session.flush()
    _emit_audit(
        session,
        entity_type="agent_configuration",
        entity_id=config.id,
        action="agent_configuration.created",
        actor_id=actor_user_id,
        payload={"logicalAgentName": logical_agent_name, "scopeType": scope_type, "scopeId": str(scope_id) if scope_id else None, "enabled": enabled},
    )
    return config


def update_agent_configuration(
    session: Session,
    config: AgentConfiguration,
    *,
    enabled: bool | None = None,
    model_alias: str | None | object = ...,
    allowed_capabilities: list[str] | None = None,
    actor_user_id: uuid.UUID,
) -> AgentConfiguration:
    before = {"enabled": config.enabled, "modelAlias": config.model_alias, "allowedCapabilities": config.allowed_capabilities_json}
    if enabled is not None:
        config.enabled = enabled
    if model_alias is not ...:
        config.model_alias = model_alias
    if allowed_capabilities is not None:
        config.allowed_capabilities_json = allowed_capabilities
    config.updated_at = _utc_now()
    config.updated_by_user_id = actor_user_id
    _emit_audit(
        session,
        entity_type="agent_configuration",
        entity_id=config.id,
        action="agent_configuration.updated",
        actor_id=actor_user_id,
        payload={"before": before, "after": {"enabled": config.enabled, "modelAlias": config.model_alias, "allowedCapabilities": config.allowed_capabilities_json}},
    )
    return config


# --- AgentFinding lifecycle ---

def list_findings(
    session: Session,
    *,
    organization_id: uuid.UUID | None = None,
    logical_agent_name: str | None = None,
    status: str | None = None,
) -> list[AgentFinding]:
    query = select(AgentFinding).join(AgentConfiguration, AgentFinding.agent_config_id == AgentConfiguration.id)
    if organization_id is not None:
        query = query.where(AgentConfiguration.scope_type == "organization", AgentConfiguration.scope_id == organization_id)
    if logical_agent_name is not None:
        query = query.where(AgentConfiguration.logical_agent_name == logical_agent_name)
    if status is not None:
        query = query.where(AgentFinding.status == status)
    return list(session.execute(query.order_by(AgentFinding.last_seen_at.desc())).scalars().all())


def acknowledge_finding(session: Session, finding: AgentFinding, *, actor_user_id: uuid.UUID) -> AgentFinding:
    if finding.status != "open":
        raise ValueError("Only an open finding can be acknowledged.")
    now = _utc_now()
    finding.status = "acknowledged"
    finding.acknowledged_at = now
    finding.acknowledged_by_user_id = actor_user_id
    _emit_audit(session, entity_type="agent_finding", entity_id=finding.id, action="agent_finding.acknowledged", actor_id=actor_user_id, payload={})
    return finding


def dismiss_finding(session: Session, finding: AgentFinding, *, reason: str, actor_user_id: uuid.UUID) -> AgentFinding:
    if finding.status not in ACTIONABLE_STATUSES:
        raise ValueError("Only an open or acknowledged finding can be dismissed.")
    now = _utc_now()
    finding.status = "dismissed"
    finding.dismissed_at = now
    finding.dismissed_by_user_id = actor_user_id
    finding.dismiss_reason = reason
    _emit_audit(session, entity_type="agent_finding", entity_id=finding.id, action="agent_finding.dismissed", actor_id=actor_user_id, payload={"reason": reason})
    return finding


def resolve_finding(session: Session, finding: AgentFinding, *, actor_user_id: uuid.UUID) -> AgentFinding:
    if finding.status not in ACTIONABLE_STATUSES:
        raise ValueError("Only an open or acknowledged finding can be resolved.")
    now = _utc_now()
    finding.status = "resolved"
    finding.resolved_at = now
    finding.resolved_by_user_id = actor_user_id
    _emit_audit(session, entity_type="agent_finding", entity_id=finding.id, action="agent_finding.resolved", actor_id=actor_user_id, payload={})
    return finding


def escalate_finding(
    session: Session,
    finding: AgentFinding,
    *,
    issue_reference: str | None,
    assignee_user_id: uuid.UUID | None,
    actor_user_id: uuid.UUID,
) -> AgentFinding:
    """Records a durable, human-actionable escalation on the finding
    itself (`assignee_user_id`/`issue_reference`, already part of
    `AgentFinding`'s own schema) -- FR-AGT-010's "route to Ed/Alfi" is not
    wired to any live Hermes agent identity in this stage (Stage 10 plan §4
    Q5: this repository's own commits stop at repo-side readiness), so this
    is the seam a later ops-level integration can build on, not a call to
    any external system. Never itself performs a governed action."""
    if finding.status not in ACTIONABLE_STATUSES:
        raise ValueError("Only an open or acknowledged finding can be escalated.")
    if assignee_user_id is not None and session.get(User, assignee_user_id) is None:
        raise ValueError("Assignee user not found.")
    if issue_reference is not None:
        finding.issue_reference = issue_reference
    if assignee_user_id is not None:
        finding.assignee_user_id = assignee_user_id
    _emit_audit(
        session,
        entity_type="agent_finding",
        entity_id=finding.id,
        action="agent_finding.escalated",
        actor_id=actor_user_id,
        payload={"issueReference": issue_reference, "assigneeUserId": str(assignee_user_id) if assignee_user_id else None},
    )
    return finding
