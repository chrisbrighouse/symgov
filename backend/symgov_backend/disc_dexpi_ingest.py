"""Import the DISC DEXPI symbol library: `apply`, `upload`, `gate` and `publish`.

`services/disc_dexpi_ingestion` decides what is recorded; this writes it, the
way `dexpi_ingest` writes the TrainingTestCases pilot. It reuses that module's
helpers and its order of work, and like it is a trusted operator interface that
owns its transaction.

**What it adds to the pilot's pattern.**

* `--dry-run` on `apply`, `upload` and `publish`: the same reads and the same
  decisions, no writes. It reports counts, the concept matches and every reason
  the real run would refuse, and exits 2 if there is one.
* Keyed on the manifest's `source_key`. A symbol whose primary digest, option
  digests or geometry signature changed gets a new revision; an unchanged one
  is left exactly as it is.
* The attribution text is read from the package once, stored on the rights
  record and nowhere else (see `services/disc_dexpi_ingestion`).

**What it cannot do.** The rights record is proposed, not approved: approving
it is a named person's decision in Semantic Review. Until then the publication
gate refuses every symbol with `rights_undecided`, which is the gate working.

Order: `apply` (database), `upload` (object storage, reads the database),
approve the rights record, `gate`, `publish`.
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
from typing import Any

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session

from .classification_assignments import (
    propose_concept_classification,
    propose_symbol_revision_classification,
    transition_concept_classification,
    transition_symbol_revision_classification,
)
from .classification_backfill import BackfillReport, backfill_one_target, select_backfill_targets
from .classification_schemes import (
    add_classification_node,
    get_classification_scheme,
    register_classification_scheme,
)
from .concept_external_references import (
    propose_concept_external_reference,
    transition_concept_external_reference,
)
from .dexpi_ingest import (
    ConfigurationError,
    PublicationRefused,
    _require_actor,
)
from .dexpi_seed import index_existing as index_existing_concepts
from .external_semantic_schemes import (
    register_external_scheme_version,
    register_external_semantic_scheme,
)
from .image_content import UnsafeImageContentError, validate_stored_image
from .models import (
    Attachment,
    AuditEvent,
    ClassificationNode,
    ConceptClassificationAssignment,
    ConceptExternalReference,
    ExternalSemanticScheme,
    ExternalSemanticSchemeVersion,
    GovernedSymbol,
    PackEntry,
    PublicationJob,
    PublicationPack,
    PublishedPage,
    RightsRecord,
    SourcePackage,
    StandardVersion,
    SymbolRevision,
    SymbolRevisionClassificationAssignment,
)
from .publication_gate import describe_refusal, enforce_publication_gate
from .rights_provenance import propose_rights_record, record_asset_transformation
from .runtime import download_object_bytes, publish_revision_to_pack, upload_object_bytes
from .semantic_concepts import create_semantic_concept, transition_semantic_concept_revision
from .services import disc_dexpi_ingestion as plan_module
from .services.disc_dexpi_ingestion import (
    CATEGORY_SCHEME_CODE,
    CLASSIFICATION_RULE_METHOD,
    DISCIPLINE_SCHEME_CODE,
    PACKAGE_CODE,
    PUBLICATION_PACK_CODE,
    REPRESENTATION_TYPE_NODE_CODE,
    REPRESENTATION_TYPE_SCHEME_CODE,
    REVISION_LIFECYCLE_STATE,
    SCHEME_CODE,
    SEMANTIC_METHOD,
    SOURCE_MAPPING_METHOD,
    DiscPlanError,
)
from .source_package_acquisition import (
    add_source_package_entry,
    get_source_package,
    register_source_package,
)
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

CONVERTER_NAME = "convert_disc.py"
LINK_VERIFICATION_METHOD = "import_manifest"
_CONCEPT_PUBLISH_SEQUENCE = ("review", "approved", "published")
_LIVE_ASSIGNMENT_STATES = ("proposed", "verified")
EXIT_BLOCKED = 2


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

    def read(name: str):
        path = root / name
        if not path.is_file():
            raise ConfigurationError(f"{name} not found in {root}")
        return json.loads(path.read_text(encoding="utf-8"))

    svg_dir = root / "svg"
    if not svg_dir.is_dir():
        raise ConfigurationError(f"svg/ not found in {root}")
    held_path = root / "held_back.csv"
    if not held_path.is_file():
        raise ConfigurationError(f"held_back.csv not found in {root}")
    with held_path.open(encoding="utf-8", newline="") as handle:
        held_back = list(csv.DictReader(handle))
    return {
        "manifest": read("manifest.json"),
        "scheme": read("dexpi_class_scheme.json"),
        "report": read("report.json"),
        "config": read("config.json"),
        "held_back": held_back,
        "svg": {path.name: path.read_bytes() for path in sorted(svg_dir.glob("*.svg"))},
        "converter_version": _converter_version(root),
    }


def _converter_version(root: Path) -> str:
    script = root / "convert_disc.py"
    if script.is_file():
        return "sha256:" + hashlib.sha256(script.read_bytes()).hexdigest()[:16]
    return "unknown"


# --------------------------------------------------------------------------
# Reading what is already recorded.
# --------------------------------------------------------------------------


def _live_rights(session: Session, package_id: uuid.UUID) -> RightsRecord | None:
    rows = session.execute(
        select(RightsRecord)
        .where(
            RightsRecord.source_package_id == package_id,
            RightsRecord.decision_status.in_(("proposed", "approved")),
        )
        .order_by(RightsRecord.created_at.desc())
    ).scalars().all()
    return rows[0] if rows else None


def _pilot_targets(session: Session) -> list[dict[str, Any]]:
    """The TrainingTestCases revisions, and the DEXPI class each is classified against."""
    rows = session.execute(
        select(GovernedSymbol.slug, SymbolRevision.id, SymbolRevision.payload_json)
        .join(SymbolRevision, SymbolRevision.id == GovernedSymbol.current_revision_id)
        .where(GovernedSymbol.slug.like("dexpi-ttc-%"))
        .order_by(GovernedSymbol.slug)
    ).all()
    return [
        {
            "slug": slug,
            "revision_id": revision_id,
            "target": plan_module.ttc_class_target(payload),
            "name_basis": ((payload or {}).get("dexpi") or {}).get("name_basis"),
        }
        for slug, revision_id, payload in rows
    ]


def read_state(session: Session, plan: dict[str, Any]) -> dict[str, Any]:
    """Everything the import's decisions depend on, read once."""
    state: dict[str, Any] = {}

    package = get_source_package(session, PACKAGE_CODE)
    state["package"] = package
    state["rights"] = _live_rights(session, package.id) if package is not None else None

    # Symbols and their revisions.
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

    # The DEXPI-CLASS scheme and its nodes.
    scheme = get_classification_scheme(session, SCHEME_CODE)
    state["scheme"] = scheme
    state["nodes"] = {}
    if scheme is not None:
        for node in session.execute(
            select(ClassificationNode).where(ClassificationNode.scheme_id == scheme.id)
        ).scalars():
            state["nodes"][node.node_code] = node

    # Pilot symbols and the class each needs.
    state["pilot"] = _pilot_targets(session)

    # Concepts.
    state["concepts"] = index_existing_concepts(session)

    # The three catalogue schemes the manifest assigns by label.
    catalogue: dict[str, dict[str, ClassificationNode]] = {}
    for code in (DISCIPLINE_SCHEME_CODE, CATEGORY_SCHEME_CODE, REPRESENTATION_TYPE_SCHEME_CODE):
        catalogue_scheme = get_classification_scheme(session, code)
        nodes = {}
        if catalogue_scheme is not None:
            for node in session.execute(
                select(ClassificationNode).where(ClassificationNode.scheme_id == catalogue_scheme.id)
            ).scalars():
                nodes[node.preferred_label] = node
                nodes[node.node_code] = node
        catalogue[code] = {"scheme": catalogue_scheme, "nodes": nodes}
    state["catalogue"] = catalogue

    # External schemes, versions and mappings.
    external: dict[str, dict[str, Any]] = {}
    version_label = plan["external"]["version_label"]
    for system, definition in plan["external"]["systems"].items():
        scheme_row = session.execute(
            select(ExternalSemanticScheme).where(ExternalSemanticScheme.scheme_code == definition["scheme_code"])
        ).scalar_one_or_none()
        version_row = None
        if scheme_row is not None:
            version_row = session.execute(
                select(ExternalSemanticSchemeVersion).where(
                    ExternalSemanticSchemeVersion.scheme_id == scheme_row.id,
                    ExternalSemanticSchemeVersion.version_label == version_label,
                )
            ).scalar_one_or_none()
        external[system] = {"scheme": scheme_row, "version": version_row}
    state["external"] = external
    existing_mappings: set[tuple] = set()
    for reference in session.execute(
        select(ConceptExternalReference).where(
            ConceptExternalReference.mapping_status.in_(_LIVE_ASSIGNMENT_STATES)
        )
    ).scalars():
        existing_mappings.add(
            (reference.semantic_concept_id, reference.scheme_version_id, reference.external_identifier, reference.mapping_type)
        )
    state["mappings"] = existing_mappings

    # Concept -> DEXPI-CLASS assignments already there.
    state["concept_nodes"] = set()
    if scheme is not None:
        for row in session.execute(
            select(ConceptClassificationAssignment.semantic_concept_id).where(
                ConceptClassificationAssignment.classification_scheme_id == scheme.id,
                ConceptClassificationAssignment.status.in_(_LIVE_ASSIGNMENT_STATES),
            )
        ):
            state["concept_nodes"].add(row[0])

    # Standards.
    editions: dict[tuple[str, str], uuid.UUID] = {}
    for definition in plan["standards"]:
        standard = get_standard(session, definition["standard_code"])
        if standard is not None:
            for version in session.execute(
                select(StandardVersion).where(StandardVersion.standard_id == standard.id)
            ).scalars():
                editions[(standard.standard_code, version.version_label)] = version.id
    state["editions"] = editions
    return state


# --------------------------------------------------------------------------
# Deciding what to do.
# --------------------------------------------------------------------------


def decide(plan: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The actions `apply` would take, and every reason it would refuse.

    A pure function of the plan and the state read, so the dry run and the real
    run cannot disagree about what is to be done.
    """
    blocking: list[str] = list(plan["package_problems"])

    # Concepts.
    matching = plan_module.match_concepts(plan["concepts"], state["concepts"])
    if matching["expected_but_unmatched"]:
        blocking.append(
            "these classes are expected to match a pilot concept and do not: "
            + ", ".join(matching["expected_but_unmatched"])
        )
    # A concept that matches an existing one exactly but is not among the 16 is
    # still the same concept, so it is reused, not blocked. It is reported
    # (`extra_matches`) because the brief's list of 16 did not name it.
    for near in matching["near_misses"]:
        blocking.append(
            f"concept {near['concept_key']} nearly matches existing {', '.join(near['existing_names'])}; "
            "creating it would duplicate a concept"
        )

    # Mappings.
    for conflict in plan["external"]["conflicts"]:
        blocking.append(
            f"concept {conflict['concept_key']} has several exact {conflict['system']} identifiers: "
            + ", ".join(conflict["identifiers"])
        )
    blocking.extend(plan["external"]["problems"])

    # Catalogue nodes the manifest names by label.
    missing_labels: dict[str, set[str]] = {}
    for symbol in plan["symbols"]:
        for scheme_code, label in (
            (DISCIPLINE_SCHEME_CODE, symbol["discipline_node_label"]),
            (CATEGORY_SCHEME_CODE, symbol["category_node_label"]),
        ):
            entry = state["catalogue"][scheme_code]
            if entry["scheme"] is None or label not in entry["nodes"]:
                missing_labels.setdefault(scheme_code, set()).add(label)
    for scheme_code, labels in sorted(missing_labels.items()):
        blocking.append(f"{scheme_code} has no node labelled: {', '.join(sorted(labels))}")
    representation = state["catalogue"][REPRESENTATION_TYPE_SCHEME_CODE]
    if representation["scheme"] is None or REPRESENTATION_TYPE_NODE_CODE not in representation["nodes"]:
        blocking.append(f"{REPRESENTATION_TYPE_SCHEME_CODE} / {REPRESENTATION_TYPE_NODE_CODE} is not in this database")

    # DEXPI-CLASS nodes.
    existing_codes = set(state["nodes"])
    nodes_to_create = [node for node in plan["scheme"]["nodes"] if node["node_code"] not in existing_codes]

    # The pilot's classes missing from the scheme.
    pilot_targets = [row for row in state["pilot"] if row["target"]]
    pilot_without_target = [row["slug"] for row in state["pilot"] if not row["target"]]
    known_names = {node["dexpi_name"] for node in plan["scheme"]["nodes"] if node["kind"] == "dexpi_class"}
    missing_pilot_names = sorted({row["target"] for row in pilot_targets} - known_names)
    pilot_nodes_to_create = []
    pilot_matched_to_node = []
    scheme_codes = {n["node_code"]: n for n in plan["scheme"]["nodes"] if n["kind"] != "package"}
    for name in missing_pilot_names:
        node = plan_module.ttc_missing_node(name)
        shared = scheme_codes.get(node["node_code"])
        if shared is not None:
            # The pilot's class is spelled exactly as a DISC custom type, so it
            # is that node, not a second one.
            pilot_matched_to_node.append({"pilot_class": name, "node_code": shared["node_code"], "node_kind": shared["kind"]})
            continue
        if node["node_code"] not in existing_codes:
            pilot_nodes_to_create.append(node)
    if not state["pilot"]:
        # Not a reason to stop: a database without the pilot has nothing to classify.
        pass

    # Mappings.
    mapping_actions = {"create": 0, "existing": 0, "by_system": {}}
    reuse_by_key = {concept["concept_key"]: concept for concept in matching["reuse"]}
    for mapping in plan["external"]["mappings"]:
        system_counts = mapping_actions["by_system"].setdefault(mapping["system"], {"create": 0, "existing": 0})
        concept = reuse_by_key.get(mapping["concept_key"])
        version = state["external"][mapping["system"]]["version"]
        if concept is not None and version is not None:
            key = (uuid.UUID(concept["concept_id"]), version.id, mapping["identifier"], mapping["relation"])
            if key in state["mappings"]:
                mapping_actions["existing"] += 1
                system_counts["existing"] += 1
                continue
        mapping_actions["create"] += 1
        system_counts["create"] += 1

    # Symbols.
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
        "nodes_to_create": nodes_to_create,
        "pilot_nodes_to_create": pilot_nodes_to_create,
        "pilot_matched_to_node": pilot_matched_to_node,
        "pilot_total": len(state["pilot"]),
        "pilot_without_target": pilot_without_target,
        "mappings": mapping_actions,
        "symbols": {"create": created, "revise": revised, "unchanged": unchanged},
        "package_exists": state["package"] is not None,
        "rights_exists": state["rights"] is not None,
        "scheme_exists": state["scheme"] is not None,
    }


def dry_run_report(
    plan: dict[str, Any], state: dict[str, Any], decision: dict[str, Any], pilot_facets: dict[str, Any] | None = None
) -> dict[str, Any]:
    concepts = decision["concepts"]
    return {
        "mode": "dry-run",
        "blocked": bool(decision["blocking"]),
        "blocking": decision["blocking"],
        "package": {
            "symbols": plan["summary"]["symbols"],
            "option_assets": plan["summary"]["option_assets"],
            "with_connection_points": plan["summary"]["with_connection_points"],
            "with_label_slots": plan["summary"]["with_label_slots"],
            "categories": plan["summary"]["categories"],
            "disciplines": plan["summary"]["disciplines"],
            "warnings": plan["summary"]["warnings"],
        },
        "symbols": {
            "would_create": len(decision["symbols"]["create"]),
            "would_revise": decision["symbols"]["revise"],
            "unchanged": len(decision["symbols"]["unchanged"]),
        },
        "source_package": {"exists": decision["package_exists"], "code": PACKAGE_CODE},
        "rights_record": {"exists": decision["rights_exists"], "will_be": "proposed (licensed / distribute)"},
        "scheme": {
            "code": SCHEME_CODE,
            "exists": decision["scheme_exists"],
            "nodes_to_create": len(decision["nodes_to_create"]),
            "nodes_total": len(plan["scheme"]["nodes"]),
            "by_kind": plan["scheme"]["counts"],
            "node_code_map": plan["scheme"]["code_map"],
            "pilot_symbols": decision["pilot_total"],
            "pilot_classes_added": [node["dexpi_name"] for node in decision["pilot_nodes_to_create"]],
            "pilot_classes_matched_to_existing_node": decision["pilot_matched_to_node"],
        },
        "concepts": {
            "distinct_in_package": len(plan["concepts"]),
            "reuse_existing": len(concepts["reuse"]),
            "would_create": len(concepts["create"]),
            "near_misses": concepts["near_misses"],
            "overlap": concepts["overlap"],
            "extra_matches": [
                {
                    "concept_key": key,
                    "pilot_concept_code": next(c["concept_code"] for c in concepts["reuse"] if c["concept_key"] == key),
                }
                for key in concepts["matched_but_unexpected"]
            ],
        },
        "external_mappings": {
            "distinct": len(plan["external"]["mappings"]),
            "would_create": decision["mappings"]["create"],
            "existing": decision["mappings"]["existing"],
            "by_system": decision["mappings"]["by_system"],
            "exact_conflicts": plan["external"]["conflicts"],
        },
        "pilot_governed_facets": pilot_facets,
        "display_name_corrections": [
            {"source_key": key, "changes": [list(pair) for pair in pairs]}
            for key, pairs in plan_module.DISPLAY_NAME_CORRECTIONS.items()
        ],
    }


# --------------------------------------------------------------------------
# Writing.
# --------------------------------------------------------------------------


def _activate_node(node: ClassificationNode, *, occurred_at: datetime) -> None:
    node.status = "active"
    node.updated_at = occurred_at


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
            decision_reason=(
                "A specific permission granted to Symgov for this library, not an open licence; "
                "the attribution text it is conditional on is held in this record's evidence."
            ),
            evidence=definition["evidence"],
        )
        session.flush()
        state["rights"] = rights
    return package, rights


def ensure_standards(session: Session, plan: dict[str, Any], state: dict[str, Any], *, occurred_at: datetime) -> None:
    for definition in plan["standards"]:
        standard = get_standard(session, definition["standard_code"])
        if standard is None:
            standard = register_standard(
                session,
                standard_code=definition["standard_code"],
                title=definition["title"],
                issuing_body=definition["issuing_body"],
                registered_at=occurred_at,
            )
            session.flush()
        for version_label in definition["versions"]:
            key = (standard.standard_code, version_label)
            if key in state["editions"]:
                continue
            version = register_standard_version(
                session, standard_id=standard.id, version_label=version_label, registered_at=occurred_at
            )
            session.flush()
            state["editions"][key] = version.id


def ensure_scheme_and_nodes(
    session: Session, plan: dict[str, Any], decision: dict[str, Any], state: dict[str, Any], *, occurred_at: datetime
) -> dict[str, ClassificationNode]:
    """The DEXPI-CLASS scheme, every node of it, and the pilot classes it lacked."""
    scheme = state["scheme"]
    if scheme is None:
        definition = plan["scheme"]
        scheme = register_classification_scheme(
            session,
            scheme_code=definition["scheme_code"],
            name=definition["name"],
            version_label=definition["version_label"],
            description=(
                f"{definition['description']} Imported from the DISC DEXPI symbol library; "
                "read-only in Semantic Review."
            ),
            registered_at=occurred_at,
            status="active",
        )
        session.flush()
        state["scheme"] = scheme
    nodes: dict[str, ClassificationNode] = dict(state["nodes"])
    by_ref: dict[tuple[str, str], ClassificationNode] = {}

    ordered = [n for n in plan["scheme"]["nodes"] if n["kind"] == "package"]
    ordered += [n for n in plan["scheme"]["nodes"] if n["kind"] == "dexpi_class"]
    ordered += [n for n in plan["scheme"]["nodes"] if n["kind"] == "custom_type"]
    ordered += decision["pilot_nodes_to_create"]
    for order, planned in enumerate(ordered):
        code = planned["node_code"]
        if code in nodes:
            by_ref[(planned["kind"], planned["dexpi_name"])] = nodes[code]
            continue
        parent = None
        if planned["parent_dexpi_name"] is not None:
            parent = by_ref.get((planned["parent_kind"], planned["parent_dexpi_name"]))
            if parent is None:
                # A parent already recorded by an earlier run.
                parent_plan = next(
                    (
                        n
                        for n in plan["scheme"]["nodes"]
                        if n["kind"] == planned["parent_kind"] and n["dexpi_name"] == planned["parent_dexpi_name"]
                    ),
                    None,
                )
                parent = nodes.get(parent_plan["node_code"]) if parent_plan else None
            if parent is None:
                raise DiscPlanError(f"node {code} has no parent {planned['parent_dexpi_name']}")
        description = planned["description"]
        if planned["kind"] == "custom_type":
            description = f"{planned['friendly_label']}. A DISC custom type of DEXPI class {planned['parent_dexpi_name']}."
        node = add_classification_node(
            session,
            scheme_id=scheme.id,
            node_code=code,
            preferred_label=planned["label"],
            description=description,
            parent_node_id=parent.id if parent is not None else None,
            sort_order=(order + 1) * 10,
            status="draft",
            added_at=occurred_at,
        )
        session.flush()
        aliases = [planned["dexpi_name"]]
        if planned.get("friendly_label") and planned["friendly_label"] not in aliases:
            aliases.append(planned["friendly_label"])
        # Plain columns the ORM does not map (migration 20261007_0070).
        session.execute(
            text("UPDATE classification_nodes SET aliases_json = CAST(:aliases AS jsonb), source_label = :source WHERE id = :id"),
            {"aliases": json.dumps(aliases), "source": planned["source"], "id": node.id},
        )
        _activate_node(node, occurred_at=occurred_at)
        nodes[code] = node
        by_ref[(planned["kind"], planned["dexpi_name"])] = node
    session.flush()
    state["nodes"] = nodes
    return nodes


def node_for_reference(plan: dict[str, Any], nodes: dict[str, ClassificationNode], reference: str) -> ClassificationNode:
    planned = plan["nodes_by_ref"].get(reference)
    if planned is None:
        # A pilot class the DEXPI list lacks: either spelled as a DISC custom
        # type (the same node), or added under `Other` by this import.
        code = plan_module.snake_code(reference)
        planned = next((n for n in plan["scheme"]["nodes"] if n["kind"] != "package" and n["node_code"] == code), None)
    if planned is None:
        planned = plan_module.ttc_missing_node(reference)
    if planned["node_code"] not in nodes:
        raise DiscPlanError(f"no DEXPI-CLASS node for {reference!r}")
    return nodes[planned["node_code"]]


def ensure_concepts(
    session: Session,
    plan: dict[str, Any],
    decision: dict[str, Any],
    nodes: dict[str, ClassificationNode],
    *,
    actor_id: uuid.UUID,
    occurred_at: datetime,
    state: dict[str, Any],
) -> dict[str, dict[str, Any]]:
    """Create the missing concepts, and classify every concept the package uses."""
    resolved: dict[str, dict[str, Any]] = {}
    for concept in decision["concepts"]["reuse"]:
        resolved[concept["concept_key"]] = {
            "concept_id": uuid.UUID(concept["concept_id"]),
            "concept_code": concept["concept_code"],
            "created": False,
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
        resolved[planned["concept_key"]] = {
            "concept_id": concept.id,
            "concept_code": concept.concept_code,
            "created": True,
        }
    session.flush()

    # The concept sits under its DEXPI class (or Custom* class's custom type).
    for planned in plan["concepts"]:
        info = resolved[planned["concept_key"]]
        if info["concept_id"] in state["concept_nodes"] or not planned["dexpi_node_reference"]:
            continue
        node = node_for_reference(plan, nodes, planned["dexpi_node_reference"])
        assignment = propose_concept_classification(
            session,
            semantic_concept_id=info["concept_id"],
            classification_node_id=node.id,
            assignment_role="primary",
            method=SOURCE_MAPPING_METHOD,
            proposed_at=occurred_at,
            proposed_by_user_id=actor_id,
            evidence={"source": "DISC Symbols.xlsm", "symbols": planned["symbols"]},
        )
        session.flush()
        transition_concept_classification(session, assignment.id, target_status="verified", occurred_at=occurred_at)
        state["concept_nodes"].add(info["concept_id"])
    return resolved


def ensure_external_mappings(
    session: Session,
    plan: dict[str, Any],
    resolved: dict[str, dict[str, Any]],
    state: dict[str, Any],
    *,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> dict[str, int]:
    package_meta = plan["source_package"]["metadata"]
    counts = {"created": 0, "existing": 0}
    versions: dict[str, ExternalSemanticSchemeVersion] = {}
    for system, definition in plan["external"]["systems"].items():
        held = state["external"][system]
        scheme = held["scheme"]
        if scheme is None:
            scheme = register_external_semantic_scheme(
                session,
                scheme_code=definition["scheme_code"],
                title=definition["title"],
                issuing_body=definition["issuing_body"],
                base_uri=definition["base_uri"],
                registered_at=occurred_at,
            )
            session.flush()
        version = held["version"]
        if version is None:
            version = register_external_scheme_version(
                session,
                scheme_id=scheme.id,
                version_label=plan["external"]["version_label"],
                registered_at=occurred_at,
                source_uri=plan["source_package"]["source_uri"],
            )
            session.flush()
        versions[system] = version
    for mapping in plan["external"]["mappings"]:
        concept = resolved[mapping["concept_key"]]
        version = versions[mapping["system"]]
        key = (concept["concept_id"], version.id, mapping["identifier"], mapping["relation"])
        if key in state["mappings"]:
            counts["existing"] += 1
            continue
        reference = propose_concept_external_reference(
            session,
            semantic_concept_id=concept["concept_id"],
            scheme_version_id=version.id,
            external_identifier=mapping["identifier"],
            external_label=mapping["label"],
            mapping_type=mapping["relation"],
            mapping_method="imported",
            proposed_at=occurred_at,
            proposed_by_user_id=actor_id,
            evidence={
                "source": "DISC Symbols.xlsm",
                "source_repository": plan["source_package"]["source_uri"],
                "source_commit": package_meta["source_commit"],
                "symbols": mapping["symbols"],
            },
        )
        session.flush()
        transition_concept_external_reference(
            session,
            reference.id,
            target_status="verified",
            occurred_at=occurred_at,
            verification_basis="authoritative_source",
        )
        state["mappings"].add(key)
        counts["created"] += 1
    return counts


def _propose_and_verify(session: Session, propose, transition, *, occurred_at: datetime, **kwargs) -> None:
    assignment = propose(session, proposed_at=occurred_at, **kwargs)
    session.flush()
    transition(session, assignment.id, target_status="verified", occurred_at=occurred_at)


def create_revision(
    session: Session,
    symbol_plan: dict[str, Any],
    symbol: GovernedSymbol,
    *,
    revision_label: str,
    package: SourcePackage,
    state: dict[str, Any],
    nodes: dict[str, ClassificationNode],
    resolved: dict[str, dict[str, Any]],
    plan: dict[str, Any],
    converter_version: str,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> SymbolRevision:
    """One revision and the governed facts it carries, as the pilot records them."""
    payload = plan_module.build_revision_payload(
        symbol_plan["record"], revision_label=revision_label, attribution=symbol_plan["attribution"]
    )
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

    for asset in [payload["assets"][0], *payload["visual_assets"]["state_variants"]]:
        attachment = Attachment(
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
        session.add(attachment)
        session.flush()
        if asset["role"] == "option":
            # `asset_role` defaults to `primary`; the option columns are plain
            # columns the ORM does not map (migration 20261007_0070).
            session.execute(
                text(
                    "UPDATE attachments SET asset_role = 'option', option_index = :index, condition = :condition "
                    "WHERE id = :id"
                ),
                {"index": asset["option_index"], "condition": asset.get("condition"), "id": attachment.id},
            )

    entry = add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision.id,
        added_at=occurred_at,
        source_label=symbol_plan["entry"]["source_label"],
        provider_entry_identifier=symbol_plan["entry"]["provider_entry_identifier"],
        source_path=symbol_plan["entry"]["source_path"],
        sort_order=symbol_plan["sort_order"],
    )
    session.execute(
        text("UPDATE source_package_entries SET import_warnings_json = CAST(:warnings AS jsonb) WHERE id = :id"),
        {"warnings": json.dumps(list(symbol_plan["warnings"])), "id": entry.id},
    )
    session.flush()

    record_asset_transformation(
        session,
        symbol_revision_id=revision.id,
        tool_name=CONVERTER_NAME,
        tool_version=converter_version,
        derived_asset_sha256=symbol_plan["primary_sha256"],
        performed_at=occurred_at,
        source_package_entry_id=entry.id,
        recorded_by_user_id=actor_id,
        evidence={"format": "svg", "filename": payload["assets"][0]["filename"]},
    )

    assertion = symbol_plan["assertion"]
    link = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision.id,
        standard_version_id=state["editions"][(assertion["standard_code"], assertion["version_label"])],
        relationship_type=assertion["relationship_type"],
        asserted_at=occurred_at,
        source_symbol_identifier=assertion["source_symbol_identifier"],
        source_uri=plan["source_package"]["source_uri"],
        notes="Asserted from the DISC DEXPI symbol register in the import package.",
        evidence={"basis": assertion["basis"], "source_path": symbol_plan["entry"]["source_path"]},
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
        evidence={"concept_key": symbol_plan["concept_key"], "source": "DISC Symbols.xlsm"},
    )

    catalogue = state["catalogue"]
    classifications = [
        (
            nodes[node_for_reference(plan, nodes, symbol_plan["dexpi_node_reference"]).node_code],
            SOURCE_MAPPING_METHOD,
            symbol_plan["dexpi_class_evidence"],
        ),
        (
            catalogue[DISCIPLINE_SCHEME_CODE]["nodes"][symbol_plan["discipline_node_label"]],
            CLASSIFICATION_RULE_METHOD,
            symbol_plan["classification_evidence"][DISCIPLINE_SCHEME_CODE],
        ),
        (
            catalogue[CATEGORY_SCHEME_CODE]["nodes"][symbol_plan["category_node_label"]],
            CLASSIFICATION_RULE_METHOD,
            symbol_plan["classification_evidence"][CATEGORY_SCHEME_CODE],
        ),
        (
            catalogue[REPRESENTATION_TYPE_SCHEME_CODE]["nodes"][REPRESENTATION_TYPE_NODE_CODE],
            CLASSIFICATION_RULE_METHOD,
            {"basis": "every symbol this package carries is a converted vector drawing", "format": "svg"},
        ),
    ]
    for node, method, evidence in classifications:
        _propose_and_verify(
            session,
            propose_symbol_revision_classification,
            transition_symbol_revision_classification,
            occurred_at=occurred_at,
            symbol_revision_id=revision.id,
            classification_node_id=node.id,
            assignment_role="primary",
            method=method,
            proposed_by_user_id=actor_id,
            evidence=evidence,
        )
    return revision


def classify_pilot(
    session: Session,
    plan: dict[str, Any],
    state: dict[str, Any],
    nodes: dict[str, ClassificationNode],
    *,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> dict[str, int]:
    """Classify the TrainingTestCases symbols against DEXPI-CLASS, once each."""
    scheme = state["scheme"]
    counts = {"classified": 0, "already": 0, "no_target": 0}
    for row in state["pilot"]:
        if not row["target"]:
            counts["no_target"] += 1
            continue
        existing = session.execute(
            select(func.count())
            .select_from(SymbolRevisionClassificationAssignment)
            .where(
                SymbolRevisionClassificationAssignment.symbol_revision_id == row["revision_id"],
                SymbolRevisionClassificationAssignment.classification_scheme_id == scheme.id,
                SymbolRevisionClassificationAssignment.status.in_(_LIVE_ASSIGNMENT_STATES),
            )
        ).scalar_one()
        if existing:
            counts["already"] += 1
            continue
        node = node_for_reference(plan, nodes, row["target"])
        _propose_and_verify(
            session,
            propose_symbol_revision_classification,
            transition_symbol_revision_classification,
            occurred_at=occurred_at,
            symbol_revision_id=row["revision_id"],
            classification_node_id=node.id,
            assignment_role="primary",
            method=SOURCE_MAPPING_METHOD,
            proposed_by_user_id=actor_id,
            evidence={"name_basis": row["name_basis"], "dexpi_class": row["target"], "source": "DEXPI TrainingTestCases"},
        )
        counts["classified"] += 1
    return counts


PILOT_SLUG_PREFIX = "dexpi-ttc-"


def govern_pilot_facets(session: Session, *, occurred_at: datetime, apply: bool) -> dict[str, Any]:
    """Give the pilot's symbols the discipline and category assignments they never had.

    The pilot recorded only a representation type, so its symbols carry no
    ENGINEERING-DISCIPLINE or SYMBOL-CATEGORY-FAMILY assignment, which is why
    the Catalogue's keyword rules were still deciding their facets. The
    SM-P0-10 utility derives those assignments from the symbol's own category
    and discipline columns, the way it did for the rest of the catalogue: method
    `legacy_backfill`, status `proposed`, never verified. It is restricted here
    to the pilot's own symbols; nothing else is touched.
    """
    report = BackfillReport(applied=apply)
    targets = [t for t in select_backfill_targets(session) if t.slug.startswith(PILOT_SLUG_PREFIX)]
    report.targets_examined = len(targets)
    for target in targets:
        backfill_one_target(session, target, backfilled_at=occurred_at, apply=apply, report=report)
    if apply:
        session.flush()
        # The Catalog search reads stored facet rows, and its staleness counters
        # move only when a payload, name or category changes, not when an
        # assignment is added. Mark the pilot's rows out of date so the next
        # search recomputes them from the governed values.
        session.execute(
            text("UPDATE catalog_symbol_facets SET rules_version = 0 WHERE symbol_revision_id = ANY(:ids)"),
            {"ids": [target.symbol_revision_id for target in targets]},
        )
    return {
        "revisions": len(targets),
        "assignments": len(report.written_assignment_ids) if apply else len(report.planned),
        "skipped": report.skip_counts(),
        "by_label": _count_labels(report),
    }


def _count_labels(report: BackfillReport) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in report.planned:
        key = f"{item.scheme_code}: {item.node_label}"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def apply(
    session: Session,
    *,
    plan: dict[str, Any],
    package_data: dict[str, Any],
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> dict[str, Any]:
    """Record everything the dry run reported, or refuse and write nothing."""
    _require_actor(session, actor_id)
    state = read_state(session, plan)
    decision = decide(plan, state)
    if decision["blocking"]:
        raise Blocked(decision["blocking"])

    package, rights = ensure_package_and_rights(session, plan, state, actor_id=actor_id, occurred_at=occurred_at)
    ensure_standards(session, plan, state, occurred_at=occurred_at)
    nodes = ensure_scheme_and_nodes(session, plan, decision, state, occurred_at=occurred_at)
    resolved = ensure_concepts(session, plan, decision, nodes, actor_id=actor_id, occurred_at=occurred_at, state=state)
    mapping_counts = ensure_external_mappings(session, plan, resolved, state, actor_id=actor_id, occurred_at=occurred_at)

    converter_version = package_data.get("converter_version") or "unknown"
    created, revised = [], []
    by_slug = {symbol["slug"]: symbol for symbol in plan["symbols"]}
    revise_reasons = {item["slug"]: item["reasons"] for item in decision["symbols"]["revise"]}
    for symbol_plan in plan["symbols"]:
        slug = symbol_plan["slug"]
        if slug in decision["symbols"]["unchanged"]:
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
        revision = create_revision(
            session,
            symbol_plan,
            symbol,
            revision_label=label,
            package=package,
            state=state,
            nodes=nodes,
            resolved=resolved,
            plan=plan,
            converter_version=converter_version,
            actor_id=actor_id,
            occurred_at=occurred_at,
        )
        if held is None:
            symbol.current_revision_id = revision.id
            created.append({"slug": slug, "symbol_revision_id": str(revision.id), "revision": label})
        else:
            revised.append(
                {"slug": slug, "symbol_revision_id": str(revision.id), "revision": label, "reasons": revise_reasons.get(slug, [])}
            )
    del by_slug

    pilot_counts = classify_pilot(session, plan, state, nodes, actor_id=actor_id, occurred_at=occurred_at)
    pilot_facets = govern_pilot_facets(session, occurred_at=occurred_at, apply=True)
    session.flush()
    return {
        "mode": "apply",
        "package_id": str(package.id),
        "rights_record_id": str(rights.id),
        "rights_decision_status": rights.decision_status,
        "scheme_nodes_created": len(decision["nodes_to_create"]) + len(decision["pilot_nodes_to_create"]),
        "pilot_classes_added": [node["dexpi_name"] for node in decision["pilot_nodes_to_create"]],
        "concepts_created": [
            {"concept_key": key, "concept_code": info["concept_code"]} for key, info in resolved.items() if info["created"]
        ],
        "concepts_reused": [
            {"concept_key": key, "concept_code": info["concept_code"]} for key, info in resolved.items() if not info["created"]
        ],
        "external_mappings": mapping_counts,
        "pilot_classification": pilot_counts,
        "pilot_governed_facets": pilot_facets,
        "symbols_created": created,
        "symbols_revised": revised,
        "symbols_unchanged": len(decision["symbols"]["unchanged"]),
    }


# --------------------------------------------------------------------------
# Storage.
# --------------------------------------------------------------------------


def _pending_attachments(session: Session, plan: dict[str, Any]) -> list[Any]:
    """The attachments of every planned symbol's revisions that are not yet published.

    Read with SQL, because `asset_role` is a column the ORM does not map.
    """
    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    return list(
        session.execute(
            text(
                "SELECT a.object_key, a.sha256, a.content_type, a.asset_role "
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
    uploader=upload_object_bytes,
) -> dict[str, Any]:
    """PUT every pending attachment's SVG to its key. Reads the database, writes nothing to it."""
    by_sha = {hashlib.sha256(data).hexdigest(): (name, data) for name, data in package_data["svg"].items()}
    attachments = _pending_attachments(session, plan)
    problems, uploaded = [], []
    for attachment in attachments:
        found = by_sha.get(attachment.sha256 or "")
        if found is None:
            problems.append(f"{attachment.object_key}: no file in the package has sha256 {attachment.sha256}")
            continue
        _name, data = found
        try:
            validate_stored_image(data, attachment.content_type)
        except UnsafeImageContentError as exc:
            problems.append(f"{attachment.object_key}: {exc}")
            continue
        if not dry_run:
            uploader(
                object_key=attachment.object_key,
                payload=data,
                content_type=attachment.content_type,
                env_file=storage_env_file,
            )
        uploaded.append(attachment.object_key)
    if problems:
        raise Blocked(problems)
    return {
        "mode": "upload-dry-run" if dry_run else "upload",
        "attachments": len(attachments),
        "primary": sum(1 for a in attachments if a.asset_role == "primary"),
        "options": sum(1 for a in attachments if a.asset_role == "option"),
        "uploaded_count": 0 if dry_run else len(uploaded),
    }


# --------------------------------------------------------------------------
# The gate and publication.
# --------------------------------------------------------------------------


def _latest_revisions(session: Session, plan: dict[str, Any]) -> list[tuple[GovernedSymbol, SymbolRevision]]:
    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    rows = []
    for symbol in session.execute(
        select(GovernedSymbol).where(GovernedSymbol.slug.in_(slugs)).order_by(GovernedSymbol.slug)
    ).scalars():
        latest = session.execute(
            select(SymbolRevision)
            .where(SymbolRevision.symbol_id == symbol.id)
            .order_by(SymbolRevision.created_at.desc(), SymbolRevision.revision_label.desc())
            .limit(1)
        ).scalar_one_or_none()
        if latest is not None:
            rows.append((symbol, latest))
    return rows


def evaluate_gate(
    session: Session, *, plan: dict[str, Any], occurred_at: datetime, actor_id: uuid.UUID | None
) -> dict[str, Any]:
    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    levels: dict[str, int] = {}
    refusals: list[dict[str, Any]] = []
    evaluated = 0
    for symbol, revision in _latest_revisions(session, plan):
        if revision.lifecycle_state != REVISION_LIFECYCLE_STATE:
            continue
        decision = enforce_publication_gate(
            session, symbol_revision_id=revision.id, evaluated_at=occurred_at, evaluated_by_user_id=actor_id
        )
        evaluated += 1
        outcomes[decision.outcome] = outcomes.get(decision.outcome, 0) + 1
        levels[decision.traceability_level] = levels.get(decision.traceability_level, 0) + 1
        for reason in decision.refusal_reasons:
            reasons[reason] = reasons.get(reason, 0) + 1
        if decision.outcome == "refused" and len(refusals) < 5:
            refusals.append({"slug": symbol.slug, "describe": describe_refusal(decision)})
    return {
        "mode": "gate",
        "evaluated": evaluated,
        "outcomes": outcomes,
        "refusal_reasons": reasons,
        "traceability_levels": levels,
        "sample_refusals": refusals,
    }


def _stored_object_problems(
    pending: list[tuple[GovernedSymbol, SymbolRevision]], session: Session, *, storage_env_file, fetcher
) -> list[str]:
    problems = []
    for symbol, revision in pending:
        for attachment in session.execute(
            text(
                "SELECT object_key, sha256, asset_role FROM attachments "
                "WHERE parent_type = 'symbol_revision' AND parent_id = :revision"
            ),
            {"revision": revision.id},
        ).all():
            try:
                stored = fetcher(object_key=attachment.object_key, env_file=storage_env_file)
            except Exception as exc:  # noqa: BLE001 -- reported by class name only
                problems.append(f"{symbol.slug}: {attachment.asset_role} object unreadable ({type(exc).__name__})")
                continue
            if hashlib.sha256(stored["payload"]).hexdigest() != attachment.sha256:
                problems.append(f"{symbol.slug}: stored {attachment.asset_role} object does not match the recorded digest")
    return problems


def publish(
    session: Session,
    *,
    plan: dict[str, Any],
    actor_id: uuid.UUID,
    occurred_at: datetime,
    storage_env_file,
    dry_run: bool,
    fetcher=download_object_bytes,
) -> dict[str, Any]:
    """Publish every pending revision to the public pack, or none of them."""
    _require_actor(session, actor_id)
    sort_orders = {symbol["slug"]: symbol["sort_order"] for symbol in plan["symbols"]}
    rows = _latest_revisions(session, plan)
    if len(rows) != len(plan["symbols"]):
        raise ConfigurationError(f"{len(plan['symbols']) - len(rows)} planned symbols are not recorded; run apply first")
    unchanged = [symbol.slug for symbol, revision in rows if revision.lifecycle_state == "published"]
    pending = [(symbol, revision) for symbol, revision in rows if revision.lifecycle_state != "published"]
    wrong_state = [
        f"{symbol.slug}: {revision.lifecycle_state}"
        for symbol, revision in pending
        if revision.lifecycle_state != REVISION_LIFECYCLE_STATE
    ]
    if wrong_state:
        raise PublicationRefused(f"{len(wrong_state)} revisions are not approved", {"not_approved": wrong_state})
    if not pending:
        return {"mode": "publish", "published_count": 0, "published": [], "unchanged_count": len(unchanged)}

    storage_problems = _stored_object_problems(pending, session, storage_env_file=storage_env_file, fetcher=fetcher)
    if storage_problems:
        raise PublicationRefused(
            f"{len(storage_problems)} stored objects are missing or wrong; run upload first",
            {"storage_problems": storage_problems},
        )

    refusals: dict[str, int] = {}
    samples: list[dict[str, Any]] = []
    levels: dict[str, int] = {}
    for symbol, revision in pending:
        decision = enforce_publication_gate(
            session, symbol_revision_id=revision.id, evaluated_at=occurred_at, evaluated_by_user_id=actor_id
        )
        levels[decision.traceability_level] = levels.get(decision.traceability_level, 0) + 1
        if not decision.permitted:
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
            "would_publish": len(pending),
            "unchanged_count": len(unchanged),
            "traceability_levels": levels,
        }

    pack = session.execute(
        select(PublicationPack).where(PublicationPack.pack_code == PUBLICATION_PACK_CODE)
    ).scalar_one_or_none()
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
            "source": "disc_dexpi_ingest publish",
            "source_package_code": PACKAGE_CODE,
            "symbol_count": len(pending),
            "simulation": False,
        },
        created_at=occurred_at,
        completed_at=occurred_at,
    )
    session.add(job)
    session.flush()

    published = []
    for symbol, revision in pending:
        previous_id = symbol.current_revision_id if symbol.current_revision_id != revision.id else None
        page, entry = publish_revision_to_pack(
            session,
            symbol=symbol,
            revision=revision,
            publication_pack=pack,
            sort_order=sort_orders[symbol.slug],
            effective_date=pack.effective_date,
            published_at=occurred_at,
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
        session.add(
            AuditEvent(
                id=uuid.uuid4(),
                entity_type=entity_type,
                entity_id=entity_id,
                action=action,
                actor_id=actor_id,
                payload_json=audit_payload,
                created_at=occurred_at,
            )
        )
    session.flush()
    session.execute(text("SELECT refresh_published_symbol_views()"))
    return {
        "mode": "publish",
        "publication_pack_code": pack.pack_code,
        "publication_job_id": str(job.id),
        "published_count": len(published),
        "published": published,
        "unchanged_count": len(unchanged),
        "traceability_levels": levels,
    }


def _retire_superseded(
    session: Session,
    symbol: GovernedSymbol,
    previous_revision_id: uuid.UUID,
    new_page_id: uuid.UUID,
    *,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> None:
    """A new revision replaces the one before it in the Catalogue, which is retired."""
    reason = "Superseded by a newer revision of the same symbol."
    for page in session.execute(
        select(PublishedPage).where(
            PublishedPage.current_symbol_revision_id == previous_revision_id,
            PublishedPage.id != new_page_id,
            PublishedPage.publication_state == "active",
        )
    ).scalars():
        page.publication_state = "retired"
        page.retired_by = actor_id
        page.retired_at = occurred_at
        page.retirement_reason = reason
        page.updated_at = occurred_at
    for entry in session.execute(
        select(PackEntry).where(
            PackEntry.symbol_revision_id == previous_revision_id,
            PackEntry.published_page_id != new_page_id,
            PackEntry.publication_state == "active",
        )
    ).scalars():
        entry.publication_state = "retired"
        entry.retired_by = actor_id
        entry.retired_at = occurred_at
        entry.retirement_reason = reason
    previous = session.get(SymbolRevision, previous_revision_id)
    if previous is not None and previous.lifecycle_state == "published":
        previous.lifecycle_state = "deprecated"


# --------------------------------------------------------------------------
# The command.
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name, help_text in (
        ("apply", "Record the package: source package, rights, scheme, concepts, mappings, symbols"),
        ("upload", "Put every pending SVG in object storage (reads the database)"),
        ("gate", "Evaluate the publication gate over the pending revisions"),
        ("publish", "Publish every pending revision to the public pack, or none"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--package", required=True, help="The extracted import package directory")
        command.add_argument("--output", help="Write the full report here as JSON")
        if name in {"apply", "upload", "publish"}:
            command.add_argument("--dry-run", action="store_true", help="Read and decide; write nothing")
        if name in {"upload", "publish"}:
            command.add_argument("--storage-env-file", help="Object storage settings (default: the API's)")
        if name in {"apply", "publish"}:
            command.add_argument("--actor-id", type=uuid.UUID, required=(name == "publish"), help="Existing user UUID")
        if name == "gate":
            command.add_argument("--actor-id", type=uuid.UUID, help="Optional: the operator the evaluation is recorded against")

    verify_command = commands.add_parser("verify", help="Run the acceptance checks against the database (reads only)")
    verify_command.add_argument("--package", required=True)
    verify_command.add_argument("--output", help="Write the full report here as JSON")
    verify_command.add_argument("--storage-env-file", help="Object storage settings, to check each stored SVG's metadata")
    verify_command.add_argument("--no-storage", action="store_true", help="Skip the check that reads each stored SVG")
    verify_command.add_argument("--expected-pilot-count", type=int, help="The symbols the pilot pack should still show")
    verify_command.add_argument("--brief-spot-checks", action="store_true", help="Also check the brief's named values (ND0004, ND0136/S-154)")

    facets_command = commands.add_parser("facet-changes", help="List published symbols whose facets change under the governed rules")
    facets_command.add_argument("--output", help="Write the list here as JSON")

    report_command = commands.add_parser("report", help="Write the Markdown import report from the saved JSON reports")
    report_command.add_argument("--package", required=True)
    report_command.add_argument("--out", required=True, help="Where to write the Markdown report")
    for name in ("dry-run-report", "apply-report", "publish-report", "verify-report", "facet-changes"):
        report_command.add_argument(f"--{name}", help="JSON written by the matching command's --output")
    report_command.add_argument("--backup", help="Path of the database backup taken before the import")
    report_command.add_argument("--commit", help="The code commit the import ran from")
    report_command.add_argument("--rights-approver", help="Who approved the rights record, as it should read in the report")
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
    for key in ("symbols_created", "symbols_revised", "published", "concepts_created", "concepts_reused"):
        if isinstance(printable.get(key), list):
            printable[key] = len(printable[key])
    scheme = printable.get("scheme")
    if isinstance(scheme, dict) and "node_code_map" in scheme:
        printable["scheme"] = {k: v for k, v in scheme.items() if k != "node_code_map"}
    return printable


def _read_report(path: str | None):
    if not path:
        return None
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "facet-changes":
            from .disc_dexpi_verify import facet_changes

            engine = _engine()
            try:
                with Session(engine) as session:
                    changes = facet_changes(session)
                    session.rollback()
            finally:
                engine.dispose()
            _write_output({"changes": changes}, args.output)
            print(json.dumps({"changed": len(changes), "ids": [item["id"] for item in changes]}, indent=2))
            return 0
        package_data = load_package(args.package)
        plan = plan_module.plan_import(package_data)
        if args.command == "report":
            from .disc_dexpi_verify import render_report

            facets = _read_report(args.facet_changes)
            markdown = render_report(
                plan=plan,
                package_data=package_data,
                dry_run=_read_report(args.dry_run_report),
                apply_report=_read_report(args.apply_report),
                publish_report=_read_report(args.publish_report),
                verify_report=_read_report(args.verify_report),
                facet_changes=(facets or {}).get("changes") if facets is not None else None,
                backup_path=args.backup,
                commit=args.commit,
                rights_approver=args.rights_approver,
            )
            Path(args.out).write_text(markdown, encoding="utf-8")
            print(json.dumps({"written": args.out, "bytes": len(markdown.encode("utf-8"))}, indent=2))
            return 0
        engine = _engine()
        try:
            now = datetime.now(timezone.utc)
            if args.command == "verify":
                from .disc_dexpi_verify import verify

                fetcher = None if args.no_storage else download_object_bytes
                with Session(engine) as session:
                    report = verify(
                        session,
                        plan,
                        package_data,
                        fetcher=fetcher,
                        storage_env_file=None if args.no_storage else _storage_env_file(args),
                        expected_pilot_count=args.expected_pilot_count,
                        brief_spot_checks=args.brief_spot_checks,
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
                    pilot_facets = govern_pilot_facets(session, occurred_at=now, apply=False)
                    session.rollback()
                report = dry_run_report(plan, state, decision, pilot_facets)
                _write_output(report, args.output)
                print(json.dumps(_printable(report), indent=2, default=str))
                return EXIT_BLOCKED if report["blocked"] else 0
            if args.command == "apply":
                if args.actor_id is None:
                    raise ConfigurationError("--actor-id is required for apply")
                with Session(engine) as session, session.begin():
                    report = apply(session, plan=plan, package_data=package_data, actor_id=args.actor_id, occurred_at=now)
            elif args.command == "upload":
                with Session(engine) as session:
                    report = upload_assets(
                        session,
                        plan,
                        package_data,
                        storage_env_file=_storage_env_file(args),
                        dry_run=args.dry_run,
                    )
                    session.rollback()
            elif args.command == "gate":
                with Session(engine) as session, session.begin():
                    report = evaluate_gate(session, plan=plan, occurred_at=now, actor_id=args.actor_id)
            else:
                try:
                    with Session(engine) as session, session.begin():
                        report = publish(
                            session,
                            plan=plan,
                            actor_id=args.actor_id,
                            occurred_at=now,
                            storage_env_file=_storage_env_file(args),
                            dry_run=args.dry_run,
                        )
                        if args.dry_run:
                            session.rollback()
                except PublicationRefused as refused:
                    print(
                        json.dumps({"mode": "publish", "refused": str(refused), **refused.report}, indent=2),
                        file=sys.stderr,
                    )
                    print("DISC DEXPI publication refused; nothing was published.", file=sys.stderr)
                    return 1
            _write_output(report, args.output)
            print(json.dumps(_printable(report), indent=2, default=str))
            return 0
        finally:
            engine.dispose()
    except Blocked as blocked:
        print("DISC DEXPI import blocked; nothing was written:", file=sys.stderr)
        for reason in blocked.reasons:
            print(f"  - {reason}", file=sys.stderr)
        return EXIT_BLOCKED
    except (ConfigurationError, DiscPlanError) as exc:
        print(f"DISC DEXPI import failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except (ValueError, LookupError) as exc:
        print(f"DISC DEXPI import failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except Exception:
        print(
            "DISC DEXPI import failed; nothing was recorded. Check the actor id, the package and database readiness.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
