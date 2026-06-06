from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Iterable, Protocol

from .models import ReviewSymbolPropertyOption

LEGACY_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "symgov/runtime-legacy-id")
PROPERTY_OPTION_FIELDS = {"category", "discipline"}
PROPERTY_OPTION_FUZZY_MATCH_THRESHOLD = 0.92


class _PropertyOptionSession(Protocol):
    def query(self, *entities): ...
    def add(self, instance): ...


@dataclass(frozen=True)
class ResolvedPropertyOption:
    field_name: str
    value: str | None
    normalized_key: str
    created: bool
    matched_existing_value: str | None = None
    match_ratio: float | None = None


def normalize_property_option_value(value: str | None) -> str | None:
    text_value = re.sub(r"\s+", " ", str(value or "").strip())
    if not text_value:
        return None
    return " ".join(word.capitalize() for word in text_value.lower().split(" "))


def property_option_key(value: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def resolve_property_option_display_value(
    field_name: str,
    value: str | None,
    *,
    existing_options: Iterable[str],
    fuzzy_match_threshold: float = PROPERTY_OPTION_FUZZY_MATCH_THRESHOLD,
) -> ResolvedPropertyOption:
    display_value = normalize_property_option_value(value)
    if field_name not in PROPERTY_OPTION_FIELDS or display_value is None:
        return ResolvedPropertyOption(
            field_name=field_name,
            value=display_value,
            normalized_key=property_option_key(display_value),
            created=False,
        )

    normalized_key = property_option_key(display_value)
    if not normalized_key:
        return ResolvedPropertyOption(field_name=field_name, value=None, normalized_key="", created=False)

    best_value = None
    best_key = ""
    best_ratio = 0.0
    for option_value in existing_options:
        option_display = normalize_property_option_value(option_value)
        option_key = property_option_key(option_display)
        if not option_key:
            continue
        ratio = 1.0 if option_key == normalized_key else SequenceMatcher(None, normalized_key, option_key).ratio()
        if ratio > best_ratio:
            best_value = option_display
            best_key = option_key
            best_ratio = ratio

    if best_value is not None and (
        best_ratio >= fuzzy_match_threshold or (len(normalized_key) >= 5 and best_ratio >= fuzzy_match_threshold)
    ):
        return ResolvedPropertyOption(
            field_name=field_name,
            value=best_value,
            normalized_key=best_key,
            created=False,
            matched_existing_value=best_value,
            match_ratio=best_ratio,
        )

    return ResolvedPropertyOption(
        field_name=field_name,
        value=display_value,
        normalized_key=normalized_key,
        created=True,
        match_ratio=best_ratio if best_value is not None else None,
    )


def find_similar_property_option(
    session: _PropertyOptionSession,
    *,
    field_name: str,
    normalized_key: str,
) -> ReviewSymbolPropertyOption | None:
    if len(normalized_key) < 5:
        return None
    options = (
        session.query(ReviewSymbolPropertyOption)
        .filter(ReviewSymbolPropertyOption.field_name == field_name)
        .all()
    )
    best_option = None
    best_ratio = 0.0
    for option in options:
        ratio = SequenceMatcher(None, normalized_key, option.normalized_key).ratio()
        if ratio > best_ratio:
            best_option = option
            best_ratio = ratio
    if best_option is not None and best_ratio >= PROPERTY_OPTION_FUZZY_MATCH_THRESHOLD:
        return best_option
    return None


def remember_property_option(
    session: _PropertyOptionSession,
    *,
    field_name: str,
    value: str | None,
    now: datetime,
) -> str | None:
    if field_name not in PROPERTY_OPTION_FIELDS:
        return normalize_property_option_value(value)

    display_value = normalize_property_option_value(value)
    if display_value is None:
        return None

    normalized_key = property_option_key(display_value)
    if not normalized_key:
        return None

    option = (
        session.query(ReviewSymbolPropertyOption)
        .filter(
            ReviewSymbolPropertyOption.field_name == field_name,
            ReviewSymbolPropertyOption.normalized_key == normalized_key,
        )
        .one_or_none()
    )
    if option is None:
        option = find_similar_property_option(session, field_name=field_name, normalized_key=normalized_key)

    if option is None:
        option = ReviewSymbolPropertyOption(
            id=uuid.uuid5(LEGACY_ID_NAMESPACE, f"review-symbol-property-option:{field_name}:{normalized_key}"),
            field_name=field_name,
            display_value=display_value,
            normalized_key=normalized_key,
            use_count=1,
            created_at=now,
            updated_at=now,
            last_used_at=now,
        )
    else:
        option.use_count = int(option.use_count or 0) + 1
        option.updated_at = now
        option.last_used_at = now

    session.add(option)
    return option.display_value
