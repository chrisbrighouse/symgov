"""Acquisition provenance for source packages and their entries (SM-P0-05).

Specification section 7.11: SourcePackage becomes "the durable acquisition/
package envelope for an ingestion batch or authoritative library release".
This module records what was obtained, from where, under which release and
with which hashes, so Appendix B.2's chain -- catalogue symbol -> source
package -> release/version -> exact provider entry/symbol ID -> hashes -- can
actually be walked.

**This module does not create packages for submissions.** `source_packages`
has had a live writer since long before the semantic model:
`runtime.ensure_source_package_for_intake` allocates one package per
submission sheet, and `workspace.py` reads it back. That path is untouched --
SM-P0-09 owns any change to an existing write path. What this module adds is
`record_package_acquisition`, which attaches section 7.11's provenance to a
package however it was created, and `register_source_package` for an
authoritative library release that has no submission behind it.

Rights are not modelled here. Section 7.12 is SM-P0-06, and
`licence_reference` is a structured pointer at whatever that package builds --
"a reference to terms/contract/rights record; not the licence text itself".
Nothing in this module interprets it.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SourcePackage, SourcePackageEntry
from .standard_sources import normalize_sha256, normalize_source_uri

# Section 7.11. The fifth `method` vocabulary in the semantic model and the
# only one sharing no value with the other four -- `symbol_semantic_-`
# `assignments.method`, `concept_external_references.mapping_method`, the
# classification assignments' `method`, and
# `standard_sources.STANDARD_VERIFICATION_METHODS`. That is not an oversight:
# how a *package* was obtained is a procurement fact, while the other four
# describe how an assertion was arrived at. Unifying them is a specification
# change, not an implementation tidy-up.
PACKAGE_ACQUISITION_METHODS = frozenset(
    {
        "manual_upload",
        "public_download",
        "licensed_download",
        "api",
        "contributed",
        "generated",
    }
)

# The two methods that imply someone accepted terms to obtain the package, and
# so should carry the section 7.12 reference forward. Policy, not a
# constraint: the rights record itself is SM-P0-06, and refusing the package
# until it exists would block ingestion on unbuilt work.
LICENSED_ACQUISITION_METHODS = frozenset({"licensed_download", "contributed"})

PACKAGE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,62}[A-Z0-9]$")

PACKAGE_CODE_MAX_LENGTH = 64
TITLE_MAX_LENGTH = 256
PROVIDER_MAX_LENGTH = 256
PACKAGE_TYPE_MAX_LENGTH = 64
PROVIDER_IDENTIFIER_MAX_LENGTH = 512
RELEASE_VERSION_MAX_LENGTH = 128
LICENCE_REFERENCE_MAX_LENGTH = 512
INGESTION_PROFILE_MAX_LENGTH = 256
SOURCE_PATH_MAX_LENGTH = 1024
SOURCE_LABEL_MAX_LENGTH = 512

# `package_type` and `status` are pre-existing columns the submission-intake
# path writes, and no check constraint governs either -- one would be
# validated against rows that path already wrote. These are the values this
# module uses for an authoritative library release, kept distinct from
# intake's `submission_sheet` so the two origins stay tellable apart.
AUTHORITATIVE_PACKAGE_TYPE = "authoritative_library"
DEFAULT_PACKAGE_STATUS = "active"


def _require_aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
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


def normalize_package_code(value: object) -> str:
    """Return a package code in canonical form.

    Uppercased. The submission-intake path already allocates four uppercase
    hex characters, and this grammar accepts those unchanged while leaving
    room for a readable code on an authoritative release.
    """
    code = _normalize_required_text(value, "source package code", PACKAGE_CODE_MAX_LENGTH)
    if not code.isascii():
        raise ValueError("source package code must contain ASCII characters only")
    normalized_code = code.upper()
    if not PACKAGE_CODE_PATTERN.match(normalized_code):
        raise ValueError(f"source package code does not match the required grammar: {value!r}")
    return normalized_code


def normalize_provider_identifier(value: object, label: str) -> str | None:
    """Return a provider's own identifier, case preserved.

    Case is not folded: the provider is the authority on its own release and
    entry identifiers.
    """
    return _normalize_optional_text(value, label, PROVIDER_IDENTIFIER_MAX_LENGTH)


def normalize_package_metadata(value: object) -> dict:
    """Return the provider-specific extension object, which must be an object."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("source package metadata must be an object")
    return value


def normalize_acquisition(
    acquired_at: object, acquisition_method: object
) -> tuple[datetime | None, str | None]:
    """Validate the acquisition pair.

    An acquisition method with no timestamp, or a timestamp with no method,
    records half an event and cannot be reproduced. The database enforces the
    same pairing, written so it can never evaluate to NULL.
    """
    if (acquired_at is None) != (acquisition_method is None):
        raise ValueError("an acquisition time and method must be given together")
    if acquisition_method is None:
        return None, None
    if acquisition_method not in PACKAGE_ACQUISITION_METHODS:
        raise ValueError("invalid source package acquisition method")
    return _require_aware_timestamp(acquired_at, "source package acquisition time"), acquisition_method


def requires_licence_reference(acquisition_method: object) -> bool:
    """Whether obtaining a package this way implies someone accepted terms.

    A reporting predicate for the section 9.2 publication gate, not a
    gate itself: the structured rights record is SM-P0-06, and refusing a
    package until it exists would block ingestion on unbuilt work.
    """
    return acquisition_method in LICENSED_ACQUISITION_METHODS


def register_source_package(
    session: Session,
    *,
    package_code: str,
    title: str,
    registered_at: datetime,
    provider: object = None,
    package_type: str = AUTHORITATIVE_PACKAGE_TYPE,
    status: str = DEFAULT_PACKAGE_STATUS,
    package_id: uuid.UUID | None = None,
    **acquisition: object,
) -> SourcePackage:
    """Register an acquisition envelope for an authoritative library release.

    Distinct from `runtime.ensure_source_package_for_intake`, which allocates
    a package per submission sheet and is left exactly as it is. Any section
    7.11 acquisition field may be passed through and is validated by
    `record_package_acquisition`.
    """
    normalized_code = normalize_package_code(package_code)
    normalized_title = _normalize_required_text(title, "source package title", TITLE_MAX_LENGTH)
    normalized_provider = _normalize_optional_text(provider, "source package provider", PROVIDER_MAX_LENGTH)
    normalized_type = _normalize_required_text(package_type, "source package type", PACKAGE_TYPE_MAX_LENGTH)
    _require_aware_timestamp(registered_at, "source package registration time")

    package = SourcePackage(
        id=package_id or uuid.uuid4(),
        package_code=normalized_code,
        title=normalized_title,
        provider=normalized_provider,
        package_type=normalized_type,
        status=status,
        created_at=registered_at,
        updated_at=registered_at,
        metadata_json={},
    )
    # Validation before `session.add`: a rejected argument should leave the
    # session untouched rather than half-populated.
    if acquisition:
        _apply_acquisition(package, recorded_at=registered_at, **acquisition)
    session.add(package)
    return package


def record_package_acquisition(
    session: Session,
    package_id: uuid.UUID,
    *,
    recorded_at: datetime,
    **acquisition: object,
) -> SourcePackage:
    """Attach section 7.11 acquisition provenance to an existing package.

    This is the path a submission-created package takes when someone
    afterwards establishes where the content actually came from, and it is why
    every one of these columns is nullable: the intake path records none of
    them, and section 7.11 marks all of them "recommended".

    It replaces the acquisition record wholesale rather than patching fields.
    One package was obtained once, from one place, under one release, and the
    `(acquired_at, acquisition_method)` pairing the database enforces only
    makes sense read that way -- so an omitted argument clears the column
    rather than preserving whatever was there before. `metadata_json` is the
    single exception: it is a provider-specific extension bag, and passing no
    `metadata` leaves it as it stands.
    """
    _require_aware_timestamp(recorded_at, "source package acquisition record time")
    package = session.get(SourcePackage, package_id, with_for_update=True)
    if package is None:
        raise LookupError(f"source package not found: {package_id}")
    _apply_acquisition(package, recorded_at=recorded_at, **acquisition)
    return package


def _apply_acquisition(
    package: SourcePackage,
    *,
    recorded_at: datetime,
    provider_package_identifier: object = None,
    source_uri: object = None,
    release_version: object = None,
    release_date: object = None,
    acquired_at: object = None,
    acquisition_method: object = None,
    licence_reference: object = None,
    package_sha256: object = None,
    ingestion_profile: object = None,
    metadata: object = None,
) -> None:
    normalized_identifier = normalize_provider_identifier(
        provider_package_identifier, "provider package identifier"
    )
    normalized_uri = normalize_source_uri(source_uri, "source package URI")
    normalized_release = _normalize_optional_text(
        release_version, "source package release version", RELEASE_VERSION_MAX_LENGTH
    )
    if release_date is not None and not isinstance(release_date, date):
        raise ValueError("source package release date must be a date")
    normalized_acquired_at, normalized_method = normalize_acquisition(acquired_at, acquisition_method)
    normalized_licence = _normalize_optional_text(
        licence_reference, "source package licence reference", LICENCE_REFERENCE_MAX_LENGTH
    )
    normalized_hash = normalize_sha256(package_sha256, "source package hash")
    normalized_profile = _normalize_optional_text(
        ingestion_profile, "source package ingestion profile", INGESTION_PROFILE_MAX_LENGTH
    )
    normalized_metadata = normalize_package_metadata(metadata)

    if normalized_hash is not None and normalized_acquired_at is None:
        raise ValueError("a source package hash must record when the package was acquired")

    package.provider_package_identifier = normalized_identifier
    package.source_uri = normalized_uri
    package.release_version = normalized_release
    package.release_date = release_date
    package.acquired_at = normalized_acquired_at
    package.acquisition_method = normalized_method
    package.licence_reference = normalized_licence
    package.package_sha256 = normalized_hash
    package.ingestion_profile = normalized_profile
    if metadata is not None:
        package.metadata_json = normalized_metadata
    package.updated_at = recorded_at


def add_source_package_entry(
    session: Session,
    *,
    source_package_id: uuid.UUID,
    symbol_revision_id: uuid.UUID,
    added_at: datetime,
    source_label: object = None,
    provider_entry_identifier: object = None,
    source_path: object = None,
    original_asset_sha256: object = None,
    sort_order: object = None,
) -> SourcePackageEntry:
    """Record one symbol revision's place inside an acquired package.

    `source_path` addresses the entry *within* the package -- the path of the
    file as the provider shipped it. The package's own addressable location is
    `SourcePackage.source_uri`, which is why section 7.11's
    `source_path/source_locator` ships as the former.

    An `original_asset_sha256` requires one of the two identifying columns.
    A hash that names no asset traces nothing, and the database refuses it too.
    """
    _require_aware_timestamp(added_at, "source package entry time")
    normalized_label = _normalize_optional_text(source_label, "source entry label", SOURCE_LABEL_MAX_LENGTH)
    normalized_identifier = normalize_provider_identifier(
        provider_entry_identifier, "provider entry identifier"
    )
    normalized_path = _normalize_optional_text(source_path, "source entry path", SOURCE_PATH_MAX_LENGTH)
    normalized_hash = normalize_sha256(original_asset_sha256, "original asset hash")
    if sort_order is not None:
        if isinstance(sort_order, bool) or not isinstance(sort_order, int):
            raise ValueError("source entry sort order must be an integer")
        if sort_order < 0:
            raise ValueError("source entry sort order must not be negative")

    if normalized_hash is not None and normalized_path is None and normalized_identifier is None:
        raise ValueError(
            "an original asset hash must name the entry it belongs to, by path or provider identifier"
        )

    package = session.get(SourcePackage, source_package_id)
    if package is None:
        raise LookupError(f"source package not found: {source_package_id}")

    entry = SourcePackageEntry(
        id=uuid.uuid4(),
        source_package_id=source_package_id,
        symbol_revision_id=symbol_revision_id,
        sort_order=sort_order,
        source_label=normalized_label,
        created_at=added_at,
        provider_entry_identifier=normalized_identifier,
        source_path=normalized_path,
        original_asset_sha256=normalized_hash,
    )
    session.add(entry)
    return entry


def get_source_package(session: Session, package_code: str) -> SourcePackage | None:
    """Look a package up by its code."""
    return session.execute(
        select(SourcePackage).where(SourcePackage.package_code == normalize_package_code(package_code))
    ).scalar_one_or_none()


def find_entries_by_provider_identifier(
    session: Session, source_package_id: uuid.UUID, provider_entry_identifier: str
) -> list[SourcePackageEntry]:
    """Find the entries one provider entry identifier names inside a package.

    Appendix B.2's "exact provider entry ID" read path. It returns a list
    rather than one row: the unique key on this table is
    (package, symbol revision), so a provider identifier covering several
    revisions is legitimate.
    """
    identifier = normalize_provider_identifier(provider_entry_identifier, "provider entry identifier")
    if identifier is None:
        raise ValueError("a provider entry identifier is required")
    return list(
        session.execute(
            select(SourcePackageEntry)
            .where(
                SourcePackageEntry.source_package_id == source_package_id,
                SourcePackageEntry.provider_entry_identifier == identifier,
            )
            .order_by(SourcePackageEntry.sort_order, SourcePackageEntry.created_at)
        ).scalars()
    )


def list_source_package_entries(
    session: Session, source_package_id: uuid.UUID
) -> list[SourcePackageEntry]:
    """List a package's entries in the order the provider shipped them."""
    return list(
        session.execute(
            select(SourcePackageEntry)
            .where(SourcePackageEntry.source_package_id == source_package_id)
            .order_by(SourcePackageEntry.sort_order, SourcePackageEntry.created_at)
        ).scalars()
    )


def trace_symbol_revision_sources(session: Session, symbol_revision_id: uuid.UUID) -> list[dict]:
    """Return the source-provenance chain for one symbol revision.

    Section 16.1's acceptance criterion, as far as SM-P0-05 reaches: "an
    ingested authoritative symbol can be traced to source package,
    release/version, exact provider entry/symbol ID where available, rights
    disposition and hashes". Every element but rights is here; `licence_-`
    `reference` is returned as the forward pointer, and the rights disposition
    it will resolve to is SM-P0-06.

    A key whose value is None means the provenance was never recorded, not
    that it does not exist. Nothing here invents a value to fill a gap.
    """
    rows = session.execute(
        select(SourcePackageEntry, SourcePackage)
        .join(SourcePackage, SourcePackage.id == SourcePackageEntry.source_package_id)
        .where(SourcePackageEntry.symbol_revision_id == symbol_revision_id)
        .order_by(SourcePackage.package_code, SourcePackageEntry.sort_order)
    ).all()
    return [
        {
            "source_package_id": package.id,
            "package_code": package.package_code,
            "provider": package.provider,
            "provider_package_identifier": package.provider_package_identifier,
            "source_uri": package.source_uri,
            "release_version": package.release_version,
            "release_date": package.release_date,
            "acquired_at": package.acquired_at,
            "acquisition_method": package.acquisition_method,
            "licence_reference": package.licence_reference,
            "package_sha256": package.package_sha256,
            "ingestion_profile": package.ingestion_profile,
            "entry_id": entry.id,
            "source_label": entry.source_label,
            "provider_entry_identifier": entry.provider_entry_identifier,
            "source_path": entry.source_path,
            "original_asset_sha256": entry.original_asset_sha256,
        }
        for entry, package in rows
    ]
