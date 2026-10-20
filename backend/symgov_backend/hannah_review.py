"""The steward's side of Hannah's concept photos (WP-H4).

The Workspace Curation tab lists concept targets with their proposed and
approved photos, and a steward:

- approves a photo: Hannah fetches a display copy (at most 1,280 px wide),
  checks its bytes are an allowed image, stores it under
  `hannah/concept-photos/<concept code>/<photo id>.<ext>`, and marks it
  approved. A concept holds at most two;
- rejects a candidate or proposal, which is then never proposed again;
- removes an approved photo, which deletes the stored copy;
- corrects a target: whether it can be photographed, and its search terms.

With automatic approval on (WP-H6), Hannah approves her own proposals that
score at or above the calibrated threshold through the same path, recorded as
`decided_by_agent = 'hannah'` with no user; a steward can still remove them.

After every decision the concept's open candidates are re-ranked, so the next
best is proposed when a slot frees, and the target's status follows. Each
decision writes an audit event naming the steward. Storage and HTTP are
injected so tests need neither.
"""

from __future__ import annotations

from datetime import datetime, timezone
import mimetypes
import re
from typing import Any, Callable
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .hannah_photos import PHOTOS_PER_CONCEPT, SEARCHED_COOLDOWN, rank_and_propose
from .hannah_targets import ConceptScope, readable_name
from .image_content import validate_stored_image
from .models.schema import AuditEvent, HannahConceptPhoto, HannahConceptTarget


GRID_THUMB_WIDTH = 500
DISPLAY_WIDTH = 1280
PHOTOGRAPHABLE_VALUES = ("yes", "no", "unsure")
TARGET_STATUSES = ("pending", "searched", "no_candidates", "has_proposals", "has_photos", "excluded")
PHOTO_STATUSES = ("candidate", "filtered", "proposed", "approved", "rejected", "removed")
DECISIONS = {
    "approve": ("candidate", "proposed", "rejected", "removed"),
    "reject": ("candidate", "proposed"),
    "remove": ("approved",),
}
PAST_TENSE = {"approve": "approved", "reject": "rejected", "remove": "removed"}
_COMMONS_ORIGINAL = re.compile(r"^(https://upload\.wikimedia\.org/wikipedia/commons)/([0-9a-f])/([0-9a-f]{2})/([^/?#]+)$")

FetchBytesFn = Callable[[str], tuple[bytes, str]]
StoreObjectFn = Callable[[str, bytes, str], Any]
DeleteObjectFn = Callable[[str], Any]


class HannahReviewError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def isoformat_utc(value: Any) -> str | None:
    if value is None:
        return None
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def commons_thumb_url(image_url: str, width: int, original_width: int | None) -> str:
    """A Wikimedia thumbnail at `width`, or the original when it is no wider.

    `.../commons/a/ab/Name.jpg` -> `.../commons/thumb/a/ab/Name.jpg/500px-Name.jpg`.
    Wikimedia refuses a thumbnail wider than the original, and anything not on
    Commons is returned as it is. The imageinfo API adds `?utm_...` tracking to
    original URLs; it is dropped, or every large original is fetched in full
    and refused as too big (2026-10-20).
    """
    match = _COMMONS_ORIGINAL.match(image_url.split("#", 1)[0].split("?", 1)[0])
    if not match or (original_width is not None and original_width <= width):
        return image_url
    root, first, second, name = match.groups()
    return f"{root}/thumb/{first}/{second}/{name}/{width}px-{name}"


def photo_response(photo: HannahConceptPhoto) -> dict[str, Any]:
    verdict = photo.vision_verdict_json or {}
    return {
        "id": str(photo.id),
        "status": photo.status,
        "rank": photo.rank,
        "score": float(photo.score) if photo.score is not None else None,
        "source": photo.source,
        "sourceUrl": photo.source_url,
        "thumbUrl": commons_thumb_url(photo.image_url, GRID_THUMB_WIDTH, photo.width),
        "title": photo.title,
        "author": photo.author,
        "licenseLabel": photo.license_label,
        "licenseUrl": photo.license_url,
        "width": photo.width,
        "height": photo.height,
        "depicts": verdict.get("depicts"),
        "reason": verdict.get("reason") or verdict.get("error"),
        "filterReasons": list(photo.filter_reasons_json or []),
        "decidedAt": isoformat_utc(photo.decided_at),
        "decidedByAgent": photo.decided_by_agent,
        "lastSeenAt": isoformat_utc(photo.last_seen_at),
    }


_CONCEPT_LIST_SQL = """
    SELECT t.id::text AS target_id, c.id::text AS concept_id, c.concept_code, c.concept_kind,
           r.preferred_name, r.definition, r.aliases_json,
           t.photographable, t.photographable_basis, t.search_terms_json, t.status, t.attempt_count,
           t.last_attempt_at, t.next_eligible_at, t.wikidata_qid, t.commons_category, t.wikipedia_url,
           (SELECT count(DISTINCT app.governed_symbol_id)
              FROM active_public_symbol_projections app
              JOIN symbol_semantic_assignments a
                ON a.symbol_revision_id = app.symbol_revision_id
               AND a.assignment_role = 'primary' AND a.status = 'verified'
             WHERE a.semantic_concept_id = c.id
               AND app.visibility = 'public' AND app.owner_organization_id IS NULL) AS symbol_count,
           (SELECT array_agg(ids.catalog_symbol_id ORDER BY ids.catalog_symbol_id) FROM (
                SELECT DISTINCT app.catalog_symbol_id
                  FROM active_public_symbol_projections app
                  JOIN symbol_semantic_assignments a
                    ON a.symbol_revision_id = app.symbol_revision_id
                   AND a.assignment_role = 'primary' AND a.status = 'verified'
                 WHERE a.semantic_concept_id = c.id
                   AND app.visibility = 'public' AND app.owner_organization_id IS NULL
                   AND app.catalog_symbol_id IS NOT NULL
                 ORDER BY app.catalog_symbol_id
                 LIMIT 3) ids) AS example_symbol_ids
    FROM hannah_concept_targets t
    JOIN semantic_concepts c ON c.id = t.semantic_concept_id
    JOIN semantic_concept_revisions r ON r.id = c.current_revision_id
"""


def _concept_filters(status: str | None, query: str | None) -> tuple[str, dict[str, Any]]:
    clauses, params = [], {}
    if status:
        if status not in TARGET_STATUSES:
            raise HannahReviewError(422, f"Unknown target status: {status}.")
        clauses.append("t.status = :status")
        params["status"] = status
    if query and query.strip():
        clauses.append("(c.concept_code ILIKE :q OR r.preferred_name ILIKE :q OR CAST(r.aliases_json AS text) ILIKE :q)")
        params["q"] = f"%{query.strip()}%"
    return (" WHERE " + " AND ".join(clauses)) if clauses else "", params


def concept_item(row: Any, photos: list[HannahConceptPhoto], counts: dict[str, int]) -> dict[str, Any]:
    scope = ConceptScope(
        concept_id=row["concept_id"], concept_code=row["concept_code"], concept_kind=row["concept_kind"],
        preferred_name=row["preferred_name"], definition=row["definition"],
        aliases=[str(alias) for alias in row["aliases_json"] or []], symbol_count=int(row["symbol_count"] or 0),
    )
    return {
        "targetId": row["target_id"],
        "conceptId": row["concept_id"],
        "conceptCode": row["concept_code"],
        "name": readable_name(scope),
        "conceptKind": row["concept_kind"],
        "photographable": row["photographable"],
        "photographableBasis": row["photographable_basis"],
        "searchTerms": list(row["search_terms_json"] or []),
        "status": row["status"],
        "attemptCount": int(row["attempt_count"] or 0),
        "lastAttemptAt": isoformat_utc(row["last_attempt_at"]),
        "nextEligibleAt": isoformat_utc(row["next_eligible_at"]),
        "wikidataQid": row["wikidata_qid"],
        "commonsCategory": row["commons_category"],
        "wikipediaUrl": row["wikipedia_url"],
        "symbolCount": scope.symbol_count,
        "exampleSymbolIds": [value for value in (row["example_symbol_ids"] or []) if value],
        "photoCounts": {status: counts.get(status, 0) for status in PHOTO_STATUSES},
        "photos": [photo_response(photo) for photo in photos],
    }


def list_concepts(
    session: Session, *, status: str | None = None, query: str | None = None, offset: int = 0, limit: int = 25
) -> dict[str, Any]:
    """A page of targets, steward work first, each with its approved and proposed photos."""
    where, params = _concept_filters(status, query)
    total = int(
        session.execute(
            text(
                "SELECT count(*) FROM hannah_concept_targets t JOIN semantic_concepts c ON c.id = t.semantic_concept_id "
                "JOIN semantic_concept_revisions r ON r.id = c.current_revision_id" + where
            ),
            params,
        ).scalar()
        or 0
    )
    rows = session.execute(
        text(
            _CONCEPT_LIST_SQL
            + where
            + " ORDER BY (t.status = 'has_proposals') DESC, c.concept_code OFFSET :offset LIMIT :limit"
        ),
        {**params, "offset": offset, "limit": limit},
    ).mappings().all()
    concept_ids = [uuid.UUID(row["concept_id"]) for row in rows]
    photos: dict[str, list[HannahConceptPhoto]] = {}
    counts: dict[str, dict[str, int]] = {}
    if concept_ids:
        for photo in (
            session.query(HannahConceptPhoto)
            .filter(HannahConceptPhoto.semantic_concept_id.in_(concept_ids))
            .filter(HannahConceptPhoto.status.in_(("approved", "proposed")))
            .order_by(HannahConceptPhoto.status, HannahConceptPhoto.rank.nulls_last())
            .all()
        ):
            photos.setdefault(str(photo.semantic_concept_id), []).append(photo)
        for concept_id, photo_status, count in session.execute(
            text(
                "SELECT semantic_concept_id::text, status, count(*) FROM hannah_concept_photos "
                "WHERE semantic_concept_id IN :ids GROUP BY 1, 2"
            ).bindparams(bindparam("ids", expanding=True)),
            {"ids": concept_ids},
        ).all():
            counts.setdefault(concept_id, {})[photo_status] = int(count)
    summary = {status_value: 0 for status_value in TARGET_STATUSES}
    for status_value, count in session.execute(text("SELECT status, count(*) FROM hannah_concept_targets GROUP BY status")).all():
        summary[status_value] = int(count)
    return {
        "items": [concept_item(row, photos.get(row["concept_id"], []), counts.get(row["concept_id"], {})) for row in rows],
        "total": total,
        "offset": offset,
        "limit": limit,
        "summary": summary,
    }


def concept_detail(session: Session, target_id: uuid.UUID) -> dict[str, Any]:
    row = session.execute(text(_CONCEPT_LIST_SQL + " WHERE t.id = :id"), {"id": target_id}).mappings().one_or_none()
    if row is None:
        raise HannahReviewError(404, "Hannah concept target not found.")
    photos = (
        session.query(HannahConceptPhoto)
        .filter_by(semantic_concept_id=uuid.UUID(row["concept_id"]))
        .filter(HannahConceptPhoto.status.in_(("approved", "proposed")))
        .order_by(HannahConceptPhoto.status, HannahConceptPhoto.rank.nulls_last())
        .all()
    )
    counts = {
        status_value: int(count)
        for status_value, count in session.execute(
            text("SELECT status, count(*) FROM hannah_concept_photos WHERE semantic_concept_id = :id GROUP BY status"),
            {"id": uuid.UUID(row["concept_id"])},
        ).all()
    }
    return concept_item(row, photos, counts)


def list_concept_photos(session: Session, target_id: uuid.UUID, *, status: str | None = None) -> list[dict[str, Any]]:
    """Every photo Hannah has looked at for a concept: the steward may prefer one she did not propose."""
    target = session.get(HannahConceptTarget, target_id)
    if target is None:
        raise HannahReviewError(404, "Hannah concept target not found.")
    query = session.query(HannahConceptPhoto).filter_by(semantic_concept_id=target.semantic_concept_id)
    if status:
        if status not in PHOTO_STATUSES:
            raise HannahReviewError(422, f"Unknown photo status: {status}.")
        query = query.filter_by(status=status)
    order = {"approved": 0, "proposed": 1, "candidate": 2, "rejected": 3, "removed": 4, "filtered": 5}
    photos = query.all()
    photos.sort(key=lambda photo: (order.get(photo.status, 9), -(float(photo.score) if photo.score is not None else -1), photo.title or ""))
    return [photo_response(photo) for photo in photos]


def _lock_target(session: Session, concept_id: uuid.UUID) -> HannahConceptTarget:
    target = (
        session.query(HannahConceptTarget)
        .filter_by(semantic_concept_id=concept_id)
        .with_for_update()
        .one_or_none()
    )
    if target is None:
        raise HannahReviewError(404, "Hannah concept target not found.")
    return target


def _refresh_concept(session: Session, target: HannahConceptTarget, now: datetime) -> None:
    """Re-rank the open candidates and set the target's status from its photos."""
    photos = session.query(HannahConceptPhoto).filter_by(semantic_concept_id=target.semantic_concept_id).all()
    rows = [
        {"photo": photo, "status": photo.status, "score": float(photo.score) if photo.score is not None else None, "rank": photo.rank}
        for photo in photos
    ]
    rank_and_propose(rows)
    for row in rows:
        if row["status"] in ("candidate", "proposed"):
            row["photo"].status, row["photo"].rank = row["status"], row["rank"]
    approved = sum(1 for photo in photos if photo.status == "approved")
    proposed = sum(1 for photo in photos if photo.status == "proposed")
    if target.status == "excluded":
        pass
    elif proposed:
        # Steward work comes first, whatever is already approved.
        target.status = "has_proposals"
    elif approved:
        # A free slot is looked for again after the usual wait; a full concept is done.
        target.status = "has_photos"
        target.next_eligible_at = None if approved >= PHOTOS_PER_CONCEPT else now + SEARCHED_COOLDOWN
    else:
        # Nothing left to choose from: search again at the next opportunity.
        target.status, target.next_eligible_at = "searched", now
    target.updated_at = now


def _audit(session: Session, *, action: str, entity_id: uuid.UUID, actor_id: Any, payload: dict[str, Any], now: datetime) -> None:
    session.add(
        AuditEvent(
            id=uuid.uuid4(), entity_type="hannah_concept_photo" if action.startswith("hannah_photo") else "hannah_concept_target",
            entity_id=entity_id, action=action, actor_id=actor_id, payload_json=payload, created_at=now,
        )
    )


def _concept_code(session: Session, concept_id: uuid.UUID) -> str:
    return str(session.execute(text("SELECT concept_code FROM semantic_concepts WHERE id = :id"), {"id": concept_id}).scalar())


def decide_photo(
    session: Session,
    photo_id: uuid.UUID,
    decision: str,
    *,
    user_id: Any,
    fetch_bytes: FetchBytesFn,
    store_object: StoreObjectFn,
    delete_object: DeleteObjectFn,
    now: datetime | None = None,
    agent: str | None = None,
) -> dict[str, Any]:
    """Approve, reject or remove one photo; returns the concept as the list shows it.

    `agent="hannah"` records an automatic decision instead of a steward's.
    """
    if decision not in DECISIONS:
        raise HannahReviewError(422, f"Unsupported decision: {decision}.")
    now = now or utc_now()
    photo = session.get(HannahConceptPhoto, photo_id)
    if photo is None:
        raise HannahReviewError(404, "Hannah photo not found.")
    target = _lock_target(session, photo.semantic_concept_id)
    session.refresh(photo)
    if photo.status not in DECISIONS[decision]:
        raise HannahReviewError(409, f"A {photo.status} photo cannot be {PAST_TENSE[decision]}.")
    concept_code = _concept_code(session, photo.semantic_concept_id)

    if decision == "approve":
        approved = (
            session.query(HannahConceptPhoto)
            .filter_by(semantic_concept_id=photo.semantic_concept_id, status="approved")
            .count()
        )
        if approved >= PHOTOS_PER_CONCEPT:
            raise HannahReviewError(409, f"This concept already has {PHOTOS_PER_CONCEPT} approved photos; remove one first.")
        try:
            payload, declared = fetch_bytes(commons_thumb_url(photo.image_url, DISPLAY_WIDTH, photo.width))
            content_type = validate_stored_image(payload, declared)
        except Exception as exc:
            raise HannahReviewError(502, f"The photo could not be fetched as an image: {type(exc).__name__}.") from exc
        extension = mimetypes.guess_extension(content_type) or ".jpg"
        object_key = f"hannah/concept-photos/{concept_code}/{photo.id}{extension}"
        store_object(object_key, payload, content_type)
        photo.object_key, photo.content_type, photo.size_bytes = object_key, content_type, len(payload)
        photo.status, photo.rank = "approved", approved + 1
    elif decision == "reject":
        photo.status, photo.rank = "rejected", None
    else:
        if photo.object_key:
            try:
                delete_object(photo.object_key)
            except Exception as exc:
                raise HannahReviewError(502, f"The stored copy could not be deleted: {type(exc).__name__}.") from exc
        photo.status, photo.rank = "removed", None
        photo.object_key, photo.content_type, photo.size_bytes = None, None, None

    photo.decided_by_user_id = None if agent else user_id
    photo.decided_by_agent = agent
    photo.decided_at = now
    session.flush()
    _refresh_concept(session, target, now)
    payload = {"concept_code": concept_code, "title": photo.title, "source_url": photo.source_url}
    if agent:
        payload.update({"agent": agent, "score": float(photo.score) if photo.score is not None else None})
    _audit(
        session, action=f"hannah_photo_{PAST_TENSE[decision]}", entity_id=photo.id,
        actor_id=None if agent else user_id, now=now, payload=payload,
    )
    session.commit()
    return concept_detail(session, target.id)


def auto_approve(
    session: Session,
    concept_id: uuid.UUID,
    *,
    min_score: float,
    fetch_bytes: FetchBytesFn,
    store_object: StoreObjectFn,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Hannah approves her best proposals scoring at least `min_score` (WP-H6).

    Proposals are taken in rank order until the concept holds its two photos.
    One that cannot be fetched or stored stays proposed for a steward.
    """
    approved: list[str] = []
    failed: list[dict[str, str]] = []
    while True:
        query = (
            session.query(HannahConceptPhoto)
            .filter_by(semantic_concept_id=concept_id, status="proposed")
            .filter(HannahConceptPhoto.score >= min_score)
        )
        if failed:
            query = query.filter(HannahConceptPhoto.id.notin_([uuid.UUID(item["id"]) for item in failed]))
        photo = query.order_by(HannahConceptPhoto.rank.nulls_last(), HannahConceptPhoto.id).first()
        if photo is None:
            break
        try:
            decide_photo(
                session, photo.id, "approve", user_id=None, agent="hannah", fetch_bytes=fetch_bytes,
                store_object=store_object, delete_object=lambda key: None, now=now,
            )
        except HannahReviewError as exc:
            session.rollback()
            if exc.status_code == 409:  # the concept is full
                break
            failed.append({"id": str(photo.id), "error": exc.detail})
            continue
        except Exception as exc:  # storage failed: leave the proposal for a steward
            session.rollback()
            failed.append({"id": str(photo.id), "error": f"The photo could not be stored: {type(exc).__name__}."})
            continue
        approved.append(str(photo.id))
    return {"approved": approved, "failed": failed}


def update_target(
    session: Session,
    target_id: uuid.UUID,
    *,
    user_id: Any,
    photographable: str | None = None,
    search_terms: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A steward's correction. It marks the decision `steward`, which no rebuild overwrites."""
    if photographable is None and search_terms is None:
        raise HannahReviewError(422, "Nothing to change.")
    now = now or utc_now()
    target = session.get(HannahConceptTarget, target_id)
    if target is None:
        raise HannahReviewError(404, "Hannah concept target not found.")
    target = _lock_target(session, target.semantic_concept_id)
    before = {"photographable": target.photographable, "search_terms": list(target.search_terms_json or [])}

    if photographable is not None:
        if photographable not in PHOTOGRAPHABLE_VALUES:
            raise HannahReviewError(422, "photographable must be yes, no or unsure.")
        target.photographable = photographable
    elif target.photographable is None:
        raise HannahReviewError(422, "Decide whether the concept can be photographed before editing its terms.")
    target.photographable_basis = "steward"

    if search_terms is not None:
        cleaned: list[str] = []
        for term in search_terms:
            value = " ".join(str(term).split()).lower()
            if value and value not in cleaned:
                cleaned.append(value)
        if not 1 <= len(cleaned) <= 3 or any(not 3 <= len(term) <= 80 for term in cleaned):
            raise HannahReviewError(422, "Give one to three search terms of 3 to 80 characters.")
        if cleaned != before["search_terms"]:
            target.search_terms_json = cleaned
            if target.photographable != "no":
                target.status, target.next_eligible_at = "pending", None

    if target.photographable == "no":
        target.status = "excluded"
    elif target.status == "excluded":
        target.status, target.next_eligible_at = "pending", None
    target.updated_by_user_id = user_id
    target.updated_at = now
    _audit(
        session, action="hannah_target_updated", entity_id=target.id, actor_id=user_id, now=now,
        payload={
            "concept_code": _concept_code(session, target.semantic_concept_id),
            "before": before,
            "after": {"photographable": target.photographable, "search_terms": list(target.search_terms_json or [])},
        },
    )
    session.commit()
    return concept_detail(session, target.id)

