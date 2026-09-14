from __future__ import annotations

import re

from sqlalchemy.orm import Session

from .models import GovernedSymbol, SymbolRevision


_SHORT_SYMBOL_DISPLAY_ID = re.compile(r"[0-9A-F]{4}-\d+", flags=re.IGNORECASE)


def is_short_symbol_display_id(value: object) -> bool:
    return bool(_SHORT_SYMBOL_DISPLAY_ID.fullmatch(str(value or "").strip()))


def symbol_revision_display_id(revision: SymbolRevision | None) -> str | None:
    payload = revision.payload_json if revision is not None else {}
    if not isinstance(payload, dict):
        return None

    package_id = payload.get("package_display_id")
    sequence = payload.get("package_symbol_sequence")
    if package_id and sequence is not None:
        try:
            candidate = f"{str(package_id).strip()}-{int(sequence)}"
        except (TypeError, ValueError):
            candidate = ""
        if is_short_symbol_display_id(candidate):
            return candidate

    for candidate in (
        payload.get("published_display_id"),
        payload.get("symbol_display_id"),
        payload.get("display_name"),
        payload.get("workspace_display_name"),
    ):
        if is_short_symbol_display_id(candidate):
            return str(candidate).strip()
    return None


def governed_symbol_human_readable_id(
    session: Session,
    symbol: GovernedSymbol,
) -> str | None:
    catalog_symbol_id = str(symbol.catalog_symbol_id or "").strip()
    if catalog_symbol_id:
        return catalog_symbol_id
    revision = (
        session.get(SymbolRevision, symbol.current_revision_id)
        if symbol.current_revision_id is not None
        else None
    )
    display_id = symbol_revision_display_id(revision)
    if display_id:
        return display_id
    if is_short_symbol_display_id(symbol.slug):
        return str(symbol.slug).strip()
    return None
