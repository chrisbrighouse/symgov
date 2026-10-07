"""Option (state) variants of a symbol: how they are listed, and how one is read.

An option SVG is the same drawing under a stated condition (a valve in
`ValvePosition = 'NC'`, say). It is never the symbol's preview or its default
download: `asset_manifest` keeps it out of every format-based selection. This
module is the one place that reads one on purpose, by its number.
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from .asset_manifest import list_state_variant_assets
from .image_content import UnsafeImageContentError, validate_stored_image
from .runtime import download_object_bytes
from .settings import get_settings


def state_variant_summaries(payload: dict | None, url_prefix: str) -> list[dict[str, Any]]:
    """`[{index, condition, url}]` for the variants a payload carries, in option order."""
    return [
        {
            "index": asset.get("option_index"),
            "condition": asset.get("condition"),
            "filename": asset.get("filename"),
            "url": f"{url_prefix}/state-variants/{asset.get('option_index')}",
        }
        for asset in list_state_variant_assets(payload)
    ]


def disc_extras(payload: dict | None) -> dict[str, Any]:
    """The geometry, register extras and option list an imported library stores, as served.

    Absent keys stay absent: a symbol without them says nothing about them.
    """
    payload = payload or {}
    extras: dict[str, Any] = {}
    geometry = payload.get("geometry")
    if isinstance(geometry, dict):
        extras["geometry"] = geometry
    disc = payload.get("disc")
    if isinstance(disc, dict):
        extras["disc"] = disc
    return extras


def read_state_variant(
    session: Session, *, revision_id: uuid.UUID, payload: dict | None, index: int
) -> tuple[bytes, str]:
    """The bytes and media type of option `index` of a published revision, or a 404.

    The attachment must be the revision's own, with role `option` and this
    number: a key that merely appears in the payload is not enough.
    """
    not_found = HTTPException(status_code=404, detail="Published symbol state variant was not found.")
    variant = next((asset for asset in list_state_variant_assets(payload) if asset.get("option_index") == index), None)
    if variant is None:
        raise not_found
    # `asset_role` and `option_index` are plain columns the ORM does not map.
    attachment = session.execute(
        text(
            "SELECT object_key, content_type FROM attachments "
            "WHERE object_key = :key AND parent_type = 'symbol_revision' AND parent_id = :revision "
            "AND asset_role = 'option' AND option_index = :index"
        ),
        {"key": variant["object_key"], "revision": revision_id, "index": index},
    ).first()
    if attachment is None:
        raise not_found
    stored = download_object_bytes(object_key=attachment.object_key, env_file=str(get_settings().storage_env_file))
    try:
        media_type = validate_stored_image(stored["payload"], attachment.content_type, stored.get("content_type"))
    except UnsafeImageContentError as exc:
        raise not_found from exc
    return stored["payload"], media_type
