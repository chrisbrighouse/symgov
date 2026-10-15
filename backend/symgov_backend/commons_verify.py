"""Acceptance checks for the Wikimedia Commons import, the near-duplicate report, and the import report.

`verify` reads the database (and, unless told not to, object storage) and
answers each acceptance check pass or fail with the numbers behind the answer.
It never writes. Two of its checks never fail: the dHash near-duplicate report
is information for the product owner, and it is skipped where no SVG renderer
is available (the production API container has none). `render_report` turns
the saved JSON reports into Markdown.
"""
from __future__ import annotations

import base64
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import tempfile
from typing import Any
import uuid

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .catalog_facets import CATALOG_DISCIPLINE_ORDER as FACET_DISCIPLINES
from .catalog_facets import catalog_taxonomy_for_symbol as browser_taxonomy
from .catalog_taxonomy import CATALOG_DISCIPLINE_ORDER as TAXONOMY_DISCIPLINES
from .image_content import sniff_image_media_type
from .models import (
    ClassificationNode,
    ClassificationScheme,
    GovernedSymbol,
    RightsRecord,
    SemanticConcept,
    SemanticConceptRevision,
    SourcePackage,
    StandardVersion,
    Standard,
    SymbolRevisionClassificationAssignment,
    SymbolSemanticAssignment,
    SymbolStandardLink,
)
from .published_catalog import PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL, choose_published_preview_asset
from .published_symbol_details import load_published_symbol_details
from .services import commons_ingestion as plan_module

WORKBENCH_JS = Path(__file__).resolve().parents[2] / "frontend" / "src" / "catalogWorkbench.js"
DHASH_DISTANCE_THRESHOLD = 4  # publication_handoff.detect_graphical_duplicates' defaults
PIXEL_DIFFERENCE_THRESHOLD = 0.08
CELL = 128


def _check(check_id: str, title: str, status: bool | str, detail: Any = None) -> dict[str, Any]:
    if isinstance(status, bool):
        status = "pass" if status else "fail"
    return {"id": check_id, "title": title, "status": status, "detail": detail}


def _uuid(value: Any) -> uuid.UUID:
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


# --------------------------------------------------------------------------
# Discipline parity.
# --------------------------------------------------------------------------


def workbench_disciplines(source: str) -> list[str]:
    match = re.search(r"export const CATALOG_DISCIPLINE_ORDER = \[(.*?)\];", source, re.S)
    if match is None:
        raise ValueError("CATALOG_DISCIPLINE_ORDER not found in catalogWorkbench.js")
    return re.findall(r"'([^']*)'", match.group(1))


def discipline_parity(js_path: Path = WORKBENCH_JS) -> dict[str, Any]:
    """The three discipline lists, and whether they agree. The JS file is absent in the API image."""
    detail: dict[str, Any] = {"catalog_facets": FACET_DISCIPLINES, "catalog_taxonomy": TAXONOMY_DISCIPLINES}
    agree = FACET_DISCIPLINES == TAXONOMY_DISCIPLINES
    if js_path.is_file():
        js = workbench_disciplines(js_path.read_text(encoding="utf-8"))
        detail["catalogWorkbench.js"] = js
        agree = agree and js == FACET_DISCIPLINES
    else:
        detail["catalogWorkbench.js"] = "not present here; frontend/src/catalogWorkbench.test.js checks it"
    detail["agree"] = agree
    return detail


# --------------------------------------------------------------------------
# dHash near duplicates.
# --------------------------------------------------------------------------


def rasterize(images: list[bytes], chromium: str, *, cell: int = CELL, columns: int = 32) -> list[Any]:
    """Each image drawn on white in a `cell` square by headless Chromium, as Pillow images.

    One browser run per 512 images: the images are laid out in a grid, the page
    is screenshotted, and the grid is cut back up. `<img>` never runs script.
    """
    from PIL import Image

    out: list[Any] = []
    per_page = columns * 16
    with tempfile.TemporaryDirectory() as directory:
        for start in range(0, len(images), per_page):
            chunk = images[start:start + per_page]
            rows = (len(chunk) + columns - 1) // columns
            cells = []
            for data in chunk:
                media = sniff_image_media_type(data) or "image/svg+xml"
                encoded = base64.b64encode(data).decode("ascii")
                cells.append(f'<div><img src="data:{media};base64,{encoded}"></div>')
            page = Path(directory) / f"grid-{start}.html"
            page.write_text(
                "<!doctype html><html><head><style>"
                f"html,body{{margin:0;background:#fff}}body{{display:grid;grid-template-columns:repeat({columns},{cell}px);grid-auto-rows:{cell}px}}"
                "div{width:100%;height:100%;overflow:hidden}img{width:100%;height:100%;object-fit:contain;display:block}"
                "</style></head><body>" + "".join(cells) + "</body></html>",
                encoding="utf-8",
            )
            shot = Path(directory) / f"grid-{start}.png"
            subprocess.run(
                [chromium, "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                 "--allow-file-access-from-files", "--virtual-time-budget=10000",
                 f"--window-size={columns * cell},{rows * cell}", f"--screenshot={shot}", page.as_uri()],
                check=True, capture_output=True, timeout=600,
            )
            with Image.open(shot) as grid:
                grid = grid.convert("L")
                for index in range(len(chunk)):
                    x, y = (index % columns) * cell, (index // columns) * cell
                    out.append(grid.crop((x, y, x + cell, y + cell)).copy())
    return out


def _normalised(image) -> Any:
    """The ink cropped to its bounding box and centred on a white square, so placement does not count."""
    from PIL import Image

    box = image.point(lambda value: 255 if value < 245 else 0).getbbox()
    if box is None:
        return None
    ink = image.crop(box)
    side = max(ink.size)
    square = Image.new("L", (side, side), 255)
    square.paste(ink, ((side - ink.size[0]) // 2, (side - ink.size[1]) // 2))
    return square


def _dhash(image) -> int:
    small = list(image.resize((9, 8)).getdata())
    value = 0
    for row in range(8):
        for col in range(8):
            value = (value << 1) | (1 if small[row * 9 + col] > small[row * 9 + col + 1] else 0)
    return value


def _pixel_difference(left, right) -> float:
    a = list(left.resize((CELL, CELL)).getdata())
    b = list(right.resize((CELL, CELL)).getdata())
    return sum(abs(x - y) for x, y in zip(a, b)) / (255.0 * len(a))


def near_duplicates(
    candidates: list[dict[str, Any]], existing: list[dict[str, Any]], *, chromium: str
) -> dict[str, Any]:
    """dHash near-duplicates among the imported symbols and against what was already published.

    Each item carries `id`, `name` and `data` (image bytes). The thresholds are
    `publication_handoff.detect_graphical_duplicates`' own (distance <= 4,
    pixel difference <= 0.08); unlike it, each image is first cropped to its
    ink, because line symbols are mostly white space.
    """
    every = candidates + existing
    rendered = rasterize([item["data"] for item in every], chromium)
    prepared = []
    blank = []
    for item, image in zip(every, rendered):
        square = _normalised(image)
        if square is None:
            blank.append(item["id"])
            continue
        prepared.append({**{k: v for k, v in item.items() if k != "data"}, "image": square, "hash": _dhash(square)})
    new = [item for item in prepared if item.get("candidate")]
    old = [item for item in prepared if not item.get("candidate")]

    def compare(left_items, right_items, same_set: bool):
        matches = []
        for i, left in enumerate(left_items):
            for j, right in enumerate(right_items):
                if same_set and j <= i:
                    continue
                distance = (left["hash"] ^ right["hash"]).bit_count()
                if distance > DHASH_DISTANCE_THRESHOLD:
                    continue
                difference = _pixel_difference(left["image"], right["image"])
                if difference <= PIXEL_DIFFERENCE_THRESHOLD:
                    matches.append({
                        "symbol": left["id"], "name": left["name"],
                        "matches": right["id"], "matches_name": right["name"],
                        "hamming_distance": distance, "pixel_difference": round(difference, 4),
                    })
        return matches

    return {
        "method": "headless Chromium raster, ink-cropped; dHash 8x8, distance <= 4 and pixel difference <= 0.08",
        "candidates": len(candidates),
        "existing_compared": len(existing),
        "blank_renders": blank,
        "against_existing": compare(new, old, False),
        "within_import": compare(new, new, True),
    }


# --------------------------------------------------------------------------
# Verify.
# --------------------------------------------------------------------------


def _published_rows(session: Session) -> list[Any]:
    return list(session.execute(text(PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL)).all())


def _latest_gate_reasons(session: Session, slugs: list[str]) -> dict[str, list[str]]:
    if not slugs:
        return {}
    rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (gs.slug) gs.slug, e.refusal_reasons_json
            FROM publication_gate_evaluations e
            JOIN symbol_revisions sr ON sr.id = e.symbol_revision_id
            JOIN governed_symbols gs ON gs.id = sr.symbol_id
            WHERE gs.slug = ANY(:slugs)
            ORDER BY gs.slug, e.evaluated_at DESC, e.id
            """
        ),
        {"slugs": slugs},
    ).all()
    return {row.slug: list(row.refusal_reasons_json or []) for row in rows}


def verify(
    session: Session,
    plan: dict[str, Any],
    package_data: dict[str, Any],
    *,
    fetcher=None,
    storage_env_file=None,
    chromium: str | None = None,
) -> dict[str, Any]:
    from .routes.published import list_published_data_sources, published_symbol_row

    checks: list[dict[str, Any]] = []
    symbols = plan["symbols"]
    by_slug = {symbol["slug"]: symbol for symbol in symbols}
    rows = _published_rows(session)
    commons_rows = {row.slug: row for row in rows if row.pack_code == plan["publication"]["pack_code"]}

    # 1. Every planned symbol is published, minus the excluded files.
    unpublished = sorted(set(by_slug) - set(commons_rows))
    reasons = _latest_gate_reasons(session, unpublished)
    checks.append(_check(
        "all-published",
        f"All {len(symbols)} planned symbols are published in \"{plan['publication']['pack_title']}\" "
        f"({len(plan['excluded'])} excluded by the fix step)",
        not unpublished,
        {"published": len(commons_rows), "expected": len(symbols), "unpublished": len(unpublished),
         "unpublished_by_gate_reason": dict(Counter(",".join(reasons.get(slug) or ["no gate evaluation"]) for slug in unpublished)),
         "unpublished_slugs": unpublished[:80]},
    ))

    # 2. Stored as planned, with the Commons provenance block.
    mismatches = []
    for slug, row in commons_rows.items():
        payload = row.payload_json or {}
        symbol = by_slug[slug]
        commons = payload.get("commons") or {}
        if ((payload.get("assets") or [{}])[0]).get("sha256") != symbol["final_sha256"]:
            mismatches.append(f"{slug}: SVG digest differs")
        if not all(commons.get(key) for key in ("author", "licence", "page_url", "pageid")):
            mismatches.append(f"{slug}: commons block incomplete")
        if row.canonical_name != symbol["canonical_name"] or row.discipline != symbol["discipline"] or row.category != symbol["category"]:
            mismatches.append(f"{slug}: name, discipline or category differs from the worksheet")
    checks.append(_check("stored-as-planned", "Name, discipline, category, SVG digest and Commons provenance match the plan",
                         not mismatches, mismatches[:10] or None))

    # 3. Rights.
    package = session.execute(select(SourcePackage).where(SourcePackage.package_code == plan_module.PACKAGE_CODE)).scalar_one_or_none()
    record = None
    if package is not None:
        record = session.execute(
            select(RightsRecord).where(RightsRecord.source_package_id == package.id, RightsRecord.decision_status == "approved")
        ).scalar_one_or_none()
    evidence = (record.evidence_json if record is not None else {}) or {}
    files = evidence.get("files") or []
    complete = all(all(item.get(key) not in (None, "", []) for key in plan_module.required_evidence_keys()) for item in files)
    checks.append(_check(
        "rights-record",
        "One approved rights record (open / distribute) with per-file Commons evidence, decided by a named person",
        record is not None and (record.rights_status, record.disposition) == ("open", "distribute")
        and record.decided_by_user_id is not None and len(files) == len(symbols) and complete,
        {"status": getattr(record, "rights_status", None), "disposition": getattr(record, "disposition", None),
         "decided": bool(getattr(record, "decided_by_user_id", None)), "evidence_files": len(files), "complete": complete},
    ))

    # 4. Standard links: vendor_implementation only, verified.
    link_rows = session.execute(
        select(GovernedSymbol.slug, SymbolStandardLink.relationship_type, SymbolStandardLink.assertion_status,
               Standard.standard_code, StandardVersion.version_label)
        .join(SymbolStandardLink, SymbolStandardLink.symbol_revision_id == GovernedSymbol.current_revision_id)
        .join(StandardVersion, StandardVersion.id == SymbolStandardLink.standard_version_id)
        .join(Standard, Standard.id == StandardVersion.standard_id)
        .where(GovernedSymbol.slug.in_(list(by_slug)))
    ).all()
    links_by_slug: dict[str, list[Any]] = defaultdict(list)
    for item in link_rows:
        links_by_slug[item.slug].append(item)
    link_problems = []
    for slug, symbol in by_slug.items():
        expected = sorted((a["standard_code"], a["version_label"]) for a in symbol["standards"])
        found = sorted((l.standard_code, l.version_label) for l in links_by_slug.get(slug, []) if l.assertion_status == "verified")
        if expected != found:
            link_problems.append(f"{slug}: expected {expected}, found {found}")
    normative = [l.slug for l in link_rows if l.relationship_type != "vendor_implementation"]
    iso = sum(1 for l in link_rows if l.standard_code == plan_module.ISO_10628_CODE and l.assertion_status == "verified")
    checks.append(_check("standard-links", "Each cited standard is a verified vendor_implementation link; none is normative_definition",
                         not link_problems and not normative,
                         {"iso_10628": iso, "by_standard": dict(Counter(l.standard_code for l in link_rows)),
                          "problems": link_problems[:10], "not_vendor_implementation": normative[:10]}))

    # 5. Concepts.
    concept_rows = session.execute(
        select(GovernedSymbol.slug, SemanticConcept.id, SemanticConceptRevision.preferred_name)
        .join(SymbolSemanticAssignment, SymbolSemanticAssignment.symbol_revision_id == GovernedSymbol.current_revision_id)
        .join(SemanticConcept, SemanticConcept.id == SymbolSemanticAssignment.semantic_concept_id)
        .join(SemanticConceptRevision, SemanticConceptRevision.id == SemanticConcept.current_revision_id)
        .where(GovernedSymbol.slug.in_(list(by_slug)), SymbolSemanticAssignment.status == "verified",
               SymbolSemanticAssignment.assignment_role == "primary")
    ).all()
    concept_of = {row.slug: (row.id, row.preferred_name) for row in concept_rows}
    wrong = [slug for slug, symbol in by_slug.items()
             if concept_of.get(slug, (None, None))[1] != plan_module.concept_name_for(symbol["concept_key"])]
    checks.append(_check("concepts", "Every symbol carries the concept the worksheet names (existing reused, NEW: created once)",
                         not wrong, wrong[:10] or None))

    # 6. Alternative drawings resolve.
    by_key: dict[str, set] = defaultdict(set)
    for slug, symbol in by_slug.items():
        if slug in concept_of:
            by_key[symbol["concept_key"]].add(concept_of[slug][0])
    split_keys = sorted(key for key, ids in by_key.items() if len(ids) > 1)
    # Which published drawings in other packs carry each concept, read independently of Details.
    other_packs: dict[Any, set[str]] = defaultdict(set)
    other_revisions = {str(row.symbol_revision_id): row for row in rows if row.pack_code != plan["publication"]["pack_code"]}
    if other_revisions:
        for assignment in session.execute(
            select(SymbolSemanticAssignment.symbol_revision_id, SymbolSemanticAssignment.semantic_concept_id).where(
                SymbolSemanticAssignment.status == "verified",
                SymbolSemanticAssignment.symbol_revision_id.in_([_uuid(value) for value in other_revisions]),
            )
        ).all():
            other_packs[assignment.semantic_concept_id].add(other_revisions[str(assignment.symbol_revision_id)].slug)
    unresolved, resolved, no_other_drawing = [], 0, []
    for slug, row in commons_rows.items():
        symbol = by_slug[slug]
        if plan_module.is_new_concept(symbol["concept_key"]) or slug not in concept_of:
            continue
        expected = other_packs.get(concept_of[slug][0], set())
        shown = {item["slug"] for item in load_published_symbol_details(session, row)["sameConcept"]}
        if not expected:
            no_other_drawing.append(f"{slug} ({symbol['concept_key']})")
        elif shown and shown <= expected:
            resolved += 1
        else:
            unresolved.append(f"{slug} ({symbol['concept_key']})")
    checks.append(_check(
        "concept-alternatives",
        "A reused concept's other published drawings appear in Details; each shared NEW: key is one concept",
        not split_keys and not unresolved,
        {"reused_and_resolved": resolved, "unresolved": unresolved[:20], "new_keys_split_over_concepts": split_keys,
         "reused_concept_with_no_other_published_drawing": no_other_drawing,
         "note": "Details lists other drawings from other packs only, so symbols sharing a NEW: concept within this pack do not list each other."},
    ))

    # 7. HVAC facet.
    hvac_published = []
    fire_facet = []
    for slug, row in commons_rows.items():
        taxonomy = browser_taxonomy(published_symbol_row(row))
        if "HVAC" in taxonomy["disciplines"]:
            hvac_published.append(slug)
        if "Fire & Life Safety" in taxonomy["disciplines"]:
            fire_facet.append(slug)
    stored_hvac = session.execute(
        text(
            "SELECT count(*) FROM catalog_symbol_facets f JOIN governed_symbols gs ON gs.id = f.governed_symbol_id "
            "WHERE gs.slug = ANY(:slugs) AND f.disciplines ? 'HVAC' AND f.rules_version > 0"
        ),
        {"slugs": list(commons_rows)},
    ).scalar_one()
    expected_hvac = sum(1 for slug in commons_rows if by_slug[slug]["discipline"] == "HVAC")
    checks.append(_check("hvac-facet", "The HVAC facet counts the published ventilation symbols (more than 0)",
                         len(hvac_published) > 0 and len(hvac_published) == expected_hvac,
                         {"hvac_facet": len(hvac_published), "expected": expected_hvac, "stored_facet_rows_with_hvac": stored_hvac}))

    # 8. Discipline parity.
    parity = discipline_parity()
    checks.append(_check("discipline-parity", "catalog_facets.py, catalog_taxonomy.py and catalogWorkbench.js list the same disciplines",
                         parity["agree"], parity))

    # 9. Fire dampers.
    scheme = session.execute(select(ClassificationScheme).where(ClassificationScheme.scheme_code == plan_module.DISCIPLINE_SCHEME_CODE)).scalar_one_or_none()
    fire_missing = []
    fire_slugs = [slug for slug, symbol in by_slug.items() if symbol["fire"]]
    for slug in fire_slugs:
        hit = session.execute(
            select(SymbolRevisionClassificationAssignment.id)
            .join(ClassificationNode, ClassificationNode.id == SymbolRevisionClassificationAssignment.classification_node_id)
            .join(GovernedSymbol, GovernedSymbol.current_revision_id == SymbolRevisionClassificationAssignment.symbol_revision_id)
            .where(GovernedSymbol.slug == slug,
                   SymbolRevisionClassificationAssignment.classification_scheme_id == (scheme.id if scheme else None),
                   SymbolRevisionClassificationAssignment.assignment_role == "secondary",
                   SymbolRevisionClassificationAssignment.status == "verified",
                   ClassificationNode.preferred_label == plan_module.FIRE_DISCIPLINE)
        ).first()
        if hit is None:
            fire_missing.append(slug)
    checks.append(_check(
        "fire-classification",
        "The fire, smoke and fire-and-smoke dampers carry a verified secondary Fire & Life Safety discipline",
        bool(fire_slugs) and not fire_missing,
        {"fire_dampers": fire_slugs, "missing": fire_missing, "catalog_fire_facet": fire_facet,
         "note": "The Catalog facet reads the symbol's one discipline column (HVAC); the secondary assignment is the governed record."},
    ))

    # 10. Drops and exclusions are absent.
    absent = [plan_module.slug_for(item["pageid"]) for item in plan["drops"] + plan["excluded"]]
    present = session.execute(select(GovernedSymbol.slug).where(GovernedSymbol.slug.in_(absent))).scalars().all()
    checks.append(_check("drops-absent", f"None of the {len(plan['drops'])} dropped or {len(plan['excluded'])} excluded files exists",
                         not present, {"present": list(present)}))

    # 11. Support's data sources.
    sources = list_published_data_sources(session)["sources"]
    source = next((s for s in sources if s["packageCode"] == plan_module.PACKAGE_CODE), None)
    checks.append(_check("support-section", "Support's data sources lists the Commons library with its attribution text",
                         source is not None and source["publishedSymbols"] == len(commons_rows),
                         {"listed": source is not None, "publishedSymbols": (source or {}).get("publishedSymbols")}))

    # 12. Stored objects, and the near-duplicate report.
    if fetcher is not None:
        bad = []
        for slug, row in commons_rows.items():
            asset = ((row.payload_json or {}).get("assets") or [{}])[0]
            try:
                stored = fetcher(object_key=asset["object_key"], env_file=storage_env_file)["payload"]
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{slug}: {type(exc).__name__}")
                continue
            if plan_module.sha256_hex(stored) != asset["sha256"]:
                bad.append(f"{slug}: digest differs")
        checks.append(_check("stored-objects", "Every published SVG is in object storage with its recorded digest", not bad, bad[:10] or None))
        if chromium:
            checks.append(_near_duplicate_check(session, rows, commons_rows, fetcher, storage_env_file, chromium))
        else:
            checks.append(_check("near-duplicates", "dHash near-duplicate report (information only)", "skipped",
                                 "no headless Chromium given (--chromium); the rehearsal report carries the result"))
    else:
        checks.append(_check("near-duplicates", "dHash near-duplicate report (information only)", "skipped", "--no-storage"))

    failed = [c for c in checks if c["status"] == "fail"]
    return {
        "mode": "verify",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failed,
        "failed": [c["id"] for c in failed],
        "checks": checks,
    }


def _near_duplicate_check(session, rows, commons_rows, fetcher, storage_env_file, chromium) -> dict[str, Any]:
    from .routes.published import published_symbol_row

    def item(row, candidate):
        payload = row.payload_json or {}
        asset = choose_published_preview_asset(payload) or ((payload.get("assets") or [{}])[0])
        key = (asset or {}).get("object_key")
        if not key:
            return None
        try:
            data = fetcher(object_key=key, env_file=storage_env_file)["payload"]
        except Exception:  # noqa: BLE001
            return None
        label = published_symbol_row(row)
        return {"id": label["catalogSymbolId"] or row.slug, "name": label["name"], "data": data, "candidate": candidate,
                "pack": row.pack_code}

    candidates = [x for x in (item(row, True) for row in commons_rows.values()) if x]
    seen = set()
    existing = []
    for row in rows:
        if row.pack_code == "commons-pid" or row.slug in seen:
            continue
        seen.add(row.slug)
        found = item(row, False)
        if found:
            existing.append(found)
    unreadable = len(seen) - len(existing)
    try:
        report = near_duplicates(candidates, existing, chromium=chromium)
    except (OSError, subprocess.SubprocessError) as exc:
        return _check("near-duplicates", "dHash near-duplicate report (information only)", "skipped", f"renderer failed: {type(exc).__name__}")
    report["existing_unreadable"] = unreadable
    return _check("near-duplicates", "dHash near-duplicate report (information only, never blocks)", "report", report)


# --------------------------------------------------------------------------
# The report.
# --------------------------------------------------------------------------


def _table(header: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        lines.append("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |")
    return "\n".join(lines)


def render_report(
    *,
    plan: dict[str, Any],
    fix_report: dict[str, Any] | None,
    dry_run: dict[str, Any] | None,
    apply_report: dict[str, Any] | None,
    gate_report: dict[str, Any] | None,
    publish_report: dict[str, Any] | None,
    verify_report: dict[str, Any] | None,
    backup_path: str | None,
    commit: str | None,
    rights_approver: str | None,
    banner: str | None = None,
) -> str:
    out: list[str] = []
    summary = plan["summary"]
    if banner:
        out += [f"> **{banner}**", ""]
    out += ["# Wikimedia Commons P&ID import report", "",
            f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Pack: **{plan['publication']['pack_title']}** "
            f"(`{plan['publication']['pack_code']}`); source package `{plan_module.PACKAGE_CODE}`.", "", "## Summary", ""]
    out.append(f"- Worksheet rows: **{summary['worksheet_rows']}** ({', '.join(f'{k} {v}' for k, v in sorted(summary['actions'].items()))}).")
    out.append(f"- Planned symbols: **{summary['symbols']}**; excluded by the fix step: **{summary['excluded']}**; dropped: **{summary['dropped']}**.")
    if publish_report:
        out.append(f"- Published in this run: **{publish_report.get('published_count')}**; held at the gate: **{len(publish_report.get('held_at_gate') or [])}**.")
    if verify_report:
        out.append(f"- Acceptance checks: **{'all passed' if verify_report['passed'] else 'failed: ' + ', '.join(verify_report['failed'])}**.")
    if backup_path:
        out.append(f"- Database backup: `{backup_path}`.")
    if commit:
        out.append(f"- Code: commit `{commit}`.")
    out += ["", "## Disciplines and categories", "",
            _table(["Discipline", "Symbols"], sorted(summary["disciplines"].items())), "",
            _table(["Category", "Symbols"], sorted(summary["categories"].items())), "",
            f"Fire & Life Safety (secondary discipline): {', '.join(summary['fire_dampers']) or 'none'}.", ""]

    out += ["## SVG fixes", "",
            "Each fix is one step of the revision's transformation chain (`asset_transformations`), from the Commons original's SHA-256 to the uploaded file's.", "",
            _table(["Fix", "Files"], sorted(summary["fixes"].items())), ""]
    if plan["excluded"]:
        out.append(_table(["Excluded page id", "Name", "Reason"], [[x["pageid"], x["name"], x["reason"]] for x in plan["excluded"]]))
    else:
        out.append("No file was excluded: every raster removal left a visible vector drawing.")
    out.append("")

    out += ["## Rights", "",
            f"- One rights record on `{plan_module.PACKAGE_CODE}`: `open` / `distribute`, method `licence_document`.",
            f"- Evidence: {plan['rights']['evidence']['file_count']} files, each with page id, Commons URL, author, licence, licence templates, Commons SHA-1 and retrieval date ({plan_module.RETRIEVED_ON}).",
            f"- Licence families: {plan['rights']['evidence']['licence_families']}.",
            f"- Approved by: {rights_approver or 'not yet approved'}.", ""]

    out += ["## Standards", "",
            _table(["Standard", "Links"], sorted(summary["standard_links"].items())), "",
            "All `vendor_implementation`. ISO 10628 links reuse the existing ISO 10628 / 2012 record. "
            "DIN EN 12792 and DIN 2429 are cited by the file's own Commons description and are not a product decision.", "",
            f"Symbols with no cited standard (the gate refuses them with `graphical_authority_unasserted`): **{len(summary['without_standard_link'])}**.", ""]

    concepts = (dry_run or {}).get("concepts") or {}
    out += ["## Concepts", ""]
    if apply_report:
        out.append(f"{len(apply_report.get('concepts_created', []))} concepts created, {len(apply_report.get('concepts_reused', []))} existing concepts reused.")
    elif concepts:
        out.append(f"{concepts.get('would_create')} concepts to create, {concepts.get('reuse_existing')} existing concepts to reuse.")
    near = (apply_report or {}).get("near_misses") or concepts.get("near_misses") or []
    out.append("")
    out.append("Near misses (reported, not merged): " + (json.dumps(near) if near else "none."))
    out.append("")

    if gate_report:
        out += ["## Gate", "", f"Outcomes: {gate_report.get('outcomes')}; refusal reasons: {gate_report.get('refusal_reasons')}.", ""]

    if verify_report:
        out += ["## Acceptance checks", ""]
        rows = []
        for check in verify_report["checks"]:
            detail = check["detail"]
            if check["id"] == "near-duplicates" and isinstance(detail, dict):
                detail = {k: (len(v) if isinstance(v, list) else v) for k, v in detail.items()}
            rows.append([check["title"], check["status"].upper(), json.dumps(detail, default=str)[:600] if detail is not None else ""])
        out.append(_table(["Check", "Result", "Detail"], rows))
        out.append("")
        dup = next((c for c in verify_report["checks"] if c["id"] == "near-duplicates" and isinstance(c["detail"], dict)), None)
        if dup:
            out += ["## Near duplicates (information only)", "",
                    _table(["Commons symbol", "Name", "Looks like", "Name", "dHash distance", "Pixel difference"],
                           [[m["symbol"], m["name"], m["matches"], m["matches_name"], m["hamming_distance"], m["pixel_difference"]]
                            for m in dup["detail"]["against_existing"]]), ""]
            within = dup["detail"]["within_import"]
            if within:
                out += ["Within the import:", "",
                        _table(["Symbol", "Name", "Looks like", "Name", "dHash distance", "Pixel difference"],
                               [[m["symbol"], m["name"], m["matches"], m["matches_name"], m["hamming_distance"], m["pixel_difference"]] for m in within]), ""]
    return "\n".join(out) + "\n"
