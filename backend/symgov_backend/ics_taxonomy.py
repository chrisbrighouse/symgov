"""ICS importer and validation for the ISO Open Data lifecycle."""
from __future__ import annotations

import hashlib
import uuid
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

from sqlalchemy.orm import Session
from sqlalchemy import text
from .models.schema import ICSDomainCrosswalk, ICSTaxonomyImport
from .catalog_taxonomy import CATALOG_DISCIPLINE_ORDER
from .classification_schemes import (
    register_classification_scheme,
    add_classification_node,
    get_classification_scheme,
    classification_scheme_seed_id,
    classification_node_seed_id,
    classification_import_id,
    classification_crosswalk_id,
)
from .models import ClassificationScheme, ClassificationNode

SOURCE = json.loads((Path(__file__).parent / "data" / "ics-source.json").read_text())

COLUMNS = ("identifier", "parent", "titleEn", "titleFr", "scopeEn", "scopeFr")
ICS_CODE = re.compile(r"[0-9]{2}(?:\.[0-9]{3}(?:\.[0-9]{2})?)?")
MAX_BYTES = 2_000_000

# These are initial browse suggestions, never semantic equivalences or assignments.
CROSSWALK = (
    ("Electrical", ("29",), "initial_broader", "Clear broader sector; not an exact semantic match."),
    ("Fire & Life Safety", ("13.220", "13"), "needs_review", "Fire protection and wider life-safety scope overlap."),
    ("Piping / P&ID", ("23", "01.100"), "needs_review", "Physical fluid systems differ from drawing conventions."),
    ("Process", ("71", "75", "27"), "needs_review", "Process is cross-sector: chemical, petroleum and energy."),
    ("Instrumentation & Controls", ("17", "25.040.40"), "needs_review", "Measurement and industrial control overlap."),
    ("Mechanical", ("21", "25"), "needs_review", "Mechanical components and manufacturing overlap."),
    ("HVAC", ("91.140.10", "91.140.30", "27"), "needs_review", "Building heating/ventilation and thermal engineering overlap."),
    ("Civil / Structural", ("91", "93"), "needs_review", "Construction materials and civil engineering overlap."),
    ("Architectural", ("91",), "initial_broader", "Clear broader construction sector; not an exact semantic match."),
    ("Safety / Signage", ("13", "01.080"), "needs_review", "Safety sector differs from graphical-sign conventions."),
    ("General / Annotation", ("01.080", "01.100"), "needs_review", "Graphical symbols and technical-drawing annotations overlap."),
)


def parse_csv(content: bytes) -> list[dict[str, str]]:
    """Validate the entire hierarchy before any persistence is possible.

    The ISO official CSV uses double quotes but does not escape internal quotes.
    We split on the exact sequence `","` or `,` when the field is empty.
    """
    if not isinstance(content, bytes) or not content or len(content) > MAX_BYTES:
        raise ValueError("ICS CSV must be nonempty bounded bytes")

    text_content = content.decode("utf-8-sig")
    if '\\' in text_content:
        raise ValueError("Invalid ICS CSV encoding: backslashes found")

    rows = []
    lines = text_content.splitlines()
    if not lines or lines[0].strip() != ",".join(COLUMNS):
        raise ValueError("Unknown ICS CSV schema; deliberate source review required")

    for line_idx, line in enumerate(lines[1:], start=2):
        if not line.strip():
            continue

        parts = []
        curr = line
        for i in range(5):
            if curr.startswith('"'):
                idx = -1
                pos = 1
                while True:
                    idx = curr.find('"', pos)
                    if idx == -1:
                        raise ValueError(f"Invalid ICS CSV line {line_idx}: {line}")
                    if idx == len(curr) - 1 or curr[idx+1] == ',':
                        break
                    pos = idx + 1

                parts.append(curr[1:idx])
                curr = curr[idx+2:]
            else:
                idx = curr.find(',')
                if idx == -1:
                    raise ValueError(f"Invalid ICS CSV line {line_idx}: {line}")
                parts.append(curr[:idx])
                curr = curr[idx+1:]

        if curr.startswith('"'):
            if not curr.endswith('"') or '",' in curr:
                raise ValueError(f"Invalid ICS CSV line {line_idx}: {line}")
            parts.append(curr[1:-1])
        else:
            if ',' in curr:
                raise ValueError(f"Invalid ICS CSV line {line_idx}: {line}")
            parts.append(curr)

        if len(parts) != 6:
            raise ValueError(f"Invalid ICS CSV line {line_idx}: {line}")

        row = dict(zip(COLUMNS, parts))
        rows.append(row)

    seen = set()
    for row in rows:
        code = row["identifier"]
        if not ICS_CODE.fullmatch(code) or code in seen:
            raise ValueError(f"Invalid or duplicate ICS identifier: {code}")
        seen.add(code)
        if any(not row[k].strip() or len(row[k]) > 256 for k in ("titleEn", "titleFr")):
            raise ValueError("ICS labels must be present and bounded")
        if any(len(row[k]) > 4000 for k in ("scopeEn", "scopeFr")):
            raise ValueError("ICS scope notes exceed storage boundary")
        parent = code.rsplit(".", 1)[0] if "." in code else ""
        if row["parent"] != parent:
            raise ValueError(f"ICS parent mismatch for {code}: expected {parent}, got {row['parent']}")

    if not rows or any(row["parent"] and row["parent"] not in seen for row in rows):
        raise ValueError("ICS hierarchy is empty or has missing parents")
    return sorted(rows, key=lambda row: row["identifier"])


def prepare_import(content: bytes, *, retrieved_at: datetime, last_modified: str | None) -> dict:
    """Pin known source bytes; a future release requires deliberate code/data review."""
    rows = parse_csv(content)
    digest = hashlib.sha256(content).hexdigest()
    if digest != SOURCE["sha256"]:
        raise ValueError("Unknown ICS content/edition; deliberate source review required")
    if not isinstance(retrieved_at, datetime) or retrieved_at.utcoffset() is None:
        raise ValueError("retrieved_at must be timezone-aware")
    if last_modified is not None and (not isinstance(last_modified, str) or len(last_modified) > 128):
        raise ValueError("Invalid Last-Modified metadata")
    if {r[0] for r in CROSSWALK} != set(CATALOG_DISCIPLINE_ORDER):
        raise ValueError("Existing domain vocabulary changed; crosswalk review required")
    labels = {row["identifier"]: row["titleEn"] for row in rows}
    crosswalk = []
    for domain, codes, status, reason in CROSSWALK:
        for code in codes:
            if code not in labels:
                raise ValueError("Unknown crosswalk target; review required")
            crosswalk.append(dict(domain=domain, code=code, label=labels[code],
                                  relation="broader" if status == "initial_broader" else "candidate",
                                  review_status=status, reason=reason))
    return {
        "content": content,
        "rows": rows,
        "crosswalk": crosswalk,
        "counts": dict(zip(("fields", "groups", "subgroups"),
                           (sum(r["identifier"].count(".") == n for r in rows) for n in range(3)))),
        "provenance": {**SOURCE, "retrieved_at": retrieved_at.astimezone(timezone.utc).isoformat(),
                       "last_modified": last_modified},
    }


def fetch_official(*, transport=None) -> tuple[bytes, datetime, str | None]:
    """One fixed TLS endpoint; no redirect, proxy, retry or unbounded response."""
    deadline = time.monotonic() + 30
    try:
        with httpx.Client(transport=transport, verify=True, trust_env=False,
                          follow_redirects=False, timeout=10) as client:
            with client.stream("GET", SOURCE["source_url"], headers={"Accept-Encoding": "identity"}) as response:
                if response.status_code != 200:
                    raise ValueError("Official ICS download failed")
                if int(response.headers.get("content-length", "0")) > MAX_BYTES:
                    raise ValueError("Official ICS download exceeds size limit")
                content = bytearray()
                for chunk in response.iter_bytes(8192):
                    content.extend(chunk)
                    if len(content) > MAX_BYTES or time.monotonic() > deadline:
                        raise ValueError("Official ICS download exceeds size/time limit")
                return bytes(content), datetime.now(timezone.utc), response.headers.get("last-modified")
    except httpx.HTTPError:
        raise ValueError("Official ICS download failed") from None


def ingest_taxonomy(session: Session, prepared: dict, *, author_id: uuid.UUID) -> dict:
    """Atomic import; caller owns commit. Revalidate all inputs before mutation."""
    prov = prepared["provenance"]
    validated = prepare_import(prepared["content"], retrieved_at=datetime.fromisoformat(prov["retrieved_at"]),
                               last_modified=prov["last_modified"])
    if validated != prepared:
        raise ValueError("Prepared ICS snapshot changed; review required")
    session.flush()
    with session.begin_nested():
        session.execute(text("SELECT pg_advisory_xact_lock(187721, 7)"))
        return _store_taxonomy(session, validated, author_id=author_id)


def _resolve_crosswalk(session: Session, prepared: dict, *, scheme, import_id: uuid.UUID) -> dict:
    """Resolve each proposal to the governed row its stable identity names.

    The first import and the idempotent re-check resolve proposals the same
    way, so stored rows are compared against exactly what would be written.
    """
    source_scheme = get_classification_scheme(session, "ENGINEERING-DISCIPLINE")
    if source_scheme is None:
        raise ValueError("ENGINEERING-DISCIPLINE scheme is required for the ICS crosswalk")
    source_nodes = {
        node.preferred_label: node
        for node in session.query(ClassificationNode).filter_by(scheme_id=source_scheme.id).all()
    }
    target_nodes = {
        node.node_code: node
        for node in session.query(ClassificationNode).filter_by(scheme_id=scheme.id).all()
    }
    resolved = {}
    for mapping in prepared["crosswalk"]:
        source_node = source_nodes.get(mapping["domain"])
        target_node = target_nodes.get(mapping["code"])
        if source_node is None or target_node is None:
            raise ValueError("Crosswalk references an unknown governed node")
        identity = classification_crosswalk_id(import_id, source_node.id, target_node.id)
        resolved[identity] = dict(
            source_scheme_id=source_scheme.id,
            source_node_id=source_node.id,
            target_scheme_id=scheme.id,
            target_node_id=target_node.id,
            relation=mapping["relation"],
            review_status=mapping["review_status"],
            reason=mapping["reason"],
        )
    return resolved


# `review_status` is the human review field and is expected to progress to
# approved/rejected, so an idempotent re-import compares only imported facts.
CROSSWALK_IMPORTED_FACTS = ("source_node_id", "target_node_id", "relation", "reason")


def _store_taxonomy(session: Session, prepared: dict, *, author_id: uuid.UUID) -> dict:
    prov = prepared["provenance"]
    scheme_code = f"ISO-ICS-{prov['edition']}"
    scheme_id = classification_scheme_seed_id(scheme_code)
    import_id = classification_import_id(scheme_id, prov["sha256"])
    scheme = get_classification_scheme(session, scheme_code)
    if scheme is not None:
        record = session.get(ICSTaxonomyImport, import_id)
        if record is None or record.source_bytes != prepared['content']:
            raise ValueError("Existing ICS snapshot differs or lacks provenance; review required")
        existing = session.query(ClassificationNode).filter_by(scheme_id=scheme.id).all()
        expected = {r['identifier']: r for r in prepared['rows']}
        if len(existing) != len(expected) or any(
            n.node_code not in expected or n.preferred_label != expected[n.node_code]['titleEn']
            or n.description != (expected[n.node_code]['scopeEn'] or None)
            or n.parent_node_id != (classification_node_seed_id(scheme_code, expected[n.node_code]['parent']) if expected[n.node_code]['parent'] else None)
            for n in existing
        ):
            raise ValueError("Stored ICS hierarchy changed; review required")
        expected_crosswalk = _resolve_crosswalk(session, prepared, scheme=scheme, import_id=import_id)
        stored_crosswalk = session.query(ICSDomainCrosswalk).filter_by(import_id=import_id).all()
        if len(stored_crosswalk) != len(expected_crosswalk) or any(
            row.id not in expected_crosswalk
            or any(
                getattr(row, field) != expected_crosswalk[row.id][field]
                for field in CROSSWALK_IMPORTED_FACTS
            )
            for row in stored_crosswalk
        ):
            raise ValueError("Stored ICS crosswalk changed; review required")
        return {"scheme_id": scheme.id, "import_id": import_id, "node_count": len(existing)}
    if scheme is None:
        scheme = register_classification_scheme(
            session, scheme_code=scheme_code, name="International Classification for Standards (ICS)",
            version_label=str(prov["edition"]), status="draft",
            description=prov["attribution"] + "\n\n" + prov["clarification"],
            registered_at=datetime.fromisoformat(prov["retrieved_at"]),
            created_by_user_id=author_id,
            scheme_id=scheme_id,
        )
        session.flush()

    # Create nodes: fields, then groups, then subgroups
    nodes_by_code = {}
    for row in prepared["rows"]:
        code = row["identifier"]
        node = session.query(ClassificationNode).filter_by(scheme_id=scheme.id, node_code=code).first()
        if node is None:
            parent_id = nodes_by_code.get(row["parent"])
            node = add_classification_node(
                session, scheme_id=scheme.id, preferred_label=row["titleEn"],
                added_at=datetime.fromisoformat(prov["retrieved_at"]),
                node_code=code, parent_node_id=parent_id,
                description=row["scopeEn"] or None,
                status="draft",
                node_id=classification_node_seed_id(scheme_code, code)
            )
            session.flush()
        nodes_by_code[code] = node.id

    imported_at = datetime.fromisoformat(prov["retrieved_at"])
    session.add(
        ICSTaxonomyImport(
            id=import_id,
            scheme_id=scheme.id,
            dataset=prov["dataset"],
            edition=prov["edition"],
            publication_year=prov["publication_year"],
            source_update_year=prov["source_update_year"],
            source_url=prov["source_url"],
            page_url=prov["page_url"],
            browse_url=prov["browse_url"],
            license_url=prov["license_url"],
            license_code=prov["license"],
            attribution=prov["attribution"],
            clarification=prov["clarification"],
            limitation=prov["limitation"],
            retrieved_at=imported_at,
            last_modified=prov["last_modified"],
            content_sha256=prov["sha256"],
            source_bytes=prepared["content"],
            created_by_user_id=author_id,
            created_at=imported_at,
        )
    )
    session.flush()

    for identity, row in _resolve_crosswalk(
        session, prepared, scheme=scheme, import_id=import_id
    ).items():
        session.add(
            ICSDomainCrosswalk(id=identity, import_id=import_id, created_at=imported_at, **row)
        )
    session.flush()
    return {"scheme_id": scheme.id, "import_id": import_id, "node_count": len(nodes_by_code)}

# Governed review states for one crosswalk proposal, and the decisions a
# reviewer may take from each.
#
# A classification assignment that is rejected is terminal, because a reviewer
# who changes their mind proposes it afresh. A crosswalk row has no such
# escape hatch: it is derived from a pinned import, and an idempotent
# re-import deliberately leaves `review_status` alone, so a terminal
# disposition could only ever be corrected by the raw SQL this service exists
# to replace. A reviewer may therefore move a decided row to the *other*
# decision, but never back to undecided -- a review cannot be un-done, and the
# row would lose its attribution if it could.
CROSSWALK_REVIEW_STATUSES = frozenset(
    {"initial_broader", "needs_review", "approved", "rejected"}
)
CROSSWALK_UNDECIDED_STATUSES = frozenset({"initial_broader", "needs_review"})
CROSSWALK_DECIDED_STATUSES = frozenset({"approved", "rejected"})
CROSSWALK_REVIEW_TRANSITIONS: dict[str, frozenset[str]] = {
    "initial_broader": frozenset({"approved", "rejected"}),
    "needs_review": frozenset({"approved", "rejected"}),
    "approved": frozenset({"rejected"}),
    "rejected": frozenset({"approved"}),
}

MAX_REVIEW_NOTE = 4000


def list_domain_crosswalks(
    session: Session,
    *,
    import_id: uuid.UUID | None = None,
    review_status: str | None = None,
) -> list[ICSDomainCrosswalk]:
    """The crosswalk review queue, oldest proposal first.

    Filtering by `review_status` is what makes the outstanding queue legible:
    `needs_review` and `initial_broader` are the two undecided states, and
    neither means approved.
    """
    if review_status is not None and review_status not in CROSSWALK_REVIEW_STATUSES:
        raise ValueError("invalid crosswalk review status")
    if import_id is not None and not isinstance(import_id, uuid.UUID):
        raise ValueError("crosswalk import id must be a UUID")

    query = session.query(ICSDomainCrosswalk)
    if import_id is not None:
        query = query.filter_by(import_id=import_id)
    if review_status is not None:
        query = query.filter_by(review_status=review_status)
    return query.order_by(
        ICSDomainCrosswalk.created_at, ICSDomainCrosswalk.id
    ).all()


def disposition_crosswalk(
    session: Session,
    crosswalk_id: uuid.UUID,
    *,
    target_status: str,
    occurred_at: datetime,
    reviewed_by_user_id: uuid.UUID,
    review_note: str | None = None,
) -> ICSDomainCrosswalk:
    """Record one human review decision on one domain-to-ICS proposal.

    The caller owns the transaction, exactly as `ingest_taxonomy` does.

    Every crosswalk disposition is a human semantic judgement -- there is no
    deterministic method here that section 8.4's "explicit policy" would let
    through unattributed -- so a named reviewer is always required, not
    optional as it is for a `source_mapping` classification assignment.

    This writes nothing to `concept_classification_assignments` or
    `symbol_revision_classifications`. Approving a browse proposal is not a
    semantic assignment and does not become one.
    """
    if not isinstance(crosswalk_id, uuid.UUID):
        raise ValueError("crosswalk id must be a UUID")
    if target_status not in CROSSWALK_DECIDED_STATUSES:
        raise ValueError("a crosswalk review decision is approved or rejected")
    if not isinstance(occurred_at, datetime) or occurred_at.utcoffset() is None:
        raise ValueError("crosswalk decision time must be timezone-aware")
    if not isinstance(reviewed_by_user_id, uuid.UUID):
        raise ValueError("a crosswalk disposition requires a named reviewer")
    if review_note is not None:
        if not isinstance(review_note, str) or not review_note.strip():
            raise ValueError("crosswalk review note must be non-blank text")
        if len(review_note) > MAX_REVIEW_NOTE:
            raise ValueError("crosswalk review note exceeds storage boundary")

    row = session.get(ICSDomainCrosswalk, crosswalk_id, with_for_update=True)
    if row is None:
        raise LookupError(f"crosswalk proposal not found: {crosswalk_id}")

    current = row.review_status
    if target_status not in CROSSWALK_REVIEW_TRANSITIONS[current]:
        raise ValueError(
            f"crosswalk proposal cannot move from {current} to {target_status}"
        )

    row.review_status = target_status
    row.reviewed_by_user_id = reviewed_by_user_id
    row.reviewed_at = occurred_at.astimezone(timezone.utc)
    row.review_note = review_note
    session.flush()
    return row


def describe_domain_crosswalks(
    session: Session,
    *,
    import_id: uuid.UUID | None = None,
    review_status: str | None = None,
) -> list[dict]:
    """The review queue in operator-readable terms.

    A reviewer decides between "Fire & Life Safety -> 13.220 Protection
    against fire" and "-> 13 Environment. Health protection. Safety". Neither
    judgement can be made from a pair of UUIDs, so every row carries the
    domain name and the ICS code and title alongside the identifier the
    disposition commands need.
    """
    rows = list_domain_crosswalks(
        session, import_id=import_id, review_status=review_status
    )
    if not rows:
        return []
    wanted = {row.source_node_id for row in rows} | {row.target_node_id for row in rows}
    nodes = {
        node.id: node
        for node in session.query(ClassificationNode)
        .filter(ClassificationNode.id.in_(wanted))
        .all()
    }
    described = []
    for row in rows:
        source = nodes.get(row.source_node_id)
        target = nodes.get(row.target_node_id)
        described.append(
            {
                "crosswalk_id": str(row.id),
                "domain": source.preferred_label if source else None,
                "ics_code": target.node_code if target else None,
                "ics_label": target.preferred_label if target else None,
                "relation": row.relation,
                "review_status": row.review_status,
                "reason": row.reason,
                "reviewed_by_user_id": (
                    str(row.reviewed_by_user_id) if row.reviewed_by_user_id else None
                ),
                "reviewed_at": (
                    row.reviewed_at.isoformat() if row.reviewed_at else None
                ),
                "review_note": row.review_note,
            }
        )
    return described


DECISION_REGISTER = Path(__file__).parent / "data" / "ics-crosswalk-decisions.json"


def load_decision_register(path: Path | None = None) -> dict:
    """The reviewed disposition of every crosswalk proposal, as recorded data.

    Deliberately *not* folded into `CROSSWALK`. The proposals are imported
    facts that a re-import compares; the decisions are review facts about
    them. Keeping them apart is what lets the register be revised without
    making a re-import fail closed, and stops a decision from looking like
    something the importer asserted.

    Keyed on `(domain, ics_code)` rather than on `crosswalk_id`, because a
    crosswalk identity hashes the *live* ENGINEERING-DISCIPLINE node ids and
    so cannot be known until the import has landed on a real database.
    """
    register = json.loads((path or DECISION_REGISTER).read_text(encoding="utf-8"))
    decisions = register.get("decisions")
    if not isinstance(decisions, list) or not decisions:
        raise ValueError("Decision register is empty")
    seen = set()
    for row in decisions:
        key = (row.get("domain"), row.get("ics_code"))
        if not all(isinstance(part, str) and part for part in key):
            raise ValueError("Decision register row lacks a domain and an ICS code")
        if key in seen:
            raise ValueError(f"Decision register names {key[0]} -> {key[1]} twice")
        seen.add(key)
        if row.get("decision") not in CROSSWALK_DECIDED_STATUSES:
            raise ValueError("Decision register decision must be approved or rejected")
        note = row.get("note")
        if note is not None and (
            not isinstance(note, str) or not note.strip() or len(note) > MAX_REVIEW_NOTE
        ):
            raise ValueError("Decision register note must be non-blank and within bounds")
    return register


def plan_register_application(
    register: dict, described: list[dict], *, allow_correction: bool = False
) -> dict:
    """Match a recorded register against the live queue, or refuse.

    Fails closed on any disagreement between the register and the rows
    actually present: a register entry with no matching row, a live row the
    register is silent about, or an ambiguous `(domain, ics_code)` pair. Each
    of those means the register and the import have drifted apart, which is a
    review item rather than something to apply partially.

    A row already carrying the decision the register names is `unchanged`, so
    re-running the application is a no-op. A row carrying the *other* decision
    is refused unless `allow_correction`, so this never silently overwrites a
    reviewer's later correction with the register's older view.
    """
    live: dict[tuple[str, str], list[dict]] = {}
    for row in described:
        live.setdefault((row["domain"], row["ics_code"]), []).append(row)

    problems: list[str] = []
    ambiguous = sorted(key for key, rows in live.items() if len(rows) > 1)
    for domain, code in ambiguous:
        problems.append(f"{domain} -> {code} matches more than one crosswalk row")

    planned: list[dict] = []
    unchanged: list[dict] = []
    for entry in register["decisions"]:
        key = (entry["domain"], entry["ics_code"])
        rows = live.get(key)
        if not rows:
            problems.append(f"register names {key[0]} -> {key[1]}, which is not in the queue")
            continue
        if len(rows) > 1:
            continue
        row = rows[0]
        action = {
            "crosswalk_id": row["crosswalk_id"],
            "domain": row["domain"],
            "ics_code": row["ics_code"],
            "ics_label": row["ics_label"],
            "from": row["review_status"],
            "to": entry["decision"],
            "note": entry.get("note"),
        }
        if row["review_status"] == entry["decision"]:
            unchanged.append(action)
        elif row["review_status"] in CROSSWALK_DECIDED_STATUSES and not allow_correction:
            problems.append(
                f"{key[0]} -> {key[1]} is already {row['review_status']}; "
                "pass allow_correction to change a recorded decision"
            )
        else:
            planned.append(action)

    registered = {(row["domain"], row["ics_code"]) for row in register["decisions"]}
    for domain, code in sorted(set(live) - registered):
        problems.append(f"{domain} -> {code} is in the queue but not in the register")

    return {"planned": planned, "unchanged": unchanged, "problems": problems}


def apply_decision_register(
    session: Session,
    *,
    reviewed_by_user_id: uuid.UUID,
    occurred_at: datetime,
    register: dict | None = None,
    import_id: uuid.UUID | None = None,
    allow_correction: bool = False,
) -> dict:
    """Record every decision the register names, in one transaction.

    The caller owns the commit, so a refusal leaves the queue untouched. Each
    row still goes through `disposition_crosswalk`, so the transition map, the
    named-reviewer rule and the storage attribution check all apply exactly as
    they would to a single hand-made decision.
    """
    register = register if register is not None else load_decision_register()
    plan = plan_register_application(
        register,
        describe_domain_crosswalks(session, import_id=import_id),
        allow_correction=allow_correction,
    )
    if plan["problems"]:
        raise ValueError(
            "Decision register does not match the crosswalk queue: "
            + "; ".join(plan["problems"])
        )
    for action in plan["planned"]:
        disposition_crosswalk(
            session,
            uuid.UUID(action["crosswalk_id"]),
            target_status=action["to"],
            occurred_at=occurred_at,
            reviewed_by_user_id=reviewed_by_user_id,
            review_note=action["note"],
        )
    return plan
