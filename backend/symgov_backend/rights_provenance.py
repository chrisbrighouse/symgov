"""Durable rights records and asset transformation lineage (SM-P0-06).

Specification section 7.12. Section 15.1's work package is "persist durable
rights disposition and transformation lineage or extend existing durable
rights model", and section 7.12 adds "if an equivalent durable rights entity
already exists elsewhere in the codebase, extend/reuse it rather than
duplicate it".

**None exists.** `provenance_assessments` is the only rights-bearing table
with a governed vocabulary, and it is not a durable record: both of its
foreign keys (`queue_item_id`, `intake_record_id`) are NOT NULL and
intake-scoped, so no rights decision can be attached to a source package, a
standard edition or a governed symbol revision; its deployed
`rights_disposition` enumeration (`cleared | unknown_warning | restricted |
conflict | failed`) shares not one value with section 7.12's; and it is
written by six modules on the live intake path, which section 12.2 and
SM-P0-09 reserve. `hannah_photo_candidates.rights_status` is candidate-scoped
and equally unsuitable. Migration 20260910_0056 therefore creates two new
entities, and nothing here reads or writes either existing table.

Three rules shape this module.

Section 7.12 asks for "who / when / why the rights disposition was approved",
so approving a rights record requires all three. This is stricter than
section 7.10's verification, which allows a controlled-system method to
verify without a named reviewer: no deterministic reading of a licence is
itself a rights decision, and section 8.4 keeps an AI determination a
proposal however confident it is.

A permissive disposition -- one that authorises SymGov to render or hand on
the asset -- may only be approved on a rights status that can support it.
That is section 9.2's rights dimension and section 16.2's "no public
authoritative-source symbol is newly published with unresolved rights".
It lives here rather than in a check constraint, deliberately: rights
*gating* is SM-P0-08's, and a storage-level rule would put a second gate in a
second place, where loosening a policy would need a migration. The same
reasoning keeps `standard_sources.STANDARD_STATUSES` out of the database.
`disposition_is_permitted` is the rule, `transition_rights_record` applies
it, and `tests/test_rights_provenance.py` is its only enforcement.
`metadata_only` and `reject` stay available for every status, so an orphan
work can still be recorded honestly.

Section 7.12 asks for the transformation *chain*, not its endpoints. Each
`AssetTransformation` row is one step, and `record_asset_transformation`
allocates `step_index` and refuses a step that does not start from the digest
the previous step produced. That continuity is a cross-row property, so it
cannot be a check constraint; everything per-row is enforced by the database
as well.

Two things this module does not do. It stores no licence text -- section 7.12
is explicit that `licence_reference` is "a reference to terms/contract/rights
record; not the licence text itself", and it carries SM-P0-05's
512-character bound for that reason. And it applies no publication gate:
`symbol_revision_rights_provenance` reports which subjects still need a
rights decision, but refusing publication on that basis is SM-P0-08.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    AssetTransformation,
    RightsRecord,
    SourcePackage,
    SourcePackageEntry,
    StandardVersion,
    SymbolStandardLink,
)
from .source_package_acquisition import requires_licence_reference
from .standard_sources import normalize_sha256

# Section 7.12's rights status vocabulary, verbatim, in the order the
# specification tabulates it. Section 13.1's "Rights" traceability dimension
# names the same six values.
RIGHTS_STATUSES = frozenset(
    {"unknown", "open", "licensed", "restricted", "prohibited", "expired"}
)

# Section 7.12's disposition vocabulary, verbatim. It shares no value with the
# deployed `provenance_assessments.rights_disposition` enumeration, which is
# one of the three reasons that table could not become the durable record.
RIGHTS_DISPOSITIONS = frozenset(
    {"display", "distribute", "transform", "compare_only", "metadata_only", "reject"}
)

# The dispositions that authorise SymGov to render or hand on the asset
# itself. `compare_only` is one of them: comparing two graphics means drawing
# both of them.
PERMISSIVE_DISPOSITIONS = frozenset({"display", "distribute", "transform", "compare_only"})

# The two that survive any rights status, because neither reproduces the
# asset. An orphan work is recorded as `unknown` + `metadata_only`.
NON_PERMISSIVE_DISPOSITIONS = frozenset({"metadata_only", "reject"})

# The rights statuses that can support a permissive disposition. `unknown`,
# `prohibited` and `expired` cannot.
PERMITTING_RIGHTS_STATUSES = frozenset({"open", "licensed", "restricted"})

# The statuses that assert someone granted terms, and so cannot be approved
# without a reference to those terms.
LICENCE_BACKED_RIGHTS_STATUSES = frozenset({"licensed", "restricted"})

# The governance lifecycle. `approved` rather than the `verified` the other
# five governed tables in this model use: section 7.12's own word is
# "approved", section 13.1's Governance dimension reads "organisation approved
# | public approved", and a rights disposition is a legal approval rather than
# a verification of fact. This is the one deliberate divergence from the
# shared `proposed | verified | rejected | retired` shape.
RIGHTS_DECISION_STATUSES = frozenset({"proposed", "approved", "rejected", "retired"})

# Statuses that keep a record live, and therefore inside the three partial
# unique indexes' reach for supersession purposes.
LIVE_RIGHTS_DECISION_STATUSES = frozenset({"proposed", "approved"})

# A rejected rights decision is terminal: a reviewer who changes their mind
# proposes afresh rather than reopening the old record. The shape SM-P0-02
# through -05 all use.
RIGHTS_DECISION_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved", "rejected", "retired"}),
    "approved": frozenset({"retired"}),
    "rejected": frozenset(),
    "retired": frozenset(),
}

# The **sixth** `method` vocabulary in the semantic model, and deliberately
# not unified with `symbol_semantic_assignments.method`,
# `concept_external_references.mapping_method`, the classification
# assignments' `method`, `symbol_standard_links.verification_method` or
# `source_packages.acquisition_method`. Section 8.4 requires an AI assertion
# to be stored as a proposal carrying its method, and without this column an
# agent-proposed rights record would be indistinguishable from a reviewer's.
# `licence_document` -- a determination read off the terms, contract or
# licence artefact itself -- appears in no other vocabulary. Unifying any of
# the six is a specification change, not an implementation tidy-up.
RIGHTS_DETERMINATION_METHODS = frozenset({"manual", "licence_document", "ai_assisted"})

# Section 8.4, with no controlled-system exception. Section 7.10 lets
# `import_manifest` and `source_api` verify a source assertion without a
# named reviewer because both read an artefact the provider published. No
# equivalent exists here: reading a licence is not deciding what SymGov may
# do under it.
NON_APPROVING_DETERMINATION_METHODS = frozenset({"ai_assisted"})

# The three subjects a rights record may attach to. Exactly one is set on any
# row, and `ck_rights_records_subject_exactly_one` enforces it.
RIGHTS_SUBJECTS = ("source_package_id", "standard_version_id", "symbol_revision_id")

LICENCE_REFERENCE_MAX_LENGTH = 512
DECISION_REASON_MAX_LENGTH = 2000
TOOL_NAME_MAX_LENGTH = 128
TOOL_VERSION_MAX_LENGTH = 64


def _require_aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _require_optional_actor(value: object, label: str) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise ValueError(f"{label} must be a real UUID")
    return value


def _normalize_required_text(value: object, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{label} must not be empty")
    if len(normalized_value) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters")
    return normalized_value


def _normalize_optional_text(value: object, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _normalize_required_text(value, label, max_length)


def normalize_licence_reference(value: object) -> str | None:
    """Return a reference to the terms, never the terms themselves.

    Section 7.12: "a reference to terms/contract/rights record; not the
    licence text itself". The 512-character bound is the same one SM-P0-05
    placed on `source_packages.licence_reference`, and it is what keeps this a
    reference: a pasted licence does not fit.
    """
    return _normalize_optional_text(value, "licence reference", LICENCE_REFERENCE_MAX_LENGTH)


def normalize_decision_reason(value: object) -> str | None:
    """Return the recorded reason for a rights decision, or None.

    Section 7.12's "why". No other table in this model requires one, and this
    one does for an approval: it is the only decision in the semantic model
    whose justification cannot be reconstructed from the rest of the row.
    """
    return _normalize_optional_text(value, "rights decision reason", DECISION_REASON_MAX_LENGTH)


def normalize_rights_evidence(value: object) -> dict:
    """Return the evidence object, which must be a JSON object when present."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("rights evidence must be an object")
    return value


def disposition_is_permitted(rights_status: object, disposition: object) -> bool:
    """Whether this disposition may be *approved* on this rights status.

    A permissive disposition -- one that renders or hands on the asset --
    needs a rights status that can support it. Approving distribution of an
    asset whose rights are `unknown`, `prohibited` or `expired` is exactly
    what section 9.2's rights dimension and section 16.2's "no public
    authoritative-source symbol is newly published with unresolved rights"
    rule out.

    Service policy, and by decision not a check constraint: the database
    accepts such a row, and `transition_rights_record` is what refuses it.
    Rights gating belongs to SM-P0-08, and duplicating the rule in storage
    would mean a migration to change a policy. `tests/test_rights_provenance.py`
    is therefore this rule's only enforcement.

    This is a predicate on the *pair*, not a publication gate: whether a
    given SymGov use is permitted by an approved disposition is SM-P0-08.
    """
    if disposition not in PERMISSIVE_DISPOSITIONS:
        return True
    return rights_status in PERMITTING_RIGHTS_STATUSES


def _subject_kwargs(
    source_package_id: object,
    standard_version_id: object,
    symbol_revision_id: object,
) -> dict[str, uuid.UUID]:
    """Return the one subject that is set, or raise.

    Exactly one, never zero and never two. A rights record about "a package
    and also an edition" asserts one decision about two different things, and
    a record about nothing is not a record.
    """
    supplied = {
        "source_package_id": source_package_id,
        "standard_version_id": standard_version_id,
        "symbol_revision_id": symbol_revision_id,
    }
    present = {name: value for name, value in supplied.items() if value is not None}
    if len(present) != 1:
        raise ValueError(
            "a rights record must name exactly one subject: a source package, "
            "a standard version or a symbol revision"
        )
    name, value = next(iter(present.items()))
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise ValueError(f"{name} must be a real UUID")
    return {name: value}


def propose_rights_record(
    session: Session,
    *,
    disposition: str,
    determination_method: str,
    proposed_at: datetime,
    rights_status: str = "unknown",
    source_package_id: uuid.UUID | None = None,
    standard_version_id: uuid.UUID | None = None,
    symbol_revision_id: uuid.UUID | None = None,
    licence_reference: object = None,
    decision_reason: object = None,
    evidence: object = None,
    proposed_by_user_id: uuid.UUID | None = None,
) -> RightsRecord:
    """Propose a rights disposition for one subject.

    Every record starts as `proposed`, whatever produced it: principle P-07
    and section 8.4 keep machine proposals out of the governed record until a
    decision is taken. `decided_by_user_id`, `decided_at` and the reason are
    left unset here for the same reason -- they record an approval, and none
    has happened.

    `rights_status` defaults to `unknown`, which is section 13.1's first
    state and the only honest value before anyone has looked. A permissive
    disposition may be *proposed* on any status; only approving one is
    constrained, so a reviewer can put "I believe we may distribute this"
    forward and have the licence evidence demanded at the decision.
    """
    if disposition not in RIGHTS_DISPOSITIONS:
        raise ValueError("invalid rights disposition")
    if rights_status not in RIGHTS_STATUSES:
        raise ValueError("invalid rights status")
    if determination_method not in RIGHTS_DETERMINATION_METHODS:
        raise ValueError("invalid rights determination method")
    _require_aware_timestamp(proposed_at, "rights proposal time")
    _require_optional_actor(proposed_by_user_id, "rights proposer")
    subject = _subject_kwargs(source_package_id, standard_version_id, symbol_revision_id)
    normalized_licence = normalize_licence_reference(licence_reference)
    normalized_reason = normalize_decision_reason(decision_reason)
    normalized_evidence = normalize_rights_evidence(evidence)

    record = RightsRecord(
        id=uuid.uuid4(),
        rights_status=rights_status,
        disposition=disposition,
        licence_reference=normalized_licence,
        determination_method=determination_method,
        decision_status="proposed",
        decided_by_user_id=None,
        decided_at=None,
        decision_reason=normalized_reason,
        evidence_json=normalized_evidence,
        proposed_by_user_id=proposed_by_user_id,
        created_at=proposed_at,
        updated_at=proposed_at,
        **subject,
    )
    session.add(record)
    return record


def transition_rights_record(
    session: Session,
    record_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    decided_by_user_id: uuid.UUID | None = None,
    decision_reason: object = None,
    rights_status: str | None = None,
    licence_reference: object = None,
) -> RightsRecord:
    """Take a decision on one rights record.

    Approving requires section 7.12's full triple -- who, when and why. A
    named decider is required unconditionally, unlike section 7.10's
    verification: there is no controlled-system rights decision, and section
    8.4 forbids an `ai_assisted` determination from approving itself.

    `rights_status` and `licence_reference` may be corrected as part of the
    decision, because the decision is often what establishes them: a record
    proposed as `unknown` becomes `licensed` when the contract is found. A
    permissive disposition can only be approved on a status that supports it,
    and `licensed` or `restricted` cannot be approved without a reference to
    the terms.

    Approving a record retires whichever record was approved before it for
    the same subject, so the one-approved-record rule is upheld by succession
    rather than by refusing the new decision -- the pattern SM-P0-01 through
    -05 all use.

    Rejecting and retiring record who and when but no reason, matching
    `review_decision` on the other governed tables. Section 7.12 asks for the
    "why" of an approval specifically.
    """
    if target_status not in RIGHTS_DECISION_STATUSES:
        raise ValueError("invalid rights decision status")
    _require_aware_timestamp(occurred_at, "rights decision time")
    _require_optional_actor(decided_by_user_id, "rights decider")
    if rights_status is not None and rights_status not in RIGHTS_STATUSES:
        raise ValueError("invalid rights status")
    normalized_reason = normalize_decision_reason(decision_reason)
    normalized_licence = normalize_licence_reference(licence_reference)

    record = session.get(RightsRecord, record_id, with_for_update=True)
    if record is None:
        raise LookupError(f"rights record not found: {record_id}")

    current_status = record.decision_status
    if current_status not in RIGHTS_DECISION_TRANSITIONS:
        raise ValueError(f"rights record carries an unrecognised status: {current_status}")
    if target_status not in RIGHTS_DECISION_TRANSITIONS[current_status]:
        raise ValueError(f"a rights record cannot move from {current_status} to {target_status}")

    effective_status = rights_status if rights_status is not None else record.rights_status
    effective_licence = normalized_licence if licence_reference is not None else record.licence_reference

    if target_status == "approved":
        if decided_by_user_id is None:
            raise ValueError("approving a rights disposition requires a named decider")
        if normalized_reason is None and record.decision_reason is None:
            raise ValueError("approving a rights disposition requires a recorded reason")
        if record.determination_method in NON_APPROVING_DETERMINATION_METHODS:
            raise ValueError(
                f"a {record.determination_method} rights determination cannot be approved; "
                "it is a proposal until a reviewer determines the disposition"
            )
        if not disposition_is_permitted(effective_status, record.disposition):
            raise ValueError(
                f"a {record.disposition} disposition cannot be approved while the rights "
                f"status is {effective_status}"
            )
        if effective_status in LICENCE_BACKED_RIGHTS_STATUSES and effective_licence is None:
            raise ValueError(
                f"a {effective_status} rights status cannot be approved without a licence reference"
            )
        _retire_superseded_rights_record(session, record)
    elif target_status == "rejected":
        if decided_by_user_id is None:
            raise ValueError("rejecting a rights disposition requires a named decider")

    if target_status in {"approved", "rejected"}:
        record.decided_by_user_id = decided_by_user_id
        record.decided_at = occurred_at
    elif decided_by_user_id is not None:
        raise ValueError("a decider only applies to approving or rejecting a rights record")

    if rights_status is not None:
        record.rights_status = rights_status
    if licence_reference is not None:
        record.licence_reference = normalized_licence
    if normalized_reason is not None:
        record.decision_reason = normalized_reason
    record.decision_status = target_status
    record.updated_at = occurred_at
    return record


def _subject_filter(record: RightsRecord):
    """The equality test that selects the same subject as this record."""
    if record.source_package_id is not None:
        return RightsRecord.source_package_id == record.source_package_id
    if record.standard_version_id is not None:
        return RightsRecord.standard_version_id == record.standard_version_id
    return RightsRecord.symbol_revision_id == record.symbol_revision_id


def _retire_superseded_rights_record(session: Session, record: RightsRecord) -> None:
    superseded = session.execute(
        select(RightsRecord)
        .where(
            _subject_filter(record),
            RightsRecord.decision_status == "approved",
            RightsRecord.id != record.id,
        )
        .with_for_update()
    ).scalars().all()
    for previous in superseded:
        previous.decision_status = "retired"
        previous.updated_at = record.updated_at
    if superseded:
        # The partial unique index is evaluated per statement, so the
        # retirement has to reach the database before the successor claims it.
        session.flush()


def approved_rights_record(
    session: Session,
    *,
    source_package_id: uuid.UUID | None = None,
    standard_version_id: uuid.UUID | None = None,
    symbol_revision_id: uuid.UUID | None = None,
) -> RightsRecord | None:
    """Return the one approved rights record for a subject, if there is one."""
    subject = _subject_kwargs(source_package_id, standard_version_id, symbol_revision_id)
    name, value = next(iter(subject.items()))
    return session.execute(
        select(RightsRecord).where(
            getattr(RightsRecord, name) == value,
            RightsRecord.decision_status == "approved",
        )
    ).scalar_one_or_none()


def list_rights_records(
    session: Session,
    *,
    source_package_id: uuid.UUID | None = None,
    standard_version_id: uuid.UUID | None = None,
    symbol_revision_id: uuid.UUID | None = None,
    decision_status: str | None = None,
) -> list[RightsRecord]:
    """List a subject's rights records, oldest first.

    Rejected and retired records are included by default. Section 14.4 keeps
    the governance decision history with the governed data, and why a
    disposition was refused is part of it.
    """
    if decision_status is not None and decision_status not in RIGHTS_DECISION_STATUSES:
        raise ValueError("invalid rights decision status filter")
    subject = _subject_kwargs(source_package_id, standard_version_id, symbol_revision_id)
    name, value = next(iter(subject.items()))
    query = select(RightsRecord).where(getattr(RightsRecord, name) == value)
    if decision_status is not None:
        query = query.where(RightsRecord.decision_status == decision_status)
    return list(session.execute(query.order_by(RightsRecord.created_at, RightsRecord.id)).scalars())


# --------------------------------------------------------------------------
# Transformation lineage
# --------------------------------------------------------------------------


def record_asset_transformation(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    tool_name: str,
    tool_version: str,
    derived_asset_sha256: str,
    performed_at: datetime,
    source_asset_sha256: object = None,
    source_package_entry_id: uuid.UUID | None = None,
    evidence: object = None,
    recorded_by_user_id: uuid.UUID | None = None,
) -> AssetTransformation:
    """Append one step to a symbol revision's transformation chain.

    Section 7.12 asks for "source asset -> transformation tool/version ->
    derived asset", and Appendix B.2 for "source file SHA-256 -> approved
    transformation -> SVG SHA-256". `step_index` is allocated here rather than
    passed in, and the step must start from the digest the previous step
    produced -- that continuity is what makes the rows a chain rather than an
    unordered set, and it is a cross-row property no check constraint can
    express.

    The first step, and only the first, may name a `source_package_entry_id`:
    that is where an acquired asset enters the chain. If the entry records an
    `original_asset_sha256` (SM-P0-05) and no source digest is passed, the
    entry's digest is adopted rather than re-stated by the caller; if one is
    passed and disagrees, the step is refused rather than silently recording
    a different source.

    `tool_name` and `tool_version` are both required. Section 7.12 asks for
    the tool *and* its version because reproducing a transformation needs
    both, and a lineage step that cannot be reproduced records only that
    something happened.
    """
    normalized_tool = _normalize_required_text(tool_name, "transformation tool name", TOOL_NAME_MAX_LENGTH)
    normalized_version = _normalize_required_text(
        tool_version, "transformation tool version", TOOL_VERSION_MAX_LENGTH
    )
    normalized_derived = normalize_sha256(derived_asset_sha256, "derived asset hash")
    if normalized_derived is None:
        raise ValueError("a derived asset hash is required")
    normalized_source = normalize_sha256(source_asset_sha256, "source asset hash")
    _require_aware_timestamp(performed_at, "transformation time")
    _require_optional_actor(recorded_by_user_id, "transformation recorder")
    normalized_evidence = normalize_rights_evidence(evidence)
    if not isinstance(symbol_revision_id, uuid.UUID) or symbol_revision_id.int == 0:
        raise ValueError("symbol_revision_id must be a real UUID")
    if source_package_entry_id is not None and (
        not isinstance(source_package_entry_id, uuid.UUID) or source_package_entry_id.int == 0
    ):
        raise ValueError("source_package_entry_id must be a real UUID")
    steps = _existing_steps(session, symbol_revision_id)
    previous = steps[-1] if steps else None

    if previous is None:
        # The chain has to start somewhere identified. A later step needs
        # neither argument, because it continues from its predecessor.
        if normalized_source is None and source_package_entry_id is None:
            raise ValueError(
                "the first step of a transformation chain must identify its source, "
                "by digest or by the source package entry"
            )
        if source_package_entry_id is not None:
            normalized_source = _entry_source_digest(
                session, source_package_entry_id, symbol_revision_id, normalized_source
            )
    else:
        if source_package_entry_id is not None:
            raise ValueError(
                "only the first step of a transformation chain may name a source package entry"
            )
        if normalized_source is None:
            normalized_source = previous.derived_asset_sha256
        elif normalized_source != previous.derived_asset_sha256:
            raise ValueError(
                "a transformation step must start from the digest the previous step produced"
            )

    if normalized_source is not None and normalized_source == normalized_derived:
        raise ValueError("a transformation that produced an identical asset transformed nothing")
    seen = {step.derived_asset_sha256 for step in steps}
    seen |= {step.source_asset_sha256 for step in steps if step.source_asset_sha256 is not None}
    if normalized_derived in seen:
        raise ValueError(
            "that derived asset digest already appears in this revision's chain; "
            "a transformation chain cannot revisit an asset"
        )

    transformation = AssetTransformation(
        id=uuid.uuid4(),
        symbol_revision_id=symbol_revision_id,
        step_index=1 if previous is None else previous.step_index + 1,
        source_package_entry_id=source_package_entry_id,
        source_asset_sha256=normalized_source,
        tool_name=normalized_tool,
        tool_version=normalized_version,
        derived_asset_sha256=normalized_derived,
        performed_at=performed_at,
        evidence_json=normalized_evidence,
        recorded_by_user_id=recorded_by_user_id,
        created_at=performed_at,
    )
    session.add(transformation)
    return transformation


def _existing_steps(session: Session, symbol_revision_id: uuid.UUID) -> list[AssetTransformation]:
    return list(
        session.execute(
            select(AssetTransformation)
            .where(AssetTransformation.symbol_revision_id == symbol_revision_id)
            .order_by(AssetTransformation.step_index)
            .with_for_update()
        ).scalars()
    )


def _entry_source_digest(
    session: Session,
    source_package_entry_id: uuid.UUID,
    symbol_revision_id: uuid.UUID,
    supplied_digest: str | None,
) -> str | None:
    """Reconcile the first step's source digest with the package entry's.

    The entry must belong to the same symbol revision. A composite foreign key
    would say so in the database, but it would need a new unique key on
    `source_package_entries`, and SM-P0-06 adds no column or constraint to a
    pre-existing table -- so this is service policy, pinned by a test.
    """
    entry = session.get(SourcePackageEntry, source_package_entry_id)
    if entry is None:
        raise LookupError(f"source package entry not found: {source_package_entry_id}")
    if entry.symbol_revision_id != symbol_revision_id:
        raise ValueError("the source package entry belongs to a different symbol revision")
    if entry.original_asset_sha256 is None:
        return supplied_digest
    if supplied_digest is None:
        return entry.original_asset_sha256
    if supplied_digest != entry.original_asset_sha256:
        raise ValueError(
            "the source digest disagrees with the source package entry's original asset hash"
        )
    return supplied_digest


def trace_asset_lineage(session: Session, symbol_revision_id: uuid.UUID) -> list[dict]:
    """Return a symbol revision's transformation chain, in order.

    A key whose value is None means the provenance was never recorded, not
    that it does not exist. Nothing here invents a value to fill a gap.
    """
    rows = session.execute(
        select(AssetTransformation)
        .where(AssetTransformation.symbol_revision_id == symbol_revision_id)
        .order_by(AssetTransformation.step_index)
    ).scalars()
    return [
        {
            "transformation_id": step.id,
            "step_index": step.step_index,
            "source_package_entry_id": step.source_package_entry_id,
            "source_asset_sha256": step.source_asset_sha256,
            "tool_name": step.tool_name,
            "tool_version": step.tool_version,
            "derived_asset_sha256": step.derived_asset_sha256,
            "performed_at": step.performed_at,
            "recorded_by_user_id": step.recorded_by_user_id,
        }
        for step in rows
    ]


def lineage_reaches_source_package(session: Session, symbol_revision_id: uuid.UUID) -> bool:
    """Whether the chain runs all the way back to an acquired package entry.

    Section 13.1's representation-lineage dimension distinguishes "imported"
    and "transformed" from "full source-to-derived lineage"; this answers the
    last of those three and nothing more. Deriving the dimension itself, and
    the section 13.2 traceability level, is reporting work -- SM-P1-06.
    """
    first = session.execute(
        select(AssetTransformation.source_package_entry_id)
        .where(
            AssetTransformation.symbol_revision_id == symbol_revision_id,
            AssetTransformation.step_index == 1,
        )
    ).scalar_one_or_none()
    return first is not None


# --------------------------------------------------------------------------
# Read-only reporting
# --------------------------------------------------------------------------


def _rights_summary(record: RightsRecord | None) -> dict | None:
    if record is None:
        return None
    return {
        "rights_record_id": record.id,
        "rights_status": record.rights_status,
        "disposition": record.disposition,
        "licence_reference": record.licence_reference,
        "determination_method": record.determination_method,
        "decision_status": record.decision_status,
        "decided_by_user_id": record.decided_by_user_id,
        "decided_at": record.decided_at,
        "decision_reason": record.decision_reason,
    }


def symbol_revision_rights_provenance(session: Session, symbol_revision_id: uuid.UUID) -> dict:
    """Report every rights subject in one symbol revision's provenance chain.

    This is the rights half of section 16.1's acceptance criterion -- "an
    ingested authoritative symbol can be traced to source package,
    release/version, exact provider entry/symbol ID where available, rights
    disposition and hashes" -- and the half
    `source_package_acquisition.trace_symbol_revision_sources` had to leave
    as a forward pointer.

    Strictly read-only, and deliberately no gate.
    `source_package_acquisition.requires_licence_reference` is wired in here
    as a *report*: it flags the two acquisition methods that imply someone
    accepted terms, and `unresolved_subjects` lists the subjects that carry no
    approved rights record. Refusing publication on that basis is SM-P0-08.

    No precedence between the three subjects is invented. A revision's own
    approved record, its packages' and its cited editions' are all returned;
    which one governs a given SymGov use is the publication gate's decision,
    and inventing an inheritance rule here would pre-empt it.
    """
    revision_rights = session.execute(
        select(RightsRecord).where(
            RightsRecord.symbol_revision_id == symbol_revision_id,
            RightsRecord.decision_status == "approved",
        )
    ).scalar_one_or_none()

    package_rows = session.execute(
        select(SourcePackageEntry, SourcePackage)
        .join(SourcePackage, SourcePackage.id == SourcePackageEntry.source_package_id)
        .where(SourcePackageEntry.symbol_revision_id == symbol_revision_id)
        .order_by(SourcePackage.package_code)
    ).all()

    unresolved: list[dict] = []
    packages: list[dict] = []
    for entry, package in package_rows:
        approved = approved_rights_record(session, source_package_id=package.id)
        licence_expected = requires_licence_reference(package.acquisition_method)
        packages.append(
            {
                "source_package_id": package.id,
                "package_code": package.package_code,
                "provider": package.provider,
                "acquisition_method": package.acquisition_method,
                "licence_reference": package.licence_reference,
                "requires_licence_reference": licence_expected,
                "source_package_entry_id": entry.id,
                "approved_rights": _rights_summary(approved),
            }
        )
        if approved is None:
            unresolved.append(
                {
                    "subject": "source_package",
                    "subject_id": package.id,
                    "requires_licence_reference": licence_expected,
                }
            )

    edition_rows = session.execute(
        select(StandardVersion.id, SymbolStandardLink.relationship_type, SymbolStandardLink.assertion_status)
        .join(SymbolStandardLink, SymbolStandardLink.standard_version_id == StandardVersion.id)
        .where(SymbolStandardLink.symbol_revision_id == symbol_revision_id)
        .order_by(StandardVersion.id)
    ).all()

    editions: list[dict] = []
    for version_id, relationship_type, assertion_status in edition_rows:
        approved = approved_rights_record(session, standard_version_id=version_id)
        editions.append(
            {
                "standard_version_id": version_id,
                "relationship_type": relationship_type,
                "assertion_status": assertion_status,
                "approved_rights": _rights_summary(approved),
            }
        )
        if approved is None:
            unresolved.append(
                {
                    "subject": "standard_version",
                    "subject_id": version_id,
                    "requires_licence_reference": False,
                }
            )

    if revision_rights is None:
        unresolved.append(
            {
                "subject": "symbol_revision",
                "subject_id": symbol_revision_id,
                "requires_licence_reference": False,
            }
        )

    return {
        "symbol_revision_id": symbol_revision_id,
        "revision_rights": _rights_summary(revision_rights),
        "source_packages": packages,
        "standard_versions": editions,
        "unresolved_subjects": unresolved,
        "lineage_steps": trace_asset_lineage(session, symbol_revision_id),
        "lineage_reaches_source_package": lineage_reaches_source_package(
            session, symbol_revision_id
        ),
    }
