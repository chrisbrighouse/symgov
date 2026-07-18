from __future__ import annotations

from collections.abc import Iterable
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import utc_now
from .models import CatalogFavourite


def _user_uuid(user_id: str | uuid.UUID) -> uuid.UUID:
    return user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))


def load_favourite_symbol_ids(
    session: Session,
    user_id: str | uuid.UUID,
    symbol_ids: Iterable[uuid.UUID] | None = None,
) -> set[uuid.UUID]:
    query = session.query(CatalogFavourite.symbol_id).filter(
        CatalogFavourite.user_id == _user_uuid(user_id)
    )
    if symbol_ids is not None:
        bounded_ids = list(symbol_ids)
        if not bounded_ids:
            return set()
        query = query.filter(CatalogFavourite.symbol_id.in_(bounded_ids))
    return {row[0] for row in query.all()}


def add_catalog_favourite(
    session: Session,
    user_id: str | uuid.UUID,
    symbol_id: uuid.UUID,
) -> None:
    account_id = _user_uuid(user_id)
    existing = session.get(
        CatalogFavourite,
        {"user_id": account_id, "symbol_id": symbol_id},
    )
    if existing is None:
        session.add(
            CatalogFavourite(
                user_id=account_id,
                symbol_id=symbol_id,
                created_at=utc_now(),
            )
        )
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            if session.get(
                CatalogFavourite,
                {"user_id": account_id, "symbol_id": symbol_id},
            ) is not None:
                return
            raise
        return
    session.commit()


def remove_catalog_favourite(
    session: Session,
    user_id: str | uuid.UUID,
    symbol_id: uuid.UUID,
) -> None:
    session.query(CatalogFavourite).filter(
        CatalogFavourite.user_id == _user_uuid(user_id),
        CatalogFavourite.symbol_id == symbol_id,
    ).delete(synchronize_session=False)
    session.commit()
