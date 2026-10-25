"""The Catalog's category picklist (2026-10-25).

Every signed-in user reads it: the private drafts form and the public review
editor both offer it. A Public Reviewer, or a Platform Admin, adds to it; a
Platform Admin retires additions from Platform Admin
(`routes/platform_admin.py`).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from ..auth import AuthenticatedUser
from ..catalog_categories import (
    CatalogCategoryError,
    SimilarCategory,
    add_catalog_category,
    catalog_category_entries,
)
from ..dependencies import get_db_session, public_workflow_roles, require_user
from ..models import ReviewCase
from ..schemas import (
    CatalogCategoryAddRequest,
    CatalogCategoryAddResponse,
    CatalogCategoryListResponse,
    CatalogCategoryResponse,
)

router = APIRouter(prefix="/catalog-categories", tags=["catalog-categories"])


def category_items(session: Session) -> list[CatalogCategoryResponse]:
    return [
        CatalogCategoryResponse(
            name=entry.name,
            builtIn=entry.built_in,
            id=str(entry.addition_id) if entry.addition_id else None,
            addedAt=entry.added_at,
            addedByName=entry.added_by_name,
        )
        for entry in catalog_category_entries(session)
    ]


def _adding_authority(current_user: AuthenticatedUser) -> str:
    """Public Reviewers add while reviewing; a Platform Admin adds during cleanup."""
    if current_user.is_platform_admin:
        return "platform_admin"
    roles = public_workflow_roles(current_user)
    if "reviewer" in roles:
        return "public_reviewer"
    if "admin" in roles:
        return "admin"
    raise HTTPException(status_code=403, detail="Only a Public Reviewer can add a category.")


@router.get("", response_model=CatalogCategoryListResponse)
def list_catalog_categories(
    session: Session = Depends(get_db_session),
    _current_user: AuthenticatedUser = Depends(require_user),
) -> CatalogCategoryListResponse:
    return CatalogCategoryListResponse(items=category_items(session))


@router.post(
    "",
    response_model=CatalogCategoryAddResponse,
    responses={409: {"description": "Close to a listed category; resend with allowSimilar"}},
)
def post_catalog_category(
    body: CatalogCategoryAddRequest,
    session: Session = Depends(get_db_session),
    current_user: AuthenticatedUser = Depends(require_user),
):
    authority = _adding_authority(current_user)
    review_case_id = None
    if body.reviewCaseId:
        try:
            review_case_id = uuid.UUID(body.reviewCaseId)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Invalid review case id.") from exc
        if session.get(ReviewCase, review_case_id) is None:
            raise HTTPException(status_code=404, detail="Review case not found.")
    try:
        name, created = add_catalog_category(
            session,
            current_user,
            body.name,
            allow_similar=body.allowSimilar,
            review_case_id=review_case_id,
            effective_authority=authority,
        )
    except SimilarCategory as exc:
        return JSONResponse(
            status_code=409,
            content={"detail": str(exc), "name": exc.name, "similarTo": exc.similar_to},
        )
    except CatalogCategoryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    session.commit()
    return CatalogCategoryAddResponse(name=name, created=created, items=category_items(session))
