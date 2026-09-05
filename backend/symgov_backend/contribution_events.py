"""Stage 9 WP9.5 -- contribution/reputation ledger, badge computation, and
lifetime aggregate counters (plan §2 item 5, spec §12/§8 line 411).

Two writer entry points, both never committing on their own (same
convention as `product_usage_events.record_governance_usage_event` --
the caller's own existing transaction commits):

- `record_contribution_awarded` appends a `contribution_awarded`
  `ContributionEvent` row. Wired into
  `organization_promotion_handoff.execute_organization_promotion_handoff`
  (a symbol's public promotion being accepted is today's only trigger --
  spec §12.1's other illustrative categories are not yet built, per the
  Stage 9 plan's own confirmed scope for this package).
- `reverse_contributions_for_symbol` appends a `contribution_reversed` row
  for every currently-active (not already reversed) accepted contribution
  a governed symbol has. Wired into `symbol_demotion.execute_demotion`,
  per spec §12.2's "Demotion or invalidation may reverse contribution
  events through append-only correction records." A symbol demoted more
  than once across its lifetime (accepted, demoted, re-promoted, demoted
  again) only ever reverses whichever award rows are still active; an
  already-reversed award, or one that has since aged past this table's
  own 90-day retention purge, is left alone.

Both writers also update `OrganizationBadge` and
`OrganizationContributionTotal` synchronously, in the same transaction --
see those two models' own docstrings for why they must never be
re-derived from this (purgeable) ledger.

Per Chris's confirmed design: First Contribution and Contributor
Organization share one trigger (an organization's first-ever accepted
contribution) and are always awarded together; Community Partner and the
two badges Q3 already deferred are not computed by this module.
Reversal never revokes an already-awarded badge -- left to WP9.6.

Stage 9 WP9.8 additionally maintains `UserContributionTotal`, a per-user
mirror of `OrganizationContributionTotal`, whenever a ledger row carries a
non-null `user_id` -- both writers update it synchronously in the same
transaction as the organization-level counter. `get_user_contributions` is
the read model behind the self-service `GET /profile/contributions`
endpoint (spec §12.2's "individual users may see private
contribution/activity statistics in their profile").
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .models import ContributionEvent, OrganizationBadge

BADGE_TYPES = ("first_contribution", "contributor_organization")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def record_contribution_awarded(
    session: Session,
    *,
    organization_id: uuid.UUID,
    submission_id: uuid.UUID,
    user_id: uuid.UUID | None = None,
    governed_symbol_id: uuid.UUID | None = None,
    symbol_revision_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
) -> ContributionEvent:
    now = occurred_at or _utc_now()
    event = ContributionEvent(
        id=uuid.uuid4(),
        organization_id=organization_id,
        user_id=user_id,
        submission_id=submission_id,
        governed_symbol_id=governed_symbol_id,
        symbol_revision_id=symbol_revision_id,
        event_type="contribution_awarded",
        occurred_at=now,
    )
    session.add(event)
    session.flush()
    _increment_accepted_total(session, organization_id=organization_id, now=now)
    if user_id is not None:
        _increment_user_accepted_total(session, user_id=user_id, now=now)
    _award_badges_if_needed(session, organization_id=organization_id, source_event_id=event.id, awarded_at=now)
    return event


def reverse_contributions_for_symbol(
    session: Session,
    *,
    governed_symbol_id: uuid.UUID,
    reason: str,
    occurred_at: datetime | None = None,
) -> list[ContributionEvent]:
    now = occurred_at or _utc_now()
    already_reversed_ids = select(ContributionEvent.reversed_event_id).where(
        ContributionEvent.reversed_event_id.is_not(None)
    )
    active_awards = (
        session.execute(
            select(ContributionEvent).where(
                ContributionEvent.governed_symbol_id == governed_symbol_id,
                ContributionEvent.event_type == "contribution_awarded",
                ContributionEvent.id.not_in(already_reversed_ids),
            )
        )
        .scalars()
        .all()
    )
    reversals: list[ContributionEvent] = []
    for award in active_awards:
        reversal = ContributionEvent(
            id=uuid.uuid4(),
            organization_id=award.organization_id,
            user_id=award.user_id,
            submission_id=award.submission_id,
            governed_symbol_id=award.governed_symbol_id,
            symbol_revision_id=award.symbol_revision_id,
            event_type="contribution_reversed",
            reversed_event_id=award.id,
            reason=reason,
            occurred_at=now,
        )
        session.add(reversal)
        reversals.append(reversal)
        _increment_reversed_total(session, organization_id=award.organization_id, now=now)
        if award.user_id is not None:
            _increment_user_reversed_total(session, user_id=award.user_id, now=now)
    if reversals:
        session.flush()
    return reversals


def _increment_accepted_total(session: Session, *, organization_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        text(
            "INSERT INTO organization_contribution_totals (organization_id, accepted_count, reversed_count, updated_at) "
            "VALUES (:organization_id, 1, 0, :updated_at) "
            "ON CONFLICT (organization_id) DO UPDATE SET "
            "accepted_count = organization_contribution_totals.accepted_count + 1, "
            "updated_at = EXCLUDED.updated_at"
        ),
        {"organization_id": organization_id, "updated_at": now},
    )


def _increment_reversed_total(session: Session, *, organization_id: uuid.UUID, now: datetime) -> None:
    # `reverse_contributions_for_symbol` only ever reverses a symbol that
    # has an active accepted contribution, so this organization's totals
    # row always already exists with accepted_count >= 1 by the time this
    # runs -- the INSERT branch below is never really taken in practice.
    # Its candidate values must still independently satisfy
    # `ck_organization_contribution_totals_reversed_le_accepted`, though:
    # Postgres validates CHECK constraints against the tentative INSERT
    # row *before* ON CONFLICT resolution even runs, regardless of whether
    # a conflict (and the resulting UPDATE-from-existing-row) will
    # ultimately be what actually happens. A literal (0, 1) candidate --
    # correct only *after* combining with an existing row's
    # accepted_count -- fails that a-priori check on its own, even though
    # the always-taken conflict path would have produced a valid final
    # row. Using (1, 1) as a self-consistent placeholder candidate sidesteps
    # this; the real, correct final counts always come from the
    # ON CONFLICT DO UPDATE SET clause below, which never touches
    # accepted_count and instead increments the existing row's own
    # reversed_count.
    session.execute(
        text(
            "INSERT INTO organization_contribution_totals (organization_id, accepted_count, reversed_count, updated_at) "
            "VALUES (:organization_id, 1, 1, :updated_at) "
            "ON CONFLICT (organization_id) DO UPDATE SET "
            "reversed_count = organization_contribution_totals.reversed_count + 1, "
            "updated_at = EXCLUDED.updated_at"
        ),
        {"organization_id": organization_id, "updated_at": now},
    )


def _increment_user_accepted_total(session: Session, *, user_id: uuid.UUID, now: datetime) -> None:
    session.execute(
        text(
            "INSERT INTO user_contribution_totals (user_id, accepted_count, reversed_count, updated_at) "
            "VALUES (:user_id, 1, 0, :updated_at) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "accepted_count = user_contribution_totals.accepted_count + 1, "
            "updated_at = EXCLUDED.updated_at"
        ),
        {"user_id": user_id, "updated_at": now},
    )


def _increment_user_reversed_total(session: Session, *, user_id: uuid.UUID, now: datetime) -> None:
    # Same a-priori CHECK-constraint-on-the-candidate-row caveat as
    # _increment_reversed_total above: the (1, 1) candidate is a
    # self-consistent placeholder, never the real final value -- the real
    # counts always come from the ON CONFLICT DO UPDATE SET clause below.
    session.execute(
        text(
            "INSERT INTO user_contribution_totals (user_id, accepted_count, reversed_count, updated_at) "
            "VALUES (:user_id, 1, 1, :updated_at) "
            "ON CONFLICT (user_id) DO UPDATE SET "
            "reversed_count = user_contribution_totals.reversed_count + 1, "
            "updated_at = EXCLUDED.updated_at"
        ),
        {"user_id": user_id, "updated_at": now},
    )


def _award_badges_if_needed(
    session: Session, *, organization_id: uuid.UUID, source_event_id: uuid.UUID, awarded_at: datetime
) -> None:
    for badge_type in BADGE_TYPES:
        session.execute(
            text(
                "INSERT INTO organization_badges (id, organization_id, badge_type, awarded_at, source_event_id) "
                "VALUES (:id, :organization_id, :badge_type, :awarded_at, :source_event_id) "
                "ON CONFLICT (organization_id, badge_type) DO NOTHING"
            ),
            {
                "id": uuid.uuid4(),
                "organization_id": organization_id,
                "badge_type": badge_type,
                "awarded_at": awarded_at,
                "source_event_id": source_event_id,
            },
        )


def get_organization_contributions(session: Session, organization_id: uuid.UUID) -> dict:
    """Read model for `GET /org/me/contributions` /
    `GET /platform/organizations/{id}/contributions`. Reads only
    `OrganizationContributionTotal`/`OrganizationBadge` -- never the raw
    `ContributionEvent` ledger -- so the response survives this ledger's
    own 90-day retention purge unchanged."""
    totals = session.execute(
        text(
            "SELECT accepted_count, reversed_count FROM organization_contribution_totals "
            "WHERE organization_id = :organization_id"
        ),
        {"organization_id": organization_id},
    ).mappings().one_or_none()
    accepted_count = totals["accepted_count"] if totals else 0
    reversed_count = totals["reversed_count"] if totals else 0

    badges = (
        session.execute(
            select(OrganizationBadge)
            .where(OrganizationBadge.organization_id == organization_id)
            .order_by(OrganizationBadge.awarded_at)
        )
        .scalars()
        .all()
    )

    return {
        "organizationId": str(organization_id),
        "acceptedContributionCount": accepted_count,
        "reversedContributionCount": reversed_count,
        "badges": [
            {"badgeType": badge.badge_type, "awardedAt": badge.awarded_at.isoformat()} for badge in badges
        ],
    }


def get_user_contributions(session: Session, user_id: uuid.UUID) -> dict:
    """Read model for the self-service `GET /profile/contributions`
    (Stage 9 WP9.8, spec §12.2). Reads only `user_contribution_totals` --
    never the raw `ContributionEvent` ledger -- so the response survives
    that ledger's own 90-day retention purge unchanged, mirroring
    `get_organization_contributions`'s own design. No badges here: §12.2
    lists badges under "Organization badges", a separate organization-level
    concept already served by `GET /org/me/contributions`."""
    totals = session.execute(
        text(
            "SELECT accepted_count, reversed_count FROM user_contribution_totals "
            "WHERE user_id = :user_id"
        ),
        {"user_id": user_id},
    ).mappings().one_or_none()
    accepted_count = totals["accepted_count"] if totals else 0
    reversed_count = totals["reversed_count"] if totals else 0

    return {
        "acceptedContributionCount": accepted_count,
        "reversedContributionCount": reversed_count,
    }
