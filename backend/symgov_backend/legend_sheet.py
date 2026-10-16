"""Legend sheet: a Symbol Set's or Project's symbols as one printable SVG.

The sheet is an A3 landscape grid, 420 x 297 mm per page, pages stacked
vertically in one SVG (each page is a `<g class="legend-page">`). Every cell
nests the symbol's own SVG, captioned with its human-readable ID, its name, its
DEXPI class and its group. A title block carries the attribution lines the
imported libraries ask every share to carry. PDF is the browser's job (print
the page, "Save as PDF"); nothing here renders a PDF.

Only `xml.etree` is used. Symbol SVGs come from storage, so they are treated as
untrusted: a document with a DOCTYPE or entity declaration is refused, active
content (script, foreignObject, event handlers, external references) is
removed, and every id is prefixed per cell so two symbols cannot capture each
other's gradients, clip paths or `<use>` targets.
"""

from __future__ import annotations

import re
import uuid
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .image_content import UnsafeImageContentError, validate_stored_image
from .models import RightsRecord, SourcePackageEntry, SymbolRevision
from .published_catalog import choose_published_preview_asset
from .published_preview_authorizations import resolve_authorized_preview_attachment
from .runtime import download_object_bytes
from .symbol_identity import governed_symbol_human_readable_id

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"

PAGE_WIDTH_MM = 420
PAGE_HEIGHT_MM = 297
MARGIN_MM = 12
TITLE_BLOCK_MM = 30          # first page only
FOOTER_MM = 8
COLUMNS = 6
CELL_HEIGHT_MM = 50
CAPTION_MM = 15
MAX_SYMBOLS = 500
MAX_SVG_BYTES = 2_000_000

_FORBIDDEN_TAGS = {"script", "foreignobject", "iframe", "audio", "video", "animate", "set", "animatetransform", "animatemotion", "handler", "listener"}
_ID_REFERENCE = re.compile(r"url\(\s*(['\"]?)#([^)'\"\s]+)\1\s*\)")
_NUMBER = re.compile(r"^\s*([0-9]*\.?[0-9]+)")


class LegendSvgError(ValueError):
    """A symbol's SVG cannot be nested safely; the cell shows a placeholder."""


@dataclass
class LegendCell:
    display_id: str
    name: str
    dexpi_class: str | None = None
    group: str | None = None
    svg: bytes | None = None
    attribution: str | None = None


@dataclass
class LegendSheet:
    title: str
    subtitle: str = ""
    cells: list[LegendCell] = field(default_factory=list)
    omitted: int = 0


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def _parse_svg(payload: bytes) -> ET.Element:
    if len(payload) > MAX_SVG_BYTES:
        raise LegendSvgError("SVG is too large.")
    head = payload.lower()
    if b"<!doctype" in head or b"<!entity" in head:
        raise LegendSvgError("SVG declares a DOCTYPE or entities.")
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise LegendSvgError("SVG is not well-formed.") from exc
    if _local(root.tag) != "svg":
        raise LegendSvgError("Document root is not svg.")
    return root


def _safe_reference(value: str) -> bool:
    value = value.strip()
    return value.startswith("#") or value.lower().startswith("data:image/")


def nested_symbol(payload: bytes, prefix: str) -> ET.Element:
    """The symbol's SVG made safe to nest, with every id prefixed by `prefix`."""
    root = _parse_svg(payload)
    ids = {el.get("id") for el in root.iter() if el.get("id")}

    def clean(parent: ET.Element) -> None:
        for child in list(parent):
            if _local(child.tag).lower() in _FORBIDDEN_TAGS:
                parent.remove(child)
                continue
            clean(child)

    clean(root)
    for el in root.iter():
        for name in list(el.attrib):
            local = name.rsplit("}", 1)[-1]
            value = el.attrib[name]
            if local.lower().startswith("on"):
                del el.attrib[name]
            elif local == "href":
                if not _safe_reference(value):
                    del el.attrib[name]
                elif value.startswith("#") and value[1:] in ids:
                    el.attrib[name] = f"#{prefix}{value[1:]}"
            elif local == "id":
                el.attrib[name] = f"{prefix}{value}"
            elif local in {"style"} or "url(" in value:
                if "url(" in value:
                    def swap(match):
                        target = match.group(2)
                        return f"url(#{prefix}{target})" if target in ids else "none"
                    value = _ID_REFERENCE.sub(swap, value)
                    # Anything still holding an external url() is dropped.
                    if re.search(r"url\(\s*['\"]?(?!#)", value):
                        del el.attrib[name]
                        continue
                    el.attrib[name] = value
        if _local(el.tag).lower() == "style" and el.text:
            if "@import" in el.text.lower() or re.search(r"url\(\s*['\"]?(?!#)", el.text):
                el.text = ""
            else:
                el.text = _ID_REFERENCE.sub(
                    lambda m: f"url(#{prefix}{m.group(2)})" if m.group(2) in ids else "none", el.text
                )
    return root


def _viewbox_of(root: ET.Element) -> tuple[float, float, float, float]:
    raw = root.get("viewBox") or root.get("viewbox")
    if raw:
        parts = [p for p in re.split(r"[\s,]+", raw.strip()) if p]
        if len(parts) == 4:
            try:
                x, y, w, h = (float(p) for p in parts)
            except ValueError:
                x = y = w = h = 0
            if w > 0 and h > 0:
                return x, y, w, h
    width = _NUMBER.match(root.get("width") or "")
    height = _NUMBER.match(root.get("height") or "")
    if width and height and float(width.group(1)) > 0 and float(height.group(1)) > 0:
        return 0.0, 0.0, float(width.group(1)), float(height.group(1))
    raise LegendSvgError("SVG has no usable size.")


def _text(parent: ET.Element, x: float, y: float, content: str, *, size: float, weight: str = "400", fill: str = "#0f172a", anchor: str = "start") -> ET.Element:
    el = ET.SubElement(parent, f"{{{SVG_NS}}}text", {
        "x": f"{x:.2f}", "y": f"{y:.2f}", "font-size": f"{size:.2f}", "font-weight": weight,
        "fill": fill, "text-anchor": anchor, "font-family": "Arial, Helvetica, sans-serif",
    })
    el.text = content
    return el


def _clip(content: str, limit: int) -> str:
    content = " ".join(str(content or "").split())
    return content if len(content) <= limit else content[: limit - 1].rstrip() + "…"


def _attribution_lines(cells: list[LegendCell]) -> list[str]:
    seen: list[str] = []
    for cell in cells:
        text = " ".join(str(cell.attribution or "").split())
        if text and text not in seen:
            seen.append(text)
    return seen


def _page_count(count: int, first_page_rows: int, other_rows: int) -> int:
    capacity_first = first_page_rows * COLUMNS
    if count <= capacity_first:
        return 1
    rest = count - capacity_first
    return 1 + -(-rest // (other_rows * COLUMNS))


def build_legend_svg(sheet: LegendSheet) -> bytes:
    ET.register_namespace("", SVG_NS)
    ET.register_namespace("xlink", XLINK_NS)
    attribution = _attribution_lines(sheet.cells)
    attribution_mm = 4.2 * len(attribution[:12]) + (4.2 if attribution else 0)
    usable_height = PAGE_HEIGHT_MM - 2 * MARGIN_MM - FOOTER_MM
    first_rows = max(1, int((usable_height - TITLE_BLOCK_MM - attribution_mm) // CELL_HEIGHT_MM))
    other_rows = max(1, int(usable_height // CELL_HEIGHT_MM))
    pages = _page_count(len(sheet.cells), first_rows, other_rows)

    root = ET.Element(f"{{{SVG_NS}}}svg", {
        "width": f"{PAGE_WIDTH_MM}mm", "height": f"{PAGE_HEIGHT_MM * pages}mm",
        "viewBox": f"0 0 {PAGE_WIDTH_MM} {PAGE_HEIGHT_MM * pages}",
    })
    ET.SubElement(root, f"{{{SVG_NS}}}title").text = sheet.title
    cell_width = (PAGE_WIDTH_MM - 2 * MARGIN_MM) / COLUMNS

    index = 0
    for page in range(pages):
        top = page * PAGE_HEIGHT_MM
        group = ET.SubElement(root, f"{{{SVG_NS}}}g", {"class": "legend-page", "data-page": str(page + 1)})
        ET.SubElement(group, f"{{{SVG_NS}}}rect", {
            "x": "0", "y": f"{top}", "width": f"{PAGE_WIDTH_MM}", "height": f"{PAGE_HEIGHT_MM}", "fill": "#ffffff",
        })
        y = top + MARGIN_MM
        rows = other_rows
        if page == 0:
            _text(group, MARGIN_MM, y + 8, _clip(sheet.title, 90), size=8, weight="700")
            if sheet.subtitle:
                _text(group, MARGIN_MM, y + 14.5, _clip(sheet.subtitle, 140), size=3.6, fill="#475569")
            for line_no, line in enumerate(attribution[:12]):
                _text(group, MARGIN_MM, y + 21 + 4.2 * line_no, _clip(line, 200), size=2.8, fill="#475569")
            y += TITLE_BLOCK_MM + attribution_mm
            rows = first_rows
        for row in range(rows):
            for column in range(COLUMNS):
                if index >= len(sheet.cells):
                    break
                cell = sheet.cells[index]
                x = MARGIN_MM + column * cell_width
                cy = y + row * CELL_HEIGHT_MM
                _draw_cell(group, cell, index, x, cy, cell_width)
                index += 1
        _text(
            group, PAGE_WIDTH_MM - MARGIN_MM, top + PAGE_HEIGHT_MM - MARGIN_MM + 2,
            f"Page {page + 1} of {pages}", size=2.8, fill="#64748b", anchor="end",
        )
        if sheet.omitted and page == pages - 1:
            _text(
                group, MARGIN_MM, top + PAGE_HEIGHT_MM - MARGIN_MM + 2,
                f"{sheet.omitted} further symbols not shown (a sheet holds at most {MAX_SYMBOLS}).",
                size=2.8, fill="#64748b",
            )
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="utf-8")


def _draw_cell(parent: ET.Element, cell: LegendCell, index: int, x: float, y: float, width: float) -> None:
    pad = 2.0
    ET.SubElement(parent, f"{{{SVG_NS}}}rect", {
        "x": f"{x + 0.5:.2f}", "y": f"{y + 0.5:.2f}", "width": f"{width - 1:.2f}", "height": f"{CELL_HEIGHT_MM - 1:.2f}",
        "fill": "none", "stroke": "#cbd5e1", "stroke-width": "0.25",
    })
    art_h = CELL_HEIGHT_MM - CAPTION_MM - 2 * pad
    art_w = width - 2 * pad
    placed = False
    if cell.svg:
        try:
            symbol = nested_symbol(cell.svg, f"c{index}-")
            vx, vy, vw, vh = _viewbox_of(symbol)
            scale = min(art_w / vw, art_h / vh)
            w, h = vw * scale, vh * scale
            for attr in ("width", "height", "x", "y", "viewbox"):
                symbol.attrib.pop(attr, None)
            symbol.set("viewBox", f"{vx:g} {vy:g} {vw:g} {vh:g}")
            symbol.set("x", f"{x + pad + (art_w - w) / 2:.2f}")
            symbol.set("y", f"{y + pad + (art_h - h) / 2:.2f}")
            symbol.set("width", f"{w:.2f}")
            symbol.set("height", f"{h:.2f}")
            symbol.set("overflow", "hidden")
            parent.append(symbol)
            placed = True
        except LegendSvgError:
            placed = False
    if not placed:
        _text(parent, x + width / 2, y + pad + art_h / 2, "No drawing available", size=2.6, fill="#64748b", anchor="middle")
    base = y + CELL_HEIGHT_MM - CAPTION_MM + 3.5
    _text(parent, x + pad, base, _clip(cell.display_id, 24), size=3.2, weight="700")
    _text(parent, x + pad, base + 3.6, _clip(cell.name, 34), size=2.7)
    if cell.dexpi_class:
        _text(parent, x + pad, base + 7.0, _clip(cell.dexpi_class, 38), size=2.3, fill="#475569")
    if cell.group:
        _text(parent, x + pad, base + 9.8, _clip(cell.group, 38), size=2.3, fill="#64748b")


# --- Loading the cells from the database -------------------------------------


def _dexpi_class(payload: dict) -> str | None:
    dexpi = payload.get("dexpi") if isinstance(payload, dict) else None
    classes = dexpi.get("component_classes") if isinstance(dexpi, dict) else None
    if isinstance(classes, list) and classes and isinstance(classes[0], str):
        return classes[0]
    return None


def _rights_attribution(session: Session, revision_id: uuid.UUID, payload: dict) -> str | None:
    dexpi = payload.get("dexpi") if isinstance(payload, dict) else None
    if not isinstance(dexpi, dict):
        return None
    if dexpi.get("attribution_source") != "rights_record":
        return dexpi.get("attribution") or None
    if dexpi.get("attribution"):
        return dexpi["attribution"]
    record = (
        session.query(RightsRecord)
        .join(SourcePackageEntry, SourcePackageEntry.source_package_id == RightsRecord.source_package_id)
        .filter(SourcePackageEntry.symbol_revision_id == revision_id, RightsRecord.decision_status == "approved")
        .order_by(RightsRecord.created_at.desc())
        .first()
    )
    evidence = record.evidence_json if record is not None else None
    text = evidence.get("attribution_text") if isinstance(evidence, dict) else None
    return text or None


def _svg_bytes(session: Session, revision_id: uuid.UUID, payload: dict, settings) -> bytes | None:
    """The symbol's authorized SVG preview, or None when it has none.

    Same chain as the preview route: the payload names the asset, the asset
    must be an authorized attachment of this revision, and the bytes must sniff
    as an SVG.
    """
    asset = choose_published_preview_asset(payload, requested_format="svg")
    object_key = asset.get("object_key") if asset else None
    if not object_key:
        return None
    attachment = resolve_authorized_preview_attachment(session, revision_id=revision_id, object_key=object_key)
    if attachment is None:
        return None
    try:
        stored = download_object_bytes(object_key=object_key, env_file=str(settings.storage_env_file), max_bytes=MAX_SVG_BYTES)
        media_type = validate_stored_image(stored["payload"], attachment.content_type, stored.get("content_type"))
    except (UnsafeImageContentError, OSError, ValueError, KeyError):
        return None
    return stored["payload"] if media_type == "image/svg+xml" else None


def load_cells(session: Session, settings, entries: list[dict]) -> LegendSheet:
    """Cells for `entries` (dicts with `governed`, `revisionId`, `group`, `label`).

    Entries must already be the caller's authorized, eligible membership; this
    only reads each one's current revision.
    """
    sheet = LegendSheet(title="")
    chosen = entries[:MAX_SYMBOLS]
    sheet.omitted = max(0, len(entries) - MAX_SYMBOLS)
    revisions = {
        revision.id: revision
        for revision in session.query(SymbolRevision).filter(
            SymbolRevision.id.in_([entry["revisionId"] for entry in chosen if entry.get("revisionId")])
        ).all()
    } if chosen else {}
    for entry in chosen:
        governed = entry["governed"]
        revision = revisions.get(entry.get("revisionId"))
        payload = (revision.payload_json if revision is not None else None) or {}
        display_id = entry.get("displayId") or governed.catalog_symbol_id or governed.slug
        cell = LegendCell(
            display_id=str(display_id),
            name=entry.get("label") or governed.canonical_name,
            dexpi_class=_dexpi_class(payload),
            group=entry.get("group"),
        )
        revision_id = entry.get("revisionId")
        if revision_id is not None:
            cell.svg = _svg_bytes(session, revision_id, payload, settings)
            cell.attribution = _rights_attribution(session, revision_id, payload)
        sheet.cells.append(cell)
    return sheet


# --- The two routes' entry points ---------------------------------------------


def set_legend(session: Session, request, settings, set_id: uuid.UUID) -> tuple[LegendSheet, str]:
    """Legend of one Symbol Set: its eligible items, in set order.

    Authorization is `symbol_set_service.get_set`'s (404 for another
    organization's set); an item that is no longer eligible is left out, as in
    the effective palette.
    """
    from .models import GovernedSymbol, SymbolSetItem
    from . import symbol_set_service
    from .symbol_eligibility import current_symbol_revisions
    from .symbol_set_service import get_set

    principal, row = get_set(session, request, settings, set_id)
    rows = session.query(SymbolSetItem, GovernedSymbol).join(
        GovernedSymbol, GovernedSymbol.id == SymbolSetItem.governed_symbol_id,
    ).filter(SymbolSetItem.symbol_set_id == row.id).order_by(SymbolSetItem.sort_order, SymbolSetItem.governed_symbol_id).all()
    current = current_symbol_revisions(
        session, [item.governed_symbol_id for item, _ in rows], principal.organization.id,
        organization_symbols_enabled=bool(getattr(settings, "organization_symbols_enabled", False)),
        public_resolver=symbol_set_service.current_public_symbols,
    )
    entries = [
        {
            "governed": governed, "revisionId": current[item.governed_symbol_id],
            "displayId": governed_symbol_human_readable_id(session, governed),
            "group": item.group_name, "label": item.display_label,
        }
        for item, governed in rows if item.governed_symbol_id in current
    ]
    sheet = load_cells(session, settings, entries)
    sheet.title = f"{row.name} legend" if getattr(row, "name", None) else "Symbol Set legend"
    sheet.subtitle = f"{principal.organization.name} · {len(entries)} symbols" if getattr(principal.organization, "name", None) else f"{len(entries)} symbols"
    return sheet, str(row.id)


def project_legend(session: Session, request, settings, project_id: uuid.UUID, *, set_code: str | None = None) -> tuple[LegendSheet, str]:
    """Legend of one Project's effective palette (its active set plus organization-wide symbols)."""
    from .effective_palette import resolve_effective_palette

    principal, project, symbol_set, _reason, palette = resolve_effective_palette(
        session, request, settings, project_id, set_code=set_code,
    )
    entries = [
        {
            "governed": entry["_governed"], "revisionId": entry["currentRevisionId"],
            "displayId": governed_symbol_human_readable_id(session, entry["_governed"]),
            "group": entry["groupName"], "label": entry["displayLabel"],
        }
        for entry in palette
    ]
    sheet = load_cells(session, settings, entries)
    project_name = getattr(project, "name", None) or "Project"
    sheet.title = f"{project_name} legend"
    set_name = getattr(symbol_set, "name", None) if symbol_set is not None else None
    sheet.subtitle = " · ".join(part for part in (set_name, f"{len(entries)} symbols") if part)
    return sheet, str(project.id)
