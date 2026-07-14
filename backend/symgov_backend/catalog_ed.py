from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Literal

from .catalog_taxonomy import (
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    CATALOG_USE_CASE_ORDER,
    FORMAT_ORDER,
    compact_unique,
    sort_by_preferred_order,
)

CatalogEdMode = Literal["auto", "find_symbols", "question"]
SelectedCatalogEdMode = Literal["find_symbols", "question"]
_ALLOWED_MODES = ("auto", "find_symbols", "question")

_GUIDANCE_DISCLAIMER = (
    "Catalog Ed provides guidance only and does not approve symbols or engineering decisions. "
    "Downloads are not available from this service."
)


@dataclass(frozen=True)
class CatalogEdResult:
    selected_mode: SelectedCatalogEdMode
    answer: str
    search_query: str
    interpreted_filters: dict[str, list[str]]
    suggested_followups: list[str]
    warnings: list[str]
    download_available: bool = False
    mutates_records: bool = False


@dataclass
class _Interpretation:
    disciplines: list[str]
    categories: list[str]
    use_cases: list[str]
    formats: list[str]
    matched_terms: list[str]


def _contains(pattern: str, prompt: str) -> bool:
    return re.search(pattern, prompt, flags=re.IGNORECASE) is not None


def _interpret_terms(raw_prompt: str) -> _Interpretation:
    prompt = raw_prompt.lower()
    interpretation = _Interpretation([], [], [], [], [])
    explicit_format = any(
        _contains(rf"\b{re.escape(format_name)}\b", raw_prompt)
        for format_name in FORMAT_ORDER
    )

    def match(pattern: str, label: str, *, disciplines=(), categories=(), use_cases=()) -> None:
        if not _contains(pattern, prompt):
            return
        interpretation.disciplines.extend(disciplines)
        interpretation.categories.extend(categories)
        interpretation.use_cases.extend(use_cases)
        interpretation.matched_terms.append(label)

    match(
        r"\b(fire|alarm|smoke|heat detector|break\s?glass|call\s?point|sounder|beacon)\b",
        "fire alarm",
        disciplines=("Fire & Life Safety",),
        categories=("Fire Alarm Devices",),
    )
    match(
        r"\b(detector|sensor|smoke|heat|co\b|carbon monoxide)\b",
        "detector/sensor",
        categories=("Sensors / Detectors",),
    )
    match(
        r"\b(electrical|elec|switchgear|distribution|lighting)\b",
        "electrical",
        disciplines=("Electrical",),
    )
    match(
        r"\b(switchgear|distribution|panelboard|panelboards|switchboard|switchboards)\b",
        "switchgear/distribution",
        categories=("Switchgear / Distribution",),
    )
    match(
        r"\b(lighting|light|lights|luminaire|luminaires)\b",
        "lighting",
        categories=("Lighting",),
    )
    match(
        r"\b(mechanical|mech)\b",
        "mechanical",
        disciplines=("Mechanical",),
    )
    match(
        r"\b(motor|motors|drive|drives|vfd|starter|starters)\b",
        "motors/drives",
        disciplines=("Electrical",),
        categories=("Motors / Drives",),
    )
    match(
        r"\b(p\s?&\s?id|pid|piping|pipework|process)\b",
        "piping/p&id",
        disciplines=("Piping / P&ID",),
    )
    match(r"\b(valve|valves)\b", "valves", categories=("Valves",))
    match(r"\b(pump|pumps)\b", "pumps", categories=("Pumps",))
    match(
        r"\b(cad|dxf|dwg|insert|editable)\b",
        "cad",
        use_cases=("Insert into CAD drawing",),
    )
    match(
        r"\b(markup|marking up|drawing review|review|annotate|annotation|redline|png|jpg|jpeg)\b",
        "markup",
        use_cases=("Mark up / annotate drawing",),
    )
    if _contains(r"\b(pdf|reports?|documents?|documentation)\b", prompt):
        interpretation.use_cases.append("Use in PDF/report")
        interpretation.matched_terms.append("documentation")
        if not explicit_format:
            interpretation.formats.extend(("SVG", "PNG", "PDF"))

    for format_name in FORMAT_ORDER:
        if _contains(rf"\b{re.escape(format_name)}\b", raw_prompt):
            interpretation.formats.append("JPG" if format_name == "JPEG" else format_name)
            interpretation.matched_terms.append(format_name)

    return interpretation


def _select_mode(mode: CatalogEdMode, prompt: str, has_matches: bool) -> SelectedCatalogEdMode:
    if mode != "auto":
        return mode
    if has_matches:
        return "find_symbols"
    if _contains(r"^\s*(what|why|how|when|where|which|who|can|does|do|is|are)\b|\?\s*$", prompt):
        return "question"
    return "find_symbols"


def interpret_catalog_ed_prompt(
    prompt: str = "",
    *,
    mode: CatalogEdMode = "auto",
) -> CatalogEdResult:
    if mode not in _ALLOWED_MODES:
        raise ValueError("mode must be one of: auto, find_symbols, question")

    raw_prompt = str(prompt or "").strip()
    interpretation = _interpret_terms(raw_prompt)
    matched_terms = compact_unique(interpretation.matched_terms)
    selected_mode = _select_mode(mode, raw_prompt, bool(matched_terms))

    interpreted_filters = {
        "catalogDisciplines": sort_by_preferred_order(
            interpretation.disciplines, CATALOG_DISCIPLINE_ORDER
        ),
        "catalogCategories": sort_by_preferred_order(
            interpretation.categories, CATALOG_CATEGORY_ORDER
        ),
        "useCases": sort_by_preferred_order(
            interpretation.use_cases, CATALOG_USE_CASE_ORDER
        ),
        "availableFormats": sort_by_preferred_order(
            interpretation.formats, FORMAT_ORDER
        ),
    }
    interpreted_filters = {
        key: values for key, values in interpreted_filters.items() if values
    }
    search_terms = compact_unique(
        [
            value
            for values in interpreted_filters.values()
            for value in values
        ]
    )
    search_query = " ".join(search_terms) or raw_prompt

    warnings: list[str] = []
    if not matched_terms:
        warnings.append(
            "Ed did not find exact filter matches; the prompt is used as a Catalog search only."
        )

    if selected_mode == "find_symbols":
        answer = "Ed interpreted the prompt as a Catalog symbol search. "
        suggested_followups = [
            "Review the interpreted filters before relying on the results.",
            "Refine the prompt with a discipline, category, use case, or format.",
        ]
    else:
        answer = "Ed interpreted the prompt as a Catalog guidance question. "
        suggested_followups = [
            "Ask for symbols by discipline, category, use case, or format."
        ]

    return CatalogEdResult(
        selected_mode=selected_mode,
        answer=answer + _GUIDANCE_DISCLAIMER,
        search_query=search_query,
        interpreted_filters=interpreted_filters,
        suggested_followups=suggested_followups,
        warnings=warnings,
    )
