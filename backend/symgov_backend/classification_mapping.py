"""Workspace classification fields as governed proposals (SM-P0-07).

Specification section 9.3 lists eleven workspace review fields and the
structured home each one should reach. All eleven already arrive in
`classification_records`; the loss happens at promotion, where nine of them
survive only as free text inside `SymbolRevision.payload_json` and four --
`industry`, `standards_source`, `library_provenance_class` and `format` --
survive not at all. This module is the mapping layer that stops that.

Three properties shape it.

**The mapping rules are a pure function.** `plan_classification_mapping`
takes plain values and returns candidate node *codes*, not nodes; resolving a
code against a seeded scheme is the only part that needs a database. Every
rule is therefore provable without one, which is the point of keeping the
planner separate from `apply_classification_mapping`.

**Nothing here can fail a promotion.** A symbol whose classification values
are unmappable still gets promoted; the value lands in `evidence_json` and in
the payload's mapping report instead of in an exception. Section 16.1 asks
that no field be silently dropped, not that every field map.

**Nothing here writes a verified row.** Section 8.4 keeps a machine
assertion proposed however confident it is, and
`propose_symbol_revision_classification` starts every row at `proposed`
regardless. `method` is `source_mapping`, not `legacy_backfill`: the
`backfill_not_verified` check constraint makes a backfilled row permanently
unverifiable, which is right for SM-P0-10's historical sweep and wrong for a
promotion-time mapping a reviewer should be able to confirm later.

Four section 9.3 rows have no structured home in P0, and are carried as gaps
rather than forced into one. `industry` and `processCategory` name schemes
that do not exist and have no real vocabulary to seed from -- see
`classification_schemes.SEED_CLASSIFICATION_SCHEMES` and
`docs/plans/2026-09-11-classification-industry-field-defect.md`.
`parentEquipmentClass` wants section 7.3's `SemanticConceptRelationship`,
which has no table under any section 15.1 package; CFIHOS's 832 equipment
classes are the likely vocabulary when SM-P2-02 imports them.
`aliases`/`keywords`/`sourceRefs` stay in `payload_json` by section 12.2's
legacy-field policy, because the multilingual `ConceptTerm` table is
deferred and there is no concept to attach terms to anyway.

That last point generalises: **no semantic concept exists for a promoted
symbol.** Nothing in production creates one, and Appendix C.1 makes "add
concept lookup/proposal to one review path" step 5, separate from step 4's
"wire workspace classification output to proposed structured assignments",
which is this package. So every section 9.3 row whose target is a *concept*
classification is a gap here by construction, not by omission, and
`propose_concept_classification` is deliberately not called.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .automation_policy import (
    PLACEHOLDER_CATEGORIES,
    PLACEHOLDER_DISCIPLINES,
    PLACEHOLDER_VALUES,
)
from .classification_assignments import propose_symbol_revision_classification
from .classification_schemes import (
    NODE_LABEL_MAX_LENGTH,
    derive_classification_node_code,
    get_classification_scheme,
)
from .models import (
    ClassificationNode,
    ClassificationRecord,
    SourcePackageEntry,
    SymbolRevisionClassificationAssignment,
    SymbolStandardLink,
)
from .source_package_acquisition import add_source_package_entry
from .standard_sources import (
    assert_symbol_standard_link,
    get_standard,
    list_standard_versions,
    normalize_standard_code,
)

MAPPER_VERSION = "symgov-classification-mapping-v1"

# Section 12.1 phase M2's method vocabulary already has the value this needs.
# `legacy_backfill` is SM-P0-10's, and carries a check constraint that makes
# the row unverifiable for ever; a promotion-time mapping is `source_mapping`.
MAPPING_METHOD = "source_mapping"

# Why a section 9.3 field produced no assignment. This is a *diagnostic*
# vocabulary, not a seventh `method` vocabulary -- it never reaches a
# `method` column, and `tests/test_classification_mapping.py` pins that.
MAPPING_GAP_REASONS = frozenset(
    {
        "no_value",
        "placeholder_value",
        "no_scheme",
        "no_node_match",
        "no_concept_target",
        "no_relationship_table",
        "no_standard_match",
        "ambiguous_standard_version",
        "no_source_package",
        "carried_in_payload",
        "mapping_failed",
    }
)

# Section 8.3's weakest honest reading of "this workspace field named a
# source". The submitter said the asset came from there; nothing has verified
# it, so the link is `proposed` with no `verification_method`, and section
# 9.2's "no ambiguous standard-associated label" is respected by naming a
# real relationship rather than inventing a vague one.
STANDARD_RELATIONSHIP_TYPE = "derived_from"

_SYMBOL_CATEGORY_SCHEME = "SYMBOL-CATEGORY-FAMILY"
_DISCIPLINE_SCHEME = "ENGINEERING-DISCIPLINE"


@dataclass(frozen=True)
class ClassificationFields:
    """Section 9.3's field list, lifted out of whatever is holding it.

    `category` and `format` are not among section 9.3's eleven rows.
    `category` is here because Appendix A's long-term treatment of
    `GovernedSymbol.category` is "derived/primary compatibility facet from
    structured classification", which SM-P0-09 cannot do unless a structured
    primary exists to derive it from. `format` is here because it is one of
    the four fields that reach the database and then vanish at promotion.
    """

    discipline: str | None = None
    category: str | None = None
    industry: str | None = None
    symbol_family: str | None = None
    process_category: str | None = None
    parent_equipment_class: str | None = None
    standards_source: str | None = None
    library_provenance_class: str | None = None
    source_classification: str | None = None
    format: str | None = None
    aliases: tuple[str, ...] = ()
    search_terms: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    confidence: Decimal | None = None
    classification_record_id: uuid.UUID | None = None


@dataclass(frozen=True)
class PlannedAssignment:
    """One proposed representation classification, before a node is resolved.

    `candidate_node_codes` is ordered by preference. The first that resolves
    to an active node in `scheme_code` wins, and `match_basis` records which
    rule found it so a reviewer can see how the value was matched.
    """

    field: str
    scheme_code: str
    assignment_role: str
    raw_value: str
    candidate_node_codes: tuple[str, ...]


@dataclass(frozen=True)
class MappingGap:
    """One section 9.3 field that produced no structured assignment."""

    field: str
    raw_value: str | None
    reason: str
    detail: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "raw_value": self.raw_value,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ClassificationMappingPlan:
    """What the pure rules say should be proposed, and what cannot be."""

    assignments: tuple[PlannedAssignment, ...] = ()
    gaps: tuple[MappingGap, ...] = ()
    standards_source: str | None = None
    library_provenance_class: str | None = None

    @property
    def planned_fields(self) -> frozenset[str]:
        return frozenset(assignment.field for assignment in self.assignments)

    @property
    def gap_fields(self) -> frozenset[str]:
        return frozenset(gap.field for gap in self.gaps)


def _clean_text(value: object) -> str | None:
    """Return a trimmed, length-bounded string, or None for an empty one."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text[:NODE_LABEL_MAX_LENGTH]


def _is_placeholder(value: str, placeholders: set[str]) -> bool:
    """Reuse the automation gate's placeholder judgement rather than a new one."""
    return value.strip().casefold() in placeholders


def _candidate_node_codes(value: str) -> tuple[str, ...]:
    """Derive the node codes a value could match, best first.

    `derive_classification_node_code` already folds case and punctuation, so
    `mechanical`, `Mechanical` and `MECHANICAL` are one code. The seeded
    labels are plural where the workspace values are singular -- `Valves`
    against Libby's `valve`, `Doors` against `door` -- so a trailing-S
    variant is tried second. That is a deterministic rewrite of one
    character, recorded in evidence when it is what matched; it is not
    similarity matching, which section 16.2 forbids.
    """
    try:
        exact = derive_classification_node_code(value)
    except ValueError:
        return ()
    if exact.endswith("S"):
        return (exact, exact[:-1])
    return (exact, f"{exact}S")


def _match_basis(code: str, candidates: tuple[str, ...]) -> str:
    return "exact" if candidates and code == candidates[0] else "plural_variant"


def plan_classification_mapping(fields: ClassificationFields) -> ClassificationMappingPlan:
    """Turn one record's section 9.3 fields into proposals and gaps.

    Pure: no session, no clock, no identifiers. Every rule in this function
    is provable in `tests/test_classification_mapping.py` with no database.
    """
    assignments: list[PlannedAssignment] = []
    gaps: list[MappingGap] = []

    def facet(
        *,
        field: str,
        value: str | None,
        scheme_code: str,
        assignment_role: str,
        placeholders: set[str],
    ) -> None:
        cleaned = _clean_text(value)
        if cleaned is None:
            gaps.append(MappingGap(field=field, raw_value=None, reason="no_value"))
            return
        if _is_placeholder(cleaned, placeholders):
            gaps.append(MappingGap(field=field, raw_value=cleaned, reason="placeholder_value"))
            return
        candidates = _candidate_node_codes(cleaned)
        if not candidates:
            gaps.append(MappingGap(field=field, raw_value=cleaned, reason="no_node_match"))
            return
        assignments.append(
            PlannedAssignment(
                field=field,
                scheme_code=scheme_code,
                assignment_role=assignment_role,
                raw_value=cleaned,
                candidate_node_codes=candidates,
            )
        )

    # Section 9.3 row 1: engineeringDiscipline -> Engineering Discipline.
    facet(
        field="engineeringDiscipline",
        value=fields.discipline,
        scheme_code=_DISCIPLINE_SCHEME,
        assignment_role="primary",
        placeholders=PLACEHOLDER_DISCIPLINES,
    )

    # Appendix A's compatibility facet, so SM-P0-09 has a primary to derive
    # `GovernedSymbol.category` from. Not one of section 9.3's eleven.
    facet(
        field="category",
        value=fields.category,
        scheme_code=_SYMBOL_CATEGORY_SCHEME,
        assignment_role="primary",
        placeholders=PLACEHOLDER_CATEGORIES,
    )

    # Section 9.3 row 3: symbolFamily, "concept or representation depending
    # on meaning". No concept exists at promotion, so it is a representation
    # classification -- and a secondary one, because `category` already holds
    # the primary in the same scheme.
    facet(
        field="symbolFamily",
        value=fields.symbol_family,
        scheme_code=_SYMBOL_CATEGORY_SCHEME,
        assignment_role="secondary",
        placeholders=PLACEHOLDER_CATEGORIES,
    )

    # Section 9.3 row 2: industry -> an Industry/Application scheme that does
    # not exist and has no real vocabulary to seed from.
    gaps.append(
        MappingGap(
            field="industry",
            raw_value=_clean_text(fields.industry),
            reason="no_scheme",
            detail="Industry/Application scheme is not seeded; ICS is the intended source",
        )
    )

    # Section 9.3 row 4: processCategory, "mapping rule to be defined".
    # Concept classification or qualifier concept -- neither has a target.
    gaps.append(
        MappingGap(
            field="processCategory",
            raw_value=_clean_text(fields.process_category),
            reason="no_scheme",
            detail="no process-category scheme is seeded and no concept exists to qualify",
        )
    )

    # Section 9.3 row 5: parentEquipmentClass -> a broader concept
    # relationship. Section 7.3's table is not in any section 15.1 package.
    gaps.append(
        MappingGap(
            field="parentEquipmentClass",
            raw_value=_clean_text(fields.parent_equipment_class),
            reason="no_relationship_table",
            detail="SemanticConceptRelationship (7.3) has no table; CFIHOS equipment classes are the likely vocabulary",
        )
    )

    # Section 9.3 row 8: sourceClassification -- the provider's own taxonomy
    # value, preserved as evidence. There is no scheme to map it into until a
    # source's taxonomy is ingested as an ExternalSemanticScheme.
    gaps.append(
        MappingGap(
            field="sourceClassification",
            raw_value=_clean_text(fields.source_classification),
            reason="no_scheme",
            detail="raw source taxonomy preserved as evidence pending an external scheme",
        )
    )

    # Section 9.3 rows 9 and 10, and the twelfth field that vanishes at
    # promotion. Section 12.2 keeps all three in payload_json.
    for field_name, raw in (
        ("aliases", ", ".join(fields.aliases) or None),
        ("keywords", ", ".join(fields.search_terms) or None),
        ("sourceRefs", ", ".join(fields.source_refs) or None),
        ("format", _clean_text(fields.format)),
    ):
        gaps.append(
            MappingGap(
                field=field_name,
                raw_value=raw,
                reason="carried_in_payload",
                detail="section 12.2 legacy-field policy; no ConceptTerm table in P0",
            )
        )

    return ClassificationMappingPlan(
        assignments=tuple(assignments),
        gaps=tuple(gaps),
        standards_source=_clean_text(fields.standards_source),
        library_provenance_class=_clean_text(fields.library_provenance_class),
    )


def classification_fields_from_record(
    record: ClassificationRecord | None,
    *,
    discipline: str | None = None,
    category: str | None = None,
) -> ClassificationFields:
    """Read section 9.3's fields off a classification record.

    `discipline` and `category` override the record's own values when the
    caller has a human-reviewed value, which is the same precedence
    `publication_handoff` already applies to the legacy columns. Keeping the
    two derived from one precedence is what lets SM-P0-09 later derive the
    legacy column *from* the assignment without changing either value.
    """
    if record is None:
        return ClassificationFields(discipline=_clean_text(discipline), category=_clean_text(category))
    return ClassificationFields(
        discipline=_clean_text(discipline) or _clean_text(record.discipline),
        category=_clean_text(category) or _clean_text(record.category),
        industry=_clean_text(record.industry),
        symbol_family=_clean_text(record.symbol_family),
        process_category=_clean_text(record.process_category),
        parent_equipment_class=_clean_text(record.parent_equipment_class),
        standards_source=_clean_text(record.standards_source),
        library_provenance_class=_clean_text(record.library_provenance_class),
        source_classification=_clean_text(record.source_classification),
        format=_clean_text(record.format),
        aliases=tuple(str(item) for item in (record.aliases_json or []) if str(item).strip()),
        search_terms=tuple(str(item) for item in (record.search_terms_json or []) if str(item).strip()),
        source_refs=tuple(str(item) for item in (record.source_refs_json or []) if str(item).strip()),
        confidence=Decimal(str(record.confidence)) if record.confidence is not None else None,
        classification_record_id=record.id,
    )


@dataclass(frozen=True)
class ClassificationMappingOutcome:
    """What actually reached the database, and what did not.

    `as_report()` is what `publication_handoff` stores in
    `payload_json["classification_mapping"]`, which is where section 16.1's
    "no field silently dropped" becomes visible to a reviewer.
    """

    assignments: tuple[SymbolRevisionClassificationAssignment, ...] = ()
    gaps: tuple[MappingGap, ...] = ()
    records: tuple[dict[str, Any], ...] = ()
    # Section 9.3 rows whose target is not a classification assignment:
    # `standardsSource` reaches `symbol_standard_links` and
    # `libraryProvenanceClass` reaches `source_package_entries`. They belong
    # in the report on success as much as on failure, or "no field silently
    # dropped" would hold only for the fields that failed.
    resolved: tuple[dict[str, Any], ...] = ()
    standard_link: SymbolStandardLink | None = None
    source_package_entry: SourcePackageEntry | None = None
    status: str = "mapped"

    def as_report(self) -> dict[str, Any]:
        return {
            "mapper_version": MAPPER_VERSION,
            "status": self.status,
            "method": MAPPING_METHOD,
            "assignments": list(self.records),
            "resolved": list(self.resolved),
            "gaps": [gap.as_dict() for gap in self.gaps],
            "standard_link_id": str(self.standard_link.id) if self.standard_link else None,
            "source_package_entry_id": (
                str(self.source_package_entry.id) if self.source_package_entry else None
            ),
        }


def _resolve_node(
    session: Session, *, scheme_code: str, candidate_node_codes: tuple[str, ...]
) -> ClassificationNode | None:
    """Return the first active node matching a candidate code, or None."""
    scheme = get_classification_scheme(session, scheme_code)
    if scheme is None:
        return None
    for code in candidate_node_codes:
        node = session.execute(
            select(ClassificationNode)
            .where(ClassificationNode.scheme_id == scheme.id)
            .where(ClassificationNode.node_code == code)
            .where(ClassificationNode.status == "active")
        ).scalar_one_or_none()
        if node is not None:
            return node
    return None


def _existing_assignment(
    session: Session, *, symbol_revision_id: uuid.UUID, node_id: uuid.UUID, assignment_role: str
) -> SymbolRevisionClassificationAssignment | None:
    """Find an assignment this mapper already made.

    Both promotion functions are idempotent by `revision_label`, so promoting
    the same decision twice must not double the proposals. Matching on
    (revision, node, role, method) rather than on the whole row leaves a
    reviewer's own manual assignment of the same node untouched.
    """
    return session.execute(
        select(SymbolRevisionClassificationAssignment)
        .where(SymbolRevisionClassificationAssignment.symbol_revision_id == symbol_revision_id)
        .where(SymbolRevisionClassificationAssignment.classification_node_id == node_id)
        .where(SymbolRevisionClassificationAssignment.assignment_role == assignment_role)
        .where(SymbolRevisionClassificationAssignment.method == MAPPING_METHOD)
    ).scalars().first()


def _standard_version_for(session: Session, raw_value: str) -> tuple[Any, MappingGap | None]:
    """Resolve a workspace `standards_source` value to exactly one edition.

    Exact code match only. Section 16.2 forbids a verified exact mapping made
    from string similarity, and while a link asserted here is only
    *proposed*, a fuzzy match would put a guess in a governed table. A
    standard carrying more than one active edition is ambiguous rather than
    wrong, so it is reported as such instead of picking the newest.
    """
    try:
        standard_code = normalize_standard_code(raw_value)
    except ValueError:
        return None, MappingGap(
            field="standardsSource",
            raw_value=raw_value,
            reason="no_standard_match",
            detail="value is not a standard code",
        )
    standard = get_standard(session, standard_code)
    if standard is None:
        return None, MappingGap(
            field="standardsSource",
            raw_value=raw_value,
            reason="no_standard_match",
            detail=f"no registered standard with code {standard_code}",
        )
    versions = list_standard_versions(session, standard.id, status="active")
    if len(versions) != 1:
        return None, MappingGap(
            field="standardsSource",
            raw_value=raw_value,
            reason="ambiguous_standard_version",
            detail=f"{standard_code} has {len(versions)} active editions",
        )
    return versions[0], None


def apply_classification_mapping(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    fields: ClassificationFields,
    proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None = None,
    source_package_id: uuid.UUID | None = None,
    context: dict[str, Any] | None = None,
) -> ClassificationMappingOutcome:
    """Propose everything section 9.3 maps, and report everything it does not.

    Total by construction: each step is attempted independently and a failure
    becomes a `mapping_failed` gap. A classification proposal must never stop
    a reviewed symbol from being promoted.
    """
    plan = plan_classification_mapping(fields)
    shared_evidence = dict(context or {})
    if fields.classification_record_id is not None:
        shared_evidence["classification_record_id"] = str(fields.classification_record_id)

    assignments: list[SymbolRevisionClassificationAssignment] = []
    records: list[dict[str, Any]] = []
    gaps: list[MappingGap] = list(plan.gaps)

    for planned in plan.assignments:
        try:
            node = _resolve_node(
                session,
                scheme_code=planned.scheme_code,
                candidate_node_codes=planned.candidate_node_codes,
            )
            if node is None:
                gaps.append(
                    MappingGap(
                        field=planned.field,
                        raw_value=planned.raw_value,
                        reason="no_node_match",
                        detail=f"no active node in {planned.scheme_code} for {planned.candidate_node_codes[0]}",
                    )
                )
                continue
            match_basis = _match_basis(node.node_code, planned.candidate_node_codes)
            existing = _existing_assignment(
                session,
                symbol_revision_id=symbol_revision_id,
                node_id=node.id,
                assignment_role=planned.assignment_role,
            )
            assignment = existing or propose_symbol_revision_classification(
                session,
                symbol_revision_id=symbol_revision_id,
                classification_node_id=node.id,
                assignment_role=planned.assignment_role,
                method=MAPPING_METHOD,
                proposed_at=proposed_at,
                proposed_by_user_id=proposed_by_user_id,
                confidence=fields.confidence,
                evidence={
                    **shared_evidence,
                    "source": "workspace_classification_fields",
                    "field": planned.field,
                    "raw_value": planned.raw_value,
                    "scheme_code": planned.scheme_code,
                    "node_code": node.node_code,
                    "match_basis": match_basis,
                    "confidence_basis": "classification_record",
                    "mapper_version": MAPPER_VERSION,
                },
            )
            assignments.append(assignment)
            records.append(
                {
                    "field": planned.field,
                    "scheme_code": planned.scheme_code,
                    "node_code": node.node_code,
                    "raw_value": planned.raw_value,
                    "assignment_role": planned.assignment_role,
                    "match_basis": match_basis,
                    "reused": existing is not None,
                }
            )
        except Exception as exc:  # pragma: no cover - defensive; promotion must not fail
            gaps.append(
                MappingGap(
                    field=planned.field,
                    raw_value=planned.raw_value,
                    reason="mapping_failed",
                    detail=str(exc)[:512],
                )
            )

    resolved: list[dict[str, Any]] = []
    standard_link = None
    if plan.standards_source is None:
        gaps.append(MappingGap(field="standardsSource", raw_value=None, reason="no_value"))
    else:
        try:
            version, gap = _standard_version_for(session, plan.standards_source)
            if gap is not None:
                gaps.append(gap)
            else:
                standard_link = _ensure_standard_link(
                    session,
                    symbol_revision_id=symbol_revision_id,
                    standard_version_id=version.id,
                    asserted_at=proposed_at,
                    raw_value=plan.standards_source,
                    evidence=shared_evidence,
                )
                resolved.append(
                    {
                        "field": "standardsSource",
                        "raw_value": plan.standards_source,
                        "target": "symbol_standard_links",
                        "relationship_type": STANDARD_RELATIONSHIP_TYPE,
                    }
                )
        except Exception as exc:  # pragma: no cover - defensive
            gaps.append(
                MappingGap(
                    field="standardsSource",
                    raw_value=plan.standards_source,
                    reason="mapping_failed",
                    detail=str(exc)[:512],
                )
            )

    entry = None
    try:
        entry, entry_gap = _ensure_package_entry(
            session,
            symbol_revision_id=symbol_revision_id,
            source_package_id=source_package_id,
            library_provenance_class=plan.library_provenance_class,
            added_at=proposed_at,
        )
        if entry_gap is not None:
            gaps.append(entry_gap)
        else:
            resolved.append(
                {
                    "field": "libraryProvenanceClass",
                    "raw_value": plan.library_provenance_class,
                    "target": "source_package_entries",
                }
            )
    except Exception as exc:  # pragma: no cover - defensive
        gaps.append(
            MappingGap(
                field="libraryProvenanceClass",
                raw_value=plan.library_provenance_class,
                reason="mapping_failed",
                detail=str(exc)[:512],
            )
        )

    return ClassificationMappingOutcome(
        assignments=tuple(assignments),
        gaps=tuple(gaps),
        records=tuple(records),
        resolved=tuple(resolved),
        standard_link=standard_link,
        source_package_entry=entry,
    )


def _ensure_standard_link(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    standard_version_id: uuid.UUID,
    asserted_at: datetime,
    raw_value: str,
    evidence: dict[str, Any],
) -> SymbolStandardLink:
    """Assert the workspace's declared source once, idempotently."""
    existing = session.execute(
        select(SymbolStandardLink)
        .where(SymbolStandardLink.symbol_revision_id == symbol_revision_id)
        .where(SymbolStandardLink.standard_version_id == standard_version_id)
    ).scalars().first()
    if existing is not None:
        return existing
    return assert_symbol_standard_link(
        session,
        symbol_revision_id=symbol_revision_id,
        standard_version_id=standard_version_id,
        relationship_type=STANDARD_RELATIONSHIP_TYPE,
        asserted_at=asserted_at,
        evidence={
            **evidence,
            "source": "workspace_classification_fields",
            "field": "standardsSource",
            "raw_value": raw_value,
            "match_basis": "exact_standard_code",
            "mapper_version": MAPPER_VERSION,
        },
    )


def _ensure_package_entry(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    source_package_id: uuid.UUID | None,
    library_provenance_class: str | None,
    added_at: datetime,
) -> tuple[SourcePackageEntry | None, MappingGap | None]:
    """Place the revision inside its source package.

    Section 9.3 maps `libraryProvenanceClass` to "SourcePackage / provenance
    classification", and no production path has ever created a
    `SourcePackageEntry` -- which is also why
    `rights_provenance.symbol_revision_rights_provenance` finds nothing for a
    promoted symbol. The entry is what gives that report something to read,
    and `source_label` is where the provenance class lands.
    """
    if source_package_id is None:
        return None, MappingGap(
            field="libraryProvenanceClass",
            raw_value=library_provenance_class,
            reason="no_source_package",
            detail="the intake record names no source package",
        )
    existing = session.execute(
        select(SourcePackageEntry)
        .where(SourcePackageEntry.symbol_revision_id == symbol_revision_id)
        .where(SourcePackageEntry.source_package_id == source_package_id)
    ).scalars().first()
    if existing is not None:
        return existing, None
    entry = add_source_package_entry(
        session,
        source_package_id=source_package_id,
        symbol_revision_id=symbol_revision_id,
        added_at=added_at,
        source_label=library_provenance_class,
    )
    return entry, None
