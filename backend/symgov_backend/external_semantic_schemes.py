"""External semantic schemes and their releases (SM-P0-03).

Specification section 3.4 is the reason this module exists: external
reference-data libraries are versioned dependencies, not timeless lookup
lists. ISO 15926 continues to publish new parts and editions, so SymGov
records the scheme *and* the release a mapping was observed in.

Nothing here imports reference data. Section 15.1 seeds the three scheme
definitions and stops there; bulk RDL ingestion is SM-P2-01/02.
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ExternalSemanticScheme, ExternalSemanticSchemeVersion

SCHEME_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,62}[A-Z0-9]$")

EXTERNAL_SCHEME_STATUSES = frozenset({"active", "deprecated", "withdrawn"})

# One vocabulary covers both the scheme and its releases. For a scheme,
# `deprecated` means the issuing body no longer maintains it; for a release, it
# means a later release has superseded it. `withdrawn` means the row was
# registered in error or retracted, and is terminal.
EXTERNAL_SCHEME_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"deprecated", "withdrawn"}),
    "deprecated": frozenset({"active", "withdrawn"}),
    "withdrawn": frozenset(),
}

# A release in one of these states accepts no new concept mappings.
CLOSED_SCHEME_VERSION_STATUSES = frozenset({"withdrawn"})

CHECKSUM_ALGORITHMS = frozenset({"md5", "sha1", "sha256", "sha512"})
_CHECKSUM_PATTERN = re.compile(r"^[0-9a-f]{32,128}$")

SCHEME_CODE_MAX_LENGTH = 64
TITLE_MAX_LENGTH = 256
ISSUING_BODY_MAX_LENGTH = 256
URI_MAX_LENGTH = 1024
VERSION_LABEL_MAX_LENGTH = 128
ETAG_MAX_LENGTH = 256

# The three scheme definitions specification section 15.1 asks P0 to seed, and
# the only rows migration 20260909_0049 inserts. Identifiers are
# uuid5(NAMESPACE_URL, "urn:symgov:external-semantic-scheme:<code>") so every
# environment agrees on them without a lookup by code.
#
# `base_uri` carries the authoritative reference URL cited in the
# specification's Appendix D. Appendix D cites no namespace root for the
# PCA/ISO 15926 reference data library, so that one is None rather than a
# plausible-looking invention.
SEED_EXTERNAL_SEMANTIC_SCHEMES: tuple[dict[str, object], ...] = (
    {
        "id": uuid.UUID("154348c5-bcb3-55c8-ac3c-c7e5be122395"),
        "scheme_code": "DEXPI-RDL",
        "title": "DEXPI Sandbox Reference Data Library",
        "issuing_body": "DEXPI e.V.",
        "base_uri": "https://dexpi.org/tools-service/",
    },
    {
        "id": uuid.UUID("3d9c4bf2-d8d6-53fc-800b-2cda40ba2c91"),
        # The specification writes this code as "ISO15926-RDL/PCA"; the slash is
        # dropped so a scheme code stays safe in a URL path segment.
        "scheme_code": "ISO15926-RDL-PCA",
        "title": "ISO 15926 Reference Data Library",
        "issuing_body": "POSC Caesar Association",
        "base_uri": None,
    },
    {
        "id": uuid.UUID("5d40bb55-fc0a-563f-bc7c-9ac7a973bddc"),
        "scheme_code": "CFIHOS-RDL",
        "title": "CFIHOS Reference Data Library",
        "issuing_body": "IOGP JIP36 / CFIHOS",
        "base_uri": "https://www.jip36-cfihos.org/cfihos-standards/",
    },
)


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


def normalize_external_scheme_code(value: object) -> str:
    """Return a scheme code in canonical form.

    Codes are uppercased so `dexpi-rdl` and `DEXPI-RDL` cannot become two
    schemes; the unique index is on the stored value, not a folded one.
    """
    code = _normalize_required_text(value, "external scheme code", SCHEME_CODE_MAX_LENGTH)
    if not code.isascii():
        raise ValueError("external scheme code must contain ASCII characters only")
    normalized_code = code.upper()
    if not SCHEME_CODE_PATTERN.match(normalized_code):
        raise ValueError(f"external scheme code does not match the required grammar: {value!r}")
    return normalized_code


def normalize_external_scheme_uri(value: object, label: str) -> str | None:
    """Return an http(s) URI, or None."""
    if value is None:
        return None
    uri = _normalize_required_text(value, label, URI_MAX_LENGTH)
    if not uri.startswith(("http://", "https://")):
        raise ValueError(f"{label} must be an http or https URI")
    return uri


def normalize_scheme_version_integrity(
    checksum: object, checksum_algorithm: object, etag: object, retrieved_at: object
) -> tuple[str | None, str | None, str | None, datetime | None]:
    """Validate the release-integrity quartet.

    A checksum without its algorithm is unverifiable, and a checksum or etag
    with no retrieval time cannot be reproduced -- which defeats the
    configuration-management traceability specification section 3.2 expects of
    a versioned external dependency.
    """
    if (checksum is None) != (checksum_algorithm is None):
        raise ValueError("a scheme version checksum and its algorithm must be given together")

    normalized_checksum: str | None = None
    normalized_algorithm: str | None = None
    if checksum is not None:
        normalized_checksum = _normalize_required_text(checksum, "scheme version checksum", 128).lower()
        if not _CHECKSUM_PATTERN.match(normalized_checksum):
            raise ValueError("scheme version checksum must be a lowercase hex digest")
        if checksum_algorithm not in CHECKSUM_ALGORITHMS:
            raise ValueError("invalid scheme version checksum algorithm")
        normalized_algorithm = checksum_algorithm

    normalized_etag = None if etag is None else _normalize_required_text(etag, "scheme version etag", ETAG_MAX_LENGTH)

    normalized_retrieved_at: datetime | None = None
    if retrieved_at is not None:
        normalized_retrieved_at = _require_aware_timestamp(retrieved_at, "scheme version retrieval time")
    if (normalized_checksum is not None or normalized_etag is not None) and normalized_retrieved_at is None:
        raise ValueError("a scheme version checksum or etag must record when it was retrieved")

    return normalized_checksum, normalized_algorithm, normalized_etag, normalized_retrieved_at


def register_external_semantic_scheme(
    session: Session,
    *,
    scheme_code: str,
    title: str,
    issuing_body: str,
    registered_at: datetime,
    base_uri: object = None,
    created_by_user_id: uuid.UUID | None = None,
    scheme_id: uuid.UUID | None = None,
) -> ExternalSemanticScheme:
    """Register an external reference-data scheme.

    Specification section 14.1 places this authority with a platform admin or a
    controlled ingestion service, so `created_by_user_id` is optional: a
    service-driven registration has no user to name.
    """
    normalized_code = normalize_external_scheme_code(scheme_code)
    normalized_title = _normalize_required_text(title, "external scheme title", TITLE_MAX_LENGTH)
    normalized_body = _normalize_required_text(issuing_body, "external scheme issuing body", ISSUING_BODY_MAX_LENGTH)
    normalized_base_uri = normalize_external_scheme_uri(base_uri, "external scheme base URI")
    _require_aware_timestamp(registered_at, "external scheme registration time")
    _require_optional_actor(created_by_user_id, "external scheme registrar")

    scheme = ExternalSemanticScheme(
        id=scheme_id or uuid.uuid4(),
        scheme_code=normalized_code,
        title=normalized_title,
        issuing_body=normalized_body,
        base_uri=normalized_base_uri,
        status="active",
        created_by_user_id=created_by_user_id,
        created_at=registered_at,
        updated_at=registered_at,
    )
    session.add(scheme)
    return scheme


def register_external_scheme_version(
    session: Session,
    *,
    scheme_id: uuid.UUID,
    version_label: str,
    registered_at: datetime,
    release_date: date | None = None,
    source_uri: object = None,
    checksum: object = None,
    checksum_algorithm: object = None,
    etag: object = None,
    retrieved_at: object = None,
    created_by_user_id: uuid.UUID | None = None,
) -> ExternalSemanticSchemeVersion:
    """Register one release of an external scheme."""
    normalized_label = _normalize_required_text(
        version_label, "external scheme version label", VERSION_LABEL_MAX_LENGTH
    )
    normalized_source_uri = normalize_external_scheme_uri(source_uri, "external scheme version source URI")
    _require_aware_timestamp(registered_at, "external scheme version registration time")
    _require_optional_actor(created_by_user_id, "external scheme version registrar")
    if release_date is not None and not isinstance(release_date, date):
        raise ValueError("external scheme version release date must be a date")
    normalized_checksum, normalized_algorithm, normalized_etag, normalized_retrieved_at = (
        normalize_scheme_version_integrity(checksum, checksum_algorithm, etag, retrieved_at)
    )

    scheme = session.get(ExternalSemanticScheme, scheme_id)
    if scheme is None:
        raise LookupError(f"external semantic scheme not found: {scheme_id}")
    if scheme.status == "withdrawn":
        raise ValueError("external semantic scheme is withdrawn and accepts no new versions")

    version = ExternalSemanticSchemeVersion(
        id=uuid.uuid4(),
        scheme_id=scheme_id,
        version_label=normalized_label,
        release_date=release_date,
        source_uri=normalized_source_uri,
        checksum=normalized_checksum,
        checksum_algorithm=normalized_algorithm,
        etag=normalized_etag,
        retrieved_at=normalized_retrieved_at,
        status="active",
        created_by_user_id=created_by_user_id,
        created_at=registered_at,
        updated_at=registered_at,
    )
    session.add(version)
    return version


def set_external_scheme_status(
    session: Session, scheme_id: uuid.UUID, *, target_status: str, occurred_at: datetime
) -> ExternalSemanticScheme:
    """Move a scheme between active, deprecated and withdrawn."""
    scheme = session.get(ExternalSemanticScheme, scheme_id)
    if scheme is None:
        raise LookupError(f"external semantic scheme not found: {scheme_id}")
    _apply_scheme_status(scheme, target_status=target_status, occurred_at=occurred_at, label="external semantic scheme")
    return scheme


def set_external_scheme_version_status(
    session: Session, version_id: uuid.UUID, *, target_status: str, occurred_at: datetime
) -> ExternalSemanticSchemeVersion:
    """Move a release between active, deprecated and withdrawn.

    Deprecating a release deliberately leaves its existing mappings alone:
    a mapping records what an earlier release said, and that remains true after
    a newer release lands.
    """
    version = session.get(ExternalSemanticSchemeVersion, version_id)
    if version is None:
        raise LookupError(f"external scheme version not found: {version_id}")
    _apply_scheme_status(version, target_status=target_status, occurred_at=occurred_at, label="external scheme version")
    return version


def _apply_scheme_status(
    row: ExternalSemanticScheme | ExternalSemanticSchemeVersion,
    *,
    target_status: str,
    occurred_at: datetime,
    label: str,
) -> None:
    if target_status not in EXTERNAL_SCHEME_STATUSES:
        raise ValueError(f"invalid {label} status")
    _require_aware_timestamp(occurred_at, f"{label} status change time")
    current_status = row.status
    if target_status not in EXTERNAL_SCHEME_STATUS_TRANSITIONS[current_status]:
        raise ValueError(f"{label} cannot move from {current_status} to {target_status}")
    row.status = target_status
    row.updated_at = occurred_at


def seed_external_semantic_schemes(session: Session, *, seeded_at: datetime) -> list[ExternalSemanticScheme]:
    """Ensure the three P0 scheme definitions exist, and return them.

    Idempotent, and it never overwrites a row an operator has since edited:
    migration 20260909_0049 already inserts these, so this function exists for
    fixtures and for a database seeded before that migration landed.
    """
    _require_aware_timestamp(seeded_at, "external scheme seed time")
    seeded: list[ExternalSemanticScheme] = []
    for definition in SEED_EXTERNAL_SEMANTIC_SCHEMES:
        existing = session.execute(
            select(ExternalSemanticScheme).where(
                ExternalSemanticScheme.scheme_code == definition["scheme_code"]
            )
        ).scalar_one_or_none()
        if existing is not None:
            seeded.append(existing)
            continue
        seeded.append(
            register_external_semantic_scheme(
                session,
                scheme_id=definition["id"],
                scheme_code=definition["scheme_code"],
                title=definition["title"],
                issuing_body=definition["issuing_body"],
                base_uri=definition["base_uri"],
                registered_at=seeded_at,
            )
        )
    return seeded


def get_external_semantic_scheme(session: Session, scheme_code: str) -> ExternalSemanticScheme | None:
    """Look a scheme up by its code."""
    return session.execute(
        select(ExternalSemanticScheme).where(
            ExternalSemanticScheme.scheme_code == normalize_external_scheme_code(scheme_code)
        )
    ).scalar_one_or_none()


def list_external_semantic_schemes(
    session: Session, *, status: str | None = None
) -> list[ExternalSemanticScheme]:
    """List schemes by code."""
    if status is not None and status not in EXTERNAL_SCHEME_STATUSES:
        raise ValueError("invalid external scheme status filter")
    query = select(ExternalSemanticScheme)
    if status is not None:
        query = query.where(ExternalSemanticScheme.status == status)
    return list(session.execute(query.order_by(ExternalSemanticScheme.scheme_code)).scalars())


def list_external_scheme_versions(
    session: Session, scheme_id: uuid.UUID, *, status: str | None = None
) -> list[ExternalSemanticSchemeVersion]:
    """List a scheme's releases, newest release date first, undated last."""
    if status is not None and status not in EXTERNAL_SCHEME_STATUSES:
        raise ValueError("invalid external scheme version status filter")
    query = select(ExternalSemanticSchemeVersion).where(
        ExternalSemanticSchemeVersion.scheme_id == scheme_id
    )
    if status is not None:
        query = query.where(ExternalSemanticSchemeVersion.status == status)
    query = query.order_by(
        ExternalSemanticSchemeVersion.release_date.is_(None),
        ExternalSemanticSchemeVersion.release_date.desc(),
        ExternalSemanticSchemeVersion.version_label,
    )
    return list(session.execute(query).scalars())
