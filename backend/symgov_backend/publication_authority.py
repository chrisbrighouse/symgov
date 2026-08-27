from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session


def lock_review_case_decision_authority(session: Session, review_case_id: uuid.UUID) -> None:
    """Serialize review-decision replacement and publication effects per review case."""
    get_bind = getattr(session, "get_bind", None)
    if get_bind is None:  # Lightweight unit-test sessions may not own a DB connection.
        return
    bind = get_bind()
    if bind.dialect.name != "postgresql":
        return
    session.execute(
        text(
            "SELECT pg_advisory_xact_lock("
            "hashtextextended('publication-authority:' || CAST(:review_case_id AS text), 0)"
            ")"
        ),
        {"review_case_id": str(review_case_id)},
    )