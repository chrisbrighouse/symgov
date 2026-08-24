from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session


PUBLIC_SYMBOL_ELIGIBILITY_SQL = text("""
    SELECT DISTINCT gs.id, gs.current_revision_id
    FROM governed_symbols gs
    JOIN symbol_revisions sr ON sr.id = gs.current_revision_id
        AND sr.symbol_id = gs.id
    JOIN published_pages pp ON pp.current_symbol_revision_id = sr.id
    JOIN publication_packs pk ON pk.id = pp.pack_id
    JOIN pack_entries pe ON pe.pack_id = pk.id
        AND pe.published_page_id = pp.id
        AND pe.symbol_revision_id = sr.id
    WHERE gs.id = ANY(:symbol_ids)
      AND pk.status = 'published'
      AND pk.audience = 'public'
      AND sr.lifecycle_state = 'published'
""")


def current_public_symbols(session: Session, symbol_ids: list) -> dict:
    """Return the currently eligible public symbols and their current revisions."""
    if not symbol_ids:
        return {}
    rows = session.execute(PUBLIC_SYMBOL_ELIGIBILITY_SQL, {"symbol_ids": symbol_ids}).all()
    return {row[0]: row[1] for row in rows}
