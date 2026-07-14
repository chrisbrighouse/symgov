from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import Text, bindparam, cast, func, text
from sqlalchemy.orm import Session

from ..asset_manifest import list_download_assets
from ..auth import hash_pin
from ..dependencies import get_db_session
from ..models import (
    AgentDefinition,
    AgentQueueItem,
    Attachment,
    AuditEvent,
    ClarificationRecord,
    GovernedSymbol,
    HannahPhotoCandidate,
    PublishedPage,
    ReviewCase,
    ReviewCaseAction,
    SymbolRevision,
    User,
    UserRole,
)
from ..published_catalog import (
    PUBLISHED_SYMBOLS_SQL,
    choose_published_preview_asset,
    published_fallback_source_asset,
    published_symbol_display_id,
)
from ..runtime import download_object_bytes
from ..settings import get_settings


router = APIRouter(prefix="/published", tags=["published"])
legacy_router = APIRouter(tags=["published"])

MAX_PUBLISHED_SYMBOL_COMMAND_SELECTION = 5
PUBLISHED_SYMBOL_COMMANDS = {"comment", "send_for_review"}
SYSTEM_ED_EMAIL = "ed@symgov.local"
SYSTEM_ED_NAME = "Ed"
ED_RUNTIME_QUEUE_DIR = Path("/data/.openclaw/workspaces/ed/runtime/agent_queue_items")


def published_download_labels(downloads: list) -> list[str]:
    labels: list[str] = []
    for download in downloads:
        if isinstance(download, str):
            labels.append(download)
        elif isinstance(download, dict):
            label = download.get("label") or download.get("filename") or download.get("format") or download.get("object_key")
            if label:
                labels.append(str(label))
    return labels


def published_symbol_row(
    row,
    supplemental_photos_by_symbol: dict[str, list[dict]] | None = None,
    comment_counts_by_symbol: dict[str, int] | None = None,
) -> dict:
    payload = row.payload_json or {}
    keywords = payload.get("keywords") or payload.get("search_terms") or []
    if not isinstance(keywords, list):
        keywords = []
    downloads = payload.get("downloads") or []
    if not isinstance(downloads, list):
        downloads = []

    supplemental_photos = (supplemental_photos_by_symbol or {}).get(str(row.symbol_id), [])
    comment_count = int((comment_counts_by_symbol or {}).get(str(row.symbol_id), 0))

    symbol_display_id = published_symbol_display_id(row)
    preview_asset = choose_published_preview_asset(payload)

    return {
        "id": row.slug,
        "symbolId": row.symbol_id,
        "displayName": symbol_display_id,
        "packageDisplayId": payload.get("package_display_id"),
        "packageSymbolSequence": payload.get("package_symbol_sequence"),
        "slug": row.slug,
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "category": row.category,
        "discipline": row.discipline,
        "revisionId": row.symbol_revision_id,
        "revision": row.revision_label,
        "revisionCreatedAt": row.revision_created_at.isoformat() if row.revision_created_at else None,
        "status": "Published",
        "summary": payload.get("summary") or payload.get("description") or row.canonical_name,
        "rationale": row.rationale or "",
        "effectiveDate": row.effective_date.isoformat(),
        "lastUpdatedAt": row.last_updated_at.isoformat() if row.last_updated_at else None,
        "pageId": row.page_id,
        "pageCode": row.page_code,
        "pageTitle": row.page_title,
        "packId": row.pack_id,
        "packCode": row.pack_code,
        "pack": row.pack_title,
        "keywords": keywords,
        "downloads": published_download_labels(downloads),
        "downloadAssets": list_download_assets(payload, fallback_source_asset=published_fallback_source_asset(payload)),
        "sortOrder": row.sort_order,
        "previewUrl": f"/api/v1/published/symbols/{row.slug}/preview" if preview_asset else None,
        "previewAsset": preview_asset,
        "supplementalPhotos": supplemental_photos,
        "hasComments": comment_count > 0,
        "commentCount": comment_count,
        "payload": payload,
    }


def load_comment_counts(session: Session, rows) -> dict[str, int]:
    symbol_ids = [row.symbol_id for row in rows]
    if not symbol_ids:
        return {}
    comment_rows = (
        session.query(ClarificationRecord.symbol_id, func.count(ClarificationRecord.id))
        .filter(ClarificationRecord.symbol_id.in_(symbol_ids))
        .group_by(ClarificationRecord.symbol_id)
        .all()
    )
    return {str(symbol_id): int(count) for symbol_id, count in comment_rows}


def published_symbol_comment_item(comment: ClarificationRecord, *, submitter_name: str | None = None) -> dict:
    return {
        "id": str(comment.id),
        "kind": comment.kind,
        "status": comment.status,
        "source": comment.source,
        "detail": comment.detail,
        "submittedBy": submitter_name or "Unknown",
        "createdAt": comment.created_at.isoformat() if comment.created_at else None,
        "updatedAt": comment.updated_at.isoformat() if comment.updated_at else None,
    }


def load_comment_history(session: Session, symbol_id: uuid.UUID) -> list[dict]:
    rows = (
        session.query(ClarificationRecord, User.display_name, User.email)
        .outerjoin(User, ClarificationRecord.submitted_by == User.id)
        .filter(ClarificationRecord.symbol_id == symbol_id)
        .order_by(ClarificationRecord.created_at.desc(), ClarificationRecord.id.desc())
        .all()
    )
    return [
        published_symbol_comment_item(
            comment,
            submitter_name=display_name or email,
        )
        for comment, display_name, email in rows
    ]


def normalize_published_symbol_command_request(payload: dict) -> dict:
    command = str(payload.get("command") or "").strip().lower().replace("-", "_")
    if command not in PUBLISHED_SYMBOL_COMMANDS:
        raise ValueError("Command must be 'comment' or 'send_for_review'.")
    symbol_ids = [str(value or "").strip() for value in (payload.get("symbolIds") or payload.get("symbol_ids") or [])]
    symbol_ids = [value for value in symbol_ids if value]
    if not symbol_ids:
        raise ValueError("Select at least one published symbol.")
    if len(symbol_ids) > MAX_PUBLISHED_SYMBOL_COMMAND_SELECTION:
        raise ValueError(f"Select no more than {MAX_PUBLISHED_SYMBOL_COMMAND_SELECTION} published symbols at a time.")
    if len(set(symbol_ids)) != len(symbol_ids):
        raise ValueError("Each selected symbol must be unique.")
    comment = str(payload.get("comment") or "").strip()
    if not comment:
        raise ValueError("Add a comment before posting.")
    return {"command": command, "symbol_ids": symbol_ids, "comment": comment}


def get_or_create_ed_user(session: Session) -> User:
    user = session.query(User).filter(func.lower(User.email) == SYSTEM_ED_EMAIL).one_or_none()
    if user is not None:
        return user
    now = datetime.now(timezone.utc).replace(microsecond=0)
    user = User(
        id=uuid.uuid4(),
        email=SYSTEM_ED_EMAIL,
        display_name=SYSTEM_ED_NAME,
        pin_hash=hash_pin("4590"),
        pin_set_at=now,
        must_change_pin=True,
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    session.flush()
    session.add(UserRole(user_id=user.id, role="admin", created_at=now))
    session.flush()
    return user


def create_ed_queue_item(session: Session, *, source_type: str, source_id: uuid.UUID, payload: dict, priority: str = "medium") -> AgentQueueItem | None:
    ed_definition = session.query(AgentDefinition).filter_by(slug="ed").one_or_none()
    if ed_definition is None:
        return None
    now = datetime.now(timezone.utc).replace(microsecond=0)
    queue_item_id = uuid.uuid4()
    queue_item = AgentQueueItem(
        id=queue_item_id,
        agent_id=ed_definition.id,
        source_type=source_type,
        source_id=source_id,
        status="queued",
        priority=priority,
        payload_json=payload,
        confidence=None,
        escalation_reason=None,
        created_at=now,
        started_at=None,
        completed_at=None,
    )
    session.add(queue_item)
    session.flush()

    runtime_payload = {
        "id": str(queue_item_id),
        "agent_id": "ed",
        "source_type": source_type,
        "source_id": str(source_id),
        "status": "queued",
        "priority": priority,
        "payload_json": payload,
        "confidence": None,
        "escalation_reason": None,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "started_at": None,
        "completed_at": None,
    }
    ED_RUNTIME_QUEUE_DIR.mkdir(parents=True, exist_ok=True)
    (ED_RUNTIME_QUEUE_DIR / f"{queue_item_id}.json").write_text(
        json.dumps(runtime_payload, indent=2) + "\n",
        encoding="utf-8",
    )
    return queue_item


def load_supplemental_photos(session: Session, rows) -> dict[str, list[dict]]:
    symbol_ids = [row.symbol_id for row in rows]
    if not symbol_ids:
        return {}
    photo_rows = (
        session.query(HannahPhotoCandidate, GovernedSymbol.slug)
        .join(GovernedSymbol, GovernedSymbol.id == HannahPhotoCandidate.symbol_id)
        .filter(HannahPhotoCandidate.symbol_id.in_(symbol_ids))
        .filter(HannahPhotoCandidate.status == "attached")
        .filter(HannahPhotoCandidate.object_key.isnot(None))
        .order_by(HannahPhotoCandidate.relevance_score.desc(), HannahPhotoCandidate.last_seen_at.desc())
        .all()
    )
    grouped: dict[str, list[dict]] = {}
    for candidate, slug in photo_rows:
        bucket = grouped.setdefault(str(candidate.symbol_id), [])
        if len(bucket) >= 2:
            continue
        bucket.append(
            {
                "id": str(candidate.id),
                "title": candidate.title,
                "sourceUrl": candidate.source_url,
                "sourceDomain": candidate.source_domain,
                "licenseLabel": candidate.license_label,
                "rightsStatus": candidate.rights_status,
                "score": float(candidate.relevance_score) if candidate.relevance_score is not None else None,
                "previewUrl": f"/api/v1/published/symbols/{slug}/supplemental-photos/{candidate.id}/preview",
            }
        )
    return grouped


def pack_row(row) -> dict:
    return {
        "id": row.id,
        "packCode": row.pack_code,
        "title": row.title,
        "audience": row.audience,
        "effectiveDate": row.effective_date.isoformat(),
        "status": row.status,
        "symbolCount": row.symbol_count,
    }


@router.get("/symbols")
@legacy_router.get("/published/symbols", include_in_schema=False)
def list_published_symbols(
    q: str | None = Query(default=None),
    pack: str | None = Query(default=None),
    session: Session = Depends(get_db_session),
) -> dict:
    filters = []
    params = {}
    if q:
        filters.append(
            """
            (
                gs.slug ILIKE :query
                OR gs.canonical_name ILIKE :query
                OR gs.category ILIKE :query
                OR gs.discipline ILIKE :query
                OR pk.pack_code ILIKE :query
                OR pk.title ILIKE :query
                OR pp.page_code ILIKE :query
            )
            """
        )
        params["query"] = f"%{q}%"
    if pack:
        filters.append("(pk.pack_code = :pack OR pk.id::text = :pack)")
        params["pack"] = pack

    where_extension = (" AND " + " AND ".join(filters)) if filters else ""
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + where_extension
            + " ORDER BY pk.effective_date DESC, pk.pack_code, pe.sort_order, gs.canonical_name"
        ),
        params,
    ).all()
    supplemental = load_supplemental_photos(session, rows)
    comment_counts = load_comment_counts(session, rows)
    return {"items": [published_symbol_row(row, supplemental, comment_counts) for row in rows]}


@router.get("/symbols/{symbol_id}")
@legacy_router.get("/published/symbols/{symbol_id}", include_in_schema=False)
def get_published_symbol(symbol_id: str, session: Session = Depends(get_db_session)) -> dict:
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + """
            AND (gs.slug = :symbol_id OR gs.id::text = :symbol_id)
            ORDER BY pp.effective_date DESC, pk.effective_date DESC
            LIMIT 1
            """
        ),
        {"symbol_id": symbol_id},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Published symbol was not found.")
    supplemental = load_supplemental_photos(session, rows)
    comment_counts = load_comment_counts(session, rows)
    return {"item": published_symbol_row(rows[0], supplemental, comment_counts)}


@router.get("/symbols/{symbol_id}/comments")
@legacy_router.get("/published/symbols/{symbol_id}/comments", include_in_schema=False)
def get_published_symbol_comments(symbol_id: str, session: Session = Depends(get_db_session)) -> dict:
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + """
            AND (gs.slug = :symbol_id OR gs.id::text = :symbol_id)
            ORDER BY pp.effective_date DESC, pk.effective_date DESC
            LIMIT 1
            """
        ),
        {"symbol_id": symbol_id},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Published symbol was not found.")
    symbol_uuid = uuid.UUID(str(rows[0].symbol_id))
    items = load_comment_history(session, symbol_uuid)
    return {
        "symbolId": str(symbol_uuid),
        "displayId": published_symbol_display_id(rows[0]),
        "commentCount": len(items),
        "items": items,
    }


@router.post("/symbols/commands")
@legacy_router.post("/published/symbols/commands", include_in_schema=False)
async def run_published_symbol_command(request: Request, session: Session = Depends(get_db_session)) -> dict:
    try:
        request_body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.") from exc
    if not isinstance(request_body, dict):
        raise HTTPException(status_code=400, detail="Request body must be a JSON object.")
    payload = request_body.get("payload") if isinstance(request_body.get("payload"), dict) else request_body
    try:
        normalized = normalize_published_symbol_command_request(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    symbol_ids = normalized["symbol_ids"]
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + """
            AND (gs.slug IN :symbol_ids OR gs.id::text IN :symbol_ids)
            ORDER BY pk.effective_date DESC, pk.pack_code, pe.sort_order, gs.canonical_name
            """
        ).bindparams(bindparam("symbol_ids", expanding=True)),
        {"symbol_ids": symbol_ids},
    ).all()
    matched_ids = {str(row.slug) for row in rows} | {str(row.symbol_id) for row in rows}
    missing = [symbol_id for symbol_id in symbol_ids if symbol_id not in matched_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"Published symbol not found: {', '.join(missing)}")

    now = datetime.now(timezone.utc).replace(microsecond=0)
    ed_user = get_or_create_ed_user(session)
    results = []
    seen_symbols: set[str] = set()
    for row in rows:
        symbol_uuid = uuid.UUID(str(row.symbol_id))
        symbol_display_id = published_symbol_display_id(row)
        if str(symbol_uuid) in seen_symbols:
            continue
        seen_symbols.add(str(symbol_uuid))
        page_uuid = uuid.UUID(str(row.page_id))
        comment_record = ClarificationRecord(
            id=uuid.uuid4(),
            symbol_id=symbol_uuid,
            published_page_id=page_uuid,
            source="published_symbol_command_menu",
            kind="review_request" if normalized["command"] == "send_for_review" else "comment",
            status="open",
            submitted_by=ed_user.id,
            external_submitter_id=None,
            detail=normalized["comment"],
            created_at=now,
            updated_at=now,
        )
        session.add(comment_record)

        review_case = None
        queue_item = None
        if normalized["command"] == "send_for_review":
            # Unpublish the current symbol revision
            current_revision = session.get(SymbolRevision, row.symbol_revision_id)
            if current_revision is not None:
                current_revision.lifecycle_state = "review"

            review_case = (
                session.query(ReviewCase)
                .filter_by(source_entity_type="published_symbol", source_entity_id=symbol_uuid)
                .filter(ReviewCase.closed_at.is_(None))
                .one_or_none()
            )
            if review_case is None:
                review_case = ReviewCase(
                    id=uuid.uuid4(),
                    source_entity_type="published_symbol",
                    source_entity_id=symbol_uuid,
                    current_stage="ux_feedback_coordination",
                    owner_id=ed_user.id,
                    escalation_level="medium",
                    opened_at=now,
                    closed_at=None,
                )
                session.add(review_case)
                session.flush()
            else:
                review_case.current_stage = "ux_feedback_coordination"

            action = ReviewCaseAction(
                id=uuid.uuid4(),
                review_case_id=review_case.id,
                decision_id=None,
                action_code="published_symbol_returned_for_review",
                action_status="queued",
                assigned_to=ed_user.id,
                target_agent_slug="ed",
                target_stage="ux_feedback_coordination",
                action_payload_json={
                    "comment": normalized["comment"],
                    "symbol_slug": row.slug,
                    "symbol_display_id": symbol_display_id,
                    "symbol_name": row.canonical_name,
                    "published_page_id": str(page_uuid),
                    "managed_by": "ed",
                },
                created_by_type="system",
                created_by_id=ed_user.id,
                created_at=now,
                started_at=None,
                completed_at=None,
            )
            session.add(action)
            queue_item = create_ed_queue_item(
                session,
                source_type="published_symbol_review_request",
                source_id=symbol_uuid,
                payload={
                    "task_type": "published_symbol_review_request",
                    "review_case_id": str(review_case.id),
                    "symbol_id": str(symbol_uuid),
                    "symbol_slug": row.slug,
                    "symbol_name": row.canonical_name,
                    "symbol_display_id": symbol_display_id,
                    "display_name": symbol_display_id,
                    "workspace_display_name": symbol_display_id,
                    "published_display_id": symbol_display_id,
                    "comment": normalized["comment"],
                    "managed_by": "ed",
                    # Ed performs the operator-feedback coordination, then the
                    # returned published symbol must land in a human-visible
                    # review stage. The review queue intentionally only lists
                    # human-actionable stages such as classification_review.
                    "next_stage": "classification_review",
                },
                priority="medium",
            )

        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                entity_type="published_symbol",
                entity_id=symbol_uuid,
                action=f"published_symbol_{normalized['command']}",
                actor_id=ed_user.id,
                payload_json={
                    "comment_id": str(comment_record.id),
                    "comment": normalized["comment"],
                    "review_case_id": str(review_case.id) if review_case is not None else None,
                    "queue_item_id": str(queue_item.id) if queue_item is not None else None,
                    "managed_by": "ed",
                },
                created_at=now,
            )
        )
        results.append(
            {
                "symbolId": str(symbol_uuid),
                "displayId": symbol_display_id,
                "name": row.canonical_name,
                "commentId": str(comment_record.id),
                "reviewCaseId": str(review_case.id) if review_case is not None else None,
                "edQueueItemId": str(queue_item.id) if queue_item is not None else None,
            }
        )

    session.commit()
    return {
        "status": "completed",
        "command": normalized["command"],
        "managedBy": "ed",
        "items": results,
        "message": (
            f"Posted comments for {len(results)} published symbol(s)."
            if normalized["command"] == "comment"
            else f"Sent {len(results)} published symbol(s) back for Ed-managed review."
        ),
    }


@router.get("/symbols/{symbol_id}/preview")
@legacy_router.get("/published/symbols/{symbol_id}/preview", include_in_schema=False)
def get_published_symbol_preview(symbol_id: str, session: Session = Depends(get_db_session)) -> Response:
    rows = session.execute(
        text(
            PUBLISHED_SYMBOLS_SQL
            + """
            AND (gs.slug = :symbol_id OR gs.id::text = :symbol_id)
            ORDER BY pp.effective_date DESC, pk.effective_date DESC
            LIMIT 1
            """
        ),
        {"symbol_id": symbol_id},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Published symbol was not found.")

    payload_json = rows[0].payload_json or {}
    preview_asset = choose_published_preview_asset(payload_json)
    object_key = preview_asset.get("object_key") if preview_asset else None
    if not object_key:
        raise HTTPException(status_code=404, detail="Published symbol preview was not found.")

    attachment = session.query(Attachment).filter(Attachment.object_key == object_key).one_or_none()
    payload = download_object_bytes(object_key=object_key, env_file=str(get_settings().storage_env_file))
    media_type = attachment.content_type if attachment is not None else payload["content_type"]
    return Response(content=payload["payload"], media_type=media_type)


@router.get("/symbols/{symbol_id}/supplemental-photos/{photo_id}/preview")
@legacy_router.get("/published/symbols/{symbol_id}/supplemental-photos/{photo_id}/preview", include_in_schema=False)
def get_published_symbol_supplemental_photo_preview(symbol_id: str, photo_id: str, session: Session = Depends(get_db_session)) -> Response:
    row = (
        session.query(HannahPhotoCandidate)
        .join(GovernedSymbol, GovernedSymbol.id == HannahPhotoCandidate.symbol_id)
        .filter(HannahPhotoCandidate.id == photo_id)
        .filter(HannahPhotoCandidate.status == "attached")
        .filter(HannahPhotoCandidate.object_key.isnot(None))
        .filter((GovernedSymbol.slug == symbol_id) | (cast(GovernedSymbol.id, Text) == symbol_id))
        .one_or_none()
    )
    if row is None or not row.object_key:
        raise HTTPException(status_code=404, detail="Published supplemental photo was not found.")

    attachment = session.query(Attachment).filter(Attachment.object_key == row.object_key).one_or_none()
    payload = download_object_bytes(object_key=row.object_key, env_file=str(get_settings().storage_env_file))
    media_type = attachment.content_type if attachment is not None else payload["content_type"]
    return Response(content=payload["payload"], media_type=media_type)


@router.get("/pages/{page_code}")
@legacy_router.get("/published/pages/{page_code}", include_in_schema=False)
def get_published_page(page_code: str, session: Session = Depends(get_db_session)) -> dict:
    rows = session.execute(
        text(PUBLISHED_SYMBOLS_SQL + " AND pp.page_code = :page_code LIMIT 1"),
        {"page_code": page_code},
    ).all()
    if not rows:
        raise HTTPException(status_code=404, detail="Published page was not found.")
    supplemental = load_supplemental_photos(session, rows)
    comment_counts = load_comment_counts(session, rows)
    return {"item": published_symbol_row(rows[0], supplemental, comment_counts)}


@router.get("/packs")
@legacy_router.get("/published/packs", include_in_schema=False)
def list_published_packs(session: Session = Depends(get_db_session)) -> dict:
    rows = session.execute(
        text(
            """
            SELECT
                pk.id::text AS id,
                pk.pack_code,
                pk.title,
                pk.audience,
                pk.effective_date,
                pk.status,
                count(pe.id)::int AS symbol_count
            FROM publication_packs pk
            LEFT JOIN pack_entries pe ON pe.pack_id = pk.id
            WHERE pk.status = 'published'
                AND pk.audience = 'public'
            GROUP BY pk.id, pk.pack_code, pk.title, pk.audience, pk.effective_date, pk.status
            ORDER BY pk.effective_date DESC, pk.pack_code
            """
        )
    ).all()
    return {"items": [pack_row(row) for row in rows]}
