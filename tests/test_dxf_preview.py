"""The DXF preview renderer and the backfill's candidate rule, without a database.

The database half is `test_dxf_preview_backfill_postgresql.py`.
"""
from __future__ import annotations

import io
import uuid

import ezdxf
import pytest

from symgov_backend.asset_manifest import list_download_assets
from symgov_backend.dxf_preview_backfill import dxf_source_asset, preview_object_key
from symgov_backend.image_content import validate_stored_image
from symgov_backend.published_catalog import choose_published_preview_asset, published_fallback_source_asset
from symgov_backend.services.dxf_preview import DxfPreviewError, render_dxf_preview


def _dxf_bytes(*, draw: bool = True) -> bytes:
    doc = ezdxf.new()
    if draw:
        msp = doc.modelspace()
        msp.add_circle((0, 0), radius=5)
        msp.add_line((-5, 0), (5, 0))
    stream = io.StringIO()
    doc.write(stream)
    return stream.getvalue().encode("utf-8")


DXF_ONLY_PAYLOAD = {
    "name": "Pump",
    "source_object_key": "external-submissions/x/members/0016-pump/PipeAcc_Equipment_Pump.dxf",
}


class TestTheRenderer:
    def test_a_drawing_renders_to_a_valid_svg(self):
        preview = render_dxf_preview(_dxf_bytes())
        assert preview.entity_count == 2
        assert b"<path" in preview.svg
        assert validate_stored_image(preview.svg, "image/svg+xml") == "image/svg+xml"

    def test_rendering_is_deterministic(self):
        assert render_dxf_preview(_dxf_bytes()).svg == render_dxf_preview(_dxf_bytes()).svg

    @pytest.mark.parametrize(
        ("data", "code"),
        [(b"", "empty_input"), (b"definitely not a dxf", "unreadable_dxf")],
    )
    def test_unusable_input_is_refused_with_a_code(self, data, code):
        with pytest.raises(DxfPreviewError) as refused:
            render_dxf_preview(data)
        assert refused.value.code == code

    def test_an_empty_model_space_is_refused(self):
        with pytest.raises(DxfPreviewError) as refused:
            render_dxf_preview(_dxf_bytes(draw=False))
        assert refused.value.code == "no_geometry"


class TestTheCandidateRule:
    def test_a_dxf_only_payload_is_a_candidate(self):
        asset = dxf_source_asset(DXF_ONLY_PAYLOAD)
        assert asset is not None and asset["object_key"] == DXF_ONLY_PAYLOAD["source_object_key"]

    def test_a_previewable_payload_is_not(self):
        payload = {**DXF_ONLY_PAYLOAD, "visual_assets": {"preview": {"object_key": "p/x.svg", "format": "svg"}}}
        assert dxf_source_asset(payload) is None

    def test_the_generated_preview_is_shown_but_never_offered_as_a_download(self):
        """What `_record_one` writes: the Catalogue previews the SVG, the DXF stays the download."""
        key = preview_object_key(uuid.uuid4(), "a" * 64)
        payload = {
            **DXF_ONLY_PAYLOAD,
            "visual_assets": {
                "preview": {
                    "object_key": key,
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_preview",
                    "downloadable": False,
                }
            },
        }
        assert choose_published_preview_asset(payload)["object_key"] == key
        downloads = list_download_assets(payload, fallback_source_asset=published_fallback_source_asset(payload))
        assert [asset["object_key"] for asset in downloads] == [DXF_ONLY_PAYLOAD["source_object_key"]]
        # A re-run finds nothing left to do.
        assert dxf_source_asset(payload) is None

    def test_preview_keys_are_content_addressed_per_revision(self):
        revision = uuid.uuid4()
        assert preview_object_key(revision, "b" * 64) == f"derived-previews/{revision}/{'b' * 64}.svg"
