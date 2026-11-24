"""Building the visual descriptor index over the published public Catalog.

One row per published public symbol revision and matcher version in
`catalog_symbol_visual_descriptors` (migration 20261123_0080). Mirrors the text
embedding indexer (`catalog_embeddings.py`): idempotent, applies nothing unless
asked, embeds only what is new or changed, and prunes what left the Catalog.

A row is current while the symbol's preview asset key, the renderer and the
font set are unchanged, so a reconcile pass never refetches a current symbol.
A symbol that cannot be rendered (an unsupported format, a broken file) is a
coverage gap, reported by count and reason, never given a placeholder row.
When the image embedding call fails the row is still written with the local
descriptor and no embedding (the D19 fallback), and the next pass retries it.

The index only ranks. What may be shown is decided by the live public-Catalog
rule at query time, never by a row here.
"""

from __future__ import annotations

import hashlib
import io
import struct
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from ..published_catalog import PUBLISHED_SYMBOLS_SQL, choose_published_preview_asset
from . import MATCHER_VERSION
from .descriptor import descriptor
from .imaging import normalize, pack_skeleton
from .rasterizer import RenderConfig, RenderError, render

MAX_SOURCE_BYTES = 4 * 1024 * 1024
_RENDERABLE = {"image/svg+xml", "image/png", "image/jpeg"}


@dataclass(frozen=True)
class Built:
    gemini: tuple[float, ...] | None
    gemini_model: str | None
    local: np.ndarray
    skeleton: bytes
    content_aspect: float
    source_sha256: str


@dataclass
class VisualIndexReport:
    matcher_version: str
    published: int = 0
    already_current: int = 0
    to_build: int = 0
    pending: int = 0
    built: int = 0
    degraded: int = 0           # rows written without the stage-1 embedding
    gaps: dict[str, int] = field(default_factory=dict)   # reason -> count; no row written
    pruned: int = 0


def pack_floats(values: Any) -> bytes:
    values = [float(v) for v in values]
    return struct.pack(f"<{len(values)}f", *values)


def unpack_floats(data: bytes) -> np.ndarray:
    if len(data) % 4:
        raise ValueError("Stored vector length is not a whole number of floats.")
    return np.frombuffer(data, dtype="<f4").copy()


def _png(gray: np.ndarray) -> bytes:
    out = io.BytesIO()
    Image.fromarray(gray).save(out, format="PNG")
    return out.getvalue()


def build_descriptor(
    data: bytes,
    media_type: str,
    cfg: RenderConfig,
    embed: Callable[[bytes], Mapping[str, Any]] | None,
) -> Built:
    """Render one symbol and compute its descriptors. Raises `RenderError` for a coverage gap.

    `embed(png)` returns `request_image_embedding`'s result; when it is None or raises, the row
    carries no stage-1 vector (degraded) and the local descriptor stands in.
    """
    rendered = render(data, media_type, cfg)
    n = normalize(rendered.image)
    if n is None:
        raise RenderError("The symbol has no usable line work.")
    gemini: tuple[float, ...] | None = None
    model: str | None = None
    if embed is not None:
        try:
            result = embed(_png(rendered.image))
            gemini = tuple(float(v) for v in result["vector"])
            model = str(result["model"])
        except Exception:  # noqa: BLE001 - any provider fault means "no stage-1 vector yet"
            gemini, model = None, None
    return Built(gemini, model, descriptor(n), pack_skeleton(n), float(n.content_aspect), hashlib.sha256(data).hexdigest())


def _published_rows(session: Session) -> list[Any]:
    return list(session.execute(text(PUBLISHED_SYMBOLS_SQL)).all())


def index_visual_descriptors(
    session: Session,
    *,
    cfg: RenderConfig,
    fetch: Callable[[str], bytes],
    embed: Callable[[bytes], Mapping[str, Any]] | None,
    matcher_version: str = MATCHER_VERSION,
    apply: bool = False,
    prune: bool = True,
    limit: int | None = None,
) -> VisualIndexReport:
    """Build or refresh descriptors for published public revisions.

    `fetch(object_key)` returns the stored bytes (at most `MAX_SOURCE_BYTES`; the caller enforces it).
    With `apply=False` nothing is fetched, embedded or written.
    """
    report = VisualIndexReport(matcher_version=matcher_version)
    rows = _published_rows(session)
    report.published = len(rows)
    existing = {
        str(r.rid): r
        for r in session.execute(
            text(
                "SELECT symbol_revision_id::text AS rid, asset_object_key, renderer_version, font_set_id, "
                "(gemini_embedding IS NOT NULL) AS has_embedding FROM catalog_symbol_visual_descriptors "
                "WHERE matcher_version = :v"
            ),
            {"v": matcher_version},
        ).all()
    }
    wanted: list[tuple[Any, dict[str, Any]]] = []
    for row in rows:
        asset = choose_published_preview_asset(row.payload_json if isinstance(row.payload_json, dict) else {})
        media_type = str((asset or {}).get("content_type") or "").split(";")[0].strip().lower()
        key = (asset or {}).get("object_key")
        if not key or media_type not in _RENDERABLE:
            reason = "no_preview_asset" if not key else "unsupported_format"
            report.gaps[reason] = report.gaps.get(reason, 0) + 1
            continue
        current = existing.get(str(row.symbol_revision_id))
        if (
            current is not None
            and current.asset_object_key == key
            and current.renderer_version == cfg.renderer_version
            and current.font_set_id == cfg.font_set_id
            and (current.has_embedding or embed is None)
        ):
            report.already_current += 1
            continue
        wanted.append((row, {"key": key, "media_type": media_type}))
    report.pending = len(wanted)
    if limit is not None:
        wanted = wanted[:limit]
    report.to_build = len(wanted)
    if not apply:
        return report

    for row, asset in wanted:
        try:
            data = fetch(asset["key"])
            if len(data) > MAX_SOURCE_BYTES:
                raise RenderError("The source file is too large.")
            built = build_descriptor(data, asset["media_type"], cfg, embed)
        except RenderError:
            report.gaps["render_failed"] = report.gaps.get("render_failed", 0) + 1
            report.pending -= 1
            continue
        except Exception:  # noqa: BLE001 - an unreadable object is retried next pass, not a gap
            continue
        session.execute(
            text(
                "INSERT INTO catalog_symbol_visual_descriptors (symbol_revision_id, governed_symbol_id, matcher_version, "
                "scope, embedding_model, dimensions, gemini_embedding, local_descriptor, skeleton, content_aspect, "
                "asset_object_key, source_sha256, renderer_version, font_set_id, built_at) "
                "VALUES (CAST(:rev AS uuid), CAST(:sym AS uuid), :v, 'public', :model, :dims, :gem, :local, :skel, "
                ":aspect, :key, :sha, :renderer, :fonts, :now) "
                "ON CONFLICT (symbol_revision_id, matcher_version) DO UPDATE SET "
                "governed_symbol_id = EXCLUDED.governed_symbol_id, embedding_model = EXCLUDED.embedding_model, "
                "dimensions = EXCLUDED.dimensions, gemini_embedding = EXCLUDED.gemini_embedding, "
                "local_descriptor = EXCLUDED.local_descriptor, skeleton = EXCLUDED.skeleton, "
                "content_aspect = EXCLUDED.content_aspect, asset_object_key = EXCLUDED.asset_object_key, "
                "source_sha256 = EXCLUDED.source_sha256, renderer_version = EXCLUDED.renderer_version, "
                "font_set_id = EXCLUDED.font_set_id, built_at = EXCLUDED.built_at"
            ),
            {
                "rev": str(row.symbol_revision_id), "sym": str(row.symbol_id), "v": matcher_version,
                "model": built.gemini_model, "dims": len(built.gemini) if built.gemini else None,
                "gem": pack_floats(built.gemini) if built.gemini else None,
                "local": pack_floats(built.local), "skel": built.skeleton, "aspect": built.content_aspect,
                "key": asset["key"], "sha": built.source_sha256, "renderer": cfg.renderer_version,
                "fonts": cfg.font_set_id, "now": datetime.now(timezone.utc),
            },
        )
        session.commit()
        report.built += 1
        report.pending -= 1
        if built.gemini is None and embed is not None:
            report.degraded += 1

    if prune:
        keep = [uuid.UUID(str(r.symbol_revision_id)) for r in rows]
        sql = "DELETE FROM catalog_symbol_visual_descriptors WHERE matcher_version = :v"
        params: dict[str, Any] = {"v": matcher_version}
        statement = text(sql + (" AND symbol_revision_id NOT IN :keep" if keep else ""))
        if keep:
            statement = statement.bindparams(bindparam("keep", expanding=True, type_=UUID(as_uuid=True)))
            params["keep"] = keep
        report.pruned = int(session.execute(statement, params).rowcount or 0)
        session.commit()
    return report


def index_status(session: Session, *, cfg: RenderConfig, matcher_version: str = MATCHER_VERSION) -> dict[str, Any]:
    """Coverage for the incomplete-index (503) check and the freshness metrics."""
    report = index_visual_descriptors(
        session, cfg=cfg, fetch=lambda key: b"", embed=None, matcher_version=matcher_version, apply=False
    )
    row = session.execute(
        text(
            "SELECT count(*) AS n, count(gemini_embedding) AS embedded, max(built_at) AS latest "
            "FROM catalog_symbol_visual_descriptors WHERE matcher_version = :v"
        ),
        {"v": matcher_version},
    ).one()
    return {
        "matcherVersion": matcher_version, "published": report.published, "indexed": int(row.n),
        "withEmbedding": int(row.embedded), "current": report.already_current,
        "needsBuild": report.to_build, "gaps": dict(report.gaps), "latestBuiltAt": row.latest,
    }
