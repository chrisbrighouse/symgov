"""Wikimedia thumbnail URLs for Hannah's review and approval (WP-H4)."""

from __future__ import annotations

from symgov_backend.hannah_review import commons_thumb_url

ORIGINAL = "https://upload.wikimedia.org/wikipedia/commons/8/8c/Inline_Centrifugal_Fan.jpg"
THUMB = "https://upload.wikimedia.org/wikipedia/commons/thumb/8/8c/Inline_Centrifugal_Fan.jpg/1280px-Inline_Centrifugal_Fan.jpg"


def test_a_wide_original_becomes_a_thumbnail():
    assert commons_thumb_url(ORIGINAL, 1280, 6000) == THUMB


def test_the_imageinfo_tracking_query_is_dropped():
    # The 2026-10-20 production failure: a 20 MB original was fetched instead.
    tracked = f"{ORIGINAL}?utm_source=commons.wikimedia.org&utm_campaign=imageinfo&utm_content=original"
    assert commons_thumb_url(tracked, 1280, 6000) == THUMB
    assert commons_thumb_url(f"{ORIGINAL}#top", 1280, 6000) == THUMB


def test_a_narrow_original_or_another_site_is_left_alone():
    assert commons_thumb_url(ORIGINAL, 1280, 1000) == ORIGINAL
    assert commons_thumb_url("https://example.org/a/ab/Fan.jpg", 1280, 6000) == "https://example.org/a/ab/Fan.jpg"
