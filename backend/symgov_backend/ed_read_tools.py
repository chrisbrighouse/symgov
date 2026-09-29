"""Typed, read-only application context tools for the Ed guru.

This module deliberately contains no LLM calls and no mutation path.  The
public functions take an already revalidated :class:`Stage4Principal` for
organization-scoped reads, so the future Ed orchestration layer cannot bypass
the application's session and tenant boundary.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
import uuid

from pydantic import BaseModel, ConfigDict, Field, computed_field
from fastapi import HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .auth import hash_session_token
from .models import (
    ClassificationNode,
    ClassificationScheme,
    GovernedSymbol,
    ICSTaxonomyImport,
    Project,
    ProjectSymbolSet,
    SymbolSet,
    SymbolSetItem,
    SymbolRevision,
    User,
    UserRole,
    UserSession,
    UserSessionProjectContext,
)
from .organization_authorization import resolve_eligible_organization_memberships
from .settings import SymgovAPISettings
from .stage4_authorization import Stage4Principal, require_stage4_principal
from .symbol_context_service import _resolved_set
from .symbol_eligibility import current_symbol_revisions
from .symbol_identity import governed_symbol_human_readable_id


ED_READ_TOOL_NAMES = (
    "get_current_user_profile",
    "list_accessible_organizations",
    "get_current_organization",
    "list_accessible_projects",
    "get_project_context",
    "list_accessible_symbol_sets",
    "get_symbol_set",
    "search_accessible_symbols",
    "get_symbol",
    "list_classification_schemes",
    "get_classification_nodes",
)

_MAX_RESULTS = 200


class _EdReadModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    as_of: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @computed_field
    @property
    def citation(self) -> dict[str, str]:
        types = {
            "EdUserProfileRead": "user",
            "EdOrganizationRead": "organization",
            "EdProjectRead": "project",
            "EdSymbolSetRead": "symbol_set",
            "EdSymbolRead": "symbol",
            "EdProjectContextRead": "project_context",
            "EdClassificationSchemeRead": "classification_scheme",
            "EdClassificationNodeRead": "classification_node",
            "EdClassificationNodesRead": "classification_nodes",
        }
        project = getattr(self, "project", None)
        record_type = types[type(self).__name__]
        raw_id = str(getattr(self, "id", None) or (project.id if project else "current"))
        opaque_id = uuid.uuid5(uuid.NAMESPACE_URL, f"symgov:ed:{record_type}:{raw_id}")
        return {
            "source_kind": "live_record",
            "record_type": record_type,
            "record_ref": f"live:{record_type}:{opaque_id}",
            "as_of": self.as_of.isoformat(),
        }


class EdUserProfileRead(_EdReadModel):
    id: str
    email: str
    display_name: str | None
    roles: tuple[str, ...]


class EdOrganizationRead(_EdReadModel):
    id: str
    code: str
    display_name: str
    role: str
    capabilities: tuple[str, ...]
    is_platform_admin: bool


class EdProjectRead(_EdReadModel):
    id: str
    code: str
    name: str
    short_description: str | None
    status: str


class EdSymbolSetItemRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    display_id: str
    name: str
    source: Literal["public", "organization_private"]
    sort_order: int
    group_name: str | None
    display_label: str | None
    preferred_format: str | None


class EdSymbolRevisionRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    revision_label: str
    lifecycle_state: str


class EdSymbolSetRead(_EdReadModel):
    id: str
    code: str
    name: str
    description: str | None
    disciplines: tuple[str, ...]
    use_cases: tuple[str, ...]
    status: str
    items: tuple[EdSymbolSetItemRead, ...] = ()


class EdSymbolRead(_EdReadModel):
    id: str
    display_id: str
    name: str
    slug: str
    category: str
    discipline: str
    source: Literal["public", "organization_private"]
    current_revision: EdSymbolRevisionRead | None = None


class EdProjectContextRead(_EdReadModel):
    project: EdProjectRead | None
    selected_symbol_set: EdSymbolSetRead | None


class EdClassificationProvenanceRead(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    dataset: str
    edition: int
    publication_year: int
    source_update_year: int
    page_url: str
    browse_url: str
    license_url: str
    license_code: str
    attribution: str
    # Decision 7.3: an ICS label may reach an answer only with this
    # codes-only clarification alongside the attribution and licence.
    clarification: str
    retrieved_at: str


class EdClassificationSchemeRead(_EdReadModel):
    id: str
    scheme_code: str
    name: str
    version_label: str
    status: str
    description: str | None
    provenance: EdClassificationProvenanceRead | None = None


class EdClassificationNodeRead(_EdReadModel):
    id: str
    scheme_id: str
    node_code: str
    parent_node_id: str | None
    preferred_label: str
    description: str | None
    sort_order: int
    status: str


class EdClassificationNodesRead(_EdReadModel):
    """Nodes from one scheme, with that scheme's third-party provenance once."""

    scheme_code: str
    provenance: EdClassificationProvenanceRead | None
    nodes: tuple[EdClassificationNodeRead, ...]


# Imported ICS schemes are coded ISO-ICS-<edition> (see ics_taxonomy.py).
ICS_SCHEME_PREFIX = "ISO-ICS-"


EdReadToolName = Literal[
    "get_current_user_profile",
    "list_accessible_organizations",
    "get_current_organization",
    "list_accessible_projects",
    "get_project_context",
    "list_accessible_symbol_sets",
    "get_symbol_set",
    "search_accessible_symbols",
    "get_symbol",
    "list_classification_schemes",
    "get_classification_nodes",
]


class EdReadToolCall(BaseModel):
    """Strict LLM-facing arguments; session scope is intentionally absent."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tool: EdReadToolName
    project_id: uuid.UUID | None = None
    symbol_set_id: uuid.UUID | None = None
    symbol_id: uuid.UUID | None = None
    query: str | None = Field(default=None, max_length=200)
    scheme_code: str | None = Field(default=None, max_length=64)
    parent_code: str | None = Field(default=None, max_length=128)
    include_closed: bool = False
    limit: int = Field(default=50, ge=1, le=_MAX_RESULTS)


@dataclass(frozen=True)
class EdReadAuthority:
    user: Any
    user_session: Any
    principal: Stage4Principal | None
    roles: tuple[str, ...] = ()


def _bounded_limit(value: int | None) -> int:
    if value is None:
        return _MAX_RESULTS
    return max(1, min(int(value), _MAX_RESULTS))


def _uuid(value: uuid.UUID | str) -> uuid.UUID | None:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError, AttributeError):
        return None


def _profile(user: User, *, roles: tuple[str, ...] | None = None) -> EdUserProfileRead:
    resolved_roles = roles if roles is not None else (getattr(user, "roles", ()) or ())
    return EdUserProfileRead(
        id=str(user.id),
        email=str(user.email),
        display_name=getattr(user, "display_name", None),
        roles=tuple(sorted(str(role) for role in resolved_roles)),
    )


def _organization(*, organization: Any, role: str, capabilities: tuple[str, ...] = (), is_platform_admin: bool = False) -> EdOrganizationRead:
    return EdOrganizationRead(
        id=str(getattr(organization, "organization_id", None) or organization.id),
        code=str(organization.code),
        display_name=str(organization.display_name),
        role=str(role),
        capabilities=tuple(str(value) for value in capabilities),
        is_platform_admin=bool(is_platform_admin),
    )


def _project(project: Any) -> EdProjectRead:
    return EdProjectRead(
        id=str(project.id),
        code=str(project.code),
        name=str(project.name),
        short_description=getattr(project, "short_description", None),
        status=str(project.status),
    )


def _symbol_set(
    symbol_set: Any, *, items: tuple[EdSymbolSetItemRead, ...] = ()
) -> EdSymbolSetRead:
    return EdSymbolSetRead(
        id=str(symbol_set.id),
        code=str(symbol_set.code),
        name=str(symbol_set.name),
        description=getattr(symbol_set, "description", None),
        disciplines=tuple(str(value) for value in (getattr(symbol_set, "disciplines_json", None) or ())),
        use_cases=tuple(str(value) for value in (getattr(symbol_set, "use_cases_json", None) or ())),
        status=str(symbol_set.status),
        items=items,
    )


def _symbol(
    session: Session,
    symbol: Any,
    *,
    revision: Any | None = None,
) -> EdSymbolRead:
    source = "public" if symbol.visibility == "public" else "organization_private"
    display_id = (
        symbol.catalog_symbol_id
        if source == "public"
        else governed_symbol_human_readable_id(session, symbol)
    ) or symbol.slug
    return EdSymbolRead(
        id=str(symbol.id),
        display_id=str(display_id),
        name=str(symbol.canonical_name),
        slug=str(symbol.slug),
        category=str(symbol.category),
        discipline=str(symbol.discipline),
        source=source,
        current_revision=(
            EdSymbolRevisionRead(
                revision_label=str(revision.revision_label),
                lifecycle_state=str(revision.lifecycle_state),
            )
            if revision is not None else None
        ),
    )


def get_current_user_profile(user: User) -> EdUserProfileRead:
    """Return the minimum authenticated profile Ed needs; never expose secrets."""
    return _profile(user)


def list_accessible_organizations(
    session: Session,
    user: User,
    settings: SymgovAPISettings,
    *,
    limit: int | None = None,
) -> tuple[EdOrganizationRead, ...]:
    """Use the existing authoritative membership resolver, not ad-hoc joins."""
    memberships = resolve_eligible_organization_memberships(session, user, settings)[:_bounded_limit(limit)]
    return tuple(
        _organization(
            organization=membership,
            role=membership.base_role,
            capabilities=membership.capabilities,
            is_platform_admin=membership.is_platform_admin,
        )
        for membership in memberships
    )


def get_current_organization(
    session: Session, principal: Stage4Principal, settings: SymgovAPISettings
) -> EdOrganizationRead:
    """Expose only the organization already bound to the authenticated session."""
    membership = next(
        (item for item in resolve_eligible_organization_memberships(session, principal.user, settings)
         if item.organization_id == principal.organization_id),
        None,
    )
    if membership is None:
        raise HTTPException(status_code=404, detail="Not found.")
    return _organization(
        organization=principal.organization,
        role=principal.role.base_role,
        capabilities=membership.capabilities,
        is_platform_admin=membership.is_platform_admin,
    )


def list_accessible_projects(
    session: Session,
    principal: Stage4Principal,
    *,
    include_closed: bool = False,
    limit: int | None = None,
) -> tuple[EdProjectRead, ...]:
    """List projects in the bound organization.

    Closed projects are never exposed to an ordinary user, even if a caller
    supplies ``include_closed=True``.  The LLM must not be able to widen this
    boundary by choosing tool arguments.
    """
    statement = select(Project).where(Project.organization_id == principal.organization_id)
    if not (include_closed and principal.is_admin):
        statement = statement.where(Project.status == "active")
    statement = statement.order_by(Project.normalized_code, Project.id).limit(_bounded_limit(limit))
    rows = session.execute(statement).scalars().all()
    return tuple(_project(row) for row in rows)


def _scoped_project_statement(principal: Stage4Principal, project_id: uuid.UUID):
    return select(Project).where(
        Project.id == project_id,
        Project.organization_id == principal.organization_id,
    )


def _selected_active_project_id(session: Session, principal: Stage4Principal) -> uuid.UUID | None:
    return session.execute(
        select(Project.id).join(
            UserSessionProjectContext,
            UserSessionProjectContext.project_id == Project.id,
        ).where(
            UserSessionProjectContext.user_session_id == principal.session.id,
            Project.organization_id == principal.organization_id,
            Project.status == "active",
        )
    ).scalars().first()


def get_project_context(
    session: Session, principal: Stage4Principal
) -> EdProjectContextRead:
    """Read the selected active project and resolve its set without repair writes."""
    context = session.execute(
        select(UserSessionProjectContext.project_id).where(
            UserSessionProjectContext.user_session_id == principal.session.id,
        )
    ).scalars().first()
    project = None
    selected_symbol_set = None
    if context is not None:
        project_id = _uuid(context)
        if project_id is not None:
            project = session.execute(_scoped_project_statement(principal, project_id)).scalars().first()
            if project is not None and project.status == "active":
                resolved_set, _source = _resolved_set(
                    session, principal, project, cleanup_stale=False
                )
                if (
                    resolved_set is not None
                    and resolved_set.owner_organization_id == principal.organization_id
                ):
                    selected_symbol_set = _symbol_set(resolved_set)
    return EdProjectContextRead(
        project=_project(project) if project is not None and project.status == "active" else None,
        selected_symbol_set=selected_symbol_set,
    )


def list_accessible_symbol_sets(
    session: Session,
    principal: Stage4Principal,
    *,
    project_id: uuid.UUID | str | None = None,
    limit: int | None = None,
) -> tuple[EdSymbolSetRead, ...]:
    """List sets linked to the selected active project, never a prompt-selected project."""
    selected_project_id = _selected_active_project_id(session, principal)
    if selected_project_id is None or (project_id is not None and _uuid(project_id) != selected_project_id):
        return ()
    statement = select(SymbolSet).join(
        ProjectSymbolSet,
        ProjectSymbolSet.symbol_set_id == SymbolSet.id,
    ).join(Project, Project.id == ProjectSymbolSet.project_id).where(
        SymbolSet.owner_organization_id == principal.organization_id,
        SymbolSet.status == "active",
        ProjectSymbolSet.project_id == selected_project_id,
        ProjectSymbolSet.status == "active",
        Project.organization_id == principal.organization_id,
        Project.status == "active",
    )
    statement = statement.order_by(SymbolSet.normalized_code, SymbolSet.id).limit(_bounded_limit(limit))
    rows = session.execute(statement).scalars().all()
    return tuple(_symbol_set(row) for row in rows)


def get_symbol_set(
    session: Session,
    principal: Stage4Principal,
    symbol_set_id: uuid.UUID | str,
    *,
    organization_symbols_enabled: bool = True,
) -> EdSymbolSetRead | None:
    """Resolve a set only inside the principal's organization."""
    resolved_id = _uuid(symbol_set_id)
    if resolved_id is None:
        return None
    selected_project_id = _selected_active_project_id(session, principal)
    if selected_project_id is None:
        return None
    row = session.execute(
        select(SymbolSet).join(
            ProjectSymbolSet,
            ProjectSymbolSet.symbol_set_id == SymbolSet.id,
        ).join(Project, Project.id == ProjectSymbolSet.project_id).where(
            SymbolSet.id == resolved_id,
            SymbolSet.owner_organization_id == principal.organization_id,
            SymbolSet.status == "active",
            ProjectSymbolSet.project_id == selected_project_id,
            ProjectSymbolSet.status == "active",
            Project.organization_id == principal.organization_id,
            Project.status == "active",
        )
    ).scalars().first()
    if row is None:
        return None
    item_statement = select(SymbolSetItem, GovernedSymbol).join(
        GovernedSymbol,
        GovernedSymbol.id == SymbolSetItem.governed_symbol_id,
    ).where(
        SymbolSetItem.symbol_set_id == row.id,
        SymbolSetItem.availability_status == "active",
    ).order_by(SymbolSetItem.sort_order, GovernedSymbol.canonical_name, GovernedSymbol.id)
    items: list[EdSymbolSetItemRead] = []
    offset = 0
    while len(items) < _MAX_RESULTS:
        item_rows = session.execute(
            item_statement.limit(_MAX_RESULTS).offset(offset)
        ).all()
        if not item_rows:
            break
        eligible = current_symbol_revisions(
            session,
            [symbol.id for _item, symbol in item_rows],
            principal.organization_id,
            organization_symbols_enabled=organization_symbols_enabled,
        )
        items.extend(
            EdSymbolSetItemRead(
                display_id=_symbol(session, symbol).display_id,
                name=str(symbol.canonical_name),
                source="public" if symbol.visibility == "public" else "organization_private",
                sort_order=int(item.sort_order),
                group_name=getattr(item, "group_name", None),
                display_label=getattr(item, "display_label", None),
                preferred_format=getattr(item, "preferred_format", None),
            )
            for item, symbol in item_rows
            if eligible.get(symbol.id) == symbol.current_revision_id
        )
        if len(item_rows) < _MAX_RESULTS:
            break
        offset += _MAX_RESULTS
    return _symbol_set(row, items=tuple(items[:_MAX_RESULTS]))


def _accessible_symbol_statement(
    principal: Stage4Principal,
    project_id: uuid.UUID,
    *,
    symbol_set_id: uuid.UUID | None = None,
    symbol_id: uuid.UUID | None = None,
    query: str | None = None,
):
    statement = select(GovernedSymbol).join(
        SymbolSetItem,
        SymbolSetItem.governed_symbol_id == GovernedSymbol.id,
    ).join(
        SymbolSet,
        SymbolSet.id == SymbolSetItem.symbol_set_id,
    ).join(
        ProjectSymbolSet,
        ProjectSymbolSet.symbol_set_id == SymbolSet.id,
    ).join(Project, Project.id == ProjectSymbolSet.project_id).where(
        Project.id == project_id,
        Project.organization_id == principal.organization_id,
        Project.status == "active",
        ProjectSymbolSet.status == "active",
        SymbolSet.owner_organization_id == principal.organization_id,
        SymbolSet.status == "active",
        SymbolSetItem.availability_status == "active",
    )
    if symbol_set_id is not None:
        statement = statement.where(SymbolSet.id == symbol_set_id)
    if symbol_id is not None:
        statement = statement.where(GovernedSymbol.id == symbol_id)
    if query:
        like = f"%{query.strip()}%"
        statement = statement.where(or_(
            GovernedSymbol.canonical_name.ilike(like),
            GovernedSymbol.slug.ilike(like),
            GovernedSymbol.category.ilike(like),
            GovernedSymbol.discipline.ilike(like),
            GovernedSymbol.catalog_symbol_id.ilike(like),
        ))
    return statement.distinct().order_by(GovernedSymbol.canonical_name, GovernedSymbol.id)


def search_accessible_symbols(
    session: Session,
    principal: Stage4Principal,
    *,
    query: str | None = None,
    symbol_set_id: uuid.UUID | str | None = None,
    limit: int | None = None,
    organization_symbols_enabled: bool = True,
) -> tuple[EdSymbolRead, ...]:
    """Read active items from active sets linked to the selected project.

    Final public/private visibility is delegated to the existing shared
    eligibility service; this query only narrows candidates and never grants
    visibility itself.
    """
    project_id = _selected_active_project_id(session, principal)
    resolved_set_id = _uuid(symbol_set_id) if symbol_set_id is not None else None
    if project_id is None or (symbol_set_id is not None and resolved_set_id is None):
        return ()
    candidate_limit = _bounded_limit(limit)
    base_statement = _accessible_symbol_statement(
        principal,
        project_id,
        symbol_set_id=resolved_set_id,
        query=query,
    )
    visible: list[EdSymbolRead] = []
    offset = 0
    while len(visible) < candidate_limit:
        candidates = session.execute(
            base_statement.limit(_MAX_RESULTS).offset(offset)
        ).scalars().all()
        if not candidates:
            break
        eligibility = current_symbol_revisions(
            session,
            [row.id for row in candidates],
            principal.organization_id,
            organization_symbols_enabled=organization_symbols_enabled,
        )
        visible.extend(
            _symbol(session, row)
            for row in candidates
            if eligibility.get(row.id) == row.current_revision_id
        )
        if len(candidates) < _MAX_RESULTS:
            break
        offset += _MAX_RESULTS
    return tuple(visible[:candidate_limit])


def get_symbol(
    session: Session,
    principal: Stage4Principal,
    symbol_id: uuid.UUID | str,
    *,
    organization_symbols_enabled: bool = True,
) -> EdSymbolRead | None:
    resolved_id = _uuid(symbol_id)
    project_id = _selected_active_project_id(session, principal)
    if resolved_id is None or project_id is None:
        return None
    candidates = session.execute(
        _accessible_symbol_statement(principal, project_id, symbol_id=resolved_id).limit(1)
    ).scalars().all()
    if not candidates:
        return None
    symbol = candidates[0]
    eligible = current_symbol_revisions(
        session,
        [symbol.id],
        principal.organization_id,
        organization_symbols_enabled=organization_symbols_enabled,
    )
    if eligible.get(symbol.id) != symbol.current_revision_id:
        return None
    revision = session.execute(
        select(SymbolRevision).where(
            SymbolRevision.id == symbol.current_revision_id,
            SymbolRevision.symbol_id == symbol.id,
        )
    ).scalars().first()
    return _symbol(session, symbol, revision=revision)


def _scheme_provenance(session: Session, scheme_id: object) -> EdClassificationProvenanceRead | None:
    """The latest stored ISO Open Data import for a scheme, if it has one."""
    provenance = session.execute(
        select(ICSTaxonomyImport).where(
            ICSTaxonomyImport.scheme_id == scheme_id
        ).order_by(
            ICSTaxonomyImport.retrieved_at.desc(),
            ICSTaxonomyImport.id.desc(),
        ).limit(1)
    ).scalars().first()
    if provenance is None:
        return None
    return EdClassificationProvenanceRead(
        dataset=str(provenance.dataset),
        edition=int(provenance.edition),
        publication_year=int(provenance.publication_year),
        source_update_year=int(provenance.source_update_year),
        page_url=str(provenance.page_url),
        browse_url=str(provenance.browse_url),
        license_url=str(provenance.license_url),
        license_code=str(provenance.license_code),
        attribution=str(provenance.attribution),
        clarification=str(provenance.clarification),
        retrieved_at=str(provenance.retrieved_at),
    )


def list_classification_schemes(
    session: Session, *, limit: int | None = None
) -> tuple[EdClassificationSchemeRead, ...]:
    statement = select(ClassificationScheme).where(
        ClassificationScheme.scope == "platform",
        ClassificationScheme.status == "active",
    ).order_by(ClassificationScheme.scheme_code, ClassificationScheme.id).limit(_bounded_limit(limit))
    rows = session.execute(statement).scalars().all()
    results = []
    for row in rows:
        results.append(EdClassificationSchemeRead(
            id=str(row.id),
            scheme_code=str(row.scheme_code),
            name=str(row.name),
            version_label=str(row.version_label),
            status=str(row.status),
            description=getattr(row, "description", None),
            provenance=_scheme_provenance(session, row.id),
        ))
    return tuple(results)


def get_classification_nodes(
    session: Session,
    scheme_code: str,
    *,
    parent_code: str | None = None,
    limit: int | None = None,
) -> EdClassificationNodesRead | tuple[()]:
    """Read active nodes from one active platform scheme, with bounded output.

    An imported ICS scheme's labels are returned only together with its
    stored attribution, licence and clarification; without a stored import
    none are returned (decision 7.3). Vendored metadata is never substituted.
    """
    normalized_scheme_code = str(scheme_code).strip().upper()
    scheme = session.execute(
        select(ClassificationScheme).where(
            ClassificationScheme.scheme_code == normalized_scheme_code,
            ClassificationScheme.scope == "platform",
            ClassificationScheme.status == "active",
        )
    ).scalars().first()
    if scheme is None:
        return ()
    provenance = _scheme_provenance(session, scheme.id)
    if provenance is None and str(scheme.scheme_code).startswith(ICS_SCHEME_PREFIX):
        return ()
    statement = select(ClassificationNode).where(
        ClassificationNode.scheme_id == scheme.id,
        ClassificationNode.status == "active",
    )
    if parent_code is None:
        statement = statement.where(ClassificationNode.parent_node_id.is_(None))
    else:
        parent = session.execute(
            select(ClassificationNode.id).where(
                ClassificationNode.scheme_id == scheme.id,
                ClassificationNode.node_code == str(parent_code).strip().upper(),
                ClassificationNode.status == "active",
            )
        ).scalars().first()
        if parent is None:
            return ()
        statement = statement.where(ClassificationNode.parent_node_id == parent)
    rows = session.execute(statement.order_by(ClassificationNode.sort_order, ClassificationNode.node_code).limit(_bounded_limit(limit))).scalars().all()
    if not rows:
        return ()
    nodes = tuple(
        EdClassificationNodeRead(
            id=str(row.id),
            scheme_id=str(row.scheme_id),
            node_code=str(row.node_code),
            parent_node_id=str(row.parent_node_id) if row.parent_node_id is not None else None,
            preferred_label=str(row.preferred_label),
            description=getattr(row, "description", None),
            sort_order=int(row.sort_order),
            status=str(row.status),
        )
        for row in rows
    )
    return EdClassificationNodesRead(
        scheme_code=str(scheme.scheme_code),
        provenance=provenance,
        nodes=nodes,
    )


def _aware(value: object) -> datetime:
    if not isinstance(value, datetime):
        return datetime.min.replace(tzinfo=timezone.utc)
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _resolve_ed_read_authority(
    session: Session,
    request: Request,
    settings: SymgovAPISettings,
) -> EdReadAuthority:
    """Revalidate cookie-bound identity without authentication maintenance writes."""
    token = request.cookies.get("symgov_session", "")
    if not token:
        raise HTTPException(status_code=401, detail="Authentication required.")
    token_hash = hash_session_token(token)
    probe = session.query(UserSession).filter(UserSession.token_hash == token_hash).one_or_none()
    if probe is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    current = session.query(UserSession).filter(
        UserSession.id == probe.id,
        UserSession.token_hash == token_hash,
    ).populate_existing().with_for_update(read=True).one_or_none()
    user = session.query(User).filter(User.id == probe.auth_user_id).populate_existing().with_for_update(read=True).one_or_none()
    now = datetime.now(timezone.utc)
    if (
        current is None
        or user is None
        or current.auth_user_id != probe.auth_user_id
        or current.revoked_at is not None
        or _aware(current.expires_at) <= now
        or current.purpose != "application"
        or not user.is_active
        or user.deleted_at is not None
    ):
        raise HTTPException(status_code=401, detail="Authentication required.")
    if user.must_change_pin:
        raise HTTPException(status_code=403, detail="PIN change is required before accessing this operation.")
    roles = tuple(
        session.execute(
            select(UserRole.role).where(UserRole.user_id == user.id).order_by(UserRole.role)
        ).scalars().all()
    )
    if current.session_mode == "personal" and current.active_organization_id is None:
        return EdReadAuthority(user=user, user_session=current, principal=None, roles=roles)
    if current.session_mode != "organization" or current.active_organization_id is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    principal = require_stage4_principal(session, request, settings)
    if principal.user.id != user.id or principal.session.id != current.id:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return EdReadAuthority(user=user, user_session=current, principal=principal, roles=roles)


def _require_organization_authority(authority: EdReadAuthority) -> Stage4Principal:
    if authority.principal is None:
        raise HTTPException(status_code=403, detail="An organization-bound session is required.")
    return authority.principal


def _required(value: Any, field: str) -> Any:
    if value is None or (isinstance(value, str) and not value.strip()):
        raise HTTPException(status_code=422, detail=f"{field} is required for this read tool.")
    return value


def execute_ed_read_tool(
    session: Session,
    request: Request,
    settings: SymgovAPISettings,
    call: EdReadToolCall,
) -> Any:
    """Single read-only dispatch boundary for the future Stage 3 orchestrator."""
    authority = _resolve_ed_read_authority(session, request, settings)
    if call.tool == "get_current_user_profile":
        return _profile(authority.user, roles=authority.roles or None)
    if call.tool == "list_accessible_organizations":
        return list_accessible_organizations(session, authority.user, settings, limit=call.limit)
    if call.tool == "list_classification_schemes":
        return list_classification_schemes(session, limit=call.limit)
    if call.tool == "get_classification_nodes":
        return get_classification_nodes(
            session,
            _required(call.scheme_code, "scheme_code"),
            parent_code=call.parent_code,
            limit=call.limit,
        )

    principal = _require_organization_authority(authority)
    if call.tool == "get_current_organization":
        return get_current_organization(session, principal, settings)
    if call.tool == "list_accessible_projects":
        return list_accessible_projects(
            session,
            principal,
            include_closed=call.include_closed,
            limit=call.limit,
        )
    if call.tool == "get_project_context":
        return get_project_context(session, principal)
    if call.tool == "list_accessible_symbol_sets":
        return list_accessible_symbol_sets(
            session,
            principal,
            project_id=call.project_id,
            limit=call.limit,
        )
    if call.tool == "get_symbol_set":
        return get_symbol_set(
            session,
            principal,
            _required(call.symbol_set_id, "symbol_set_id"),
            organization_symbols_enabled=bool(settings.organization_symbols_enabled),
        )
    if call.tool == "search_accessible_symbols":
        return search_accessible_symbols(
            session,
            principal,
            query=call.query,
            symbol_set_id=call.symbol_set_id,
            limit=call.limit,
            organization_symbols_enabled=bool(settings.organization_symbols_enabled),
        )
    if call.tool == "get_symbol":
        return get_symbol(
            session,
            principal,
            _required(call.symbol_id, "symbol_id"),
            organization_symbols_enabled=bool(settings.organization_symbols_enabled),
        )
    raise HTTPException(status_code=422, detail="Unsupported Ed read tool.")
