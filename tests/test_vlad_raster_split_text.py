"""Vlad's raster sheet analysis keeps drawings whole and does not turn lettering into symbols.

A sheet of heat exchangers with a bold title used to be split into one proposed
symbol per letter group of the title (14 junk records beside 9 real ones). The
sheets here are drawn from rectangles so the test needs no image library.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
import struct
import zlib

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_vlad():
    spec = importlib.util.spec_from_file_location("run_vlad_validation_for_split_text", REPO_ROOT / "scripts" / "run_vlad_validation.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_png(path: Path, width: int, height: int, ink: set[tuple[int, int]]) -> Path:
    rows = bytearray()
    for y in range(height):
        rows.append(0)
        rows.extend(0 if (x, y) in ink else 255 for x in range(width))

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(bytes(rows)))
        + chunk(b"IEND", b"")
    )
    return path


def filled(x: int, y: int, w: int, h: int):
    return {(px, py) for px in range(x, x + w) for py in range(y, y + h)}


def outline(x: int, y: int, w: int, h: int, thickness: int = 2):
    return filled(x, y, w, h) - filled(x + thickness, y + thickness, w - 2 * thickness, h - 2 * thickness)


def word(x: int, y: int, letters: int, letter_w: int = 14, letter_h: int = 22, gap: int = 4):
    ink = set()
    for index in range(letters):
        ink |= filled(x + index * (letter_w + gap), y, letter_w, letter_h)
    return ink


def sheet(tmp_path, *, title=True, symbols=None):
    ink = set()
    if title:
        ink |= word(20, 10, 4) | word(120, 10, 9)          # "HEAT" and "EXCHANGERS": bold, closely spaced letters
    for x, y, w, h in symbols or [(20, 80, 70, 50), (130, 80, 60, 60), (230, 80, 70, 50)]:
        ink |= outline(x, y, w, h)
    return write_png(tmp_path / "sheet.png", 360, 170, ink)


def test_lettering_is_not_proposed_as_symbols(tmp_path):
    analysis = load_vlad().analyze_raster_sheet(sheet(tmp_path))
    assert analysis["estimated_symbol_count"] == 3
    assert len(analysis["candidate_regions"]) == 3
    assert len(analysis["text_regions"]) == 2          # each word is one region, not one per letter
    assert {region["y"] for region in analysis["candidate_regions"]} == {80}
    assert all(region["y"] == 10 for region in analysis["text_regions"])
    assert analysis["sheet_type"] == "multi_symbol_sheet"


def test_a_symbol_made_of_nearby_pieces_stays_one_region(tmp_path):
    # A body with a detached arrow-like piece a few pixels away, as a heater and its arrow.
    body = outline(30, 80, 60, 60)
    arrow = filled(95, 90, 21, 6) | filled(95, 110, 21, 6)
    other = outline(200, 80, 70, 50)
    path = write_png(tmp_path / "pieces.png", 320, 170, body | arrow | other)
    analysis = load_vlad().analyze_raster_sheet(path)
    regions = sorted(analysis["candidate_regions"], key=lambda region: region["x"])
    assert len(regions) == 2
    assert regions[0]["x"] == 30 and regions[0]["x"] + regions[0]["width"] >= 116   # body and arrow together
    assert analysis["text_regions"] == []


def test_a_solid_symbol_is_not_mistaken_for_lettering(tmp_path):
    # Dense but square: a filled block is not wide and short.
    path = write_png(tmp_path / "solid.png", 320, 170, filled(30, 40, 50, 50) | outline(150, 40, 90, 50))
    analysis = load_vlad().analyze_raster_sheet(path)
    assert len(analysis["candidate_regions"]) == 2 and analysis["text_regions"] == []


def test_a_wide_solid_bar_taller_than_lettering_is_kept(tmp_path):
    path = write_png(tmp_path / "bar.png", 400, 220, filled(20, 20, 200, 90) | outline(260, 20, 100, 90))
    analysis = load_vlad().analyze_raster_sheet(path)
    assert len(analysis["candidate_regions"]) == 2 and analysis["text_regions"] == []


def test_the_split_plan_proposes_children_only_for_the_symbols(tmp_path):
    vlad = load_vlad()
    path = sheet(tmp_path)
    plan = vlad.build_split_plan(path, vlad.analyze_raster_sheet(path))
    assert plan["estimated_symbol_count"] == 3 and len(plan["regions"]) == 3
    assert plan["recommended_action"] == "create_proposed_children"


def test_a_sheet_of_only_lettering_has_nothing_to_split(tmp_path):
    path = write_png(tmp_path / "words.png", 360, 80, word(20, 20, 6) | word(160, 20, 8))
    analysis = load_vlad().analyze_raster_sheet(path)
    assert analysis["candidate_regions"] == [] and len(analysis["text_regions"]) == 2
    assert analysis["sheet_type"] == "ambiguous" and analysis["split_recommended"] is False
