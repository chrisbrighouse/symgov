"""A meaning-based index over the published public Catalog.

One vector per currently published public symbol revision, built from the
words a person would search by: name, category, discipline, summary, keywords,
pack and the Catalog's own filter labels. Private symbols are never indexed.

The index only ranks. Whether a symbol may be shown is decided by the live
public-Catalog rule (`PUBLISHED_SYMBOLS_SQL`) every time, so a stale or
superseded vector cannot make anything visible. Vectors are stored as
little-endian float32 bytes, unit length, in `catalog_symbol_embeddings`
(migration 20261010_0071); similarity is a dot product, computed with numpy
when it is installed and in plain Python otherwise.
"""

from __future__ import annotations

import hashlib
import struct
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from .published_catalog import PUBLISHED_SYMBOLS_SQL

try:  # numpy is optional: it only makes scoring faster.
    import numpy as _np
except ImportError:  # pragma: no cover - exercised where numpy is absent
    _np = None


MAX_TOP_K = 100
_CACHE_SECONDS = 60.0
_MAX_FIELD_CHARS = 600
_MAX_KEYWORDS = 12
_FACET_COLUMNS = {
    "catalogDisciplines": "disciplines",
    "catalogCategories": "categories",
    "useCases": "use_cases",
    "availableFormats": "formats",
}


def _clean(value: Any, limit: int = _MAX_FIELD_CHARS) -> str:
    if not isinstance(value, str):
        return ""
    return " ".join(value.split())[:limit]


def _words(values: Any) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [text_value for item in values if (text_value := _clean(item, 60))]


def embedding_text(
    *,
    name: str,
    category: str,
    discipline: str,
    payload: Mapping[str, Any] | None,
    pack_title: str | None,
    facets: Mapping[str, Sequence[str]] | None = None,
) -> str:
    """The text that stands for one symbol, in plain sentences.

    Only descriptive fields. No identifiers, URLs, people or storage keys.
    """
    payload = payload or {}
    facets = facets or {}
    parts = [f"Symbol: {_clean(name)}."]
    summary = _clean(payload.get("summary") or payload.get("description"))
    if summary:
        parts.append(f"Description: {summary}")
    categories = _words(list(facets.get("categories", ()))) or ([_clean(category)] if _clean(category) else [])
    disciplines = _words(list(facets.get("disciplines", ()))) or ([_clean(discipline)] if _clean(discipline) else [])
    if categories:
        parts.append(f"Category: {', '.join(categories)}.")
    if disciplines:
        parts.append(f"Discipline: {', '.join(disciplines)}.")
    use_cases = _words(list(facets.get("use_cases", ())))
    if use_cases:
        parts.append(f"Used for: {', '.join(use_cases)}.")
    keywords = _words(payload.get("keywords") or payload.get("search_terms"))[:_MAX_KEYWORDS]
    if keywords:
        parts.append(f"Keywords: {', '.join(keywords)}.")
    pack = _clean(pack_title or "")
    if pack:
        parts.append(f"Pack: {pack}.")
    return " ".join(parts)


def content_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def pack_vector(vector: Sequence[float]) -> bytes:
    return struct.pack(f"<{len(vector)}f", *vector)


def unpack_vector(data: bytes, dimensions: int) -> tuple[float, ...]:
    if len(data) != dimensions * 4:
        raise ValueError("Stored vector length does not match its dimensions.")
    return struct.unpack(f"<{dimensions}f", data)


# --- Building the index ------------------------------------------------------


@dataclass
class IndexReport:
    model: str
    published: int = 0
    already_current: int = 0
    to_embed: int = 0
    embedded: int = 0
    pruned: int = 0
    dimensions: int | None = None
    input_tokens: int = 0
    failed_batches: int = 0
    sample: list[str] = field(default_factory=list)


def _published_rows(session: Session) -> list[Any]:
    return list(session.execute(text(PUBLISHED_SYMBOLS_SQL)).all())


def _facet_labels(session: Session, revision_ids: Sequence[uuid.UUID]) -> dict[str, dict[str, list[str]]]:
    if not revision_ids:
        return {}
    statement = text(
        "SELECT symbol_revision_id::text AS rid, disciplines, categories, use_cases "
        "FROM catalog_symbol_facets WHERE symbol_revision_id IN :ids"
    ).bindparams(bindparam("ids", expanding=True, type_=UUID(as_uuid=True)))
    return {
        row.rid: {
            "disciplines": list(row.disciplines or []),
            "categories": list(row.categories or []),
            "use_cases": list(row.use_cases or []),
        }
        for row in session.execute(statement, {"ids": list(revision_ids)}).all()
    }


def index_public_symbols(
    session: Session,
    *,
    model: str,
    embed: Callable[[list[str]], Mapping[str, Any]],
    apply: bool = False,
    batch_size: int = 32,
    prune: bool = True,
    limit: int | None = None,
) -> IndexReport:
    """Embed every published public revision whose text is new or changed.

    `embed(texts)` returns the `request_llm_embeddings` result. With
    `apply=False` nothing is embedded or written and the report says what
    would be. Idempotent: a second run finds nothing to do.
    """
    report = IndexReport(model=model)
    rows = _published_rows(session)
    report.published = len(rows)
    revision_ids = [uuid.UUID(str(row.symbol_revision_id)) for row in rows]
    facets = _facet_labels(session, revision_ids)
    existing = {
        str(row.rid): row.content_hash
        for row in session.execute(
            text("SELECT symbol_revision_id::text AS rid, content_hash FROM catalog_symbol_embeddings WHERE model = :model"),
            {"model": model},
        ).all()
    }

    wanted: list[tuple[Any, str, str]] = []
    for row in rows:
        payload = row.payload_json if isinstance(row.payload_json, dict) else {}
        body = embedding_text(
            name=str(payload.get("name") or payload.get("canonical_name") or row.canonical_name),
            category=str(row.category or ""),
            discipline=str(row.discipline or ""),
            payload=payload,
            pack_title=row.pack_title,
            facets=facets.get(str(row.symbol_revision_id)),
        )
        digest = content_hash(body)
        if existing.get(str(row.symbol_revision_id)) == digest:
            report.already_current += 1
        else:
            wanted.append((row, body, digest))
    if limit is not None:
        wanted = wanted[:limit]
    report.to_embed = len(wanted)
    report.sample = [body for _row, body, _digest in wanted[:3]]
    if not apply:
        return report

    for start in range(0, len(wanted), max(1, batch_size)):
        batch = wanted[start : start + max(1, batch_size)]
        try:
            result = embed([body for _row, body, _digest in batch])
        except Exception:
            report.failed_batches += 1
            continue
        vectors = result["vectors"]
        dimensions = int(result["dimensions"])
        if report.dimensions not in (None, dimensions):
            raise RuntimeError("The embedding model changed its vector size part way through an index run.")
        report.dimensions = dimensions
        usage = result.get("usage") or {}
        tokens = usage.get("prompt_tokens") if isinstance(usage, Mapping) else None
        report.input_tokens += tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else 0
        now = datetime.now(timezone.utc)
        for (row, _body, digest), vector in zip(batch, vectors, strict=True):
            session.execute(
                text(
                    "INSERT INTO catalog_symbol_embeddings "
                    "(symbol_revision_id, governed_symbol_id, model, dimensions, content_hash, embedding, embedded_at) "
                    "VALUES (CAST(:revision AS uuid), CAST(:symbol AS uuid), :model, :dimensions, :hash, :embedding, :now) "
                    "ON CONFLICT (symbol_revision_id, model) DO UPDATE SET "
                    "governed_symbol_id = EXCLUDED.governed_symbol_id, dimensions = EXCLUDED.dimensions, "
                    "content_hash = EXCLUDED.content_hash, embedding = EXCLUDED.embedding, "
                    "embedded_at = EXCLUDED.embedded_at"
                ),
                {
                    "revision": str(row.symbol_revision_id),
                    "symbol": str(row.symbol_id),
                    "model": model,
                    "dimensions": dimensions,
                    "hash": digest,
                    "embedding": pack_vector(vector),
                    "now": now,
                },
            )
        session.commit()
        report.embedded += len(batch)

    if prune and report.failed_batches == 0 and limit is None:
        current = [str(value) for value in revision_ids]
        statement = text(
            "DELETE FROM catalog_symbol_embeddings WHERE model = :model "
            "AND symbol_revision_id NOT IN :keep"
        ).bindparams(bindparam("keep", expanding=True, type_=UUID(as_uuid=True)))
        if current:
            result_rows = session.execute(statement, {"model": model, "keep": [uuid.UUID(v) for v in current]})
            report.pruned = int(result_rows.rowcount or 0)
        session.commit()
    return report


# --- Searching the index -----------------------------------------------------


@dataclass(frozen=True)
class SemanticHit:
    symbol_revision_id: uuid.UUID
    governed_symbol_id: uuid.UUID
    similarity: float


@dataclass
class _Loaded:
    stamp: tuple[Any, ...]
    loaded_at: float
    dimensions: int
    revision_ids: list[uuid.UUID]
    symbol_ids: list[uuid.UUID]
    matrix: Any  # numpy array, or a list of tuples without numpy


_CACHE: dict[str, _Loaded] = {}
_CACHE_LOCK = threading.Lock()


def clear_index_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _load_index(session: Session, model: str) -> _Loaded | None:
    stamp_row = session.execute(
        text("SELECT count(*) AS n, max(embedded_at) AS latest FROM catalog_symbol_embeddings WHERE model = :model"),
        {"model": model},
    ).one()
    stamp = (int(stamp_row.n), stamp_row.latest)
    if stamp[0] == 0:
        return None
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(model)
        if cached is not None and cached.stamp == stamp and now - cached.loaded_at < _CACHE_SECONDS:
            return cached
    rows = session.execute(
        text(
            "SELECT symbol_revision_id, governed_symbol_id, dimensions, embedding "
            "FROM catalog_symbol_embeddings WHERE model = :model ORDER BY symbol_revision_id"
        ),
        {"model": model},
    ).all()
    dimensions = int(rows[0].dimensions)
    usable = [row for row in rows if int(row.dimensions) == dimensions]
    vectors = [unpack_vector(bytes(row.embedding), dimensions) for row in usable]
    matrix: Any = _np.array(vectors, dtype=_np.float32) if _np is not None else vectors
    loaded = _Loaded(
        stamp=stamp,
        loaded_at=now,
        dimensions=dimensions,
        revision_ids=[uuid.UUID(str(row.symbol_revision_id)) for row in usable],
        symbol_ids=[uuid.UUID(str(row.governed_symbol_id)) for row in usable],
        matrix=matrix,
    )
    with _CACHE_LOCK:
        _CACHE[model] = loaded
    return loaded


def _scores(loaded: _Loaded, query: Sequence[float]) -> list[float]:
    if _np is not None:
        return [float(value) for value in loaded.matrix @ _np.array(query, dtype=_np.float32)]
    return [sum(a * b for a, b in zip(vector, query)) for vector in loaded.matrix]


def semantic_candidates(
    session: Session,
    query_vector: Sequence[float],
    *,
    model: str,
    min_similarity: float,
    top_k: int = 40,
) -> list[SemanticHit]:
    """The nearest indexed revisions at or above `min_similarity`, best first.

    These are candidates only: the caller must pass them through
    `public_visible_revision_ids` before showing anything.
    """
    loaded = _load_index(session, model)
    if loaded is None or len(query_vector) != loaded.dimensions:
        return []
    scored = sorted(
        (
            (score, index)
            for index, score in enumerate(_scores(loaded, query_vector))
            if score >= min_similarity
        ),
        reverse=True,
    )[: max(1, min(top_k, MAX_TOP_K))]
    return [
        SemanticHit(loaded.revision_ids[index], loaded.symbol_ids[index], round(score, 4))
        for score, index in scored
    ]


def public_visible_revision_ids(session: Session, revision_ids: Sequence[uuid.UUID]) -> set[uuid.UUID]:
    """Those revisions the public Catalog shows right now, by its own rule."""
    if not revision_ids:
        return set()
    statement = text(PUBLISHED_SYMBOLS_SQL + " AND sr.id IN :ids").bindparams(
        bindparam("ids", expanding=True, type_=UUID(as_uuid=True))
    )
    return {
        uuid.UUID(str(row.symbol_revision_id))
        for row in session.execute(statement, {"ids": list(revision_ids)}).all()
    }


def revisions_matching_facets(
    session: Session, revision_ids: Sequence[uuid.UUID], facets: Mapping[str, Sequence[str]]
) -> set[uuid.UUID]:
    """The revisions whose stored filter labels satisfy every given facet.

    Same rule as the Catalog search: any of the values within a facet, all of
    the facets together. Facets the index does not know are ignored by the
    caller before it gets here.
    """
    if not revision_ids:
        return set()
    clauses = []
    params: dict[str, Any] = {"ids": list(revision_ids)}
    for key, values in facets.items():
        column = _FACET_COLUMNS.get(key)
        selected = [str(value) for value in values if str(value).strip()]
        if column is None or not selected:
            continue
        params[f"facet_{column}"] = selected
        clauses.append(f"{column} ?| CAST(:facet_{column} AS text[])")
    sql = "SELECT symbol_revision_id FROM catalog_symbol_facets WHERE symbol_revision_id IN :ids"
    if clauses:
        sql += " AND " + " AND ".join(clauses)
    statement = text(sql).bindparams(bindparam("ids", expanding=True, type_=UUID(as_uuid=True)))
    return {uuid.UUID(str(row.symbol_revision_id)) for row in session.execute(statement, params).all()}


def probe_nearest(
    session: Session, query_vector: Sequence[float], *, model: str, top: int = 10
) -> list[dict[str, Any]]:
    """The nearest public symbols with their scores, with no similarity floor.

    For calibrating `SYMGOV_ED_SEMANTIC_MIN_SIMILARITY`: shows where real
    matches stop and noise starts. Public Catalog data only.
    """
    hits = semantic_candidates(session, query_vector, model=model, min_similarity=-1.0, top_k=top * 3)
    visible = public_visible_revision_ids(session, [hit.symbol_revision_id for hit in hits])
    hits = [hit for hit in hits if hit.symbol_revision_id in visible][:top]
    if not hits:
        return []
    statement = text(
        "SELECT id::text AS gid, catalog_symbol_id, canonical_name FROM governed_symbols WHERE id IN :ids"
    ).bindparams(bindparam("ids", expanding=True, type_=UUID(as_uuid=True)))
    names = {
        row.gid: (row.catalog_symbol_id, row.canonical_name)
        for row in session.execute(statement, {"ids": [hit.governed_symbol_id for hit in hits]}).all()
    }
    return [
        {
            "displayId": names.get(str(hit.governed_symbol_id), ("", ""))[0],
            "name": names.get(str(hit.governed_symbol_id), ("", ""))[1],
            "similarity": hit.similarity,
        }
        for hit in hits
    ]


def index_status(session: Session, *, model: str) -> dict[str, Any]:
    """What is published, what is indexed for `model`, and how much is stale."""
    report = index_public_symbols(session, model=model, embed=lambda texts: {}, apply=False)
    indexed = session.execute(
        text("SELECT count(*) AS n, max(dimensions) AS d FROM catalog_symbol_embeddings WHERE model = :model"),
        {"model": model},
    ).one()
    return {
        "model": model,
        "published": report.published,
        "indexed": int(indexed.n),
        "dimensions": int(indexed.d) if indexed.d is not None else None,
        "current": report.already_current,
        "needsEmbedding": report.to_embed,
    }
