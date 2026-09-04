"""Stage 9 WP9.2 -- shared `ProductUsageEvent` emission helper for
governance-mutation events.

Every call site adds the returned row to `session` and lets the caller's
own existing flush/commit persist it in the *same* transaction as the
mutation itself -- this module never commits on its own, mirroring how
`AuditEvent`/`audit()`/`_emit_audit()` are already added inline throughout
the governance service modules.

`session_mode` is fixed to `'organization'` for every governance-lifecycle
event this module emits, regardless of the acting user's own literal
session mode. This is a deliberate modeling choice, not an oversight: two
sites in WP9.2's scope (`symbol_demotion.execute_demotion`, and
`organization_service.assign_platform_admin`/`revoke_platform_admin`) are
platform-admin actions that do not require the actor's *own* session to be
organization-bound (`require_platform_admin` only requires `require_user`).
`session_mode`/`organization_id` here describe which organization the
*event itself* concerns, not the caller's own request-time session state --
every governance-lifecycle event in this module's scope concerns exactly
one real organization (the symbol's owning organization, or the protected
`symgov` organization for platform-admin actions), so `organization_id` is
always populated and `session_mode` is always `'organization'`, matching
`ProductUsageEvent`'s own `ck_product_usage_events_session_mode_organization`
constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import ProductUsageEvent


def record_governance_usage_event(
    session: Session,
    *,
    event_type: str,
    user_id: uuid.UUID,
    organization_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
    symbol_set_id: uuid.UUID | None = None,
    governed_symbol_id: uuid.UUID | None = None,
    symbol_revision_id: uuid.UUID | None = None,
    symbol_source: str | None = None,
    context_resolution_basis: str | None = None,
    occurred_at: datetime | None = None,
) -> ProductUsageEvent:
    row = ProductUsageEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        session_mode="organization",
        user_id=user_id,
        organization_id=organization_id,
        project_id=project_id,
        symbol_set_id=symbol_set_id,
        governed_symbol_id=governed_symbol_id,
        symbol_revision_id=symbol_revision_id,
        symbol_source=symbol_source,
        context_resolution_basis=context_resolution_basis,
    )
    session.add(row)
    return row
