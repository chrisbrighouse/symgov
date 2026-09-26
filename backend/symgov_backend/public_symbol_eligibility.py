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
    JOIN active_public_symbol_projections app
        ON app.governed_symbol_id = gs.id
       AND app.symbol_revision_id = sr.id
       AND app.published_page_id = pp.id
       AND app.pack_entry_id = pe.id
       AND app.publication_pack_id = pk.id
    WHERE gs.id = ANY(:symbol_ids)
      AND pk.status = 'published'
      AND pk.audience = 'public'
      AND sr.lifecycle_state = 'published'
""")


# The query joins the publication tables twice (directly and through
# `active_public_symbol_projections`), and the planner estimates a single row,
# so it probes by nested loop at about 8 ms per symbol. That suits checking a
# few symbols and not a Symbol Set: for 1,000 symbols over 52,500 public rows
# it took 7.7 s, and 0.23 s with hash joins (measured 2026-09-25). A hash pass
# costs about the same whatever the list size, so small lists keep the
# nested loops.
HASH_JOIN_THRESHOLD = 50


def current_public_symbols(session: Session, symbol_ids: list) -> dict:
    """Return the currently eligible public symbols and their current revisions."""
    if not symbol_ids:
        return {}
    hash_joins = len(symbol_ids) >= HASH_JOIN_THRESHOLD and session.get_bind().dialect.name == "postgresql"
    if hash_joins:
        # Undone by a rollback if the query fails, so it is only reset here.
        session.execute(text("SET LOCAL enable_nestloop = off"))
    rows = session.execute(PUBLIC_SYMBOL_ELIGIBILITY_SQL, {"symbol_ids": symbol_ids}).all()
    if hash_joins:
        session.execute(text("RESET enable_nestloop"))
    return {row[0]: row[1] for row in rows}
