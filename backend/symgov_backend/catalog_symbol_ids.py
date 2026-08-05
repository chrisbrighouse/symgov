from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import insert, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .models import CatalogSymbolIdentifier, GovernedSymbol

CATALOG_SYMBOL_ID_PATTERN = re.compile(r"^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$")
POSTGRESQL_BIGINT_MAX = 9_223_372_036_854_775_807
CATALOG_SYMBOL_ID_ALLOCATION_ATTEMPTS = 3
CATALOG_SYMBOL_IDENTIFIER_PK_CONSTRAINT = "pk_catalog_symbol_identifiers"
CATALOG_SYMBOL_ID_CORRECTION_REASON_MAX_LENGTH = 500
CATALOG_SYMBOL_ID_ALLOCATION_SOURCES = frozenset(
    {"legacy_backfill", "global_sequence", "reviewed_correction"}
)


def _is_identifier_pk_violation(error: IntegrityError) -> bool:
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original, "pgcode", None
    )
    diagnostic = getattr(original, "diag", None)
    constraint_name = getattr(diagnostic, "constraint_name", None) or getattr(
        original, "constraint_name", None
    )
    return (
        sqlstate == "23505"
        and constraint_name == CATALOG_SYMBOL_IDENTIFIER_PK_CONSTRAINT
    )


def normalize_catalog_symbol_id(value: object) -> str:
    """Return an identifier in canonical form."""
    if not isinstance(value, str):
        raise ValueError("catalog symbol ID must be a string")
    if value != value.strip():
        raise ValueError("catalog symbol ID must not contain surrounding whitespace")
    if any(character in value for character in "/\\%?#"):
        raise ValueError("catalog symbol ID contains a path or URL delimiter")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("catalog symbol ID contains a control character")
    if not value.isascii():
        raise ValueError("catalog symbol ID must contain ASCII characters only")
    if value.startswith("-") or value.endswith("-"):
        raise ValueError("catalog symbol ID must not start or end with a hyphen")
    if len(value) < 2:
        raise ValueError("catalog symbol ID must contain at least two characters")
    if len(value) > 32:
        raise ValueError("catalog symbol ID must not exceed 32 characters")
    normalized_value = value.upper()
    if CATALOG_SYMBOL_ID_PATTERN.fullmatch(normalized_value) is None:
        raise ValueError("catalog symbol ID has invalid grammar")
    return normalized_value


def format_allocated_catalog_symbol_id(sequence_value: int) -> str:
    """Format an allocated PostgreSQL sequence value as a catalog symbol ID."""
    if isinstance(sequence_value, bool) or not isinstance(sequence_value, int):
        raise ValueError("catalog symbol ID sequence value must be an integer")
    if sequence_value <= 0:
        raise ValueError("catalog symbol ID sequence value must be positive")
    if sequence_value > POSTGRESQL_BIGINT_MAX:
        raise ValueError("catalog symbol ID sequence value exceeds PostgreSQL BIGINT maximum")
    return f"S-{sequence_value:06d}"


def ensure_catalog_symbol_id(
    session: Session,
    symbol_id: uuid.UUID,
    *,
    allocated_at: datetime,
    allocation_source: str = "global_sequence",
) -> str:
    """Lock the governed symbol, return an existing ID, or atomically allocate one."""
    if allocation_source not in CATALOG_SYMBOL_ID_ALLOCATION_SOURCES:
        raise ValueError("invalid catalog symbol ID allocation source")
    if not isinstance(allocated_at, datetime) or allocated_at.utcoffset() is None:
        raise ValueError("catalog symbol ID allocation time must be timezone-aware")

    symbol = session.get(GovernedSymbol, symbol_id, with_for_update=True)
    if symbol is None:
        raise LookupError(f"governed symbol not found: {symbol_id}")
    if symbol.catalog_symbol_id is not None:
        canonical_identifier = normalize_catalog_symbol_id(symbol.catalog_symbol_id)
        if canonical_identifier != symbol.catalog_symbol_id:
            raise ValueError("existing catalog symbol ID is not canonical")
        return canonical_identifier

    # Session.begin_nested() performs a mandatory pre-savepoint flush. Establish
    # a clean boundary first so failures from unrelated pending work are never
    # mistaken for a candidate-identifier collision.
    session.flush()

    for attempt in range(CATALOG_SYMBOL_ID_ALLOCATION_ATTEMPTS):
        sequence_value = session.execute(
            text("SELECT nextval('catalog_symbol_id_seq')")
        ).scalar_one()
        identifier = format_allocated_catalog_symbol_id(sequence_value)
        registry_insert = insert(CatalogSymbolIdentifier).values(
            identifier=identifier,
            role="canonical",
            governed_symbol_id=symbol_id,
            allocation_source=allocation_source,
            allocated_at=allocated_at,
        )
        savepoint = session.begin_nested()
        try:
            with savepoint:
                session.execute(registry_insert)
        except IntegrityError as error:
            if not _is_identifier_pk_violation(error) or attempt + 1 == CATALOG_SYMBOL_ID_ALLOCATION_ATTEMPTS:
                raise
            continue
        symbol.catalog_symbol_id = identifier
        return identifier

    raise RuntimeError("catalog symbol ID allocation attempts exhausted")


def correct_catalog_symbol_id(
    session: Session,
    symbol_id: uuid.UUID,
    new_identifier: str,
    *,
    actor_id: uuid.UUID,
    reason: str,
    preserve_old_link: bool,
    changed_at: datetime,
) -> str:
    """Replace canonical identity under lock; preserve or tombstone the old value."""
    normalized_identifier = normalize_catalog_symbol_id(new_identifier)
    if not isinstance(actor_id, uuid.UUID) or actor_id.int == 0:
        raise ValueError("catalog symbol ID correction actor must be a real UUID")
    if not isinstance(changed_at, datetime) or changed_at.utcoffset() is None:
        raise ValueError("catalog symbol ID correction time must be timezone-aware")
    if not isinstance(reason, str):
        raise ValueError("catalog symbol ID correction reason must be a string")
    normalized_reason = reason.strip()
    if not normalized_reason:
        raise ValueError("catalog symbol ID correction reason must not be empty")
    if len(normalized_reason) > CATALOG_SYMBOL_ID_CORRECTION_REASON_MAX_LENGTH:
        raise ValueError(
            "catalog symbol ID correction reason must not exceed "
            f"{CATALOG_SYMBOL_ID_CORRECTION_REASON_MAX_LENGTH} characters"
        )
    if not isinstance(preserve_old_link, bool):
        raise ValueError("preserve_old_link must be a boolean")

    symbol = session.get(GovernedSymbol, symbol_id, with_for_update=True)
    if symbol is None:
        raise LookupError(f"governed symbol not found: {symbol_id}")
    if symbol.catalog_symbol_id is None:
        raise ValueError("governed symbol has no current catalog symbol ID")
    current_identifier = normalize_catalog_symbol_id(symbol.catalog_symbol_id)
    if current_identifier != symbol.catalog_symbol_id:
        raise ValueError("existing catalog symbol ID is not canonical")

    current_row = session.get(
        CatalogSymbolIdentifier, current_identifier, with_for_update=True
    )
    if current_row is None:
        raise ValueError("current catalog symbol ID registry row is missing")
    if (
        current_row.role != "canonical"
        or current_row.governed_symbol_id != symbol_id
    ):
        raise ValueError("current catalog symbol ID registry row is inconsistent")

    collision = session.get(
        CatalogSymbolIdentifier, normalized_identifier, with_for_update=True
    )
    if collision is not None:
        raise ValueError(f"catalog symbol ID is permanently reserved: {normalized_identifier}")

    # Establish the same clean pre-savepoint boundary as sequence allocation so
    # unrelated pending failures cannot be mistaken for an identifier race.
    session.flush()
    registry_insert = insert(CatalogSymbolIdentifier).values(
        identifier=normalized_identifier,
        role="canonical",
        governed_symbol_id=symbol_id,
        allocation_source="reviewed_correction",
        allocated_at=changed_at,
        changed_at=changed_at,
        changed_by=actor_id,
        change_reason=normalized_reason,
    )
    savepoint = session.begin_nested()
    try:
        with savepoint:
            current_row.role = (
                "historical_alias" if preserve_old_link else "tombstone"
            )
            current_row.governed_symbol_id = symbol_id if preserve_old_link else None
            current_row.changed_at = changed_at
            current_row.changed_by = actor_id
            current_row.change_reason = normalized_reason
            session.execute(registry_insert)
            symbol.catalog_symbol_id = normalized_identifier
    except IntegrityError as error:
        if _is_identifier_pk_violation(error):
            raise ValueError(
                f"catalog symbol ID is permanently reserved: {normalized_identifier}"
            ) from error
        raise
    return normalized_identifier
