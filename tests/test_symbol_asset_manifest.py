from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from symgov_backend.asset_manifest import (  # noqa: E402
    choose_preview_asset,
    is_browser_previewable,
    list_download_assets,
)


def test_browser_previewable_accepts_web_images_by_type_extension_and_format():
    assert is_browser_previewable(content_type="image/png")
    assert is_browser_previewable(content_type="image/jpeg")
    assert is_browser_previewable(content_type="image/jpg")
    assert is_browser_previewable(content_type="image/svg+xml")
    assert is_browser_previewable(filename="symbol.png")
    assert is_browser_previewable(filename="symbol.JPG")
    assert is_browser_previewable(filename="symbol.jpeg")
    assert is_browser_previewable(filename="symbol.svg")
    assert is_browser_previewable(format="png")
    assert is_browser_previewable(format="jpg")
    assert is_browser_previewable(format="jpeg")
    assert is_browser_previewable(format="svg")


def test_browser_previewable_rejects_dxf_and_zip():
    assert not is_browser_previewable(content_type="application/dxf")
    assert not is_browser_previewable(content_type="application/x-dxf")
    assert not is_browser_previewable(filename="symbol.dxf")
    assert not is_browser_previewable(format="dxf")
    assert not is_browser_previewable(content_type="application/zip")
    assert not is_browser_previewable(filename="bundle.zip")
    assert not is_browser_previewable(format="zip")


def test_browser_previewable_normalizes_content_type_parameters_and_blocks_dxf_over_extension():
    assert is_browser_previewable(content_type="image/svg+xml; charset=utf-8")
    assert is_browser_previewable(content_type="IMAGE/PNG; charset=binary")
    assert not is_browser_previewable(content_type="application/dxf; charset=binary", filename="preview.png")
    assert not is_browser_previewable(content_type="application/zip; charset=binary", filename="preview.svg")


def test_choose_preview_prefers_explicit_preview_key_when_previewable():
    payload = {
        "preview_object_key": "assets/explicit.png",
        "preview_content_type": "image/png",
        "preview_filename": "explicit.png",
        "visual_assets": {
            "preview": {
                "object_key": "assets/companion.jpg",
                "filename": "companion.jpg",
                "content_type": "image/jpeg",
                "format": "jpg",
            }
        },
    }

    assert choose_preview_asset(payload) == {
        "object_key": "assets/explicit.png",
        "filename": "explicit.png",
        "content_type": "image/png",
        "format": "png",
        "role": "preview",
    }


def test_choose_preview_normalizes_explicit_preview_asset():
    payload = {
        "preview_object_key": "  12345  ",
        "preview_content_type": "image/png; charset=binary",
    }

    assert choose_preview_asset(payload) == {
        "object_key": "12345",
        "filename": "12345",
        "content_type": "image/png",
        "format": "png",
        "role": "preview",
    }


def test_choose_preview_rejects_blank_explicit_preview_key():
    payload = {
        "preview_object_key": "   ",
        "preview_content_type": "image/png",
        "visual_assets": {
            "preview": {
                "object_key": "assets/companion.jpg",
                "filename": "companion.jpg",
                "content_type": "image/jpeg",
                "format": "jpg",
            }
        },
    }

    assert choose_preview_asset(payload)["object_key"] == "assets/companion.jpg"


def test_companion_jpg_beats_generated_svg_when_visual_preview_is_set():
    payload = {
        "visual_assets": {
            "preview": {
                "object_key": "assets/source-preview.jpg",
                "filename": "source-preview.jpg",
                "content_type": "image/jpeg",
                "format": "jpg",
                "role": "companion_preview",
                "label": "Contributor preview JPG",
            },
            "derivatives": [
                {
                    "object_key": "assets/generated.svg",
                    "filename": "generated.svg",
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_preview",
                }
            ],
        }
    }

    selected = choose_preview_asset(payload)

    assert selected["object_key"] == "assets/source-preview.jpg"
    assert selected["label"] == "Contributor preview JPG"


def test_generated_svg_beats_raw_dxf_when_only_dxf_source_and_svg_derivative_exist():
    payload = {
        "visual_assets": {
            "source_assets": [
                {
                    "object_key": "assets/raw.dxf",
                    "filename": "raw.dxf",
                    "content_type": "application/dxf",
                    "format": "dxf",
                    "role": "source",
                }
            ],
            "derivatives": [
                {
                    "object_key": "assets/generated.svg",
                    "filename": "generated.svg",
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_preview",
                }
            ],
        }
    }

    assert choose_preview_asset(payload)["object_key"] == "assets/generated.svg"


def test_raw_browser_image_source_is_usable_as_preview():
    payload = {
        "visual_assets": {
            "source_assets": [
                {
                    "object_key": "assets/raw.png",
                    "filename": "raw.png",
                    "content_type": "image/png",
                    "format": "png",
                    "role": "source",
                }
            ]
        }
    }

    assert choose_preview_asset(payload)["object_key"] == "assets/raw.png"


def test_preview_asset_preserves_metadata_and_inferrs_filename_from_object_key():
    payload = {
        "visual_assets": {
            "source_assets": [
                {
                    "object_key": "assets/raw.PNG",
                    "content_type": "image/png; charset=binary",
                    "role": "source",
                    "sha256": "abc123",
                    "size_bytes": 123,
                    "downloadable": True,
                }
            ]
        }
    }

    selected = choose_preview_asset(payload)

    assert selected["object_key"] == "assets/raw.PNG"
    assert selected["filename"] == "raw.PNG"
    assert selected["format"] == "png"
    assert selected["sha256"] == "abc123"
    assert selected["size_bytes"] == 123
    assert selected["downloadable"] is True


def test_raw_dxf_alone_is_not_usable_as_preview():
    payload = {
        "visual_assets": {
            "source_assets": [
                {
                    "object_key": "assets/raw.dxf",
                    "filename": "raw.dxf",
                    "content_type": "application/x-dxf",
                    "format": "dxf",
                    "role": "source",
                }
            ]
        }
    }

    assert choose_preview_asset(payload) is None


def test_preview_falls_back_to_previewable_fallback_source_asset():
    payload = {"visual_assets": {"source_assets": []}}
    fallback = {
        "object_key": "assets/fallback.svg",
        "filename": "fallback.svg",
        "content_type": "image/svg+xml",
        "format": "svg",
        "role": "source",
    }

    assert choose_preview_asset(payload, fallback_source_asset=fallback)["object_key"] == "assets/fallback.svg"


def test_list_download_assets_includes_sources_companions_and_structured_downloads_deduped():
    payload = {
        "visual_assets": {
            "preview": {
                "object_key": "assets/companion.jpg",
                "filename": "companion.jpg",
                "content_type": "image/jpeg",
                "format": "jpg",
                "role": "companion_preview",
            },
            "source_assets": [
                {
                    "object_key": "assets/raw.dxf",
                    "filename": "raw.dxf",
                    "content_type": "application/dxf",
                    "format": "dxf",
                    "role": "source",
                },
                {
                    "object_key": "assets/companion-source.png",
                    "filename": "companion-source.png",
                    "content_type": "image/png",
                    "format": "png",
                    "role": "companion_source",
                },
            ],
            "derivatives": [
                {
                    "object_key": "assets/generated.svg",
                    "filename": "generated.svg",
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_preview",
                }
            ],
        },
        "downloads": [
            {
                "object_key": "assets/raw.dxf",
                "filename": "raw-again.dxf",
                "content_type": "application/dxf",
                "format": "dxf",
                "label": "Duplicate raw source",
            },
            {
                "object_key": "assets/license.txt",
                "filename": "license.txt",
                "content_type": "text/plain",
                "format": "txt",
                "label": "License",
            },
        ],
    }

    downloads = list_download_assets(payload)

    assert [asset["object_key"] for asset in downloads] == [
        "assets/raw.dxf",
        "assets/companion-source.png",
        "assets/companion.jpg",
        "assets/license.txt",
    ]
    assert "assets/generated.svg" not in [asset["object_key"] for asset in downloads]


def test_list_download_assets_excludes_generated_preview_when_it_is_visual_preview():
    payload = {
        "visual_assets": {
            "preview": {
                "object_key": "assets/generated.svg",
                "filename": "generated.svg",
                "content_type": "image/svg+xml",
                "format": "svg",
                "role": "generated_preview",
            },
            "source_assets": [
                {
                    "object_key": "assets/raw.dxf",
                    "filename": "raw.dxf",
                    "content_type": "application/dxf",
                    "format": "dxf",
                    "role": "source",
                }
            ],
        }
    }

    assert [asset["object_key"] for asset in list_download_assets(payload)] == ["assets/raw.dxf"]


def test_list_download_assets_includes_explicitly_downloadable_derivatives():
    payload = {
        "visual_assets": {
            "source_assets": [
                {
                    "object_key": "assets/raw.dxf",
                    "filename": "raw.dxf",
                    "content_type": "application/dxf",
                    "format": "dxf",
                    "role": "source",
                }
            ],
            "derivatives": [
                {
                    "object_key": "assets/generated.svg",
                    "filename": "generated.svg",
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_preview",
                },
                {
                    "object_key": "assets/export.svg",
                    "filename": "export.svg",
                    "content_type": "image/svg+xml",
                    "format": "svg",
                    "role": "generated_export",
                    "downloadable": True,
                },
            ],
        }
    }

    assert [asset["object_key"] for asset in list_download_assets(payload)] == [
        "assets/raw.dxf",
        "assets/export.svg",
    ]


def test_list_download_assets_includes_legacy_fallback_source_asset():
    downloads = list_download_assets(
        {"downloads": [{"object_key": "assets/readme.txt", "filename": "readme.txt"}]},
        fallback_source_asset={
            "object_key": "assets/legacy.dxf",
            "filename": "legacy.dxf",
            "content_type": "application/dxf",
            "format": "dxf",
            "role": "source",
        },
    )

    assert [asset["object_key"] for asset in downloads] == ["assets/legacy.dxf", "assets/readme.txt"]
