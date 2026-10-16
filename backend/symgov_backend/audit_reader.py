"""Reading the audit trail (backlog F1.5).

`audit_events` records who did what to which entity, but not which
organization it concerned: only the entity says that. So an organization's
view is worked out at read time, entity type by entity type, from the entity's
own owner. Only the entity types listed in `ORGANIZATION_ENTITY_TYPES` are ever
shown to an organization admin; every other type (agent findings, review
cases, platform role changes, ...) is visible to the platform admin only, so a
type added later is private until someone decides otherwise.

Payloads are filtered through an allow-list of keys with small values, and CSV
cells that a spreadsheet would execute are neutralised.
"""

from __future__ import annotations

import csv
from datetime import date, datetime, time, timedelta, timezone
import io
import uuid

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .models import (
    AuditEvent,
    CatalogApiKeyHolder,
    GovernedSymbol,
    OrganizationMemberCapability,
    OrganizationMembership,
    OrganizationRoleAssignment,
    OrganizationSymbolReviewSubmission,
    Project,
    SymbolSet,
    SymbolSetItem,
    User,
)

MAX_PAGE_SIZE = 200
MAX_CSV_DAYS = 31
MAX_CSV_ROWS = 20000

ORGANIZATION_ENTITY_TYPES = (
    "organization",
    "organization_membership",
    "organization_role_assignment",
    "organization_member_capability",
    "project",
    "symbol_set",
    "symbol_set_item",
    "governed_symbol",
    "organization_symbol_review_submission",
    "catalog_api_key",
)

PAYLOAD_KEYS = frozenset({
    "organizationId", "projectId", "symbolSetId", "affectedSymbolIds", "beforeItemCount", "afterItemCount",
    "reason", "source", "before", "after", "effective_authority", "actor_type", "api_key_id", "key_prefix",
    "integration_name", "scopes", "organization_id", "holder_user_id", "previous_stage", "role", "baseRole",
    "capability", "status", "name", "code",
})
_MAX_VALUE_CHARS = 200
_MAX_LIST = 20


class AuditReaderError(ValueError):
    """The request cannot be answered as asked (bad range, too many rows)."""


def _owned_by(organization_id: uuid.UUID):
    """The condition 'this audit row concerns this organization', by entity type."""
    membership_ids = select(OrganizationMembership.id).where(OrganizationMembership.organization_id == organization_id)
    return or_(
        and_(AuditEvent.entity_type == "organization", AuditEvent.entity_id == organization_id),
        and_(AuditEvent.entity_type == "organization_membership", AuditEvent.entity_id.in_(membership_ids)),
        and_(
            AuditEvent.entity_type == "organization_role_assignment",
            AuditEvent.entity_id.in_(select(OrganizationRoleAssignment.id).where(OrganizationRoleAssignment.membership_id.in_(membership_ids))),
        ),
        and_(
            AuditEvent.entity_type == "organization_member_capability",
            AuditEvent.entity_id.in_(select(OrganizationMemberCapability.id).where(OrganizationMemberCapability.membership_id.in_(membership_ids))),
        ),
        and_(AuditEvent.entity_type == "project", AuditEvent.entity_id.in_(select(Project.id).where(Project.organization_id == organization_id))),
        and_(AuditEvent.entity_type == "symbol_set", AuditEvent.entity_id.in_(select(SymbolSet.id).where(SymbolSet.owner_organization_id == organization_id))),
        and_(
            AuditEvent.entity_type == "symbol_set_item",
            AuditEvent.entity_id.in_(
                select(SymbolSetItem.id).where(
                    SymbolSetItem.symbol_set_id.in_(select(SymbolSet.id).where(SymbolSet.owner_organization_id == organization_id))
                )
            ),
        ),
        and_(
            AuditEvent.entity_type == "governed_symbol",
            AuditEvent.entity_id.in_(select(GovernedSymbol.id).where(GovernedSymbol.owner_organization_id == organization_id)),
        ),
        and_(
            AuditEvent.entity_type == "organization_symbol_review_submission",
            AuditEvent.entity_id.in_(
                select(OrganizationSymbolReviewSubmission.id).where(OrganizationSymbolReviewSubmission.organization_id == organization_id)
            ),
        ),
        and_(
            AuditEvent.entity_type == "catalog_api_key",
            AuditEvent.entity_id.in_(select(CatalogApiKeyHolder.api_key_id).where(CatalogApiKeyHolder.organization_id == organization_id)),
        ),
    )


def safe_payload(payload: object) -> dict:
    """Only allow-listed keys, and only small plain values."""
    if not isinstance(payload, dict):
        return {}
    cleaned: dict = {}
    for key, value in payload.items():
        if key not in PAYLOAD_KEYS:
            continue
        if isinstance(value, (bool, int)) or value is None:
            cleaned[key] = value
        elif isinstance(value, str):
            cleaned[key] = value[:_MAX_VALUE_CHARS]
        elif isinstance(value, list):
            cleaned[key] = [str(item)[:_MAX_VALUE_CHARS] for item in value[:_MAX_LIST] if isinstance(item, (str, int, bool))]
        elif isinstance(value, dict):
            cleaned[key] = {
                str(inner_key)[:60]: (inner if isinstance(inner, (bool, int)) else str(inner)[:_MAX_VALUE_CHARS])
                for inner_key, inner in list(value.items())[:_MAX_LIST]
            }
    return cleaned


def _row_view(event: AuditEvent, actor: User | None) -> dict:
    return {
        "id": str(event.id),
        "occurredAt": event.created_at.isoformat() if event.created_at else None,
        "entityType": event.entity_type,
        "entityId": str(event.entity_id),
        "action": event.action,
        "actorUserId": str(event.actor_id) if event.actor_id else None,
        "actorName": actor.display_name if actor is not None else None,
        "actorEmail": actor.email if actor is not None else None,
        "details": safe_payload(event.payload_json),
    }


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=timezone.utc)


def _query(
    session: Session,
    *,
    organization_id: uuid.UUID | None,
    entity_type: str | None,
    action: str | None,
    actor_id: uuid.UUID | None,
    since: date | None,
    until: date | None,
):
    query = session.query(AuditEvent, User).outerjoin(User, User.id == AuditEvent.actor_id)
    if organization_id is not None:
        query = query.filter(AuditEvent.entity_type.in_(ORGANIZATION_ENTITY_TYPES), _owned_by(organization_id))
    if entity_type:
        query = query.filter(AuditEvent.entity_type == entity_type)
    if action:
        query = query.filter(AuditEvent.action == action)
    if actor_id:
        query = query.filter(AuditEvent.actor_id == actor_id)
    if since:
        query = query.filter(AuditEvent.created_at >= _midnight(since))
    if until:
        query = query.filter(AuditEvent.created_at < _midnight(until) + timedelta(days=1))
    return query.order_by(AuditEvent.created_at.desc(), AuditEvent.id)


def read_audit(
    session: Session,
    *,
    organization_id: uuid.UUID | None,
    entity_type: str | None = None,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
    since: date | None = None,
    until: date | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """One page of the trail. `organization_id=None` is the platform admin's view of everything."""
    if organization_id is not None and entity_type and entity_type not in ORGANIZATION_ENTITY_TYPES:
        return {"items": [], "page": page, "pageSize": page_size, "total": 0, "entityTypes": list(ORGANIZATION_ENTITY_TYPES)}
    page_size = max(1, min(page_size, MAX_PAGE_SIZE))
    query = _query(session, organization_id=organization_id, entity_type=entity_type, action=action, actor_id=actor_id, since=since, until=until)
    total = query.count()
    rows = query.offset((max(page, 1) - 1) * page_size).limit(page_size).all()
    return {
        "items": [_row_view(event, actor) for event, actor in rows],
        "page": page,
        "pageSize": page_size,
        "total": total,
        "entityTypes": list(ORGANIZATION_ENTITY_TYPES) if organization_id is not None else None,
    }


def csv_safe(value: object) -> str:
    """A cell a spreadsheet will show, never run."""
    text = "" if value is None else str(value)
    text = text.replace("\r", " ").replace("\n", " ")
    if text[:1] in {"=", "+", "-", "@", "\t"}:
        return "'" + text
    return text


def audit_csv(
    session: Session,
    *,
    organization_id: uuid.UUID | None,
    since: date,
    until: date,
    entity_type: str | None = None,
    action: str | None = None,
    actor_id: uuid.UUID | None = None,
) -> str:
    if until < since:
        raise AuditReaderError("until must not be before since.")
    if (until - since).days + 1 > MAX_CSV_DAYS:
        raise AuditReaderError(f"A CSV export covers at most {MAX_CSV_DAYS} days.")
    query = _query(session, organization_id=organization_id, entity_type=entity_type, action=action, actor_id=actor_id, since=since, until=until)
    rows = query.limit(MAX_CSV_ROWS + 1).all()
    if len(rows) > MAX_CSV_ROWS:
        raise AuditReaderError(f"More than {MAX_CSV_ROWS} events in that range; narrow the dates or filters.")
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["occurred_at", "entity_type", "entity_id", "action", "actor_name", "actor_email", "details"])
    for event, actor in rows:
        view = _row_view(event, actor)
        details = "; ".join(f"{key}={value}" for key, value in view["details"].items())
        writer.writerow([csv_safe(value) for value in (
            view["occurredAt"], view["entityType"], view["entityId"], view["action"], view["actorName"], view["actorEmail"], details,
        )])
    return buffer.getvalue()
