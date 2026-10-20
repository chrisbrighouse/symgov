"""Acceptance checks for the QElectroTech elements import.

`verify` reads the database (and, unless told not to, object storage) and
answers each check pass or fail with the numbers behind the answer. It never
writes. Several checks exist only because of QElectroTech's conditions of use
(`services/qet_ingestion`): each symbol's Details page credits the element's
own author in their wording, links qelectrotech.org, and nothing the import
wrote presents QElectroTech as a partner or endorser.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import re
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .catalog_facets import catalog_taxonomy_for_symbol as browser_taxonomy
from .commons_verify import _check, _latest_gate_reasons, _published_rows, discipline_parity
from .models import (
    GovernedSymbol,
    RightsRecord,
    SemanticConceptRevision,
    SemanticConcept,
    SourcePackage,
    Standard,
    StandardVersion,
    SymbolSemanticAssignment,
    SymbolStandardLink,
)
from .published_symbol_details import load_published_symbol_details
from .rights_provenance import trace_asset_lineage
from .services import qet_ingestion as plan_module

# Words that would present QElectroTech as more than the source it is.
ENDORSEMENT_WORDS = re.compile(r"\b(partner(ship)?|endorse[ds]?|endorsement|official|certified|approved by qelectrotech)\b", re.I)


def verify(session: Session, plan: dict[str, Any], package_data: dict[str, Any], *, fetcher=None, storage_env_file=None) -> dict[str, Any]:
    from .routes.published import list_published_data_sources, published_symbol_row

    checks: list[dict[str, Any]] = []
    by_slug = {symbol["slug"]: symbol for symbol in plan["symbols"]}
    rows = {row.slug: row for row in _published_rows(session) if row.pack_code == plan["publication"]["pack_code"]}

    # 1. Every planned symbol is published.
    unpublished = sorted(set(by_slug) - set(rows))
    reasons = _latest_gate_reasons(session, unpublished)
    checks.append(_check(
        "all-published", f"All {len(by_slug)} planned symbols are published in \"{plan['publication']['pack_title']}\"",
        not unpublished and len(rows) == len(by_slug),
        {"published": len(rows), "expected": len(by_slug),
         "unpublished_by_gate_reason": dict(Counter(",".join(reasons.get(s) or ["no gate evaluation"]) for s in unpublished)),
         "unpublished_slugs": unpublished[:40]},
    ))

    # 2. Stored as planned.
    mismatches = []
    for slug, row in rows.items():
        payload = row.payload_json or {}
        symbol = by_slug.get(slug)
        if symbol is None:
            mismatches.append(f"{slug}: published in the pack but not planned")
            continue
        if ((payload.get("assets") or [{}])[0]).get("sha256") != symbol["final_sha256"]:
            mismatches.append(f"{slug}: SVG digest differs")
        qet = payload.get("qet") or {}
        if qet.get("uuid") != symbol["qet_uuid"] or qet.get("en60617_ref") != symbol["qet"]["en60617_ref"]:
            mismatches.append(f"{slug}: qet block differs")
        if (payload.get("attribution") or {}).get("creator") != symbol["qet"]["author"]:
            mismatches.append(f"{slug}: author differs")
        if row.canonical_name != symbol["canonical_name"] or row.discipline != symbol["discipline"] or row.category != symbol["category"]:
            mismatches.append(f"{slug}: name, discipline or category differs from the worksheet")
    checks.append(_check("stored-as-planned", "Name, discipline, category, SVG digest, author and QET provenance match the plan",
                         not mismatches, mismatches[:10] or None))

    # 3. Rights.
    package = session.execute(select(SourcePackage).where(SourcePackage.package_code == plan_module.PACKAGE_CODE)).scalar_one_or_none()
    record = None
    if package is not None:
        record = session.execute(
            select(RightsRecord).where(RightsRecord.source_package_id == package.id, RightsRecord.decision_status == "approved")
        ).scalar_one_or_none()
    evidence = (record.evidence_json if record is not None else {}) or {}
    checks.append(_check(
        "rights-record",
        "One approved rights record (open / distribute), decided by a named person, carrying QElectroTech's "
        "attribution template and the qelectrotech.org link",
        record is not None and (record.rights_status, record.disposition) == ("open", "distribute")
        and record.decided_by_user_id is not None
        and evidence.get("attribution_template") == plan_module.ATTRIBUTION_TEMPLATE
        and evidence.get("source_url") == plan_module.QET_URL and evidence.get("no_endorsement") is True,
        {"status": getattr(record, "rights_status", None), "disposition": getattr(record, "disposition", None),
         "decided": bool(getattr(record, "decided_by_user_id", None)),
         "template_matches": evidence.get("attribution_template") == plan_module.ATTRIBUTION_TEMPLATE,
         "source_url": evidence.get("source_url")},
    ))

    # 4. Standard links: one verified vendor_implementation of EN 60617 per symbol, at its clause.
    link_rows = session.execute(
        select(GovernedSymbol.slug, SymbolStandardLink.relationship_type, SymbolStandardLink.assertion_status,
               SymbolStandardLink.clause_reference, Standard.standard_code, StandardVersion.version_label)
        .join(SymbolStandardLink, SymbolStandardLink.symbol_revision_id == GovernedSymbol.current_revision_id)
        .join(StandardVersion, StandardVersion.id == SymbolStandardLink.standard_version_id)
        .join(Standard, Standard.id == StandardVersion.standard_id)
        .where(GovernedSymbol.slug.in_(list(by_slug)))
    ).all()
    links: dict[str, list[Any]] = defaultdict(list)
    for item in link_rows:
        links[item.slug].append(item)
    link_problems = []
    for slug, symbol in by_slug.items():
        found = [(l.standard_code, l.version_label, l.clause_reference, l.relationship_type)
                 for l in links.get(slug, []) if l.assertion_status == "verified"]
        expected = [(plan_module.EN_60617["standard_code"], plan_module.EN_60617["version_label"],
                     symbol["qet"]["en60617_ref"], "vendor_implementation")]
        if found != expected:
            link_problems.append(f"{slug}: expected {expected}, found {found}")
    checks.append(_check("standard-links", "Each symbol has exactly one verified vendor_implementation link to EN 60617 at its clause",
                         not link_problems, {"links": len(link_rows), "problems": link_problems[:10]}))

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
    wrong = [s for s, symbol in by_slug.items() if concept_of.get(s, (None, None))[1] != plan_module.concept_name_for(symbol["concept_key"])]
    ids_by_key: dict[str, set] = defaultdict(set)
    for slug, symbol in by_slug.items():
        if slug in concept_of:
            ids_by_key[symbol["concept_key"]].add(concept_of[slug][0])
    split = sorted(key for key, ids in ids_by_key.items() if len(ids) > 1)
    checks.append(_check("concepts", "Every symbol carries the concept the worksheet names, and each key is one concept",
                         not wrong and not split, {"wrong": wrong[:10], "keys_split_over_concepts": split[:10],
                                                   "distinct_concepts": len({v[0] for v in concept_of.values()})}))

    # 6. Transformation chain: the .elmt, converted once, to the SVG published.
    chain_problems = []
    for slug, row in rows.items():
        symbol = by_slug.get(slug)
        if symbol is None:
            continue
        lineage = trace_asset_lineage(session, row.symbol_revision_id)
        if (len(lineage) != 1 or lineage[0].get("source_asset_sha256") != symbol["original_sha256"]
                or lineage[0].get("derived_asset_sha256") != symbol["final_sha256"]):
            chain_problems.append(slug)
    checks.append(_check("transformation-chain", "Each symbol's chain is one step, from its .elmt digest to the published SVG digest",
                         not chain_problems, chain_problems[:10] or None))

    # 7. QElectroTech's conditions, as the Details view serves them.
    attribution_problems = []
    for slug, row in rows.items():
        symbol = by_slug.get(slug)
        if symbol is None:
            continue
        rights = load_published_symbol_details(session, row)["rights"] or {}
        author = symbol["qet"]["author"]
        if rights.get("attributionText") != plan_module.attribution_text(author) or rights.get("creator") != author:
            attribution_problems.append(f"{slug}: attribution is {rights.get('attributionText')!r}")
        elif rights.get("sourceUrl") != plan_module.QET_URL:
            attribution_problems.append(f"{slug}: source link is {rights.get('sourceUrl')!r}")
    checks.append(_check(
        "details-attribution",
        "Every symbol's Details credits its own author in QElectroTech's wording and links qelectrotech.org",
        bool(rows) and not attribution_problems,
        {"checked": len(rows), "credits": dict(Counter(by_slug[s]["qet"]["author"] for s in rows if s in by_slug)),
         "problems": attribution_problems[:10]},
    ))
    endorsement = []
    for slug, row in rows.items():
        payload = row.payload_json or {}
        for field in ("name", "summary", "description"):
            if ENDORSEMENT_WORDS.search(str(payload.get(field) or "")):
                endorsement.append(f"{slug}: {field}")
    for value in (plan["publication"]["pack_title"], (package.title if package is not None else ""),
                  str(evidence.get("attribution_template") or "")):
        if ENDORSEMENT_WORDS.search(value):
            endorsement.append(f"pack, package or attribution wording: {value!r}")
    checks.append(_check("no-endorsement", "Nothing the import wrote presents QElectroTech as a partner or endorser",
                         not endorsement, endorsement[:10] or None))

    # 8. The Electrical facet.
    electrical = sum(1 for row in rows.values() if "Electrical" in browser_taxonomy(published_symbol_row(row))["disciplines"])
    stored = session.execute(
        text("SELECT count(*) FROM catalog_symbol_facets f JOIN governed_symbols gs ON gs.id = f.governed_symbol_id "
             "WHERE gs.slug = ANY(:slugs) AND f.disciplines ? 'Electrical' AND f.rules_version > 0"),
        {"slugs": list(rows)},
    ).scalar_one()
    checks.append(_check("electrical-facet", "The Electrical facet counts every published QElectroTech symbol",
                         bool(rows) and electrical == len(rows),
                         {"electrical_facet": electrical, "published": len(rows), "stored_facet_rows": stored}))

    parity = discipline_parity()
    checks.append(_check("discipline-parity", "catalog_facets.py, catalog_taxonomy.py and catalogWorkbench.js list the same disciplines",
                         parity["agree"], parity))

    # 9. Drops absent.
    absent = [plan_module.slug_for(item["qet_uuid"]) for item in plan["drops"] if item.get("qet_uuid")]
    present = session.execute(select(GovernedSymbol.slug).where(GovernedSymbol.slug.in_(absent))).scalars().all()
    checks.append(_check("drops-absent", f"None of the {len(plan['drops'])} dropped elements exists", not present, {"present": list(present)}))

    # 10. Support's data sources.
    source = next((s for s in list_published_data_sources(session)["sources"] if s["packageCode"] == plan_module.PACKAGE_CODE), None)
    checks.append(_check("support-section", "Support's data sources lists the QElectroTech library with its attribution text",
                         source is not None and source["publishedSymbols"] == len(rows),
                         {"listed": source is not None, "publishedSymbols": (source or {}).get("publishedSymbols")}))

    # 11. Stored objects.
    if fetcher is not None:
        bad = []
        for slug, row in rows.items():
            asset = ((row.payload_json or {}).get("assets") or [{}])[0]
            try:
                stored_bytes = fetcher(object_key=asset["object_key"], env_file=storage_env_file)["payload"]
            except Exception as exc:  # noqa: BLE001
                bad.append(f"{slug}: {type(exc).__name__}")
                continue
            if plan_module.sha256_hex(stored_bytes) != asset["sha256"]:
                bad.append(f"{slug}: digest differs")
        checks.append(_check("stored-objects", "Every published SVG is in object storage with its recorded digest", not bad, bad[:10] or None))
    else:
        checks.append(_check("stored-objects", "Every published SVG is in object storage with its recorded digest", "skipped", "--no-storage"))

    failed = [c for c in checks if c["status"] == "fail"]
    return {
        "mode": "verify",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "passed": not failed,
        "failed": [c["id"] for c in failed],
        "checks": checks,
    }
