"""What the Catalog's Details view reads about one published symbol.

`GET /published/symbols/{ref}/details` returns the governed facts the published
row does not carry: its classifications, its external mappings, the same
concept drawn in other packs, the other symbols of its DEXPI class, the rights
that let it be shared, where it came from, and its revision and approval
history. Everything is read for a symbol that is already public, so nothing
here decides visibility.

What is deliberately left out, because this is served to every signed-in
reader of a public symbol: no user identity of any kind (proposers, reviewers,
rights deciders, the people behind a review decision), no evidence or
confidence, no storage object keys, and no internal review or lineage IDs.
History says what happened and when, never who did it.

Response shape (camelCase; absent data is `null` or `[]`, never omitted):

    {
      "catalogSymbolId": "S-273",
      "classifications": [{"schemeCode", "schemeName", "nodeCode",
                           "nodePath": [labels, root first], "nodeLabel",
                           "method", "role", "status"}],
      "externalMappings": [{"system", "identifier", "label", "relation", "status"}],
      "sameConcept": [{"catalogSymbolId", "name", "slug", "packCode", "pack", "previewUrl"}],
      "sameClass": {"className", "total", "items": [same item shape]} | null,
      "rights": {"status", "disposition", "licensor", "creator",
                 "attributionText", "attributionIsPlaceholder", "sourceUrl"} | null,
      "provenance": {"packCode", "pack", "sourceUri", "sourceCommit",
                     "releaseVersion", "sourcePath", "providerEntryIdentifier"} | null,
      "history": [{"kind", "at", "label"}]   # newest first
    }

Decisions the contract left open:

* Classifications are the assignments on the current published revision whose
  status is `proposed` or `verified` (rejected and retired are left out), as
  `governed_assignment_exists_sql` already counts them.
* External mappings are `verified` mappings of a concept the revision holds a
  `verified` semantic assignment to. `relation` is the mapping type.
* `sameClass.total` counts the Catalog rows `dexpiClass=<class>` returns, so it
  includes the symbol being viewed; `items` never do. A symbol listed in two
  packs counts twice, exactly as the search counts it.
* `rights` is the approved rights record of the symbol's source package, as the
  attribution text is read; a symbol with no such record has `null`.
* `history` kinds are only `revision_created`, `revision_imported`,
  `rights_approved`, `review_approved`, `published` and `demoted`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
import uuid

from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from .published_catalog import (
    PUBLISHED_SYMBOLS_SQL,
    dexpi_class_parameter,
    dexpi_class_predicate_sql,
    list_published_preview_assets,
    choose_published_preview_asset,
)

SAME_CONCEPT_LIMIT = 24
SAME_CLASS_ITEM_LIMIT = 8
MAX_NODE_DEPTH = 24

# Newest first, and when two events share a timestamp the later step in a
# symbol's life comes first.
_KIND_RANK = {
    "demoted": 5,
    "published": 4,
    "review_approved": 3,
    "rights_approved": 2,
    "revision_imported": 1,
    "revision_created": 1,
}

# Revisions that were never offered for approval are not part of the story.
_HISTORY_REVISION_STATES = ("approved", "published", "deprecated", "withdrawn")
_APPROVING_DECISIONS = ("approve", "approved")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _text_or_none(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def _natural_key(identifier: str | None) -> tuple:
    parts: list = []
    buffer = ""
    for char in identifier or "":
        if char.isdigit():
            buffer += char
            continue
        if buffer:
            parts.append((1, int(buffer), ""))
            buffer = ""
        parts.append((0, 0, char))
    if buffer:
        parts.append((1, int(buffer), ""))
    return tuple(parts)


def symbol_summary_item(row) -> dict:
    """The thumbnail row the Details view lists for another symbol."""
    payload = row.payload_json or {}
    identifier = row.catalog_symbol_id
    has_preview = bool(
        choose_published_preview_asset(payload) or list_published_preview_assets(payload)
    )
    return {
        "catalogSymbolId": identifier,
        "name": payload.get("name") or payload.get("canonical_name") or row.canonical_name,
        "slug": row.slug,
        "packCode": row.pack_code,
        "pack": row.pack_title,
        "previewUrl": f"/api/v1/published/symbols/{identifier}/preview" if has_preview and identifier else None,
    }


def _classifications(session: Session, revision_id: uuid.UUID) -> list[dict]:
    rows = session.execute(
        text(
            """
            SELECT cs.scheme_code, cs.name AS scheme_name, cn.id AS node_id, cn.node_code,
                   cn.preferred_label, src.assignment_role, src.status, src.method
            FROM symbol_revision_classifications src
            JOIN classification_nodes cn ON cn.id = src.classification_node_id
            JOIN classification_schemes cs ON cs.id = src.classification_scheme_id
            WHERE src.symbol_revision_id = :revision_id
              AND src.status IN ('proposed', 'verified')
            ORDER BY cs.scheme_code,
                     CASE src.assignment_role WHEN 'primary' THEN 0 ELSE 1 END,
                     cn.node_code
            """
        ),
        {"revision_id": revision_id},
    ).all()
    if not rows:
        return []
    chain_rows = session.execute(
        text(
            """
            WITH RECURSIVE chain AS (
                SELECT cn.id AS leaf_id, cn.id, cn.parent_node_id, cn.preferred_label, 0 AS depth
                FROM classification_nodes cn
                WHERE cn.id IN :node_ids
                UNION ALL
                SELECT chain.leaf_id, parent.id, parent.parent_node_id, parent.preferred_label, chain.depth + 1
                FROM chain
                JOIN classification_nodes parent ON parent.id = chain.parent_node_id
                WHERE chain.depth < :max_depth
            )
            SELECT leaf_id, preferred_label, depth FROM chain ORDER BY leaf_id, depth DESC
            """
        ).bindparams(bindparam("node_ids", expanding=True)),
        {"node_ids": sorted({row.node_id for row in rows}, key=str), "max_depth": MAX_NODE_DEPTH},
    ).all()
    paths: dict[uuid.UUID, list[str]] = {}
    for leaf_id, label, _depth in chain_rows:
        paths.setdefault(leaf_id, []).append(label)
    return [
        {
            "schemeCode": row.scheme_code,
            "schemeName": row.scheme_name,
            "nodeCode": row.node_code,
            "nodePath": paths.get(row.node_id) or [row.preferred_label],
            "nodeLabel": row.preferred_label,
            "method": row.method,
            "role": row.assignment_role,
            "status": row.status,
        }
        for row in rows
    ]


def _concept_ids(session: Session, revision_id: uuid.UUID) -> list[uuid.UUID]:
    return [
        row.semantic_concept_id
        for row in session.execute(
            text(
                """
                SELECT DISTINCT ssa.semantic_concept_id
                FROM symbol_semantic_assignments ssa
                WHERE ssa.symbol_revision_id = :revision_id AND ssa.status = 'verified'
                """
            ),
            {"revision_id": revision_id},
        ).all()
    ]


def _external_mappings(session: Session, concept_ids: list[uuid.UUID]) -> list[dict]:
    if not concept_ids:
        return []
    rows = session.execute(
        text(
            """
            SELECT ess.title AS system, cer.external_identifier, cer.external_label,
                   cer.mapping_type, cer.mapping_status
            FROM concept_external_references cer
            JOIN external_semantic_scheme_versions esv ON esv.id = cer.scheme_version_id
            JOIN external_semantic_schemes ess ON ess.id = esv.scheme_id
            WHERE cer.semantic_concept_id IN :concept_ids
              AND cer.mapping_status = 'verified'
            ORDER BY ess.title, cer.external_identifier
            """
        ).bindparams(bindparam("concept_ids", expanding=True)),
        {"concept_ids": sorted(concept_ids, key=str)},
    ).all()
    return [
        {
            "system": row.system,
            "identifier": row.external_identifier,
            "label": row.external_label,
            "relation": row.mapping_type,
            "status": row.mapping_status,
        }
        for row in rows
    ]


def _same_concept(session: Session, row, concept_ids: list[uuid.UUID]) -> list[dict]:
    if not concept_ids:
        return []
    query = text(
        PUBLISHED_SYMBOLS_SQL
        + """
        AND gs.id <> :symbol_id
        AND pk.id::text <> :pack_id
        AND EXISTS (
            SELECT 1 FROM symbol_semantic_assignments ssa
            WHERE ssa.symbol_revision_id = sr.id
              AND ssa.status = 'verified'
              AND ssa.semantic_concept_id IN :concept_ids
        )
        """
    ).bindparams(bindparam("concept_ids", expanding=True))
    rows = session.execute(
        query,
        {
            "symbol_id": uuid.UUID(str(row.symbol_id)),
            "pack_id": str(row.pack_id),
            "concept_ids": sorted(concept_ids, key=str),
        },
    ).all()
    seen: set[str] = set()
    ordered = []
    for other in sorted(rows, key=lambda item: (_natural_key(item.catalog_symbol_id), str(item.pack_code))):
        if other.symbol_id in seen:
            continue
        seen.add(other.symbol_id)
        ordered.append(symbol_summary_item(other))
    return ordered[:SAME_CONCEPT_LIMIT]


def dexpi_class_of(payload: dict | None) -> str | None:
    """The first DEXPI class the revision's payload lists, if it lists one."""
    dexpi = (payload or {}).get("dexpi")
    classes = dexpi.get("component_classes") if isinstance(dexpi, dict) else None
    if isinstance(classes, list):
        for value in classes:
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


def _same_class(session: Session, row) -> dict | None:
    class_name = dexpi_class_of(row.payload_json)
    if not class_name:
        return None
    params = {"dexpi_class_json": dexpi_class_parameter(class_name)}
    predicate = " AND " + dexpi_class_predicate_sql("sr.payload_json")
    # The total is the row count the search filter returns, this symbol included.
    total = session.execute(
        text("SELECT count(*) FROM (" + PUBLISHED_SYMBOLS_SQL + predicate + ") counted"),
        params,
    ).scalar_one()
    rows = session.execute(
        text(PUBLISHED_SYMBOLS_SQL + predicate + " AND gs.id <> :symbol_id"),
        {**params, "symbol_id": uuid.UUID(str(row.symbol_id))},
    ).all()
    seen: set[str] = set()
    items = []
    for other in sorted(rows, key=lambda item: (_natural_key(item.catalog_symbol_id), str(item.pack_code))):
        if other.symbol_id in seen:
            continue
        seen.add(other.symbol_id)
        items.append(symbol_summary_item(other))
        if len(items) == SAME_CLASS_ITEM_LIMIT:
            break
    return {"className": class_name, "total": int(total), "items": items}


def _rights(session: Session, revision_id: uuid.UUID) -> dict | None:
    record = session.execute(
        text(
            """
            SELECT rr.rights_status, rr.disposition, rr.evidence_json,
                   sp.metadata_json, sp.source_uri
            FROM source_package_entries spe
            JOIN source_packages sp ON sp.id = spe.source_package_id
            JOIN rights_records rr ON rr.source_package_id = sp.id
            WHERE spe.symbol_revision_id = :revision_id
              AND rr.decision_status = 'approved'
            ORDER BY rr.created_at DESC
            LIMIT 1
            """
        ),
        {"revision_id": revision_id},
    ).first()
    if record is None:
        return None
    evidence = record.evidence_json or {}
    metadata = record.metadata_json or {}
    return {
        "status": record.rights_status,
        "disposition": record.disposition,
        "licensor": _text_or_none(evidence.get("licensor") or metadata.get("licensor")),
        "creator": _text_or_none(evidence.get("creator") or metadata.get("creator")),
        "attributionText": _text_or_none(evidence.get("attribution_text")),
        "attributionIsPlaceholder": bool(evidence.get("attribution_is_placeholder")),
        "sourceUrl": _text_or_none(evidence.get("source_url") or record.source_uri),
    }


def _provenance(session: Session, row) -> dict | None:
    record = session.execute(
        text(
            """
            SELECT sp.source_uri, sp.release_version, sp.metadata_json,
                   spe.source_path, spe.provider_entry_identifier,
                   (SELECT rr.evidence_json ->> 'source_commit'
                      FROM rights_records rr
                     WHERE rr.source_package_id = sp.id AND rr.decision_status = 'approved'
                     ORDER BY rr.created_at DESC LIMIT 1) AS rights_source_commit
            FROM source_package_entries spe
            JOIN source_packages sp ON sp.id = spe.source_package_id
            WHERE spe.symbol_revision_id = :revision_id
            ORDER BY spe.created_at DESC
            LIMIT 1
            """
        ),
        {"revision_id": uuid.UUID(str(row.symbol_revision_id))},
    ).first()
    if record is None:
        return None
    metadata = record.metadata_json or {}
    return {
        "packCode": row.pack_code,
        "pack": row.pack_title,
        "sourceUri": _text_or_none(record.source_uri),
        "sourceCommit": _text_or_none(metadata.get("source_commit") or record.rights_source_commit),
        "releaseVersion": _text_or_none(record.release_version),
        "sourcePath": _text_or_none(record.source_path),
        "providerEntryIdentifier": _text_or_none(record.provider_entry_identifier),
    }


def _history(session: Session, row) -> list[dict]:
    symbol_id = uuid.UUID(str(row.symbol_id))
    events: list[dict] = []

    def add(kind: str, at: datetime | None, label: str) -> None:
        if at is not None:
            events.append({"kind": kind, "at": at, "label": label})

    revisions = session.execute(
        text(
            """
            SELECT sr.id, sr.revision_label, sr.created_at, sr.payload_json,
                   EXISTS (SELECT 1 FROM source_package_entries spe
                           WHERE spe.symbol_revision_id = sr.id) AS imported
            FROM symbol_revisions sr
            WHERE sr.symbol_id = :symbol_id AND sr.lifecycle_state IN :states
            """
        ).bindparams(bindparam("states", expanding=True)),
        {"symbol_id": symbol_id, "states": list(_HISTORY_REVISION_STATES)},
    ).all()
    revision_labels = {revision.id: revision.revision_label for revision in revisions}
    for revision in revisions:
        if revision.imported:
            add("revision_imported", revision.created_at, f"Revision {revision.revision_label} imported")
        else:
            add("revision_created", revision.created_at, f"Revision {revision.revision_label} created")
    if not revisions:
        return []
    revision_ids = sorted(revision_labels, key=str)

    # Imported libraries are approved on the source package's rights record;
    # a symbol's own record, where one exists, counts the same way.
    rights = session.execute(
        text(
            """
            SELECT DISTINCT rr.id, rr.rights_status, rr.disposition, rr.decided_at
            FROM rights_records rr
            WHERE rr.decision_status = 'approved' AND rr.decided_at IS NOT NULL
              AND (rr.symbol_revision_id IN :revision_ids
                   OR rr.source_package_id IN (
                       SELECT spe.source_package_id FROM source_package_entries spe
                       WHERE spe.symbol_revision_id IN :revision_ids))
            """
        ).bindparams(bindparam("revision_ids", expanding=True)),
        {"revision_ids": revision_ids},
    ).all()
    for record in rights:
        detail = " · ".join(value for value in (record.rights_status, record.disposition) if value)
        add("rights_approved", record.decided_at, f"Rights approved ({detail})" if detail else "Rights approved")

    # Approvals from the review path: the decision a legacy publication names
    # in its payload, and a change request's own approving decision.
    decision_ids = []
    for revision in revisions:
        raw = (revision.payload_json or {}).get("review_decision_id")
        try:
            decision_ids.append(uuid.UUID(str(raw)))
        except (TypeError, ValueError):
            continue
    reviewed: list[tuple[datetime, str]] = []
    if decision_ids:
        for decision in session.execute(
            text(
                """
                SELECT created_at FROM human_review_decisions
                WHERE id IN :ids AND lower(decision_code) IN :approving
                """
            ).bindparams(bindparam("ids", expanding=True), bindparam("approving", expanding=True)),
            {"ids": sorted(set(decision_ids), key=str), "approving": list(_APPROVING_DECISIONS)},
        ).all():
            reviewed.append((decision.created_at, "Review approved"))
    for decision in session.execute(
        text(
            """
            SELECT rd.created_at, cr.proposed_revision_id
            FROM review_decisions rd
            JOIN change_requests cr ON cr.id = rd.change_request_id
            WHERE cr.proposed_revision_id IN :revision_ids AND lower(rd.decision) IN :approving
            """
        ).bindparams(bindparam("revision_ids", expanding=True), bindparam("approving", expanding=True)),
        {"revision_ids": revision_ids, "approving": list(_APPROVING_DECISIONS)},
    ).all():
        label = revision_labels.get(decision.proposed_revision_id)
        reviewed.append((decision.created_at, f"Review approved for revision {label}" if label else "Review approved"))
    for at, label in reviewed:
        add("review_approved", at, label)

    for page in session.execute(
        text(
            """
            SELECT pp.created_at, pk.title AS pack_title, sr.revision_label
            FROM published_pages pp
            JOIN publication_packs pk ON pk.id = pp.pack_id
            JOIN symbol_revisions sr ON sr.id = pp.current_symbol_revision_id
            WHERE pp.current_symbol_revision_id IN :revision_ids AND pk.audience = 'public'
            """
        ).bindparams(bindparam("revision_ids", expanding=True)),
        {"revision_ids": revision_ids},
    ).all():
        where = f" in {page.pack_title}" if page.pack_title else ""
        add("published", page.created_at, f"Revision {page.revision_label} published{where}")

    for event in session.execute(
        text(
            """
            SELECT created_at FROM audit_events
            WHERE entity_type = 'governed_symbol' AND entity_id = :symbol_id
              AND action = 'governed_symbol.demoted'
            """
        ),
        {"symbol_id": symbol_id},
    ).all():
        add("demoted", event.created_at, "Withdrawn from the public Catalog")

    events.sort(key=lambda event: (event["at"], _KIND_RANK[event["kind"]]), reverse=True)
    return [{"kind": event["kind"], "at": _iso(event["at"]), "label": event["label"]} for event in events]


def load_published_symbol_details(session: Session, row) -> dict:
    """The Details view's data for one public published row (see the module docstring)."""
    revision_id = uuid.UUID(str(row.symbol_revision_id))
    concept_ids = _concept_ids(session, revision_id)
    return {
        "catalogSymbolId": row.catalog_symbol_id,
        "classifications": _classifications(session, revision_id),
        "externalMappings": _external_mappings(session, concept_ids),
        "sameConcept": _same_concept(session, row, concept_ids),
        "sameClass": _same_class(session, row),
        "rights": _rights(session, revision_id),
        "provenance": _provenance(session, row),
        "history": _history(session, row),
    }
