from __future__ import annotations

import pytest

from symgov_backend.catalog_ed import interpret_catalog_ed_prompt


@pytest.mark.parametrize(
    ("prompt", "disciplines", "categories"),
    [
        ("fire alarm symbols", ["Fire & Life Safety"], ["Fire Alarm Devices"]),
        (
            "smoke detector symbols",
            ["Fire & Life Safety"],
            ["Fire Alarm Devices", "Sensors / Detectors"],
        ),
        ("electrical symbols", ["Electrical"], []),
        ("mechanical symbols", ["Mechanical"], []),
        ("P&ID symbols", ["Piping / P&ID"], []),
        ("valve symbols", [], ["Valves"]),
        ("pump symbols", [], ["Pumps"]),
        ("lighting symbols", ["Electrical"], ["Lighting"]),
        (
            "switchgear symbols",
            ["Electrical"],
            ["Switchgear / Distribution"],
        ),
        ("motor symbols", ["Electrical"], ["Motors / Drives"]),
    ],
)
def test_interprets_frontend_discipline_and_category_mappings(
    prompt: str,
    disciplines: list[str],
    categories: list[str],
):
    result = interpret_catalog_ed_prompt(prompt, mode="find_symbols")

    assert result.interpreted_filters.get("catalogDisciplines", []) == disciplines
    assert result.interpreted_filters.get("catalogCategories", []) == categories
    assert result.mutates_records is False


@pytest.mark.parametrize(
    ("prompt", "expected_use_case"),
    [
        ("editable CAD symbols", "Insert into CAD drawing"),
        ("symbols for drawing markup", "Mark up / annotate drawing"),
        ("symbols for a PDF report", "Use in PDF/report"),
    ],
)
def test_interprets_cad_markup_and_pdf_use_cases(prompt: str, expected_use_case: str):
    result = interpret_catalog_ed_prompt(prompt)

    assert expected_use_case in result.interpreted_filters["useCases"]
    assert result.mutates_records is False


def test_explicit_formats_are_normalized_and_canonically_ordered():
    result = interpret_catalog_ed_prompt("Need PDF JPG PNG SVG DWG and DXF symbols")

    assert result.interpreted_filters["availableFormats"] == [
        "DXF",
        "DWG",
        "SVG",
        "PNG",
        "JPG",
        "PDF",
    ]
    assert result.mutates_records is False


def test_pdf_use_case_adds_recommended_formats_only_without_an_explicit_format():
    implicit = interpret_catalog_ed_prompt("symbols for documentation")
    explicit = interpret_catalog_ed_prompt("symbols for a PDF")

    assert implicit.interpreted_filters["availableFormats"] == ["SVG", "PNG", "PDF"]
    assert explicit.interpreted_filters["availableFormats"] == ["PDF"]
    assert implicit.mutates_records is False
    assert explicit.mutates_records is False


@pytest.mark.parametrize("mode", ["find_symbols", "question"])
def test_explicit_allowed_modes_are_preserved(mode: str):
    result = interpret_catalog_ed_prompt("Where are smoke detectors?", mode=mode)

    assert result.selected_mode == mode
    assert result.mutates_records is False


def test_invalid_mode_fails_clearly():
    with pytest.raises(ValueError, match="mode must be one of: auto, find_symbols, question"):
        interpret_catalog_ed_prompt("smoke detector", mode="approve")


@pytest.mark.parametrize(
    ("prompt", "expected_mode"),
    [
        ("find smoke detector symbols", "find_symbols"),
        ("What does this catalog contain?", "question"),
        ("unclassified custom glyph", "find_symbols"),
    ],
)
def test_auto_mode_selection_is_deterministic(prompt: str, expected_mode: str):
    first = interpret_catalog_ed_prompt(prompt, mode="auto")
    second = interpret_catalog_ed_prompt(prompt, mode="auto")

    assert first == second
    assert first.selected_mode == expected_mode
    assert first.mutates_records is False


def test_no_matched_terms_falls_back_to_prompt_as_catalog_search():
    result = interpret_catalog_ed_prompt("unclassified custom glyph")

    assert result.selected_mode == "find_symbols"
    assert result.search_query == "unclassified custom glyph"
    assert result.interpreted_filters == {}
    assert any("exact filter matches" in warning for warning in result.warnings)
    assert result.mutates_records is False


def test_answer_is_guidance_not_approval_and_downloads_are_unavailable():
    result = interpret_catalog_ed_prompt("fire alarm DXF symbols")

    assert "guidance" in result.answer.lower()
    assert "does not approve" in result.answer.lower()
    assert "downloads are not available" in result.answer.lower()
    assert result.download_available is False
    assert result.mutates_records is False
