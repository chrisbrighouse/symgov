"""Record the DEXPI pilot symbols and their provenance (WP4). No migrations.

`services/dexpi_ingestion` decides what is recorded; this writes it, the way
`dexpi_seed` writes what `services/dexpi_concepts` decides. Like `ics_import`
and the seed it is a trusted operator interface rather than an API endpoint,
it owns its transaction, `plan` reaches the database only to read, and it adds
no migration.

**What one `apply` writes, per plan section 4's WP4 list.** One
`authoritative_library` `SourcePackage` with its acquisition and one proposed
rights record, the standards and editions the assertions need, and then per
symbol: a `GovernedSymbol` and an `approved` `SymbolRevision`, a
`source_package_entries` row, one `asset_transformations` step from the source
XML digest to the SVG digest, a verified `symbol_standard_links` assertion,
a verified primary semantic assignment against its WP3 concept, and a verified
classification.

**The one step this cannot take.** The rights record is *proposed*, never
approved. `transition_rights_record` demands a named decider unconditionally
and section 8.4 allows no controlled-system exception, so the approval happens
through the SM-P1-01 review UI. Until it does, section 9.2's rights dimension
refuses every one of these symbols with `rights_undecided` -- which is the
gate working, not a defect.

**Publishing is its own command, and it is all-or-nothing (decision D10).**
`apply` creates revisions `approved` and publishes nothing. `upload` puts each
converted SVG in object storage at the content-addressed key the plan
decided, and touches no database. `publish` then, in one transaction: checks
every stored object against the digest the revision records, evaluates
section 9.2's gate for every symbol, and only if all of them pass writes the
`publication_packs`, `published_pages` and `pack_entries` rows the Catalogue
reads -- through `runtime.publish_revision_to_pack`, the routine Rupert's
publication handoff uses, so the two paths cannot drift. One refusal
publishes nothing. `--actor-id` is recorded as the publication's requester
and approver, because publishing to the public Catalogue is a named person's
decision just as the rights approval is.

**Idempotent by check, like the seed.** A symbol is identified by its slug,
which WP2's geometry signature decides, so a second `apply` recognises its own
earlier work and skips it. A slug already present is never rewritten: changing
a governed revision is a reviewer's decision, not a driver's.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from .classification_assignments import (
    propose_symbol_revision_classification,
    transition_symbol_revision_classification,
)
from .image_content import UnsafeImageContentError, validate_stored_image
from .models import (
    Attachment,
    AuditEvent,
    ClassificationNode,
    ClassificationScheme,
    GovernedSymbol,
    PublicationJob,
    PublicationPack,
    SourcePackage,
    StandardVersion,
    SymbolRevision,
    User,
)
from .publication_gate import describe_refusal, enforce_publication_gate
from .published_catalog import choose_published_preview_asset
from .rights_provenance import list_rights_records, propose_rights_record, record_asset_transformation
from .runtime import download_object_bytes, publish_revision_to_pack, upload_object_bytes
from .services.dexpi_converter import CONVERTER_NAME, CONVERTER_VERSION
from .services.dexpi_ingestion import (
    CLASSIFICATION_METHOD,
    PACKAGE_SOURCE_URI,
    DexpiIngestionPlanError,
    LINK_VERIFICATION_METHOD,
    PACKAGE_CODE,
    PUBLICATION_PACK_CODE,
    PUBLICATION_PACK_TITLE,
    REPRESENTATION_TYPE_NODE_CODE,
    REPRESENTATION_TYPE_SCHEME_CODE,
    REVISION_LABEL,
    REVISION_LIFECYCLE_STATE,
    SEMANTIC_METHOD,
    SVG_CONTENT_TYPE,
    plan_ingestion,
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

DEFAULT_SELECTION_PATH = "integrations/dexpi/selection.json"


class ConfigurationError(ValueError):
    """A missing setting or manifest, safe to name. Never carries a credential."""


def _engine():
    url = os.environ.get("SYMGOV_DATABASE_URL")
    if not url:
        raise ConfigurationError("SYMGOV_DATABASE_URL is required in the environment")
    # hide_parameters keeps bound values out of any driver exception text.
    return create_engine(url, hide_parameters=True)


def _read_json(path: str | Path, label: str) -> dict:
    resolved = Path(path)
    if not resolved.is_file():
        raise ConfigurationError(f"{label} not found at {resolved}")
    return json.loads(resolved.read_text(encoding="utf-8"))


def index_harvest_assets(harvest_root: str | Path) -> dict[tuple[str, str], Path]:
    """Every converted SVG, keyed by the source path and filename that name it.

    The WP2 selection records `source_path` and the SVG's basename; the WP1
    harvest writes one directory per converted file, whose `manifest.json`
    carries the same `source_path`. This is the join between them, and it is
    done once rather than by scanning for a basename that two files could
    share.
    """
    root = Path(harvest_root)
    if not root.is_dir():
        raise ConfigurationError(f"harvest directory not found at {root}")
    index: dict[tuple[str, str], Path] = {}
    for manifest_path in sorted(root.glob("assets/*/manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source_path = manifest.get("source_path")
        if not source_path:
            continue
        for symbol in manifest.get("symbols") or []:
            name = symbol.get("svg")
            if name:
                index[(source_path, name)] = manifest_path.parent / name
    if not index:
        raise ConfigurationError(f"no converted assets were found under {root}")
    return index


def verify_assets(plan: dict, assets: dict[tuple[str, str], Path]) -> tuple[dict[str, int], list[str]]:
    """Confirm each planned SVG is on disk with the digest the manifest claims.

    The digest is what section 9.2's integrity dimension reads and what the
    transformation chain records, so recording one that no file matches would
    make the chain unverifiable at exactly the point it exists to be verified.
    """
    sizes: dict[str, int] = {}
    problems: list[str] = []
    for symbol in plan["symbols"]:
        key = (symbol["entry"]["source_path"], symbol["transformation"]["svg"])
        path = assets.get(key)
        if path is None or not path.is_file():
            problems.append(f"{symbol['canonical_name']}: no converted SVG at {key[0]} / {key[1]}")
            continue
        data = path.read_bytes()
        digest = hashlib.sha256(data).hexdigest()
        if digest != symbol["transformation"]["derived_asset_sha256"]:
            problems.append(
                f"{symbol['canonical_name']}: converted SVG digest does not match the manifest"
            )
            continue
        sizes[symbol["geometry_signature"]] = len(data)
    return sizes, problems


# --------------------------------------------------------------------------
# Reading what is already recorded.
# --------------------------------------------------------------------------


def index_existing_symbols(session: Session, plan: dict) -> dict[str, uuid.UUID]:
    """The planned slugs already present, so `apply` never writes one twice."""
    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    rows = session.execute(
        select(GovernedSymbol.slug, GovernedSymbol.id).where(GovernedSymbol.slug.in_(slugs))
    ).all()
    return {slug: symbol_id for slug, symbol_id in rows}


def _require_actor(session: Session, actor_id: uuid.UUID) -> uuid.UUID:
    if session.get(User, actor_id) is None:
        raise ConfigurationError(f"no user exists with id {actor_id}")
    return actor_id


def _classification_node(session: Session) -> ClassificationNode:
    """Decision D9's node, which migration 20260909_0052 seeds.

    Read from the constants rather than from a symbol, because D9 assigns the
    same node to all of them and a plan with nothing left to create still has
    to resolve it.
    """
    node = session.execute(
        select(ClassificationNode)
        .join(ClassificationScheme, ClassificationScheme.id == ClassificationNode.scheme_id)
        .where(
            ClassificationScheme.scheme_code == REPRESENTATION_TYPE_SCHEME_CODE,
            ClassificationNode.node_code == REPRESENTATION_TYPE_NODE_CODE,
        )
    ).scalar_one_or_none()
    if node is None:
        raise ConfigurationError(
            "the classification node decision D9 assigns is not in this database: "
            f"{REPRESENTATION_TYPE_SCHEME_CODE} / {REPRESENTATION_TYPE_NODE_CODE}"
        )
    return node


# --------------------------------------------------------------------------
# Writing.
# --------------------------------------------------------------------------


def ensure_package(session: Session, plan: dict, *, occurred_at: datetime) -> tuple[SourcePackage, bool]:
    """The one `authoritative_library` package decision D4 settles on."""
    definition = plan["package"]
    package = get_source_package(session, definition["package_code"])
    if package is not None:
        return package, False
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
        licence_reference=definition["licence_reference"],
        metadata=definition["metadata"],
    )
    session.flush()
    return package, True


def ensure_rights_proposal(
    session: Session, plan: dict, *, package: SourcePackage, actor_id: uuid.UUID, occurred_at: datetime
) -> tuple[uuid.UUID | None, bool]:
    """Propose the single CC BY 4.0 rights record, once.

    Proposed and left there. Approving it is a named human decision through
    the SM-P1-01 UI, and this command has no way to take one.
    """
    live = [
        record
        for record in list_rights_records(session, source_package_id=package.id)
        if record.decision_status in {"proposed", "approved"}
    ]
    if live:
        return live[0].id, False
    definition = plan["rights"]
    record = propose_rights_record(
        session,
        source_package_id=package.id,
        rights_status=definition["rights_status"],
        disposition=definition["disposition"],
        determination_method=definition["determination_method"],
        licence_reference=definition["licence_reference"],
        proposed_at=occurred_at,
        proposed_by_user_id=actor_id,
        decision_reason=(
            "The TrainingTestCases repository publishes the whole corpus under CC BY 4.0, "
            "which permits redistribution with attribution."
        ),
        evidence={
            "licence": "CC BY 4.0",
            "licence_uri": definition["licence_reference"],
            "source_uri": plan["package"]["source_uri"],
            "basis": "the repository's own LICENSE file",
        },
    )
    session.flush()
    return record.id, True


def ensure_standards(session: Session, plan: dict, *, occurred_at: datetime) -> dict[tuple[str, str], uuid.UUID]:
    """Find or register every standard and edition the assertions name."""
    editions: dict[tuple[str, str], uuid.UUID] = {}
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
            version = session.execute(
                select(StandardVersion).where(
                    StandardVersion.standard_id == standard.id,
                    StandardVersion.version_label == version_label,
                )
            ).scalar_one_or_none()
            if version is None:
                version = register_standard_version(
                    session,
                    standard_id=standard.id,
                    version_label=version_label,
                    registered_at=occurred_at,
                )
                session.flush()
            editions[(standard.standard_code, version_label)] = version.id
    return editions


def ingest_symbol(
    session: Session,
    symbol_plan: dict,
    *,
    package: SourcePackage,
    editions: dict[tuple[str, str], uuid.UUID],
    classification_node: ClassificationNode,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> dict:
    """One symbol and its five governed facts, in one transaction's worth of work."""
    symbol = GovernedSymbol(
        id=uuid.uuid4(),
        slug=symbol_plan["slug"],
        canonical_name=symbol_plan["canonical_name"],
        category=symbol_plan["category"],
        discipline=symbol_plan["discipline"],
        owner_id=actor_id,
        owner_organization_id=None,
        # Platform-owned and not organisation-scoped. `catalog_symbol_id` stays
        # null: an identifier is allocated by the publication path, and nothing
        # here publishes.
        visibility="public",
        organization_wide=False,
        current_revision_id=None,
        created_at=occurred_at,
        updated_at=occurred_at,
    )
    session.add(symbol)
    session.flush()

    revision = SymbolRevision(
        id=uuid.uuid4(),
        symbol_id=symbol.id,
        revision_label=REVISION_LABEL,
        lifecycle_state=REVISION_LIFECYCLE_STATE,
        payload_json=symbol_plan["payload"],
        rationale=(
            "Ingested from the DEXPI TrainingTestCases example corpus under the "
            "DEXPI symbol library pilot."
        ),
        author_id=actor_id,
        created_at=occurred_at,
    )
    session.add(revision)
    session.flush()
    symbol.current_revision_id = revision.id

    # Parented to the revision itself, which is the lineage
    # `ensure_preview_authorization` trusts without further proof; the bytes
    # are `upload`'s job, at the key the plan decided (decision D10).
    asset = symbol_plan["payload"]["assets"][0]
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

    entry = add_source_package_entry(
        session,
        source_package_id=package.id,
        symbol_revision_id=revision.id,
        added_at=occurred_at,
        source_label=symbol_plan["entry"]["source_label"],
        provider_entry_identifier=symbol_plan["entry"]["provider_entry_identifier"],
        source_path=symbol_plan["entry"]["source_path"],
        original_asset_sha256=symbol_plan["entry"]["original_asset_sha256"],
        sort_order=symbol_plan["sort_order"],
    )
    session.flush()

    # Appendix B.2's chain. The source digest is adopted from the entry rather
    # than re-stated, which is what `record_asset_transformation` asks for.
    record_asset_transformation(
        session,
        symbol_revision_id=revision.id,
        tool_name=CONVERTER_NAME,
        tool_version=CONVERTER_VERSION,
        derived_asset_sha256=symbol_plan["transformation"]["derived_asset_sha256"],
        performed_at=occurred_at,
        source_package_entry_id=entry.id,
        recorded_by_user_id=actor_id,
        evidence={"format": "svg", "filename": symbol_plan["transformation"]["svg"]},
    )

    assertion = symbol_plan["assertion"]
    link = assert_symbol_standard_link(
        session,
        symbol_revision_id=revision.id,
        standard_version_id=editions[(assertion["standard_code"], assertion["version_label"])],
        relationship_type=assertion["relationship_type"],
        asserted_at=occurred_at,
        source_symbol_identifier=assertion["source_symbol_identifier"],
        # Where the source file lives, so its digest can be re-checked later.
        source_uri=PACKAGE_SOURCE_URI,
        notes=(
            "Asserted from the WP2 selection manifest under decision D2 of the DEXPI "
            "symbol library pilot."
        ),
        evidence={"basis": assertion["basis"], "source_path": symbol_plan["entry"]["source_path"]},
    )
    session.flush()
    # `import_manifest` is one of the two controlled-system methods, so this
    # verifies without a named reviewer -- the assertion is read out of the
    # provider's own shipped content.
    transition_symbol_standard_link(
        session,
        link.id,
        target_status="verified",
        occurred_at=occurred_at,
        verification_method=LINK_VERIFICATION_METHOD,
    )

    assignment = propose_symbol_semantic_assignment(
        session,
        symbol_revision_id=revision.id,
        semantic_concept_id=uuid.UUID(symbol_plan["concept"]["concept_id"]),
        assignment_role="primary",
        method=SEMANTIC_METHOD,
        proposed_at=occurred_at,
        proposed_by_user_id=actor_id,
        evidence={
            "concept_key": symbol_plan["concept"]["concept_key"],
            "name_basis": symbol_plan["payload"]["dexpi"]["name_basis"],
        },
    )
    session.flush()
    transition_symbol_semantic_assignment(
        session, assignment.id, target_status="verified", occurred_at=occurred_at
    )

    classification = propose_symbol_revision_classification(
        session,
        symbol_revision_id=revision.id,
        classification_node_id=classification_node.id,
        assignment_role="primary",
        method=CLASSIFICATION_METHOD,
        proposed_at=occurred_at,
        proposed_by_user_id=actor_id,
        evidence={
            "basis": "every symbol this package carries is a converted vector drawing",
            "format": "svg",
        },
    )
    session.flush()
    transition_symbol_revision_classification(
        session, classification.id, target_status="verified", occurred_at=occurred_at
    )

    return {
        "slug": symbol_plan["slug"],
        "canonical_name": symbol_plan["canonical_name"],
        "symbol_id": str(symbol.id),
        "symbol_revision_id": str(revision.id),
        "relationship_type": assertion["relationship_type"],
        "concept_code": symbol_plan["concept"]["concept_code"],
    }


def ingest(
    session: Session,
    *,
    plan: dict,
    actor_id: uuid.UUID,
    occurred_at: datetime,
) -> dict:
    """Record every planned symbol that is not already recorded."""
    _require_actor(session, actor_id)
    unmeasured = [
        symbol["canonical_name"]
        for symbol in plan["symbols"]
        if "size_bytes" not in symbol["payload"]["assets"][0]
    ]
    if unmeasured:
        # `attachments.size_bytes` is NOT NULL and a size nobody measured is
        # not recorded, so the SVGs have to be read before anything is written.
        raise ConfigurationError(
            f"{len(unmeasured)} planned SVGs carry no measured size; run apply with --harvest"
        )
    existing = index_existing_symbols(session, plan)
    package, package_created = ensure_package(session, plan, occurred_at=occurred_at)
    rights_id, rights_created = ensure_rights_proposal(
        session, plan, package=package, actor_id=actor_id, occurred_at=occurred_at
    )
    editions = ensure_standards(session, plan, occurred_at=occurred_at)
    classification_node = _classification_node(session)

    created, unchanged = [], []
    for symbol_plan in plan["symbols"]:
        if symbol_plan["slug"] in existing:
            unchanged.append(
                {"slug": symbol_plan["slug"], "canonical_name": symbol_plan["canonical_name"]}
            )
            continue
        created.append(
            ingest_symbol(
                session,
                symbol_plan,
                package=package,
                editions=editions,
                classification_node=classification_node,
                actor_id=actor_id,
                occurred_at=occurred_at,
            )
        )
    return {
        "package_code": package.package_code,
        "package_id": str(package.id),
        "package_created": package_created,
        "rights_record_id": str(rights_id) if rights_id else None,
        "rights_record_created": rights_created,
        "rights_decision_status": "proposed",
        "standard_editions": len(editions),
        "created": created,
        "unchanged": unchanged,
    }


# --------------------------------------------------------------------------
# The gate.
# --------------------------------------------------------------------------


def evaluate_gate(
    session: Session, *, plan: dict, occurred_at: datetime, actor_id: uuid.UUID | None
) -> dict:
    """Run section 9.2's gate over every ingested revision and record it.

    Decision D6's whole point: the evaluations are read in a database that is
    thrown away afterwards, before anything reaches production. Recording is
    what `enforce_publication_gate` does on either publication path, so this
    reads the gate exactly as production would.
    """
    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    rows = session.execute(
        select(GovernedSymbol.slug, GovernedSymbol.canonical_name, GovernedSymbol.current_revision_id)
        .where(GovernedSymbol.slug.in_(slugs))
        .order_by(GovernedSymbol.slug)
    ).all()

    outcomes: dict[str, int] = {}
    reasons: dict[str, int] = {}
    levels: dict[str, int] = {}
    refusals: list[dict] = []
    evaluated = 0
    for slug, canonical_name, revision_id in rows:
        if revision_id is None:
            continue
        decision = enforce_publication_gate(
            session,
            symbol_revision_id=revision_id,
            evaluated_at=occurred_at,
            evaluated_by_user_id=actor_id,
        )
        evaluated += 1
        outcomes[decision.outcome] = outcomes.get(decision.outcome, 0) + 1
        levels[decision.traceability_level] = levels.get(decision.traceability_level, 0) + 1
        for reason in decision.refusal_reasons:
            reasons[reason] = reasons.get(reason, 0) + 1
        if decision.outcome == "refused" and len(refusals) < 5:
            refusals.append(
                {
                    "slug": slug,
                    "canonical_name": canonical_name,
                    "describe": describe_refusal(decision),
                    "dimensions": [
                        result.as_report() for result in decision.dimensions if not result.satisfied
                    ],
                }
            )
    return {
        "evaluated": evaluated,
        "not_evaluated": len(slugs) - evaluated,
        "outcomes": outcomes,
        "refusal_reasons": reasons,
        "traceability_levels": levels,
        "sample_refusals": refusals,
    }


# --------------------------------------------------------------------------
# Storage and publication (decision D10).
# --------------------------------------------------------------------------


class PublicationRefused(ValueError):
    """`publish` found a reason to publish nothing. Carries a report, never a credential."""

    def __init__(self, message: str, report: dict):
        super().__init__(message)
        self.report = report


def upload_assets(
    plan: dict,
    assets: dict[tuple[str, str], Path],
    *,
    storage_env_file,
    uploader=upload_object_bytes,
) -> dict:
    """PUT every planned SVG to its content-addressed key. Touches no database.

    `verify_assets` has already matched each file to its digest; the stored
    image check here is the one `organization_symbol_drafts` applies to an
    upload, so a file that is not an SVG never reaches the bucket under an SVG
    content type. Re-running is the same PUT of the same bytes.
    """
    uploaded = []
    for symbol in plan["symbols"]:
        asset = symbol["payload"]["assets"][0]
        path = assets[(symbol["entry"]["source_path"], symbol["transformation"]["svg"])]
        data = path.read_bytes()
        try:
            validate_stored_image(data, asset["content_type"])
        except UnsafeImageContentError as exc:
            raise ConfigurationError(f"{symbol['canonical_name']}: {exc}") from exc
        uploader(
            object_key=asset["object_key"],
            payload=data,
            content_type=SVG_CONTENT_TYPE,
            env_file=storage_env_file,
        )
        uploaded.append(asset["object_key"])
    return {"uploaded_count": len(uploaded), "uploaded": uploaded}


def _stored_object_problems(symbols: list[tuple], *, storage_env_file, fetcher) -> list[str]:
    """Every planned object must be in the bucket with the bytes the revision records."""
    problems = []
    for symbol, revision in symbols:
        asset = (revision.payload_json or {}).get("assets", [{}])[0]
        # `publish_revision_to_pack` accepts `no_preview` silently, as Rupert's
        # path must; a DEXPI symbol without one would be a blank catalogue card.
        preview = choose_published_preview_asset(revision.payload_json)
        if preview is None or preview.get("object_key") != asset.get("object_key"):
            problems.append(f"{symbol.slug}: the Catalogue would not preview the stored SVG")
            continue
        try:
            stored = fetcher(object_key=asset["object_key"], env_file=storage_env_file)
        except Exception as exc:  # noqa: BLE001 -- reported by class name only
            problems.append(f"{symbol.slug}: stored object unreadable ({type(exc).__name__})")
            continue
        if hashlib.sha256(stored["payload"]).hexdigest() != asset.get("sha256"):
            problems.append(f"{symbol.slug}: stored object does not match the recorded digest")
    return problems


def publish(
    session: Session,
    *,
    plan: dict,
    actor_id: uuid.UUID,
    occurred_at: datetime,
    storage_env_file,
    fetcher=download_object_bytes,
) -> dict:
    """Publish every planned symbol to the public Catalogue, or none of them."""
    _require_actor(session, actor_id)
    slugs = [symbol["slug"] for symbol in plan["symbols"]]
    sort_orders = {symbol["slug"]: symbol["sort_order"] for symbol in plan["symbols"]}
    rows = session.execute(
        select(GovernedSymbol, SymbolRevision)
        .join(SymbolRevision, SymbolRevision.id == GovernedSymbol.current_revision_id)
        .where(GovernedSymbol.slug.in_(slugs))
        .order_by(GovernedSymbol.slug)
    ).all()
    if len(rows) != len(slugs):
        raise ConfigurationError(
            f"{len(slugs) - len(rows)} planned symbols are not recorded; run apply first"
        )

    unchanged = [symbol.slug for symbol, revision in rows if revision.lifecycle_state == "published"]
    pending = [(symbol, revision) for symbol, revision in rows if revision.lifecycle_state != "published"]
    wrong_state = [
        f"{symbol.slug}: {revision.lifecycle_state}"
        for symbol, revision in pending
        if revision.lifecycle_state != "approved"
    ]
    if wrong_state:
        raise PublicationRefused(
            f"{len(wrong_state)} revisions are not approved",
            {"not_approved": wrong_state},
        )
    if not pending:
        return {"published_count": 0, "published": [], "unchanged_count": len(unchanged)}

    storage_problems = _stored_object_problems(
        pending, storage_env_file=storage_env_file, fetcher=fetcher
    )
    if storage_problems:
        raise PublicationRefused(
            f"{len(storage_problems)} stored objects are missing, wrong or not previewable; run upload first",
            {"storage_problems": storage_problems},
        )

    # Every gate before any publication: `publish_revision_to_pack` allocates
    # catalogue identity, which is irreversible, and one refusal publishes
    # nothing.
    refusals: dict[str, int] = {}
    samples: list[dict] = []
    levels: dict[str, int] = {}
    for symbol, revision in pending:
        decision = enforce_publication_gate(
            session,
            symbol_revision_id=revision.id,
            evaluated_at=occurred_at,
            evaluated_by_user_id=actor_id,
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

    pack = session.execute(
        select(PublicationPack).where(PublicationPack.pack_code == PUBLICATION_PACK_CODE)
    ).scalar_one_or_none()
    if pack is None:
        pack = PublicationPack(
            id=uuid.uuid4(),
            pack_code=PUBLICATION_PACK_CODE,
            title=PUBLICATION_PACK_TITLE,
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
            "source": "dexpi_ingest publish",
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
        page, entry = publish_revision_to_pack(
            session,
            symbol=symbol,
            revision=revision,
            publication_pack=pack,
            sort_order=sort_orders[symbol.slug],
            effective_date=pack.effective_date,
            published_at=occurred_at,
        )
        published.append(
            {
                "slug": symbol.slug,
                "catalog_symbol_id": symbol.catalog_symbol_id,
                "page_code": page.page_code,
                "published_page_id": str(page.id),
                "pack_entry_id": str(entry.id),
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
    ] + [
        ("published_page", uuid.UUID(item["published_page_id"]), "published_page_upserted")
        for item in published
    ]
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
        "publication_pack_code": pack.pack_code,
        "publication_job_id": str(job.id),
        "published_count": len(published),
        "published": published,
        "unchanged_count": len(unchanged),
        "traceability_levels": levels,
    }


# --------------------------------------------------------------------------
# The command.
# --------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)

    for name, help_text in (
        ("plan", "Read-only: what the ingestion would record, and what is already there"),
        ("apply", "Record every planned symbol that is not already recorded"),
        ("gate", "Evaluate section 9.2's publication gate over the ingested revisions"),
        ("upload", "Put every converted SVG in object storage; touches no database"),
        ("publish", "Publish every ingested symbol to the public Catalogue, or none"),
    ):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--selection",
            default=DEFAULT_SELECTION_PATH,
            help=f"WP2 selection manifest (default {DEFAULT_SELECTION_PATH})",
        )
        command.add_argument(
            "--concept-map",
            required=True,
            help="The concept map `dexpi_seed apply --output` wrote",
        )
        command.add_argument(
            "--harvest",
            # `apply` records each SVG's size and `upload` sends its bytes, so
            # neither can run from the manifests alone.
            required=name in {"apply", "upload"},
            help="WP1 harvest directory; when given, every SVG digest is re-checked on disk",
        )
        command.add_argument("--output", help="Write the full report here as JSON")
        if name in {"upload", "publish"}:
            command.add_argument(
                "--storage-env-file",
                help="Object storage settings (default: the API's configured storage env file)",
            )
        if name in {"apply", "publish"}:
            command.add_argument(
                "--actor-id",
                type=uuid.UUID,
                required=True,
                help="Existing user UUID; a governed revision is never authored anonymously",
            )
        if name == "gate":
            command.add_argument(
                "--actor-id",
                type=uuid.UUID,
                help="Optional: the operator the gate evaluation is recorded against",
            )
    return parser


def _storage_env_file(args):
    if args.storage_env_file:
        return args.storage_env_file
    from .settings import get_settings

    return get_settings().storage_env_file


def build_plan(args) -> tuple[dict, list[str], dict]:
    selection = _read_json(args.selection, "selection manifest")
    concept_map = _read_json(args.concept_map, "concept map")
    sizes: dict[str, int] = {}
    problems: list[str] = []
    assets: dict[tuple[str, str], Path] = {}
    if args.harvest:
        assets = index_harvest_assets(args.harvest)
        draft = plan_ingestion(selection, concept_map)
        sizes, problems = verify_assets(draft, assets)
    return plan_ingestion(selection, concept_map, svg_sizes=sizes), problems, assets


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        plan, asset_problems, assets = build_plan(args)
        if asset_problems and args.command in {"apply", "upload"}:
            raise ConfigurationError(
                f"{len(asset_problems)} converted assets do not match the manifest; "
                f"first: {asset_problems[0]}"
            )
        if args.command == "upload":
            report = {
                "command": "upload",
                "mode": "upload",
                "planned_symbols": plan["summary"]["symbol_count"],
                **upload_assets(plan, assets, storage_env_file=_storage_env_file(args)),
            }
            if args.output:
                Path(args.output).write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
            printable = {key: value for key, value in report.items() if key != "uploaded"}
            print(json.dumps(printable, indent=2, default=str))
            return 0
        engine = _engine()
        try:
            report = {
                "command": args.command,
                "planned_symbols": plan["summary"]["symbol_count"],
                "summary": plan["summary"],
                "asset_problems": asset_problems,
            }
            if args.command == "plan":
                with Session(engine) as session:
                    existing = index_existing_symbols(session, plan)
                    package = get_source_package(session, plan["package"]["package_code"])
                    # Nothing was written, but roll back explicitly so the read
                    # transaction cannot outlive the report.
                    session.rollback()
                report.update(
                    {
                        "mode": "dry-run",
                        "package_recorded": package is not None,
                        "would_create": plan["summary"]["symbol_count"] - len(existing),
                        "already_recorded": len(existing),
                    }
                )
            elif args.command == "apply":
                with Session(engine) as session, session.begin():
                    result = ingest(
                        session,
                        plan=plan,
                        actor_id=args.actor_id,
                        occurred_at=datetime.now(timezone.utc),
                    )
                report.update({"mode": "apply", **result})
                report["created_count"] = len(result["created"])
                report["unchanged_count"] = len(result["unchanged"])
            elif args.command == "publish":
                try:
                    with Session(engine) as session, session.begin():
                        result = publish(
                            session,
                            plan=plan,
                            actor_id=args.actor_id,
                            occurred_at=datetime.now(timezone.utc),
                            storage_env_file=_storage_env_file(args),
                        )
                except PublicationRefused as refused:
                    # The transaction has rolled back; say why, with counts.
                    print(
                        json.dumps({"mode": "publish", "refused": str(refused), **refused.report}, indent=2),
                        file=sys.stderr,
                    )
                    print("DEXPI publication refused; nothing was published.", file=sys.stderr)
                    return 1
                report.update({"mode": "publish", **result})
            else:
                with Session(engine) as session, session.begin():
                    result = evaluate_gate(
                        session,
                        plan=plan,
                        occurred_at=datetime.now(timezone.utc),
                        actor_id=getattr(args, "actor_id", None),
                    )
                report.update({"mode": "gate", **result})

            if args.output:
                Path(args.output).write_text(
                    json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
                )
                report["output"] = args.output
            printable = dict(report)
            # The per-symbol lists are what `--output` is for; the console gets
            # the counts, so a 174-symbol run stays readable.
            for key in ("created", "unchanged", "summary", "published"):
                if isinstance(printable.get(key), list):
                    printable[key] = len(printable[key])
            print(json.dumps(printable, indent=2, default=str))
            return 1 if asset_problems else 0
        finally:
            engine.dispose()
    except (ConfigurationError, DexpiIngestionPlanError) as exc:
        # Naming an absent setting or a manifest gap leaks nothing.
        print(f"DEXPI ingestion failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        # Service-layer refusals name symbols, states and vocabularies only.
        print(f"DEXPI ingestion failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except LookupError as exc:
        print(f"DEXPI ingestion failed; nothing was recorded: {exc}", file=sys.stderr)
        return 1
    except Exception:
        # Driver exceptions may carry connection credentials. Never echo them.
        print(
            "DEXPI ingestion failed; nothing was recorded. Check the actor id, the "
            "manifests and database readiness.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
