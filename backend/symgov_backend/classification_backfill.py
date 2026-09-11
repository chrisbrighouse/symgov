"""Legacy classification backfill (SM-P0-10).

Specification section 12.1 phase M2: propose `SymbolRevisionClassificationAssignment`
rows for symbols that already exist, derived from the `GovernedSymbol.category`
and `.discipline` columns they were promoted with. Section 15.1 calls this a
*utility* rather than a schema change, and that is what it is -- no migration,
no new table, no new column. Everything it needs was added by SM-P0-04.

Four properties shape it.

**The rules are SM-P0-07's rules, not a second set.** Every value goes through
`classification_mapping.plan_facet`, the same function promotion uses. That is
the point: SM-P0-09 derives `GovernedSymbol.category` back *from* whichever
assignment exists, and if the backfill and promotion disagreed about what
`piping` means, the derived legacy value would depend on how a symbol happened
to reach the catalogue rather than on what it is.

**A backfilled row can never be verified.** `method` is `legacy_backfill`, and
`ck_symbol_revision_classification_assignments_backfill_not_verified` makes
that permanent in storage -- `transition_symbol_revision_classification`
refuses the move and tells the reviewer to propose afresh with a real method.
Section 12.3 asks exactly this: existing published symbols must not be
silently reclassified as verified, and a sweep of eleven thousand rows is the
last place to relax it.

**It never overwrites a better assertion.** A revision that already carries a
non-rejected primary in the scheme is skipped with `already_assigned`,
whatever method produced it. A symbol promoted since SM-P0-07 already has a
`source_mapping` proposal from its actual review record, which is better
evidence than a column; the backfill has nothing to add and stays out.

**It targets the revisions the product displays.** The legacy columns live on
`governed_symbols`, so every revision of one symbol shares one category --
backfilling all of them would multiply near-identical rows across history for
no reader. The displayed set is the union of each symbol's
`current_revision_id` and each published page's `current_symbol_revision_id`,
the latter being what `published_catalog.PUBLISHED_SYMBOLS_SQL` joins. Private
symbols are included: this writes governance rows, exposes nothing, and
section 14.2's boundary is a property of the *read* paths it does not touch.

Dry run is the default. `run_legacy_classification_backfill` plans the whole
sweep and reports it without a write unless `apply=True`, following
`backfill-tracy-libby-review-cases`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .automation_policy import PLACEHOLDER_CATEGORIES, PLACEHOLDER_DISCIPLINES
from .classification_assignments import (
    BACKFILL_METHODS,
    propose_symbol_revision_classification,
)
from .classification_mapping import (
    MappingGap,
    plan_facet,
    resolve_node,
)
from .legacy_classification_sync import NON_DISPLAY_MATCH_BASES
from .models import (
    GovernedSymbol,
    PublishedPage,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
)

# The one method this module may write. `BACKFILL_METHODS` is the frozenset
# the check constraint mirrors; taking the single member from it rather than
# repeating the literal means a second backfill method could not be added
# behind this module's back.
(BACKFILL_METHOD,) = sorted(BACKFILL_METHODS)

BACKFILLER_VERSION = "symgov-legacy-classification-backfill-v1"

_SYMBOL_CATEGORY_SCHEME = "SYMBOL-CATEGORY-FAMILY"
_DISCIPLINE_SCHEME = "ENGINEERING-DISCIPLINE"

# The two legacy columns and the seeded scheme each one backfills. Both are
# `primary`: the one-verified-primary rule is per scheme, so a discipline and
# a category primary on one revision do not compete -- and neither can ever
# be verified anyway.
LEGACY_FACETS: tuple[dict[str, Any], ...] = (
    {
        "field": "discipline",
        "column": "discipline",
        "scheme_code": _DISCIPLINE_SCHEME,
        "placeholders": PLACEHOLDER_DISCIPLINES,
    },
    {
        "field": "category",
        "column": "category",
        "scheme_code": _SYMBOL_CATEGORY_SCHEME,
        "placeholders": PLACEHOLDER_CATEGORIES,
    },
)

# Why one facet of one revision produced no backfilled row. A *diagnostic*
# vocabulary that never reaches a `method` column: four reasons reused
# verbatim from `classification_mapping.MAPPING_GAP_REASONS`, plus
# `already_assigned`, which is this module's own and is added to that same
# frozenset rather than starting a rival one.
BACKFILL_SKIP_REASONS = frozenset(
    {"no_value", "placeholder_value", "no_node_match", "mapping_failed", "already_assigned"}
)


@dataclass(frozen=True)
class BackfillTarget:
    """One revision to sweep, with the legacy values it inherited."""

    symbol_id: uuid.UUID
    symbol_revision_id: uuid.UUID
    slug: str
    category: str | None
    discipline: str | None

    def legacy_value(self, column: str) -> str | None:
        return getattr(self, column)


@dataclass(frozen=True)
class PlannedBackfill:
    """One facet of one revision, resolved against the seeded schemes."""

    symbol_revision_id: uuid.UUID
    slug: str
    field: str
    raw_value: str
    scheme_code: str
    node_code: str
    node_label: str
    match_basis: str
    classification_node_id: uuid.UUID
    reused: bool

    @property
    def label_differs_from_column(self) -> bool:
        """Whether the matched node's label reads differently from the column.

        Not the same question as "will the catalogue change" -- see
        `would_change_column`. A label can differ for three reasons: the
        trailing-S rule (`door` -> `Doors`), an exact match on a differently
        cased value (`pumps` -> `Pumps`), or the legacy taxonomy table
        (`Piping` -> `Piping / P&ID`), and only the first two are allowed to
        reach the column.
        """
        return self.node_label != self.raw_value

    @property
    def would_change_column(self) -> bool:
        """Whether SM-P0-09 deriving this back would actually move the column.

        The count an operator wants before running with `--apply`. A
        `legacy_taxonomy` match is barred from the display column by
        `legacy_classification_sync.NON_DISPLAY_MATCH_BASES`, because those
        tables bucket for browsing -- `Cylinder`, `Stirrer` and `Envelope`
        all fold into `Equipment` -- and folding them into the column would
        destroy a distinction an operator recorded.
        """
        return self.label_differs_from_column and self.match_basis not in NON_DISPLAY_MATCH_BASES

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_revision_id": str(self.symbol_revision_id),
            "slug": self.slug,
            "field": self.field,
            "raw_value": self.raw_value,
            "scheme_code": self.scheme_code,
            "node_code": self.node_code,
            "node_label": self.node_label,
            "match_basis": self.match_basis,
            "reused": self.reused,
            "label_differs_from_column": self.label_differs_from_column,
            "would_change_column": self.would_change_column,
        }


@dataclass(frozen=True)
class SkippedBackfill:
    """One facet of one revision that produced nothing, and why."""

    symbol_revision_id: uuid.UUID
    slug: str
    field: str
    raw_value: str | None
    reason: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_revision_id": str(self.symbol_revision_id),
            "slug": self.slug,
            "field": self.field,
            "raw_value": self.raw_value,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass
class BackfillReport:
    """What a sweep did, or would do."""

    applied: bool
    targets_examined: int = 0
    planned: list[PlannedBackfill] = dataclass_field(default_factory=list)
    skipped: list[SkippedBackfill] = dataclass_field(default_factory=list)
    written_assignment_ids: list[uuid.UUID] = dataclass_field(default_factory=list)

    @property
    def label_differences(self) -> list[PlannedBackfill]:
        """Assignments whose node label reads differently from the column."""
        return [item for item in self.planned if item.label_differs_from_column]

    @property
    def expected_column_changes(self) -> list[PlannedBackfill]:
        """The subset of those that SM-P0-09 would actually write back."""
        return [item for item in self.planned if item.would_change_column]

    def skip_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.skipped:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "backfiller_version": BACKFILLER_VERSION,
            "method": BACKFILL_METHOD,
            "targets_examined": self.targets_examined,
            "assignments_planned": len(self.planned),
            "assignments_written": len(self.written_assignment_ids),
            # Two counts, deliberately. The first is how many matched labels
            # read differently from the column; the second is how many of those
            # SM-P0-09 would actually write back. They were one field until the
            # coarse-match guard landed, at which point a single number read as
            # a promise of catalogue churn that does not happen.
            "labels_differing_from_column": len(self.label_differences),
            "expected_column_changes": len(self.expected_column_changes),
            "skipped": len(self.skipped),
            "skip_counts": self.skip_counts(),
            "planned": [item.as_dict() for item in self.planned],
            "skipped_detail": [item.as_dict() for item in self.skipped],
        }


def select_backfill_targets(
    session: Session,
    *,
    limit: int | None = None,
    symbol_slug: str | None = None,
) -> list[BackfillTarget]:
    """Return the revisions this sweep covers, oldest symbol first.

    The union of each governed symbol's current revision and each published
    page's current symbol revision. A symbol usually contributes one row; it
    contributes two when its current revision is not the one still on a
    published page, and both are displayed somewhere, so both are swept.
    """
    current = (
        select(
            GovernedSymbol.id.label("symbol_id"),
            GovernedSymbol.current_revision_id.label("symbol_revision_id"),
            GovernedSymbol.slug,
            GovernedSymbol.category,
            GovernedSymbol.discipline,
            GovernedSymbol.created_at,
        )
        .where(GovernedSymbol.current_revision_id.is_not(None))
    )
    published = (
        select(
            GovernedSymbol.id.label("symbol_id"),
            PublishedPage.current_symbol_revision_id.label("symbol_revision_id"),
            GovernedSymbol.slug,
            GovernedSymbol.category,
            GovernedSymbol.discipline,
            GovernedSymbol.created_at,
        )
        .join(SymbolRevision, SymbolRevision.id == PublishedPage.current_symbol_revision_id)
        .join(GovernedSymbol, GovernedSymbol.id == SymbolRevision.symbol_id)
    )
    if symbol_slug is not None:
        current = current.where(GovernedSymbol.slug == symbol_slug)
        published = published.where(GovernedSymbol.slug == symbol_slug)

    combined = current.union(published).subquery()
    query = select(
        combined.c.symbol_id,
        combined.c.symbol_revision_id,
        combined.c.slug,
        combined.c.category,
        combined.c.discipline,
    ).order_by(combined.c.created_at, combined.c.slug)
    if limit is not None:
        if limit < 1:
            raise ValueError("backfill limit must be at least 1")
        query = query.limit(limit)

    return [
        BackfillTarget(
            symbol_id=row.symbol_id,
            symbol_revision_id=row.symbol_revision_id,
            slug=row.slug,
            category=row.category,
            discipline=row.discipline,
        )
        for row in session.execute(query)
    ]


def existing_primary_assignment(
    session: Session, *, symbol_revision_id: uuid.UUID, classification_scheme_id: uuid.UUID
) -> SymbolRevisionClassificationAssignment | None:
    """Find a primary assignment already asserting this scheme for a revision.

    Any status but `rejected`: a `proposed` promotion-time mapping is better
    evidence than a column, a `verified` one is authoritative, and a
    `retired` one records that the question has already been governed. Only
    an outright rejection leaves the field open for a backfilled proposal.
    """
    return session.execute(
        select(SymbolRevisionClassificationAssignment)
        .where(SymbolRevisionClassificationAssignment.symbol_revision_id == symbol_revision_id)
        .where(
            SymbolRevisionClassificationAssignment.classification_scheme_id
            == classification_scheme_id
        )
        .where(SymbolRevisionClassificationAssignment.assignment_role == "primary")
        .where(SymbolRevisionClassificationAssignment.status != "rejected")
    ).scalars().first()


def _existing_backfill(
    session: Session, *, symbol_revision_id: uuid.UUID, node_id: uuid.UUID
) -> SymbolRevisionClassificationAssignment | None:
    """Find a row a previous run of this module already wrote.

    Matching on (revision, node, role, method) rather than the whole row, so
    a rerun is idempotent without disturbing anything a reviewer added.
    """
    return session.execute(
        select(SymbolRevisionClassificationAssignment)
        .where(SymbolRevisionClassificationAssignment.symbol_revision_id == symbol_revision_id)
        .where(SymbolRevisionClassificationAssignment.classification_node_id == node_id)
        .where(SymbolRevisionClassificationAssignment.assignment_role == "primary")
        .where(SymbolRevisionClassificationAssignment.method == BACKFILL_METHOD)
    ).scalars().first()


def backfill_one_target(
    session: Session,
    target: BackfillTarget,
    *,
    backfilled_at: datetime,
    apply: bool,
    report: BackfillReport,
) -> None:
    """Plan, and optionally write, both facets of one revision.

    Total by construction, like `apply_classification_mapping`: each facet is
    attempted independently and a failure becomes a `mapping_failed` skip. A
    sweep of the whole catalogue must not stop on one bad row.
    """
    for facet in LEGACY_FACETS:
        try:
            _backfill_one_facet(
                session,
                target,
                facet=facet,
                backfilled_at=backfilled_at,
                apply=apply,
                report=report,
            )
        except Exception as exc:  # pragma: no cover - defensive; a sweep must not stop
            report.skipped.append(
                SkippedBackfill(
                    symbol_revision_id=target.symbol_revision_id,
                    slug=target.slug,
                    field=str(facet["field"]),
                    raw_value=target.legacy_value(str(facet["column"])),
                    reason="mapping_failed",
                    detail=str(exc)[:512],
                )
            )


def _backfill_one_facet(
    session: Session,
    target: BackfillTarget,
    *,
    facet: dict[str, Any],
    backfilled_at: datetime,
    apply: bool,
    report: BackfillReport,
) -> None:
    field_name = str(facet["field"])
    scheme_code = str(facet["scheme_code"])
    raw_value = target.legacy_value(str(facet["column"]))

    planned = plan_facet(
        field=field_name,
        value=raw_value,
        scheme_code=scheme_code,
        assignment_role="primary",
        placeholders=facet["placeholders"],
    )
    if isinstance(planned, MappingGap):
        report.skipped.append(
            SkippedBackfill(
                symbol_revision_id=target.symbol_revision_id,
                slug=target.slug,
                field=field_name,
                raw_value=planned.raw_value,
                reason=planned.reason,
                detail=planned.detail,
            )
        )
        return

    resolved = resolve_node(session, scheme_code=scheme_code, candidates=planned.candidates)
    if resolved is None:
        report.skipped.append(
            SkippedBackfill(
                symbol_revision_id=target.symbol_revision_id,
                slug=target.slug,
                field=field_name,
                raw_value=planned.raw_value,
                reason="no_node_match",
                detail=f"no active node in {scheme_code} for {planned.candidate_node_codes[0]}",
            )
        )
        return
    node, match_basis = resolved

    existing = _existing_backfill(
        session, symbol_revision_id=target.symbol_revision_id, node_id=node.id
    )
    if existing is None:
        occupant = existing_primary_assignment(
            session,
            symbol_revision_id=target.symbol_revision_id,
            classification_scheme_id=node.scheme_id,
        )
        if occupant is not None:
            report.skipped.append(
                SkippedBackfill(
                    symbol_revision_id=target.symbol_revision_id,
                    slug=target.slug,
                    field=field_name,
                    raw_value=planned.raw_value,
                    reason="already_assigned",
                    detail=f"a {occupant.status} {occupant.method} primary already asserts {scheme_code}",
                )
            )
            return

    report.planned.append(
        PlannedBackfill(
            symbol_revision_id=target.symbol_revision_id,
            slug=target.slug,
            field=field_name,
            raw_value=planned.raw_value,
            scheme_code=scheme_code,
            node_code=node.node_code,
            node_label=node.preferred_label,
            match_basis=match_basis,
            classification_node_id=node.id,
            reused=existing is not None,
        )
    )
    if existing is not None:
        # A previous run already wrote this exact row. Reported as planned and
        # reused; not counted as written, so a rerun's count is honest.
        return
    if not apply:
        return

    assignment = propose_symbol_revision_classification(
        session,
        symbol_revision_id=target.symbol_revision_id,
        classification_node_id=node.id,
        assignment_role="primary",
        method=BACKFILL_METHOD,
        proposed_at=backfilled_at,
        proposed_by_user_id=None,
        evidence={
            "source": "legacy_governed_symbol_columns",
            "field": field_name,
            "raw_value": planned.raw_value,
            "scheme_code": scheme_code,
            "node_code": node.node_code,
            "match_basis": match_basis,
            "governed_symbol_id": str(target.symbol_id),
            "governed_symbol_slug": target.slug,
            "backfiller_version": BACKFILLER_VERSION,
        },
    )
    report.written_assignment_ids.append(assignment.id)


def run_legacy_classification_backfill(
    session: Session,
    *,
    backfilled_at: datetime,
    limit: int | None = None,
    symbol_slug: str | None = None,
    apply: bool = False,
) -> BackfillReport:
    """Sweep the displayed symbol revisions. Dry run unless `apply`.

    `confidence` is deliberately left null on every row. Section 8.4 reads it
    as a proposal's own confidence, and a column copied out of a database has
    none to report -- the honest statement is that a human wrote a word into
    a text field years ago, which `evidence_json` records and a number would
    only dress up.
    """
    targets = select_backfill_targets(session, limit=limit, symbol_slug=symbol_slug)
    report = BackfillReport(applied=apply, targets_examined=len(targets))
    for target in targets:
        backfill_one_target(
            session,
            target,
            backfilled_at=backfilled_at,
            apply=apply,
            report=report,
        )
    if apply:
        session.flush()
    return report


def backfilled_assignments(
    session: Session, *, symbol_revision_id: uuid.UUID | None = None
) -> list[SymbolRevisionClassificationAssignment]:
    """List what this module has written, for reporting and for tests."""
    query = select(SymbolRevisionClassificationAssignment).where(
        SymbolRevisionClassificationAssignment.method == BACKFILL_METHOD
    )
    if symbol_revision_id is not None:
        query = query.where(
            SymbolRevisionClassificationAssignment.symbol_revision_id == symbol_revision_id
        )
    return list(session.execute(query.order_by(SymbolRevisionClassificationAssignment.created_at)).scalars())
