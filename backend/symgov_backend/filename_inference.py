from __future__ import annotations

import re
from pathlib import Path
from typing import Any

DISCIPLINE_PREFIXES: dict[str, str] = {
    "elec": "Electrical",
    "elect": "Electrical",
    "electrical": "Electrical",
    "mech": "Mechanical",
    "mechanical": "Mechanical",
    "hvac": "HVAC",
    "instr": "Instrumentation",
    "instrument": "Instrumentation",
    "instrumentation": "Instrumentation",
    "piping": "Piping",
    "pipe": "Piping",
    "fire": "Fire Protection",
}

PRESERVE_UPPER_TOKENS = {"BMS", "DP", "DXF", "FCU", "HVAC", "JPG", "MCC", "PDF", "PNG", "SVG", "UPS", "VAV", "VFD"}

GENERIC_TOKENS = {
    "drawing",
    "dwg",
    "image",
    "img",
    "jpeg",
    "jpg",
    "legend",
    "page",
    "pdf",
    "photo",
    "png",
    "scan",
    "sheet",
    "symbol",
    "svg",
}

TRAILING_NOISE_TOKENS = {"a11y", "accessible", "accessibility"}


def _tokenize_stem(stem: str) -> list[str]:
    normalized = re.sub(r"[\s_\-]+", " ", str(stem or "").strip())
    return [part for part in normalized.split(" ") if part]


def _display_token(token: str) -> str:
    if not token:
        return ""
    lower = token.lower()
    if lower in TRAILING_NOISE_TOKENS:
        return ""
    discipline = DISCIPLINE_PREFIXES.get(lower)
    if discipline:
        return discipline
    if token in PRESERVE_UPPER_TOKENS:
        return token
    if any(char.islower() for char in token) and any(char.isupper() for char in token[1:]):
        return token
    if token.isupper() and token.isalpha():
        return token.capitalize()
    return token[:1].upper() + token[1:]


def infer_filename_metadata(filename: str | None) -> dict[str, Any]:
    raw_filename = Path(str(filename or "")).name
    stem = Path(raw_filename).stem
    raw_tokens = _tokenize_stem(stem)
    trimmed_tokens = list(raw_tokens)
    while trimmed_tokens and trimmed_tokens[-1].lower() in TRAILING_NOISE_TOKENS:
        trimmed_tokens.pop()

    evidence: list[str] = []
    display_tokens: list[str] = []
    generic_token_count = 0
    discipline_hint = None

    for index, token in enumerate(trimmed_tokens):
        lower = token.lower()
        if lower in GENERIC_TOKENS:
            generic_token_count += 1
            evidence.append("generic_token")
        display = _display_token(token)
        if not display:
            continue
        if discipline_hint is None and index == 0:
            discipline_hint = DISCIPLINE_PREFIXES.get(lower)
            if discipline_hint:
                evidence.append("discipline_prefix")
        elif discipline_hint is None and lower in {"electrical", "mechanical", "instrumentation", "piping", "hvac"}:
            discipline_hint = _display_token(token)
            evidence.append("discipline_token")
        display_tokens.append(display)

    if not display_tokens and stem:
        display_tokens = [stem]

    informative_tokens = [token for token in trimmed_tokens if token.lower() not in GENERIC_TOKENS]
    inferred_name = " ".join(display_tokens).strip()
    inferred_title = inferred_name
    description_fallback = inferred_name

    confidence = 0.2 if inferred_name else 0.0
    if len(informative_tokens) >= 2:
        confidence += 0.3
        evidence.append("multi_token_name")
    elif len(informative_tokens) == 1:
        confidence += 0.15
    if discipline_hint:
        confidence += 0.35
    if any(any(char.islower() for char in token) and any(char.isupper() for char in token[1:]) for token in trimmed_tokens):
        confidence += 0.05
        evidence.append("compound_token")
    if generic_token_count:
        confidence -= min(0.25, generic_token_count * 0.15)
    if not informative_tokens:
        confidence = min(confidence, 0.4)
    if len(trimmed_tokens) <= 1 and generic_token_count:
        confidence = min(confidence, 0.35)
    confidence = round(max(0.0, min(0.95, confidence)), 2)

    return {
        "raw_filename": raw_filename,
        "raw_stem": stem,
        "raw_tokens": raw_tokens,
        "normalized_tokens": [token.lower() for token in trimmed_tokens],
        "display_tokens": display_tokens,
        "inferred_name": inferred_name,
        "inferred_title": inferred_title,
        "description_fallback": description_fallback,
        "discipline_hint": discipline_hint,
        "confidence": confidence,
        "evidence": sorted(set(evidence)),
    }


def inferred_candidate_title(filename: str | None) -> str:
    return str(infer_filename_metadata(filename).get("inferred_title") or "")
