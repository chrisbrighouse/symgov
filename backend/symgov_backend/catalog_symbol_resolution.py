from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
import logging
import re
import uuid
from urllib.parse import unquote_to_bytes

from sqlalchemy import text
from sqlalchemy.orm import Session

from .catalog_symbol_ids import CATALOG_SYMBOL_ID_PATTERN


CATALOG_SYMBOL_REFERENCE_MAX_LENGTH = 128
_HEX_ESCAPE = re.compile(r"%[0-9A-Fa-f]{2}")
LOGGER = logging.getLogger(__name__)


class CatalogSymbolLookupUnavailable(RuntimeError):
    """Raised when resolver/database lookup cannot be completed safely."""


@dataclass(frozen=True)
class ResolvedCatalogSymbol:
    symbol_id: uuid.UUID
    catalog_symbol_id: str
    matched_by: Literal[
        "canonical", "uuid", "slug", "historical_alias", "page_code"
    ]


def _safe_reference(raw_reference: object) -> str | None:
    if not isinstance(raw_reference, str) or not raw_reference:
        return None
    if len(raw_reference) > CATALOG_SYMBOL_REFERENCE_MAX_LENGTH:
        return None
    if raw_reference != raw_reference.strip() or any(
        character.isspace() for character in raw_reference
    ):
        return None
    if re.search(r"%(?![0-9A-Fa-f]{2})", raw_reference):
        return None
    try:
        decoded = unquote_to_bytes(raw_reference).decode("utf-8", errors="strict")
    except (UnicodeDecodeError, ValueError):
        return None
    if not decoded or len(decoded) > CATALOG_SYMBOL_REFERENCE_MAX_LENGTH:
        return None
    if decoded != decoded.strip() or any(character.isspace() for character in decoded):
        return None
    if any(character in decoded for character in "%/\\?#"):
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in decoded):
        return None
    return decoded


def _resolved(rows: list[dict], matched_by: str) -> ResolvedCatalogSymbol | None:
    candidates: dict[uuid.UUID, str] = {}
    for row in rows:
        catalog_symbol_id = row.get("catalog_symbol_id") if isinstance(row, dict) else getattr(row, "catalog_symbol_id", None)
        try:
            raw_symbol_id = row.get("symbol_id") if isinstance(row, dict) else getattr(row, "symbol_id", None)
            symbol_id = uuid.UUID(str(raw_symbol_id))
        except (TypeError, ValueError, AttributeError):
            return None
        if not isinstance(catalog_symbol_id, str):
            return None
        existing = candidates.get(symbol_id)
        if existing is not None and existing != catalog_symbol_id:
            return None
        candidates[symbol_id] = catalog_symbol_id
    if len(candidates) != 1:
        return None
    symbol_id, catalog_symbol_id = next(iter(candidates.items()))
    return ResolvedCatalogSymbol(
        symbol_id=symbol_id,
        catalog_symbol_id=catalog_symbol_id,
        matched_by=matched_by,  # type: ignore[arg-type]
    )


def _log_resolution(
    *,
    route_family: str,
    match_type: str,
    outcome: str,
    catalog_symbol_id: str | None,
) -> None:
    if catalog_symbol_id:
        LOGGER.info(
            "catalog_symbol_resolution route_family=%s match_type=%s outcome=%s catalog_symbol_id=%s",
            route_family,
            match_type,
            outcome,
            catalog_symbol_id,
        )
        return
    LOGGER.info(
        "catalog_symbol_resolution route_family=%s match_type=%s outcome=%s",
        route_family,
        match_type,
        outcome,
    )


def _lookup(
    session: Session,
    sql: str,
    params: dict,
    matched_by: str,
    *,
    route_family: str,
):
    try:
        result = session.execute(text(sql), params)
        rows = result.mappings().all() if hasattr(result, "mappings") else result.all()
    except Exception as exc:  # pragma: no cover - exercised via route tests
        _log_resolution(
            route_family=route_family,
            match_type=matched_by,
            outcome="failure",
            catalog_symbol_id=None,
        )
        raise CatalogSymbolLookupUnavailable(
            "Catalog symbol lookup is temporarily unavailable. Please retry."
        ) from exc
    resolved = _resolved(rows, matched_by)
    return resolved, len(rows)


def resolve_catalog_symbol(
    session: Session,
    raw_reference: str,
    *,
    route_family: str = "unspecified",
) -> ResolvedCatalogSymbol | None:
    """Resolve one safe Catalog reference without scanning revision JSON identity."""
    reference = _safe_reference(raw_reference)
    if reference is None:
        return None

    uppercase = reference.upper()
    if len(uppercase) <= 32 and CATALOG_SYMBOL_ID_PATTERN.fullmatch(uppercase):
        resolved, row_count = _lookup(
            session,
            """
            SELECT csi.governed_symbol_id AS symbol_id,
                   gs.catalog_symbol_id
            FROM catalog_symbol_identifiers csi
            JOIN governed_symbols gs ON gs.id = csi.governed_symbol_id
            WHERE csi.identifier = :identifier
              AND csi.role = :role
              AND gs.catalog_symbol_id = csi.identifier
            LIMIT 2
            """,
            {"identifier": uppercase, "role": "canonical", "symbol_ref": reference},
            "canonical",
            route_family=route_family,
        )
        if resolved is not None:
            _log_resolution(
                route_family=route_family,
                match_type="canonical",
                outcome="resolved",
                catalog_symbol_id=resolved.catalog_symbol_id,
            )
            return resolved
        if row_count:
            _log_resolution(
                route_family=route_family,
                match_type="canonical",
                outcome="ambiguous",
                catalog_symbol_id=None,
            )
            return None

    try:
        symbol_uuid = uuid.UUID(reference)
    except ValueError:
        symbol_uuid = None
    if symbol_uuid is not None:
        resolved, row_count = _lookup(
            session,
            """
            SELECT gs.id AS symbol_id, gs.catalog_symbol_id
            FROM governed_symbols gs
            WHERE gs.id = :symbol_id
              AND gs.catalog_symbol_id IS NOT NULL
            LIMIT 2
            """,
            {"symbol_id": symbol_uuid, "symbol_ref": reference},
            "uuid",
            route_family=route_family,
        )
        if resolved is not None:
            _log_resolution(
                route_family=route_family,
                match_type="uuid",
                outcome="resolved",
                catalog_symbol_id=resolved.catalog_symbol_id,
            )
            return resolved
        if row_count:
            _log_resolution(
                route_family=route_family,
                match_type="uuid",
                outcome="ambiguous",
                catalog_symbol_id=None,
            )
            return None

    resolved, row_count = _lookup(
        session,
        """
        SELECT gs.id AS symbol_id, gs.catalog_symbol_id
        FROM governed_symbols gs
        WHERE gs.slug = :slug
          AND gs.catalog_symbol_id IS NOT NULL
        LIMIT 2
        """,
        {"slug": reference, "symbol_ref": reference},
        "slug",
        route_family=route_family,
    )
    if resolved is not None:
        _log_resolution(
            route_family=route_family,
            match_type="slug",
            outcome="resolved",
            catalog_symbol_id=resolved.catalog_symbol_id,
        )
        return resolved
    if row_count:
        _log_resolution(
            route_family=route_family,
            match_type="slug",
            outcome="ambiguous",
            catalog_symbol_id=None,
        )
        return None

    resolved, row_count = _lookup(
        session,
        """
        SELECT csi.governed_symbol_id AS symbol_id,
               gs.catalog_symbol_id
        FROM catalog_symbol_identifiers csi
        JOIN governed_symbols gs ON gs.id = csi.governed_symbol_id
        WHERE csi.identifier = :identifier
          AND csi.role = :role
          AND gs.catalog_symbol_id IS NOT NULL
        LIMIT 2
        """,
        {"identifier": uppercase, "role": "historical_alias", "symbol_ref": reference},
        "historical_alias",
        route_family=route_family,
    )
    if resolved is not None:
        _log_resolution(
            route_family=route_family,
            match_type="historical_alias",
            outcome="resolved",
            catalog_symbol_id=resolved.catalog_symbol_id,
        )
        return resolved
    if row_count:
        _log_resolution(
            route_family=route_family,
            match_type="historical_alias",
            outcome="ambiguous",
            catalog_symbol_id=None,
        )
        return None

    resolved, row_count = _lookup(
        session,
        """
        SELECT sr.symbol_id, gs.catalog_symbol_id
        FROM published_pages pp
        JOIN symbol_revisions sr ON sr.id = pp.current_symbol_revision_id
        JOIN governed_symbols gs ON gs.id = sr.symbol_id
        WHERE pp.page_code = :page_code
          AND gs.catalog_symbol_id IS NOT NULL
        LIMIT 2
        """,
        {"page_code": reference, "symbol_ref": reference},
        "page_code",
        route_family=route_family,
    )
    if resolved is not None:
        _log_resolution(
            route_family=route_family,
            match_type="page_code",
            outcome="resolved",
            catalog_symbol_id=resolved.catalog_symbol_id,
        )
        return resolved
    if row_count:
        _log_resolution(
            route_family=route_family,
            match_type="page_code",
            outcome="ambiguous",
            catalog_symbol_id=None,
        )
        return None
    _log_resolution(
        route_family=route_family,
        match_type="none",
        outcome="not_found",
        catalog_symbol_id=None,
    )
    return None
