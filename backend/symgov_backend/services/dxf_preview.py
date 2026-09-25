"""Render a stored DXF symbol to an SVG preview. A pure file service.

Seventeen public symbols from the 2026-06 external submission were published
with a DXF source and nothing a browser can show, so the Catalogue can only
draw a placeholder for them. DXF stays the governed asset and the download;
this produces a display-only SVG from it, which `dxf_preview_backfill` attaches
as the revision's generated preview.

Drawn with `ezdxf`'s own drawing add-on and SVG backend, in black on a
transparent background, fitted to the drawing's extents with a small margin.
Callers own storage, the database and access control.
"""
from __future__ import annotations

import io
from dataclasses import dataclass

import ezdxf
from ezdxf import recover
from ezdxf.addons.drawing import Frontend, RenderContext, config, layout, svg

RENDERER_NAME = "symgov dxf_preview (ezdxf drawing add-on, SVG backend)"
RENDERER_VERSION = f"1.0 / ezdxf {ezdxf.__version__}"

# The largest submitted symbol DXF is tens of kilobytes; a limit well above
# that keeps a pathological file from being parsed at all.
MAX_INPUT_BYTES = 5 * 1024 * 1024


class DxfPreviewError(ValueError):
    """A DXF that cannot be drawn. Carries a code and a detail naming no secret."""

    def __init__(self, code: str, detail: str):
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class DxfPreview:
    svg: bytes
    entity_count: int


def render_dxf_preview(data: bytes) -> DxfPreview:
    if not data:
        raise DxfPreviewError("empty_input", "The DXF is empty.")
    if len(data) > MAX_INPUT_BYTES:
        raise DxfPreviewError("input_too_large", f"The DXF exceeds {MAX_INPUT_BYTES} bytes.")
    try:
        # `recover` reads both ASCII and binary DXF and tolerates the minor
        # structural faults CAD exports commonly carry.
        doc, auditor = recover.read(io.BytesIO(data))
    except (IOError, ezdxf.DXFStructureError) as exc:
        raise DxfPreviewError("unreadable_dxf", type(exc).__name__) from exc
    if auditor.has_errors:
        raise DxfPreviewError("unrecoverable_dxf", f"{len(auditor.errors)} structural errors")

    modelspace = doc.modelspace()
    entity_count = len(modelspace)
    if entity_count == 0:
        raise DxfPreviewError("no_geometry", "The DXF model space has no entities.")

    backend = svg.SVGBackend()
    settings = config.Configuration(
        background_policy=config.BackgroundPolicy.OFF,
        color_policy=config.ColorPolicy.BLACK,
    )
    Frontend(RenderContext(doc), backend, config=settings).draw_layout(modelspace)
    # Width and height 0 fit the page to the drawing's extents.
    page = layout.Page(0, 0, layout.Units.mm, margins=layout.Margins.all(2))
    rendered = backend.get_string(page).encode("utf-8")
    if b"<path" not in rendered:
        raise DxfPreviewError("nothing_drawn", "The DXF produced no drawable geometry.")
    return DxfPreview(svg=rendered, entity_count=entity_count)
