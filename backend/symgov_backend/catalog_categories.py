"""The Catalog's category picklist: the built-in names plus reviewer additions.

Chris's decisions, 2026-10-25:

- Public Reviewers add a category while reviewing; it is offered at once, and
  every addition is audited. A Platform Admin can retire one.
- One list serves the public review editor and private organization drafts.
- Values reviewers typed before the picklist (`review_symbol_property_options`)
  are not added automatically; Platform Admin sees them for cleanup.

The built-in names stay in code (`catalog_taxonomy.CATALOG_CATEGORY_ORDER`),
because the browser's facet rules and the Catalog API are written against
them. An added name needs no rule: the facet rules already file an unmapped
category under its own name.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from difflib import SequenceMatcher

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser
from .catalog_taxonomy import CATALOG_CATEGORY_ORDER
from .models import CatalogCategoryAddition, GovernedSymbol, ReviewSymbolPropertyOption, User
from .organization_service import _emit_audit

UNCLASSIFIED = "Miscellaneous / Unclassified"
NAME_MIN_LENGTH = 2
NAME_MAX_LENGTH = 80
# Close enough to an existing name that the reviewer is asked to confirm.
SIMILARITY_THRESHOLD = 0.85


class CatalogCategoryError(ValueError):
    """A name that cannot be added."""


class CatalogCategoryNotFound(LookupError):
    pass


class SimilarCategory(Exception):
    """The name is close to one already listed; adding it needs confirmation."""

    def __init__(self, name: str, similar_to: str) -> None:
        super().__init__(f"'{name}' is close to the listed category '{similar_to}'.")
        self.name = name
        self.similar_to = similar_to


@dataclass(frozen=True)
class CategoryEntry:
    name: str
    built_in: bool
    addition_id: uuid.UUID | None = None
    added_at: datetime | None = None
    added_by_name: str | None = None


def category_key(value: str | None) -> str:
    """Letters and digits only, lower case: `Vessels/Tanks` and `vessels / tanks` are one key."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


_BUILT_IN_BY_KEY = {category_key(name): name for name in CATALOG_CATEGORY_ORDER}


def _active_additions(session: Session) -> list[CatalogCategoryAddition]:
    return (
        session.query(CatalogCategoryAddition)
        .filter(CatalogCategoryAddition.retired_at.is_(None))
        .order_by(func.lower(CatalogCategoryAddition.name))
        .all()
    )


def catalog_category_entries(session: Session) -> list[CategoryEntry]:
    """The picklist in display order: built-in names, then additions A-Z, then Unclassified last."""
    built_in = [CategoryEntry(name, True) for name in CATALOG_CATEGORY_ORDER if name != UNCLASSIFIED]
    additions = _active_additions(session)
    names = {
        user.id: user.display_name
        for user in session.execute(
            select(User).where(User.id.in_({row.added_by_user_id for row in additions}))
        ).scalars()
    } if additions else {}
    added = [
        CategoryEntry(row.name, False, row.id, row.added_at, names.get(row.added_by_user_id))
        for row in additions
    ]
    return [*built_in, *added, CategoryEntry(UNCLASSIFIED, True)]


def catalog_category_names(session: Session) -> list[str]:
    return [entry.name for entry in catalog_category_entries(session)]


def canonical_catalog_category(session: Session, value: str | None) -> str | None:
    """The listed name a value means (ignoring case, spaces and punctuation), or None.

    A built-in name is answered without a query; only other values look at
    the additions.
    """
    key = category_key(value)
    if not key:
        return None
    if key in _BUILT_IN_BY_KEY:
        return _BUILT_IN_BY_KEY[key]
    row = (
        session.query(CatalogCategoryAddition)
        .filter(CatalogCategoryAddition.normalized_key == key, CatalogCategoryAddition.retired_at.is_(None))
        .one_or_none()
    )
    return row.name if row is not None else None


def listed_category_or_current(session: Session, value: str | None, *, current: str | None) -> str | None:
    """What a save stores for `value`: its listed name, or `current` unchanged.

    Empty stays empty. A value saved before the picklist is kept while it is
    left as it was; changing it means choosing a listed name (or adding one).
    """
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return None
    listed = canonical_catalog_category(session, text)
    if listed is not None:
        return listed
    if current is not None and text == current:
        return text
    raise CatalogCategoryError(f"Unknown category '{text}'. Choose one from the list, or add it.")


def _similar_name(key: str, names: list[str]) -> str | None:
    best, best_ratio = None, 0.0
    for name in names:
        other = category_key(name)
        # A plural or singular of a listed name is the listed name.
        if key in (other + "s", other + "es") or other in (key + "s", key + "es"):
            return name
        ratio = SequenceMatcher(None, key, other).ratio()
        if ratio > best_ratio:
            best, best_ratio = name, ratio
    return best if best_ratio >= SIMILARITY_THRESHOLD else None


def clean_category_name(value: str | None) -> str:
    name = re.sub(r"\s+", " ", str(value or "").strip())
    if len(name) < NAME_MIN_LENGTH or len(name) > NAME_MAX_LENGTH:
        raise CatalogCategoryError(
            f"A category name must be {NAME_MIN_LENGTH} to {NAME_MAX_LENGTH} characters."
        )
    if not re.search(r"[A-Za-z]", name):
        raise CatalogCategoryError("A category name must contain a letter.")
    return name


def add_catalog_category(
    session: Session,
    actor: AuthenticatedUser,
    name: str,
    *,
    allow_similar: bool = False,
    review_case_id: uuid.UUID | None = None,
    effective_authority: str,
) -> tuple[str, bool]:
    """Add `name` to the picklist. Returns `(listed name, created)`.

    A name already listed (by key) is returned unchanged with `created=False`.
    A retired name is refused: only a Platform Admin retires, so bringing one
    back is theirs to decide. A name close to a listed one raises
    `SimilarCategory` unless `allow_similar`.
    """
    clean = clean_category_name(name)
    key = category_key(clean)
    existing = canonical_catalog_category(session, clean)
    if existing is not None:
        return existing, False
    retired = session.execute(
        select(CatalogCategoryAddition).where(CatalogCategoryAddition.normalized_key == key)
    ).scalar_one_or_none()
    if retired is not None:
        raise CatalogCategoryError(f"'{retired.name}' was retired by a Platform Admin and cannot be added again here.")
    if not allow_similar:
        similar = _similar_name(key, catalog_category_names(session))
        if similar is not None:
            raise SimilarCategory(clean, similar)

    now = _utc_now()
    actor_id = uuid.UUID(actor.id)
    row = CatalogCategoryAddition(
        id=uuid.uuid4(),
        name=clean,
        normalized_key=key,
        added_by_user_id=actor_id,
        added_at=now,
        review_case_id=review_case_id,
    )
    session.add(row)
    _emit_audit(
        session,
        entity_type="catalog_category",
        entity_id=row.id,
        action="catalog_category.added",
        actor_id=actor_id,
        payload={
            "name": clean,
            "effective_authority": effective_authority,
            "review_case_id": str(review_case_id) if review_case_id else None,
            "confirmed_similar": bool(allow_similar),
        },
    )
    session.flush()
    return clean, True


def retire_catalog_category(session: Session, actor: AuthenticatedUser, addition_id: uuid.UUID) -> CatalogCategoryAddition:
    """Stop offering an added category. Symbols that carry it keep it."""
    row = session.execute(
        select(CatalogCategoryAddition).where(CatalogCategoryAddition.id == addition_id).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        raise CatalogCategoryNotFound("Category not found.")
    if row.retired_at is None:
        actor_id = uuid.UUID(actor.id)
        row.retired_at = _utc_now()
        row.retired_by_user_id = actor_id
        _emit_audit(
            session,
            entity_type="catalog_category",
            entity_id=row.id,
            action="catalog_category.retired",
            actor_id=actor_id,
            payload={"name": row.name, "effective_authority": "platform_admin"},
        )
        session.flush()
    return row


def _symbol_counts(session: Session) -> dict[str, int]:
    """How many governed symbols carry each stored category, by key."""
    counts: dict[str, int] = {}
    for category, count in session.execute(
        select(GovernedSymbol.category, func.count()).group_by(GovernedSymbol.category)
    ):
        key = category_key(category)
        if key:
            counts[key] = counts.get(key, 0) + int(count)
    return counts


@dataclass(frozen=True)
class AdditionReport:
    id: uuid.UUID
    name: str
    added_at: datetime
    added_by_name: str | None
    retired_at: datetime | None
    symbol_count: int


@dataclass(frozen=True)
class LegacyValue:
    value: str
    use_count: int
    last_used_at: datetime | None
    symbol_count: int


def platform_category_report(session: Session) -> tuple[list[AdditionReport], list[LegacyValue]]:
    """Every addition (retired too) with its usage, and the old typed values not on any list."""
    counts = _symbol_counts(session)
    rows = list(
        session.execute(select(CatalogCategoryAddition).order_by(CatalogCategoryAddition.added_at.desc())).scalars()
    )
    users = {
        user.id: user.display_name
        for user in session.execute(select(User).where(User.id.in_({row.added_by_user_id for row in rows}))).scalars()
    } if rows else {}
    additions = [
        AdditionReport(
            row.id, row.name, row.added_at, users.get(row.added_by_user_id), row.retired_at,
            counts.get(row.normalized_key, 0),
        )
        for row in rows
    ]
    known = {category_key(name) for name in CATALOG_CATEGORY_ORDER} | {row.normalized_key for row in rows}
    legacy = [
        LegacyValue(option.display_value, int(option.use_count or 0), option.last_used_at,
                    counts.get(category_key(option.display_value), 0))
        for option in session.execute(
            select(ReviewSymbolPropertyOption)
            .where(ReviewSymbolPropertyOption.field_name == "category")
            .order_by(ReviewSymbolPropertyOption.use_count.desc(), ReviewSymbolPropertyOption.display_value.asc())
        ).scalars()
        if category_key(option.display_value) not in known
    ]
    return additions, legacy
