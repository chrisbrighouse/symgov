"""Catalog workbench state: preferences, saved views and the clipboard.

Preferences and saved views belong to the account. The clipboard belongs to
the account *within a session scope*: a personal session and each organization
session have their own, so an organization's private symbols never appear in
the clipboard outside that organization.

Each section is written whole by its own route, so saving the clipboard in one
tab never overwrites preferences saved in another.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any
import uuid

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .auth import AuthenticatedUser, utc_now
from .models import CatalogWorkbenchClipboard, CatalogWorkbenchState


# The Catalog UI already keeps at most twelve saved views; the clipboard had no
# bound at all, so this one is new and deliberately generous.
MAX_SAVED_VIEWS = 12
MAX_CLIPBOARD_ITEMS = 200
MAX_LIST_VALUES = 50
MAX_FACET_KEYS = 20

Label = Annotated[str, StringConstraints(max_length=120)]
FacetKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,39}$")]


def _unique_ids(items: list[Any]) -> list[Any]:
    ids = [item.id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("Item ids must be unique.")
    return items


class CatalogWorkbenchPreferences(BaseModel):
    model_config = ConfigDict(extra="forbid")

    disciplines: list[Label] = Field(default_factory=list, max_length=MAX_LIST_VALUES)
    categories: list[Label] = Field(default_factory=list, max_length=MAX_LIST_VALUES)
    formats: list[Label] = Field(default_factory=list, max_length=MAX_LIST_VALUES)
    useCases: list[Label] = Field(default_factory=list, max_length=MAX_LIST_VALUES)


class CatalogSavedView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=80)
    name: str = Field(min_length=1, max_length=120)
    query: str = Field(default="", max_length=500)
    facetFilters: dict[FacetKey, list[Label]] = Field(default_factory=dict)
    preferredFormats: list[Label] = Field(default_factory=list, max_length=MAX_LIST_VALUES)
    createdAt: str | None = Field(default=None, max_length=40)

    @field_validator("facetFilters")
    @classmethod
    def _bound_facet_filters(cls, value: dict[str, list[str]]) -> dict[str, list[str]]:
        if len(value) > MAX_FACET_KEYS:
            raise ValueError(f"At most {MAX_FACET_KEYS} facet filters are allowed.")
        if any(len(values) > MAX_LIST_VALUES for values in value.values()):
            raise ValueError(f"At most {MAX_LIST_VALUES} values are allowed per facet filter.")
        return value


class CatalogClipboardItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    displayName: str = Field(default="", max_length=200)
    name: str = Field(default="", max_length=300)
    availableFormats: list[Label] = Field(default_factory=list, max_length=MAX_LIST_VALUES)


class CatalogSavedViewsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CatalogSavedView] = Field(max_length=MAX_SAVED_VIEWS)

    @field_validator("items")
    @classmethod
    def _unique_items(cls, value: list[CatalogSavedView]) -> list[CatalogSavedView]:
        return _unique_ids(value)


class CatalogClipboardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    items: list[CatalogClipboardItem] = Field(max_length=MAX_CLIPBOARD_ITEMS)

    @field_validator("items")
    @classmethod
    def _unique_items(cls, value: list[CatalogClipboardItem]) -> list[CatalogClipboardItem]:
        return _unique_ids(value)


SECTION_COLUMNS = {
    "preferences": "preferences_json",
    "savedViews": "saved_views_json",
}


def _user_uuid(user_id: str | uuid.UUID) -> uuid.UUID:
    return user_id if isinstance(user_id, uuid.UUID) else uuid.UUID(str(user_id))


def _empty_preferences() -> dict:
    return CatalogWorkbenchPreferences().model_dump()


def clipboard_organization_id(current_user: AuthenticatedUser) -> uuid.UUID | None:
    """The organization the session's clipboard belongs to; None for a personal session."""
    if current_user.session_mode == "organization" and current_user.active_organization_id:
        return uuid.UUID(str(current_user.active_organization_id))
    return None


def _find_clipboard(
    session: Session,
    account_id: uuid.UUID,
    organization_id: uuid.UUID | None,
    *,
    for_update: bool = False,
) -> CatalogWorkbenchClipboard | None:
    query = session.query(CatalogWorkbenchClipboard).filter(
        CatalogWorkbenchClipboard.user_id == account_id,
        CatalogWorkbenchClipboard.organization_id.is_(None)
        if organization_id is None
        else CatalogWorkbenchClipboard.organization_id == organization_id,
    )
    if for_update:
        query = query.with_for_update()
    return query.one_or_none()


def load_catalog_workbench(
    session: Session,
    user_id: str | uuid.UUID,
    organization_id: uuid.UUID | None = None,
) -> dict:
    account_id = _user_uuid(user_id)
    state = session.get(CatalogWorkbenchState, account_id)
    clipboard = _find_clipboard(session, account_id, organization_id)
    updated = [row.updated_at for row in (state, clipboard) if row is not None and row.updated_at]
    return {
        "preferences": {**_empty_preferences(), **((state.preferences_json if state else None) or {})},
        "savedViews": list((state.saved_views_json if state else None) or []),
        "clipboard": list((clipboard.items_json if clipboard else None) or []),
        "updatedAt": max(updated).isoformat() if updated else None,
    }


def _upsert(session: Session, find: Callable[[], Any], create: Callable[[], Any], apply: Callable[[Any], None]) -> None:
    row = find()
    if row is None:
        row = create()
        apply(row)
        session.add(row)
        try:
            session.commit()
            return
        except IntegrityError:
            # Another request created the row first; update that one instead.
            session.rollback()
            row = find()
            if row is None:
                raise
    apply(row)
    session.commit()


def save_catalog_workbench_section(
    session: Session,
    user_id: str | uuid.UUID,
    section: str,
    value: dict | list,
) -> None:
    """Save the account's preferences or saved views, which follow the user everywhere."""
    column = SECTION_COLUMNS[section]
    account_id = _user_uuid(user_id)
    now = utc_now()

    def create() -> CatalogWorkbenchState:
        return CatalogWorkbenchState(
            user_id=account_id,
            preferences_json=_empty_preferences(),
            saved_views_json=[],
            created_at=now,
            updated_at=now,
        )

    def apply(state: CatalogWorkbenchState) -> None:
        setattr(state, column, value)
        state.updated_at = now

    _upsert(session, lambda: session.get(CatalogWorkbenchState, account_id, with_for_update=True), create, apply)


def save_catalog_workbench_clipboard(
    session: Session,
    user_id: str | uuid.UUID,
    organization_id: uuid.UUID | None,
    items: list,
) -> None:
    """Save the clipboard for one session scope: the account's personal
    clipboard, or its clipboard inside one organization."""
    account_id = _user_uuid(user_id)
    now = utc_now()

    def create() -> CatalogWorkbenchClipboard:
        return CatalogWorkbenchClipboard(
            user_id=account_id,
            organization_id=organization_id,
            items_json=[],
            created_at=now,
            updated_at=now,
        )

    def apply(clipboard: CatalogWorkbenchClipboard) -> None:
        clipboard.items_json = items
        clipboard.updated_at = now

    _upsert(session, lambda: _find_clipboard(session, account_id, organization_id, for_update=True), create, apply)
