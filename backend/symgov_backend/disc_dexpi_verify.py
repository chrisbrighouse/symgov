"""Acceptance checks for the DISC DEXPI import, and the report that records them.

`verify` reads the database (and, for the attribution check, object storage)
and answers each of the brief's acceptance checks pass or fail, with the
numbers behind the answer. It never writes. `render_report` turns the dry-run,
apply, publish and verify reports into the Markdown the importer's owner can
share.

Most checks compare the database with the package it was imported from, so
they hold for any package. The few the brief states as literal values about
named symbols (ND0004, ND0136 against S-154) are in `brief_spot_checks` and run
only when asked for, because they describe the real library.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .catalog_facets import catalog_taxonomy_for_symbol as browser_taxonomy
from .catalog_taxonomy import catalog_taxonomy_for_symbol as api_taxonomy
from .catalog_search import row_taxonomy_input
from .models import (
    ClassificationNode,
    ClassificationScheme,
    ConceptExternalReference,
    ExternalSemanticScheme,
    ExternalSemanticSchemeVersion,
    GovernedSymbol,
    RightsRecord,
    SemanticConcept,
    SemanticConceptRevision,
    SymbolRevisionClassificationAssignment,
    SymbolSemanticAssignment,
)
from .published_catalog import PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL
from .routes.published import published_symbol_row
from .services import disc_dexpi_ingestion as plan_module

PILOT_PACK_CODE = "dexpi-ttc-1-2-1-3"

# The brief's stated values for the real library. Used only with --brief-spot-checks.
BRIEF_ND0004 = {
    "points": [(9.0, 0.0625), (-9.0, 0.0625), (0.0, -8.9375)],
    "label": "A",
    "conditions": ["ValvePosition = 'NC'", "ValvePosition = '1NCAngle'"],
    "node_code": "DOUBLE_BLOCK_AND_BLEED_VALVE",
    "rdl": "http://data.posccaesar.org/rdl/RDS552689",
}


def _check(check_id: str, title: str, ok: bool, detail: Any = None) -> dict[str, Any]:
    return {"id": check_id, "title": title, "status": "pass" if ok else "fail", "detail": detail}


def _published_rows(session: Session) -> list[Any]:
    return list(session.execute(text(PUBLISHED_SYMBOLS_WITH_GOVERNANCE_SQL)).all())


def verify(
    session: Session,
    plan: dict[str, Any],
    package_data: dict[str, Any],
    *,
    fetcher=None,
    storage_env_file=None,
    expected_pilot_count: int | None = None,
    brief_spot_checks: bool = False,
) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    symbols = plan["symbols"]
    slugs = [symbol["slug"] for symbol in symbols]
    rows = _published_rows(session)
    by_pack = Counter(row.pack_code for row in rows)
    rows_by_slug = {row.slug: row for row in rows}

    # 1. The Catalog pack filter shows every imported symbol, and the pilot is intact.
    disc_live = by_pack.get(plan["publication"]["pack_code"], 0)
    checks.append(
        _check(
            "pack-count",
            f"Pack filter \"{plan['publication']['pack_title']}\" shows {len(symbols)} symbols",
            disc_live == len(symbols),
            {"shown": disc_live, "expected": len(symbols)},
        )
    )
    if expected_pilot_count is not None:
        checks.append(
            _check(
                "pilot-count",
                f"The TrainingTestCases pilot still shows {expected_pilot_count}",
                by_pack.get(PILOT_PACK_CODE, 0) == expected_pilot_count,
                {"shown": by_pack.get(PILOT_PACK_CODE, 0), "expected": expected_pilot_count},
            )
        )

    # 2. Every symbol is stored as the package says: geometry, options, assets.
    mismatches: list[str] = []
    for symbol in symbols:
        row = rows_by_slug.get(symbol["slug"])
        if row is None:
            mismatches.append(f"{symbol['disc_id']}: not published")
            continue
        payload = row.payload_json or {}
        record = symbol["record"]["payload"]
        if payload.get("geometry") != record.get("geometry"):
            mismatches.append(f"{symbol['disc_id']}: geometry differs")
        if [o.get("condition") for o in payload.get("options") or []] != [o.get("condition") for o in record.get("options") or []]:
            mismatches.append(f"{symbol['disc_id']}: option conditions differ")
        if payload.get("disc") != record.get("disc"):
            mismatches.append(f"{symbol['disc_id']}: register extras differ")
        primary = (payload.get("assets") or [{}])[0]
        if primary.get("sha256") != symbol["primary_sha256"]:
            mismatches.append(f"{symbol['disc_id']}: primary digest differs")
    checks.append(
        _check("stored-as-packaged", "Geometry, options, register extras and digests match the package for every symbol",
               not mismatches, mismatches[:10] or None)
    )

    # 3. The counts the brief asks for.
    option_assets = session.execute(
        text(
            "SELECT count(*) FROM attachments a JOIN symbol_revisions sr ON sr.id = a.parent_id "
            "JOIN governed_symbols gs ON gs.id = sr.symbol_id "
            "WHERE a.asset_role = 'option' AND a.parent_type = 'symbol_revision' AND gs.slug = ANY(:slugs) "
            "AND sr.lifecycle_state = 'published'"
        ),
        {"slugs": slugs},
    ).scalar_one()
    with_points = sum(1 for row in rows if row.slug in set(slugs) and ((row.payload_json or {}).get("geometry") or {}).get("connection_points"))
    with_slots = sum(1 for row in rows if row.slug in set(slugs) and ((row.payload_json or {}).get("geometry") or {}).get("label_slots"))
    summary = plan["summary"]
    checks.append(
        _check(
            "counts",
            f"{summary['with_connection_points']} symbols have connection points, {summary['with_label_slots']} have label slots, {summary['option_assets']} option assets are stored",
            (with_points, with_slots, option_assets) == (summary["with_connection_points"], summary["with_label_slots"], summary["option_assets"]),
            {"connection_points": with_points, "label_slots": with_slots, "option_assets": option_assets},
        )
    )

    # 4. DEXPI-CLASS assignments, for this library and for the pilot.
    scheme = session.execute(select(ClassificationScheme).where(ClassificationScheme.scheme_code == plan_module.SCHEME_CODE)).scalar_one_or_none()
    wrong_nodes: list[str] = []
    classified = 0
    if scheme is not None:
        nodes = {n.node_code: n for n in session.execute(select(ClassificationNode).where(ClassificationNode.scheme_id == scheme.id)).scalars()}
        for symbol in symbols:
            row = rows_by_slug.get(symbol["slug"])
            if row is None:
                continue
            expected_code = plan["nodes_by_ref"][symbol["dexpi_node_reference"]]["node_code"]
            assignment = session.execute(
                select(SymbolRevisionClassificationAssignment).where(
                    SymbolRevisionClassificationAssignment.symbol_revision_id == uuid_of(row.symbol_revision_id),
                    SymbolRevisionClassificationAssignment.classification_scheme_id == scheme.id,
                    SymbolRevisionClassificationAssignment.status == "verified",
                    SymbolRevisionClassificationAssignment.assignment_role == "primary",
                )
            ).scalar_one_or_none()
            if assignment is None or assignment.method != "source_mapping" or nodes.get(expected_code) is None or assignment.classification_node_id != nodes[expected_code].id:
                wrong_nodes.append(symbol["disc_id"])
            else:
                classified += 1
    checks.append(
        _check("dexpi-class-disc", "Every imported symbol has its DEXPI-CLASS node, method source_mapping, verified",
               scheme is not None and not wrong_nodes and classified == len(symbols),
               {"classified": classified, "wrong_or_missing": wrong_nodes[:10]})
    )
    pilot_total = pilot_classified = 0
    if scheme is not None:
        for row in rows:
            if row.pack_code != PILOT_PACK_CODE:
                continue
            target = plan_module.ttc_class_target(row.payload_json)
            if not target:
                continue
            pilot_total += 1
            hit = session.execute(
                select(SymbolRevisionClassificationAssignment.id).where(
                    SymbolRevisionClassificationAssignment.symbol_revision_id == uuid_of(row.symbol_revision_id),
                    SymbolRevisionClassificationAssignment.classification_scheme_id == scheme.id,
                    SymbolRevisionClassificationAssignment.method == "source_mapping",
                    SymbolRevisionClassificationAssignment.status == "verified",
                )
            ).first()
            pilot_classified += 1 if hit else 0
    checks.append(
        _check("dexpi-class-pilot", "Semantic Review lists DEXPI-CLASS assignments (source_mapping) for the pilot symbols too",
               pilot_total > 0 and pilot_classified == pilot_total, {"classified": pilot_classified, "of": pilot_total})
    )

    # 5. Concepts: each symbol's primary concept is the one its register names.
    concept_problems: list[str] = []
    for symbol in symbols:
        row = rows_by_slug.get(symbol["slug"])
        if row is None:
            continue
        name = session.execute(
            select(SemanticConceptRevision.preferred_name)
            .join(SemanticConcept, SemanticConcept.id == SemanticConceptRevision.concept_id)
            .join(SymbolSemanticAssignment, SymbolSemanticAssignment.semantic_concept_id == SemanticConcept.id)
            .where(
                SymbolSemanticAssignment.symbol_revision_id == uuid_of(row.symbol_revision_id),
                SymbolSemanticAssignment.status == "verified",
                SymbolSemanticAssignment.assignment_role == "primary",
            )
        ).first()
        if name is None or name[0] != symbol["concept_key"]:
            concept_problems.append(symbol["disc_id"])
    checks.append(
        _check("concepts", "Every imported symbol resolves to the concept its register names",
               not concept_problems, concept_problems[:10] or None)
    )

    # 6. External mappings exist, verified, as imported.
    missing_mappings: list[str] = []
    for mapping in plan["external"]["mappings"]:
        found = session.execute(
            select(ConceptExternalReference.id)
            .join(SemanticConceptRevision, SemanticConceptRevision.concept_id == ConceptExternalReference.semantic_concept_id)
            .join(ExternalSemanticSchemeVersion, ExternalSemanticSchemeVersion.id == ConceptExternalReference.scheme_version_id)
            .join(ExternalSemanticScheme, ExternalSemanticScheme.id == ExternalSemanticSchemeVersion.scheme_id)
            .where(
                SemanticConceptRevision.preferred_name == mapping["concept_key"],
                ExternalSemanticScheme.scheme_code == mapping["scheme_code"],
                ConceptExternalReference.external_identifier == mapping["identifier"],
                ConceptExternalReference.mapping_type == mapping["relation"],
                ConceptExternalReference.mapping_status == "verified",
                ConceptExternalReference.mapping_method == "imported",
            )
        ).first()
        if found is None:
            missing_mappings.append(f"{mapping['concept_key']} -> {mapping['identifier']}")
    checks.append(
        _check("external-mappings", f"{len(plan['external']['mappings'])} concept-to-RDL mappings are verified, method imported",
               not missing_mappings, missing_mappings[:10] or None)
    )

    # 7. Rights, attribution and the Support source.
    rights = session.execute(
        select(RightsRecord).where(RightsRecord.evidence_json["attribution_text"].astext.is_not(None), RightsRecord.decision_status == "approved")
    ).scalars().all()
    expected_text = plan["rights"]["evidence"]["attribution_text"]
    record = next((r for r in rights if r.evidence_json.get("attribution_text") == expected_text), None)
    checks.append(
        _check(
            "rights-record",
            "One approved rights record (licensed, distribute) holds the attribution text",
            record is not None and (record.rights_status, record.disposition) == ("licensed", "distribute") and record.decided_by_user_id is not None,
            {"status": getattr(record, "rights_status", None), "disposition": getattr(record, "disposition", None),
             "decided": bool(getattr(record, "decided_by_user_id", None))},
        )
    )
    if fetcher is not None:
        lacking: list[str] = []
        sampled = 0
        for symbol in symbols:
            row = rows_by_slug.get(symbol["slug"])
            if row is None:
                continue
            asset = ((row.payload_json or {}).get("assets") or [{}])[0]
            stored = fetcher(object_key=asset["object_key"], env_file=storage_env_file)["payload"]
            sampled += 1
            if hashlib.sha256(stored).hexdigest() != asset["sha256"] or expected_text.encode("utf-8") not in stored:
                lacking.append(symbol["disc_id"])
        checks.append(
            _check("svg-metadata", "Each stored SVG carries the attribution text in its metadata and matches its digest",
                   not lacking and sampled == len(symbols), {"checked": sampled, "lacking": lacking[:10]})
        )
    from .routes.published import list_published_data_sources

    sources = list_published_data_sources(session)["sources"]
    source = next((s for s in sources if s["packageCode"] == plan["source_package"]["package_code"]), None)
    checks.append(
        _check("support-section", "Support's data sources lists the library, with its attribution read from the record",
               source is not None and source["attributionText"] == expected_text and source["publishedSymbols"] == len(symbols),
               {"listed": source is not None, "publishedSymbols": (source or {}).get("publishedSymbols")})
    )

    # 8. Facets: nothing that is a heat exchanger is under Fire & Life Safety.
    offenders: list[str] = []
    fire_elsewhere: list[str] = []
    for row in rows:
        item = published_symbol_row(row)
        facets = [browser_taxonomy(item), api_taxonomy(row_taxonomy_input(row))]
        fire = any("Fire & Life Safety" in f["disciplines"] or "Fire Alarm Devices" in f["categories"] for f in facets)
        label = (item["name"] or "").lower() + " " + " ".join(str(k) for k in (item.get("keywords") or [])).lower()
        if fire and ("heat exch" in label or "heatexchanger" in label.replace(" ", "")):
            offenders.append(item["catalogSymbolId"])
        elif fire:
            fire_elsewhere.append(item["catalogSymbolId"])
    checks.append(
        _check("no-heat-exchanger-in-fire", "No heat exchanger (pilot or DISC) appears under Fire & Life Safety",
               not offenders, {"offenders": offenders[:20], "other_fire_symbols": len(fire_elsewhere)})
    )

    # 9. Held back symbols are absent.
    held = [r["disc_id"] for r in package_data["held_back"]]
    present = session.execute(
        select(GovernedSymbol.slug).where(GovernedSymbol.slug.in_([f"disc-dexpi-{h.lower()}" for h in held]))
    ).scalars().all()
    checks.append(
        _check("held-back-absent", f"None of the {len(held)} held-back symbols exist in the catalog",
               not present, {"held_back": len(held), "present": list(present)[:10]})
    )

    if brief_spot_checks:
        checks.extend(_brief_spot_checks(session, plan, rows, rows_by_slug))

    failed = [c for c in checks if c["status"] != "pass"]
    return {
        "mode": "verify",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failed,
        "failed": [c["id"] for c in failed],
        "checks": checks,
    }


def facet_changes(session: Session) -> list[dict[str, Any]]:
    """Every published symbol whose facets differ between the old keyword-only rules and the governed ones.

    "Old" is the same row without its governed flags, which is exactly what the
    rules saw before. Both Catalog views (the browser's and the /catalog API's)
    are compared, and only a difference is listed.
    """
    changed: list[dict[str, Any]] = []
    for row in _published_rows(session):
        item = published_symbol_row(row)
        old_item = {key: value for key, value in item.items() if key != "governedTaxonomy"}
        taxonomy_input = row_taxonomy_input(row)
        old_input = {key: value for key, value in taxonomy_input.items() if key != "governedTaxonomy"}
        record = {
            "id": item["catalogSymbolId"], "slug": item["slug"], "name": item["name"],
            "category": item["category"], "discipline": item["discipline"], "pack": item["packCode"],
            "governed": item.get("governedTaxonomy"),
        }
        pairs = (
            ("browser", browser_taxonomy(old_item), browser_taxonomy(item)),
            ("api", api_taxonomy(old_input), api_taxonomy(taxonomy_input)),
        )
        for view, old, new in pairs:
            for facet in ("disciplines", "categories"):
                if old[facet] != new[facet]:
                    record.setdefault("changes", []).append({"view": view, "facet": facet, "old": old[facet], "new": new[facet]})
        if record.get("changes"):
            changed.append(record)
    return changed


def uuid_of(value: Any):
    import uuid

    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _brief_spot_checks(session: Session, plan: dict[str, Any], rows: list[Any], rows_by_slug: dict[str, Any]) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    row = rows_by_slug.get("disc-dexpi-nd0004")
    if row is None:
        return [_check("brief-nd0004", "ND0004 is published", False, "not found")]
    payload = row.payload_json or {}
    geometry = payload.get("geometry") or {}
    points = [(p["x_mm"], p["y_mm"]) for p in geometry.get("connection_points") or [] if "piping" in (p.get("kinds") or [])]
    conditions = [o.get("condition") for o in payload.get("options") or []]
    slots = [s.get("label_index") for s in geometry.get("label_slots") or []]
    node = session.execute(
        select(ClassificationNode.node_code)
        .join(SymbolRevisionClassificationAssignment, SymbolRevisionClassificationAssignment.classification_node_id == ClassificationNode.id)
        .join(ClassificationScheme, ClassificationScheme.id == ClassificationNode.scheme_id)
        .where(
            SymbolRevisionClassificationAssignment.symbol_revision_id == uuid_of(row.symbol_revision_id),
            ClassificationScheme.scheme_code == plan_module.SCHEME_CODE,
        )
    ).scalar_one_or_none()
    mapping = session.execute(
        select(ConceptExternalReference.id).where(
            ConceptExternalReference.external_identifier == BRIEF_ND0004["rdl"],
            ConceptExternalReference.mapping_status == "verified",
        )
    ).first()
    checks.append(
        _check(
            "brief-nd0004",
            "ND0004: 3 piping points, label slot A, 2 options, node CustomOperatedValve/DoubleBlockAndBleedValve, RDS552689",
            sorted(points) == sorted(BRIEF_ND0004["points"]) and slots == [BRIEF_ND0004["label"]]
            and conditions == BRIEF_ND0004["conditions"] and node == BRIEF_ND0004["node_code"] and mapping is not None,
            {"points": points, "slots": slots, "conditions": conditions, "node": node, "mapping": mapping is not None},
        )
    )
    pump = rows_by_slug.get("disc-dexpi-nd0136")
    pilot_pump = next((r for r in rows if r.pack_code == PILOT_PACK_CODE and (r.payload_json or {}).get("name") == "CentrifugalPump (HEX 1)"), None)

    def concept_of(row):
        return session.execute(
            select(SymbolSemanticAssignment.semantic_concept_id).where(
                SymbolSemanticAssignment.symbol_revision_id == uuid_of(row.symbol_revision_id),
                SymbolSemanticAssignment.status == "verified",
                SymbolSemanticAssignment.assignment_role == "primary",
            )
        ).scalar_one_or_none()

    same = pump is not None and pilot_pump is not None and concept_of(pump) is not None and concept_of(pump) == concept_of(pilot_pump)
    checks.append(
        _check("brief-pump-concept", "ND0136 (Pump Centrifugal) and S-154 (CentrifugalPump HEX 1) resolve to the same CentrifugalPump concept",
               same, {"nd0136": pump is not None, "pilot_found": pilot_pump is not None,
                      "pilot_id": getattr(pilot_pump, "catalog_symbol_id", None)})
    )
    return checks


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
    package_data: dict[str, Any],
    dry_run: dict[str, Any] | None,
    apply_report: dict[str, Any] | None,
    publish_report: dict[str, Any] | None,
    verify_report: dict[str, Any] | None,
    facet_changes: list[dict[str, Any]] | None,
    backup_path: str | None,
    commit: str | None,
    rights_approver: str | None,
) -> str:
    out: list[str] = []
    config = package_data["config"]
    summary = plan["summary"]
    out.append("# DISC DEXPI import report")
    out.append("")
    out.append(f"Generated {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Pack: **{config['pack_name']}**.")
    out.append("")
    out.append("## Summary")
    out.append("")
    published = (publish_report or {}).get("published_count")
    out.append(f"- Symbols in the package: **{summary['symbols']}** ({summary['option_assets']} option assets; "
               f"{summary['with_connection_points']} with connection points; {summary['with_label_slots']} with label slots).")
    out.append(f"- Held back (not imported): **{package_data['report']['held_back']}**.")
    if published is not None:
        out.append(f"- Published to the public Catalog in this run: **{published}**.")
    if verify_report:
        out.append(f"- Acceptance checks: **{'all passed' if verify_report['passed'] else 'some failed: ' + ', '.join(verify_report['failed'])}**.")
    if backup_path:
        out.append(f"- Database backup taken before the migration and import: `{backup_path}`.")
    if commit:
        out.append(f"- Code: commit `{commit}`.")
    out.append(f"- Source: {config['source_url']} at commit `{package_data['manifest'][0]['rights']['source_commit']}`; licensor {config['licensor']}; creator {config['creator']}.")
    out.append("")

    out.append("## Rights and attribution")
    out.append("")
    out.append(f"- One rights record on source package `{plan['source_package']['package_code']}`: status `licensed`, disposition `distribute`.")
    out.append(f"- Evidence recorded: \"{plan['rights']['evidence']['permission']}\".")
    out.append(f"- Approved by: {rights_approver or 'not yet approved'}.")
    out.append(f"- The attribution text is stored once, on that record, and is flagged as a **placeholder** "
               f"({'yes' if plan['rights']['evidence']['attribution_is_placeholder'] else 'no'}). "
               "It is filled into `payload.dexpi.attribution` when a response is built; it is not stored in any revision.")
    out.append("- The SVG files carry the same wording in their `<metadata>` because they are the delivered files. "
               "To change the wording: edit `attribution_text` in `config.json`, set `attribution_is_placeholder` to false, "
               "re-run the converter, update the rights record's evidence, and re-run `apply`; only the SVG digests change, "
               "and each changed symbol gets a new revision.")
    out.append("")

    out.append("## Things to tell Tonia Pedersen")
    out.append("")
    for key, pairs in plan_module.DISPLAY_NAME_CORRECTIONS.items():
        for wrong, right in pairs:
            out.append(f"- `{key.rsplit('-', 1)[-1]}`: the register's symbol name reads \"{wrong}\"; Symgov displays \"{right}\". "
                       "The register text, `payload.disc`, and the SVG's own `<title>` are left as supplied.")
    out.append("")

    out.append("## Held-back symbols")
    out.append("")
    reasons = Counter(row["reason"] for row in package_data["held_back"])
    out.append(_table(["Reason", "Symbols"], [[reason, count] for reason, count in sorted(reasons.items())]))
    out.append("")
    out.append("<details><summary>All held-back ids</summary>\n")
    out.append(_table(["ND id", "Description", "DEXPI class", "Reason"],
                      [[r["disc_id"], r["description"], r["dexpi_class"], r["reason"]] for r in package_data["held_back"]]))
    out.append("\n</details>")
    out.append("")

    out.append("## Register warnings recorded for review")
    out.append("")
    out.append("Stored on each symbol's source package entry (`source_package_entries.import_warnings_json`), not in the public payload.")
    out.append("")
    warnings = [[s["disc_id"], w] for s in plan["symbols"] for w in s["warnings"]]
    out.append(_table(["Symbol", "Warning"], warnings))
    out.append("")

    out.append("## DEXPI-CLASS scheme")
    out.append("")
    out.append(f"Nodes: {plan['scheme']['counts']}. Codes are upper-case snake; the DEXPI name is kept as the node label and as an alias. "
               "Package nodes take a `PACKAGE_` prefix because the package `Equipment` and the class `Equipment` share a spelling.")
    if apply_report:
        out.append(f"Pilot classes the DEXPI list lacked, added under the `Other` package with source \"DEXPI TrainingTestCases\": "
                   f"{len(apply_report.get('pilot_classes_added', []))}.")
    out.append("")
    out.append("<details><summary>DEXPI name to node code</summary>\n")
    out.append(_table(["Kind", "DEXPI name", "Node code"], [[*key.split(":", 1), code] for key, code in sorted(plan["scheme"]["code_map"].items())]))
    out.append("\n</details>")
    out.append("")

    out.append("## Concepts")
    out.append("")
    if apply_report:
        reused = {c["concept_key"]: c["concept_code"] for c in apply_report.get("concepts_reused", [])}
        out.append(f"{len(apply_report.get('concepts_created', []))} concepts created, {len(reused)} reused.")
        out.append("")
        out.append("The 16 classes shared with the pilot, and the existing concept each matched:")
        out.append("")
        out.append(_table(["Class", "Existing concept"], [[key, reused.get(key, "not matched")] for key in plan_module.EXPECTED_OVERLAP_CLASSES]))
        extras = sorted(set(reused) - set(plan_module.EXPECTED_OVERLAP_CLASSES))
        if extras:
            out.append("")
            out.append("Also matched exactly, though the brief did not list them (reused, not duplicated): "
                       + ", ".join(f"{key} ({reused[key]})" for key in extras) + ".")
    out.append("")

    out.append("## External mappings")
    out.append("")
    counts = Counter((m["system"], m["relation"]) for m in plan["external"]["mappings"])
    out.append(_table(["System", "Relation", "Distinct mappings"], [[s, r, c] for (s, r), c in sorted(counts.items())]))
    out.append("")
    out.append("All are concept-level, method `imported`, status `verified`, with the source repository and commit as evidence.")
    out.append("")

    if facet_changes is not None:
        out.append("## Catalog facet fix")
        out.append("")
        out.append("Governed discipline and category assignments (proposed or verified) now decide a symbol's facets; "
                   "the keyword rules run only for a symbol with none. The pilot's symbols had none, so they were given "
                   "discipline and category assignments from their own columns (method `legacy_backfill`, proposed).")
        out.append("")
        out.append(f"{len(facet_changes)} published symbols change facets:")
        out.append("")
        rows = []
        for item in facet_changes:
            for change in item.get("changes", []):
                if change["view"] == "browser":
                    rows.append([item["id"], item["name"], change["facet"], ", ".join(change["old"]), ", ".join(change["new"])])
        out.append(_table(["Symbol", "Name", "Facet", "Before", "After"], rows))
        out.append("")

    if verify_report:
        out.append("## Acceptance checks")
        out.append("")
        out.append(_table(["Check", "Result", "Detail"], [[c["title"], c["status"].upper(), json.dumps(c["detail"], default=str) if c["detail"] is not None else ""] for c in verify_report["checks"]]))
        out.append("")

    out.append("## Not done, or not known")
    out.append("")
    out.append("- The attribution wording is a placeholder until the licensor supplies the final text.")
    out.append("- Symbols that DISC has not yet mapped are not imported; re-run the converter when they are, and `apply` adds them.")
    out.append("")
    return "\n".join(out) + "\n"
