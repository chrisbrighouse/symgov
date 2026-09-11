"""The minimum publication gate for authoritative ingestion (SM-P0-08).

Specification section 9.2, scoped by section 17: "apply to new authoritative
ingestion profiles; grandfather current public data", which section 12.1's
phase M6 repeats as a migration phase.

**Measure section 9.2 against what a symbol can actually satisfy before
reading any further.** As of 20260910_0056 the six dimensions stood like
this, and the measurement is the reason this module is shaped the way it is:

* *Semantic identity* -- unsatisfiable by any path. Nothing in
  `symgov_backend` imports `semantic_concepts`, so no `SemanticConcept` row is
  ever created in production and no assignment can point at one. Section 9.2
  supplies the only way through: "an explicit approved 'semantic identity
  pending' exception".
* *Source* -- satisfiable since SM-P0-07, which makes promotion write a
  `source_package_entries` row. Not satisfiable for anything promoted before
  it.
* *Graphical authority* -- satisfiable sometimes. SM-P0-07 proposes a link
  only on an exact standard-code match with exactly one active edition.
* *Rights* -- unsatisfiable. Nothing imports `rights_provenance` either, so
  no `RightsRecord` exists. SM-P0-08 changes half of that: see
  `propose_intake_rights_record` below, which makes a *proposal* durable. It
  still cannot approve one.
* *Integrity* -- satisfiable on the organisation path, where
  `organization_symbol_drafts` hashes every asset at upload. Not satisfiable
  from the payload on the Rupert path, whose
  `publication_handoff.ensure_approved_symbol_revision` writes no `assets`
  key at all.
* *Classification* -- satisfiable since SM-P0-07, though
  `test_an_entirely_unmappable_classification_still_promotes` builds the
  symbol that has none.

So a gate switched on for the current publication path would publish nothing.
That is not an argument against the gate; it is exactly why section 17 scopes
it to new authoritative ingestion and grandfathers what is already public.

**What "new authoritative ingestion" means here, concretely.** A revision is
in scope when it reaches a `source_packages` row whose `package_type` is
`source_package_acquisition.AUTHORITATIVE_PACKAGE_TYPE`
(`authoritative_library`), which SM-P0-05 already defined and which
`register_source_package` already takes as its default. Every package in
production is created by `runtime.ensure_source_package_for_intake` as
`submission_sheet`; `register_source_package` is called from tests only. The
grandfathering is therefore a property of the data rather than a cutoff date
or a backfill flag, and a connector that registers an authoritative library
is gated from its first symbol without having to opt in. Nothing existing
becomes unpublishable.

**This is not `automation_policy.evaluate_publication_automation_gate`.**
That function is live, is also called a publication gate, and answers a
different question on a different path: whether a symbol may skip human
review and go straight to Rupert. It reads `provenance_assessments`, whose
rights vocabulary (`cleared | unknown_warning | restricted | conflict |
failed`) shares not one value with `rights_records`' -- the finding
20260910_0056 records at length. The two gates coexist and stay strangers:
neither calls the other, neither reads the other's tables, and extending that
function instead of adding this one would have put section 9.2's gate on the
automation path reading the wrong table.
`tests/test_publication_gate.py` pins the two vocabularies apart.

**The rights inheritance rule section 9.2 left open.**
`rights_provenance.symbol_revision_rights_provenance` deliberately invented no
precedence between a revision's own rights record, its packages' and its cited
standard editions', because "which one governs a given SymGov use is the
publication gate's decision". It is this:

* The revision's own approved record governs when it exists. It is the most
  specific, and it is about the exact asset SymGov stores.
* Failing that, every source package the revision traces to must carry an
  approved permissive record. Every, not any: a revision assembled from two
  packages needs permission from both, and a licence granted over one release
  says nothing about another.
* A standard edition's record is reported and never sufficient. Rights to
  read a standard are not rights to publish a symbol derived from it, and
  section 8.3's `derived_from` is precisely the case where SymGov redrew
  someone else's graphic.

The disposition must additionally be permissive
(`rights_provenance.PERMISSIVE_DISPOSITIONS`), because publishing to the
public catalogue renders and hands on the asset. `metadata_only` and `reject`
are honest records of a work SymGov may not publish, so an approved record
carrying one refuses the dimension rather than satisfying it.

**What the gate blocks and what it only reports.** For an in-scope revision
it blocks: the caller receives a refusal and the publication does not happen.
For everything else it reports -- the evaluation is recorded with all six
dimensions and the section 13.2 level, outcome `not_in_scope`, and
publication proceeds. SM-P0-07's rule that "a mapping failure never blocks
promotion" does not carry over; a gate that never blocks is not a gate. What
makes blocking safe today is that the in-scope set is empty, not that the
refusal is soft.

**Section 13.2's T0-T5 ships here, and only as a derivation.**
`derive_traceability_level` is a pure function of the same facts the six
dimensions already need, so computing it costs nothing and a refusal that
does not say how far the symbol got loses evidence. It is stored on the
evaluation row. The dashboard, the API surface and the coverage metrics of
section 13.3 are SM-P1-06 and are not here.

`GATE_REFUSAL_REASONS` is the **eighth** vocabulary in this model and the
second diagnostic one, after `classification_mapping.MAPPING_GAP_REASONS`. It
never reaches a `method` column, shares no value with any of the six method
vocabularies or with the gap reasons, and `tests/test_publication_gate.py`
pins all of that.

No read route is exposed here, so section 14.2's private-symbol boundary is
untouched: every entry point is called from inside a publication path that
has already established its own authority.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .classification_assignments import list_symbol_revision_classifications
from .models import (
    Attachment,
    ProvenanceAssessment,
    PublicationGateEvaluation,
    PublicationGateException,
    SourcePackage,
    SourcePackageEntry,
    SymbolRevision,
)
from .rights_provenance import (
    PERMISSIVE_DISPOSITIONS,
    approved_rights_record,
    lineage_reaches_source_package,
    propose_rights_record,
    trace_asset_lineage,
)
from .source_package_acquisition import AUTHORITATIVE_PACKAGE_TYPE
from .standard_sources import (
    AUTHORITATIVE_RELATIONSHIP_TYPES,
    SOURCE_RELATIONSHIP_TYPES,
    list_symbol_standard_links,
)
from .symbol_semantic_assignments import (
    list_symbol_semantic_assignments,
    verified_primary_assignment,
)

# Section 7.13: a persisted snapshot "should store the dimensions and policy
# version used". A refusal under a policy nobody can name is not auditable.
PUBLICATION_GATE_POLICY_VERSION = "symgov-publication-gate-v1"

# Section 9.2's six dimensions, in the order the specification tabulates them.
# The snake_case spelling is this model's; the words are the specification's.
GATE_DIMENSIONS = (
    "semantic_identity",
    "source",
    "graphical_authority",
    "rights",
    "integrity",
    "classification",
)

# What one evaluation concluded. `not_in_scope` is section 17's
# grandfathering, recorded as an outcome rather than as an absence.
GATE_OUTCOMES = frozenset({"permitted", "refused", "not_in_scope"})

# Section 13.2, verbatim.
TRACEABILITY_LEVELS = ("T0", "T1", "T2", "T3", "T4", "T5")

# The **eighth** vocabulary in this model and the second *diagnostic* one.
# It never reaches a `method` column and shares no value with any of the six
# method vocabularies or with `classification_mapping.MAPPING_GAP_REASONS`;
# `tests/test_publication_gate.py` pins both.
GATE_REFUSAL_REASONS = frozenset(
    {
        "semantic_identity_unverified",
        "source_package_unrecorded",
        "graphical_authority_unasserted",
        "rights_undecided",
        "rights_disposition_withholds",
        "final_asset_hash_absent",
        "classification_absent",
    }
)

# The dimension each refusal reason belongs to. Every reason names exactly
# one dimension, so a caller can say which of section 9.2's six refused
# without re-deriving it.
REFUSAL_REASON_DIMENSIONS: dict[str, str] = {
    "semantic_identity_unverified": "semantic_identity",
    "source_package_unrecorded": "source",
    "graphical_authority_unasserted": "graphical_authority",
    "rights_undecided": "rights",
    "rights_disposition_withholds": "rights",
    "final_asset_hash_absent": "integrity",
    "classification_absent": "classification",
}

# Which of section 9.2's six an approved exception may waive. Section 9.2
# offers exactly one -- "an explicit approved 'semantic identity pending'
# exception for non-engineering/annotation symbols" -- and offers none for
# the other five. Section 16.2's "no public authoritative-source symbol is
# newly published with unresolved rights" is why `rights` is not here, and is
# the one this set most matters for.
#
# Service policy, not a check constraint, and deliberately the same split
# 20260910_0056 settled on for `disposition_is_permitted`: the *vocabulary*
# is storage and the *policy* is service, so loosening a policy never needs a
# migration. `publication_gate_exceptions.dimension` accepts all six;
# `propose_publication_gate_exception` accepts these.
WAIVABLE_DIMENSIONS = frozenset({"semantic_identity"})

# The package types that put a revision in scope. SM-P0-05's constant, and
# `register_source_package`'s default. `submission_sheet` -- every package the
# live intake path has ever created -- is deliberately absent.
GATED_PACKAGE_TYPES = frozenset({AUTHORITATIVE_PACKAGE_TYPE})

# The governance lifecycle for a waiver. `approved` rather than `verified`
# for 20260910_0056's reason: excusing a publication requirement is an
# approval, not a verification of fact.
GATE_EXCEPTION_STATUSES = frozenset({"proposed", "approved", "rejected", "retired"})

# A rejected waiver is terminal: a reviewer who changes their mind proposes
# afresh. The shape SM-P0-02 through -06 all use.
GATE_EXCEPTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"approved", "rejected", "retired"}),
    "approved": frozenset({"retired"}),
    "rejected": frozenset(),
    "retired": frozenset(),
}

APPROVAL_REASON_MAX_LENGTH = 2000

# Classification assignment and semantic assignment statuses that keep a row
# live. A retired or rejected assignment satisfies nothing.
_LIVE_ASSIGNMENT_STATUSES = frozenset({"proposed", "verified"})

# --------------------------------------------------------------------------
# Section 9.2's rights dimension needs an approved `RightsRecord`, and nothing
# in production writes one. This is the mapping that makes an intake
# assessment durable as a *proposal*, so a reviewer has something to decide
# on rather than a blank page.
#
# It reads `provenance_assessments.rights_disposition` and not
# `.rights_status`: the disposition column carries a deployed check constraint
# and therefore a known vocabulary, while `rights_status` carries none and is
# written straight from an agent payload (`runtime.py:2740`), where observed
# values include `public_domain`, `cc0` and `cc-by` alongside the five below.
#
# The proposed section 7.12 *status* is always `unknown`, whatever the
# assessment said. That is the honest value and the whole finding
# 20260910_0056 recorded: an intake assessment never reads a licence, so it
# cannot establish `open` or `licensed`. Only the proposed *disposition*
# varies, and `transition_rights_record` will demand the licence evidence at
# the decision -- which is exactly the shape `propose_rights_record`'s
# docstring describes ("a reviewer can put 'I believe we may distribute this'
# forward and have the licence evidence demanded at the decision").
#
# `cleared` therefore proposes `display`, which cannot be *approved* while
# the status is `unknown`. That is deliberate, not an oversight: the reviewer
# must establish the licence before approving, and the proposal records what
# the assessment actually found instead of discarding it.
INTAKE_RIGHTS_DISPOSITION_PROPOSALS: dict[str, str] = {
    # No rights concern found. Carry the intent forward as a rendering
    # proposal; the status stays `unknown` because nothing established terms.
    "cleared": "display",
    # Nothing established either way.
    "unknown_warning": "metadata_only",
    # A restriction was observed. Non-permissive until someone reads the
    # terms.
    "restricted": "metadata_only",
    # Conflicting rights signals. Section 10.2 treats a conflict as an
    # escalation, and the safe direction is a proposal a reviewer must
    # actively overturn.
    "conflict": "reject",
    # The assessment did not complete, so it established nothing.
    "failed": "metadata_only",
}

# Section 8.4 with no controlled-system exception: an agent's reading of a
# submission is a proposal. `ai_assisted` also makes the row unapprovable by
# `rights_records`' `approved_not_ai_determined` constraint, which is correct
# -- a reviewer proposes their own record rather than approving Tracy's.
INTAKE_RIGHTS_DETERMINATION_METHOD = "ai_assisted"


def _require_aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise ValueError(f"{label} must be a timezone-aware datetime")
    return value


def _require_optional_actor(value: object, label: str) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise ValueError(f"{label} must be a real UUID")
    return value


def _normalize_approval_reason(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) > APPROVAL_REASON_MAX_LENGTH:
        raise ValueError(
            f"a gate exception reason must be at most {APPROVAL_REASON_MAX_LENGTH} characters"
        )
    return text


def _normalize_evidence(value: object) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("gate exception evidence must be an object")
    return dict(value)


# --------------------------------------------------------------------------
# The facts the evaluator reads. Deliberately plain: every field is a bool,
# an int, a string or a tuple of them, so the whole of `evaluate_publication_
# gate` is provable without a database. `collect_publication_gate_facts` is
# the only part that needs one.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PublicationGateFacts:
    """Everything section 9.2's six dimensions need, already loaded."""

    symbol_revision_id: uuid.UUID

    # Scope. `gated_package_ids` are the authoritative packages this revision
    # reaches; `source_package_ids` are all of them, gated or not.
    gated_package_ids: tuple[uuid.UUID, ...] = ()
    source_package_ids: tuple[uuid.UUID, ...] = ()

    # Section 9.2 source: "SourcePackage recorded; exact source locator where
    # available". The locator is evidence, never a requirement -- "where
    # available" is the specification's own hedge.
    source_locator_recorded: bool = False
    package_release_identified: bool = False

    # Section 9.2 semantic identity.
    verified_primary_concept: bool = False
    live_semantic_assignment_count: int = 0
    waived_dimensions: frozenset[str] = frozenset()

    # Section 9.2 graphical authority. Verified links only for the dimension;
    # the live set is evidence about what is still awaiting review.
    verified_relationship_types: tuple[str, ...] = ()
    live_relationship_types: tuple[str, ...] = ()

    # Section 9.2 rights. Each entry is the approved `(rights_status,
    # disposition)` pair for that subject, or None where no record is
    # approved.
    revision_rights: tuple[str, str] | None = None
    package_rights: tuple[tuple[uuid.UUID, tuple[str, str] | None], ...] = ()
    edition_rights: tuple[tuple[uuid.UUID, tuple[str, str] | None], ...] = ()

    # Section 9.2 integrity: "at least the final stored asset hash; source
    # hash where available".
    final_asset_sha256: str | None = None
    final_asset_hash_source: str | None = None
    source_asset_hash_recorded: bool = False
    lineage_reaches_source_package: bool = False

    # Section 9.2 classification.
    live_classification_count: int = 0
    verified_classification_count: int = 0

    # Section 13.2 T4's "verified external semantic mapping where applicable".
    external_mapping_count: int = 0
    verified_external_mapping_count: int = 0


@dataclass(frozen=True)
class DimensionResult:
    dimension: str
    satisfied: bool
    waived: bool = False
    reason: str | None = None
    evidence: dict = field(default_factory=dict)

    def as_report(self) -> dict:
        return {
            "dimension": self.dimension,
            "satisfied": self.satisfied,
            "waived": self.waived,
            "reason": self.reason,
            "evidence": _jsonable(self.evidence),
        }


@dataclass(frozen=True)
class PublicationGateDecision:
    symbol_revision_id: uuid.UUID
    in_scope: bool
    source_package_id: uuid.UUID | None
    outcome: str
    dimensions: tuple[DimensionResult, ...]
    refusal_reasons: tuple[str, ...]
    traceability_level: str
    policy_version: str = PUBLICATION_GATE_POLICY_VERSION

    @property
    def permitted(self) -> bool:
        """Whether publication may proceed.

        True for `not_in_scope` as well as `permitted`: section 17
        grandfathers current public data, so an out-of-scope revision
        publishes. The two are kept distinct in `outcome` precisely so that
        "published because it passed" and "published because it was
        grandfathered" are never confused in the record.
        """
        return self.outcome in {"permitted", "not_in_scope"}

    def as_report(self) -> dict:
        return {
            "symbol_revision_id": str(self.symbol_revision_id),
            "in_scope": self.in_scope,
            "source_package_id": str(self.source_package_id) if self.source_package_id else None,
            "outcome": self.outcome,
            "traceability_level": self.traceability_level,
            "policy_version": self.policy_version,
            "refusal_reasons": list(self.refusal_reasons),
            "dimensions": [result.as_report() for result in self.dimensions],
        }


def _jsonable(value: Any) -> Any:
    """UUIDs and datetimes reach JSONB as strings; nothing else is changed."""
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


# --------------------------------------------------------------------------
# The evaluator. A pure function of `PublicationGateFacts`.
# --------------------------------------------------------------------------


def _semantic_identity(facts: PublicationGateFacts) -> DimensionResult:
    """Section 9.2: one verified primary concept, or an approved exception."""
    evidence = {
        "verified_primary_concept": facts.verified_primary_concept,
        "live_semantic_assignments": facts.live_semantic_assignment_count,
    }
    if facts.verified_primary_concept:
        return DimensionResult("semantic_identity", True, evidence=evidence)
    if "semantic_identity" in facts.waived_dimensions:
        # Section 9.2's "explicit approved 'semantic identity pending'
        # exception". Satisfied *and* flagged: a waiver is not a pass, and the
        # record has to be able to tell them apart.
        return DimensionResult("semantic_identity", True, waived=True, evidence=evidence)
    return DimensionResult(
        "semantic_identity", False, reason="semantic_identity_unverified", evidence=evidence
    )


def _source(facts: PublicationGateFacts) -> DimensionResult:
    """Section 9.2: SourcePackage recorded; exact source locator where available."""
    evidence = {
        "source_package_count": len(facts.source_package_ids),
        # Named, not just counted: `publication_gate_evaluations` records one
        # `source_package_id`, and a revision assembled from two packages
        # would otherwise lose the second.
        "source_package_ids": [str(value) for value in facts.source_package_ids],
        "gated_source_package_ids": [str(value) for value in facts.gated_package_ids],
        "source_locator_recorded": facts.source_locator_recorded,
        "package_release_identified": facts.package_release_identified,
    }
    if facts.source_package_ids:
        return DimensionResult("source", True, evidence=evidence)
    return DimensionResult("source", False, reason="source_package_unrecorded", evidence=evidence)


def _graphical_authority(facts: PublicationGateFacts) -> DimensionResult:
    """Section 9.2: relationship type explicitly asserted; no ambiguous label.

    "Explicitly asserted" is read as section 7.10's `assertion_status =
    'verified'`, not as the existence of a proposal. Section 8.4 is direct
    about it: "normative graphical-source assertions should require human or
    deterministic authoritative-source verification for public publication".
    A `proposed` link -- which is all SM-P0-07's mapping ever writes -- is the
    ambiguous standard-association this dimension exists to refuse.

    Any of section 8.3's eight types satisfies it. `vendor_implementation`
    and `owner_variant` are explicit assertions about a graphic's authority
    just as `normative_definition` is; the dimension asks that the
    relationship be *stated*, not that it be normative. Whether it is one of
    the two authoritative types is carried as evidence, and feeds section
    13.2's level.
    """
    authoritative = tuple(
        value for value in facts.verified_relationship_types if value in AUTHORITATIVE_RELATIONSHIP_TYPES
    )
    evidence = {
        "verified_relationship_types": list(facts.verified_relationship_types),
        "live_relationship_types": list(facts.live_relationship_types),
        "authoritative_relationship_types": list(authoritative),
    }
    if facts.verified_relationship_types:
        return DimensionResult("graphical_authority", True, evidence=evidence)
    return DimensionResult(
        "graphical_authority", False, reason="graphical_authority_unasserted", evidence=evidence
    )


def _rights(facts: PublicationGateFacts) -> DimensionResult:
    """Section 9.2: the disposition explicitly permits the intended SymGov use.

    The precedence rule `rights_provenance.symbol_revision_rights_provenance`
    deliberately left to this package. See the module docstring; in short, the
    revision's own approved record governs, failing that *every* source
    package must carry one, and a standard edition's is evidence only.
    """
    permitted_subject: str | None = None
    withholding_subject: str | None = None

    if facts.revision_rights is not None:
        _status, disposition = facts.revision_rights
        if disposition in PERMISSIVE_DISPOSITIONS:
            permitted_subject = "symbol_revision"
        else:
            withholding_subject = "symbol_revision"
    elif facts.package_rights:
        # Every package, not any: a revision assembled from two packages needs
        # permission from both, and a licence over one release says nothing
        # about another.
        decided = [pair for _id, pair in facts.package_rights if pair is not None]
        if len(decided) == len(facts.package_rights):
            if all(disposition in PERMISSIVE_DISPOSITIONS for _status, disposition in decided):
                permitted_subject = "source_package"
            else:
                withholding_subject = "source_package"

    evidence = {
        "revision_rights": list(facts.revision_rights) if facts.revision_rights else None,
        "source_packages_with_approved_rights": sum(
            1 for _id, pair in facts.package_rights if pair is not None
        ),
        "source_package_count": len(facts.package_rights),
        # Reported and never sufficient: rights to read a standard are not
        # rights to publish a symbol derived from it.
        "standard_editions_with_approved_rights": sum(
            1 for _id, pair in facts.edition_rights if pair is not None
        ),
        "standard_edition_count": len(facts.edition_rights),
        "governing_subject": permitted_subject or withholding_subject,
    }

    if permitted_subject is not None:
        return DimensionResult("rights", True, evidence=evidence)
    if withholding_subject is not None:
        # An approved record exists and says no. That is a different failure
        # from nobody having decided, and section 13.1's rights dimension
        # distinguishes them.
        return DimensionResult(
            "rights", False, reason="rights_disposition_withholds", evidence=evidence
        )
    return DimensionResult("rights", False, reason="rights_undecided", evidence=evidence)


def _integrity(facts: PublicationGateFacts) -> DimensionResult:
    """Section 9.2: at least the final stored asset hash; source hash where available."""
    evidence = {
        "final_asset_sha256": facts.final_asset_sha256,
        "final_asset_hash_source": facts.final_asset_hash_source,
        "source_asset_hash_recorded": facts.source_asset_hash_recorded,
        "lineage_reaches_source_package": facts.lineage_reaches_source_package,
    }
    if facts.final_asset_sha256:
        return DimensionResult("integrity", True, evidence=evidence)
    return DimensionResult("integrity", False, reason="final_asset_hash_absent", evidence=evidence)


def _classification(facts: PublicationGateFacts) -> DimensionResult:
    """Section 9.2: at least one governed classification assignment.

    A *live* assignment -- `proposed` or `verified` -- satisfies this, and the
    contrast with the semantic-identity row is deliberate drafting rather than
    an omission: that row says "verified primary" in as many words, and this
    one says only "governed ... assignment for discoverability". Discovery
    works from a proposal; engineering identity does not. How many are
    verified is carried as evidence and feeds section 13.2's level.
    """
    evidence = {
        "live_classification_assignments": facts.live_classification_count,
        "verified_classification_assignments": facts.verified_classification_count,
    }
    if facts.live_classification_count > 0:
        return DimensionResult("classification", True, evidence=evidence)
    return DimensionResult("classification", False, reason="classification_absent", evidence=evidence)


_DIMENSION_EVALUATORS = {
    "semantic_identity": _semantic_identity,
    "source": _source,
    "graphical_authority": _graphical_authority,
    "rights": _rights,
    "integrity": _integrity,
    "classification": _classification,
}


def derive_traceability_level(
    facts: PublicationGateFacts, dimensions: Iterable[DimensionResult]
) -> str:
    """Section 13.2's T0-T5, derived from the facts the gate already holds.

    A monotone ladder: the level is the highest rung whose condition holds
    and whose every lower rung holds too, so the answer stops at the first
    gap. Section 13.2 calls the level "a convenience indicator" and insists
    the underlying dimensions stay visible, which is why the evaluation row
    stores both.

    "Where applicable" is load-bearing at T4: a symbol with no external
    mapping at all is not held back for lacking a verified one -- section
    13.2 says so directly ("an architectural or safety symbol may be fully
    traceable without a DEXPI mapping").

    This is the derivation only. Section 13.3's coverage metrics and the
    dashboard that reads them are SM-P1-06.
    """
    by_name = {result.dimension: result for result in dimensions}

    # T1 Source identified.
    if not facts.source_package_ids:
        return "T0"
    # T2 Source package/version identified.
    if not facts.package_release_identified:
        return "T1"
    # T3 Exact source entry/symbol and rights disposition established.
    rights = by_name.get("rights")
    if not (facts.source_locator_recorded and rights is not None and rights.satisfied):
        return "T2"
    # T4 Verified SymGov semantic concept plus verified external mapping
    # where applicable. A waiver is not a verified concept: section 9.2's
    # exception lets a symbol publish, not lets it claim semantic identity.
    external_ok = (
        facts.external_mapping_count == 0 or facts.verified_external_mapping_count > 0
    )
    if not (facts.verified_primary_concept and external_ok):
        return "T3"
    # T5 End-to-end source, transformation, semantic, rights and governance
    # provenance complete under the current policy version.
    graphical = by_name.get("graphical_authority")
    integrity = by_name.get("integrity")
    complete = (
        facts.lineage_reaches_source_package
        and integrity is not None
        and integrity.satisfied
        and graphical is not None
        and graphical.satisfied
        and facts.verified_classification_count > 0
    )
    return "T5" if complete else "T4"


def evaluate_publication_gate(facts: PublicationGateFacts) -> PublicationGateDecision:
    """Apply section 9.2's six dimensions. Pure; reads no database.

    All six are always evaluated, whatever the scope and whatever fails
    first. A partial evaluation is not a gate decision, an out-of-scope
    symbol still needs its traceability gaps reported (section 12.1's M6),
    and a refusal that stops at the first failure makes a reviewer fix one
    thing at a time.
    """
    dimensions = tuple(_DIMENSION_EVALUATORS[name](facts) for name in GATE_DIMENSIONS)
    refusal_reasons = tuple(
        result.reason for result in dimensions if not result.satisfied and result.reason
    )
    in_scope = bool(facts.gated_package_ids)
    if not in_scope:
        outcome = "not_in_scope"
    elif refusal_reasons:
        outcome = "refused"
    else:
        outcome = "permitted"

    return PublicationGateDecision(
        symbol_revision_id=facts.symbol_revision_id,
        in_scope=in_scope,
        source_package_id=facts.gated_package_ids[0] if in_scope else None,
        outcome=outcome,
        dimensions=dimensions,
        refusal_reasons=refusal_reasons,
        traceability_level=derive_traceability_level(facts, dimensions),
    )


def describe_refusal(decision: PublicationGateDecision) -> str:
    """One operator-readable sentence naming what refused and why."""
    if decision.outcome != "refused":
        return "The publication gate did not refuse this revision."
    parts = ", ".join(
        f"{REFUSAL_REASON_DIMENSIONS[reason]} ({reason})" for reason in decision.refusal_reasons
    )
    return (
        "The section 9.2 publication gate refused this revision on "
        f"{len(decision.refusal_reasons)} dimension(s): {parts}."
    )


# --------------------------------------------------------------------------
# Loading the facts.
# --------------------------------------------------------------------------


def _asset_hashes_from_payload(revision: SymbolRevision | None) -> str | None:
    """The last hashed asset in `payload_json["assets"]`, if there is one.

    The organisation draft path writes these; `organization_symbol_drafts.
    upload_draft_asset` computes a SHA-256 for every upload. The Rupert path
    writes no `assets` key at all, which is why this returns None there.
    """
    if revision is None:
        return None
    payload = revision.payload_json if isinstance(revision.payload_json, dict) else {}
    digest: str | None = None
    for asset in payload.get("assets") or []:
        if not isinstance(asset, dict):
            continue
        value = str(asset.get("sha256") or "").strip().lower()
        if len(value) == 64 and all(char in "0123456789abcdef" for char in value):
            digest = value
    return digest


def _final_asset_hash(session: Session, revision: SymbolRevision | None, revision_id: uuid.UUID):
    """Section 9.2's "final stored asset hash", from wherever it is recorded.

    Three places hold one, in descending order of authority:

    1. the last step of SM-P0-06's `asset_transformations` chain, whose
       `derived_asset_sha256` is by definition the asset SymGov ended up with;
    2. `payload_json["assets"][].sha256`, which the organisation draft path
       writes at upload;
    3. an `attachments` row parented on the revision, the shape
       `runtime.py` uses -- `sha256` there is nullable, so it is last.
    """
    lineage = trace_asset_lineage(session, revision_id)
    if lineage:
        digest = lineage[-1].get("derived_asset_sha256")
        if digest:
            return str(digest), "asset_transformation"

    digest = _asset_hashes_from_payload(revision)
    if digest:
        return digest, "revision_payload_asset"

    row = session.execute(
        select(Attachment.sha256)
        .where(
            Attachment.parent_type == "symbol_revision",
            Attachment.parent_id == revision_id,
            Attachment.sha256.is_not(None),
        )
        .order_by(Attachment.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row:
        return str(row), "attachment"
    return None, None


def collect_publication_gate_facts(
    session: Session, symbol_revision_id: uuid.UUID
) -> PublicationGateFacts:
    """Read every governed row section 9.2's six dimensions depend on.

    The only part of this module that touches a database. Every service that
    already owns one of these reads is called rather than reimplemented:
    `symbol_semantic_assignments`, `classification_assignments`,
    `standard_sources`, `rights_provenance` and `source_package_acquisition`.
    """
    revision = session.get(SymbolRevision, symbol_revision_id)

    package_rows = session.execute(
        select(SourcePackageEntry, SourcePackage)
        .join(SourcePackage, SourcePackage.id == SourcePackageEntry.source_package_id)
        .where(SourcePackageEntry.symbol_revision_id == symbol_revision_id)
        .order_by(SourcePackage.package_code)
    ).all()

    source_package_ids: list[uuid.UUID] = []
    gated_package_ids: list[uuid.UUID] = []
    package_rights: list[tuple[uuid.UUID, tuple[str, str] | None]] = []
    source_locator_recorded = False
    package_release_identified = False
    source_asset_hash_recorded = False
    for entry, package in package_rows:
        source_package_ids.append(package.id)
        if package.package_type in GATED_PACKAGE_TYPES:
            gated_package_ids.append(package.id)
        approved = approved_rights_record(session, source_package_id=package.id)
        package_rights.append(
            (package.id, (approved.rights_status, approved.disposition) if approved else None)
        )
        if entry.provider_entry_identifier or entry.source_path:
            source_locator_recorded = True
        if entry.original_asset_sha256:
            source_asset_hash_recorded = True
        if package.release_version or package.provider_package_identifier or package.package_sha256:
            package_release_identified = True

    links = list_symbol_standard_links(session, symbol_revision_id)
    verified_relationship_types: list[str] = []
    live_relationship_types: list[str] = []
    edition_rights: list[tuple[uuid.UUID, tuple[str, str] | None]] = []
    seen_editions: set[uuid.UUID] = set()
    for link in links:
        if link.relationship_type not in SOURCE_RELATIONSHIP_TYPES:
            continue
        if link.assertion_status == "verified":
            verified_relationship_types.append(link.relationship_type)
        if link.assertion_status in {"proposed", "verified"}:
            live_relationship_types.append(link.relationship_type)
        if link.source_symbol_identifier or link.figure_reference or link.table_reference:
            source_locator_recorded = True
        if link.source_asset_sha256:
            source_asset_hash_recorded = True
        if link.standard_version_id not in seen_editions:
            seen_editions.add(link.standard_version_id)
            approved = approved_rights_record(session, standard_version_id=link.standard_version_id)
            edition_rights.append(
                (
                    link.standard_version_id,
                    (approved.rights_status, approved.disposition) if approved else None,
                )
            )

    revision_record = approved_rights_record(session, symbol_revision_id=symbol_revision_id)
    classifications = list_symbol_revision_classifications(session, symbol_revision_id)
    semantic_assignments = list_symbol_semantic_assignments(session, symbol_revision_id)
    final_hash, final_hash_source = _final_asset_hash(session, revision, symbol_revision_id)
    external_total, external_verified = _external_mapping_counts(session, symbol_revision_id)

    return PublicationGateFacts(
        symbol_revision_id=symbol_revision_id,
        gated_package_ids=tuple(gated_package_ids),
        source_package_ids=tuple(source_package_ids),
        source_locator_recorded=source_locator_recorded,
        package_release_identified=package_release_identified,
        verified_primary_concept=verified_primary_assignment(session, symbol_revision_id) is not None,
        live_semantic_assignment_count=sum(
            1 for row in semantic_assignments if row.status in _LIVE_ASSIGNMENT_STATUSES
        ),
        waived_dimensions=approved_gate_exceptions(session, symbol_revision_id),
        verified_relationship_types=tuple(verified_relationship_types),
        live_relationship_types=tuple(live_relationship_types),
        revision_rights=(
            (revision_record.rights_status, revision_record.disposition) if revision_record else None
        ),
        package_rights=tuple(package_rights),
        edition_rights=tuple(edition_rights),
        final_asset_sha256=final_hash,
        final_asset_hash_source=final_hash_source,
        source_asset_hash_recorded=source_asset_hash_recorded,
        lineage_reaches_source_package=lineage_reaches_source_package(session, symbol_revision_id),
        live_classification_count=sum(
            1 for row in classifications if row.status in _LIVE_ASSIGNMENT_STATUSES
        ),
        verified_classification_count=sum(1 for row in classifications if row.status == "verified"),
        external_mapping_count=external_total,
        verified_external_mapping_count=external_verified,
    )


def _external_mapping_counts(session: Session, symbol_revision_id: uuid.UUID) -> tuple[int, int]:
    """Section 13.2's T4 "verified external semantic mapping where applicable".

    An external mapping belongs to a *concept*, not to a revision, so this
    reaches them through the revision's semantic assignments. There are none
    in production -- nothing creates a `SemanticConcept` -- so this is zero
    for every symbol today, which is exactly why T4 is unreachable without
    one.
    """
    from .models import ConceptExternalReference, SymbolSemanticAssignment

    rows = session.execute(
        select(ConceptExternalReference.mapping_status)
        .join(
            SymbolSemanticAssignment,
            SymbolSemanticAssignment.semantic_concept_id
            == ConceptExternalReference.semantic_concept_id,
        )
        .where(
            SymbolSemanticAssignment.symbol_revision_id == symbol_revision_id,
            SymbolSemanticAssignment.status.in_(sorted(_LIVE_ASSIGNMENT_STATUSES)),
        )
    ).scalars().all()
    return len(rows), sum(1 for status in rows if status == "verified")


# --------------------------------------------------------------------------
# Section 9.2's exception.
# --------------------------------------------------------------------------


def approved_gate_exceptions(session: Session, symbol_revision_id: uuid.UUID) -> frozenset[str]:
    """The dimensions an approved waiver excuses for this revision."""
    rows = session.execute(
        select(PublicationGateException.dimension).where(
            PublicationGateException.symbol_revision_id == symbol_revision_id,
            PublicationGateException.decision_status == "approved",
        )
    ).scalars().all()
    return frozenset(rows)


def propose_publication_gate_exception(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    dimension: str,
    proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None = None,
    evidence: object = None,
    approval_reason: object = None,
) -> PublicationGateException:
    """Propose a waiver of one section 9.2 dimension for one revision.

    Only `WAIVABLE_DIMENSIONS` may be proposed. Section 9.2 offers an
    exception for semantic identity and for nothing else, and section 16.2
    rules out publishing an authoritative-source symbol with unresolved
    rights, so a `rights` waiver is refused here rather than in a check
    constraint -- the vocabulary is storage, the policy is service, exactly as
    `rights_provenance.disposition_is_permitted` is.

    Every waiver starts `proposed`. Principle P-07 and section 8.4 keep an
    assertion out of the governed record until a decision is taken, and
    section 9.2's word for this exception is "approved".
    """
    if dimension not in GATE_DIMENSIONS:
        raise ValueError("invalid publication gate dimension")
    if dimension not in WAIVABLE_DIMENSIONS:
        raise ValueError(
            f"the {dimension} dimension of the section 9.2 publication gate cannot be waived"
        )
    _require_aware_timestamp(proposed_at, "gate exception proposal time")
    _require_optional_actor(proposed_by_user_id, "gate exception proposer")
    if not isinstance(symbol_revision_id, uuid.UUID) or symbol_revision_id.int == 0:
        raise ValueError("symbol_revision_id must be a real UUID")

    record = PublicationGateException(
        id=uuid.uuid4(),
        symbol_revision_id=symbol_revision_id,
        dimension=dimension,
        decision_status="proposed",
        approved_by_user_id=None,
        approved_at=None,
        approval_reason=_normalize_approval_reason(approval_reason),
        evidence_json=_normalize_evidence(evidence),
        proposed_by_user_id=proposed_by_user_id,
        created_at=proposed_at,
        updated_at=proposed_at,
    )
    session.add(record)
    return record


def transition_publication_gate_exception(
    session: Session,
    exception_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    approved_by_user_id: uuid.UUID | None = None,
    approval_reason: object = None,
) -> PublicationGateException:
    """Take a decision on one waiver.

    Approving requires a named approver and a recorded reason: section 9.2's
    exception is "for non-engineering/annotation symbols", which is a
    judgement about this symbol, and a waiver that does not say why is not an
    explicit exception. This is the same triple section 7.12 demands of a
    rights approval and `transition_rights_record` enforces.

    Approving retires whichever waiver was approved before it for the same
    revision and dimension, so the one-approved-waiver rule is upheld by
    succession rather than by refusing the new decision -- the shape SM-P0-01
    through -06 all use.
    """
    if target_status not in GATE_EXCEPTION_STATUSES:
        raise ValueError("invalid gate exception status")
    _require_aware_timestamp(occurred_at, "gate exception decision time")
    _require_optional_actor(approved_by_user_id, "gate exception approver")
    normalized_reason = _normalize_approval_reason(approval_reason)

    record = session.get(PublicationGateException, exception_id, with_for_update=True)
    if record is None:
        raise LookupError(f"publication gate exception not found: {exception_id}")

    current_status = record.decision_status
    if current_status not in GATE_EXCEPTION_TRANSITIONS:
        raise ValueError(f"gate exception carries an unrecognised status: {current_status}")
    if target_status not in GATE_EXCEPTION_TRANSITIONS[current_status]:
        raise ValueError(f"a gate exception cannot move from {current_status} to {target_status}")

    if target_status == "approved":
        if record.dimension not in WAIVABLE_DIMENSIONS:
            raise ValueError(
                f"the {record.dimension} dimension of the section 9.2 publication gate "
                "cannot be waived"
            )
        if approved_by_user_id is None:
            raise ValueError("approving a publication gate exception requires a named approver")
        if normalized_reason is None and record.approval_reason is None:
            raise ValueError("approving a publication gate exception requires a recorded reason")
        _retire_superseded_exception(session, record)
    elif target_status == "rejected":
        if approved_by_user_id is None:
            raise ValueError("rejecting a publication gate exception requires a named decider")

    if target_status in {"approved", "rejected"}:
        record.approved_by_user_id = approved_by_user_id
        record.approved_at = occurred_at
    elif approved_by_user_id is not None:
        raise ValueError("a decider only applies to approving or rejecting a gate exception")

    if normalized_reason is not None:
        record.approval_reason = normalized_reason
    record.decision_status = target_status
    record.updated_at = occurred_at
    return record


def _retire_superseded_exception(session: Session, record: PublicationGateException) -> None:
    superseded = session.execute(
        select(PublicationGateException)
        .where(
            PublicationGateException.symbol_revision_id == record.symbol_revision_id,
            PublicationGateException.dimension == record.dimension,
            PublicationGateException.decision_status == "approved",
            PublicationGateException.id != record.id,
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


# --------------------------------------------------------------------------
# Recording, and the one entry point the publication paths call.
# --------------------------------------------------------------------------


def record_publication_gate_evaluation(
    session: Session,
    *,
    decision: PublicationGateDecision,
    evaluated_at: datetime,
    evaluated_by_user_id: uuid.UUID | None = None,
) -> PublicationGateEvaluation:
    """Persist one evaluation. Section 14.4 keeps it with the governed data."""
    _require_aware_timestamp(evaluated_at, "publication gate evaluation time")
    _require_optional_actor(evaluated_by_user_id, "publication gate evaluator")
    if decision.outcome not in GATE_OUTCOMES:
        raise ValueError("invalid publication gate outcome")

    row = PublicationGateEvaluation(
        id=uuid.uuid4(),
        symbol_revision_id=decision.symbol_revision_id,
        source_package_id=decision.source_package_id,
        in_scope=decision.in_scope,
        outcome=decision.outcome,
        traceability_level=decision.traceability_level,
        dimension_results_json=[result.as_report() for result in decision.dimensions],
        # A grandfathered revision keeps the reasons it would have failed on:
        # `refusal_names_a_reason` constrains only `refused`, and section
        # 12.1's M6 asks for the gaps to be reported rather than hidden.
        refusal_reasons_json=list(decision.refusal_reasons),
        policy_version=decision.policy_version,
        evaluated_at=evaluated_at,
        evaluated_by_user_id=evaluated_by_user_id,
    )
    session.add(row)
    session.flush()
    return row


def enforce_publication_gate(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    evaluated_at: datetime,
    evaluated_by_user_id: uuid.UUID | None = None,
) -> PublicationGateDecision:
    """Evaluate section 9.2's gate for one revision and record the result.

    Returns the decision rather than raising. The two publication paths refuse
    in different ways -- `organization_promotion_handoff` marks its review
    action failed and returns a detail string, `runtime.persist_publication_`
    `execution` raises inside its session scope -- and each keeps the shape it
    already had for every other refusal.

    Recording happens whatever the outcome, including `not_in_scope`. That is
    the point: section 12.1's M6 grandfathers existing data "with traceability
    gaps reported", and a gap nobody wrote down is not reported.
    """
    facts = collect_publication_gate_facts(session, symbol_revision_id)
    decision = evaluate_publication_gate(facts)
    record_publication_gate_evaluation(
        session,
        decision=decision,
        evaluated_at=evaluated_at,
        evaluated_by_user_id=evaluated_by_user_id,
    )
    return decision


# --------------------------------------------------------------------------
# Making the intake rights assessment durable as a proposal.
# --------------------------------------------------------------------------


def resolve_intake_rights_assessment(
    session: Session,
    *,
    provenance: ProvenanceAssessment | None,
    intake_record_id: uuid.UUID | None,
) -> ProvenanceAssessment | None:
    """Find the rights assessment for one intake, if the caller has not.

    `publication_handoff.load_review_context` loads a `ProvenanceAssessment`
    only when the review case is rooted on one. The ordinary path is rooted on
    a `ValidationReport`, so on that path the context's
    `provenance_assessment` is always None even though an assessment for the
    same intake record usually exists -- which is also why
    `payload_json["lineage"]["provenance_assessment_id"]` is None for those
    revisions.

    Looked up here rather than by widening `load_review_context`, which nine
    dependent test files pin and which SM-P0-09 reserves. The lookup is the
    one `ix_provenance_assessments_intake_assessed_at` already indexes, and
    the latest assessment wins because a re-assessment supersedes its
    predecessor.
    """
    if provenance is not None:
        return provenance
    if intake_record_id is None:
        return None
    return session.execute(
        select(ProvenanceAssessment)
        .where(ProvenanceAssessment.intake_record_id == intake_record_id)
        .order_by(ProvenanceAssessment.assessed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def propose_intake_rights_record(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    provenance: ProvenanceAssessment | None,
    proposed_at: datetime,
    proposed_by_user_id: uuid.UUID | None = None,
    intake_record_id: uuid.UUID | None = None,
    context: dict | None = None,
):
    """Carry one intake rights assessment into a durable proposed record.

    Section 9.2's rights dimension needs an *approved* `RightsRecord` and
    nothing in production writes any record at all, so without this the
    dimension could never pass for any symbol and the reviewer who must
    resolve it starts from a blank page. What SM-P0-06 established is that
    `provenance_assessments` cannot *be* the durable record: it is
    intake-scoped on both NOT NULL keys, and its vocabulary is not section
    7.12's. What it can do is seed one.

    Three properties, each deliberate:

    **The proposal asserts nothing about a licence.** `rights_status` is
    always `unknown`. An intake assessment never reads terms, so it cannot
    establish `open` or `licensed`, and `INTAKE_RIGHTS_DISPOSITION_PROPOSALS`
    maps only the *disposition*.

    **It can never be approved as it stands.** `determination_method` is
    `ai_assisted`, so `rights_records`' `approved_not_ai_determined`
    constraint refuses it, and section 8.4 says why: no AI determination
    approves itself. A reviewer proposes their own record. This one is
    evidence, and durable evidence is what was missing.

    **It is written once.** A revision that already carries any rights record
    is left alone, so re-running a promotion neither duplicates the proposal
    nor overwrites a reviewer's work.

    Returns the record it created, or None when there was nothing to carry or
    a record already exists.
    """
    from .models import RightsRecord

    provenance = resolve_intake_rights_assessment(
        session, provenance=provenance, intake_record_id=intake_record_id
    )
    if provenance is None:
        return None
    disposition = INTAKE_RIGHTS_DISPOSITION_PROPOSALS.get(
        str(provenance.rights_disposition or "").strip().lower()
    )
    if disposition is None:
        return None

    existing = session.execute(
        select(RightsRecord.id).where(RightsRecord.symbol_revision_id == symbol_revision_id).limit(1)
    ).first()
    if existing is not None:
        return None

    return propose_rights_record(
        session,
        disposition=disposition,
        determination_method=INTAKE_RIGHTS_DETERMINATION_METHOD,
        proposed_at=proposed_at,
        rights_status="unknown",
        symbol_revision_id=symbol_revision_id,
        proposed_by_user_id=proposed_by_user_id,
        evidence={
            "provenance_assessment_id": str(provenance.id),
            "intake_rights_disposition": provenance.rights_disposition,
            # The ungoverned column, carried verbatim as evidence and mapped
            # from nowhere: `provenance_assessments.rights_status` has no
            # check constraint and is written straight from an agent payload.
            "intake_rights_status": provenance.rights_status,
            "intake_risk_level": provenance.risk_level,
            "policy_version": PUBLICATION_GATE_POLICY_VERSION,
            **(context or {}),
        },
    )
