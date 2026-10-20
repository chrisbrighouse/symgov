"""Calibrate Hannah's automatic approval against a steward's decisions (WP-H6).

The steward's approvals and rejections in the Workspace are the labels. For
each vision model under test, every labelled photo gets a score: the stored
one when that model checked it, otherwise a fresh vision call on the same
800 px thumbnail. A threshold sweep then shows, for each candidate threshold,
how many labelled photos it would have approved automatically, how many of
those the steward rejected, and what share of the steward's approvals it
would have caught. The recommended threshold is the lowest that would have
approved nothing the steward rejected (plan section WP-H6, step 3).

Nothing is written to the photo rows: a fresh score lives only in the report,
so the stored verdict stays the one that produced the proposal. Vision calls
are recorded in the usage ledger like any other `hannah_vision` call.

Caveat: the run stored each file's title but not its Commons description, so
a fresh check is captioned with the title alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable
import uuid

from sqlalchemy.orm import Session

from .hannah_photos import (
    PHOTOS_PER_CONCEPT,
    PROPOSE_MIN_SCORE,
    THUMB_WIDTH,
    CompleteVisionFn,
    FetchBytesFn,
    TargetView,
    parse_verdict,
    score_verdict,
    target_view_from_row,
    vision_messages,
)
from .hannah_review import commons_thumb_url
from .models.schema import HannahConceptPhoto, HannahConceptTarget


# The steward's decisions that count as labels. A removed photo was approved
# and later taken down, which may be about the photo or only about room for a
# better one, so it is reported but not used.
LABELS = {"approved": True, "rejected": False}
THRESHOLDS = tuple(round(PROPOSE_MIN_SCORE + 0.05 * step, 2) for step in range(13) if PROPOSE_MIN_SCORE + 0.05 * step <= 1.0)
# Fewer steward approvals than this at the recommended threshold, and the
# recommendation is marked as resting on too little evidence.
MIN_SUPPORT = 10


@dataclass
class LabelledPhoto:
    photo: HannahConceptPhoto
    view: TargetView
    label: bool


def load_labelled(session: Session) -> tuple[list[LabelledPhoto], dict[str, int]]:
    """Every photo a steward approved or rejected, with its concept's view."""
    photos = (
        session.query(HannahConceptPhoto)
        .filter(HannahConceptPhoto.decided_by_user_id.isnot(None))
        .filter(HannahConceptPhoto.status.in_(("approved", "rejected", "removed")))
        .order_by(HannahConceptPhoto.semantic_concept_id, HannahConceptPhoto.decided_at, HannahConceptPhoto.id)
        .all()
    )
    views: dict[uuid.UUID, TargetView] = {}
    labelled: list[LabelledPhoto] = []
    counts = {"approved": 0, "rejected": 0, "removed_not_used": 0}
    for photo in photos:
        if photo.status not in LABELS:
            counts["removed_not_used"] += 1
            continue
        if photo.semantic_concept_id not in views:
            target = session.query(HannahConceptTarget).filter_by(semantic_concept_id=photo.semantic_concept_id).one()
            views[photo.semantic_concept_id] = target_view_from_row(session, target)
        labelled.append(LabelledPhoto(photo=photo, view=views[photo.semantic_concept_id], label=LABELS[photo.status]))
        counts[photo.status] += 1
    return labelled, counts


def score_photo(
    item: LabelledPhoto,
    model: str,
    *,
    fresh: bool,
    fetch_bytes: FetchBytesFn,
    complete_vision: CompleteVisionFn,
) -> dict[str, Any]:
    """One model's score for one labelled photo: stored when it can be, else a new call."""
    photo = item.photo
    if not fresh and photo.vision_model == model and photo.score is not None:
        verdict = photo.vision_verdict_json or {}
        return {"score": float(photo.score), "from": "stored", "depicts": verdict.get("depicts"), "reason": verdict.get("reason")}
    try:
        image, content_type = fetch_bytes(commons_thumb_url(photo.image_url, THUMB_WIDTH, photo.width))
        verdict = parse_verdict(complete_vision(vision_messages(item.view, image, content_type, photo.title)))
    except Exception as exc:  # one failed photo must not end the run
        return {"score": None, "from": "error", "error": f"{type(exc).__name__}: {exc}"[:300]}
    score, reasons = score_verdict(verdict)
    return {
        "score": score,
        "from": "fresh",
        "hard_reject": reasons,
        "depicts": verdict.get("depicts"),
        "reason": verdict.get("reason"),
    }


def sweep(pairs: list[tuple[float | None, bool]], thresholds: tuple[float, ...] = THRESHOLDS) -> list[dict[str, Any]]:
    """What each threshold would have approved, against the steward's labels.

    `pairs` are (score, steward approved?). A missing score, from a hard
    reject or a failed check, is never approved.
    """
    positives = sum(1 for _score, label in pairs if label)
    rows = []
    for threshold in thresholds:
        chosen = [label for score, label in pairs if score is not None and score >= threshold]
        caught = sum(1 for label in chosen if label)
        rows.append(
            {
                "threshold": threshold,
                "would_approve": len(chosen),
                "steward_rejected": len(chosen) - caught,
                "steward_approved_caught": caught,
                "recall": round(caught / positives, 3) if positives else None,
            }
        )
    return rows


def recommend(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The lowest threshold that approves something and nothing the steward rejected."""
    for row in rows:
        if row["would_approve"] and not row["steward_rejected"]:
            return {**row, "enough_evidence": row["steward_approved_caught"] >= MIN_SUPPORT}
    return None


def calibrate(
    session: Session,
    models: list[str],
    *,
    completer_for: Callable[[str], CompleteVisionFn],
    fetch_bytes: FetchBytesFn,
    fresh: bool = False,
    max_vision_calls: int = 200,
) -> dict[str, Any]:
    """The calibration report for each model, on the same labelled photos."""
    labelled, counts = load_labelled(session)
    report: dict[str, Any] = {
        "labels": counts,
        "photos_per_concept": PHOTOS_PER_CONCEPT,
        "propose_min_score": PROPOSE_MIN_SCORE,
        "models": {},
    }
    calls = 0
    for model in models:
        complete = completer_for(model)
        photos = []
        for item in labelled:
            will_call = fresh or item.photo.vision_model != model or item.photo.score is None
            if will_call and calls >= max_vision_calls:
                result = {"score": None, "from": "skipped", "error": "vision call limit reached"}
            else:
                result = score_photo(item, model, fresh=fresh, fetch_bytes=fetch_bytes, complete_vision=complete)
                calls += result["from"] in ("fresh", "error")
            photos.append(
                {
                    "photo_id": str(item.photo.id),
                    "concept_code": item.view.concept_code,
                    "concept_name": item.view.name,
                    "title": item.photo.title,
                    "source_url": item.photo.source_url,
                    "steward": "approved" if item.label else "rejected",
                    **result,
                }
            )
        scored = [photo for photo in photos if photo["from"] in ("stored", "fresh")]
        rows = sweep([(photo["score"], photo["steward"] == "approved") for photo in scored])
        report["models"][model] = {
            "scored": len(scored),
            "not_scored": len(photos) - len(scored),
            "sweep": rows,
            "recommended": recommend(rows),
            "photos": photos,
        }
    report["vision_calls"] = calls
    return report
