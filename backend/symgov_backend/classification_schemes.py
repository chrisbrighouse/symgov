"""Governed classification schemes and their nodes (SM-P0-04).

Specification section 7.6 moves the catalogue's browse facets from
code-defined lists toward governed data, and is explicit about what these
tables are *not*: a classification scheme is a browse/reporting system, "not
necessarily an ontology". Meaning lives in `semantic_concepts`; the nodes here
are a display hierarchy over it.

Section 15.1 seeds the three schemes the catalogue already renders from
`catalog_taxonomy.py` and stops there. `sort_order` reproduces those list
orders exactly -- that ordering is what the catalogue UI shows today, so
losing it would be a visible regression.

Scope: platform only in P0. Section 14.1 places scheme management with a
platform admin and names organisation-specific schemes as a future extension,
so `CLASSIFICATION_SCHEME_SCOPES` holds one value and the check constraint in
`schema.py` holds the same one.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .catalog_taxonomy import (
    CATALOG_CATEGORY_ORDER,
    CATALOG_DISCIPLINE_ORDER,
    CATALOG_USE_CASE_ORDER,
)
from .models import ClassificationNode, ClassificationScheme

SCHEME_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9.-]{0,62}[A-Z0-9]$")
NODE_CODE_PATTERN = re.compile(r"^[A-Z0-9][A-Z0-9_]{0,62}[A-Z0-9]$")

# Section 14.1: platform admin manages schemes, and organisation-specific
# schemes are named as a future extension. The deferral is deliberate and
# structural rather than implied -- widening this set is the change that lands
# organisation-scoped schemes, alongside the `scope` check constraint.
CLASSIFICATION_SCHEME_SCOPES = frozenset({"platform"})

# One vocabulary covers a scheme and its nodes, matching `semantic_concepts`:
# these are SymGov-governed artefacts, not externally issued ones.
CLASSIFICATION_STATUSES = frozenset({"draft", "active", "deprecated", "withdrawn"})

CLASSIFICATION_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"active", "withdrawn"}),
    "active": frozenset({"deprecated", "withdrawn"}),
    "deprecated": frozenset({"active", "withdrawn"}),
    "withdrawn": frozenset(),
}

# A scheme in one of these states accepts no new nodes.
CLOSED_SCHEME_STATUSES = frozenset({"withdrawn"})

# A node in one of these states accepts no new assignments.
CLOSED_NODE_STATUSES = frozenset({"withdrawn"})

SCHEME_CODE_MAX_LENGTH = 64
NODE_CODE_MAX_LENGTH = 64
SCHEME_NAME_MAX_LENGTH = 256
NODE_LABEL_MAX_LENGTH = 256
VERSION_LABEL_MAX_LENGTH = 64
DESCRIPTION_MAX_LENGTH = 4000

# Multiples of ten, so a node can later be inserted between two seeded ones
# without renumbering the whole scheme.
SORT_ORDER_STEP = 10

# The three schemes section 15.1 asks P0 to seed, and the only rows migration
# 20260909_0051 inserts. Node labels come straight from `catalog_taxonomy.py`
# so the seed cannot drift from what the catalogue renders; the tests pin that.
#
# FORMAT_ORDER is deliberately absent: it is a file-format list, not a
# classification facet.
#
# Section 7.6 also names Industry/Application and Drawing Type as desirable
# schemes. Neither has a hard-coded list to seed from, so neither is created
# here -- an empty scheme would be an invented vocabulary.
SEED_CLASSIFICATION_SCHEMES: tuple[dict[str, object], ...] = (
    {
        "scheme_code": "ENGINEERING-DISCIPLINE",
        "name": "Engineering Discipline",
        "description": (
            "Engineering discipline facet, seeded from the catalogue's hard-coded "
            "discipline order."
        ),
        "labels": tuple(CATALOG_DISCIPLINE_ORDER),
    },
    {
        "scheme_code": "SYMBOL-CATEGORY-FAMILY",
        "name": "Symbol Category/Family",
        "description": (
            "Symbol category and family facet, seeded from the catalogue's hard-coded "
            "category order."
        ),
        "labels": tuple(CATALOG_CATEGORY_ORDER),
    },
    {
        "scheme_code": "USE-CASE",
        "name": "Use Case",
        "description": (
            "Download use-case facet, seeded from the catalogue's hard-coded use-case "
            "order."
        ),
        "labels": tuple(CATALOG_USE_CASE_ORDER),
    },
)

# Section 7.6: "The existing hard-coded orders can be seeded as version 1 of
# those schemes."
SEED_VERSION_LABEL = "1"


def classification_scheme_seed_id(scheme_code: str) -> uuid.UUID:
    """Return the deterministic identifier for a seeded scheme.

    uuid5 of a SymGov URN, so every environment agrees on the seed identifiers
    without a lookup by code -- the same shape SM-P0-03 uses.
    """
    return uuid.uuid5(uuid.NAMESPACE_URL, f"urn:symgov:classification-scheme:{scheme_code}")


def classification_node_seed_id(scheme_code: str, node_code: str) -> uuid.UUID:
    """Return the deterministic identifier for a seeded node."""
    return uuid.uuid5(
        uuid.NAMESPACE_URL, f"urn:symgov:classification-node:{scheme_code}:{node_code}"
    )


def _require_aware_timestamp(value: object, label: str) -> datetime:
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _require_optional_actor(value: object, label: str) -> uuid.UUID | None:
    if value is None:
        return None
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise ValueError(f"{label} must be a real UUID")
    return value


def _normalize_required_text(value: object, label: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized_value = value.strip()
    if not normalized_value:
        raise ValueError(f"{label} must not be empty")
    if len(normalized_value) > max_length:
        raise ValueError(f"{label} must be at most {max_length} characters")
    return normalized_value


def _normalize_optional_text(value: object, label: str, max_length: int) -> str | None:
    if value is None:
        return None
    return _normalize_required_text(value, label, max_length)


def normalize_classification_scheme_code(value: object) -> str:
    """Return a scheme code in canonical form.

    Codes are uppercased so `use-case` and `USE-CASE` cannot become two
    schemes; the unique index is on the stored value, not a folded one.
    """
    code = _normalize_required_text(value, "classification scheme code", SCHEME_CODE_MAX_LENGTH)
    if not code.isascii():
        raise ValueError("classification scheme code must contain ASCII characters only")
    normalized_code = code.upper()
    if not SCHEME_CODE_PATTERN.match(normalized_code):
        raise ValueError(
            f"classification scheme code does not match the required grammar: {value!r}"
        )
    return normalized_code


def normalize_classification_node_code(value: object) -> str:
    """Return a node code in canonical form."""
    code = _normalize_required_text(value, "classification node code", NODE_CODE_MAX_LENGTH)
    if not code.isascii():
        raise ValueError("classification node code must contain ASCII characters only")
    normalized_code = code.upper()
    if not NODE_CODE_PATTERN.match(normalized_code):
        raise ValueError(
            f"classification node code does not match the required grammar: {value!r}"
        )
    return normalized_code


def derive_classification_node_code(label: object) -> str:
    """Derive a node code from a display label.

    The seeded labels carry punctuation the code grammar excludes ("Piping /
    P&ID", "Fire & Life Safety"), so every run of non-alphanumeric characters
    collapses to a single underscore. Deterministic, because the seed
    identifiers are uuid5 of the resulting code.
    """
    text_label = _normalize_required_text(label, "classification node label", NODE_LABEL_MAX_LENGTH)
    collapsed = re.sub(r"[^A-Z0-9]+", "_", text_label.upper()).strip("_")
    if not collapsed:
        raise ValueError(f"classification node label yields no code: {label!r}")
    return normalize_classification_node_code(collapsed)


def normalize_sort_order(value: object) -> int:
    """Return a non-negative display order."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("classification node sort order must be an integer")
    if value < 0:
        raise ValueError("classification node sort order must not be negative")
    return value


def register_classification_scheme(
    session: Session,
    *,
    scheme_code: str,
    name: str,
    registered_at: datetime,
    version_label: str = SEED_VERSION_LABEL,
    scope: str = "platform",
    description: object = None,
    status: str = "draft",
    created_by_user_id: uuid.UUID | None = None,
    scheme_id: uuid.UUID | None = None,
) -> ClassificationScheme:
    """Register a classification scheme.

    Specification section 14.1 places this authority with a platform admin, so
    `created_by_user_id` is optional: a migration-driven registration has no
    user to name.
    """
    normalized_code = normalize_classification_scheme_code(scheme_code)
    normalized_name = _normalize_required_text(name, "classification scheme name", SCHEME_NAME_MAX_LENGTH)
    normalized_label = _normalize_required_text(
        version_label, "classification scheme version label", VERSION_LABEL_MAX_LENGTH
    )
    normalized_description = _normalize_optional_text(
        description, "classification scheme description", DESCRIPTION_MAX_LENGTH
    )
    if scope not in CLASSIFICATION_SCHEME_SCOPES:
        raise ValueError("invalid classification scheme scope")
    if status not in {"draft", "active"}:
        raise ValueError("a classification scheme is registered as draft or active")
    _require_aware_timestamp(registered_at, "classification scheme registration time")
    _require_optional_actor(created_by_user_id, "classification scheme registrar")

    scheme = ClassificationScheme(
        id=scheme_id or uuid.uuid4(),
        scheme_code=normalized_code,
        name=normalized_name,
        scope=scope,
        version_label=normalized_label,
        status=status,
        description=normalized_description,
        created_by_user_id=created_by_user_id,
        created_at=registered_at,
        updated_at=registered_at,
    )
    session.add(scheme)
    return scheme


def add_classification_node(
    session: Session,
    *,
    scheme_id: uuid.UUID,
    preferred_label: str,
    added_at: datetime,
    node_code: object = None,
    parent_node_id: uuid.UUID | None = None,
    description: object = None,
    sort_order: object = None,
    status: str = "draft",
    node_id: uuid.UUID | None = None,
) -> ClassificationNode:
    """Add one node to a scheme.

    `parent_node_id` must name a node of the *same* scheme. The composite
    foreign key on (parent_node_id, scheme_id) enforces that in storage; this
    function additionally refuses a parent chain that would close a cycle,
    which no check constraint can see.
    """
    normalized_label = _normalize_required_text(
        preferred_label, "classification node label", NODE_LABEL_MAX_LENGTH
    )
    normalized_code = (
        derive_classification_node_code(normalized_label)
        if node_code is None
        else normalize_classification_node_code(node_code)
    )
    normalized_description = _normalize_optional_text(
        description, "classification node description", DESCRIPTION_MAX_LENGTH
    )
    if status not in {"draft", "active"}:
        raise ValueError("a classification node is added as draft or active")
    _require_aware_timestamp(added_at, "classification node creation time")

    scheme = session.get(ClassificationScheme, scheme_id)
    if scheme is None:
        raise LookupError(f"classification scheme not found: {scheme_id}")
    if scheme.status in CLOSED_SCHEME_STATUSES:
        raise ValueError(f"classification scheme is {scheme.status} and accepts no new nodes")

    identifier = node_id or uuid.uuid4()
    if parent_node_id is not None:
        _require_parent_in_scheme(session, scheme_id, parent_node_id, identifier)

    if sort_order is None:
        highest = session.execute(
            select(ClassificationNode.sort_order)
            .where(ClassificationNode.scheme_id == scheme_id)
            .order_by(ClassificationNode.sort_order.desc())
            .limit(1)
        ).scalar_one_or_none()
        normalized_sort_order = SORT_ORDER_STEP if highest is None else highest + SORT_ORDER_STEP
    else:
        normalized_sort_order = normalize_sort_order(sort_order)

    node = ClassificationNode(
        id=identifier,
        scheme_id=scheme_id,
        node_code=normalized_code,
        parent_node_id=parent_node_id,
        preferred_label=normalized_label,
        description=normalized_description,
        sort_order=normalized_sort_order,
        status=status,
        created_at=added_at,
        updated_at=added_at,
    )
    session.add(node)
    return node


def _require_parent_in_scheme(
    session: Session, scheme_id: uuid.UUID, parent_node_id: uuid.UUID, node_id: uuid.UUID
) -> None:
    if parent_node_id == node_id:
        raise ValueError("a classification node cannot be its own parent")
    parent = session.get(ClassificationNode, parent_node_id)
    if parent is None:
        raise LookupError(f"classification node not found: {parent_node_id}")
    if parent.scheme_id != scheme_id:
        raise ValueError("a classification node's parent must belong to the same scheme")
    if parent.status in CLOSED_NODE_STATUSES:
        raise ValueError(f"classification node is {parent.status} and accepts no children")

    # Walk to the root. A cycle is invisible to a check constraint, and the
    # walk is bounded by the number of nodes in the scheme.
    seen = {node_id, parent_node_id}
    ancestor = parent.parent_node_id
    while ancestor is not None:
        if ancestor in seen:
            raise ValueError("a classification node parent would close a cycle")
        seen.add(ancestor)
        row = session.get(ClassificationNode, ancestor)
        if row is None:
            raise LookupError(f"classification node not found: {ancestor}")
        ancestor = row.parent_node_id


def set_classification_scheme_status(
    session: Session, scheme_id: uuid.UUID, *, target_status: str, occurred_at: datetime
) -> ClassificationScheme:
    """Move a scheme through its governed lifecycle."""
    scheme = session.get(ClassificationScheme, scheme_id)
    if scheme is None:
        raise LookupError(f"classification scheme not found: {scheme_id}")
    _apply_status(scheme, target_status=target_status, occurred_at=occurred_at, label="classification scheme")
    return scheme


def set_classification_node_status(
    session: Session, node_id: uuid.UUID, *, target_status: str, occurred_at: datetime
) -> ClassificationNode:
    """Move a node through its governed lifecycle.

    Deprecating a node deliberately leaves its existing assignments alone: an
    assignment records a classification that was made, and retiring the node
    does not make that untrue.
    """
    node = session.get(ClassificationNode, node_id)
    if node is None:
        raise LookupError(f"classification node not found: {node_id}")
    _apply_status(node, target_status=target_status, occurred_at=occurred_at, label="classification node")
    return node


def _apply_status(
    row: ClassificationScheme | ClassificationNode,
    *,
    target_status: str,
    occurred_at: datetime,
    label: str,
) -> None:
    if target_status not in CLASSIFICATION_STATUSES:
        raise ValueError(f"invalid {label} status")
    _require_aware_timestamp(occurred_at, f"{label} status change time")
    current_status = row.status
    if target_status not in CLASSIFICATION_STATUS_TRANSITIONS[current_status]:
        raise ValueError(f"{label} cannot move from {current_status} to {target_status}")
    row.status = target_status
    row.updated_at = occurred_at


def seed_classification_schemes(
    session: Session, *, seeded_at: datetime
) -> list[ClassificationScheme]:
    """Ensure the three P0 schemes and their nodes exist, and return them.

    Idempotent, and it never overwrites a row an operator has since edited:
    migration 20260909_0051 already inserts these, so this function exists for
    fixtures and for a database seeded before that migration landed.
    """
    _require_aware_timestamp(seeded_at, "classification seed time")
    seeded: list[ClassificationScheme] = []
    for definition in SEED_CLASSIFICATION_SCHEMES:
        scheme_code = definition["scheme_code"]
        scheme = session.execute(
            select(ClassificationScheme).where(ClassificationScheme.scheme_code == scheme_code)
        ).scalar_one_or_none()
        if scheme is None:
            scheme = register_classification_scheme(
                session,
                scheme_id=classification_scheme_seed_id(scheme_code),
                scheme_code=scheme_code,
                name=definition["name"],
                description=definition["description"],
                version_label=SEED_VERSION_LABEL,
                status="active",
                registered_at=seeded_at,
            )
            session.flush()
        seeded.append(scheme)

        existing_codes = set(
            session.execute(
                select(ClassificationNode.node_code).where(
                    ClassificationNode.scheme_id == scheme.id
                )
            ).scalars()
        )
        for index, label in enumerate(definition["labels"]):
            node_code = derive_classification_node_code(label)
            if node_code in existing_codes:
                continue
            add_classification_node(
                session,
                node_id=classification_node_seed_id(scheme_code, node_code),
                scheme_id=scheme.id,
                node_code=node_code,
                preferred_label=label,
                sort_order=(index + 1) * SORT_ORDER_STEP,
                status="active",
                added_at=seeded_at,
            )
        session.flush()
    return seeded


def get_classification_scheme(session: Session, scheme_code: str) -> ClassificationScheme | None:
    """Look a scheme up by its code."""
    return session.execute(
        select(ClassificationScheme).where(
            ClassificationScheme.scheme_code == normalize_classification_scheme_code(scheme_code)
        )
    ).scalar_one_or_none()


def list_classification_schemes(
    session: Session, *, status: str | None = None
) -> list[ClassificationScheme]:
    """List schemes by code."""
    if status is not None and status not in CLASSIFICATION_STATUSES:
        raise ValueError("invalid classification scheme status filter")
    query = select(ClassificationScheme)
    if status is not None:
        query = query.where(ClassificationScheme.status == status)
    return list(session.execute(query.order_by(ClassificationScheme.scheme_code)).scalars())


def list_classification_nodes(
    session: Session,
    scheme_id: uuid.UUID,
    *,
    status: str | None = None,
    parent_node_id: uuid.UUID | None = None,
    top_level_only: bool = False,
) -> list[ClassificationNode]:
    """List a scheme's nodes in display order.

    `sort_order` is deliberately not unique -- a node can be inserted between
    two existing ones -- so `preferred_label` breaks any tie and the order
    stays deterministic.
    """
    if status is not None and status not in CLASSIFICATION_STATUSES:
        raise ValueError("invalid classification node status filter")
    if top_level_only and parent_node_id is not None:
        raise ValueError("top_level_only and parent_node_id cannot both be given")

    query = select(ClassificationNode).where(ClassificationNode.scheme_id == scheme_id)
    if status is not None:
        query = query.where(ClassificationNode.status == status)
    if top_level_only:
        query = query.where(ClassificationNode.parent_node_id.is_(None))
    elif parent_node_id is not None:
        query = query.where(ClassificationNode.parent_node_id == parent_node_id)
    query = query.order_by(ClassificationNode.sort_order, ClassificationNode.preferred_label)
    return list(session.execute(query).scalars())


def classification_node_labels(session: Session, scheme_code: str) -> list[str]:
    """Return one scheme's active node labels in display order.

    This is the read the catalogue facet lists in `catalog_taxonomy.py` become
    once SM-P0-05 reads classifications rather than the hard-coded constants.
    """
    scheme = get_classification_scheme(session, scheme_code)
    if scheme is None:
        raise LookupError(f"classification scheme not found: {scheme_code}")
    return [
        node.preferred_label
        for node in list_classification_nodes(session, scheme.id, status="active")
    ]
