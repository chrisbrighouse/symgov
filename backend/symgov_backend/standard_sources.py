"""Standards, standard releases and governed source assertions (SM-P0-05).

Specification section 7.10. `standards`, `standard_versions` and
`symbol_standard_links` have existed since 20260409_0001 with no writer
anywhere in the repository, so this module is their first service layer as
well as the place SM-P0-05's new precision fields are validated.

Two rules shape it.

Section 7.10's purpose is to *distinguish a precise normative graphical source
from a looser reference*. That is why `relationship_type` is now section 8.3's
eight values rather than free text, and why a verified assertion must say what
verified it.

Section 8.4: an AI-assisted assertion is a proposal. `ai_assisted` is
deliberately absent from the methods that may be verified without a named
reviewer, however specific the evidence.

Two things this module does *not* do. It stores no rights disposition --
section 7.12 is SM-P0-06, and the forward reference lives on
`source_packages.licence_reference`. And it declares no database-level status
vocabulary for `standards` or `standard_versions`: section 7.10 says to
preserve those two entities and names no vocabulary for them, so
`STANDARD_STATUSES` below is service policy and the tests pin it here rather
than in a check constraint.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Standard, StandardVersion, SymbolStandardLink

# Section 8.3, in full, and in the order the specification tabulates them.
# The first two are the normative pair; the rest describe weaker or explicitly
# non-authoritative relationships.
SOURCE_RELATIONSHIP_TYPES = frozenset(
    {
        "normative_definition",
        "normative_equivalent",
        "informative_example",
        "vendor_implementation",
        "owner_variant",
        "project_deviation",
        "derived_from",
        "comparison_only",
    }
)

# Section 9.2's publication gate asks that graphical authority be "explicitly
# asserted; no ambiguous 'standard-associated' label". These are the two
# section 8.3 describes as formal. Policy, not a constraint: the gate itself
# is SM-P0-09's dual-write and publication work, and a symbol may legitimately
# carry only a `derived_from` assertion until then.
AUTHORITATIVE_RELATIONSHIP_TYPES = frozenset({"normative_definition", "normative_equivalent"})

STANDARD_ASSERTION_STATUSES = frozenset({"proposed", "verified", "rejected", "retired"})

# The fourth `method` vocabulary in the semantic model, and deliberately not
# the same as `symbol_semantic_assignments.method` (`source_mapping`),
# `concept_external_references.mapping_method` (`imported`) or the
# classification assignments' (`legacy_backfill`). Section 7.10 names
# `import_manifest` and `source_api` because *how* a source assertion was
# obtained -- from a provider's shipped manifest or from its live API -- is a
# different question from how a meaning was mapped. Unifying them is a
# specification change, not an implementation tidy-up.
STANDARD_VERIFICATION_METHODS = frozenset({"manual", "import_manifest", "source_api", "ai_assisted"})

# A rejected assertion is terminal: a reviewer who changes their mind asserts
# afresh rather than reopening the old record. The same shape SM-P0-02, -03
# and -04 use.
STANDARD_ASSERTION_TRANSITIONS: dict[str, frozenset[str]] = {
    "proposed": frozenset({"verified", "rejected", "retired"}),
    "verified": frozenset({"retired"}),
    "rejected": frozenset(),
    "retired": frozenset(),
}

# Section 7.10 allows "human or controlled-system verification". These two are
# the controlled-system half: both read an authoritative artifact the provider
# published, so both are reproducible without a named reviewer. `manual` needs
# a person by definition, and `ai_assisted` is excluded by section 8.4.
AUTO_VERIFIABLE_METHODS = frozenset({"import_manifest", "source_api"})

# Statuses in which a standard or release accepts no new assertions. Service
# policy; see the module docstring for why it is not a check constraint.
STANDARD_STATUSES = frozenset({"active", "deprecated", "withdrawn"})
CLOSED_STANDARD_STATUSES = frozenset({"withdrawn"})

STANDARD_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"deprecated", "withdrawn"}),
    "deprecated": frozenset({"active", "withdrawn"}),
    "withdrawn": frozenset(),
}

# Governance states that keep an assertion live, and therefore inside the
# `uq_symbol_standard_links_active_assertion` partial unique index.
LIVE_ASSERTION_STATUSES = frozenset({"proposed", "verified"})

# `+` is BSI amendment notation -- `BS 7608+A1` means "incorporating
# Amendment 1" -- and appears in the CFIHOS register, so it is part of the
# code rather than decoration. `:` appears because some issuing bodies bake
# the edition into the code; splitting that out is the caller's job.
STANDARD_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9./:+ -]{0,62}[A-Z0-9]$")

# Sections 7.10 and 7.11 name SHA-256 specifically, so this is a fixed
# 64-character grammar rather than SM-P0-03's algorithm-paired
# `^[0-9a-f]{32,128}$`. No algorithm column exists or is wanted: pairing one
# here would admit an MD5 digest into a column the specification says is
# SHA-256.
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

STANDARD_CODE_MAX_LENGTH = 64
TITLE_MAX_LENGTH = 256
ISSUING_BODY_MAX_LENGTH = 256
VERSION_LABEL_MAX_LENGTH = 128
PROVIDER_IDENTIFIER_MAX_LENGTH = 512
SOURCE_SYMBOL_IDENTIFIER_MAX_LENGTH = 512
CLAUSE_REFERENCE_MAX_LENGTH = 256
FIGURE_REFERENCE_MAX_LENGTH = 256
TABLE_REFERENCE_MAX_LENGTH = 256
URI_MAX_LENGTH = 1024


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


def normalize_standard_code(value: object) -> str:
    """Return a standard code in canonical form.

    Uppercased so `api spec 6d` and `API Spec 6D` cannot become two standards.
    Spaces, dots, slashes and colons are permitted because issuing bodies use
    them in the codes themselves -- `ANSI/ISA-75.05.01`, `API Spec 6D`.
    """
    code = _normalize_required_text(value, "standard code", STANDARD_CODE_MAX_LENGTH)
    # Collapse whitespace *before* the ASCII test. A non-breaking space is
    # both non-ASCII and whitespace, and reporting it as "must contain ASCII
    # characters only" describes the wrong defect -- it is separator noise
    # from a spreadsheet export, not a character the code really carries.
    normalized_code = " ".join(code.upper().split())
    if not normalized_code.isascii():
        raise ValueError("standard code must contain ASCII characters only")
    if not STANDARD_CODE_PATTERN.match(normalized_code):
        raise ValueError(f"standard code does not match the required grammar: {value!r}")
    return normalized_code


def normalize_source_uri(value: object, label: str = "source URI") -> str | None:
    """Return an http(s) locator, or None.

    Section 7.10 calls this an "authoritative lookup or licensed-source
    locator". It is deliberately restricted to http(s), matching
    `external_semantic_schemes.normalize_external_scheme_uri`: a locator
    nobody can dereference records less than nothing, and the in-package path
    of a licensed artifact belongs on `SourcePackageEntry.source_path`.
    """
    if value is None:
        return None
    uri = _normalize_required_text(value, label, URI_MAX_LENGTH)
    if not uri.startswith(("http://", "https://")):
        raise ValueError(f"{label} must be an http or https URI")
    return uri


def normalize_sha256(value: object, label: str) -> str | None:
    """Return a lowercase SHA-256 digest, or None."""
    if value is None:
        return None
    digest = _normalize_required_text(value, label, 64).lower()
    if not SHA256_PATTERN.match(digest):
        raise ValueError(f"{label} must be 64 lowercase hexadecimal characters")
    return digest


def normalize_source_symbol_identifier(value: object) -> str | None:
    """Return the standard's own symbol number, case preserved.

    Case is not folded: the standard is the authority on its own identifiers,
    and `A-123a` and `A-123A` may be two different symbols.
    """
    return _normalize_optional_text(
        value, "source symbol identifier", SOURCE_SYMBOL_IDENTIFIER_MAX_LENGTH
    )


def normalize_assertion_evidence(value: object) -> dict:
    """Return the evidence object, which must be a JSON object when present."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("source assertion evidence must be an object")
    return value


def register_standard(
    session: Session,
    *,
    standard_code: str,
    title: str,
    registered_at: datetime,
    issuing_body: object = None,
    status: str = "active",
    standard_id: uuid.UUID | None = None,
) -> Standard:
    """Register a standard, independent of any edition of it.

    The edition belongs on `StandardVersion`. Providers routinely bake it into
    the code string -- CFIHOS ships `API Spec 6D:2014` as one field -- and
    section 7.10 wants that split, because a symbol defined by the 2014
    edition is not thereby defined by the 2021 one.
    """
    if status not in STANDARD_STATUSES:
        raise ValueError("invalid standard status")
    normalized_code = normalize_standard_code(standard_code)
    normalized_title = _normalize_required_text(title, "standard title", TITLE_MAX_LENGTH)
    normalized_body = _normalize_optional_text(issuing_body, "standard issuing body", ISSUING_BODY_MAX_LENGTH)
    _require_aware_timestamp(registered_at, "standard registration time")

    standard = Standard(
        id=standard_id or uuid.uuid4(),
        standard_code=normalized_code,
        title=normalized_title,
        issuing_body=normalized_body,
        status=status,
        created_at=registered_at,
        updated_at=registered_at,
    )
    session.add(standard)
    return standard


def register_standard_version(
    session: Session,
    *,
    standard_id: uuid.UUID,
    version_label: str,
    registered_at: datetime,
    effective_date: date | None = None,
    status: str = "active",
    version_id: uuid.UUID | None = None,
    provider_identifier: object = None,
) -> StandardVersion:
    """Register one edition of a standard.

    `provider_identifier` is the identifier a reference-data provider gives
    this edition in its own register -- CFIHOS numbers `API Spec 6D:2014` as
    `CFIHOS-90000008`. It belongs on the edition rather than the standard
    because that is what such a register enumerates: CFIHOS carries two rows
    for API Spec 17D, one per edition.
    """
    if status not in STANDARD_STATUSES:
        raise ValueError("invalid standard version status")
    normalized_label = _normalize_required_text(
        version_label, "standard version label", VERSION_LABEL_MAX_LENGTH
    )
    normalized_provider_identifier = _normalize_optional_text(
        provider_identifier, "standard version provider identifier", PROVIDER_IDENTIFIER_MAX_LENGTH
    )
    _require_aware_timestamp(registered_at, "standard version registration time")
    if effective_date is not None and not isinstance(effective_date, date):
        raise ValueError("standard version effective date must be a date")

    standard = session.get(Standard, standard_id)
    if standard is None:
        raise LookupError(f"standard not found: {standard_id}")
    if standard.status in CLOSED_STANDARD_STATUSES:
        raise ValueError(f"standard is {standard.status} and accepts no new versions")

    version = StandardVersion(
        id=version_id or uuid.uuid4(),
        standard_id=standard_id,
        version_label=normalized_label,
        effective_date=effective_date,
        status=status,
        provider_identifier=normalized_provider_identifier,
        created_at=registered_at,
        updated_at=registered_at,
    )
    session.add(version)
    return version


def set_standard_status(
    session: Session, standard_id: uuid.UUID, *, target_status: str, occurred_at: datetime
) -> Standard:
    """Move a standard between active, deprecated and withdrawn."""
    standard = session.get(Standard, standard_id)
    if standard is None:
        raise LookupError(f"standard not found: {standard_id}")
    _apply_standard_status(standard, target_status=target_status, occurred_at=occurred_at, label="standard")
    return standard


def set_standard_version_status(
    session: Session, version_id: uuid.UUID, *, target_status: str, occurred_at: datetime
) -> StandardVersion:
    """Move one edition between active, deprecated and withdrawn.

    Deprecating an edition leaves its assertions alone. A verified assertion
    records what that edition said, and a later edition does not make it
    untrue.
    """
    version = session.get(StandardVersion, version_id)
    if version is None:
        raise LookupError(f"standard version not found: {version_id}")
    _apply_standard_status(version, target_status=target_status, occurred_at=occurred_at, label="standard version")
    return version


def _apply_standard_status(
    row: Standard | StandardVersion, *, target_status: str, occurred_at: datetime, label: str
) -> None:
    if target_status not in STANDARD_STATUSES:
        raise ValueError(f"invalid {label} status")
    _require_aware_timestamp(occurred_at, f"{label} status change time")
    current_status = row.status
    if current_status not in STANDARD_STATUS_TRANSITIONS:
        raise ValueError(f"{label} carries an unrecognised status: {current_status}")
    if target_status not in STANDARD_STATUS_TRANSITIONS[current_status]:
        raise ValueError(f"{label} cannot move from {current_status} to {target_status}")
    row.status = target_status
    row.updated_at = occurred_at


def assert_symbol_standard_link(
    session: Session,
    *,
    symbol_revision_id: uuid.UUID,
    standard_version_id: uuid.UUID,
    relationship_type: str,
    asserted_at: datetime,
    source_symbol_identifier: object = None,
    clause_reference: object = None,
    figure_reference: object = None,
    table_reference: object = None,
    source_uri: object = None,
    source_asset_sha256: object = None,
    notes: object = None,
    evidence: object = None,
) -> SymbolStandardLink:
    """Record a proposed relationship between a symbol revision and an edition.

    Every assertion starts as `proposed`, whatever produced it: principle
    P-07 keeps machine proposals out of the public record until a review
    decision is taken. `verification_method` is left NULL here for the same
    reason -- it records how a verification happened, and no verification has.

    A `source_asset_sha256` requires a `source_uri`. A hash of an artifact
    with no record of where the artifact came from cannot be re-verified,
    which is the only reason to store it; the database enforces this too.
    """
    if relationship_type not in SOURCE_RELATIONSHIP_TYPES:
        raise ValueError("invalid source relationship type")
    _require_aware_timestamp(asserted_at, "source assertion time")
    normalized_identifier = normalize_source_symbol_identifier(source_symbol_identifier)
    normalized_clause = _normalize_optional_text(clause_reference, "clause reference", CLAUSE_REFERENCE_MAX_LENGTH)
    normalized_figure = _normalize_optional_text(figure_reference, "figure reference", FIGURE_REFERENCE_MAX_LENGTH)
    normalized_table = _normalize_optional_text(table_reference, "table reference", TABLE_REFERENCE_MAX_LENGTH)
    normalized_uri = normalize_source_uri(source_uri)
    normalized_hash = normalize_sha256(source_asset_sha256, "source asset hash")
    normalized_notes = _normalize_optional_text(notes, "source assertion notes", 2000)
    normalized_evidence = normalize_assertion_evidence(evidence)

    if normalized_hash is not None and normalized_uri is None:
        raise ValueError("a source asset hash must record where the asset was obtained")

    version = session.get(StandardVersion, standard_version_id)
    if version is None:
        raise LookupError(f"standard version not found: {standard_version_id}")
    if version.status in CLOSED_STANDARD_STATUSES:
        raise ValueError(f"standard version is {version.status} and accepts no new assertions")

    link = SymbolStandardLink(
        id=uuid.uuid4(),
        symbol_revision_id=symbol_revision_id,
        standard_version_id=standard_version_id,
        relationship_type=relationship_type,
        clause_reference=normalized_clause,
        notes=normalized_notes,
        created_at=asserted_at,
        source_symbol_identifier=normalized_identifier,
        figure_reference=normalized_figure,
        table_reference=normalized_table,
        source_uri=normalized_uri,
        assertion_status="proposed",
        verification_method=None,
        source_asset_sha256=normalized_hash,
        verified_by_user_id=None,
        verified_at=None,
        evidence_json=normalized_evidence,
    )
    session.add(link)
    return link


def transition_symbol_standard_link(
    session: Session,
    link_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    verification_method: str | None = None,
    verified_by_user_id: uuid.UUID | None = None,
) -> SymbolStandardLink:
    """Take a review decision on one source assertion.

    Verifying requires a `verification_method`, because section 7.10 asks what
    the verification relied on and a bare "verified" answers nothing. A named
    reviewer is required unless the method is one of the two controlled-system
    methods, which read an artifact the provider published and are therefore
    reproducible; `ai_assisted` is excluded by section 8.4 however confident
    the proposal.

    Verifying a `normative_definition` retires whichever normative definition
    was verified before it for the same revision and edition, so the
    one-definition rule is upheld by succession rather than by refusing the
    new decision -- the pattern SM-P0-02 through -04 all use.

    Only a verification records an actor and a timestamp on the row. Section
    7.10's field list gives this table `verified_by`/`verified_at` and no
    reviewer pair, so a rejection or retirement changes the status and leaves
    no actor behind; section 14.4's governance decision history is where that
    belongs, not a column invented here.
    """
    if target_status not in STANDARD_ASSERTION_STATUSES:
        raise ValueError("invalid source assertion status")
    _require_aware_timestamp(occurred_at, "source assertion decision time")
    _require_optional_actor(verified_by_user_id, "source assertion verifier")

    link = session.get(SymbolStandardLink, link_id, with_for_update=True)
    if link is None:
        raise LookupError(f"source assertion not found: {link_id}")

    current_status = link.assertion_status
    if current_status not in STANDARD_ASSERTION_TRANSITIONS:
        raise ValueError(f"source assertion carries an unrecognised status: {current_status}")
    if target_status not in STANDARD_ASSERTION_TRANSITIONS[current_status]:
        raise ValueError(f"source assertion cannot move from {current_status} to {target_status}")

    if target_status == "verified":
        _check_verification(session, link, verification_method, verified_by_user_id)
        _retire_superseded_definition(session, link, occurred_at)
        link.verification_method = verification_method
        link.verified_by_user_id = verified_by_user_id
        link.verified_at = occurred_at
    elif verification_method is not None or verified_by_user_id is not None:
        raise ValueError("a verification method and verifier only apply to verifying an assertion")

    link.assertion_status = target_status
    return link


def _check_verification(
    session: Session,
    link: SymbolStandardLink,
    verification_method: str | None,
    verified_by_user_id: uuid.UUID | None,
) -> None:
    if verification_method not in STANDARD_VERIFICATION_METHODS:
        raise ValueError("verifying a source assertion requires a valid verification method")

    if verified_by_user_id is None and verification_method not in AUTO_VERIFIABLE_METHODS:
        raise ValueError(
            f"verifying a {verification_method} source assertion requires a named verifier"
        )

    # Section 7.10's whole purpose. A normative assertion that cannot say
    # which symbol of the standard it means is exactly the ambiguous
    # "standard-associated" label section 9.2 rules out for publication.
    if (
        link.relationship_type in AUTHORITATIVE_RELATIONSHIP_TYPES
        and link.source_symbol_identifier is None
        and link.figure_reference is None
        and link.table_reference is None
        and link.clause_reference is None
    ):
        raise ValueError(
            "a normative source assertion cannot be verified without a symbol identifier, "
            "figure, table or clause reference"
        )

    version = session.get(StandardVersion, link.standard_version_id)
    if version is not None and version.status in CLOSED_STANDARD_STATUSES:
        raise ValueError(
            f"standard version is {version.status} and its assertions cannot be verified"
        )


def _retire_superseded_definition(
    session: Session, link: SymbolStandardLink, occurred_at: datetime
) -> None:
    if link.relationship_type != "normative_definition":
        return
    superseded = session.execute(
        select(SymbolStandardLink)
        .where(
            SymbolStandardLink.symbol_revision_id == link.symbol_revision_id,
            SymbolStandardLink.standard_version_id == link.standard_version_id,
            SymbolStandardLink.relationship_type == "normative_definition",
            SymbolStandardLink.assertion_status == "verified",
            SymbolStandardLink.id != link.id,
        )
        .with_for_update()
    ).scalars().all()
    for previous in superseded:
        previous.assertion_status = "retired"
    if superseded:
        # The partial unique index is evaluated per statement, so the
        # retirement must reach the database before the successor claims it.
        session.flush()


def verified_normative_definition(
    session: Session, symbol_revision_id: uuid.UUID, standard_version_id: uuid.UUID
) -> SymbolStandardLink | None:
    """Return the edition's verified normative definition of a symbol, if any."""
    return session.execute(
        select(SymbolStandardLink).where(
            SymbolStandardLink.symbol_revision_id == symbol_revision_id,
            SymbolStandardLink.standard_version_id == standard_version_id,
            SymbolStandardLink.relationship_type == "normative_definition",
            SymbolStandardLink.assertion_status == "verified",
        )
    ).scalar_one_or_none()


def list_symbol_standard_links(
    session: Session,
    symbol_revision_id: uuid.UUID,
    *,
    assertion_status: str | None = None,
    relationship_type: str | None = None,
) -> list[SymbolStandardLink]:
    """List a revision's source assertions, most authoritative first."""
    if assertion_status is not None and assertion_status not in STANDARD_ASSERTION_STATUSES:
        raise ValueError("invalid source assertion status filter")
    if relationship_type is not None and relationship_type not in SOURCE_RELATIONSHIP_TYPES:
        raise ValueError("invalid source relationship type filter")

    query = select(SymbolStandardLink).where(
        SymbolStandardLink.symbol_revision_id == symbol_revision_id
    )
    if assertion_status is not None:
        query = query.where(SymbolStandardLink.assertion_status == assertion_status)
    if relationship_type is not None:
        query = query.where(SymbolStandardLink.relationship_type == relationship_type)
    query = query.order_by(
        # The eight section 8.3 names do not sort usefully alphabetically, and
        # a reviewer wants the normative pair at the top.
        SymbolStandardLink.relationship_type.notin_(sorted(AUTHORITATIVE_RELATIONSHIP_TYPES)),
        SymbolStandardLink.created_at,
    )
    return list(session.execute(query).scalars())


def find_links_by_source_symbol_identifier(
    session: Session,
    standard_version_id: uuid.UUID,
    source_symbol_identifier: str,
    *,
    assertion_status: str | None = None,
) -> list[SymbolStandardLink]:
    """Find every symbol revision one standard's own symbol number points at.

    This is the reverse lookup section 14.3 indexes
    `(standard_version_id, source_symbol_identifier)` for. More than one
    revision may legitimately cite the same source symbol -- successive
    revisions of the same SymGov symbol, for one -- so nothing constrains the
    reverse direction.
    """
    if assertion_status is not None and assertion_status not in STANDARD_ASSERTION_STATUSES:
        raise ValueError("invalid source assertion status filter")
    identifier = normalize_source_symbol_identifier(source_symbol_identifier)
    if identifier is None:
        raise ValueError("a source symbol identifier is required")

    query = select(SymbolStandardLink).where(
        SymbolStandardLink.standard_version_id == standard_version_id,
        SymbolStandardLink.source_symbol_identifier == identifier,
    )
    if assertion_status is not None:
        query = query.where(SymbolStandardLink.assertion_status == assertion_status)
    return list(session.execute(query.order_by(SymbolStandardLink.created_at)).scalars())


def get_standard(session: Session, standard_code: str) -> Standard | None:
    """Look a standard up by its code."""
    return session.execute(
        select(Standard).where(Standard.standard_code == normalize_standard_code(standard_code))
    ).scalar_one_or_none()


def list_standard_versions(
    session: Session, standard_id: uuid.UUID, *, status: str | None = None
) -> list[StandardVersion]:
    """List a standard's editions, newest effective date first, undated last."""
    if status is not None and status not in STANDARD_STATUSES:
        raise ValueError("invalid standard version status filter")
    query = select(StandardVersion).where(StandardVersion.standard_id == standard_id)
    if status is not None:
        query = query.where(StandardVersion.status == status)
    query = query.order_by(
        StandardVersion.effective_date.is_(None),
        StandardVersion.effective_date.desc(),
        StandardVersion.version_label,
    )
    return list(session.execute(query).scalars())


def find_standard_version_by_provider_identifier(
    session: Session, provider_identifier: str
) -> StandardVersion | None:
    """Look one edition up by a provider's own identifier for it.

    Case is preserved on storage, so this matches exactly: the provider is the
    authority on its own identifiers.
    """
    identifier = _normalize_optional_text(
        provider_identifier, "standard version provider identifier", PROVIDER_IDENTIFIER_MAX_LENGTH
    )
    if identifier is None:
        raise ValueError("a provider identifier is required")
    return session.execute(
        select(StandardVersion).where(StandardVersion.provider_identifier == identifier)
    ).scalar_one_or_none()
