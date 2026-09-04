"""Stage 9 WP9.2/WP9.3 -- shared `ProductUsageEvent` emission helpers.

Two tiers, matching the programme plan's own two-reliability-tier split
(line 916) and this stage's plan §1.3:

- `record_governance_usage_event` (WP9.2): governance-mutation events.
  Every call site adds the returned row to `session` and lets the caller's
  own existing flush/commit persist it in the *same* transaction as the
  mutation itself -- this function never commits on its own, mirroring how
  `AuditEvent`/`audit()`/`_emit_audit()` are already added inline throughout
  the governance service modules. `session_mode` is fixed to `'organization'`
  for every row it writes, regardless of the acting user's own literal
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

- `record_browse_usage_event`/`record_browse_usage_event_best_effort`
  (WP9.3): low-value browse events (preview, download, Favorite change,
  passive context resolution). Unlike the governance tier, `session_mode`/
  `organization_id` here are derived from the acting user's own *literal*
  session state (`current_user.session_mode`/`current_user.active_organization_id`),
  since these events describe what the user themselves actually did in
  their own session, not a fixed governance-relevant organization. The
  `_best_effort` wrapper commits immediately and swallows any exception
  (rolling back only the usage-event insert), mirroring this repository's
  own existing `log_catalog_usage_event_best_effort` pattern for
  `CatalogApiUsageEvent` -- a bug in usage-event recording must never break
  the real preview/download/Favorite response it rides alongside, per the
  plan's own "bounded... with documented loss behavior" browse-tier
  standard (§1.3).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

from .models import ProductUsageEvent

if TYPE_CHECKING:
    from .auth import AuthenticatedUser


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


def record_browse_usage_event_for_session(
    session: Session,
    *,
    event_type: str,
    user_id: uuid.UUID,
    session_mode: str,
    organization_id: uuid.UUID | None,
    project_id: uuid.UUID | None = None,
    symbol_set_id: uuid.UUID | None = None,
    governed_symbol_id: uuid.UUID | None = None,
    symbol_revision_id: uuid.UUID | None = None,
    symbol_source: str | None = None,
    format: str | None = None,
    favourite_action: str | None = None,
    context_resolution_basis: str | None = None,
    occurred_at: datetime | None = None,
) -> ProductUsageEvent:
    """Low-level browse-tier constructor taking already-derived session
    fields directly -- shared by `record_browse_usage_event` (derives them
    from an `AuthenticatedUser`) and `symbol_context_service.get_context`
    (derives them from a `Stage4Principal`, which is always organization-
    bound by construction, so has no literal-session-mode ambiguity to
    resolve)."""
    row = ProductUsageEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        occurred_at=occurred_at or datetime.now(timezone.utc),
        session_mode=session_mode,
        user_id=user_id,
        organization_id=organization_id,
        project_id=project_id,
        symbol_set_id=symbol_set_id,
        governed_symbol_id=governed_symbol_id,
        symbol_revision_id=symbol_revision_id,
        symbol_source=symbol_source,
        format=format,
        favourite_action=favourite_action,
        context_resolution_basis=context_resolution_basis,
    )
    session.add(row)
    return row


def record_browse_usage_event(
    session: Session,
    *,
    event_type: str,
    current_user: "AuthenticatedUser",
    project_id: uuid.UUID | None = None,
    symbol_set_id: uuid.UUID | None = None,
    governed_symbol_id: uuid.UUID | None = None,
    symbol_revision_id: uuid.UUID | None = None,
    symbol_source: str | None = None,
    format: str | None = None,
    favourite_action: str | None = None,
    context_resolution_basis: str | None = None,
    occurred_at: datetime | None = None,
) -> ProductUsageEvent:
    organization_id = (
        uuid.UUID(current_user.active_organization_id)
        if current_user.session_mode == "organization" and current_user.active_organization_id
        else None
    )
    return record_browse_usage_event_for_session(
        session,
        event_type=event_type,
        user_id=uuid.UUID(current_user.id),
        session_mode=current_user.session_mode,
        organization_id=organization_id,
        project_id=project_id,
        symbol_set_id=symbol_set_id,
        governed_symbol_id=governed_symbol_id,
        symbol_revision_id=symbol_revision_id,
        symbol_source=symbol_source,
        format=format,
        favourite_action=favourite_action,
        context_resolution_basis=context_resolution_basis,
        occurred_at=occurred_at,
    )


def record_browse_usage_event_best_effort(session: Session, **kwargs: object) -> None:
    try:
        record_browse_usage_event(session, **kwargs)  # type: ignore[arg-type]
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass


def record_browse_usage_event_for_session_best_effort(session: Session, **kwargs: object) -> None:
    try:
        record_browse_usage_event_for_session(session, **kwargs)  # type: ignore[arg-type]
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            pass
