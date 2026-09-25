"""Generate SVG previews for public symbols that only carry a DXF. No migrations.

Measured 2026-09-25: 17 of the Catalogue's public symbols, all from the
2026-06 external submission, were published with a DXF source and no
browser-previewable asset, so the Catalogue draws a placeholder for each. This
renders each DXF with `services/dxf_preview` and attaches the result to the
symbol's *current published revision* as a display-only preview.

**What changes on a revision, and what does not.** Decided with Chris on
2026-09-25: the preview is added to the existing revision rather than cut as a
new one, because it is derived display data, not governed content. The DXF
stays the governed asset and the only download -- the preview's role is
`generated_preview`, which `asset_manifest.list_download_assets` excludes. Per
symbol, one transaction savepoint writes: an `Attachment` parented to the
revision (the lineage preview authorization trusts), a `visual_assets.preview`
entry in the payload, one `asset_transformations` step from the DXF's SHA-256
to the SVG's naming the renderer and its version, the preview authorization,
and an audit event naming the actor. Catalogue identity, revision label and
lifecycle state are untouched.

`plan` downloads and renders every candidate and writes nothing; `apply`
uploads each SVG to a content-addressed key first and then records it. A
revision that already has a preview is not a candidate, so re-running is safe,
and a symbol that fails to render or record is reported and skipped without
affecting the others.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from .asset_manifest import canonical_asset_format, list_available_assets
from .db import get_database_url, normalize_database_url
from .image_content import UnsafeImageContentError, validate_stored_image
from .models import Attachment, AuditEvent, GovernedSymbol, SymbolRevision, User
from .published_catalog import choose_published_preview_asset, published_fallback_source_asset
from .published_preview_authorizations import ensure_preview_authorization
from .rights_provenance import record_asset_transformation
from .runtime import download_object_bytes, upload_object_bytes
from .services.dxf_preview import (
    RENDERER_NAME,
    RENDERER_VERSION,
    DxfPreviewError,
    render_dxf_preview,
)

SVG_CONTENT_TYPE = "image/svg+xml"
PREVIEW_ROLE = "generated_preview"


class ConfigurationError(ValueError):
    """A missing setting or actor, safe to name. Never carries a credential."""


def _engine():
    # The API container supplies its connection through the database env file,
    # not the environment, so read it the way the app does.
    url = os.environ.get("SYMGOV_DATABASE_URL") or get_database_url()
    return create_engine(normalize_database_url(url), hide_parameters=True)


def _storage_env_file(args):
    if args.storage_env_file:
        return args.storage_env_file
    from .settings import get_settings

    return get_settings().storage_env_file


def dxf_source_asset(payload: dict | None) -> dict | None:
    """The DXF to render, for a payload the Catalogue cannot preview; else None."""
    payload = payload or {}
    if choose_published_preview_asset(payload) is not None:
        return None
    for asset in list_available_assets(payload, fallback_source_asset=published_fallback_source_asset(payload)):
        if canonical_asset_format(asset.get("format")) == "dxf" and asset.get("object_key"):
            return asset
    return None


def preview_object_key(revision_id: uuid.UUID, svg_sha256: str) -> str:
    return f"derived-previews/{revision_id}/{svg_sha256}.svg"


def find_candidates(session: Session) -> list[tuple[GovernedSymbol, SymbolRevision, dict]]:
    """Public symbols whose current revision has a DXF and nothing previewable."""
    public_ids = list(
        session.execute(text("SELECT governed_symbol_id FROM active_public_symbol_projections")).scalars()
    )
    if not public_ids:
        return []
    rows = session.execute(
        select(GovernedSymbol, SymbolRevision)
        .join(SymbolRevision, SymbolRevision.id == GovernedSymbol.current_revision_id)
        .where(GovernedSymbol.id.in_(public_ids))
        .order_by(GovernedSymbol.catalog_symbol_id)
    ).all()
    candidates = []
    for symbol, revision in rows:
        asset = dxf_source_asset(revision.payload_json)
        if asset is not None:
            candidates.append((symbol, revision, asset))
    return candidates


def render_candidates(candidates, *, storage_env_file, fetcher=download_object_bytes) -> tuple[list[dict], list[dict]]:
    rendered, failures = [], []
    for symbol, revision, asset in candidates:
        label = {"catalog_symbol_id": symbol.catalog_symbol_id, "slug": symbol.slug}
        try:
            dxf = fetcher(object_key=asset["object_key"], env_file=storage_env_file)["payload"]
        except Exception as exc:  # noqa: BLE001 -- reported by class name only
            failures.append({**label, "reason": f"source_unreadable ({type(exc).__name__})"})
            continue
        try:
            preview = render_dxf_preview(dxf)
            validate_stored_image(preview.svg, SVG_CONTENT_TYPE)
        except (DxfPreviewError, UnsafeImageContentError) as exc:
            failures.append({**label, "reason": str(exc)})
            continue
        svg_sha256 = hashlib.sha256(preview.svg).hexdigest()
        rendered.append(
            {
                **label,
                "symbol_revision_id": revision.id,
                "source_object_key": asset["object_key"],
                "source_sha256": hashlib.sha256(dxf).hexdigest(),
                "svg": preview.svg,
                "svg_sha256": svg_sha256,
                "object_key": preview_object_key(revision.id, svg_sha256),
                "entity_count": preview.entity_count,
            }
        )
    return rendered, failures


def _record_one(session: Session, item: dict, *, actor_id: uuid.UUID, occurred_at: datetime) -> None:
    revision = session.execute(
        select(SymbolRevision).where(SymbolRevision.id == item["symbol_revision_id"]).with_for_update()
    ).scalar_one()
    if dxf_source_asset(revision.payload_json) is None:
        raise ConfigurationError("the revision gained a preview since it was planned")

    filename = Path(item["source_object_key"]).stem + ".preview.svg"
    attachment = Attachment(
        id=uuid.uuid4(),
        parent_type="symbol_revision",
        parent_id=revision.id,
        filename=filename,
        object_key=item["object_key"],
        content_type=SVG_CONTENT_TYPE,
        size_bytes=len(item["svg"]),
        sha256=item["svg_sha256"],
        created_at=occurred_at,
    )
    session.add(attachment)
    session.flush()

    payload = dict(revision.payload_json or {})
    visual_assets = dict(payload.get("visual_assets") or {})
    visual_assets["preview"] = {
        "attachment_id": str(attachment.id),
        "object_key": item["object_key"],
        "filename": filename,
        "content_type": SVG_CONTENT_TYPE,
        "format": "svg",
        # `generated…` keeps it out of `list_download_assets`: the DXF stays
        # the only download.
        "role": PREVIEW_ROLE,
        "downloadable": False,
        "sha256": item["svg_sha256"],
        "size_bytes": len(item["svg"]),
        "generated_from": item["source_object_key"],
        "generated_at": occurred_at.isoformat(),
        "generator": {"name": RENDERER_NAME, "version": RENDERER_VERSION},
    }
    payload["visual_assets"] = visual_assets
    revision.payload_json = payload
    session.flush()

    record_asset_transformation(
        session,
        symbol_revision_id=revision.id,
        tool_name=RENDERER_NAME,
        tool_version=RENDERER_VERSION,
        source_asset_sha256=item["source_sha256"],
        derived_asset_sha256=item["svg_sha256"],
        performed_at=occurred_at,
        recorded_by_user_id=actor_id,
        evidence={"purpose": "display-only preview", "source_object_key": item["source_object_key"]},
    )
    authorization = ensure_preview_authorization(
        session, revision=revision, source="dxf_preview_backfill", created_at=occurred_at
    )
    if authorization.status not in {"created", "unchanged"}:
        raise ConfigurationError(f"preview authorization failed: {authorization.status}")
    session.add(
        AuditEvent(
            id=uuid.uuid4(),
            entity_type="symbol_revision",
            entity_id=revision.id,
            action="symbol_revision_preview_generated",
            actor_id=actor_id,
            payload_json={
                "catalog_symbol_id": item["catalog_symbol_id"],
                "object_key": item["object_key"],
                "svg_sha256": item["svg_sha256"],
                "source_object_key": item["source_object_key"],
                "source_sha256": item["source_sha256"],
                "generator": {"name": RENDERER_NAME, "version": RENDERER_VERSION},
            },
            created_at=occurred_at,
        )
    )
    session.flush()


def apply(
    session: Session,
    *,
    rendered: list[dict],
    actor_id: uuid.UUID,
    occurred_at: datetime,
    storage_env_file,
    uploader=upload_object_bytes,
) -> dict:
    if session.get(User, actor_id) is None:
        raise ConfigurationError(f"no user exists with id {actor_id}")
    recorded, failures = [], []
    for item in rendered:
        uploader(
            object_key=item["object_key"],
            payload=item["svg"],
            content_type=SVG_CONTENT_TYPE,
            env_file=storage_env_file,
        )
        try:
            # One savepoint per symbol, so one refusal does not undo the others.
            with session.begin_nested():
                _record_one(session, item, actor_id=actor_id, occurred_at=occurred_at)
        except ValueError as exc:
            failures.append({"catalog_symbol_id": item["catalog_symbol_id"], "reason": str(exc)})
            continue
        recorded.append({"catalog_symbol_id": item["catalog_symbol_id"], "object_key": item["object_key"]})
    if recorded:
        session.execute(text("SELECT refresh_published_symbol_views()"))
    return {"recorded": recorded, "failures": failures}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("plan", "Read-only: find, download and render every candidate; record nothing"),
        ("apply", "Upload and attach a generated preview to every candidate"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--storage-env-file", help="Object storage settings (default: the API's)")
        command.add_argument("--output", help="Write the full report here as JSON")
        if name == "apply":
            command.add_argument("--actor-id", type=uuid.UUID, required=True, help="Existing user UUID")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        storage_env_file = _storage_env_file(args)
        engine = _engine()
        try:
            if args.command == "plan":
                with Session(engine) as session:
                    candidates = find_candidates(session)
                    rendered, render_failures = render_candidates(candidates, storage_env_file=storage_env_file)
                    # Nothing was written; do not let the read outlive the report.
                    session.rollback()
                report = {
                    "command": "plan",
                    "candidates": len(candidates),
                    "rendered": len(rendered),
                    "render_failures": render_failures,
                    "would_record": [item["catalog_symbol_id"] for item in rendered],
                }
            else:
                with Session(engine) as session, session.begin():
                    candidates = find_candidates(session)
                    rendered, render_failures = render_candidates(candidates, storage_env_file=storage_env_file)
                    report = {
                        "command": "apply",
                        "candidates": len(candidates),
                        "rendered": len(rendered),
                        "render_failures": render_failures,
                    }
                    result = apply(
                        session,
                        rendered=rendered,
                        actor_id=args.actor_id,
                        occurred_at=datetime.now(timezone.utc),
                        storage_env_file=storage_env_file,
                    )
                    report["recorded_count"] = len(result["recorded"])
                    report["recorded"] = result["recorded"]
                    report["record_failures"] = result["failures"]
        finally:
            engine.dispose()
        if args.output:
            Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(json.dumps(report, indent=2, default=str))
        return 1 if report["render_failures"] or report.get("record_failures") else 0
    except ConfigurationError as exc:
        print(f"DXF preview backfill failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions may carry connection credentials. Never echo them.
        print(
            "DXF preview backfill failed; nothing was recorded. Check the actor id, "
            "storage settings and database readiness.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
