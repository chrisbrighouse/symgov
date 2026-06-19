from __future__ import annotations

import hashlib
import hmac
import os
import socket
import struct
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import sys
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
BACKEND_DEPS = BACKEND_ROOT / ".deps"

if os.environ.get("SYMGOV_DISABLE_BACKEND_DEPS", "").strip().lower() not in {"1", "true", "yes", "on"} and BACKEND_DEPS.exists() and str(BACKEND_DEPS) not in sys.path:
    sys.path.insert(0, str(BACKEND_DEPS))

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from .db import create_session_factory, read_env_file
from .property_options import remember_property_option
from .models import (
    AgentDefinition,
    AgentOutputArtifact,
    AgentQueueItem,
    AgentRun,
    Attachment,
    AuditEvent,
    ClassificationRecord,
    ExternalIdentity,
    GovernedSymbol,
    IntakeRecord,
    PackEntry,
    ProvenanceAssessment,
    PublicationJob,
    PublicationPack,
    PublishedPage,
    ReviewCase,
    ReviewCaseAction,
    ReviewSplitItem,
    ReviewSymbolPropertyOption,
    ScottSourceDiscoverySite,
    ControlException,
    SourcePackage,
    SymbolRevision,
    HannahPhotoCandidate,
    HannahSymbolCurationState,
    User,
    ValidationReport,
    WhitneyDemandSignal,
    WhitneyMarketIntelligenceReport,
)


DEFAULT_STORAGE_ENV_FILE = Path("/data/.openclaw/workspace/symgov/.env.backend.storage")
LEGACY_ID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "symgov/runtime-legacy-id")
DEFAULT_AGENT_MODEL = "ollama/gemma4:e4b"
DEFAULT_VLAD_GEMINI_MODEL = "gemini/gemini-2.5-flash"


def get_gemini_api_key() -> str:
    return os.environ.get("SYMGOV_GEMINI_API_KEY", "").strip() or os.environ.get("GEMINI_API_KEY", "").strip()


def resolve_vlad_agent_model() -> str:
    """Prefer Gemini for Vlad when credentials/config make it available."""
    configured = os.environ.get("SYMGOV_VLAD_MODEL", "").strip()
    if configured:
        return configured
    if get_gemini_api_key():
        return os.environ.get("SYMGOV_GEMINI_MODEL", DEFAULT_VLAD_GEMINI_MODEL).strip() or DEFAULT_VLAD_GEMINI_MODEL
    return DEFAULT_AGENT_MODEL


def agent_definition_seeds() -> tuple[dict[str, str], ...]:
    return (
        {
            "slug": "scott",
            "display_name": "Scott",
            "role": "intake agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "intake",
        },
        {
            "slug": "vlad",
            "display_name": "Vlad",
            "role": "technical validation and graphic-quality agent",
            "model": resolve_vlad_agent_model(),
            "status": "active",
            "queue_family": "validation",
        },
        {
            "slug": "tracy",
            "display_name": "Tracy",
            "role": "provenance and rights agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "provenance",
        },
        {
            "slug": "daisy",
            "display_name": "Daisy",
            "role": "review coordination agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "review_coordination",
        },
        {
            "slug": "libby",
            "display_name": "Libby",
            "role": "classification and research librarian",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "classification",
        },
        {
            "slug": "rupert",
            "display_name": "Rupert",
            "role": "publishing and release management agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "publication",
        },
        {
            "slug": "ed",
            "display_name": "Ed",
            "role": "visual experience and feedback agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "ux_feedback",
        },
        {
            "slug": "hannah",
            "display_name": "Hannah",
            "role": "catalogue quality and long-term curation agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "curation",
        },
        {
            "slug": "reggie",
            "display_name": "Reggie",
            "role": "audit, compliance, and control-room agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "control_audit",
        },
        {
            "slug": "whitney",
            "display_name": "Whitney",
            "role": "market intelligence and demand sensing agent",
            "model": DEFAULT_AGENT_MODEL,
            "status": "active",
            "queue_family": "market_intelligence",
        },
    )


AGENT_DEFINITION_SEEDS = agent_definition_seeds()

SCOTT_SOURCE_DISCOVERY_DEFAULT_SEED_QUERY = (
    "ProjectMaterials P&ID symbols ISA-5.1 ISO 14617 IEC 60617 NECA 100 QElectroTech GD&T"
)

SCOTT_SOURCE_DISCOVERY_SEED_QUERIES = [
    SCOTT_SOURCE_DISCOVERY_DEFAULT_SEED_QUERY,
    "Free engineering symbol library for P&ID",
    "Process control valve P&ID symbols SVG library",
    "Electrical substation single line diagram symbols IEC 60617 download",
    "Fire alarm system plan symbols CAD blocks",
]

SCOTT_SOURCE_DISCOVERY_SITE_SEEDS = (
    {
        "domain": "projectmaterials.com",
        "url": "https://projectmaterials.com/pid-symbols/",
        "title": "ProjectMaterials P&ID symbols",
        "description": (
            "Broad, practical P&ID symbol list grouped around real engineering categories. "
            "Use as an immediate seed source, then map candidates back to ISA-5.1 and ISO 14617."
        ),
        "industry": "piping and instrumentation",
        "process": "P&ID symbol intake",
        "organization_type": "practical engineering reference",
        "source_prompt": (
            "Inspect ProjectMaterials first for broad P&ID category coverage. Treat the site as an intake/reference "
            "source, not the authority; map every candidate symbol back to ISA-5.1 and/or ISO 14617 before validation."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["web", "image", "P&ID"],
        "evidence_json": {
            "recommended_use": "immediate_seed_source",
            "authority_role": "candidate_source_only",
            "map_back_to": ["ISA-5.1", "ISO 14617"],
            "rights_note": "Check rights, reuse terms, provenance, and standard alignment before reuse.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9900"),
    },
    {
        "domain": "vistaprojects.com",
        "url": "https://www.vistaprojects.com/",
        "title": "Vista Projects P&ID resources",
        "description": "Practical engineering articles and P&ID references useful for intake and validation context.",
        "industry": "piping and instrumentation",
        "process": "P&ID symbol intake and validation",
        "organization_type": "engineering reference publisher",
        "source_prompt": (
            "Use Vista Projects as a practical P&ID reference source. Extract candidate categories cautiously and "
            "cross-check against ISA-5.1 / ISO 14617 before creating governed records."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["web", "P&ID"],
        "evidence_json": {
            "recommended_use": "candidate_source",
            "map_back_to": ["ISA-5.1", "ISO 14617"],
            "rights_note": "Reference/intake only until provenance and reuse terms are checked.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9400"),
    },
    {
        "domain": "qelectrotech.org",
        "url": "https://qelectrotech.org/",
        "title": "QElectroTech",
        "description": "Open electrical diagram editor with symbol collections useful for electrical candidate intake.",
        "industry": "electrical",
        "process": "electrical symbol intake",
        "organization_type": "open-source symbol library",
        "source_prompt": (
            "Use QElectroTech for electrical candidate symbols and file references. Map candidates back to IEC 60617 "
            "and NECA 100 where applicable; verify licence/provenance before reuse."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["QET", "XML", "SVG", "electrical"],
        "evidence_json": {
            "recommended_use": "candidate_source",
            "map_back_to": ["IEC 60617", "NECA 100"],
            "rights_note": "Check licence, provenance, and standard alignment before importing downloadable symbols.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9300"),
    },
    {
        "domain": "necanet.org",
        "url": "https://www.necanet.org/",
        "title": "NECA 100",
        "description": "Electrical symbols standard/reference to use alongside IEC 60617 for electrical taxonomy alignment.",
        "industry": "electrical",
        "process": "electrical taxonomy alignment",
        "organization_type": "standards body",
        "source_prompt": (
            "Use NECA 100 as an electrical standards/taxonomy reference with IEC 60617. Do not treat website excerpts "
            "as reusable symbol artwork unless rights are explicitly checked."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["standard", "electrical"],
        "evidence_json": {
            "recommended_use": "standards_backbone",
            "authority_role": "taxonomy_alignment",
            "pair_with": ["IEC 60617"],
            "rights_note": "Standards references guide taxonomy; symbol reuse still requires rights/provenance checks.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9200"),
    },
    {
        "domain": "webstore.iec.ch",
        "url": "https://webstore.iec.ch/en/publication/602",
        "title": "IEC 60617 graphical symbols",
        "description": "Authoritative electrical graphical-symbol taxonomy backbone.",
        "industry": "electrical",
        "process": "standards taxonomy alignment",
        "organization_type": "standards catalogue",
        "source_prompt": (
            "Use IEC 60617 as the authoritative electrical taxonomy backbone. Use public metadata for alignment; "
            "do not copy protected standard content or artwork without rights clearance."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["standard", "electrical"],
        "evidence_json": {
            "recommended_use": "authoritative_taxonomy_backbone",
            "applies_to": ["electrical"],
            "rights_note": "Use for classification/alignment; protected standard content is not intake artwork.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9800"),
    },
    {
        "domain": "isa.org",
        "url": "https://www.isa.org/standards-and-publications/isa-standards",
        "title": "ISA-5.1 instrumentation symbols",
        "description": "Authoritative instrumentation/P&ID symbol taxonomy backbone.",
        "industry": "piping and instrumentation",
        "process": "P&ID taxonomy alignment",
        "organization_type": "standards body",
        "source_prompt": (
            "Use ISA-5.1 as the authoritative P&ID/instrumentation taxonomy reference. Candidate symbols from "
            "ProjectMaterials or Vista Projects must be mapped back to ISA-5.1 / ISO 14617."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["standard", "P&ID", "instrumentation"],
        "evidence_json": {
            "recommended_use": "authoritative_taxonomy_backbone",
            "applies_to": ["P&ID", "instrumentation"],
            "rights_note": "Use for taxonomy alignment; do not copy protected standard content without clearance.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9800"),
    },
    {
        "domain": "iso.org",
        "url": "https://www.iso.org/standard/81532.html",
        "title": "ISO 14617 / ISO 1101 graphical symbol standards",
        "description": "Authoritative ISO backbone for diagram symbols and GD&T/geometrical tolerancing references.",
        "industry": "cross-industry",
        "process": "standards taxonomy alignment",
        "organization_type": "standards catalogue",
        "source_prompt": (
            "Use ISO 14617 as an authoritative graphical-symbol taxonomy backbone and ISO 1101 for GD&T alignment. "
            "Use public metadata only unless rights to standard content are confirmed."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["standard", "P&ID", "mechanical", "GD&T"],
        "evidence_json": {
            "recommended_use": "authoritative_taxonomy_backbone",
            "standards": ["ISO 14617", "ISO 1101"],
            "rights_note": "Standards guide taxonomy/alignment; protected content is not an intake asset.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9700"),
    },
    {
        "domain": "asme.org",
        "url": "https://www.asme.org/codes-standards/find-codes-standards/y14-5-dimensioning-tolerancing",
        "title": "ASME Y14.5 GD&T",
        "description": "Authoritative GD&T/geometrical tolerancing reference for mechanical symbol taxonomy.",
        "industry": "mechanical",
        "process": "GD&T taxonomy alignment",
        "organization_type": "standards body",
        "source_prompt": (
            "Use ASME Y14.5 with ISO 1101 for mechanical/GD&T taxonomy alignment. Use accessible metadata and "
            "secondary descriptions for intake; do not copy protected standard diagrams without clearance."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["standard", "mechanical", "GD&T"],
        "evidence_json": {
            "recommended_use": "authoritative_taxonomy_backbone",
            "pair_with": ["ISO 1101", "Keyence", "GD&T Basics"],
            "rights_note": "Use for classification/alignment only until reuse rights are cleared.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9600"),
    },
    {
        "domain": "keyence.com",
        "url": "https://www.keyence.com/ss/products/measure-sys/gd-and-t/",
        "title": "Keyence GD&T explanations",
        "description": "Readable GD&T descriptions useful for mechanical symbol descriptions and validation context.",
        "industry": "mechanical",
        "process": "GD&T description support",
        "organization_type": "manufacturer reference",
        "source_prompt": (
            "Use Keyence for readable GD&T explanations, not as the authority. Map terms back to ASME Y14.5 / ISO 1101 "
            "and avoid reusing diagrams unless rights are clear."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["web", "mechanical", "GD&T"],
        "evidence_json": {
            "recommended_use": "readable_description_source",
            "map_back_to": ["ASME Y14.5", "ISO 1101"],
            "rights_note": "Reference/intake only until reuse terms and provenance are checked.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.9000"),
    },
    {
        "domain": "gdandtbasics.com",
        "url": "https://www.gdandtbasics.com/gdt-symbols/",
        "title": "GD&T Basics symbols",
        "description": "Readable GD&T symbol descriptions for mechanical intake and operator-friendly explanations.",
        "industry": "mechanical",
        "process": "GD&T description support",
        "organization_type": "educational reference",
        "source_prompt": (
            "Use GD&T Basics for readable descriptions and candidate terminology. Map candidates back to ASME Y14.5 / "
            "ISO 1101; treat site diagrams as reference only unless reuse rights are clear."
        ),
        "include_next_run": True,
        "symbol_formats_json": ["web", "mechanical", "GD&T"],
        "evidence_json": {
            "recommended_use": "readable_description_source",
            "map_back_to": ["ASME Y14.5", "ISO 1101"],
            "rights_note": "Reference/intake only until reuse terms and provenance are checked.",
        },
        "status": "recommended",
        "relevance_score": Decimal("0.8900"),
    },
    {
        "domain": "freecad.org",
        "url": "https://www.freecad.org/",
        "title": "FreeCAD resources",
        "description": "Open CAD ecosystem and symbol/reference material useful for intake context, with rights checks required.",
        "industry": "mechanical",
        "process": "CAD reference intake",
        "organization_type": "open-source CAD project",
        "source_prompt": (
            "Use FreeCAD resources as reference/intake only. Downloadable CAD libraries must not be promoted to reusable "
            "assets until licence, provenance, rights, and standard alignment have been checked."
        ),
        "include_next_run": False,
        "symbol_formats_json": ["CAD", "SVG", "mechanical"],
        "evidence_json": {
            "recommended_use": "candidate_reference_source",
            "rights_note": "Downloadable CAD libraries are reference/intake only until rights and provenance are checked.",
        },
        "status": "candidate",
        "relevance_score": Decimal("0.7800"),
    },
    {
        "domain": "traceparts.com",
        "url": "https://www.traceparts.com/",
        "title": "Manufacturer CAD libraries",
        "description": "Representative manufacturer CAD-library aggregator for candidate/reference intake only.",
        "industry": "cross-industry",
        "process": "manufacturer CAD reference intake",
        "organization_type": "manufacturer CAD library aggregator",
        "source_prompt": (
            "Treat manufacturer CAD libraries as reference/intake only. Do not reuse or publish downloaded CAD "
            "symbols until rights, reuse terms, provenance, and alignment to IEC/ISO/ISA/ASME standards are checked."
        ),
        "include_next_run": False,
        "symbol_formats_json": ["CAD", "manufacturer"],
        "evidence_json": {
            "recommended_use": "candidate_reference_source",
            "rights_note": "Reference/intake only until rights, reuse terms, provenance, and standard alignment are checked.",
        },
        "status": "candidate",
        "relevance_score": Decimal("0.7600"),
    },
    {
        "domain": "commons.wikimedia.org",
        "url": "https://commons.wikimedia.org/w/index.php?search=P%26ID+symbols&title=Special%3AMediaSearch&type=image",
        "title": "Wikimedia Commons",
        "description": "Supplemental public media repository; useful after priority standards and practical engineering sources.",
        "industry": "cross-industry",
        "process": "supplemental symbol discovery",
        "organization_type": "public media repository",
        "source_prompt": (
            "Use only as supplemental discovery after ProjectMaterials/Vista/QElectroTech/standards-backed sources. "
            "Verify licence, provenance, and standard alignment before reuse."
        ),
        "include_next_run": False,
        "symbol_formats_json": ["image"],
        "evidence_json": {
            "recommended_use": "supplemental_source",
            "note": "No longer the primary seed source; prioritise standards-backed sources first.",
            "rights_note": "Check licence, provenance, and standard alignment before reuse.",
        },
        "status": "candidate",
        "relevance_score": Decimal("0.6500"),
    },
    {
        "domain": "linecad.com",
        "url": "https://linecad.com",
        "title": "linecad.com",
        "description": "Ignored for Scott source discovery per operator guidance.",
        "industry": "cross-industry",
        "process": "symbol discovery",
        "organization_type": "website",
        "source_prompt": None,
        "include_next_run": False,
        "symbol_formats_json": [],
        "evidence_json": {
            "note": "Explicitly ignored.",
        },
        "status": "ignored",
        "relevance_score": Decimal("0.0000"),
    },
    {
        "domain": "autodesk.com",
        "url": "https://autodesk.com",
        "title": "autodesk.com",
        "description": "Ignored for Scott source discovery per operator guidance.",
        "industry": "cross-industry",
        "process": "symbol discovery",
        "organization_type": "website",
        "source_prompt": None,
        "include_next_run": False,
        "symbol_formats_json": [],
        "evidence_json": {
            "note": "Explicitly ignored.",
        },
        "status": "ignored",
        "relevance_score": Decimal("0.0000"),
    },
    {
        "domain": "svghmi.pro",
        "url": "https://svghmi.pro",
        "title": "svghmi.pro",
        "description": "Ignored for Scott source discovery per operator guidance.",
        "industry": "cross-industry",
        "process": "symbol discovery",
        "organization_type": "website",
        "source_prompt": None,
        "include_next_run": False,
        "symbol_formats_json": [],
        "evidence_json": {
            "note": "Explicitly ignored.",
        },
        "status": "ignored",
        "relevance_score": Decimal("0.0000"),
    },
)

def parse_timestamp(value: str | None) -> datetime:
    if not value:
        return datetime.now(timezone.utc).replace(microsecond=0)
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def coerce_uuid(value: str | uuid.UUID | None) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return uuid.uuid5(LEGACY_ID_NAMESPACE, str(value))


def coerce_numeric(value: float | int | str | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def resolve_hannah_symbol_curation_state(
    session,
    symbol_states: dict[uuid.UUID, HannahSymbolCurationState],
    symbol_id: uuid.UUID,
    completed_at: datetime,
) -> HannahSymbolCurationState:
    """Resolve or create a Hannah symbol state once per symbol per transaction.

    A Hannah run can emit more than one attempt for the same symbol, especially
    when published records have duplicate slugs/labels. SQLAlchemy queries may
    not reliably de-duplicate pending inserts before flush, so keep an explicit
    in-memory map for the current durable report to avoid violating the unique
    symbol_id constraint.
    """
    state = symbol_states.get(symbol_id)
    if state is not None:
        return state

    state = session.query(HannahSymbolCurationState).filter_by(symbol_id=symbol_id).one_or_none()
    if state is None:
        state = HannahSymbolCurationState(
            id=uuid.uuid4(),
            symbol_id=symbol_id,
            created_at=completed_at,
        )
        session.add(state)
    symbol_states[symbol_id] = state
    return state



def resolve_hannah_photo_candidate_record(
    session,
    candidate_payload: dict[str, Any],
    symbol_id: uuid.UUID,
    image_url: str,
    completed_at: datetime,
) -> HannahPhotoCandidate:
    """Resolve or create a Hannah photo candidate without reusing colliding IDs.

    Hannah runner payload IDs can be deterministic source-result IDs. The same
    source image can be proposed for more than one published symbol, so a
    payload ID may already exist for a different (symbol_id, image_url) pair.
    In that case, do not mutate the existing row and do not insert with the
    conflicting primary key; create a fresh row and rely on the unique
    (symbol_id, image_url) lookup for idempotence on later runs.
    """
    candidate_id = coerce_uuid(candidate_payload.get("id"))
    if candidate_id is not None:
        record = session.get(HannahPhotoCandidate, candidate_id)
        if record is not None and record.symbol_id == symbol_id and record.image_url == image_url:
            return record

    record = session.query(HannahPhotoCandidate).filter_by(symbol_id=symbol_id, image_url=image_url).one_or_none()
    if record is not None:
        return record

    record_id = candidate_id
    if record_id is not None and session.get(HannahPhotoCandidate, record_id) is not None:
        record_id = None

    record = HannahPhotoCandidate(
        id=record_id or uuid.uuid4(),
        symbol_id=symbol_id,
        image_url=image_url,
        first_seen_at=completed_at,
    )
    session.add(record)
    return record


def env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def slugify_public_code(value: Any) -> str:
    text_value = str(value or "").strip().lower()
    chars = []
    last_dash = False
    for char in text_value:
        if char.isalnum():
            chars.append(char)
            last_dash = False
        elif not last_dash:
            chars.append("-")
            last_dash = True
    slug = "".join(chars).strip("-")
    return slug or "published"


def normalize_scott_source_discovery_status(site_payload: dict[str, Any]) -> str:
    status = str(site_payload.get("status") or "").strip().lower()
    access_status = str(site_payload.get("access_status") or "").strip().lower()
    if status in {"ignored", "blocked", "unreachable", "inaccessible", "timeout", "failed"}:
        return "ignored"
    if access_status in {"blocked", "unreachable", "inaccessible", "timeout", "failed", "error"}:
        return "ignored"
    if status:
        return status
    return "candidate"


def next_source_package_code(session) -> str:
    session.execute(text("LOCK TABLE source_packages IN EXCLUSIVE MODE"))
    codes = session.execute(text("select package_code from source_packages")).scalars().all()
    current_max = 0
    for code in codes:
        candidate = str(code or "").strip().upper()
        if len(candidate) == 4 and all(char in "0123456789ABCDEF" for char in candidate):
            current_max = max(current_max, int(candidate, 16))
    next_value = current_max + 1
    if next_value > 0xFFFF:
        raise RuntimeError("Source package code space exhausted.")
    return f"{next_value:04X}"


def _coerce_positive_int(value: Any) -> int | None:
    try:
        candidate = int(value)
    except (TypeError, ValueError):
        return None
    return candidate if candidate > 0 else None


def find_existing_source_package_for_submission(session, normalized: dict[str, Any]) -> SourcePackage | None:
    submission_batch_id = str(normalized.get("submission_batch_id") or "").strip()
    package_token = str(normalized.get("source_package_id") or "").strip()
    package_queue_item_id = str(normalized.get("source_package_queue_item_id") or "").strip()

    lookup_specs: list[tuple[str, str]] = []
    if submission_batch_id:
        lookup_specs.append(("submission_batch_id", submission_batch_id))
    if package_token:
        lookup_specs.append(("source_package_id", package_token))
    if package_queue_item_id:
        lookup_specs.append(("source_package_queue_item_id", package_queue_item_id))

    for json_key, lookup_value in lookup_specs:
        row = session.execute(
            text(
                f"""
                SELECT source_package_id
                FROM intake_records
                WHERE source_package_id IS NOT NULL
                  AND normalized_submission_json->>'{json_key}' = :lookup_value
                ORDER BY created_at ASC
                LIMIT 1
                """
            ),
            {"lookup_value": lookup_value},
        ).first()
        if row and row[0]:
            package = session.get(SourcePackage, coerce_uuid(row[0]))
            if package is not None:
                return package

    return None


def next_source_package_sequence(session, source_package_id: uuid.UUID) -> int:
    row = session.execute(
        text(
            """
            SELECT COALESCE(
                MAX(
                    CASE
                        WHEN (normalized_submission_json->>'package_symbol_sequence') ~ '^[0-9]+$'
                        THEN (normalized_submission_json->>'package_symbol_sequence')::integer
                        ELSE NULL
                    END
                ),
                0
            )
            FROM intake_records
            WHERE source_package_id = :source_package_id
            """
        ),
        {"source_package_id": source_package_id},
    ).scalar_one()
    return int(row or 0) + 1


def ensure_source_package_for_intake(session, durable_record: dict[str, Any], created_at: datetime) -> SourcePackage:
    package_id = coerce_uuid(durable_record.get("source_package_id"))
    normalized = dict(durable_record.get("normalized_submission_json") or {})
    source_file = (
        normalized.get("original_filename")
        or normalized.get("origin_file_name")
        or normalized.get("file_name")
        or durable_record.get("raw_object_key")
        or durable_record.get("source_ref")
        or "Submitted sheet"
    )

    if package_id is not None:
        package = session.get(SourcePackage, package_id)
        if package is not None:
            return package

    package = find_existing_source_package_for_submission(session, normalized)
    if package is not None:
        return package

    # Serialize package allocation so one submission package (batch/ZIP token)
    # always maps to one shared source package code.
    session.execute(text("LOCK TABLE intake_records IN EXCLUSIVE MODE"))
    session.execute(text("LOCK TABLE source_packages IN EXCLUSIVE MODE"))

    package = find_existing_source_package_for_submission(session, normalized)
    if package is not None:
        return package

    package_code = str(normalized.get("source_package_code") or "").strip().upper()
    if not (len(package_code) == 4 and all(char in "0123456789ABCDEF" for char in package_code)):
        package_code = next_source_package_code(session)

    package = session.query(SourcePackage).filter_by(package_code=package_code).one_or_none()
    if package is None:
        package = SourcePackage(
            id=package_id or uuid.uuid4(),
            package_code=package_code,
            title=Path(str(source_file)).name,
            provider=durable_record.get("submitter"),
            package_type="submission_sheet",
            status="active",
            created_at=created_at,
            updated_at=created_at,
        )
        session.add(package)
    else:
        package.title = package.title or Path(str(source_file)).name
        package.provider = package.provider or durable_record.get("submitter")
        package.updated_at = created_at
    session.flush()
    return package


def read_storage_env_file(env_file: str | os.PathLike[str] | None = None) -> tuple[Path, dict[str, str]]:
    path = Path(env_file) if env_file else DEFAULT_STORAGE_ENV_FILE
    return path, read_env_file(path)


def _storage_connection_settings(env: dict[str, str]) -> dict[str, str]:
    endpoint = env.get("SYMGOV_S3_ENDPOINT")
    bucket = env.get("SYMGOV_S3_BUCKET")
    region = env.get("SYMGOV_S3_REGION") or "us-east-1"
    access_key_id = env.get("SYMGOV_S3_ACCESS_KEY_ID")
    secret_access_key = env.get("SYMGOV_S3_SECRET_ACCESS_KEY")
    if not endpoint:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_ENDPOINT")
    if not bucket:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_BUCKET")
    if not access_key_id:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_ACCESS_KEY_ID")
    if not secret_access_key:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_SECRET_ACCESS_KEY")
    return {
        "endpoint": endpoint,
        "bucket": bucket,
        "region": region,
        "access_key_id": access_key_id,
        "secret_access_key": secret_access_key,
    }


def _canonical_object_path(endpoint: str, bucket: str, object_key: str) -> str:
    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.hostname:
        raise RuntimeError("SYMGOV_S3_ENDPOINT must be a full URL with scheme and hostname.")

    prefix = parsed.path.rstrip("/")
    key_path = urllib.parse.quote(object_key.lstrip("/"), safe="/-_.~")
    return f"{prefix}/{bucket}/{key_path}" if prefix else f"/{bucket}/{key_path}"


def _aws_v4_sign(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _aws_v4_signing_key(secret_key: str, date_stamp: str, region: str, service: str = "s3") -> bytes:
    date_key = _aws_v4_sign(("AWS4" + secret_key).encode("utf-8"), date_stamp)
    region_key = hmac.new(date_key, region.encode("utf-8"), hashlib.sha256).digest()
    service_key = hmac.new(region_key, service.encode("utf-8"), hashlib.sha256).digest()
    return hmac.new(service_key, b"aws4_request", hashlib.sha256).digest()


def download_object_bytes(
    *,
    object_key: str,
    env_file: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    resolved_env_path, env = read_storage_env_file(env_file)
    settings = _storage_connection_settings(env)
    endpoint = settings["endpoint"]
    bucket = settings["bucket"]
    region = settings["region"]
    access_key_id = settings["access_key_id"]
    secret_access_key = settings["secret_access_key"]

    parsed = urllib.parse.urlparse(endpoint)
    host = parsed.netloc
    canonical_uri = _canonical_object_path(endpoint, bucket, object_key)
    payload_hash = hashlib.sha256(b"").hexdigest()
    request_time = datetime.now(timezone.utc)
    amz_date = request_time.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = request_time.strftime("%Y%m%d")

    canonical_headers = (
        f"host:{host}\n"
        f"x-amz-content-sha256:{payload_hash}\n"
        f"x-amz-date:{amz_date}\n"
    )
    signed_headers = "host;x-amz-content-sha256;x-amz-date"
    canonical_request = "\n".join(
        [
            "GET",
            canonical_uri,
            "",
            canonical_headers,
            signed_headers,
            payload_hash,
        ]
    )
    credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            credential_scope,
            hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
        ]
    )
    signing_key = _aws_v4_signing_key(secret_access_key, date_stamp, region)
    signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key_id}/{credential_scope}, "
        f"SignedHeaders={signed_headers}, "
        f"Signature={signature}"
    )

    request_url = urllib.parse.urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            canonical_uri,
            "",
            "",
            "",
        )
    )
    request = urllib.request.Request(
        request_url,
        method="GET",
        headers={
            "Authorization": authorization,
            "Host": host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        },
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        if not (200 <= response.status < 300):
            raise RuntimeError(f"Storage download failed with HTTP {response.status} for {object_key}")
        payload = response.read()
        content_type = response.headers.get("Content-Type") or "application/octet-stream"
        etag = response.headers.get("ETag")

    return {
        "bucket": bucket,
        "endpoint": endpoint,
        "env_path": str(resolved_env_path),
        "object_key": object_key,
        "payload": payload,
        "content_type": content_type,
        "size_bytes": len(payload),
        "etag": etag,
        "status_code": response.status,
    }


def check_storage_health(env_file: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    resolved_env_path, env = read_storage_env_file(env_file)
    endpoint = env.get("SYMGOV_S3_ENDPOINT")
    if not endpoint:
        raise RuntimeError("Missing required storage setting: SYMGOV_S3_ENDPOINT")

    parsed = urllib.parse.urlparse(endpoint)
    if not parsed.scheme or not parsed.hostname:
        raise RuntimeError("SYMGOV_S3_ENDPOINT must be a full URL with scheme and hostname.")

    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    health_url = urllib.parse.urljoin(endpoint.rstrip("/") + "/", "minio/health/live")
    result: dict[str, Any] = {
        "env_path": str(resolved_env_path),
        "endpoint": endpoint,
        "bucket": env.get("SYMGOV_S3_BUCKET"),
        "region": env.get("SYMGOV_S3_REGION"),
        "access_key_id": env.get("SYMGOV_S3_ACCESS_KEY_ID"),
        "use_ssl": env.get("SYMGOV_S3_USE_SSL"),
        "network_ok": False,
        "healthcheck_ok": False,
    }

    with socket.create_connection((parsed.hostname, port), timeout=5):
        result["network_ok"] = True

    request = urllib.request.Request(health_url, method="GET")
    with urllib.request.urlopen(request, timeout=5) as response:
        result["healthcheck_ok"] = 200 <= response.status < 300
        result["healthcheck_status"] = response.status
        result["healthcheck_url"] = health_url

    return result


def check_database_health(
    env_file: str | os.PathLike[str] | None = None,
    migration: bool = False,
) -> dict[str, Any]:
    session_factory = create_session_factory(env_file=env_file, migration=migration, nopool=True)
    engine = session_factory.kw["bind"]
    assert engine is not None
    parsed = urllib.parse.urlparse(str(engine.url))

    startup_params = (
        b"user\x00" + urllib.parse.unquote(parsed.username or "").encode("utf-8") +
        b"\x00database\x00" + (parsed.path or "/").lstrip("/").encode("utf-8") +
        b"\x00application_name\x00symgov-backend-healthcheck\x00"
        + b"client_encoding\x00UTF8\x00\x00"
    )
    startup = struct.pack("!I", len(startup_params) + 8) + struct.pack("!I", 196608) + startup_params
    with socket.create_connection((parsed.hostname or "localhost", parsed.port or 5432), timeout=5) as conn:
        conn.sendall(startup)
        payload = conn.recv(4096)

    result: dict[str, Any] = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "database": (parsed.path or "/").lstrip("/"),
        "username": urllib.parse.unquote(parsed.username or ""),
        "network_ok": True,
        "postgres_protocol_ok": bool(payload) and chr(payload[0]) == "R",
        "auth_message_type": chr(payload[0]) if payload else None,
        "auth_code": struct.unpack("!I", payload[5:9])[0] if len(payload) >= 9 and chr(payload[0]) == "R" else None,
    }

    with session_factory() as session:
        current_user, current_database = session.execute(text("select current_user, current_database()")).one()
        table_names = (
            "agent_definitions",
            "agent_queue_items",
            "agent_runs",
            "agent_output_artifacts",
            "intake_records",
            "validation_reports",
            "provenance_assessments",
        )
        table_counts = {
            table_name: session.execute(text(f"select count(*) from {table_name}")).scalar_one()
            for table_name in table_names
        }

    result.update(
        {
            "query_ok": True,
            "current_user": current_user,
            "current_database": current_database,
            "table_counts": table_counts,
        }
    )
    return result


class RuntimePersistenceBridge:
    def __init__(self, env_file: str | os.PathLike[str] | None = None, nopool: bool = True):
        # RuntimePersistenceBridge is frequently constructed per-task by agent
        # workers (see agent_queue_worker.py), and the resulting engines are
        # never explicitly disposed. Default to NullPool so each query opens
        # and closes its own connection, preventing idle-connection accumulation
        # against Postgres max_connections. Long-lived callers can pass
        # nopool=False to opt back into the standard pool.
        self.session_factory = create_session_factory(env_file=env_file, nopool=nopool)

    @contextmanager
    def session_scope(self):
        with self.session_factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    def list_review_symbol_property_options(self) -> dict[str, list[str]]:
        with self.session_scope() as session:
            rows = (
                session.query(ReviewSymbolPropertyOption)
                .order_by(
                    ReviewSymbolPropertyOption.field_name.asc(),
                    ReviewSymbolPropertyOption.use_count.desc(),
                    ReviewSymbolPropertyOption.display_value.asc(),
                )
                .all()
            )
            result: dict[str, list[str]] = {"category": [], "discipline": []}
            for row in rows:
                result.setdefault(row.field_name, []).append(row.display_value)
            return result

    def remember_review_symbol_property_option(self, *, field_name: str, value: str | None) -> str | None:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            return remember_property_option(session, field_name=field_name, value=value, now=now)

    def seed_agent_definitions(self) -> list[dict[str, str]]:
        operations: list[dict[str, str]] = []
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            for spec in agent_definition_seeds():
                row = session.query(AgentDefinition).filter_by(slug=spec["slug"]).one_or_none()
                if row is None:
                    row = AgentDefinition(
                        id=coerce_uuid(f"agent-definition:{spec['slug']}"),
                        slug=spec["slug"],
                        display_name=spec["display_name"],
                        role=spec["role"],
                        model=spec["model"],
                        status=spec["status"],
                        queue_family=spec["queue_family"],
                        created_at=now,
                        updated_at=now,
                    )
                    session.add(row)
                    operations.append({"slug": spec["slug"], "action": "inserted"})
                    continue

                changed = False
                for field in ("display_name", "role", "model", "status", "queue_family"):
                    if getattr(row, field) != spec[field]:
                        setattr(row, field, spec[field])
                        changed = True
                row.updated_at = now
                operations.append({"slug": spec["slug"], "action": "updated" if changed else "unchanged"})

        return operations

    def seed_scott_source_discovery_sites(self) -> list[dict[str, str]]:
        operations: list[dict[str, str]] = []
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            for spec in SCOTT_SOURCE_DISCOVERY_SITE_SEEDS:
                domain = str(spec["domain"]).strip().lower()
                row = (
                    session.query(ScottSourceDiscoverySite)
                    .filter(text("lower(domain) = :domain"))
                    .params(domain=domain)
                    .one_or_none()
                )
                if row is None:
                    row = ScottSourceDiscoverySite(
                        id=coerce_uuid(f"scott-source-discovery-site:{domain}"),
                        domain=domain,
                        first_seen_at=now,
                        last_seen_at=now,
                    )
                    session.add(row)
                    action = "inserted"
                else:
                    action = "updated"

                changed = False
                for field in (
                    "url",
                    "title",
                    "description",
                    "industry",
                    "process",
                    "organization_type",
                    "source_prompt",
                    "status",
                ):
                    if getattr(row, field) != spec[field]:
                        setattr(row, field, spec[field])
                        changed = True

                if bool(row.include_next_run) != bool(spec.get("include_next_run", False)):
                    row.include_next_run = bool(spec.get("include_next_run", False))
                    changed = True
                if list(row.symbol_formats_json or []) != list(spec["symbol_formats_json"]):
                    row.symbol_formats_json = list(spec["symbol_formats_json"])
                    changed = True
                if dict(row.evidence_json or {}) != dict(spec["evidence_json"]):
                    row.evidence_json = dict(spec["evidence_json"])
                    changed = True
                if row.relevance_score != spec["relevance_score"]:
                    row.relevance_score = spec["relevance_score"]
                    changed = True

                row.last_seen_at = now
                operations.append({"domain": domain, "action": action if action == "inserted" else ("updated" if changed else "unchanged")})

        return operations

    def persist_hannah_curation_report(self, durable_record: dict[str, Any]) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        completed_at = parse_timestamp(durable_record.get("completed_at")) if durable_record.get("completed_at") else now
        queue_item_id = coerce_uuid(durable_record.get("queue_item_id"))
        candidate_results: list[dict[str, Any]] = []
        state_results: list[dict[str, Any]] = []
        metadata_results: list[dict[str, Any]] = []

        with self.session_scope() as session:
            symbol_states: dict[uuid.UUID, HannahSymbolCurationState] = {}
            for attempt in durable_record.get("symbol_attempts") or []:
                symbol_id = coerce_uuid(attempt.get("symbol_id"))
                if symbol_id is None:
                    continue
                photo_count = int(attempt.get("photo_count") or 0)
                state = resolve_hannah_symbol_curation_state(session, symbol_states, symbol_id, completed_at)
                state.status = attempt.get("status") or state.status or "attempted"
                state.attempt_count = int(state.attempt_count or 0) + 1
                state.photo_count = photo_count
                state.last_attempt_at = completed_at
                if photo_count:
                    state.last_success_at = completed_at
                state.notes_json = attempt.get("notes") or state.notes_json or {}
                state.updated_at = completed_at
                state_results.append({"symbol_id": str(symbol_id), "status": state.status})

                for update in attempt.get("metadata_updates") or []:
                    field = update.get("field")
                    value = str(update.get("value") or "").strip()
                    if field not in {"canonical_name", "category", "discipline"} or not value:
                        continue
                    symbol = session.get(GovernedSymbol, symbol_id)
                    if symbol is None:
                        continue
                    before = getattr(symbol, field)
                    if before == value:
                        continue
                    setattr(symbol, field, value)
                    symbol.updated_at = completed_at
                    session.add(
                        AuditEvent(
                            id=uuid.uuid4(),
                            entity_type="governed_symbol",
                            entity_id=symbol_id,
                            action="hannah_metadata_updated",
                            actor_id=None,
                            payload_json={
                                "field": field,
                                "before": before,
                                "after": value,
                                "queue_item_id": str(queue_item_id) if queue_item_id else None,
                            },
                            created_at=completed_at,
                        )
                    )
                    metadata_results.append({"symbol_id": str(symbol_id), "field": field, "action": "updated"})

            for candidate in durable_record.get("candidates") or []:
                symbol_id = coerce_uuid(candidate.get("symbol_id"))
                image_url = str(candidate.get("image_url") or "").strip()
                if symbol_id is None or not image_url:
                    continue
                row = session.query(HannahPhotoCandidate).filter_by(symbol_id=symbol_id, image_url=image_url).one_or_none()
                action = "updated"
                if row is None:
                    row = HannahPhotoCandidate(
                        id=coerce_uuid(candidate.get("id")) or uuid.uuid4(),
                        symbol_id=symbol_id,
                        image_url=image_url,
                        first_seen_at=completed_at,
                    )
                    session.add(row)
                    action = "inserted"

                row.symbol_revision_id = coerce_uuid(candidate.get("symbol_revision_id"))
                row.published_page_id = coerce_uuid(candidate.get("published_page_id"))
                row.queue_item_id = queue_item_id
                row.source_url = candidate.get("source_url") or image_url
                row.source_domain = candidate.get("source_domain") or "unknown"
                row.title = candidate.get("title")
                row.description = candidate.get("description")
                row.rights_status = candidate.get("rights_status") or "unknown"
                row.license_label = candidate.get("license_label")
                row.status = candidate.get("status") or "candidate"
                row.relevance_score = coerce_numeric(candidate.get("relevance_score"))
                row.attachment_id = coerce_uuid(candidate.get("attachment_id"))
                row.object_key = candidate.get("object_key")
                row.evidence_json = candidate.get("evidence") or {}
                row.last_seen_at = completed_at
                candidate_results.append({"id": str(row.id), "symbol_id": str(symbol_id), "status": row.status, "action": action})

                if row.status == "attached" and row.object_key:
                    session.add(
                        AuditEvent(
                            id=uuid.uuid4(),
                            entity_type="governed_symbol",
                            entity_id=symbol_id,
                            action="hannah_supplemental_photo_attached",
                            actor_id=None,
                            payload_json={
                                "candidate_id": str(row.id),
                                "object_key": row.object_key,
                                "source_url": row.source_url,
                                "license_label": row.license_label,
                                "queue_item_id": str(queue_item_id) if queue_item_id else None,
                            },
                            created_at=completed_at,
                        )
                    )

        return {
            "candidate_count": len(candidate_results),
            "state_count": len(state_results),
            "metadata_update_count": len(metadata_results),
            "candidates": candidate_results,
            "states": state_results,
            "metadata_updates": metadata_results,
        }

    def upsert_external_identity(
        self,
        *,
        display_name: str,
        email: str | None = None,
        organization: str | None = None,
        identity_type: str = "submitter",
        status: str = "active",
    ) -> dict[str, str]:
        normalized_email = email.strip().lower() if email else None
        now = datetime.now(timezone.utc).replace(microsecond=0)

        with self.session_scope() as session:
            row = None
            if normalized_email:
                row = (
                    session.query(ExternalIdentity)
                    .filter(text("lower(email) = :email"))
                    .params(email=normalized_email)
                    .one_or_none()
                )

            if row is None:
                row = ExternalIdentity(
                    id=uuid.uuid4(),
                    display_name=display_name,
                    email=normalized_email,
                    organization=organization,
                    identity_type=identity_type,
                    status=status,
                    created_at=now,
                    updated_at=now,
                )
                session.add(row)
                action = "inserted"
            else:
                row.display_name = display_name
                row.email = normalized_email
                row.organization = organization
                row.identity_type = identity_type
                row.status = status
                row.updated_at = now
                action = "updated"

            session.flush()
            return {
                "id": str(row.id),
                "display_name": row.display_name,
                "email": row.email or "",
                "action": action,
            }

    def create_attachment(
        self,
        *,
        parent_type: str,
        parent_id: str | uuid.UUID,
        filename: str,
        object_key: str,
        content_type: str,
        size_bytes: int,
        sha256: str | None = None,
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        parent_uuid = coerce_uuid(parent_id)

        # Idempotency guard: retries can attempt to re-create the same object_key.
        # Reuse the existing attachment row when one already exists for that key.
        with self.session_scope() as session:
            existing = session.query(Attachment).filter(Attachment.object_key == object_key).one_or_none()
            if existing is not None:
                return {
                    "id": str(existing.id),
                    "object_key": existing.object_key,
                    "filename": existing.filename,
                }

            attachment_id = uuid.uuid4()
            row = Attachment(
                id=attachment_id,
                parent_type=parent_type,
                parent_id=parent_uuid,
                filename=filename,
                object_key=object_key,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                created_at=now,
            )
            session.add(row)
            try:
                session.flush()
            except IntegrityError:
                # Race-safe fallback: another worker inserted the same object_key.
                session.rollback()
                existing = session.query(Attachment).filter(Attachment.object_key == object_key).one_or_none()
                if existing is None:
                    raise
                return {
                    "id": str(existing.id),
                    "object_key": existing.object_key,
                    "filename": existing.filename,
                }

        return {
            "id": str(attachment_id),
            "object_key": object_key,
            "filename": filename,
        }

    def upload_object_bytes(
        self,
        *,
        object_key: str,
        payload: bytes,
        content_type: str,
        env_file: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        resolved_env_path, env = read_storage_env_file(env_file)
        settings = _storage_connection_settings(env)
        endpoint = settings["endpoint"]
        bucket = settings["bucket"]
        region = settings["region"]
        access_key_id = settings["access_key_id"]
        secret_access_key = settings["secret_access_key"]

        parsed = urllib.parse.urlparse(endpoint)
        host = parsed.netloc
        canonical_uri = _canonical_object_path(endpoint, bucket, object_key)
        payload_hash = hashlib.sha256(payload).hexdigest()
        request_time = datetime.now(timezone.utc)
        amz_date = request_time.strftime("%Y%m%dT%H%M%SZ")
        date_stamp = request_time.strftime("%Y%m%d")

        canonical_headers = (
            f"host:{host}\n"
            f"x-amz-content-sha256:{payload_hash}\n"
            f"x-amz-date:{amz_date}\n"
        )
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_request = "\n".join(
            [
                "PUT",
                canonical_uri,
                "",
                canonical_headers,
                signed_headers,
                payload_hash,
            ]
        )
        credential_scope = f"{date_stamp}/{region}/s3/aws4_request"
        string_to_sign = "\n".join(
            [
                "AWS4-HMAC-SHA256",
                amz_date,
                credential_scope,
                hashlib.sha256(canonical_request.encode("utf-8")).hexdigest(),
            ]
        )
        signing_key = _aws_v4_signing_key(secret_access_key, date_stamp, region)
        signature = hmac.new(signing_key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
        authorization = (
            "AWS4-HMAC-SHA256 "
            f"Credential={access_key_id}/{credential_scope}, "
            f"SignedHeaders={signed_headers}, "
            f"Signature={signature}"
        )

        request_url = urllib.parse.urlunparse(
            (
                parsed.scheme,
                parsed.netloc,
                canonical_uri,
                "",
                "",
                "",
            )
        )
        request = urllib.request.Request(
            request_url,
            data=payload,
            method="PUT",
            headers={
                "Authorization": authorization,
                "Content-Length": str(len(payload)),
                "Content-Type": content_type,
                "Host": host,
                "x-amz-content-sha256": payload_hash,
                "x-amz-date": amz_date,
            },
        )
        with urllib.request.urlopen(request, timeout=15) as response:
            if not (200 <= response.status < 300):
                raise RuntimeError(f"Storage upload failed with HTTP {response.status} for {object_key}")
            etag = response.headers.get("ETag")

        return {
            "bucket": bucket,
            "endpoint": endpoint,
            "env_path": str(resolved_env_path),
            "object_key": object_key,
            "content_type": content_type,
            "size_bytes": len(payload),
            "etag": etag,
            "status_code": response.status,
        }

    def upload_file(
        self,
        *,
        object_key: str,
        path: str | os.PathLike[str],
        content_type: str,
        env_file: str | os.PathLike[str] | None = None,
    ) -> dict[str, Any]:
        file_path = Path(path)
        return self.upload_object_bytes(
            object_key=object_key,
            payload=file_path.read_bytes(),
            content_type=content_type,
            env_file=env_file,
        )

    def create_audit_event(
        self,
        *,
        entity_type: str,
        entity_id: str | uuid.UUID,
        action: str,
        payload_json: dict[str, Any],
        actor_id: str | uuid.UUID | None = None,
    ) -> dict[str, str]:
        event_id = uuid.uuid4()
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = AuditEvent(
                id=event_id,
                entity_type=entity_type,
                entity_id=coerce_uuid(entity_id),
                action=action,
                actor_id=coerce_uuid(actor_id),
                payload_json=payload_json,
                created_at=now,
            )
            session.add(row)
        return {"id": str(event_id), "action": action}

    def create_agent_output_artifact(
        self,
        *,
        queue_item_id: str | uuid.UUID,
        artifact_type: str,
        schema_version: str,
        payload_json: dict[str, Any],
        created_at: str | datetime | None = None,
    ) -> dict[str, str]:
        artifact_id = uuid.uuid4()
        created_value = created_at if isinstance(created_at, datetime) else parse_timestamp(created_at) if created_at else datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = AgentOutputArtifact(
                id=artifact_id,
                queue_item_id=coerce_uuid(queue_item_id),
                artifact_type=artifact_type,
                schema_version=schema_version,
                payload_json=payload_json,
                created_at=created_value,
            )
            session.add(row)
        return {"id": str(artifact_id), "artifact_type": artifact_type}

    def upsert_agent_queue_item(self, queue_item: dict[str, Any]) -> dict[str, str]:
        with self.session_scope() as session:
            agent_definition = session.query(AgentDefinition).filter_by(slug=queue_item["agent_id"]).one_or_none()
            if agent_definition is None:
                raise RuntimeError(f"Missing agent_definitions row for slug {queue_item['agent_id']}.")

            queue_item_id = coerce_uuid(queue_item["id"])
            row = session.get(AgentQueueItem, queue_item_id)
            if row is None:
                row = AgentQueueItem(id=queue_item_id)
                session.add(row)

            row.agent_id = agent_definition.id
            row.source_type = queue_item["source_type"]
            row.source_id = coerce_uuid(queue_item["source_id"])
            row.status = queue_item["status"]
            row.priority = queue_item["priority"]
            row.payload_json = queue_item["payload_json"]
            row.confidence = coerce_numeric(queue_item.get("confidence"))
            row.escalation_reason = queue_item.get("escalation_reason")
            row.created_at = parse_timestamp(queue_item.get("created_at"))
            row.started_at = parse_timestamp(queue_item["started_at"]) if queue_item.get("started_at") else None
            row.completed_at = parse_timestamp(queue_item["completed_at"]) if queue_item.get("completed_at") else None

        return {"id": str(queue_item_id), "agent_slug": queue_item["agent_id"], "status": queue_item["status"]}

    def create_review_case(
        self,
        *,
        source_entity_type: str,
        source_entity_id: str | uuid.UUID,
        current_stage: str,
        escalation_level: str,
        owner_id: str | uuid.UUID | None = None,
        opened_at: str | datetime | None = None,
    ) -> dict[str, str]:
        review_case_id = uuid.uuid4()
        opened_value = opened_at if isinstance(opened_at, datetime) else parse_timestamp(opened_at) if opened_at else datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = ReviewCase(
                id=review_case_id,
                source_entity_type=source_entity_type,
                source_entity_id=coerce_uuid(source_entity_id),
                current_stage=current_stage,
                owner_id=coerce_uuid(owner_id),
                escalation_level=escalation_level,
                opened_at=opened_value,
                closed_at=None,
            )
            session.add(row)
        return {
            "id": str(review_case_id),
            "source_entity_type": source_entity_type,
            "source_entity_id": str(coerce_uuid(source_entity_id)),
            "current_stage": current_stage,
            "escalation_level": escalation_level,
        }

    def update_review_case(
        self,
        *,
        review_case_id: str | uuid.UUID,
        current_stage: str | None = None,
        escalation_level: str | None = None,
        source_entity_type: str | None = None,
        source_entity_id: str | uuid.UUID | None = None,
    ) -> dict[str, str]:
        with self.session_scope() as session:
            row = session.get(ReviewCase, coerce_uuid(review_case_id))
            if row is None:
                raise RuntimeError(f"Missing review_cases row for id {review_case_id}.")
            if current_stage is not None:
                row.current_stage = current_stage
            if escalation_level is not None:
                row.escalation_level = escalation_level
            if source_entity_type is not None:
                row.source_entity_type = source_entity_type
            if source_entity_id is not None:
                row.source_entity_id = coerce_uuid(source_entity_id)
            session.flush()
            return {
                "id": str(row.id),
                "source_entity_type": row.source_entity_type,
                "source_entity_id": str(row.source_entity_id),
                "current_stage": row.current_stage,
                "escalation_level": row.escalation_level,
            }

    def return_review_split_item_for_review(
        self,
        *,
        review_case_id: str | uuid.UUID,
        child_key: str,
        attachment_object_key: str,
        payload_updates: dict[str, Any] | None = None,
        latest_note: str | None = None,
        latest_details: str | None = None,
    ) -> dict[str, str]:
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = (
                session.query(ReviewSplitItem)
                .filter(
                    ReviewSplitItem.review_case_id == coerce_uuid(review_case_id),
                    ReviewSplitItem.child_key == child_key,
                )
                .one_or_none()
            )
            if row is None:
                raise RuntimeError(f"Missing review_split_items row for case {review_case_id} child {child_key}.")
            row.attachment_object_key = attachment_object_key
            row.file_name = Path(attachment_object_key).name
            row.status = "returned_for_review"
            row.latest_action = None
            row.latest_note = latest_note
            row.latest_details = latest_details
            row.downstream_agent_slug = None
            row.downstream_queue_item_id = None
            row.processed_at = None
            row.updated_at = now
            row.payload_json = {**(row.payload_json or {}), **(payload_updates or {})}
            return {
                "id": str(row.id),
                "review_case_id": str(row.review_case_id),
                "child_key": row.child_key,
                "attachment_object_key": row.attachment_object_key,
                "status": row.status,
            }

    def dispose_review_split_item(
        self,
        *,
        review_case_id: str | uuid.UUID,
        child_key: str,
        disposition: str,
        latest_note: str | None = None,
        latest_details: str | None = None,
        payload_updates: dict[str, Any] | None = None,
    ) -> dict[str, str]:
        normalized_disposition = "deleted" if disposition == "deleted" else "rejected"
        now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = (
                session.query(ReviewSplitItem)
                .filter(
                    ReviewSplitItem.review_case_id == coerce_uuid(review_case_id),
                    ReviewSplitItem.child_key == child_key,
                )
                .one_or_none()
            )
            if row is None:
                raise RuntimeError(f"Missing review_split_items row for case {review_case_id} child {child_key}.")
            row.status = normalized_disposition
            row.latest_action = normalized_disposition
            row.latest_note = latest_note
            row.latest_details = latest_details
            row.downstream_agent_slug = None
            row.downstream_queue_item_id = None
            row.processed_at = now
            row.updated_at = now
            row.payload_json = {
                **(row.payload_json or {}),
                "libby_disposition": {
                    "disposition": normalized_disposition,
                    "note": latest_note,
                    "details": latest_details,
                    "recorded_at": now.isoformat().replace("+00:00", "Z"),
                },
                **(payload_updates or {}),
            }
            session.add(
                AuditEvent(
                    entity_type="review_split_item",
                    entity_id=row.id,
                    action=f"libby_split_item_{normalized_disposition}",
                    actor_id=None,
                    payload_json={
                        "review_case_id": str(row.review_case_id),
                        "child_key": row.child_key,
                        "note": latest_note,
                        "details": latest_details,
                    },
                    created_at=now,
                )
            )
            return {
                "id": str(row.id),
                "review_case_id": str(row.review_case_id),
                "child_key": row.child_key,
                "status": row.status,
            }

    def resolve_duplicate_review_split_item(
        self,
        *,
        review_case_id: str | uuid.UUID,
        duplicate_resolution: dict[str, Any],
        queue_item_id: str | None = None,
        review_decision_id: str | uuid.UUID | None = None,
    ) -> dict[str, str]:
        outcome = str(duplicate_resolution.get("outcome") or "").strip()
        if outcome not in {"duplicate_confirmed", "needs_human_review"}:
            raise RuntimeError(f"Unsupported Libby duplicate-resolution outcome: {outcome or 'missing'}.")

        now = datetime.now(timezone.utc).replace(microsecond=0)
        split_item_id = duplicate_resolution.get("duplicate_split_item_id")
        candidate_revision_id = duplicate_resolution.get("candidate_revision_id")
        with self.session_scope() as session:
            query = session.query(ReviewSplitItem).filter(ReviewSplitItem.review_case_id == coerce_uuid(review_case_id))
            if split_item_id:
                row = query.filter(ReviewSplitItem.id == coerce_uuid(split_item_id)).one_or_none()
            elif review_decision_id:
                row = query.filter(ReviewSplitItem.latest_decision_id == coerce_uuid(review_decision_id)).one_or_none()
            elif candidate_revision_id:
                row = (
                    query.filter(ReviewSplitItem.payload_json["published_symbol_revision_id"].astext == str(candidate_revision_id))
                    .one_or_none()
                )
            else:
                row = None
            if row is None:
                raise RuntimeError(f"Missing review_split_items row for Libby duplicate resolution in case {review_case_id}.")

            if outcome == "duplicate_confirmed":
                row.status = "duplicate_resolved"
                row.latest_action = "duplicate_confirmed"
                row.downstream_agent_slug = None
                row.downstream_queue_item_id = None
                row.processed_at = now
                audit_action = "libby_duplicate_confirmed"
            else:
                row.status = "duplicate_exception"
                row.latest_action = "needs_human_review"
                row.downstream_agent_slug = "daisy"
                row.downstream_queue_item_id = None
                row.processed_at = None
                audit_action = "libby_duplicate_exception_escalated"

            row.latest_note = duplicate_resolution.get("recommended_action")
            row.latest_details = duplicate_resolution.get("reason")
            row.updated_at = now
            row.payload_json = {
                **(row.payload_json or {}),
                "libby_duplicate_resolution": {
                    **duplicate_resolution,
                    "queue_item_id": queue_item_id,
                    "review_decision_id": str(review_decision_id) if review_decision_id else None,
                    "recorded_at": now.isoformat().replace("+00:00", "Z"),
                },
            }
            if review_decision_id:
                row.latest_decision_id = coerce_uuid(review_decision_id)

            action_id = uuid.uuid4()
            session.add(
                ReviewCaseAction(
                    id=action_id,
                    review_case_id=coerce_uuid(review_case_id),
                    decision_id=coerce_uuid(review_decision_id) if review_decision_id else None,
                    action_code=audit_action,
                    action_status="completed" if outcome == "duplicate_confirmed" else "queued",
                    assigned_to=None,
                    target_agent_slug=None if outcome == "duplicate_confirmed" else "daisy",
                    target_stage=None if outcome == "duplicate_confirmed" else "duplicate_exception_review",
                    action_payload_json={
                        "queue_item_id": queue_item_id,
                        "review_split_item_id": str(row.id),
                        "duplicate_resolution": duplicate_resolution,
                    },
                    created_by_type="agent",
                    created_by_id=None,
                    created_at=now,
                    started_at=now,
                    completed_at=now if outcome == "duplicate_confirmed" else None,
                )
            )
            session.add(
                AuditEvent(
                    entity_type="review_split_item",
                    entity_id=row.id,
                    action=audit_action,
                    actor_id=None,
                    payload_json={
                        "review_case_id": str(row.review_case_id),
                        "queue_item_id": queue_item_id,
                        "duplicate_resolution": duplicate_resolution,
                    },
                    created_at=now,
                )
            )
            return {
                "id": str(row.id),
                "review_case_id": str(row.review_case_id),
                "child_key": row.child_key,
                "status": row.status,
                "action_id": str(action_id),
            }

    def create_control_exception(
        self,
        *,
        source_type: str,
        source_id: str | uuid.UUID,
        severity: str,
        rule_code: str,
        detail: str,
        status: str = "open",
        created_at: str | datetime | None = None,
    ) -> dict[str, str]:
        exception_id = uuid.uuid4()
        created_value = created_at if isinstance(created_at, datetime) else parse_timestamp(created_at) if created_at else datetime.now(timezone.utc).replace(microsecond=0)
        with self.session_scope() as session:
            row = ControlException(
                id=exception_id,
                source_type=source_type,
                source_id=coerce_uuid(source_id),
                severity=severity,
                rule_code=rule_code,
                detail=detail,
                status=status,
                created_at=created_value,
                updated_at=created_value,
            )
            session.add(row)
        return {"id": str(exception_id), "rule_code": rule_code}

    def ensure_publication_service_user(self, session) -> User:
        service_email = "symgov-publication-service@symgov.local"
        row = (
            session.query(User)
            .filter(text("lower(email) = :email"))
            .params(email=service_email)
            .one_or_none()
        )
        now = datetime.now(timezone.utc).replace(microsecond=0)
        if row is None:
            row = User(
                id=coerce_uuid("user:symgov-publication-service"),
                email=service_email,
                display_name="SymGov Publication Service",
                role="standards_owner",
                created_at=now,
            )
            session.add(row)
            session.flush()
        return row

    def generate_published_page_code(
        self,
        *,
        symbol_slug: str,
        revision_label: str,
        pack_code: str,
    ) -> str:
        return "-".join(
            [
                slugify_public_code(symbol_slug),
                slugify_public_code(revision_label),
                slugify_public_code(pack_code),
            ]
        )

    def _publication_pack_from_artifact(self, artifact: dict[str, Any]) -> dict[str, Any]:
        publication_pack = artifact.get("publication_pack") or {}
        return {
            "pack_code": publication_pack.get("pack_code") or f"pack-{slugify_public_code(artifact.get('release_target'))}",
            "title": publication_pack.get("title") or f"Publication pack {artifact.get('release_target') or 'current'}",
            "audience": publication_pack.get("audience") or "public",
            "effective_date": publication_pack.get("effective_date") or datetime.now(timezone.utc).date().isoformat(),
            "status": publication_pack.get("status") or "published",
        }

    def persist_publication_execution(
        self,
        queue_item: dict[str, Any],
        run_record: dict[str, Any],
        output_artifact_record: dict[str, Any],
        publication_report: dict[str, Any],
    ) -> dict[str, Any]:
        artifact = output_artifact_record["payload_json"]
        if queue_item.get("agent_id") != "rupert":
            raise RuntimeError("Publication persistence only supports Rupert queue items.")
        if queue_item.get("status") != "completed" or artifact.get("decision") != "stage":
            raise RuntimeError("Only successfully staged Rupert publication handoffs can be persisted.")

        payload = queue_item.get("payload_json") or {}
        if payload.get("simulation"):
            raise RuntimeError("Refusing to persist simulated Rupert publication handoff.")
        if payload.get("human_decision") != "approve" or not payload.get("human_approved"):
            raise RuntimeError("Publication persistence requires explicit human approval.")

        revision_ids = [coerce_uuid(item) for item in artifact.get("staged_symbol_revisions") or []]
        if not revision_ids:
            raise RuntimeError("Publication persistence requires at least one symbol revision.")

        pack_spec = self._publication_pack_from_artifact(artifact)
        effective_date = parse_timestamp(str(pack_spec["effective_date"]) + "T00:00:00Z").date()
        completed_at = parse_timestamp(queue_item.get("completed_at")) if queue_item.get("completed_at") else datetime.now(timezone.utc).replace(microsecond=0)
        started_at = parse_timestamp(queue_item.get("started_at")) if queue_item.get("started_at") else completed_at
        created_at = parse_timestamp(queue_item.get("created_at")) if queue_item.get("created_at") else started_at

        with self.session_scope() as session:
            service_user = self.ensure_publication_service_user(session)
            agent_definition = session.query(AgentDefinition).filter_by(slug="rupert").one_or_none()
            if agent_definition is None:
                raise RuntimeError("Missing agent_definitions row for Rupert.")

            queue_item_id = coerce_uuid(queue_item["id"])
            agent_queue_item = session.get(AgentQueueItem, queue_item_id)
            if agent_queue_item is None:
                agent_queue_item = AgentQueueItem(id=queue_item_id)
                session.add(agent_queue_item)
            agent_queue_item.agent_id = agent_definition.id
            agent_queue_item.source_type = queue_item["source_type"]
            agent_queue_item.source_id = coerce_uuid(queue_item["source_id"])
            agent_queue_item.status = queue_item["status"]
            agent_queue_item.priority = queue_item["priority"]
            agent_queue_item.payload_json = queue_item["payload_json"]
            agent_queue_item.confidence = coerce_numeric(queue_item.get("confidence"))
            agent_queue_item.escalation_reason = queue_item.get("escalation_reason")
            agent_queue_item.created_at = created_at
            agent_queue_item.started_at = started_at
            agent_queue_item.completed_at = completed_at
            session.flush()

            agent_run = session.get(AgentRun, coerce_uuid(run_record["id"]))
            if agent_run is None:
                agent_run = AgentRun(id=coerce_uuid(run_record["id"]))
                session.add(agent_run)
            agent_run.queue_item_id = queue_item_id
            agent_run.model = run_record["model"]
            agent_run.prompt_version = run_record["prompt_version"]
            agent_run.tool_trace_json = run_record["tool_trace_json"]
            agent_run.result_status = run_record["result_status"]
            agent_run.started_at = parse_timestamp(run_record["started_at"])
            agent_run.completed_at = parse_timestamp(run_record["completed_at"])

            output_artifact = session.get(AgentOutputArtifact, coerce_uuid(output_artifact_record["id"]))
            if output_artifact is None:
                output_artifact = AgentOutputArtifact(id=coerce_uuid(output_artifact_record["id"]))
                session.add(output_artifact)
            output_artifact.queue_item_id = queue_item_id
            output_artifact.artifact_type = output_artifact_record["artifact_type"]
            output_artifact.schema_version = output_artifact_record["schema_version"]
            output_artifact.payload_json = artifact
            output_artifact.created_at = parse_timestamp(output_artifact_record["created_at"])

            publication_pack = session.query(PublicationPack).filter_by(pack_code=pack_spec["pack_code"]).one_or_none()
            if publication_pack is None:
                publication_pack = PublicationPack(
                    id=uuid.uuid4(),
                    pack_code=pack_spec["pack_code"],
                    title=pack_spec["title"],
                    audience=pack_spec["audience"],
                    effective_date=effective_date,
                    status="published",
                    created_at=completed_at,
                    updated_at=completed_at,
                )
                session.add(publication_pack)
            else:
                publication_pack.title = pack_spec["title"]
                publication_pack.audience = pack_spec["audience"]
                publication_pack.effective_date = effective_date
                publication_pack.status = "published"
                publication_pack.updated_at = completed_at
            session.flush()

            publication_job_id = coerce_uuid(f"publication-job:{queue_item['id']}")
            publication_job = session.get(PublicationJob, publication_job_id)
            if publication_job is None:
                publication_job = PublicationJob(id=publication_job_id)
                session.add(publication_job)
            publication_job.pack_id = publication_pack.id
            publication_job.status = "completed"
            publication_job.requested_by = service_user.id
            publication_job.approved_by = service_user.id
            publication_job.artifact_manifest_json = {
                "queue_item_id": queue_item["id"],
                "run_id": run_record["id"],
                "artifact_id": output_artifact_record["id"],
                "publication_report_id": publication_report["id"],
                "release_manifest_path": artifact.get("release_manifest_path"),
                "release_target": artifact.get("release_target"),
                "standards_availability_summary": artifact.get("standards_availability_summary") or {},
                "simulation": False,
            }
            publication_job.created_at = created_at
            publication_job.completed_at = completed_at

            published_pages: list[dict[str, str]] = []
            pack_entries: list[dict[str, str]] = []
            for sort_order, revision_id in enumerate(revision_ids, start=1):
                revision = session.get(SymbolRevision, revision_id)
                if revision is None:
                    raise RuntimeError(f"Missing symbol_revisions row for id {revision_id}.")
                if revision.lifecycle_state not in {"approved", "published"}:
                    raise RuntimeError(
                        f"Symbol revision {revision_id} must be approved before publication; found {revision.lifecycle_state}."
                    )
                symbol = session.get(GovernedSymbol, revision.symbol_id)
                if symbol is None:
                    raise RuntimeError(f"Missing governed_symbols row for revision {revision_id}.")

                page_code = self.generate_published_page_code(
                    symbol_slug=symbol.slug,
                    revision_label=revision.revision_label,
                    pack_code=publication_pack.pack_code,
                )
                page_title = f"{symbol.canonical_name} ({revision.revision_label})"
                published_page = session.query(PublishedPage).filter_by(page_code=page_code).one_or_none()
                if published_page is None:
                    published_page = PublishedPage(
                        id=uuid.uuid4(),
                        page_code=page_code,
                        title=page_title,
                        pack_id=publication_pack.id,
                        current_symbol_revision_id=revision.id,
                        effective_date=effective_date,
                        created_at=completed_at,
                        updated_at=completed_at,
                    )
                    session.add(published_page)
                else:
                    published_page.title = page_title
                    published_page.pack_id = publication_pack.id
                    published_page.current_symbol_revision_id = revision.id
                    published_page.effective_date = effective_date
                    published_page.updated_at = completed_at
                session.flush()

                pack_entry = (
                    session.query(PackEntry)
                    .filter_by(
                        pack_id=publication_pack.id,
                        symbol_revision_id=revision.id,
                        published_page_id=published_page.id,
                    )
                    .one_or_none()
                )
                if pack_entry is None:
                    pack_entry = PackEntry(
                        id=uuid.uuid4(),
                        pack_id=publication_pack.id,
                        symbol_revision_id=revision.id,
                        published_page_id=published_page.id,
                        sort_order=sort_order,
                        created_at=completed_at,
                    )
                    session.add(pack_entry)
                else:
                    pack_entry.sort_order = sort_order

                revision.lifecycle_state = "published"
                symbol.current_revision_id = revision.id
                symbol.updated_at = completed_at
                published_pages.append(
                    {
                        "id": str(published_page.id),
                        "page_code": page_code,
                        "symbol_revision_id": str(revision.id),
                    }
                )
                pack_entries.append(
                    {
                        "id": str(pack_entry.id),
                        "symbol_revision_id": str(revision.id),
                        "published_page_id": str(published_page.id),
                    }
                )

            audit_payload = {
                "queue_item_id": queue_item["id"],
                "publication_job_id": str(publication_job.id),
                "pack_code": publication_pack.pack_code,
                "published_pages": published_pages,
                "pack_entries": pack_entries,
            }
            for entity_type, entity_id, action in (
                ("publication_pack", publication_pack.id, "publication_pack_published"),
                ("publication_job", publication_job.id, "publication_job_completed"),
            ):
                session.add(
                    AuditEvent(
                        id=uuid.uuid4(),
                        entity_type=entity_type,
                        entity_id=entity_id,
                        action=action,
                        actor_id=service_user.id,
                        payload_json=audit_payload,
                        created_at=completed_at,
                    )
                )
            for page in published_pages:
                session.add(
                    AuditEvent(
                        id=uuid.uuid4(),
                        entity_type="published_page",
                        entity_id=coerce_uuid(page["id"]),
                        action="published_page_upserted",
                        actor_id=service_user.id,
                        payload_json=audit_payload,
                        created_at=completed_at,
                    )
                )

            session.flush()
            session.execute(text("SELECT refresh_published_symbol_views()"))

            return {
                "agent_slug": "rupert",
                "queue_item_id": str(queue_item_id),
                "durable_kind": "publication",
                "publication_job_id": str(publication_job.id),
                "publication_pack_id": str(publication_pack.id),
                "publication_pack_code": publication_pack.pack_code,
                "published_pages": published_pages,
                "pack_entries": pack_entries,
                "published_symbol_views_refreshed": True,
            }

    def persist_agent_execution(
        self,
        queue_item: dict[str, Any],
        run_record: dict[str, Any],
        output_artifact_record: dict[str, Any],
        durable_record: dict[str, Any],
        durable_kind: str,
    ) -> dict[str, str]:
        with self.session_scope() as session:
            agent_definition = session.query(AgentDefinition).filter_by(slug=queue_item["agent_id"]).one_or_none()
            if agent_definition is None:
                raise RuntimeError(f"Missing agent_definitions row for slug {queue_item['agent_id']}.")

            queue_item_id = coerce_uuid(queue_item["id"])
            agent_queue_item = session.get(AgentQueueItem, queue_item_id)
            if agent_queue_item is None:
                agent_queue_item = AgentQueueItem(id=queue_item_id)
                session.add(agent_queue_item)

            agent_queue_item.agent_id = agent_definition.id
            agent_queue_item.source_type = queue_item["source_type"]
            agent_queue_item.source_id = coerce_uuid(queue_item["source_id"])
            agent_queue_item.status = queue_item["status"]
            agent_queue_item.priority = queue_item["priority"]
            agent_queue_item.payload_json = queue_item["payload_json"]
            agent_queue_item.confidence = coerce_numeric(queue_item.get("confidence"))
            agent_queue_item.escalation_reason = queue_item.get("escalation_reason")
            agent_queue_item.created_at = parse_timestamp(queue_item.get("created_at"))
            agent_queue_item.started_at = parse_timestamp(queue_item["started_at"]) if queue_item.get("started_at") else None
            agent_queue_item.completed_at = parse_timestamp(queue_item["completed_at"]) if queue_item.get("completed_at") else None
            session.flush()

            agent_run = session.get(AgentRun, coerce_uuid(run_record["id"]))
            if agent_run is None:
                agent_run = AgentRun(id=coerce_uuid(run_record["id"]))
                session.add(agent_run)
            agent_run.queue_item_id = queue_item_id
            agent_run.model = run_record["model"]
            agent_run.prompt_version = run_record["prompt_version"]
            agent_run.tool_trace_json = run_record["tool_trace_json"]
            agent_run.result_status = run_record["result_status"]
            agent_run.started_at = parse_timestamp(run_record["started_at"])
            agent_run.completed_at = parse_timestamp(run_record["completed_at"])

            output_artifact = session.get(AgentOutputArtifact, coerce_uuid(output_artifact_record["id"]))
            if output_artifact is None:
                output_artifact = AgentOutputArtifact(id=coerce_uuid(output_artifact_record["id"]))
                session.add(output_artifact)
            output_artifact.queue_item_id = queue_item_id
            output_artifact.artifact_type = output_artifact_record["artifact_type"]
            output_artifact.schema_version = output_artifact_record["schema_version"]
            output_artifact.payload_json = output_artifact_record["payload_json"]
            output_artifact.created_at = parse_timestamp(output_artifact_record["created_at"])

            if durable_kind == "intake_record":
                created_at = parse_timestamp(durable_record["created_at"])
                source_package = ensure_source_package_for_intake(session, durable_record, created_at)
                normalized_submission = dict(durable_record["normalized_submission_json"])

                package_sequence = _coerce_positive_int(normalized_submission.get("package_symbol_sequence"))
                if package_sequence is None:
                    package_sequence = next_source_package_sequence(session, source_package.id)

                package_code = source_package.package_code
                display_name = f"{package_code}-{package_sequence}"
                normalized_submission["source_package_code"] = package_code
                normalized_submission["package_display_id"] = package_code
                normalized_submission["package_symbol_sequence"] = package_sequence
                normalized_submission["symbol_display_id"] = display_name
                normalized_submission["workspace_display_name"] = display_name

                queue_payload = dict(agent_queue_item.payload_json or {})
                queue_payload["source_package_code"] = package_code
                queue_payload["package_display_id"] = package_code
                queue_payload["package_symbol_sequence"] = package_sequence
                queue_payload["symbol_display_id"] = display_name
                queue_payload["workspace_display_name"] = display_name
                queue_payload["display_name"] = display_name
                agent_queue_item.payload_json = queue_payload

                durable_record["source_package_id"] = str(source_package.id)
                durable_record["normalized_submission_json"] = normalized_submission

                record = session.get(IntakeRecord, coerce_uuid(durable_record["id"]))
                if record is None:
                    record = IntakeRecord(id=coerce_uuid(durable_record["id"]))
                    session.add(record)
                record.queue_item_id = queue_item_id
                record.source_type = durable_record["source_type"]
                record.source_ref = durable_record["source_ref"]
                record.submitter = durable_record["submitter"]
                record.submission_kind = durable_record["submission_kind"]
                record.intake_status = durable_record["intake_status"]
                record.eligibility_status = durable_record["eligibility_status"]
                record.source_package_id = coerce_uuid(durable_record.get("source_package_id"))
                record.raw_object_key = durable_record.get("raw_object_key")
                record.normalized_submission_json = normalized_submission
                record.routing_recommendation_json = durable_record["routing_recommendation_json"]
                record.report_json = durable_record["report_json"]
                record.created_at = created_at
            elif durable_kind == "validation_report":
                record = session.get(ValidationReport, coerce_uuid(durable_record["id"]))
                if record is None:
                    record = ValidationReport(id=coerce_uuid(durable_record["id"]))
                    session.add(record)
                record.queue_item_id = queue_item_id
                record.source_type = durable_record["source_type"]
                record.source_id = coerce_uuid(durable_record["source_id"])
                record.validation_status = durable_record["validation_status"]
                record.defect_count = durable_record["defect_count"]
                record.normalized_payload_json = durable_record["normalized_payload_json"]
                record.report_json = durable_record["report_json"]
                record.created_at = parse_timestamp(durable_record["created_at"])
            elif durable_kind == "provenance_assessment":
                record = session.get(ProvenanceAssessment, coerce_uuid(durable_record["id"]))
                if record is None:
                    record = ProvenanceAssessment(id=coerce_uuid(durable_record["id"]))
                    session.add(record)
                record.queue_item_id = queue_item_id
                record.intake_record_id = coerce_uuid(durable_record["intake_record_id"])
                record.rights_status = durable_record["rights_status"]
                
                # Canonical state separation with compatibility fallbacks
                if "rights_disposition" in durable_record:
                    record.rights_disposition = durable_record["rights_disposition"]
                else:
                    rs = str(durable_record.get("rights_status") or "unknown").lower()
                    if rs == "cleared":
                        record.rights_disposition = "cleared"
                    elif rs == "unknown":
                        record.rights_disposition = "unknown_warning"
                    elif rs == "restricted":
                        record.rights_disposition = "restricted"
                    elif rs == "conflict":
                        record.rights_disposition = "conflict"
                    else:
                        record.rights_disposition = "failed"

                if "processing_outcome" in durable_record:
                    record.processing_outcome = durable_record["processing_outcome"]
                else:
                    report = durable_record.get("report_json") or {}
                    decision = str(report.get("decision") or "").lower()
                    rs = str(durable_record.get("rights_status") or "unknown").lower()
                    if decision == "pass":
                        record.processing_outcome = "pass"
                    elif rs in {"restricted", "conflict"} or decision == "fail":
                        record.processing_outcome = "failed"
                    else:
                        record.processing_outcome = "review_required"

                record.risk_level = durable_record["risk_level"]
                record.confidence = coerce_numeric(durable_record["confidence"]) or Decimal("0")
                record.summary = durable_record["summary"]
                record.evidence_json = durable_record["evidence_json"]
                record.report_json = durable_record["report_json"]
                record.assessed_at = parse_timestamp(durable_record["assessed_at"])
            elif durable_kind == "classification_record":
                record_id = coerce_uuid(durable_record["id"])
                record = session.get(ClassificationRecord, record_id)
                symbol_key = durable_record.get("symbol_key")
                if record is None:
                    record = ClassificationRecord(id=record_id)
                    session.add(record)

                if durable_record.get("status") == "current":
                    prior_query = session.query(ClassificationRecord).filter(
                        ClassificationRecord.id != record_id,
                        ClassificationRecord.status == "current",
                    )
                    if symbol_key:
                        prior_query = prior_query.filter(ClassificationRecord.symbol_key == symbol_key)
                    else:
                        prior_query = prior_query.filter(
                            ClassificationRecord.source_type == durable_record["source_type"],
                            ClassificationRecord.source_id == coerce_uuid(durable_record["source_id"]),
                        )

                    prior_records = prior_query.all()
                    supersedes_id = durable_record.get("supersedes_classification_id")
                    if supersedes_id is None and prior_records:
                        supersedes_id = str(prior_records[0].id)

                    for prior in prior_records:
                        prior.status = "obsolete"
                        prior.updated_at = parse_timestamp(durable_record.get("updated_at") or durable_record["created_at"])

                    durable_record["supersedes_classification_id"] = supersedes_id

                record.queue_item_id = queue_item_id
                record.intake_record_id = coerce_uuid(durable_record.get("intake_record_id"))
                record.validation_report_id = coerce_uuid(durable_record.get("validation_report_id"))
                record.provenance_assessment_id = coerce_uuid(durable_record.get("provenance_assessment_id"))
                record.review_case_id = coerce_uuid(durable_record.get("review_case_id"))
                record.origin_attachment_id = coerce_uuid(durable_record.get("origin_attachment_id"))
                record.origin_object_key = durable_record.get("origin_object_key")
                record.origin_file_name = durable_record.get("origin_file_name")
                record.origin_batch_id = durable_record.get("origin_batch_id")
                record.parent_review_case_id = coerce_uuid(durable_record.get("parent_review_case_id"))
                record.symbol_key = symbol_key
                record.symbol_region_index = durable_record.get("symbol_region_index")
                record.status = durable_record.get("status") or "current"
                record.classification_status = durable_record.get("classification_status") or "provisional"
                record.supersedes_classification_id = coerce_uuid(durable_record.get("supersedes_classification_id"))
                record.source_id = coerce_uuid(durable_record["source_id"])
                record.source_type = durable_record["source_type"]
                record.category = durable_record["category"]
                record.discipline = durable_record["discipline"]
                record.format = durable_record.get("format")
                record.industry = durable_record.get("industry")
                record.symbol_family = durable_record.get("symbol_family")
                record.process_category = durable_record.get("process_category")
                record.parent_equipment_class = durable_record.get("parent_equipment_class")
                record.standards_source = durable_record.get("standards_source")
                record.library_provenance_class = durable_record.get("library_provenance_class")
                record.source_classification = durable_record.get("source_classification")
                record.aliases_json = durable_record.get("aliases_json") or []
                record.search_terms_json = durable_record.get("search_terms_json") or []
                record.source_refs_json = durable_record.get("source_refs_json") or []
                record.evidence_json = durable_record.get("evidence_json") or {}
                record.taxonomy_terms_created_json = durable_record.get("taxonomy_terms_created_json") or []
                record.review_summary = durable_record.get("review_summary")
                record.confidence = coerce_numeric(durable_record["confidence"]) or Decimal("0")
                record.libby_approved = bool(durable_record.get("libby_approved"))
                record.created_at = parse_timestamp(durable_record["created_at"])
                record.updated_at = parse_timestamp(durable_record.get("updated_at") or durable_record["created_at"])
            elif durable_kind == "review_followup_report":
                pass
            elif durable_kind == "scott_source_discovery":
                completed_at = parse_timestamp(durable_record["completed_at"])
                for site_payload in durable_record.get("sites", []):
                    domain = str(site_payload.get("domain") or "").strip().lower()
                    url = str(site_payload.get("url") or "").strip()
                    if not domain or not url:
                        continue

                    record = (
                        session.query(ScottSourceDiscoverySite)
                        .filter(text("lower(domain) = :domain"))
                        .params(domain=domain)
                        .one_or_none()
                    )
                    if record is None:
                        record = ScottSourceDiscoverySite(
                            id=uuid.uuid4(),
                            domain=domain,
                            first_seen_at=completed_at,
                        )
                        session.add(record)

                    record.url = url
                    record.title = site_payload.get("title")
                    record.description = site_payload.get("description")
                    record.industry = site_payload.get("industry")
                    record.process = site_payload.get("process")
                    record.organization_type = site_payload.get("organization_type")
                    record.symbol_formats_json = site_payload.get("symbol_formats") or []
                    record.evidence_json = site_payload.get("evidence") or {}
                    record.status = normalize_scott_source_discovery_status(site_payload)
                    detected_requires_auth = bool(site_payload.get("requires_auth"))
                    record.requires_auth = bool(record.requires_auth) or detected_requires_auth
                    incoming_auth_status = str(site_payload.get("auth_status") or ("gated_detected" if detected_requires_auth else "no_auth")).strip().lower()
                    if incoming_auth_status not in {"no_auth", "gated_detected", "auth_configured", "auth_verified", "auth_failed"}:
                        incoming_auth_status = "gated_detected" if detected_requires_auth else "no_auth"
                    if incoming_auth_status == "no_auth" and record.requires_auth and str(record.auth_status or "").strip().lower() in {"gated_detected", "auth_configured", "auth_verified", "auth_failed"}:
                        incoming_auth_status = str(record.auth_status or "gated_detected").strip().lower()
                    record.auth_status = incoming_auth_status
                    secret_key = str(site_payload.get("auth_secret_key") or "").strip().upper()
                    if secret_key:
                        record.auth_secret_key = secret_key
                    record.relevance_score = coerce_numeric(site_payload.get("relevance_score"))
                    record.last_seen_at = completed_at
                    record.last_session_queue_item_id = queue_item_id
            elif durable_kind == "hannah_curation_report":
                completed_at = parse_timestamp(durable_record["completed_at"])
                symbol_states: dict[uuid.UUID, HannahSymbolCurationState] = {}
                for attempt in durable_record.get("symbol_attempts") or []:
                    symbol_id = coerce_uuid(attempt.get("symbol_id"))
                    if symbol_id is None:
                        continue
                    state = resolve_hannah_symbol_curation_state(session, symbol_states, symbol_id, completed_at)
                    state.status = attempt.get("status") or "attempted"
                    state.attempt_count = int(state.attempt_count or 0) + 1
                    state.photo_count = int(attempt.get("photo_count") or 0)
                    state.last_attempt_at = completed_at
                    if state.photo_count:
                        state.last_success_at = completed_at
                    state.notes_json = attempt.get("notes") or {}
                    state.updated_at = completed_at

                    for update in attempt.get("metadata_updates") or []:
                        field = update.get("field")
                        value = str(update.get("value") or "").strip()
                        if field not in {"canonical_name", "category", "discipline"} or not value:
                            continue
                        symbol = session.get(GovernedSymbol, symbol_id)
                        if symbol is None:
                            continue
                        before = getattr(symbol, field)
                        if before == value:
                            continue
                        setattr(symbol, field, value)
                        symbol.updated_at = completed_at
                        session.add(
                            AuditEvent(
                                id=uuid.uuid4(),
                                entity_type="governed_symbol",
                                entity_id=symbol_id,
                                action="hannah_metadata_updated",
                                actor_id=None,
                                payload_json={
                                    "field": field,
                                    "before": before,
                                    "after": value,
                                    "queue_item_id": str(queue_item_id),
                                },
                                created_at=completed_at,
                            )
                        )

                for candidate_payload in durable_record.get("candidates") or []:
                    symbol_id = coerce_uuid(candidate_payload.get("symbol_id"))
                    image_url = str(candidate_payload.get("image_url") or "").strip()
                    if symbol_id is None or not image_url:
                        continue
                    record = resolve_hannah_photo_candidate_record(
                        session,
                        candidate_payload,
                        symbol_id,
                        image_url,
                        completed_at,
                    )
                    record.symbol_revision_id = coerce_uuid(candidate_payload.get("symbol_revision_id"))
                    record.published_page_id = coerce_uuid(candidate_payload.get("published_page_id"))
                    record.queue_item_id = queue_item_id
                    record.source_url = candidate_payload.get("source_url") or image_url
                    record.source_domain = candidate_payload.get("source_domain") or "unknown"
                    record.title = candidate_payload.get("title")
                    record.description = candidate_payload.get("description")
                    record.rights_status = candidate_payload.get("rights_status") or "unknown"
                    record.license_label = candidate_payload.get("license_label")
                    record.status = candidate_payload.get("status") or "candidate"
                    record.relevance_score = coerce_numeric(candidate_payload.get("relevance_score"))
                    record.attachment_id = coerce_uuid(candidate_payload.get("attachment_id"))
                    record.object_key = candidate_payload.get("object_key")
                    record.evidence_json = candidate_payload.get("evidence") or {}
                    record.last_seen_at = completed_at
                    if record.status == "attached" and record.object_key:
                        session.add(
                            AuditEvent(
                                id=uuid.uuid4(),
                                entity_type="governed_symbol",
                                entity_id=symbol_id,
                                action="hannah_supplemental_photo_attached",
                                actor_id=None,
                                payload_json={
                                    "candidate_id": str(record.id),
                                    "object_key": record.object_key,
                                    "source_url": record.source_url,
                                    "license_label": record.license_label,
                                    "queue_item_id": str(queue_item_id),
                                },
                                created_at=completed_at,
                            )
                        )
            elif durable_kind == "whitney_market_intelligence_report":
                completed_at = parse_timestamp(durable_record["completed_at"])
                created_at = parse_timestamp(durable_record.get("created_at") or durable_record["completed_at"])
                report_id = coerce_uuid(durable_record["id"])
                report = session.get(WhitneyMarketIntelligenceReport, report_id)
                if report is None:
                    report = WhitneyMarketIntelligenceReport(id=report_id)
                    session.add(report)
                report.queue_item_id = queue_item_id
                report.report_type = durable_record.get("report_type") or "demand_sensing"
                report.status = durable_record.get("status") or "completed"
                report.summary = durable_record.get("summary") or "Whitney demand sensing report completed."
                report.signals_json = durable_record.get("signals") or []
                report.recommendations_json = durable_record.get("recommendations") or []
                report.evidence_json = durable_record.get("evidence") or {}
                report.created_at = created_at
                report.completed_at = completed_at
                session.flush()

                for signal_payload in durable_record.get("signals") or []:
                    source_type = str(signal_payload.get("source_type") or "internal_telemetry")
                    source_ref = str(signal_payload.get("source_ref") or signal_payload.get("id") or "")
                    signal_type = str(signal_payload.get("signal_type") or "demand_signal")
                    signal_id = coerce_uuid(signal_payload.get("id")) or coerce_uuid(
                        f"whitney-demand-signal:{source_type}:{source_ref}:{signal_type}"
                    )
                    signal = session.get(WhitneyDemandSignal, signal_id)
                    if signal is None:
                        signal = (
                            session.query(WhitneyDemandSignal)
                            .filter_by(source_type=source_type, source_ref=source_ref, signal_type=signal_type)
                            .one_or_none()
                        )
                    if signal is None:
                        signal = WhitneyDemandSignal(
                            id=signal_id,
                            first_seen_at=completed_at,
                        )
                        session.add(signal)

                    signal.queue_item_id = queue_item_id
                    signal.report_id = report_id
                    signal.symbol_id = coerce_uuid(signal_payload.get("symbol_id"))
                    signal.published_page_id = coerce_uuid(signal_payload.get("published_page_id"))
                    signal.signal_type = signal_type
                    signal.market_segment = signal_payload.get("market_segment")
                    signal.discipline = signal_payload.get("discipline")
                    signal.category = signal_payload.get("category")
                    signal.source_type = source_type
                    signal.source_ref = source_ref
                    signal.title = signal_payload.get("title") or "Demand signal"
                    signal.summary = signal_payload.get("summary") or ""
                    signal.demand_score = coerce_numeric(signal_payload.get("demand_score"))
                    signal.confidence = coerce_numeric(signal_payload.get("confidence"))
                    signal.recommended_action = signal_payload.get("recommended_action")
                    signal.evidence_json = signal_payload.get("evidence") or {}
                    signal.status = signal_payload.get("status") or "active"
                    signal.last_seen_at = completed_at
            else:
                raise ValueError(f"Unsupported durable_kind: {durable_kind}")

        return {
            "agent_slug": queue_item["agent_id"],
            "queue_item_id": str(queue_item_id),
            "durable_kind": durable_kind,
            "durable_record_id": str(coerce_uuid(durable_record["id"])),
        }
