"""Backward-compatible legacy field sync (SM-P0-09).

Specification section 12.2: `GovernedSymbol.category` and `.discipline`
"continue writing a primary/display value derived from governed
classification". Section 12.1 phase M4 is the write side of that; this module
is the derivation both promotion paths call once their structured assignments
exist.

**It derives from a verified primary if there is one, and otherwise from a
proposed one.** That is a decision rather than a reading of the text.
`classification_assignments.verified_primary_symbol_classification` was
written in SM-P0-04 with a docstring naming itself as this module's source,
but nothing in production can create a verified assignment -- nothing calls
`transition_symbol_revision_classification` -- and a `legacy_backfill` row can
never become one by check constraint. Deriving from verified rows alone would
make the whole package permanently inert. Deriving from the best available
proposal makes it live, and costs nothing in governance terms because the
legacy columns are a *display* facet, not an assertion: section 12.2 calls
them a display value and section 11.2's example marks them
"legacy compatibility".

**It never empties a column.** Both are `nullable=False`. When no assignment
resolves -- a placeholder value, a value matching no node, a revision the
mapper could not read -- the column keeps exactly what it held. There is no
state in which this package blanks a catalogue field, and
`test_legacy_classification_sync.py` pins that for every refusal reason.

**What visibly changes is a normalisation onto the seeded labels.** The
seeded nodes *are* `catalog_taxonomy`'s hard-coded lists, so a column already
holding a seeded label derives back byte-identical and nothing moves. A column
holding `door`, `piping` or `hvac` derives back `Doors`, `Piping / P&ID` or
`Heating / HVAC`. That is SM-P0-07's approved decision 8 -- tidying existing
values onto the seeded labels is wanted -- and it repairs a live defect: the
catalogue facet list serves the seeded labels, and the filter
`gs.category ILIKE '%Doors%'` has never matched a symbol stored as `door`.

**Choosing between proposals is deterministic.** Status first, then method,
then age. A reviewer's `manual` assertion beats a mapper's `source_mapping`
one, which beats a `legacy_backfill` row copied out of the very column being
derived. Without an order the displayed value would depend on row ordering,
which is not a property a catalogue field may have.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .classification_schemes import get_classification_scheme
from .models import (
    ClassificationNode,
    GovernedSymbol,
    SymbolRevisionClassificationAssignment,
)

_SYMBOL_CATEGORY_SCHEME = "SYMBOL-CATEGORY-FAMILY"
_DISCIPLINE_SCHEME = "ENGINEERING-DISCIPLINE"

# The two legacy columns and the scheme each derives from. Same pairing the
# backfill uses, and the same pairing SM-P0-07 proposes against.
LEGACY_COLUMN_SCHEMES: tuple[tuple[str, str], ...] = (
    ("category", _SYMBOL_CATEGORY_SCHEME),
    ("discipline", _DISCIPLINE_SCHEME),
)

# A governed assignment may drive a display value only from these states. A
# `rejected` or `retired` primary is a closed question, not a display value.
DERIVABLE_STATUSES: tuple[str, ...] = ("verified", "proposed")

# Method preference among equally-statused proposals, best first. A method
# absent from this tuple sorts last rather than raising: the vocabulary can
# grow without this module refusing to derive.
METHOD_PREFERENCE: tuple[str, ...] = (
    "manual",
    "source_mapping",
    "rule",
    "ai_assisted",
    "legacy_backfill",
)

# Why a facet derived nothing. Diagnostic, like
# `classification_mapping.MAPPING_GAP_REASONS`, and disjoint from every
# `method` vocabulary. It is *not* disjoint from the mapping gap reasons, on
# purpose: `no_scheme` means the same thing in both places -- the scheme is
# not seeded -- and giving that one fact two names would be worse than
# sharing it. The other two are this module's own.
SYNC_SKIP_REASONS = frozenset({"no_derivable_assignment", "no_scheme", "value_unchanged"})
SHARED_WITH_MAPPING_GAP_REASONS = frozenset({"no_scheme"})


def _status_rank(status: str) -> int:
    return DERIVABLE_STATUSES.index(status) if status in DERIVABLE_STATUSES else len(
        DERIVABLE_STATUSES
    )


def _method_rank(method: str) -> int:
    return (
        METHOD_PREFERENCE.index(method)
        if method in METHOD_PREFERENCE
        else len(METHOD_PREFERENCE)
    )


@dataclass(frozen=True)
class LegacyDerivation:
    """What one legacy column should hold, and what the answer rests on."""

    column: str
    scheme_code: str
    current_value: str | None
    derived_value: str | None
    reason: str | None = None
    assignment_id: uuid.UUID | None = None
    assignment_status: str | None = None
    assignment_method: str | None = None

    @property
    def changes(self) -> bool:
        return self.derived_value is not None and self.derived_value != self.current_value

    def as_dict(self) -> dict[str, Any]:
        return {
            "column": self.column,
            "scheme_code": self.scheme_code,
            "current_value": self.current_value,
            "derived_value": self.derived_value,
            "changes": self.changes,
            "reason": self.reason,
            "assignment_id": str(self.assignment_id) if self.assignment_id else None,
            "assignment_status": self.assignment_status,
            "assignment_method": self.assignment_method,
        }


@dataclass(frozen=True)
class LegacySyncReport:
    """What the dual write did to one symbol's legacy columns."""

    symbol_revision_id: uuid.UUID
    derivations: tuple[LegacyDerivation, ...]
    applied: bool

    @property
    def changed_columns(self) -> tuple[str, ...]:
        return tuple(item.column for item in self.derivations if item.changes)

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol_revision_id": str(self.symbol_revision_id),
            "applied": self.applied,
            "changed_columns": list(self.changed_columns),
            "derivations": [item.as_dict() for item in self.derivations],
        }


def derivable_primary_assignment(
    session: Session, *, symbol_revision_id: uuid.UUID, classification_scheme_id: uuid.UUID
) -> SymbolRevisionClassificationAssignment | None:
    """Return the primary assignment a display value derives from.

    Ordered in Python rather than SQL: the ranking is a policy this module
    owns, and expressing it as a `CASE` in the query would put the same
    judgement in two places. The candidate set is at most a handful of rows
    for one revision in one scheme.
    """
    candidates = list(
        session.execute(
            select(SymbolRevisionClassificationAssignment)
            .where(
                SymbolRevisionClassificationAssignment.symbol_revision_id == symbol_revision_id
            )
            .where(
                SymbolRevisionClassificationAssignment.classification_scheme_id
                == classification_scheme_id
            )
            .where(SymbolRevisionClassificationAssignment.assignment_role == "primary")
            .where(SymbolRevisionClassificationAssignment.status.in_(DERIVABLE_STATUSES))
        ).scalars()
    )
    if not candidates:
        return None
    candidates.sort(
        key=lambda row: (
            _status_rank(row.status),
            _method_rank(row.method),
            row.created_at,
            str(row.id),
        )
    )
    return candidates[0]


def derive_legacy_column(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    column: str,
    scheme_code: str,
    current_value: str | None,
) -> LegacyDerivation:
    """Derive one legacy column from one scheme's primary assignment."""
    scheme = get_classification_scheme(session, scheme_code)
    if scheme is None:
        return LegacyDerivation(
            column=column,
            scheme_code=scheme_code,
            current_value=current_value,
            derived_value=None,
            reason="no_scheme",
        )
    assignment = derivable_primary_assignment(
        session,
        symbol_revision_id=symbol_revision_id,
        classification_scheme_id=scheme.id,
    )
    if assignment is None:
        return LegacyDerivation(
            column=column,
            scheme_code=scheme_code,
            current_value=current_value,
            derived_value=None,
            reason="no_derivable_assignment",
        )
    node = session.get(ClassificationNode, assignment.classification_node_id)
    if node is None:  # pragma: no cover - a composite FK makes this unreachable
        return LegacyDerivation(
            column=column,
            scheme_code=scheme_code,
            current_value=current_value,
            derived_value=None,
            reason="no_derivable_assignment",
        )
    return LegacyDerivation(
        column=column,
        scheme_code=scheme_code,
        current_value=current_value,
        derived_value=node.preferred_label,
        reason=None if node.preferred_label != current_value else "value_unchanged",
        assignment_id=assignment.id,
        assignment_status=assignment.status,
        assignment_method=assignment.method,
    )


def plan_legacy_sync(
    session: Session, *, symbol: GovernedSymbol, symbol_revision_id: uuid.UUID
) -> tuple[LegacyDerivation, ...]:
    """Derive both legacy columns without writing either."""
    return tuple(
        derive_legacy_column(
            session,
            symbol_revision_id=symbol_revision_id,
            column=column,
            scheme_code=scheme_code,
            current_value=getattr(symbol, column),
        )
        for column, scheme_code in LEGACY_COLUMN_SCHEMES
    )


def sync_legacy_symbol_columns(
    session: Session,
    *,
    symbol: GovernedSymbol,
    symbol_revision_id: uuid.UUID,
    synced_at: datetime,
    apply: bool = True,
) -> LegacySyncReport:
    """Write each legacy column the structured primary disagrees with.

    Total by construction, and called after the irreversible steps of a
    promotion rather than before one: a defect here must not be able to stop
    a reviewed symbol from being promoted, and a column that keeps its old
    value is a compatibility outcome rather than a failure. The caller wraps
    this the way it wraps `record_classification_mapping`.
    """
    derivations = plan_legacy_sync(
        session, symbol=symbol, symbol_revision_id=symbol_revision_id
    )
    changed = False
    for derivation in derivations:
        if not derivation.changes:
            continue
        if apply:
            setattr(symbol, derivation.column, derivation.derived_value)
            changed = True
    if changed:
        symbol.updated_at = synced_at
        session.flush()
    return LegacySyncReport(
        symbol_revision_id=symbol_revision_id,
        derivations=derivations,
        applied=apply and changed,
    )
