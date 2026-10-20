"""Import the QElectroTech elements (phase 1): `apply`, `upload`, `gate`, `publish`, `verify`.

`services/qet_ingestion` decides what is recorded; this writes it, the way
`commons_ingest` writes the Wikimedia Commons symbols, and it reuses that
importer's helpers and order of work. A trusted operator interface that owns
its transaction.

* Reads the package and never changes it. The SVGs are converted from the
  package's .elmt files at plan time; nothing converted is kept on disk.
* `apply --dry-run` makes the same reads and decisions as `apply` and writes
  nothing; it exits 2 if the real run would refuse.
* Keyed on the element's QElectroTech UUID (`qet-<uuid>`). Re-running `apply`
  changes nothing; a changed SVG, name, concept or author makes a new revision.

**What it cannot do.** The rights record is proposed, not approved: approving
it is a named person's decision in Semantic Review (`#/semantic-review`).
Until then the publication gate refuses every symbol with `rights_undecided`.

Order: `apply --dry-run`, `apply`, `upload`, approve the rights record,
`gate`, `publish`, `verify`. See docs/qet-import-runbook.md.
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

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .catalog_facets import compute_catalog_facets
from .classification_assignments import (
    propose_symbol_revision_classification,
    transition_symbol_revision_classification,
)
from .classification_schemes import get_classification_scheme
from .commons_ingest import (
    EXIT_BLOCKED,
    Blocked,
    _engine,
    _latest_revisions,
    _live_rights,
    _pending_attachments,
    _printable,
    _propose_and_verify,
    _storage_env_file,
    _stored_object_problems,
    _write_output,
    ensure_concepts,
    ensure_standards,
    evaluate_gate,
    invalidate_facets,
)
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
    SemanticConceptRevision,
    StandardVersion,
    SymbolRevision,
)
from .publication_gate import describe_refusal, enforce_publication_gate
from .rights_provenance import propose_rights_record, record_asset_transformation
from .runtime import download_object_bytes, publish_revision_to_pack, upload_object_bytes
from .services import qet_ingestion as plan_module
from .services.qet_ingestion import (
    CATEGORY_SCHEME_CODE,
    CLASSIFICATION_RULE_METHOD,
    DISCIPLINE_SCHEME_CODE,
    LINK_VERIFICATION_METHOD,
    PACKAGE_CODE,
    PUBLICATION_PACK_CODE,
    REPRESENTATION_TYPE_NODE_CODE,
    REPRESENTATION_TYPE_SCHEME_CODE,
    REVISION_LIFECYCLE_STATE,
    SEMANTIC_METHOD,
    QetPlanError,
)
from .source_package_acquisition import add_source_package_entry, get_source_package, register_source_package
from .standard_sources import assert_symbol_standard_link, get_standard, transition_symbol_standard_link
from .symbol_semantic_assignments import (
    propose_symbol_semantic_assignment,
    transition_symbol_semantic_assignment,
)


# --------------------------------------------------------------------------
# The package on disk.
# --------------------------------------------------------------------------


def load_package(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    if not root.is_dir():
        raise ConfigurationError(f"package directory not found at {root}")
    manifest_path = root / "manifest.json"
    worksheet_path = root / "curation" / "worksheet.csv"
    elmt_dir = root / "elmt"
    for path in (manifest_path, worksheet_path, elmt_dir):
        if not path.exists():
            raise ConfigurationError(f"{path.relative_to(root)} not found in {root}")
    with worksheet_path.open(encoding="utf-8", newline="") as handle:
        worksheet = list(csv.DictReader(handle))
    return {
        "root": str(root),
        "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
        "worksheet": worksheet,
        "elmt": {f"elmt/{path.name}": path.read_bytes() for path in sorted(elmt_dir.glob("*.elmt"))},
    }


def final_bytes_by_sha(package_data: dict[str, Any]) -> dict[str, bytes]:
    return {
        result["sha256"]: result["bytes"]
        for result in plan_module.conversions(package_data).values()
        if "error" not in result
    }


# --------------------------------------------------------------------------
# Reading and deciding.
# --------------------------------------------------------------------------


def read_state(session: Session, plan: dict[str, Any]) -> dict[str, Any]:
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
    drop_slugs = [plan_module.slug_for(item["qet_uuid"]) for item in plan["drops"] if item.get("qet_uuid")]
    state["present_but_not_planned"] = sorted(
        session.execute(select(GovernedSymbol.slug).where(GovernedSymbol.slug.in_(drop_slugs))).scalars().all()
    )
    return state


def decide(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The actions `apply` would take, and every reason it would refuse. Pure."""
    blocking: list[str] = list(plan["package_problems"])
    if not plan["symbols"]:
        blocking.append("the package plans no symbols")
    matching = plan_module.match_concepts(plan["concepts"], state["concepts"], state.get("own_concepts", set()))

    missing: dict[str, set[str]] = {}
    for symbol in plan["symbols"]:
        for scheme_code, label in ((DISCIPLINE_SCHEME_CODE, symbol["discipline"]), (CATEGORY_SCHEME_CODE, symbol["category"])):
            if label not in state["catalogue"][scheme_code]["nodes"]:
                missing.setdefault(scheme_code, set()).add(label)
    for scheme_code, labels in sorted(missing.items()):
        blocking.append(f"{scheme_code} has no node labelled: {', '.join(sorted(labels))}")
    if REPRESENTATION_TYPE_NODE_CODE not in state["catalogue"][REPRESENTATION_TYPE_SCHEME_CODE]["nodes"]:
        blocking.append(f"{REPRESENTATION_TYPE_SCHEME_CODE} / {REPRESENTATION_TYPE_NODE_CODE} is not in this database")
    if state["present_but_not_planned"]:
        blocking.append("dropped elements are already recorded as symbols: " + ", ".join(state["present_but_not_planned"]))

    standards = [
        {
            "standard_code": definition["standard_code"],
            "exists": definition["standard_code"] in state["standards"],
            "versions": {
                label: ("existing" if (definition["standard_code"], label) in state["editions"] else "would_register")
                for label in definition["versions"]
            },
        }
        for definition in plan["standards"]
    ]

    created, revised, unchanged = [], [], []
    for symbol in plan["symbols"]:
        held = state["symbols"].get(symbol["slug"])
        if held is None:
            created.append(symbol["slug"])
            continue
        reasons = plan_module.revision_needs_bump(held["latest"].payload_json if held["latest"] else None, symbol)
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
    return {
        "mode": "dry-run",
        "blocked": bool(decision["blocking"]),
        "blocking": decision["blocking"],
        "package": plan["summary"],
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
        },
        "standards": decision["standards"],
        "concepts": {
            "distinct_in_package": len(plan["concepts"]),
            "reuse_existing": len(concepts["reuse"]),
            "would_create": len(concepts["create"]),
            "near_misses": concepts["near_misses"],
            "reused": [
                {"concept_key": c["concept_key"], "concept_code": c["concept_code"], "by_this_import": c["created_by_this_import"]}
                for c in concepts["reuse"]
            ],
        },
    }


# --------------------------------------------------------------------------
# Writing.
# --------------------------------------------------------------------------


def ensure_package_and_rights(session: Session, plan: dict[str, Any], state: dict[str, Any], *, actor_id, occurred_at):
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


def create_revision(session, symbol_plan, symbol, *, revision_label, package, state, resolved, actor_id, occurred_at):
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
            id=uuid.uuid4(), parent_type="symbol_revision", parent_id=revision.id, filename=asset["filename"],
            object_key=asset["object_key"], content_type=asset["content_type"], size_bytes=asset["size_bytes"],
            sha256=asset["sha256"], created_at=occurred_at,
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

    conversion = symbol_plan["conversion"]
    record_asset_transformation(
        session,
        symbol_revision_id=revision.id,
        tool_name=conversion["tool_name"],
        tool_version=conversion["tool_version"],
        source_asset_sha256=conversion["source_sha256"],
        derived_asset_sha256=conversion["derived_sha256"],
        performed_at=occurred_at,
        source_package_entry_id=entry.id,
        recorded_by_user_id=actor_id,
        evidence={
            "step": "converted .elmt to SVG",
            "removed_labels": conversion["removed_labels"],
            "removed_labels_basis": "product owner decision 2026-10-20: variant captions are not part of the symbol"
            if conversion["removed_labels"] else None,
            "qet_uuid": symbol_plan["qet_uuid"],
            "qet_path": symbol_plan["qet"]["path"],
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
            clause_reference=assertion["clause_reference"],
            source_uri=symbol_plan["qet"]["element_url"],
            notes=f"The QElectroTech element is drawn to {assertion['cited_as']}; drawn to it, not copied from it.",
            evidence={
                "basis": assertion["basis"],
                "cited_as": assertion["cited_as"],
                "edition": assertion["edition_basis"],
                "qet_path": symbol_plan["qet"]["path"],
            },
        )
        session.flush()
        transition_symbol_standard_link(
            session, link.id, target_status="verified", occurred_at=occurred_at, verification_method=LINK_VERIFICATION_METHOD
        )

    concept = resolved[symbol_plan["concept_key"]]
    _propose_and_verify(
        session, propose_symbol_semantic_assignment, transition_symbol_semantic_assignment, occurred_at=occurred_at,
        symbol_revision_id=revision.id, semantic_concept_id=concept["concept_id"], assignment_role="primary",
        method=SEMANTIC_METHOD, proposed_by_user_id=actor_id,
        evidence={"concept_key": symbol_plan["concept_key"], "source": "curation/worksheet.csv"},
    )
    catalogue = state["catalogue"]
    worksheet_evidence = {"source": "curation/worksheet.csv", "confirmed": "product owner", "qet_uuid": symbol_plan["qet_uuid"]}
    for node, evidence in (
        (catalogue[DISCIPLINE_SCHEME_CODE]["nodes"][symbol_plan["discipline"]],
         {**worksheet_evidence, "basis": "curated discipline", "value": symbol_plan["discipline"]}),
        (catalogue[CATEGORY_SCHEME_CODE]["nodes"][symbol_plan["category"]],
         {**worksheet_evidence, "basis": "curated category", "value": symbol_plan["category"]}),
        (catalogue[REPRESENTATION_TYPE_SCHEME_CODE]["nodes"][REPRESENTATION_TYPE_NODE_CODE],
         {"basis": "every symbol this package carries is an SVG vector drawing", "format": "svg"}),
    ):
        _propose_and_verify(
            session, propose_symbol_revision_classification, transition_symbol_revision_classification,
            occurred_at=occurred_at, symbol_revision_id=revision.id, classification_node_id=node.id,
            assignment_role="primary", method=CLASSIFICATION_RULE_METHOD, proposed_by_user_id=actor_id, evidence=evidence,
        )
    return revision


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
                id=uuid.uuid4(), slug=slug, canonical_name=symbol_plan["canonical_name"], category=symbol_plan["category"],
                discipline=symbol_plan["discipline"], owner_id=actor_id, owner_organization_id=None, visibility="public",
                organization_wide=False, current_revision_id=None, created_at=occurred_at, updated_at=occurred_at,
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
        "concepts_created": [{"concept_key": k, "concept_code": v["concept_code"]} for k, v in resolved.items() if v["created"]],
        "concepts_reused": [{"concept_key": k, "concept_code": v["concept_code"]} for k, v in resolved.items() if not v["created"]],
        "near_misses": decision["concepts"]["near_misses"],
        "standards": decision["standards"],
        "symbols_created": created,
        "symbols_revised": revised,
        "symbols_unchanged": len(unchanged),
    }


# --------------------------------------------------------------------------
# Storage, the gate and publication.
# --------------------------------------------------------------------------


def upload_assets(session, plan, package_data, *, storage_env_file, dry_run: bool, uploader=None) -> dict[str, Any]:
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


def publish(session, *, plan, actor_id, occurred_at, storage_env_file, dry_run: bool, fetcher=None) -> dict[str, Any]:
    """Publish every pending revision to the public pack, or none of them.

    Every phase 1 symbol carries a verified EN 60617 link, so there is no
    `--permitted-only`: any refusal publishes nothing.
    """
    _require_actor(session, actor_id)
    fetcher = fetcher or download_object_bytes
    sort_orders = {symbol["slug"]: symbol["sort_order"] for symbol in plan["symbols"]}
    rows = _latest_revisions(session, plan)
    if len(rows) != len(plan["symbols"]):
        raise ConfigurationError(f"{len(plan['symbols']) - len(rows)} planned symbols are not recorded; run apply first")
    unchanged = [symbol.slug for symbol, revision in rows if revision.lifecycle_state == "published"]
    pending = [(symbol, revision) for symbol, revision in rows if revision.lifecycle_state != "published"]
    wrong_state = [f"{s.slug}: {r.lifecycle_state}" for s, r in pending if r.lifecycle_state != REVISION_LIFECYCLE_STATE]
    if wrong_state:
        raise PublicationRefused(f"{len(wrong_state)} revisions are not approved", {"not_approved": wrong_state})
    if not pending:
        return {"mode": "publish", "published_count": 0, "published": [], "unchanged_count": len(unchanged)}

    storage_problems = _stored_object_problems(pending, session, storage_env_file=storage_env_file, fetcher=fetcher)
    if storage_problems:
        raise PublicationRefused(
            f"{len(storage_problems)} stored objects are missing or wrong; run upload first", {"storage_problems": storage_problems}
        )

    refusals: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    levels: dict[str, int] = {}
    for symbol, revision in pending:
        decision = enforce_publication_gate(session, symbol_revision_id=revision.id, evaluated_at=occurred_at, evaluated_by_user_id=actor_id)
        if decision.permitted:
            levels[decision.traceability_level] = levels.get(decision.traceability_level, 0) + 1
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
        return {"mode": "publish-dry-run", "would_publish": len(pending), "unchanged_count": len(unchanged), "traceability_levels": levels}

    pack = session.execute(select(PublicationPack).where(PublicationPack.pack_code == PUBLICATION_PACK_CODE)).scalar_one_or_none()
    if pack is None:
        pack = PublicationPack(
            id=uuid.uuid4(), pack_code=PUBLICATION_PACK_CODE, title=plan["publication"]["pack_title"], audience="public",
            effective_date=occurred_at.date(), status="published", created_at=occurred_at, updated_at=occurred_at,
        )
        session.add(pack)
    else:
        pack.status = "published"
        pack.updated_at = occurred_at
    session.flush()
    job = PublicationJob(
        id=uuid.uuid4(), pack_id=pack.id, status="completed", requested_by=actor_id, approved_by=actor_id,
        artifact_manifest_json={
            "source": "qet_ingest publish", "source_package_code": PACKAGE_CODE,
            "symbol_count": len(pending), "simulation": False,
        },
        created_at=occurred_at, completed_at=occurred_at,
    )
    session.add(job)
    session.flush()

    published = []
    for symbol, revision in pending:
        previous_id = symbol.current_revision_id if symbol.current_revision_id != revision.id else None
        page, entry = publish_revision_to_pack(
            session, symbol=symbol, revision=revision, publication_pack=pack, sort_order=sort_orders[symbol.slug],
            effective_date=pack.effective_date, published_at=occurred_at,
        )
        if previous_id is not None:
            _retire_superseded(session, symbol, previous_id, page.id, actor_id=actor_id, occurred_at=occurred_at)
        published.append({
            "slug": symbol.slug, "catalog_symbol_id": symbol.catalog_symbol_id, "page_code": page.page_code,
            "published_page_id": str(page.id), "pack_entry_id": str(entry.id), "revision": revision.revision_label,
            "symbol_revision_id": str(revision.id),
        })
    audit_payload = {
        "publication_job_id": str(job.id), "pack_code": pack.pack_code, "source_package_code": PACKAGE_CODE,
        "published_count": len(published), "approval_actor": {"id": str(actor_id), "type": "user"},
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
        "mode": "publish", "publication_pack_code": pack.pack_code, "publication_job_id": str(job.id),
        "published_count": len(published), "published": published, "unchanged_count": len(unchanged),
        "traceability_levels": levels, "facet_rows_computed": facets,
    }


# --------------------------------------------------------------------------
# The command.
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("apply", "Record the package: source package, rights, standard, concepts, symbols"),
        ("upload", "Put every pending SVG in object storage (reads the database)"),
        ("gate", "Evaluate the publication gate over the pending revisions"),
        ("publish", "Publish every pending revision to the public pack, or none"),
        ("verify", "Run the acceptance checks against the database (reads only)"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--package", required=True, help="The import package directory")
        command.add_argument("--output", help="Write the full report here as JSON")
        if name in {"apply", "upload", "publish"}:
            command.add_argument("--dry-run", action="store_true", help="Read and decide; write nothing")
        if name in {"upload", "publish", "verify"}:
            command.add_argument("--storage-env-file", help="Object storage settings (default: the API's)")
        if name in {"apply", "publish"}:
            command.add_argument("--actor-id", type=uuid.UUID, required=(name == "publish"), help="Existing user UUID")
        if name == "gate":
            command.add_argument("--actor-id", type=uuid.UUID, help="Optional: the operator the evaluation is recorded against")
        if name == "verify":
            command.add_argument("--no-storage", action="store_true", help="Skip the checks that read stored SVGs")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        package_data = load_package(args.package)
        plan = plan_module.plan_import(package_data)
        engine = _engine()
        try:
            now = datetime.now(timezone.utc)
            if args.command == "verify":
                from .qet_verify import verify

                with Session(engine) as session:
                    report = verify(
                        session, plan, package_data,
                        fetcher=None if args.no_storage else (lambda **kwargs: download_object_bytes(**kwargs)),
                        storage_env_file=None if args.no_storage else _storage_env_file(args),
                    )
                    session.rollback()
                _write_output(report, args.output)
                print(json.dumps({"passed": report["passed"], "failed": report["failed"],
                                  "checks": [{"id": c["id"], "status": c["status"]} for c in report["checks"]]}, indent=2))
                return 0 if report["passed"] else 1
            if args.command == "apply" and args.dry_run:
                with Session(engine) as session:
                    decision = decide(plan, read_state(session, plan))
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
            elif args.command == "gate":
                with Session(engine) as session, session.begin():
                    report = evaluate_gate(session, plan=plan, occurred_at=now, actor_id=args.actor_id)
            else:
                try:
                    with Session(engine) as session, session.begin():
                        report = publish(
                            session, plan=plan, actor_id=args.actor_id, occurred_at=now,
                            storage_env_file=_storage_env_file(args), dry_run=args.dry_run,
                        )
                        if args.dry_run:
                            session.rollback()
                except PublicationRefused as refused:
                    print(json.dumps({"mode": "publish", "refused": str(refused), **refused.report}, indent=2), file=sys.stderr)
                    print("QElectroTech publication refused; nothing was published.", file=sys.stderr)
                    return 1
            _write_output(report, args.output)
            print(json.dumps(_printable(report), indent=2, default=str))
            return 0
        finally:
            engine.dispose()
    except Blocked as blocked:
        print("QElectroTech import blocked; nothing was written:", file=sys.stderr)
        for reason in blocked.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return EXIT_BLOCKED
    except (ConfigurationError, QetPlanError, ValueError, LookupError) as exc:
        print(f"QElectroTech import failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print("QElectroTech import failed; nothing was recorded. Check the actor id, the package and database readiness.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
