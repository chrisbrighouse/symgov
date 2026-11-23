"""Catalog rasterisation (design R1, D6): SVG -> resvg with a pinned font set; raster -> Pillow.

Output is an 8-bit grey image on white with the long side at `long_side_px`. A failed render is a
coverage gap reported to the caller, never a placeholder. Fixes recorded in benchmark Phase 1:
resvg mis-sizes physical-unit and tiny-viewBox SVGs, so the root size is set from the viewBox
(`normalize_svg_size`); dpi is 96 (dpi=0 collapses mm sizes); font families are rewritten through an
alias map because resvg has no aliasing and falls back to DejaVu Sans Bold for an unmatched family.
resvg fetches nothing: `resources_dir` is an empty directory and external references are not resolved.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from dataclasses import dataclass, field

import numpy as np
from PIL import Image

DEFAULT_ALIASES = {
    "arial": "Liberation Sans", "helvetica": "Liberation Sans", "arial unicode ms": "Liberation Sans",
    "verdana": "Liberation Sans", "tahoma": "Liberation Sans", "segoe ui": "Liberation Sans",
    "calibri": "Liberation Sans", "isocpeur": "Liberation Sans", "osifont": "Liberation Sans",
    "times": "Liberation Serif", "times new roman": "Liberation Serif", "courier": "Liberation Mono",
    "courier new": "Liberation Mono", "consolas": "Liberation Mono", "ibm plex sans": "Liberation Sans",
    "lucida console": "Liberation Mono", "liberation sans": "Liberation Sans",
}
GENERICS = {"serif", "sans-serif", "monospace", "cursive", "fantasy", "system-ui"}
MAX_SVG_BYTES = 8 * 1024 * 1024
_FONT_ATTR = re.compile(r'(font-family\s*=\s*)(["\'])(.*?)\2', re.IGNORECASE | re.DOTALL)
_FONT_CSS = re.compile(r'(font-family\s*:\s*)([^;"}<]+)', re.IGNORECASE)
_ROOT = re.compile(r"<svg\b[^>]*>", re.IGNORECASE | re.DOTALL)
_UNITS_PX = {"": 1.0, "px": 1.0, "pt": 96 / 72, "pc": 16.0, "mm": 96 / 25.4, "cm": 96 / 2.54, "in": 96.0}


def vendored_font_dir() -> str:
    """The pinned font set shipped with the package (DejaVu and Liberation; licences alongside)."""
    return str(Path(__file__).resolve().parent / "fonts")


class RenderError(RuntimeError):
    """The symbol could not be rendered: a coverage gap, never a reason to substitute an image."""


@dataclass(frozen=True)
class RenderConfig:
    font_dirs: tuple[str, ...]
    resources_dir: str                      # an empty directory
    font_set_id: str
    long_side_px: int = 512
    renderer_version: str = "resvg_py-0.5.0"
    font_alias_map: dict[str, str] = field(default_factory=lambda: dict(DEFAULT_ALIASES))
    font_family: str = "DejaVu Sans"
    sans_serif_family: str = "DejaVu Sans"
    serif_family: str = "DejaVu Serif"
    monospace_family: str = "DejaVu Sans Mono"


@dataclass(frozen=True)
class Rendered:
    image: np.ndarray               # uint8 grey, white background
    source_kind: str                # "svg" | "raster"
    source_size: tuple[float, float]


def rewrite_font_families(svg: str, alias_map: dict[str, str]) -> str:
    def fix(value: str) -> str:
        families = [f.strip().strip("'\"") for f in value.split(",") if f.strip()]
        out = list(dict.fromkeys(alias_map.get(f.lower(), f) for f in families))
        if not any(f.lower() in GENERICS for f in out):
            out.append("sans-serif")
        return ", ".join(f"'{f}'" if " " in f and f.lower() not in GENERICS else f for f in out)

    svg = _FONT_ATTR.sub(lambda m: m.group(1) + m.group(2) + fix(m.group(3)).replace(m.group(2), "") + m.group(2), svg)
    return _FONT_CSS.sub(lambda m: m.group(1) + fix(m.group(2)), svg)


def _attr(tag: str, name: str) -> str | None:
    m = re.search(rf'\s{name}\s*=\s*(["\'])(.*?)\1', tag, re.IGNORECASE | re.DOTALL)
    return m.group(2).strip() if m else None


def _length_px(value: str | None) -> float | None:
    if not value:
        return None
    m = re.fullmatch(r"([0-9.eE+-]+)\s*(px|pt|pc|mm|cm|in)?", value.strip())
    if not m:
        return None
    try:
        number = float(m.group(1))
    except ValueError:
        return None
    return number * _UNITS_PX[(m.group(2) or "").lower()] if number > 0 else None


def normalize_svg_size(svg: str, long_side_px: int) -> tuple[str, tuple[float, float]]:
    m = _ROOT.search(svg)
    if not m:
        raise RenderError("The SVG has no root element.")
    tag = m.group(0)
    vb = None
    view_box = _attr(tag, "viewBox")
    if view_box:
        try:
            parts = [float(p) for p in re.split(r"[\s,]+", view_box) if p]
            if len(parts) == 4 and parts[2] > 0 and parts[3] > 0:
                vb = parts
        except ValueError:
            vb = None
    width, height = _length_px(_attr(tag, "width")), _length_px(_attr(tag, "height"))
    if vb:
        w, h = vb[2], vb[3]
    elif width and height:
        w, h = width, height
    else:
        raise RenderError("The SVG has neither a usable viewBox nor a width and height.")
    scale = long_side_px / max(w, h)
    out_w, out_h = max(1, round(w * scale)), max(1, round(h * scale))
    new_tag = re.sub(r'\s(width|height)\s*=\s*(["\']).*?\2', "", tag, flags=re.IGNORECASE | re.DOTALL)
    extra = f' width="{out_w}" height="{out_h}"' + ("" if vb else f' viewBox="0 0 {w:g} {h:g}"')
    new_tag = new_tag[:-1].rstrip("/").rstrip() + extra + (" />" if tag.rstrip().endswith("/>") else ">")
    return svg[: m.start()] + new_tag + svg[m.end():], (w, h)


def _grey_on_white(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
        bg.alpha_composite(img)
        img = bg
    return img.convert("L")


def render(data: bytes, media_type: str, cfg: RenderConfig) -> Rendered:
    try:
        if media_type == "image/svg+xml":
            if len(data) > MAX_SVG_BYTES:
                raise RenderError("The SVG is too large.")
            import resvg_py  # imported here so the rest of the package works without the wheel

            svg = rewrite_font_families(data.decode("utf-8", "replace"), cfg.font_alias_map)
            svg, (w, h) = normalize_svg_size(svg, cfg.long_side_px)
            png = resvg_py.svg_to_bytes(
                svg_string=svg, dpi=96.0, background="#ffffff", skip_system_fonts=True,
                font_dirs=list(cfg.font_dirs), font_family=cfg.font_family,
                sans_serif_family=cfg.sans_serif_family, serif_family=cfg.serif_family,
                monospace_family=cfg.monospace_family, resources_dir=cfg.resources_dir,
            )
            img = _grey_on_white(Image.open(io.BytesIO(bytes(png))))
            return Rendered(np.asarray(img), "svg", (round(w, 4), round(h, 4)))
        if media_type in {"image/png", "image/jpeg"}:
            src = Image.open(io.BytesIO(data))
            src.load()
            w, h = src.size
            scale = cfg.long_side_px / max(w, h)
            img = _grey_on_white(src).resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.Resampling.LANCZOS)
            return Rendered(np.asarray(img), "raster", (float(w), float(h)))
    except RenderError:
        raise
    except Exception as exc:  # noqa: BLE001 - any decoder/renderer failure is a coverage gap
        raise RenderError("The symbol could not be rendered.") from exc
    raise RenderError(f"Unsupported media type {media_type!r}.")
