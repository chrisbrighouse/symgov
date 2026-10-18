"""Import the curated Wikimedia Commons P&ID symbols: `fix-svgs`, `apply`, `upload`, `gate`, `publish`, `assert-standard`.

`services/commons_ingestion` decides what is recorded; this writes it, the way
`disc_dexpi_ingest` writes the DISC DEXPI library. It reuses that importer's
order of work and its helpers, and like it is a trusted operator interface that
owns its transaction.

* `fix-svgs` writes the fixed SVGs to `svg-fixed/` inside the package, and
  nowhere else. Everything else reads the package and never changes it.
* `apply --dry-run` makes the same reads and decisions as `apply` and writes
  nothing; it exits 2 if the real run would refuse.
* Keyed on the Commons page id (`commons-<pageid>`). Re-running `apply` changes
  nothing; a changed SVG, name or concept makes a new revision.

**What it cannot do.** The rights record is proposed, not approved: approving
it is a named person's decision in Semantic Review (`#/semantic-review`). Until
then the publication gate refuses every symbol with `rights_undecided`, which
is the gate working.

Order: `fix-svgs`, `apply --dry-run`, `apply`, `upload`, approve the rights
record, `gate`, `publish`, `verify`, `report`.

**Symbols held without a standard.** `publish --permitted-only` leaves behind
the symbols whose file cites no standard. `assert-standard` records the
product owner's comparison of each one (`curation/comparisons.csv`, see
`plan_comparisons`) as a verified `comparison_only` link; a second
`publish --permitted-only` then publishes them. `--dry-run` writes nothing.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid
from typing import Any

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from .catalog_facets import compute_catalog_facets
from .classification_assignments import (
    propose_symbol_revision_classification,
    transition_symbol_revision_classification,
)
from .classification_schemes import get_classification_scheme
from .dexpi_ingest import ConfigurationError, PublicationRefused, _require_actor
from .dexpi_seed import index_existing as index_existing_concepts
from .disc_dexpi_ingest import _retire_superseded
from .image_content import UnsafeImageContentError, validate_stored_image
from .models import (
    Attachment,
    AuditEvent,
    ClassificationNode,
    GovernedSymbol,
    PublicationJob,
    PublicationPack,
    RightsRecord,
    SemanticConceptRevision,
    SourcePackage,
    StandardVersion,
    SymbolRevision,
    SymbolStandardLink,
)
from .publication_gate import describe_refusal, enforce_publication_gate
from .rights_provenance import propose_rights_record, record_asset_transformation
from .runtime import download_object_bytes, publish_revision_to_pack, upload_object_bytes
from .semantic_concepts import create_semantic_concept, transition_semantic_concept_revision
from .services import commons_ingestion as plan_module
from .services.commons_ingestion import (
    CATEGORY_SCHEME_CODE,
    COMPARISON_RELATIONSHIP_TYPE,
    COMPARISON_VERIFICATION_METHOD,
    COMPARISONS_FILE,
    CLASSIFICATION_RULE_METHOD,
    DISCIPLINE_SCHEME_CODE,
    FIRE_DISCIPLINE,
    FIX_TOOL_NAME,
    FIX_TOOL_VERSION,
    ISO_10628_CODE,
    ISO_10628_EDITION,
    LINK_VERIFICATION_METHOD,
    PACKAGE_CODE,
    PUBLICATION_PACK_CODE,
    REPRESENTATION_TYPE_NODE_CODE,
    REPRESENTATION_TYPE_SCHEME_CODE,
    REVISION_LIFECYCLE_STATE,
    SEMANTIC_METHOD,
    CommonsPlanError,
)
from .source_package_acquisition import add_source_package_entry, get_source_package, register_source_package
from .standard_sources import (
    assert_symbol_standard_link,
    get_standard,
    register_standard,
    register_standard_version,
    transition_symbol_standard_link,
)
from .symbol_semantic_assignments import (
    propose_symbol_semantic_assignment,
    transition_symbol_semantic_assignment,
)

_CONCEPT_PUBLISH_SEQUENCE = ("review", "approved", "published")
EXIT_BLOCKED = 2
FIXED_DIRECTORY = "svg-fixed"
# Refusals `publish --permitted-only` may leave behind. A rights refusal is never one of them.
SKIPPABLE_REFUSALS = frozenset({"graphical_authority_unasserted"})


class Blocked(ValueError):
    """The import found a reason not to write anything. Carries the reasons."""

    def __init__(self, reasons: list[str]):
        super().__init__("; ".join(reasons))
        self.reasons = reasons


def _engine():
    url = os.environ.get("SYMGOV_DATABASE_URL")
    if not url:
        raise ConfigurationError("SYMGOV_DATABASE_URL is required in the environment")
    return create_engine(url, hide_parameters=True)


# --------------------------------------------------------------------------
# The package on disk.
# --------------------------------------------------------------------------


def load_package(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    if not root.is_dir():
        raise ConfigurationError(f"package directory not found at {root}")
    manifest_path = root / "manifest.json"
    worksheet_path = root / "curation" / "worksheet.csv"
    svg_dir = root / "svg"
    for path in (manifest_path, worksheet_path, svg_dir):
        if not path.exists():
            raise ConfigurationError(f"{path.relative_to(root)} not found in {root}")
    with worksheet_path.open(encoding="utf-8", newline="") as handle:
        worksheet = list(csv.DictReader(handle))
    fixed_dir = root / FIXED_DIRECTORY
    comparisons_path = root / "curation" / COMPARISONS_FILE
    comparisons = []
    if comparisons_path.exists():
        with comparisons_path.open(encoding="utf-8", newline="") as handle:
            comparisons = list(csv.DictReader(handle))
    return {
        "root": str(root),
        "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
        "worksheet": worksheet,
        "svg": {f"svg/{path.name}": path.read_bytes() for path in sorted(svg_dir.glob("*.svg"))},
        "fixed": {path.name: path.read_bytes() for path in sorted(fixed_dir.glob("*.svg"))} if fixed_dir.is_dir() else {},
        "comparisons": comparisons,
    }


def final_bytes_by_sha(package_data: dict[str, Any]) -> dict[str, bytes]:
    """The bytes each planned symbol is published from, by digest."""
    return {
        result["fixed_sha256"]: result["bytes"]
        for result in plan_module.fix_results(package_data).values()
        if "error" not in result and not result["excluded"]
    }


def fix_svgs(package_data: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    """Write every fixed SVG to `svg-fixed/`, and the steps to `svg-fixed/fixes.json`.

    Writes nothing outside `svg-fixed/`. A fixed file already there with other
    bytes is a refusal, not an overwrite: something else made it.
    """
    root = Path(package_data["root"])
    target = root / FIXED_DIRECTORY
    fixes = plan_module.fix_results(package_data)
    written, unchanged, conflicts, excluded, errors = [], [], [], [], []
    report_files = []
    for pageid, result in sorted(fixes.items(), key=lambda item: int(item[0])):
        if "error" in result:
            errors.append({"pageid": int(pageid), "error": result["error"]})
            continue
        if result["excluded"]:
            excluded.append({"pageid": int(pageid), "reason": result["excluded"], "original_sha256": result["original_sha256"]})
            continue
        if not result["steps"]:
            continue
        name = plan_module.svg_filename(pageid)
        problems = plan_module.final_svg_problems(result["bytes"])
        if problems:
            errors.append({"pageid": int(pageid), "error": "; ".join(problems)})
            continue
        existing = package_data["fixed"].get(name)
        if existing is not None and existing != result["bytes"]:
            conflicts.append(name)
            continue
        report_files.append(
            {
                "pageid": int(pageid),
                "file": f"{FIXED_DIRECTORY}/{name}",
                "original_sha256": result["original_sha256"],
                "fixed_sha256": result["fixed_sha256"],
                "steps": result["steps"],
                "visible_elements": result["visible_elements"],
            }
        )
        if existing is not None:
            unchanged.append(name)
            continue
        if not dry_run:
            target.mkdir(exist_ok=True)
            (target / name).write_bytes(result["bytes"])
            package_data["fixed"][name] = result["bytes"]
        written.append(name)
    report = {
        "mode": "fix-svgs-dry-run" if dry_run else "fix-svgs",
        "tool": {"name": FIX_TOOL_NAME, "version": FIX_TOOL_VERSION},
        "written": len(written),
        "already_present": len(unchanged),
        "conflicts": conflicts,
        "excluded": excluded,
        "errors": errors,
        "by_fix": dict(sorted({step["fix"]: sum(1 for f in report_files for s in f["steps"] if s["fix"] == step["fix"])
                               for f in report_files for step in f["steps"]}.items())),
        "files": report_files,
    }
    if not dry_run and not conflicts:
        target.mkdir(exist_ok=True)
        (target / "fixes.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if conflicts:
        raise Blocked([f"svg-fixed/{name} exists with other bytes; remove it or find out what wrote it" for name in conflicts])
    return report


# --------------------------------------------------------------------------
# Reading what is already recorded.
# --------------------------------------------------------------------------


def _live_rights(session: Session, package_id: uuid.UUID) -> RightsRecord | None:
    rows = session.execute(
        select(RightsRecord)
        .where(RightsRecord.source_package_id == package_id, RightsRecord.decision_status.in_(("proposed", "approved")))
        .order_by(RightsRecord.created_at.desc())
    ).scalars().all()
    return rows[0] if rows else None


def read_state(session: Session, plan: dict[str, Any]) -> dict[str, Any]:
    """Everything the import's decisions depend on, read once."""
    state: dict[str, Any] = {}
    package = get_source_package(session, PACKAGE_CODE)
    state["package"] = package
    state["rights"] = _live_rights(session, package.id) if package is not None else None

    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    symbols: dict[str, dict[str, Any]] = {}
    for symbol in session.execute(select(GovernedSymbol).where(GovernedSymbol.slug.in_(slugs))).scalars():
        revisions = session.execute(
            select(SymbolRevision)
            .where(SymbolRevision.symbol_id == symbol.id)
            .order_by(SymbolRevision.created_at, SymbolRevision.revision_label)
        ).scalars().all()
        symbols[symbol.slug] = {"symbol": symbol, "revisions": revisions, "latest": revisions[-1] if revisions else None}
    state["symbols"] = symbols
    state["concepts"] = index_existing_concepts(session)
    state["own_concepts"] = {
        name.casefold()
        for name in session.execute(
            select(SemanticConceptRevision.preferred_name).where(SemanticConceptRevision.notes == plan_module.NEW_CONCEPT_NOTES)
        ).scalars()
    }

    catalogue: dict[str, dict[str, Any]] = {}
    for code in (DISCIPLINE_SCHEME_CODE, CATEGORY_SCHEME_CODE, REPRESENTATION_TYPE_SCHEME_CODE):
        scheme = get_classification_scheme(session, code)
        nodes: dict[str, ClassificationNode] = {}
        if scheme is not None:
            for node in session.execute(select(ClassificationNode).where(ClassificationNode.scheme_id == scheme.id)).scalars():
                nodes[node.preferred_label] = node
                nodes[node.node_code] = node
        catalogue[code] = {"scheme": scheme, "nodes": nodes}
    state["catalogue"] = catalogue

    editions: dict[tuple[str, str], uuid.UUID] = {}
    standards: dict[str, Any] = {}
    for definition in plan["standards"]:
        standard = get_standard(session, definition["standard_code"])
        if standard is not None:
            standards[definition["standard_code"]] = standard
            for version in session.execute(select(StandardVersion).where(StandardVersion.standard_id == standard.id)).scalars():
                editions[(standard.standard_code, version.version_label)] = version.id
    state["standards"] = standards
    state["editions"] = editions
    drop_slugs = [plan_module.slug_for(item["pageid"]) for item in plan["drops"]]
    drop_slugs += [plan_module.slug_for(item["pageid"]) for item in plan["excluded"]]
    state["present_but_not_planned"] = sorted(
        session.execute(select(GovernedSymbol.slug).where(GovernedSymbol.slug.in_(drop_slugs))).scalars().all()
    )
    return state


# --------------------------------------------------------------------------
# Deciding what to do.
# --------------------------------------------------------------------------


def decide(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The actions `apply` would take, and every reason it would refuse. Pure."""
    blocking: list[str] = list(plan["package_problems"])
    matching = plan_module.match_concepts(plan["concepts"], state["concepts"], state.get("own_concepts", set()))
    blocking.extend(matching["problems"])

    missing: dict[str, set[str]] = {}
    for symbol in plan["symbols"]:
        for scheme_code, label in ((DISCIPLINE_SCHEME_CODE, symbol["discipline"]), (CATEGORY_SCHEME_CODE, symbol["category"])):
            if label not in state["catalogue"][scheme_code]["nodes"]:
                missing.setdefault(scheme_code, set()).add(label)
    if any(symbol["fire"] for symbol in plan["symbols"]) and FIRE_DISCIPLINE not in state["catalogue"][DISCIPLINE_SCHEME_CODE]["nodes"]:
        missing.setdefault(DISCIPLINE_SCHEME_CODE, set()).add(FIRE_DISCIPLINE)
    for scheme_code, labels in sorted(missing.items()):
        blocking.append(f"{scheme_code} has no node labelled: {', '.join(sorted(labels))}")
    if REPRESENTATION_TYPE_NODE_CODE not in state["catalogue"][REPRESENTATION_TYPE_SCHEME_CODE]["nodes"]:
        blocking.append(f"{REPRESENTATION_TYPE_SCHEME_CODE} / {REPRESENTATION_TYPE_NODE_CODE} is not in this database")
    if state["present_but_not_planned"]:
        blocking.append(
            "dropped or excluded files are already recorded as symbols: " + ", ".join(state["present_but_not_planned"])
        )

    standards = []
    for definition in plan["standards"]:
        code = definition["standard_code"]
        standards.append(
            {
                "standard_code": code,
                "exists": code in state["standards"],
                "versions": {
                    label: ("existing" if (code, label) in state["editions"] else "would_register")
                    for label in definition["versions"]
                },
            }
        )

    created, revised, unchanged = [], [], []
    for symbol in plan["symbols"]:
        held = state["symbols"].get(symbol["slug"])
        if held is None:
            created.append(symbol["slug"])
            continue
        latest = held["latest"]
        reasons = plan_module.revision_needs_bump(latest.payload_json if latest else None, symbol)
        if reasons:
            revised.append({"slug": symbol["slug"], "reasons": reasons})
        else:
            unchanged.append(symbol["slug"])
    return {
        "blocking": blocking,
        "concepts": matching,
        "standards": standards,
        "symbols": {"create": created, "revise": revised, "unchanged": unchanged},
        "package_exists": state["package"] is not None,
        "rights_exists": state["rights"] is not None,
        "rights_status": state["rights"].decision_status if state["rights"] is not None else None,
    }


def dry_run_report(plan: dict[str, Any], decision: dict[str, Any]) -> dict[str, Any]:
    concepts = decision["concepts"]
    summary = plan["summary"]
    return {
        "mode": "dry-run",
        "blocked": bool(decision["blocking"]),
        "blocking": decision["blocking"],
        "package": {key: summary[key] for key in (
            "worksheet_rows", "actions", "symbols", "excluded", "dropped", "disciplines", "categories",
            "fixes", "fixed_files", "standard_links", "licence_families", "fire_dampers",
        )},
        "excluded": plan["excluded"],
        "without_standard_link": summary["without_standard_link"],
        "symbols": {
            "would_create": len(decision["symbols"]["create"]),
            "would_revise": decision["symbols"]["revise"],
            "unchanged": len(decision["symbols"]["unchanged"]),
        },
        "source_package": {"exists": decision["package_exists"], "code": PACKAGE_CODE},
        "rights_record": {
            "exists": decision["rights_exists"],
            "decision_status": decision["rights_status"],
            "will_be": "proposed (open / distribute)",
            "evidence_files": plan["rights"]["evidence"]["file_count"],
        },
        "standards": decision["standards"],
        "concepts": {
            "distinct_in_package": len(plan["concepts"]),
            "reuse_existing": len(concepts["reuse"]),
            "would_create": len(concepts["create"]),
            "near_misses": concepts["near_misses"],
            "reused": [{"concept_key": c["concept_key"], "concept_code": c["concept_code"]} for c in concepts["reuse"]],
        },
    }


# --------------------------------------------------------------------------
# Writing.
# --------------------------------------------------------------------------


def ensure_package_and_rights(
    session: Session, plan: dict[str, Any], state: dict[str, Any], *, actor_id: uuid.UUID, occurred_at: datetime
) -> tuple[SourcePackage, RightsRecord]:
    package = state["package"]
    if package is None:
        definition = plan["source_package"]
        package = register_source_package(
            session,
            package_code=definition["package_code"],
            title=definition["title"],
            provider=definition["provider"],
            registered_at=occurred_at,
            source_uri=definition["source_uri"],
            release_version=definition["release_version"],
            acquired_at=occurred_at,
            acquisition_method=definition["acquisition_method"],
            licence_reference=plan["rights"]["licence_reference"],
            metadata=definition["metadata"],
        )
        session.flush()
        state["package"] = package
    rights = state["rights"]
    if rights is None:
        definition = plan["rights"]
        rights = propose_rights_record(
            session,
            source_package_id=package.id,
            rights_status=definition["rights_status"],
            disposition=definition["disposition"],
            determination_method=definition["determination_method"],
            licence_reference=definition["licence_reference"],
            proposed_at=occurred_at,
            proposed_by_user_id=actor_id,
            decision_reason=definition["decision_reason"],
            evidence=definition["evidence"],
        )
        session.flush()
        state["rights"] = rights
    return package, rights


def ensure_standards(session: Session, plan: dict[str, Any], state: dict[str, Any], *, occurred_at: datetime) -> None:
    """Reuse the existing standard and edition records; register only what is missing."""
    for definition in plan["standards"]:
        standard = state["standards"].get(definition["standard_code"]) or get_standard(session, definition["standard_code"])
        if standard is None:
            standard = register_standard(
                session,
                standard_code=definition["standard_code"],
                title=definition["title"],
                issuing_body=definition["issuing_body"],
                registered_at=occurred_at,
            )
            session.flush()
            state["standards"][definition["standard_code"]] = standard
        for version_label in definition["versions"]:
            key = (standard.standard_code, version_label)
            if key in state["editions"]:
                continue
            version = register_standard_version(
                session, standard_id=standard.id, version_label=version_label, registered_at=occurred_at
            )
            session.flush()
            state["editions"][key] = version.id


def ensure_concepts(
    session: Session, decision: dict[str, Any], *, actor_id: uuid.UUID, occurred_at: datetime
) -> dict[str, dict[str, Any]]:
    resolved: dict[str, dict[str, Any]] = {}
    for concept in decision["concepts"]["reuse"]:
        resolved[concept["concept_key"]] = {
            "concept_id": uuid.UUID(concept["concept_id"]), "concept_code": concept["concept_code"], "created": False,
            "created_earlier_by_this_import": bool(concept.get("created_by_this_import")),
        }
    for planned in decision["concepts"]["create"]:
        concept, revision = create_semantic_concept(
            session,
            concept_kind=planned["concept_kind"],
            preferred_name=planned["preferred_name"],
            definition=planned["definition"],
            created_by_user_id=actor_id,
            created_at=occurred_at,
            revision_label="r1",
            aliases=planned["aliases"],
            notes=planned["notes"],
            rationale=planned["rationale"],
        )
        for target_state in _CONCEPT_PUBLISH_SEQUENCE:
            transition_semantic_concept_revision(
                session, revision.id, target_state=target_state, actor_id=actor_id, occurred_at=occurred_at
            )
        resolved[planned["concept_key"]] = {"concept_id": concept.id, "concept_code": concept.concept_code, "created": True}
    session.flush()
    return resolved


def _propose_and_verify(session: Session, propose, transition, *, occurred_at: datetime, **kwargs):
    assignment = propose(session, proposed_at=occurred_at, **kwargs)
    session.flush()
    transition(session, assignment.id, target_status="verified", occurred_at=occurred_at)
    return assignment


def create_revision(
    session: Session,
    symbol_plan: dict[str, Any],
    symbol: GovernedSymbol,
    *,
    revision_label: str,
    package: SourcePackage,
    state: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> SymbolRevision:
    """One revision and every governed fact it carries."""
    payload = plan_module.build_revision_payload(symbol_plan, revision_label=revision_label)
    revision = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label=revision_label,
        lifecycle_state=REVISION_LIFECYCLE_STATE,
        payload_json=payload,
        rationale=symbol_plan["rationale"],
        author_id=actor_id,
        created_at=occurred_at,
    )
    session.add(revision)
    session.flush()
    asset = payload["assets"][0]
    session.add(
        Attachment(
            id=uuid.uuid4(),
            parent_type="symbol_revision",
            parent_id=revision.id,
            filename=asset["filename"],
            object_key=asset["object_key"],
            content_type=asset["content_type"],
            size_bytes=asset["size_bytes"],
            sha256=asset["sha256"],
            created_at=occurred_at,
        )
    )
    entry_plan = symbol_plan["entry"]
    entry = add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision.id,
        added_at=occurred_at,
        source_label=entry_plan["source_label"],
        provider_entry_identifier=entry_plan["provider_entry_identifier"],
        source_path=entry_plan["source_path"],
        original_asset_sha256=entry_plan["original_asset_sha256"],
        sort_order=symbol_plan["sort_order"],
    )
    session.flush()

    # Decision 7: each fix is one step of the chain, Commons original first.
    for index, step in enumerate(symbol_plan["fix_steps"]):
        record_asset_transformation(
            session,
            symbol_revision_id=revision.id,
            tool_name=FIX_TOOL_NAME,
            tool_version=FIX_TOOL_VERSION,
            source_asset_sha256=step["source_sha256"],
            derived_asset_sha256=step["derived_sha256"],
            performed_at=occurred_at,
            source_package_entry_id=entry.id if index == 0 else None,
            recorded_by_user_id=actor_id,
            evidence={
                "fix": step["fix"],
                "detail": step["detail"],
                "original_sha256": symbol_plan["original_sha256"],
                "fixed_sha256": symbol_plan["final_sha256"],
                "commons_pageid": symbol_plan["pageid"],
            },
        )
        session.flush()

    for assertion in symbol_plan["standards"]:
        link = assert_symbol_standard_link(
            session,
            symbol_revision_id=revision.id,
            standard_version_id=state["editions"][(assertion["standard_code"], assertion["version_label"])],
            relationship_type=assertion["relationship_type"],
            asserted_at=occurred_at,
            source_uri=symbol_plan["commons"]["page_url"],
            notes=f"The Wikimedia Commons file cites {assertion['cited_as']}; drawn to it, not copied from it.",
            evidence={
                "basis": assertion["basis"],
                "cited_as": assertion["cited_as"],
                "edition": assertion["edition_basis"],
                "commons_title": symbol_plan["commons"]["title"],
            },
        )
        session.flush()
        transition_symbol_standard_link(
            session, link.id, target_status="verified", occurred_at=occurred_at, verification_method=LINK_VERIFICATION_METHOD
        )

    concept = resolved[symbol_plan["concept_key"]]
    _propose_and_verify(
        session,
        propose_symbol_semantic_assignment,
        transition_symbol_semantic_assignment,
        occurred_at=occurred_at,
        symbol_revision_id=revision.id,
        semantic_concept_id=concept["concept_id"],
        assignment_role="primary",
        method=SEMANTIC_METHOD,
        proposed_by_user_id=actor_id,
        evidence={"concept_key": symbol_plan["concept_key"], "source": "curation/worksheet.csv"},
    )

    catalogue = state["catalogue"]
    worksheet_evidence = {"source": "curation/worksheet.csv", "confirmed": "product owner", "pageid": symbol_plan["pageid"]}
    classifications = [
        (catalogue[DISCIPLINE_SCHEME_CODE]["nodes"][symbol_plan["discipline"]], "primary",
         {**worksheet_evidence, "basis": "curated discipline", "value": symbol_plan["discipline"]}),
        (catalogue[CATEGORY_SCHEME_CODE]["nodes"][symbol_plan["category"]], "primary",
         {**worksheet_evidence, "basis": "curated category", "value": symbol_plan["category"]}),
        (catalogue[REPRESENTATION_TYPE_SCHEME_CODE]["nodes"][REPRESENTATION_TYPE_NODE_CODE], "primary",
         {"basis": "every symbol this package carries is an SVG vector drawing", "format": "svg"}),
    ]
    if symbol_plan["fire"]:
        classifications.append(
            (catalogue[DISCIPLINE_SCHEME_CODE]["nodes"][FIRE_DISCIPLINE], "secondary",
             {**worksheet_evidence, "basis": "a fire or smoke damper is also Fire & Life Safety (import decision 5)",
              "name": symbol_plan["canonical_name"]})
        )
    for node, role, evidence in classifications:
        _propose_and_verify(
            session,
            propose_symbol_revision_classification,
            transition_symbol_revision_classification,
            occurred_at=occurred_at,
            symbol_revision_id=revision.id,
            classification_node_id=node.id,
            assignment_role=role,
            method=CLASSIFICATION_RULE_METHOD,
            proposed_by_user_id=actor_id,
            evidence=evidence,
        )
    return revision


def invalidate_facets(session: Session, revision_ids: list[uuid.UUID]) -> None:
    """Mark stored Catalog facet rows out of date.

    The facet store's staleness counters move when a payload, name or category
    changes, not when a classification assignment is added, so a row computed
    before the assignments would keep the keyword rules' answer. The DISC
    import needed the same step.
    """
    if revision_ids:
        session.execute(
            text("UPDATE catalog_symbol_facets SET rules_version = 0 WHERE symbol_revision_id = ANY(:ids)"),
            {"ids": revision_ids},
        )


def apply(session: Session, *, plan: dict[str, Any], actor_id: uuid.UUID, occurred_at: datetime) -> dict[str, Any]:
    """Record everything the dry run reported, or refuse and write nothing."""
    _require_actor(session, actor_id)
    state = read_state(session, plan)
    decision = decide(plan, state)
    if decision["blocking"]:
        raise Blocked(decision["blocking"])

    package, rights = ensure_package_and_rights(session, plan, state, actor_id=actor_id, occurred_at=occurred_at)
    ensure_standards(session, plan, state, occurred_at=occurred_at)
    resolved = ensure_concepts(session, decision, actor_id=actor_id, occurred_at=occurred_at)

    created, revised, touched = [], [], []
    revise_reasons = {item["slug"]: item["reasons"] for item in decision["symbols"]["revise"]}
    unchanged = set(decision["symbols"]["unchanged"])
    for symbol_plan in plan["symbols"]:
        slug = symbol_plan["slug"]
        if slug in unchanged:
            continue
        held = state["symbols"].get(slug)
        if held is None:
            symbol = GovernedSymbol(
                id=uuid.uuid4(),
                slug=slug,
                canonical_name=symbol_plan["canonical_name"],
                category=symbol_plan["category"],
                discipline=symbol_plan["discipline"],
                owner_id=actor_id,
                owner_organization_id=None,
                visibility="public",
                organization_wide=False,
                current_revision_id=None,
                created_at=occurred_at,
                updated_at=occurred_at,
            )
            session.add(symbol)
            session.flush()
            label = plan_module.FIRST_REVISION_LABEL
        else:
            symbol = held["symbol"]
            label = plan_module.next_revision_label([r.revision_label for r in held["revisions"]])
            symbol.canonical_name = symbol_plan["canonical_name"]
            symbol.category = symbol_plan["category"]
            symbol.discipline = symbol_plan["discipline"]
            symbol.updated_at = occurred_at
        revision = create_revision(
            session, symbol_plan, symbol, revision_label=label, package=package, state=state,
            resolved=resolved, actor_id=actor_id, occurred_at=occurred_at,
        )
        touched.append(revision.id)
        if held is None:
            symbol.current_revision_id = revision.id
            created.append({"slug": slug, "symbol_revision_id": str(revision.id), "revision": label})
        else:
            revised.append({"slug": slug, "symbol_revision_id": str(revision.id), "revision": label, "reasons": revise_reasons.get(slug, [])})
    session.flush()
    invalidate_facets(session, touched)
    return {
        "mode": "apply",
        "package_id": str(package.id),
        "rights_record_id": str(rights.id),
        "rights_decision_status": rights.decision_status,
        "concepts_created": [
            {"concept_key": key, "concept_code": info["concept_code"]} for key, info in resolved.items() if info["created"]
        ],
        "concepts_reused": [
            {"concept_key": key, "concept_code": info["concept_code"]} for key, info in resolved.items() if not info["created"]
        ],
        "near_misses": decision["concepts"]["near_misses"],
        "standards": decision["standards"],
        "symbols_created": created,
        "symbols_revised": revised,
        "symbols_unchanged": len(unchanged),
        "excluded": plan["excluded"],
    }


# --------------------------------------------------------------------------
# Storage.
# --------------------------------------------------------------------------


def _pending_attachments(session: Session, plan: dict[str, Any]) -> list[Any]:
    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    return list(
        session.execute(
            text(
                "SELECT a.object_key, a.sha256, a.content_type "
                "FROM attachments a "
                "JOIN symbol_revisions sr ON sr.id = a.parent_id "
                "JOIN governed_symbols gs ON gs.id = sr.symbol_id "
                "WHERE a.parent_type = 'symbol_revision' AND gs.slug = ANY(:slugs) "
                "AND sr.lifecycle_state = :state ORDER BY a.object_key"
            ),
            {"slugs": slugs, "state": REVISION_LIFECYCLE_STATE},
        ).all()
    )


def upload_assets(
    session: Session,
    plan: dict[str, Any],
    package_data: dict[str, Any],
    *,
    storage_env_file,
    dry_run: bool,
    uploader=None,
) -> dict[str, Any]:
    """PUT every pending attachment's SVG. Reads the database, writes nothing to it."""
    uploader = uploader or upload_object_bytes
    by_sha = final_bytes_by_sha(package_data)
    attachments = _pending_attachments(session, plan)
    problems, uploaded = [], []
    for attachment in attachments:
        data = by_sha.get(attachment.sha256 or "")
        if data is None:
            problems.append(f"{attachment.object_key}: no planned SVG has sha256 {attachment.sha256}")
            continue
        try:
            validate_stored_image(data, attachment.content_type)
        except UnsafeImageContentError as exc:
            problems.append(f"{attachment.object_key}: {exc}")
            continue
        if not dry_run:
            uploader(object_key=attachment.object_key, payload=data, content_type=attachment.content_type, env_file=storage_env_file)
        uploaded.append(attachment.object_key)
    if problems:
        raise Blocked(problems)
    return {
        "mode": "upload-dry-run" if dry_run else "upload",
        "attachments": len(attachments),
        "uploaded_count": 0 if dry_run else len(uploaded),
        "bytes": sum(len(by_sha[a.sha256]) for a in attachments),
    }


# --------------------------------------------------------------------------
# The gate and publication.
# --------------------------------------------------------------------------


def _latest_revisions(session: Session, plan: dict[str, Any]) -> list[tuple[GovernedSymbol, SymbolRevision]]:
    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    rows = []
    for symbol in session.execute(select(GovernedSymbol).where(GovernedSymbol.slug.in_(slugs)).order_by(GovernedSymbol.slug)).scalars():
        latest = session.execute(
            select(SymbolRevision)
            .where(SymbolRevision.symbol_id == symbol.id)
            .order_by(SymbolRevision.created_at.desc(), SymbolRevision.revision_label.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None:
            rows.append((symbol, latest))
    return rows


def evaluate_gate(session: Session, *, plan: dict[str, Any], occurred_at: datetime, actor_id: uuid.UUID | None) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    levels: dict[str, int] = {}
    refused_by_reason: dict[str, list[str]] = {}
    samples: list[dict[str, Any]] = []
    evaluated = 0
    for symbol, revision in _latest_revisions(session, plan):
        if revision.lifecycle_state != REVISION_LIFECYCLE_STATE:
            continue
        decision = enforce_publication_gate(session, symbol_revision_id=revision.id, evaluated_at=occurred_at, evaluated_by_user_id=actor_id)
        evaluated += 1
        outcomes[decision.outcome] = outcomes.get(decision.outcome, 0) + 1
        levels[decision.traceability_level] = levels.get(decision.traceability_level, 0) + 1
        for reason in decision.refusal_reasons:
            reasons[reason] = reasons.get(reason, 0) + 1
            refused_by_reason.setdefault(reason, []).append(symbol.slug)
        if decision.outcome == "refused" and len(samples) < 5:
            samples.append({"slug": symbol.slug, "describe": describe_refusal(decision)})
    return {
        "mode": "gate",
        "evaluated": evaluated,
        "outcomes": outcomes,
        "refusal_reasons": reasons,
        "traceability_levels": levels,
        "sample_refusals": samples,
        "refused_slugs_by_reason": {
            reason: slugs for reason, slugs in refused_by_reason.items() if reason in SKIPPABLE_REFUSALS
        },
    }


def _stored_object_problems(pending, session: Session, *, storage_env_file, fetcher) -> list[str]:
    problems = []
    for symbol, revision in pending:
        for attachment in session.execute(
            text("SELECT object_key, sha256 FROM attachments WHERE parent_type = 'symbol_revision' AND parent_id = :revision"),
            {"revision": revision.id},
        ).all():
            try:
                stored = fetcher(object_key=attachment.object_key, env_file=storage_env_file)
            except Exception as exc:  # noqa: BLE001 -- reported by class name only
                problems.append(f"{symbol.slug}: stored object unreadable ({type(exc).__name__})")
                continue
            if plan_module.sha256_hex(stored["payload"]) != attachment.sha256:
                problems.append(f"{symbol.slug}: stored object does not match the recorded digest")
    return problems


def publish(
    session: Session,
    *,
    plan: dict[str, Any],
    actor_id: uuid.UUID,
    occurred_at: datetime,
    storage_env_file,
    dry_run: bool,
    permitted_only: bool = False,
    fetcher=None,
) -> dict[str, Any]:
    """Publish every pending revision to the public pack, or none of them.

    With `permitted_only`, revisions the gate refuses only for want of a
    standard link (`graphical_authority_unasserted`) are left unpublished and
    listed, and the rest are published. Any other refusal -- above all
    `rights_undecided` -- still publishes nothing.
    """
    _require_actor(session, actor_id)
    fetcher = fetcher or download_object_bytes
    sort_orders = {symbol["slug"]: symbol["sort_order"] for symbol in plan["symbols"]}
    rows = _latest_revisions(session, plan)
    if len(rows) != len(plan["symbols"]):
        raise ConfigurationError(f"{len(plan['symbols']) - len(rows)} planned symbols are not recorded; run apply first")
    unchanged = [symbol.slug for symbol, revision in rows if revision.lifecycle_state == "published"]
    pending = [(symbol, revision) for symbol, revision in rows if revision.lifecycle_state != "published"]
    wrong_state = [f"{symbol.slug}: {revision.lifecycle_state}" for symbol, revision in pending if revision.lifecycle_state != REVISION_LIFECYCLE_STATE]
    if wrong_state:
        raise PublicationRefused(f"{len(wrong_state)} revisions are not approved", {"not_approved": wrong_state})
    if not pending:
        return {"mode": "publish", "published_count": 0, "published": [], "unchanged_count": len(unchanged), "held_at_gate": []}

    storage_problems = _stored_object_problems(pending, session, storage_env_file=storage_env_file, fetcher=fetcher)
    if storage_problems:
        raise PublicationRefused(
            f"{len(storage_problems)} stored objects are missing or wrong; run upload first", {"storage_problems": storage_problems}
        )

    refusals: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    levels: dict[str, int] = {}
    held: list[dict[str, Any]] = []
    publishable = []
    for symbol, revision in pending:
        decision = enforce_publication_gate(session, symbol_revision_id=revision.id, evaluated_at=occurred_at, evaluated_by_user_id=actor_id)
        if decision.permitted:
            levels[decision.traceability_level] = levels.get(decision.traceability_level, 0) + 1
            publishable.append((symbol, revision))
            continue
        if permitted_only and set(decision.refusal_reasons) <= SKIPPABLE_REFUSALS:
            held.append({"slug": symbol.slug, "reasons": list(decision.refusal_reasons)})
            continue
        for reason in decision.refusal_reasons:
            refusals[reason] = refusals.get(reason, 0) + 1
        if len(samples) < 5:
            samples.append({"slug": symbol.slug, "describe": describe_refusal(decision)})
    if refusals:
        raise PublicationRefused(
            "the publication gate refused at least one symbol; nothing was published",
            {"refusal_reasons": refusals, "sample_refusals": samples},
        )
    if dry_run:
        return {
            "mode": "publish-dry-run",
            "would_publish": len(publishable),
            "held_at_gate": held,
            "unchanged_count": len(unchanged),
            "traceability_levels": levels,
        }

    pack = session.execute(select(PublicationPack).where(PublicationPack.pack_code == PUBLICATION_PACK_CODE)).scalar_one_or_none()
    if pack is None:
        pack = PublicationPack(
            id=uuid.uuid4(),
            pack_code=PUBLICATION_PACK_CODE,
            title=plan["publication"]["pack_title"],
            audience="public",
            effective_date=occurred_at.date(),
            status="published",
            created_at=occurred_at,
            updated_at=occurred_at,
        )
        session.add(pack)
    else:
        pack.status = "published"
        pack.updated_at = occurred_at
    session.flush()
    job = PublicationJob(
        id=uuid.uuid4(),
        pack_id=pack.id,
        status="completed",
        requested_by=actor_id,
        approved_by=actor_id,
        artifact_manifest_json={
            "source": "commons_ingest publish",
            "source_package_code": PACKAGE_CODE,
            "symbol_count": len(publishable),
            "held_at_gate": len(held),
            "simulation": False,
        },
        created_at=occurred_at,
        completed_at=occurred_at,
    )
    session.add(job)
    session.flush()

    published = []
    for symbol, revision in publishable:
        previous_id = symbol.current_revision_id if symbol.current_revision_id != revision.id else None
        page, entry = publish_revision_to_pack(
            session, symbol=symbol, revision=revision, publication_pack=pack, sort_order=sort_orders[symbol.slug],
            effective_date=pack.effective_date, published_at=occurred_at,
        )
        if previous_id is not None:
            _retire_superseded(session, symbol, previous_id, page.id, actor_id=actor_id, occurred_at=occurred_at)
        published.append(
            {
                "slug": symbol.slug,
                "catalog_symbol_id": symbol.catalog_symbol_id,
                "page_code": page.page_code,
                "published_page_id": str(page.id),
                "pack_entry_id": str(entry.id),
                "revision": revision.revision_label,
                "symbol_revision_id": str(revision.id),
            }
        )
    audit_payload = {
        "publication_job_id": str(job.id),
        "pack_code": pack.pack_code,
        "source_package_code": PACKAGE_CODE,
        "published_count": len(published),
        "approval_actor": {"id": str(actor_id), "type": "user"},
    }
    events = [
        ("publication_pack", pack.id, "publication_pack_published"),
        ("publication_job", job.id, "publication_job_completed"),
    ] + [("published_page", uuid.UUID(item["published_page_id"]), "published_page_upserted") for item in published]
    for entity_type, entity_id, action in events:
        session.add(AuditEvent(id=uuid.uuid4(), entity_type=entity_type, entity_id=entity_id, action=action,
                               actor_id=actor_id, payload_json=audit_payload, created_at=occurred_at))
    session.flush()
    session.execute(text("SELECT refresh_published_symbol_views()"))
    revision_ids = [uuid.UUID(item["symbol_revision_id"]) for item in published]
    invalidate_facets(session, revision_ids)
    facets = compute_catalog_facets(session, revision_ids)
    return {
        "mode": "publish",
        "publication_pack_code": pack.pack_code,
        "publication_job_id": str(job.id),
        "published_count": len(published),
        "published": published,
        "held_at_gate": held,
        "unchanged_count": len(unchanged),
        "traceability_levels": levels,
        "facet_rows_computed": facets,
    }


# --------------------------------------------------------------------------
# Comparison links for the symbols held without a standard.
# --------------------------------------------------------------------------


def _iso_10628_edition(session: Session) -> StandardVersion | None:
    standard = get_standard(session, ISO_10628_CODE)
    if standard is None:
        return None
    return session.execute(
        select(StandardVersion).where(
            StandardVersion.standard_id == standard.id, StandardVersion.version_label == ISO_10628_EDITION
        )
    ).scalar_one_or_none()


def assert_comparisons(
    session: Session, *, plan: dict[str, Any], comparisons: list[dict[str, Any]], actor_id: uuid.UUID,
    occurred_at: datetime, dry_run: bool,
) -> dict[str, Any]:
    """Record each `compared` row as a verified `comparison_only` link, or refuse and write nothing.

    The link goes on the symbol's held revision, which must still be approved
    and unpublished. Every reference must already be published, because the
    comparison was made against what the Catalog shows. A revision that
    already carries the link is left alone, so a re-run changes nothing.
    """
    _require_actor(session, actor_id)
    decided = plan_module.plan_comparisons(plan, comparisons)
    problems = list(decided["problems"])
    edition = _iso_10628_edition(session)
    if edition is None:
        problems.append(f"{ISO_10628_CODE}:{ISO_10628_EDITION} is not in this database")

    reference_slugs = sorted({ref["slug"] for row in decided["compared"] for ref in row["references"]})
    published_references = {
        row.slug: row.catalog_symbol_id
        for row in session.execute(
            select(GovernedSymbol.slug, GovernedSymbol.catalog_symbol_id)
            .join(SymbolRevision, SymbolRevision.id == GovernedSymbol.current_revision_id)
            .where(GovernedSymbol.slug.in_(reference_slugs), SymbolRevision.lifecycle_state == "published")
        )
    }
    latest = {symbol.slug: revision for symbol, revision in _latest_revisions(session, plan)}
    to_assert, unchanged = [], []
    for row in decided["compared"]:
        revision = latest.get(row["slug"])
        if revision is None:
            problems.append(f"{row['slug']}: not recorded; run apply first")
            continue
        missing = [ref["slug"] for ref in row["references"] if not published_references.get(ref["slug"])]
        if missing:
            problems.append(f"{row['slug']}: reference not published: {', '.join(missing)}")
            continue
        existing = edition is not None and session.execute(
            select(SymbolStandardLink.id).where(
                SymbolStandardLink.symbol_revision_id == revision.id,
                SymbolStandardLink.standard_version_id == edition.id,
                SymbolStandardLink.relationship_type == COMPARISON_RELATIONSHIP_TYPE,
                SymbolStandardLink.assertion_status.in_(("proposed", "verified")),
            )
        ).first()
        if existing:
            unchanged.append(row["slug"])
            continue
        if revision.lifecycle_state != REVISION_LIFECYCLE_STATE:
            problems.append(f"{row['slug']}: latest revision is {revision.lifecycle_state}, not {REVISION_LIFECYCLE_STATE}")
            continue
        to_assert.append((row, revision))
    if problems:
        raise Blocked(problems)

    asserted = []
    for row, revision in to_assert:
        compared_with = [
            {"slug": ref["slug"], "catalog_symbol_id": published_references[ref["slug"]], "name": ref["name"]}
            for ref in row["references"]
        ]
        identifiers = ", ".join(f"{item['catalog_symbol_id']} ({item['name']})" for item in compared_with)
        if not dry_run:
            link = assert_symbol_standard_link(
                session,
                symbol_revision_id=revision.id,
                standard_version_id=edition.id,
                relationship_type=COMPARISON_RELATIONSHIP_TYPE,
                asserted_at=occurred_at,
                source_uri=row["page_url"],
                notes=(
                    f"Compared with the ISO 10628-2 drawings in the Catalog: {identifiers}. "
                    "Records that comparison only; the Commons file does not claim the standard."
                )[:2000],
                evidence={
                    "basis": "product_owner_comparison",
                    "source": f"curation/{COMPARISONS_FILE}",
                    "finding": row["finding"],
                    "compared_with": compared_with,
                    "commons_pageid": row["pageid"],
                },
            )
            session.flush()
            transition_symbol_standard_link(
                session, link.id, target_status="verified", occurred_at=occurred_at,
                verification_method=COMPARISON_VERIFICATION_METHOD, verified_by_user_id=actor_id,
            )
        asserted.append({"slug": row["slug"], "compared_with": [item["catalog_symbol_id"] for item in compared_with]})
    session.flush()
    return {
        "mode": "assert-standard-dry-run" if dry_run else "assert-standard",
        "asserted_count": len(asserted),
        "asserted": asserted,
        "unchanged": unchanged,
        "held_by_decision": decided["held_by_decision"],
    }


# --------------------------------------------------------------------------
# The command.
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    fix = commands.add_parser("fix-svgs", help="Write the fixed SVGs to svg-fixed/ inside the package (nowhere else)")
    fix.add_argument("--package", required=True)
    fix.add_argument("--dry-run", action="store_true")
    fix.add_argument("--output")
    for name, help_text in (
        ("apply", "Record the package: source package, rights, standards, concepts, symbols"),
        ("upload", "Put every pending SVG in object storage (reads the database)"),
        ("gate", "Evaluate the publication gate over the pending revisions"),
        ("publish", "Publish every pending revision to the public pack, or none"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--package", required=True, help="The harvest package directory")
        command.add_argument("--output", help="Write the full report here as JSON")
        if name in {"apply", "upload", "publish"}:
            command.add_argument("--dry-run", action="store_true", help="Read and decide; write nothing")
        if name in {"upload", "publish"}:
            command.add_argument("--storage-env-file", help="Object storage settings (default: the API's)")
        if name in {"apply", "publish"}:
            command.add_argument("--actor-id", type=uuid.UUID, required=(name == "publish"), help="Existing user UUID")
        if name == "gate":
            command.add_argument("--actor-id", type=uuid.UUID, help="Optional: the operator the evaluation is recorded against")
        if name == "publish":
            command.add_argument(
                "--permitted-only", action="store_true",
                help="Leave symbols the gate refuses only for want of a standard link unpublished, and publish the rest",
            )
    compare = commands.add_parser(
        "assert-standard",
        help=f"Record curation/{COMPARISONS_FILE} as verified comparison_only links to ISO 10628 (held symbols only)",
    )
    compare.add_argument("--package", required=True)
    compare.add_argument("--actor-id", type=uuid.UUID, required=True, help="The product owner who made the comparison")
    compare.add_argument("--dry-run", action="store_true", help="Read and decide; write nothing")
    compare.add_argument("--output")
    verify = commands.add_parser("verify", help="Run the acceptance checks against the database (reads only)")
    verify.add_argument("--package", required=True)
    verify.add_argument("--output")
    verify.add_argument("--storage-env-file")
    verify.add_argument("--no-storage", action="store_true", help="Skip the checks that read stored SVGs")
    verify.add_argument("--chromium", help="A headless Chromium binary, for the dHash near-duplicate report")
    report = commands.add_parser("report", help="Write the Markdown import report from the saved JSON reports")
    report.add_argument("--package", required=True)
    report.add_argument("--out", required=True)
    for name in ("fix-report", "dry-run-report", "apply-report", "gate-report", "publish-report", "verify-report"):
        report.add_argument(f"--{name}", help="JSON written by the matching command's --output")
    report.add_argument("--backup")
    report.add_argument("--commit")
    report.add_argument("--rights-approver")
    report.add_argument("--banner", help="A line printed above the title, e.g. to mark a rehearsal")
    return parser


def _storage_env_file(args):
    if getattr(args, "storage_env_file", None):
        return args.storage_env_file
    from .settings import get_settings

    return get_settings().storage_env_file


def _write_output(report: dict[str, Any], path: str | None) -> None:
    if path:
        Path(path).write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _printable(report: dict[str, Any]) -> dict[str, Any]:
    printable = dict(report)
    for key in ("symbols_created", "symbols_revised", "published", "concepts_created", "concepts_reused", "files", "reused", "asserted"):
        if isinstance(printable.get(key), list):
            printable[key] = len(printable[key])
    if isinstance(printable.get("concepts"), dict) and isinstance(printable["concepts"].get("reused"), list):
        printable["concepts"] = {**printable["concepts"], "reused": len(printable["concepts"]["reused"])}
    return printable


def _read_report(path: str | None):
    return json.loads(Path(path).read_text(encoding="utf-8")) if path else None


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        package_data = load_package(args.package)
        if args.command == "fix-svgs":
            report = fix_svgs(package_data, dry_run=args.dry_run)
            _write_output(report, args.output)
            print(json.dumps(_printable(report), indent=2, default=str))
            return EXIT_BLOCKED if report["errors"] else 0
        plan = plan_module.plan_import(package_data)
        if args.command == "report":
            from .commons_verify import render_report

            markdown = render_report(
                plan=plan,
                fix_report=_read_report(args.fix_report),
                dry_run=_read_report(args.dry_run_report),
                apply_report=_read_report(args.apply_report),
                gate_report=_read_report(args.gate_report),
                publish_report=_read_report(args.publish_report),
                verify_report=_read_report(args.verify_report),
                backup_path=args.backup,
                commit=args.commit,
                rights_approver=args.rights_approver,
                banner=args.banner,
            )
            Path(args.out).write_text(markdown, encoding="utf-8")
            print(json.dumps({"written": args.out, "bytes": len(markdown.encode("utf-8"))}, indent=2))
            return 0
        engine = _engine()
        try:
            now = datetime.now(timezone.utc)
            if args.command == "verify":
                from .commons_verify import verify

                with Session(engine) as session:
                    report = verify(
                        session, plan, package_data,
                        fetcher=None if args.no_storage else (lambda **kwargs: download_object_bytes(**kwargs)),
                        storage_env_file=None if args.no_storage else _storage_env_file(args),
                        chromium=args.chromium or os.environ.get("SYMGOV_CHROMIUM"),
                    )
                    session.rollback()
                _write_output(report, args.output)
                print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                                  "checks": [{"id": c["id"], "status": c["status"]} for c in report["checks"]]}, indent=2))
                return 0 if report["passed"] else 1
            if args.command == "apply" and args.dry_run:
                with Session(engine) as session:
                    state = read_state(session, plan)
                    decision = decide(plan, state)
                    session.rollback()
                report = dry_run_report(plan, decision)
                _write_output(report, args.output)
                print(json.dumps(_printable(report), indent=2, default=str))
                return EXIT_BLOCKED if report["blocked"] else 0
            if args.command == "apply":
                if args.actor_id is None:
                    raise ConfigurationError("--actor-id is required for apply")
                with Session(engine) as session, session.begin():
                    report = apply(session, plan=plan, actor_id=args.actor_id, occurred_at=now)
            elif args.command == "upload":
                with Session(engine) as session:
                    report = upload_assets(session, plan, package_data, storage_env_file=_storage_env_file(args), dry_run=args.dry_run)
                    session.rollback()
            elif args.command == "assert-standard":
                with Session(engine) as session, session.begin():
                    report = assert_comparisons(
                        session, plan=plan, comparisons=package_data["comparisons"], actor_id=args.actor_id,
                        occurred_at=now, dry_run=args.dry_run,
                    )
                    if args.dry_run:
                        session.rollback()
            elif args.command == "gate":
                with Session(engine) as session, session.begin():
                    report = evaluate_gate(session, plan=plan, occurred_at=now, actor_id=args.actor_id)
            else:
                try:
                    with Session(engine) as session, session.begin():
                        report = publish(
                            session, plan=plan, actor_id=args.actor_id, occurred_at=now,
                            storage_env_file=_storage_env_file(args), dry_run=args.dry_run,
                            permitted_only=args.permitted_only,
                        )
                        if args.dry_run:
                            session.rollback()
                except PublicationRefused as refused:
                    print(json.dumps({"mode": "publish", "refused": str(refused), **refused.report}, indent=2), file=sys.stderr)
                    print("Commons publication refused; nothing was published.", file=sys.stderr)
                    return 1
            _write_output(report, args.output)
            print(json.dumps(_printable(report), indent=2, default=str))
            return 0
        finally:
            engine.dispose()
    except Blocked as blocked:
        print("Commons import blocked; nothing was written:", file=sys.stderr)
        for reason in blocked.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return EXIT_BLOCKED
    except (ConfigurationError, CommonsPlanError, ValueError, LookupError) as exc:
        print(f"Commons import failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("Commons import failed; nothing was recorded. Check the actor id, the package and database readiness.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
