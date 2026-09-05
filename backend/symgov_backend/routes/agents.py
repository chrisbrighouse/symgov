"""Stage 10 WP10.4 -- Organization Steward / Platform Governance routes.

Split mirrors WP9.4/WP9.5's own dual-route precedent: `/org/me/...` is
self-scoped (Organization Admin sees/actions only their own organization's
findings, and triggers Organization Steward for their own organization
only), `/platform/...` is Platform Admin's broader authority (any
organization's findings, Platform Governance's own platform-scoped
findings, and all `AgentConfiguration` management). Per I-25,
`AgentConfiguration` mutations require step-up ("agent model/policy
changes"); finding acknowledge/dismiss/resolve/escalate are considered
day-to-day operator actions, not policy changes, and do not require it,
mirroring how ordinary review decisions elsewhere in this repository are
not step-up-gated while role/policy grants are.

FR-AGT-005 boundary: nothing in this file can publish/approve/demote/grant
a role/change tenant policy -- every mutation here only changes
`AgentConfiguration`/`AgentFinding` rows and their own audit trail."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..agent_governance_service import (
    acknowledge_finding,
    create_agent_configuration,
    dismiss_finding,
    escalate_finding,
    get_organization_agent_configuration,
    get_platform_agent_configuration,
    list_agent_configurations,
    list_findings,
    resolve_finding,
    update_agent_configuration,
)
from ..auth import AuthenticatedUser
from ..dependencies import get_db_session, require_organization_admin, require_platform_admin, require_recent_step_up
from ..models import AgentConfiguration, AgentFinding
from ..organization_service import get_organization_detail
from ..organization_steward import run_organization_steward
from ..platform_governance import run_platform_governance
from ..schemas import (
    AgentConfigurationItem,
    AgentConfigurationListResponse,
    AgentFindingItem,
    AgentFindingListResponse,
    CreateAgentConfigurationRequest,
    DismissAgentFindingRequest,
    EscalateAgentFindingRequest,
    RunAgentResponse,
    UpdateAgentConfigurationRequest,
)
from ..settings import SymgovAPISettings, get_settings

router = APIRouter(tags=["agents"])


def _require_agents_enabled(settings: SymgovAPISettings = Depends(get_settings)) -> None:
    if not (settings.organizations_enabled and settings.organization_agents_enabled):
        raise HTTPException(status_code=404, detail="Not found.")


def _active_org_id(current_user: AuthenticatedUser) -> uuid.UUID:
    if current_user.active_organization_id is None:
        raise HTTPException(status_code=409, detail="No active organization session.")
    return uuid.UUID(current_user.active_organization_id)


def _config_item(config: AgentConfiguration) -> AgentConfigurationItem:
    return AgentConfigurationItem(
        id=str(config.id),
        logicalAgentName=config.logical_agent_name,
        scopeType=config.scope_type,
        scopeId=str(config.scope_id) if config.scope_id else None,
        enabled=config.enabled,
        modelAlias=config.model_alias,
        allowedCapabilities=list(config.allowed_capabilities_json or []),
        updatedAt=config.updated_at,
    )


def _finding_item(session: Session, finding: AgentFinding) -> AgentFindingItem:
    config = session.get(AgentConfiguration, finding.agent_config_id)
    return AgentFindingItem(
        id=str(finding.id),
        agentConfigId=str(finding.agent_config_id),
        logicalAgentName=config.logical_agent_name if config else "",
        severity=finding.severity,
        findingType=finding.finding_type,
        entityType=finding.entity_type,
        entityId=str(finding.entity_id),
        summary=finding.summary,
        evidence=finding.evidence_json or {},
        policyVersion=finding.policy_version,
        status=finding.status,
        firstSeenAt=finding.first_seen_at,
        lastSeenAt=finding.last_seen_at,
        acknowledgedAt=finding.acknowledged_at,
        dismissedAt=finding.dismissed_at,
        dismissReason=finding.dismiss_reason,
        resolvedAt=finding.resolved_at,
        supersededByFindingId=str(finding.superseded_by_finding_id) if finding.superseded_by_finding_id else None,
        assigneeUserId=str(finding.assignee_user_id) if finding.assignee_user_id else None,
        issueReference=finding.issue_reference,
    )


def _get_finding_or_404(session: Session, finding_id: str) -> AgentFinding:
    try:
        parsed_id = uuid.UUID(finding_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid finding ID.") from exc
    finding = session.get(AgentFinding, parsed_id)
    if finding is None:
        raise HTTPException(status_code=404, detail="Finding not found.")
    return finding


# --- Organization Admin: self-scoped ---

@router.get("/org/me/agent-findings", response_model=AgentFindingListResponse, dependencies=[Depends(_require_agents_enabled)])
def list_org_agent_findings(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> AgentFindingListResponse:
    org_id = _active_org_id(current_user)
    findings = list_findings(session, organization_id=org_id, logical_agent_name="organization_steward")
    return AgentFindingListResponse(items=[_finding_item(session, f) for f in findings])


@router.post("/org/me/agents/organization-steward/run", response_model=RunAgentResponse, dependencies=[Depends(_require_agents_enabled)])
def run_org_steward_route(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> RunAgentResponse:
    org_id = _active_org_id(current_user)
    config = get_organization_agent_configuration(session, logical_agent_name="organization_steward", organization_id=org_id)
    if config is None or not config.enabled:
        raise HTTPException(status_code=409, detail="Organization Steward is not enabled for this organization.")
    touched = run_organization_steward(session, config)
    session.commit()
    return RunAgentResponse(touchedFindingIds=[str(f.id) for f in touched])


def _org_finding_action(session: Session, current_user: AuthenticatedUser, finding_id: str):
    org_id = _active_org_id(current_user)
    finding = _get_finding_or_404(session, finding_id)
    config = session.get(AgentConfiguration, finding.agent_config_id)
    if config is None or config.scope_type != "organization" or config.scope_id != org_id:
        raise HTTPException(status_code=404, detail="Finding not found.")
    return finding


@router.post("/org/me/agent-findings/{finding_id}/acknowledge", response_model=AgentFindingItem, dependencies=[Depends(_require_agents_enabled)])
def acknowledge_org_finding(
    finding_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> AgentFindingItem:
    finding = _org_finding_action(session, current_user, finding_id)
    try:
        acknowledge_finding(session, finding, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _finding_item(session, finding)


@router.post("/org/me/agent-findings/{finding_id}/dismiss", response_model=AgentFindingItem, dependencies=[Depends(_require_agents_enabled)])
def dismiss_org_finding(
    finding_id: str,
    body: DismissAgentFindingRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> AgentFindingItem:
    finding = _org_finding_action(session, current_user, finding_id)
    try:
        dismiss_finding(session, finding, reason=body.reason, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _finding_item(session, finding)


@router.post("/org/me/agent-findings/{finding_id}/resolve", response_model=AgentFindingItem, dependencies=[Depends(_require_agents_enabled)])
def resolve_org_finding(
    finding_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_organization_admin),
) -> AgentFindingItem:
    finding = _org_finding_action(session, current_user, finding_id)
    try:
        resolve_finding(session, finding, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _finding_item(session, finding)


# --- Platform Admin: broader authority ---

@router.get("/platform/agent-configurations", response_model=AgentConfigurationListResponse, dependencies=[Depends(_require_agents_enabled)])
def list_platform_agent_configurations(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> AgentConfigurationListResponse:
    return AgentConfigurationListResponse(items=[_config_item(c) for c in list_agent_configurations(session)])


@router.post("/platform/agent-configurations", response_model=AgentConfigurationItem, status_code=201, dependencies=[Depends(_require_agents_enabled)])
def create_platform_agent_configuration(
    body: CreateAgentConfigurationRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> AgentConfigurationItem:
    scope_id = None
    if body.scopeType == "organization":
        if body.scopeId is None:
            raise HTTPException(status_code=400, detail="scopeId is required for an organization-scoped configuration.")
        try:
            scope_id = uuid.UUID(body.scopeId)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid scopeId.") from exc
        if get_organization_detail(session, scope_id) is None:
            raise HTTPException(status_code=404, detail="Organization not found.")
    try:
        config = create_agent_configuration(
            session,
            logical_agent_name=body.logicalAgentName,
            scope_type=body.scopeType,
            scope_id=scope_id,
            enabled=body.enabled,
            actor_user_id=uuid.UUID(current_user.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _config_item(config)


@router.patch("/platform/agent-configurations/{config_id}", response_model=AgentConfigurationItem, dependencies=[Depends(_require_agents_enabled)])
def patch_platform_agent_configuration(
    config_id: str,
    body: UpdateAgentConfigurationRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
    _step_up: AuthenticatedUser = Depends(require_recent_step_up),
) -> AgentConfigurationItem:
    try:
        parsed_id = uuid.UUID(config_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid configuration ID.") from exc
    config = session.get(AgentConfiguration, parsed_id)
    if config is None:
        raise HTTPException(status_code=404, detail="Configuration not found.")
    model_alias_update = body.modelAlias if "modelAlias" in body.model_fields_set else ...
    update_agent_configuration(
        session,
        config,
        enabled=body.enabled,
        model_alias=model_alias_update,
        allowed_capabilities=body.allowedCapabilities,
        actor_user_id=uuid.UUID(current_user.id),
    )
    session.commit()
    return _config_item(config)


@router.get("/platform/agent-findings", response_model=AgentFindingListResponse, dependencies=[Depends(_require_agents_enabled)])
def list_platform_agent_findings(
    organizationId: str | None = None,
    logicalAgentName: str | None = None,
    status: str | None = None,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> AgentFindingListResponse:
    org_id = None
    if organizationId is not None:
        try:
            org_id = uuid.UUID(organizationId)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid organizationId.") from exc
    findings = list_findings(session, organization_id=org_id, logical_agent_name=logicalAgentName, status=status)
    return AgentFindingListResponse(items=[_finding_item(session, f) for f in findings])


@router.post("/platform/organizations/{organization_id}/agents/organization-steward/run", response_model=RunAgentResponse, dependencies=[Depends(_require_agents_enabled)])
def run_platform_org_steward_route(
    organization_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> RunAgentResponse:
    try:
        org_id = uuid.UUID(organization_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid organization ID.") from exc
    config = get_organization_agent_configuration(session, logical_agent_name="organization_steward", organization_id=org_id)
    if config is None or not config.enabled:
        raise HTTPException(status_code=409, detail="Organization Steward is not enabled for this organization.")
    touched = run_organization_steward(session, config)
    session.commit()
    return RunAgentResponse(touchedFindingIds=[str(f.id) for f in touched])


@router.post("/platform/agents/platform-governance/run", response_model=RunAgentResponse, dependencies=[Depends(_require_agents_enabled)])
def run_platform_governance_route(
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> RunAgentResponse:
    config = get_platform_agent_configuration(session, logical_agent_name="platform_governance")
    if config is None or not config.enabled:
        raise HTTPException(status_code=409, detail="Platform Governance is not enabled.")
    touched = run_platform_governance(session, config)
    session.commit()
    return RunAgentResponse(touchedFindingIds=[str(f.id) for f in touched])


@router.post("/platform/agent-findings/{finding_id}/acknowledge", response_model=AgentFindingItem, dependencies=[Depends(_require_agents_enabled)])
def acknowledge_platform_finding(
    finding_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> AgentFindingItem:
    finding = _get_finding_or_404(session, finding_id)
    try:
        acknowledge_finding(session, finding, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _finding_item(session, finding)


@router.post("/platform/agent-findings/{finding_id}/dismiss", response_model=AgentFindingItem, dependencies=[Depends(_require_agents_enabled)])
def dismiss_platform_finding(
    finding_id: str,
    body: DismissAgentFindingRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> AgentFindingItem:
    finding = _get_finding_or_404(session, finding_id)
    try:
        dismiss_finding(session, finding, reason=body.reason, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _finding_item(session, finding)


@router.post("/platform/agent-findings/{finding_id}/resolve", response_model=AgentFindingItem, dependencies=[Depends(_require_agents_enabled)])
def resolve_platform_finding(
    finding_id: str,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> AgentFindingItem:
    finding = _get_finding_or_404(session, finding_id)
    try:
        resolve_finding(session, finding, actor_user_id=uuid.UUID(current_user.id))
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _finding_item(session, finding)


@router.post("/platform/agent-findings/{finding_id}/escalate", response_model=AgentFindingItem, dependencies=[Depends(_require_agents_enabled)])
def escalate_platform_finding(
    finding_id: str,
    body: EscalateAgentFindingRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_platform_admin),
) -> AgentFindingItem:
    finding = _get_finding_or_404(session, finding_id)
    assignee_user_id = None
    if body.assigneeUserId is not None:
        try:
            assignee_user_id = uuid.UUID(body.assigneeUserId)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid assigneeUserId.") from exc
    try:
        escalate_finding(
            session,
            finding,
            issue_reference=body.issueReference,
            assignee_user_id=assignee_user_id,
            actor_user_id=uuid.UUID(current_user.id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.commit()
    return _finding_item(session, finding)
