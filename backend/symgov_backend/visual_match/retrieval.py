"""Visual match retrieval over the Catalog (design rev 4, WP3; private symbols D5, 2026-11-25).

Order of work, which is the order of the safety rules:

1. Decode and normalise the query image (input errors are 4xx, never a "no match").
2. Eligibility BEFORE ranking: the live public-Catalog rule (`PUBLISHED_SYMBOLS_SQL`, the same query the
   Catalog uses, so there is no second visibility rule), plus, for an organization session, the Catalog
   tab's rule for that organization's own organization-wide private symbols, intersected with the
   structured filters. An index row never makes anything visible; it only ranks.
3. Stage 1: the query and its three quarter turns are embedded and the best cosine per symbol shortlists
   `shortlist` candidates. If any embedding call fails the local descriptor takes over (D19) and the result
   says `degraded`.
4. Stage 2: the frozen exact rerank, then interchangeable-symbol grouping.
5. A final visibility recheck of everything about to be returned.

Outcome: ranked groups with a band, `likely` / `possible` / `weak`. Bands are provisional agreement rules,
never probabilities, and there is no "no match" verdict (the benchmark does not support one). Nothing about
the query image is stored or logged here.

Private rows (scope 'organization_private', migration 20261125_0081) are read fresh for the one organization
on every query and never enter the shared cache, which holds public rows only. Without an organization
(personal sessions, API keys) nothing private is read. Public coverage below `MIN_COVERAGE` is a 503; a
private symbol not indexed yet (the worker runs every few minutes) is only a `private_symbols_pending`
warning.
"""

from __future__ import annotations

import io
import logging
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from PIL import Image
from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Session

from ..catalog_embeddings import _FACET_COLUMNS, public_visible_revision_ids, revisions_matching_facets
from ..published_catalog import ORGANIZATION_WIDE_PRIVATE_PREDICATE_SQL, PUBLISHED_SYMBOLS_SQL
from . import MATCHER_VERSION
from .descriptor import cosine_shortlist, descriptor
from .errors import VisualMatchInputError, VisualMatchUnavailable
from .grouping import group_members
from .imaging import SIZE, SIZE_FINE, Normalized, decode_gray, from_skeleton, normalize, rotated
from .index import unpack_floats
from .rerank import rerank

logger = logging.getLogger(__name__)

SHORTLIST = 300
RERANK_TOP = 50
GROUPED = 20                      # reranked symbols that are grouped and returned
QUERY_LONG_SIDE = 512             # the long side candidates are rendered at, so the embedder sees like with like
MIN_COVERAGE = 0.8                # indexed / eligible-to-index below this is a 503
_CACHE_SECONDS = 60.0


@dataclass(frozen=True)
class BandPolicy:
    """Provisional band rules (Gate C validates them on a fresh held-out set).

    `likely`: the first group holds both the stage-1 embedding's top symbol and the local descriptor's top
    symbol (two independent descriptors agree) and the top cosine is at least `likely_cosine_floor`
    (the benchmark's 10% false-accept tune threshold). `possible`: the first `possible_groups` groups.
    Everything else is `weak`. In degraded mode (no embedding) nothing is `likely`.
    """

    likely_cosine_floor: float = 0.9407
    possible_groups: int = 5


@dataclass(frozen=True)
class Member:
    revision_id: uuid.UUID
    symbol_id: uuid.UUID
    score: float
    chamfer: float
    rotation: int
    stage1_rank: int
    cosine: float
    private: bool = False


@dataclass(frozen=True)
class Group:
    rank: int
    band: str
    members: list[Member]


@dataclass
class VisualMatchResult:
    groups: list[Group]
    degraded: bool
    candidates_considered: int
    eligible: int
    coverage: float
    stronger_match_outside_filters: bool = False
    warnings: list[str] = field(default_factory=list)
    private_pending: int = 0      # the organization's private symbols not indexed yet


@dataclass
class _Index:
    stamp: tuple[Any, ...]
    loaded_at: float
    revision_ids: list[uuid.UUID]
    symbol_ids: list[uuid.UUID]
    gemini: np.ndarray            # (n, d) float32; zero rows where there is no embedding
    has_gemini: np.ndarray        # (n,) bool
    local: np.ndarray             # (n, 360) float32
    skeleton: list[bytes]
    fine: list[bytes]
    aspect: list[float]
    private: np.ndarray | None = None   # (n,) bool; None means every row is public
    norm128: dict[int, Normalized] = field(default_factory=dict)
    norm256: dict[int, Normalized] = field(default_factory=dict)

    def n128(self, i: int) -> Normalized:
        if i not in self.norm128:
            self.norm128[i] = from_skeleton(self.skeleton[i], self.aspect[i], SIZE)
        return self.norm128[i]

    def n256(self, i: int) -> Normalized:
        if i not in self.norm256:
            self.norm256[i] = from_skeleton(self.fine[i], self.aspect[i], SIZE_FINE)
        return self.norm256[i]


_CACHE: dict[str, _Index] = {}
_CACHE_LOCK = threading.Lock()


def clear_index_cache() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _load_index(session: Session, matcher_version: str) -> _Index | None:
    stamp_row = session.execute(
        text("SELECT count(*) AS n, max(built_at) AS latest FROM catalog_symbol_visual_descriptors "
             "WHERE matcher_version = :v AND scope = 'public'"),
        {"v": matcher_version},
    ).one()
    stamp = (int(stamp_row.n), stamp_row.latest)
    if stamp[0] == 0:
        return None
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(matcher_version)
        if cached is not None and cached.stamp == stamp and now - cached.loaded_at < _CACHE_SECONDS:
            return cached
    rows = session.execute(
        text("SELECT symbol_revision_id, governed_symbol_id, dimensions, gemini_embedding, local_descriptor, "
             "skeleton, skeleton_fine, content_aspect FROM catalog_symbol_visual_descriptors "
             "WHERE matcher_version = :v AND scope = 'public' ORDER BY symbol_revision_id"),
        {"v": matcher_version},
    ).all()
    dims = max((int(r.dimensions) for r in rows if r.dimensions), default=0)
    loaded = _from_rows(rows, stamp=stamp, loaded_at=now, dims=dims)
    with _CACHE_LOCK:
        _CACHE[matcher_version] = loaded
    return loaded


def _from_rows(rows: Sequence[Any], *, stamp: tuple[Any, ...], loaded_at: float, dims: int) -> _Index:
    gem = np.zeros((len(rows), dims), np.float32)
    has = np.zeros(len(rows), bool)
    for i, r in enumerate(rows):
        if r.gemini_embedding is not None and r.dimensions and int(r.dimensions) == dims:
            gem[i] = unpack_floats(bytes(r.gemini_embedding))
            has[i] = True
    return _Index(
        stamp=stamp, loaded_at=loaded_at,
        revision_ids=[uuid.UUID(str(r.symbol_revision_id)) for r in rows],
        symbol_ids=[uuid.UUID(str(r.governed_symbol_id)) for r in rows],
        gemini=gem, has_gemini=has,
        local=np.stack([unpack_floats(bytes(r.local_descriptor)) for r in rows]),
        skeleton=[bytes(r.skeleton) for r in rows], fine=[bytes(r.skeleton_fine) for r in rows],
        aspect=[float(r.content_aspect) for r in rows],
    )


def _with_private(session: Session, public: _Index, matcher_version: str, organization_id: uuid.UUID) -> _Index:
    """The public index plus one organization's private rows, as a new object for this query only.

    The shared cache is never written: private rows are appended after the public ones in fresh arrays,
    and the decoded-skeleton caches start as copies of the public ones.
    """
    rows = session.execute(
        text("SELECT symbol_revision_id, governed_symbol_id, dimensions, gemini_embedding, local_descriptor, "
             "skeleton, skeleton_fine, content_aspect FROM catalog_symbol_visual_descriptors "
             "WHERE matcher_version = :v AND scope = 'organization_private' AND owner_organization_id = :org "
             "ORDER BY symbol_revision_id"),
        {"v": matcher_version, "org": organization_id},
    ).all()
    if not rows:
        return public
    private = _from_rows(rows, stamp=public.stamp, loaded_at=public.loaded_at, dims=public.gemini.shape[1])
    flags = np.concatenate([np.zeros(len(public.revision_ids), bool), np.ones(len(rows), bool)])
    return _Index(
        stamp=public.stamp, loaded_at=public.loaded_at,
        revision_ids=public.revision_ids + private.revision_ids,
        symbol_ids=public.symbol_ids + private.symbol_ids,
        gemini=np.concatenate([public.gemini, private.gemini]),
        has_gemini=np.concatenate([public.has_gemini, private.has_gemini]),
        local=np.concatenate([public.local, private.local]),
        skeleton=public.skeleton + private.skeleton, fine=public.fine + private.fine,
        aspect=public.aspect + private.aspect, private=flags,
        norm128=dict(public.norm128), norm256=dict(public.norm256),
    )


def _visible_ids(session: Session) -> set[uuid.UUID]:
    rows = session.execute(text("SELECT x.symbol_revision_id FROM (" + PUBLISHED_SYMBOLS_SQL + ") x"))
    return {uuid.UUID(str(r.symbol_revision_id)) for r in rows}


def private_visible_revision_ids(
    session: Session, organization_id: uuid.UUID, revision_ids: Sequence[uuid.UUID] | None = None
) -> set[uuid.UUID]:
    """The organization's own private revisions its Catalog tab lists right now, by the Catalog's rule."""
    sql = (
        "SELECT sr.id FROM governed_symbols gs JOIN symbol_revisions sr ON sr.id = gs.current_revision_id "
        f"WHERE gs.owner_organization_id = :org AND {ORGANIZATION_WIDE_PRIVATE_PREDICATE_SQL}"
    )
    params: dict[str, Any] = {"org": organization_id}
    if revision_ids is not None:
        if not revision_ids:
            return set()
        sql += " AND sr.id IN :ids"
        params["ids"] = list(revision_ids)
        statement = text(sql).bindparams(bindparam("ids", expanding=True, type_=UUID(as_uuid=True)))
    else:
        statement = text(sql)
    return {uuid.UUID(str(r.id)) for r in session.execute(statement, params).all()}


def _fill_missing_facets(session: Session, revision_ids: Sequence[uuid.UUID]) -> None:
    """Store the filter labels of private revisions that have none yet, as the Catalog search does.

    A private symbol approved since the last Catalog search or backfill has no facet row, and every
    filter would otherwise drop it. Public revisions are filled at deploy time.
    """
    if not revision_ids:
        return
    statement = text(
        "SELECT x.id FROM unnest(CAST(:ids AS uuid[])) AS x(id) "
        "WHERE NOT EXISTS (SELECT 1 FROM catalog_symbol_facets f WHERE f.symbol_revision_id = x.id)"
    )
    missing = [r.id for r in session.execute(statement, {"ids": [str(i) for i in revision_ids]}).all()]
    if missing:
        from ..catalog_facets import compute_catalog_facets

        compute_catalog_facets(session, missing)
        session.commit()


def validate_filters(filters: Mapping[str, Sequence[str]] | None) -> dict[str, list[str]]:
    clean: dict[str, list[str]] = {}
    for key, values in (filters or {}).items():
        if key not in _FACET_COLUMNS:
            raise VisualMatchInputError("invalid_filter", f"Unknown filter {key!r}.")
        if isinstance(values, str) or not isinstance(values, Sequence) or len(values) > 50:
            raise VisualMatchInputError("invalid_filter", f"Filter {key!r} must be a short list of values.")
        selected = [str(v).strip() for v in values if str(v).strip()]
        if any(len(v) > 120 for v in selected):
            raise VisualMatchInputError("invalid_filter", f"Filter {key!r} has a value that is too long.")
        if selected:
            clean[key] = selected
    return clean


def _query_png(gray: np.ndarray, quarter_turns: int) -> bytes:
    img = Image.fromarray(np.ascontiguousarray(np.rot90(gray, quarter_turns)))
    scale = QUERY_LONG_SIDE / max(img.size)
    if scale != 1.0:
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="PNG")
    return out.getvalue()


def _embed_rotations(gray: np.ndarray, embed: Callable[[bytes], Mapping[str, Any]]) -> list[np.ndarray] | None:
    """The four rotation embeddings, or None if any call fails (the caller falls back to D19)."""
    pngs = [_query_png(gray, k) for k in range(4)]
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(embed, pngs))
        return [np.asarray(r["vector"], np.float32) for r in results]
    except Exception as exc:  # noqa: BLE001 - the provider detail never leaves this module
        # The caller degrades silently, so this line is the only record of why: class and our own message
        # (`request_image_embedding` raises with no provider detail), never the image.
        logger.warning(
            "visual match query embedding failed; using the local descriptor error_type=%s error=%s",
            type(exc).__name__, str(exc)[:200],
        )
        return None


def assign_bands(
    groups: list[list[int]], *, gemini_top: int | None, local_top: int | None, top_cosine: float,
    degraded: bool, policy: BandPolicy,
) -> list[str]:
    """One band per group (parallel to `groups`, best first)."""
    bands = ["possible" if rank < policy.possible_groups else "weak" for rank in range(len(groups))]
    if (
        not degraded and groups and gemini_top is not None and local_top is not None
        and gemini_top in groups[0] and local_top in groups[0] and top_cosine >= policy.likely_cosine_floor
    ):
        bands[0] = "likely"
    return bands


def visual_match(
    session: Session,
    *,
    image: bytes,
    embed: Callable[[bytes], Mapping[str, Any]] | None,
    filters: Mapping[str, Sequence[str]] | None = None,
    matcher_version: str = MATCHER_VERSION,
    shortlist: int = SHORTLIST,
    policy: BandPolicy = BandPolicy(),
    min_coverage: float = MIN_COVERAGE,
    max_groups: int = 10,
    organization_id: uuid.UUID | None = None,
) -> VisualMatchResult:
    """Rank the Catalog against the image. `organization_id` adds that organization's own private symbols;
    the caller passes it only for a signed-in organization session."""
    filters = validate_filters(filters)
    gray = decode_gray(image)
    query = normalize(gray)
    if query is None:
        raise VisualMatchInputError("no_line_work", "The image has no usable line work.")

    index = _load_index(session, matcher_version)
    visible = _visible_ids(session)
    if index is None or not visible:
        raise VisualMatchUnavailable("index_empty", "The visual index has not been built yet.")
    coverage = sum(1 for rid in index.revision_ids if rid in visible) / len(visible)
    if coverage < min_coverage:
        raise VisualMatchUnavailable("index_incomplete", "The visual index is not complete enough to answer.")
    private_pending = 0
    if organization_id is not None:
        private_visible = private_visible_revision_ids(session, organization_id)
        if private_visible:
            index = _with_private(session, index, matcher_version, organization_id)
            visible = visible | private_visible
            private_pending = len(private_visible - set(index.revision_ids))
    positions = {rid: i for i, rid in enumerate(index.revision_ids)}
    indexed_visible = [rid for rid in index.revision_ids if rid in visible]

    # Eligibility before ranking: the structured filters narrow the visible set; they are never applied after.
    eligible_ids = set(indexed_visible)
    if filters:
        if index.private is not None:
            _fill_missing_facets(session, [index.revision_ids[p] for p in np.flatnonzero(index.private)])
        eligible_ids = revisions_matching_facets(session, list(eligible_ids), filters)
    eligible = np.array(sorted(positions[r] for r in eligible_ids), dtype=int)
    warnings: list[str] = ["private_symbols_pending"] if private_pending else []
    if eligible.size == 0:
        return VisualMatchResult([], False, 0, 0, round(coverage, 4), warnings=warnings, private_pending=private_pending)
    rotations = [rotated(query, k) for k in range(4)]
    local_q = [descriptor(r) for r in rotations]
    vectors = _embed_rotations(gray, embed) if embed is not None else None
    degraded = vectors is None
    if degraded:
        warnings.append("degraded")
    use_gemini = not degraded
    pool = eligible[index.has_gemini[eligible]] if use_gemini else eligible
    if use_gemini and pool.size < eligible.size:
        warnings.append("some_symbols_not_embedded")
    if pool.size == 0:
        pool, use_gemini, degraded = eligible, False, True
        if "degraded" not in warnings:
            warnings.append("degraded")

    k = min(shortlist, int(pool.size))
    matrix = index.gemini[pool] if use_gemini else index.local[pool]
    q_vecs = vectors if use_gemini else local_q
    order, cos = cosine_shortlist(q_vecs, matrix, k)
    short = [int(pool[o]) for o in order]
    cosines = {pos: float(c) for pos, c in zip(short, cos)}

    # Independent second descriptor, for the agreement test behind `likely`.
    local_order, _ = cosine_shortlist(local_q, index.local[eligible], 1)
    local_top = int(eligible[int(local_order[0])])
    gemini_top = short[0] if use_gemini else None

    ranked = rerank(query, [index.n128(p) for p in short], top=RERANK_TOP)[:GROUPED]
    members_pos = [short[r.index] for r in ranked]
    group_input = list(members_pos)
    if local_top not in group_input:
        group_input.append(local_top)
    local_idx = group_input.index(local_top)
    gemini_idx = group_input.index(gemini_top) if gemini_top in group_input else None
    raw = group_members([(index.n128(p), index.n256(p)) for p in group_input])
    # `raw` is ordered by first-seen; the reranked order is the order of group_input, so it is rank order.
    bands = assign_bands(
        raw, gemini_top=gemini_idx, local_top=local_idx, top_cosine=cosines[short[0]] if use_gemini else 0.0,
        degraded=degraded, policy=policy,
    )

    # Final recheck: nothing is returned that the Catalog does not show this caller right now.
    returning = [index.revision_ids[p] for p in group_input]
    still_visible = public_visible_revision_ids(session, returning)
    if organization_id is not None and index.private is not None:
        still_visible |= private_visible_revision_ids(
            session, organization_id, [index.revision_ids[p] for p in group_input if index.private[p]]
        )
    by_pos = {r_pos: r for r_pos, r in zip(members_pos, ranked)}
    groups: list[Group] = []
    for rank, (indices, band) in enumerate(zip(raw, bands), start=1):
        members = []
        for gi in indices:
            pos = group_input[gi]
            r = by_pos.get(pos)
            if r is None or index.revision_ids[pos] not in still_visible:
                continue
            members.append(Member(index.revision_ids[pos], index.symbol_ids[pos], r.score, r.chamfer,
                                  r.rotation, r.index + 1, round(cosines.get(pos, 0.0), 4),
                                  private=bool(index.private is not None and index.private[pos])))
        if members:
            groups.append(Group(len(groups) + 1, band, members))

    outside = False
    if filters and use_gemini:
        everyone = np.array(sorted(positions[r] for r in indexed_visible), dtype=int)
        everyone = everyone[index.has_gemini[everyone]]
        if everyone.size:
            top_all, c_all = cosine_shortlist(vectors, index.gemini[everyone], 1)
            outside = bool(int(everyone[int(top_all[0])]) not in set(eligible.tolist()) and float(c_all[0]) >= cosines[short[0]])

    return VisualMatchResult(
        groups=groups[:max_groups], degraded=degraded, candidates_considered=len(short),
        eligible=int(eligible.size), coverage=round(coverage, 4),
        stronger_match_outside_filters=outside, warnings=warnings, private_pending=private_pending,
    )
