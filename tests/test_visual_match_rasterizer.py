"""Catalog rasterisation: sizing fixes, font rewriting, no placeholders. Text rendering needs the pinned fonts."""
import os
import tempfile

import numpy as np
import pytest

from symgov_backend.visual_match.rasterizer import (
    vendored_font_dir,
    RenderConfig, RenderError, normalize_svg_size, render, rewrite_font_families,
)

FONTS = os.environ.get("SYMGOV_VISUAL_MATCH_FONT_DIR") or vendored_font_dir()


@pytest.fixture
def cfg(tmp_path):
    res = tmp_path / "res"; res.mkdir()
    return RenderConfig(font_dirs=(FONTS,) if FONTS else (), resources_dir=str(res), font_set_id="test")


def ink_box(img):
    r, c = np.nonzero(img < 250)
    return r.min(), r.max(), c.min(), c.max()


def test_physical_units_do_not_collapse(cfg):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" width="16.35mm" height="8mm" viewBox="0 0 16.35 8"><rect x="1" y="1" width="14" height="6" fill="none" stroke="black" stroke-width="0.3"/></svg>'
    out = render(svg, "image/svg+xml", cfg)
    assert max(out.image.shape) == 512 and (out.image < 250).any()


def test_tiny_viewbox_without_size_renders(cfg):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 0.0099 0.0099"><circle cx="0.005" cy="0.005" r="0.004" fill="none" stroke="black" stroke-width="0.0003"/></svg>'
    out = render(svg, "image/svg+xml", cfg)
    assert max(out.image.shape) == 512 and (out.image < 250).any()


def test_aspect_ratio_is_kept(cfg):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 10"><line x1="0" y1="5" x2="40" y2="5" stroke="black" stroke-width="1"/></svg>'
    assert render(svg, "image/svg+xml", cfg).image.shape == (128, 512)


def test_no_viewbox_no_size_is_a_coverage_gap(cfg):
    with pytest.raises(RenderError):
        render(b'<svg xmlns="http://www.w3.org/2000/svg"><circle r="3"/></svg>', "image/svg+xml", cfg)


def test_garbage_and_unsupported_types_are_errors_not_images(cfg):
    for data, mt in ((b"<not svg", "image/svg+xml"), (b"x", "image/png"), (b"x", "application/pdf")):
        with pytest.raises(RenderError):
            render(data, mt, cfg)


def test_external_references_are_not_fetched(cfg):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 10 10"><image xlink:href="http://127.0.0.1:9/x.png" width="10" height="10"/><line x1="0" y1="5" x2="10" y2="5" stroke="black"/></svg>'
    assert (render(svg, "image/svg+xml", cfg).image < 250).any()


def test_font_family_rewrite():
    out = rewrite_font_families('<text font-family="Arial">M</text><text style="font-family: Courier New">x</text>', {"arial": "Liberation Sans", "courier new": "Liberation Mono"})
    assert "Liberation Sans" in out and "'Liberation Mono'" in out and "sans-serif" in out


def test_normalize_size_rewrites_root():
    svg, (w, h) = normalize_svg_size('<svg viewBox="0 0 20 10" width="3mm">', 100)
    assert 'width="100"' in svg and 'height="50"' in svg and (w, h) == (20, 10)


@pytest.mark.skipif(not FONTS, reason="needs SYMGOV_VISUAL_MATCH_FONT_DIR (the pinned fonts)")
def test_text_renders_with_pinned_fonts(cfg):
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 20"><text x="4" y="15" font-family="Arial" font-size="14">M</text></svg>'
    assert (render(svg, "image/svg+xml", cfg).image < 250).any()


def test_raster_is_scaled_to_the_long_side(cfg):
    import io
    from PIL import Image
    buf = io.BytesIO(); Image.new("RGB", (100, 50), "white").save(buf, format="PNG")
    assert render(buf.getvalue(), "image/png", cfg).image.shape == (256, 512)
