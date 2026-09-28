from __future__ import annotations

import importlib
from types import SimpleNamespace
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy.dialects import sqlite


ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")
PROJECT_ID = uuid.UUID("33333333-3333-3333-3333-333333333333")
SET_ID = uuid.UUID("44444444-4444-4444-4444-444444444444")


def _module():
    return importlib.import_module("symgov_backend.ed_read_tools")


def _principal(*, admin: bool = False):
    return SimpleNamespace(
        user=SimpleNamespace(id=USER_ID),
        session=SimpleNamespace(id=uuid.UUID("55555555-5555-5555-5555-555555555555")),
        organization=SimpleNamespace(
            id=ORG_ID,
            code="ACME",
            display_name="Acme Engineering",
            locale="en-US",
            entitlement_status="active",
        ),
        membership=SimpleNamespace(status="active"),
        role=SimpleNamespace(base_role="admin" if admin else "user"),
        organization_id=ORG_ID,
        is_admin=admin,
    )


class _Result:
    def __init__(self, rows=(), scalar=None):
        self.rows = list(rows)
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.rows

    def one_or_none(self):
        return self.scalar

    def first(self):
        return self.scalar


class _Session:
    def __init__(self, rows=(), scalar=None):
        self.rows = rows
        self.scalar = scalar
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.rows, self.scalar)


def _compiled(statement):
    return str(statement.compile(dialect=sqlite.dialect()))


def test_tool_registry_is_read_only_and_names_requested_domains():
    module = _module()

    assert module.ED_READ_TOOL_NAMES == (
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
    assert all(not name.endswith(("_create", "_update", "_delete")) for name in module.ED_READ_TOOL_NAMES)


def test_list_accessible_organizations_uses_authoritative_membership_resolver(monkeypatch):
    module = _module()
    membership = SimpleNamespace(
        organization_id=ORG_ID,
        code="ACME",
        display_name="Acme Engineering",
        base_role="user",
        capabilities=("contributor",),
        is_platform_admin=False,
    )
    monkeypatch.setattr(module, "resolve_eligible_organization_memberships", lambda *args: (membership,))

    result = module.list_accessible_organizations(object(), object(), object())

    assert result == (
        module.EdOrganizationRead(
            as_of=result[0].as_of,
            id=str(ORG_ID),
            code="ACME",
            display_name="Acme Engineering",
            role="user",
            capabilities=("contributor",),
            is_platform_admin=False,
        ),
    )


def test_list_accessible_organizations_bounds_authoritative_results(monkeypatch):
    module = _module()
    memberships = tuple(
        SimpleNamespace(
            organization_id=uuid.uuid4(),
            code=f"ORG-{index}",
            display_name=f"Organization {index}",
            base_role="user",
            capabilities=(),
            is_platform_admin=False,
        )
        for index in range(201)
    )
    monkeypatch.setattr(module, "resolve_eligible_organization_memberships", lambda *args: memberships)

    assert len(module.list_accessible_organizations(object(), object(), object())) == 200
    assert len(module.list_accessible_organizations(object(), object(), object(), limit=10)) == 10


def test_get_current_organization_returns_only_bound_principal_context(monkeypatch):
    module = _module()
    monkeypatch.setattr(module, "resolve_eligible_organization_memberships", lambda *args: (
        SimpleNamespace(organization_id=ORG_ID, capabilities=(), is_platform_admin=False),
    ))

    result = module.get_current_organization(object(), _principal(), object())

    assert result == module.EdOrganizationRead(
        as_of=result.as_of,
        id=str(ORG_ID),
        code="ACME",
        display_name="Acme Engineering",
        role="user",
        capabilities=(),
        is_platform_admin=False,
    )


def test_list_projects_is_scoped_to_principal_organization_and_active_rows():
    module = _module()
    project = SimpleNamespace(
        id=PROJECT_ID,
        organization_id=ORG_ID,
        code="P-01",
        name="Plant Upgrade",
        short_description="Phase one",
        status="active",
        external_reference=None,
    )
    session = _Session(rows=(project,))

    result = module.list_accessible_projects(session, _principal())

    assert result == (module.EdProjectRead(
        as_of=result[0].as_of,
        id=str(PROJECT_ID),
        code="P-01",
        name="Plant Upgrade",
        short_description="Phase one",
        status="active",
    ),)
    statement = _compiled(session.statements[0])
    assert "projects.organization_id =" in statement
    assert "projects.status =" in statement


def test_list_projects_does_not_widen_to_closed_rows_for_non_admin():
    module = _module()
    session = _Session()

    module.list_accessible_projects(session, _principal(), include_closed=True)

    statement = _compiled(session.statements[0])
    assert "projects.status =" in statement


def test_list_symbol_sets_is_scoped_to_principal_organization_and_project():
    module = _module()
    symbol_set = SimpleNamespace(
        id=SET_ID,
        owner_organization_id=ORG_ID,
        code="SET-01",
        name="Plant symbols",
        description="Approved plant set",
        disciplines_json=["Piping / P&ID"],
        use_cases_json=["Design"],
        status="active",
    )
    class SessionWithProject(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            return _Result(
                rows=(symbol_set,) if len(self.statements) > 1 else (),
                scalar=PROJECT_ID if len(self.statements) == 1 else None,
            )
    session = SessionWithProject()

    result = module.list_accessible_symbol_sets(session, _principal(), project_id=PROJECT_ID)

    assert result == (module.EdSymbolSetRead(
        as_of=result[0].as_of,
        id=str(SET_ID),
        code="SET-01",
        name="Plant symbols",
        description="Approved plant set",
        disciplines=("Piping / P&ID",),
        use_cases=("Design",),
        status="active",
    ),)
    statement = _compiled(session.statements[-1])
    assert "symbol_sets.owner_organization_id =" in statement
    assert "project_symbol_sets.project_id =" in statement
    assert "project_symbol_sets.status =" in statement
    assert "symbol_sets.status =" in statement


def test_get_symbol_set_returns_none_for_unresolved_or_cross_scope_lookup():
    module = _module()
    class SessionWithProject(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            return _Result(scalar=PROJECT_ID if len(self.statements) == 1 else None)
    session = SessionWithProject()

    assert module.get_symbol_set(session, _principal(), SET_ID) is None
    statement = _compiled(session.statements[-1])
    assert "symbol_sets.owner_organization_id =" in statement


def test_classification_readers_only_return_active_governed_rows():
    module = _module()
    scheme = SimpleNamespace(
        id=uuid.UUID("66666666-6666-6666-6666-666666666666"),
        scheme_code="ENGINEERING-DISCIPLINE",
        name="Engineering Discipline",
        version_label="1",
        status="active",
        description="Disciplines",
    )
    session = _Session(rows=(scheme,))

    result = module.list_classification_schemes(session)

    assert result == (module.EdClassificationSchemeRead(
        as_of=result[0].as_of,
        id=str(scheme.id),
        scheme_code="ENGINEERING-DISCIPLINE",
        name="Engineering Discipline",
        version_label="1",
        status="active",
        description="Disciplines",
    ),)
    statement = _compiled(session.statements[0])
    assert "classification_schemes.status =" in statement


def test_organization_admin_does_not_claim_platform_admin_without_platform_assignment(monkeypatch):
    module = _module()
    principal = _principal(admin=True)
    principal.organization.normalized_code = "symgov"
    membership = SimpleNamespace(
        organization_id=ORG_ID, code="symgov", display_name="Symgov",
        base_role="admin", capabilities=(), is_platform_admin=False,
    )
    monkeypatch.setattr(module, "resolve_eligible_organization_memberships", lambda *args: (membership,))

    assert module.get_current_organization(object(), principal, object()).is_platform_admin is False


def test_closed_selected_project_does_not_expose_its_selected_set(monkeypatch):
    module = _module()
    class ContextSession:
        def execute(self, statement):
            name = str(statement.column_descriptions[0]["name"])
            if name == "project_id":
                return _Result(scalar=PROJECT_ID)
            if name == "Project":
                return _Result(scalar=SimpleNamespace(
                    id=PROJECT_ID, code="P-01", name="Closed", short_description=None, status="closed",
                ))
            raise AssertionError(f"unexpected read after closed project: {name}")

    monkeypatch.setattr(module, "get_symbol_set", lambda *args: 1 / 0)
    result = module.get_project_context(ContextSession(), _principal())
    assert result.project is None
    assert result.selected_symbol_set is None


def test_get_symbol_set_requires_an_active_selected_project():
    module = _module()
    class NoContextSession:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            if statement.column_descriptions[0]["name"] == "id":
                return _Result(scalar=None)
            return _Result(scalar=SimpleNamespace(id=SET_ID, code="UNLINKED", name="Private", status="active"))

    session = NoContextSession()
    assert module.get_symbol_set(session, _principal(), SET_ID) is None
    assert len(session.statements) == 1


def test_symbol_set_lookup_requires_active_link_to_selected_project():
    module = _module()
    class SessionWithProject(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            return _Result(scalar=PROJECT_ID if len(self.statements) == 1 else None)
    session = SessionWithProject()
    module.get_symbol_set(session, _principal(), SET_ID)
    lookup = _compiled(session.statements[-1])
    assert "project_symbol_sets.project_id =" in lookup
    assert "project_symbol_sets.status =" in lookup
    assert "projects.status =" in lookup
    assert "symbol_sets.owner_organization_id =" in lookup


def test_prompt_supplied_project_cannot_override_session_project():
    module = _module()
    class ContextSession:
        def __init__(self):
            self.statements = []

        def execute(self, statement):
            self.statements.append(statement)
            return _Result(
                rows=(SimpleNamespace(id=SET_ID, code="LEAK", name="Private", status="active"),),
                scalar=PROJECT_ID,
            )

    session = ContextSession()
    foreign_project = uuid.UUID("99999999-9999-9999-9999-999999999999")
    assert module.list_accessible_symbol_sets(session, _principal(), project_id=foreign_project) == ()
    assert len(session.statements) == 1


def test_live_records_expose_stable_source_reference_and_fresh_as_of():
    from datetime import datetime, timezone
    module = _module()
    profile = module.get_current_user_profile(SimpleNamespace(
        id=USER_ID, email="member@example.test", display_name="Member", roles=(),
    ))

    citation = profile.model_dump()["citation"]
    assert citation["source_kind"] == "live_record"
    assert citation["record_type"] == "user"
    assert citation["record_ref"].startswith("live:user:")
    assert str(USER_ID) not in citation["record_ref"]
    assert datetime.fromisoformat(citation["as_of"]).tzinfo is not None
    assert datetime.fromisoformat(citation["as_of"]) <= datetime.now(timezone.utc)


def test_real_queries_hide_unlinked_and_cross_organization_sets_without_writes():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from symgov_backend.models import (
        GovernedSymbol,
        Project,
        ProjectSymbolSet,
        SymbolSet,
        SymbolSetItem,
        UserSessionProjectContext,
    )

    module = _module()
    engine = create_engine("sqlite:///:memory:")
    # Disposable SQL tables retain mapped column names and bind/result types,
    # without PostgreSQL-only checks or defaults irrelevant to these reads.
    with engine.begin() as connection:
        for model in (
            Project,
            SymbolSet,
            ProjectSymbolSet,
            UserSessionProjectContext,
            SymbolSetItem,
            GovernedSymbol,
        ):
            table = model.__table__
            columns = ", ".join(f'"{column.name}" TEXT' for column in table.columns)
            connection.exec_driver_sql(f'CREATE TABLE "{table.name}" ({columns})')

    foreign_org = uuid.UUID("99999999-9999-9999-9999-999999999999")
    linked, unlinked, foreign, inactive = (uuid.uuid4() for _ in range(4))
    with Session(engine, autoflush=False) as session:
        session.execute(Project.__table__.insert().values(
            id=PROJECT_ID, organization_id=ORG_ID, code="P-01", normalized_code="p-01",
            name="Active project", status="active",
        ))
        session.execute(UserSessionProjectContext.__table__.insert().values(
            user_session_id=_principal().session.id, project_id=PROJECT_ID,
        ))
        for identifier, owner, status in (
            (linked, ORG_ID, "active"), (unlinked, ORG_ID, "active"),
            (foreign, foreign_org, "active"), (inactive, ORG_ID, "archived"),
        ):
            session.execute(SymbolSet.__table__.insert().values(
                id=identifier, owner_organization_id=owner, code=str(identifier)[:8],
                normalized_code=str(identifier)[:8], name="Test set", status=status,
            ))
        for identifier in (linked, foreign, inactive):
            session.execute(ProjectSymbolSet.__table__.insert().values(
                id=uuid.uuid4(), project_id=PROJECT_ID, symbol_set_id=identifier,
                status="active", is_default=False,
            ))
        session.flush()
        before = session.dirty.copy()
        assert [row.id for row in module.list_accessible_symbol_sets(session, _principal())] == [str(linked)]
        assert module.get_symbol_set(session, _principal(), linked).id == str(linked)
        for hidden in (unlinked, foreign, inactive):
            assert module.get_symbol_set(session, _principal(), hidden) is None
        assert session.dirty == before
        session.execute(Project.__table__.update().where(Project.id == PROJECT_ID).values(status="closed"))
        assert module.list_accessible_symbol_sets(session, _principal()) == ()
        assert module.get_symbol_set(session, _principal(), linked) is None


def test_tool_call_is_typed_bounded_and_rejects_prompt_scope_fields():
    module = _module()

    assert module.EdReadToolCall(tool="search_accessible_symbols", query="valve", limit=20).limit == 20
    with pytest.raises(ValidationError):
        module.EdReadToolCall(tool="search_accessible_symbols", limit=201)
    with pytest.raises(ValidationError):
        module.EdReadToolCall(
            tool="search_accessible_symbols",
            user_id=str(USER_ID),
            organization_id=str(ORG_ID),
        )


def test_personal_mode_entrypoint_allows_safe_profile_but_denies_organization_tools(monkeypatch):
    module = _module()
    authority = module.EdReadAuthority(
        user=SimpleNamespace(id=USER_ID, email="member@example.test", display_name="Member", roles=()),
        user_session=SimpleNamespace(session_mode="personal"),
        principal=None,
    )
    monkeypatch.setattr(module, "_resolve_ed_read_authority", lambda *args: authority)

    profile = module.execute_ed_read_tool(
        object(), object(), object(), module.EdReadToolCall(tool="get_current_user_profile")
    )
    assert profile.id == str(USER_ID)
    with pytest.raises(module.HTTPException) as denied:
        module.execute_ed_read_tool(
            object(), object(), object(), module.EdReadToolCall(tool="list_accessible_projects")
        )
    assert denied.value.status_code == 403


def test_personal_mode_authority_is_revalidated_from_cookie_without_writes():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from starlette.requests import Request
    from symgov_backend.models import User, UserRole, UserSession
    from symgov_backend.settings import SymgovAPISettings

    module = _module()
    engine = create_engine("sqlite:///:memory:")
    for table in (User.__table__, UserRole.__table__, UserSession.__table__):
        table.create(engine)
    now = datetime.now(timezone.utc)
    token = "ed-personal-session"
    user_id = uuid.UUID("aaaaaaaa-2222-2222-2222-222222222222")
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        session.add(User(
            id=user_id,
            email="member@example.test",
            display_name="Member",
            pin_hash="not-read",
            pin_set_at=now,
            must_change_pin=False,
            is_active=True,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        ))
        session.add(UserRole(user_id=user_id, role="reviewer", created_at=now))
        session.add(UserSession(
            id=uuid.UUID("bbbbbbbb-5555-5555-5555-555555555555"),
            auth_user_id=user_id,
            token_hash=module.hash_session_token(token),
            created_at=now,
            expires_at=now + timedelta(hours=1),
            revoked_at=None,
            last_seen_at=None,
            purpose="application",
            session_mode="personal",
            active_organization_id=None,
            recent_step_up_at=None,
        ))
        session.commit()
        request = Request({
            "type": "http",
            "method": "POST",
            "path": "/api/v1/ed/chat",
            "headers": [(b"cookie", f"symgov_session={token}".encode())],
        })

        before = (set(session.new), set(session.dirty), set(session.deleted))
        profile = module.execute_ed_read_tool(
            session,
            request,
            SymgovAPISettings(),
            module.EdReadToolCall(tool="get_current_user_profile"),
        )

        assert profile.id == str(user_id)
        assert profile.roles == ("reviewer",)
        assert (set(session.new), set(session.dirty), set(session.deleted)) == before


def test_symbol_queries_require_active_project_link_set_item_and_shared_eligibility(monkeypatch):
    module = _module()
    symbol_id = uuid.UUID("77777777-7777-7777-7777-777777777777")
    symbol = SimpleNamespace(
        id=symbol_id,
        catalog_symbol_id=None,
        slug="gate-valve",
        canonical_name="Gate valve",
        category="Valve",
        discipline="Piping",
        visibility="organization_private",
        owner_organization_id=ORG_ID,
        current_revision_id=uuid.UUID("88888888-8888-8888-8888-888888888888"),
    )

    class SymbolSession(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return _Result(scalar=PROJECT_ID)
            return _Result(rows=(symbol,))

    monkeypatch.setattr(
        module,
        "current_symbol_revisions",
        lambda session, ids, organization_id, **kwargs: {symbol_id: symbol.current_revision_id},
    )
    monkeypatch.setattr(module, "governed_symbol_human_readable_id", lambda session, row: "ABCD-7")
    session = SymbolSession()
    rows = module.search_accessible_symbols(session, _principal(), query="valve", limit=10)

    assert [row.id for row in rows] == [str(symbol_id)]
    assert rows[0].display_id == "ABCD-7"
    statement = _compiled(session.statements[-1])
    assert "symbol_set_items.availability_status =" in statement
    assert "symbol_sets.status =" in statement
    assert "project_symbol_sets.status =" in statement
    assert "projects.status =" in statement
    assert "projects.organization_id =" in statement


def test_symbol_lookup_cannot_escape_selected_project_or_visibility_service(monkeypatch):
    module = _module()
    foreign_symbol_id = uuid.UUID("99999999-9999-9999-9999-999999999999")

    class SymbolSession(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return _Result(scalar=PROJECT_ID)
            return _Result(rows=(SimpleNamespace(id=foreign_symbol_id, current_revision_id=uuid.uuid4()),))

    monkeypatch.setattr(module, "current_symbol_revisions", lambda *args, **kwargs: {})
    session = SymbolSession()

    assert module.get_symbol(session, _principal(), foreign_symbol_id) is None
    assert len(session.statements) == 2


def test_project_context_uses_read_only_resolver_fallback_without_leaking_stale_selection(monkeypatch):
    module = _module()
    project = SimpleNamespace(
        id=PROJECT_ID, code="P-01", name="Plant", short_description=None, status="active",
    )
    fallback_set = SimpleNamespace(
        id=SET_ID, owner_organization_id=ORG_ID, code="DEFAULT", name="Default", description=None,
        disciplines_json=[], use_cases_json=[], status="active",
    )

    class ContextSession(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return _Result(scalar=PROJECT_ID)
            return _Result(scalar=project)

    calls = []

    def resolved_set(session, principal, selected_project, *, cleanup_stale=True):
        calls.append((selected_project.id, cleanup_stale))
        return fallback_set, "project_default"

    monkeypatch.setattr(module, "_resolved_set", resolved_set)
    result = module.get_project_context(ContextSession(), _principal())

    assert result.project.id == str(PROJECT_ID)
    assert result.selected_symbol_set.id == str(SET_ID)
    assert calls == [(PROJECT_ID, False)]


def test_symbol_search_pages_past_ineligible_first_batch(monkeypatch):
    module = _module()
    revision_id = uuid.uuid4()
    candidates = [
        SimpleNamespace(
            id=uuid.uuid4(), catalog_symbol_id=f"SYM-{index:03}", slug=f"symbol-{index:03}",
            canonical_name=f"Symbol {index:03}", category="Valve", discipline="Piping",
            visibility="public", current_revision_id=revision_id,
        )
        for index in range(201)
    ]

    class PagingSession(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return _Result(scalar=PROJECT_ID)
            page = len(self.statements) - 2
            return _Result(rows=tuple(candidates[page * 200:(page + 1) * 200]))

    monkeypatch.setattr(
        module,
        "current_symbol_revisions",
        lambda session, ids, organization_id, **kwargs: (
            {candidates[-1].id: revision_id} if candidates[-1].id in ids else {}
        ),
    )

    rows = module.search_accessible_symbols(PagingSession(), _principal(), query="symbol", limit=1)

    assert [row.id for row in rows] == [str(candidates[-1].id)]


def test_symbol_set_pages_past_ineligible_items_and_caps_visible_results(monkeypatch):
    module = _module()
    revision_id = uuid.uuid4()
    symbol_set = SimpleNamespace(
        id=SET_ID, code="SET-01", name="Plant", description=None,
        disciplines_json=[], use_cases_json=[], status="active",
    )
    item_rows = []
    for index in range(401):
        symbol_id = uuid.uuid4()
        item_rows.append((
            SimpleNamespace(
                governed_symbol_id=symbol_id, sort_order=1, group_name=None,
                display_label=None, preferred_format=None,
            ),
            SimpleNamespace(
                id=symbol_id, catalog_symbol_id=f"SYM-{index:03}", slug=f"symbol-{index:03}",
                canonical_name="Duplicate name", category="Valve", discipline="Piping",
                visibility="public", current_revision_id=revision_id,
            ),
        ))

    class PagingSession(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            call = len(self.statements)
            if call == 1:
                return _Result(scalar=PROJECT_ID)
            if call == 2:
                return _Result(scalar=symbol_set)
            page = call - 3
            return _Result(rows=tuple(item_rows[page * 200:(page + 1) * 200]))

    eligibility_batches = []

    def eligible_revisions(session, ids, organization_id, **kwargs):
        eligibility_batches.append(tuple(ids))
        return {
            symbol_id: revision_id
            for symbol_id in ids
            if symbol_id not in {symbol.id for _item, symbol in item_rows[:201]}
        }

    monkeypatch.setattr(module, "current_symbol_revisions", eligible_revisions)
    session = PagingSession()

    result = module.get_symbol_set(session, _principal(), SET_ID)

    assert len(result.items) == 200
    assert result.items[0].display_id == "SYM-201"
    assert result.items[-1].display_id == "SYM-400"
    assert [len(batch) for batch in eligibility_batches] == [200, 200, 1]
    item_query = _compiled(session.statements[2])
    assert "symbol_set_items.sort_order" in item_query
    assert "governed_symbols.canonical_name" in item_query
    assert "governed_symbols.id" in item_query


def test_symbol_set_and_symbol_include_bounded_safe_current_summaries(monkeypatch):
    module = _module()
    symbol_id = uuid.uuid4()
    revision_id = uuid.uuid4()
    symbol_set = SimpleNamespace(
        id=SET_ID, code="SET-01", name="Plant", description=None,
        disciplines_json=[], use_cases_json=[], status="active",
    )
    symbol = SimpleNamespace(
        id=symbol_id, catalog_symbol_id="PUB-1", slug="gate-valve", canonical_name="Gate valve",
        category="Valve", discipline="Piping", visibility="public", current_revision_id=revision_id,
    )
    item = SimpleNamespace(
        governed_symbol_id=symbol_id, sort_order=7, group_name="Valves",
        display_label="Main isolation", preferred_format="SVG",
    )
    revision = SimpleNamespace(revision_label="R2", lifecycle_state="published")

    class ProjectionSession(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            call = len(self.statements)
            if call in (1, 4):
                return _Result(scalar=PROJECT_ID)
            if call == 2:
                return _Result(scalar=symbol_set)
            if call == 3:
                return _Result(rows=((item, symbol),))
            if call == 5:
                return _Result(rows=(symbol,))
            return _Result(scalar=revision)

    monkeypatch.setattr(
        module, "current_symbol_revisions",
        lambda session, ids, organization_id, **kwargs: {symbol_id: revision_id},
    )
    session = ProjectionSession()

    set_result = module.get_symbol_set(session, _principal(), SET_ID)
    symbol_result = module.get_symbol(session, _principal(), symbol_id)

    assert len(set_result.items) == 1
    assert set_result.items[0].display_id == "PUB-1"
    assert set_result.items[0].display_label == "Main isolation"
    assert symbol_result.current_revision.revision_label == "R2"
    assert symbol_result.current_revision.lifecycle_state == "published"
    assert "payload" not in symbol_result.model_dump_json()


def test_classification_scheme_includes_safe_latest_ics_provenance():
    module = _module()
    scheme_id = uuid.uuid4()
    scheme = SimpleNamespace(
        id=scheme_id, scheme_code="ICS", name="ICS", version_label="7", status="active",
        description="International Classification for Standards",
    )
    provenance = SimpleNamespace(
        dataset="ISO ICS", edition=7, publication_year=2015, source_update_year=2024,
        page_url="https://example.test/ics", browse_url="https://example.test/browse",
        license_url="https://example.test/licence", license_code="ISO-OD",
        attribution="ISO Open Data", retrieved_at="2026-09-01T00:00:00+00:00",
    )

    class SchemeSession(_Session):
        def execute(self, statement):
            self.statements.append(statement)
            if len(self.statements) == 1:
                return _Result(rows=(scheme,))
            return _Result(scalar=provenance)

    result = module.list_classification_schemes(SchemeSession())

    assert result[0].provenance.dataset == "ISO ICS"
    assert result[0].provenance.license_code == "ISO-OD"
    assert result[0].provenance.page_url == "https://example.test/ics"


def test_organization_mode_dispatch_revalidates_authority_and_performs_no_dml():
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import Boolean, DateTime, create_engine, event
    from sqlalchemy.orm import Session
    from starlette.requests import Request
    from symgov_backend.models import (
        Organization,
        OrganizationMembership,
        OrganizationRoleAssignment,
        Project,
        User,
        UserRole,
        UserSession,
    )
    from symgov_backend.settings import SymgovAPISettings

    module = _module()
    engine = create_engine("sqlite:///:memory:")
    models = (
        User,
        UserRole,
        UserSession,
        Organization,
        OrganizationMembership,
        OrganizationRoleAssignment,
        Project,
    )
    with engine.begin() as connection:
        for model in models:
            declarations = []
            for column in model.__table__.columns:
                sql_type = "BOOLEAN" if isinstance(column.type, Boolean) else (
                    "DATETIME" if isinstance(column.type, DateTime) else "TEXT"
                )
                declarations.append(f'"{column.name}" {sql_type}')
            connection.exec_driver_sql(
                f'CREATE TABLE "{model.__tablename__}" ({", ".join(declarations)})'
            )

    now = datetime.now(timezone.utc)
    token = "ed-organization-session"
    membership_id = uuid.uuid4()
    role_id = uuid.uuid4()
    session_id = uuid.uuid4()
    closed_project_id = uuid.uuid4()
    statements = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda connection, cursor, statement, parameters, context, executemany: statements.append(statement),
    )
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        session.add(User(
            id=USER_ID, email="member@example.test", display_name="Member", pin_hash="unused",
            pin_set_at=now, must_change_pin=False, is_active=True, created_at=now,
            updated_at=now, deleted_at=None,
        ))
        session.add(Organization(
            id=ORG_ID, code="ACME", normalized_code="acme", display_name="Acme",
            legal_name=None, name_key="acme", legal_name_key=None, locale="en-US",
            entitlement_status="active", is_active=True, is_protected=False,
            icon_seed_version="v1", fallback_icon_svg="<svg/>", default_symbol_set_id=None,
            created_at=now, updated_at=now,
        ))
        session.add(UserSession(
            id=session_id, auth_user_id=USER_ID, token_hash=module.hash_session_token(token),
            created_at=now, expires_at=now + timedelta(hours=1), revoked_at=None,
            last_seen_at=None, purpose="application", session_mode="organization",
            active_organization_id=ORG_ID, recent_step_up_at=None,
        ))
        session.add(OrganizationMembership(
            id=membership_id, organization_id=ORG_ID, user_id=USER_ID, status="active",
            invited_at=None, activated_at=now, deactivated_at=None, created_at=now, updated_at=now,
        ))
        role = OrganizationRoleAssignment(
            id=role_id, membership_id=membership_id, base_role="user", is_active=True,
            assigned_at=now, assigned_by_user_id=None, revoked_at=None,
            revoked_by_user_id=None, revoke_reason=None,
        )
        session.add(role)
        for identifier, code, status in (
            (PROJECT_ID, "ACTIVE", "active"),
            (closed_project_id, "CLOSED", "closed"),
        ):
            session.add(Project(
                id=identifier, organization_id=ORG_ID, code=code, normalized_code=code.lower(),
                name=code.title(), short_description=None, status=status, external_reference=None,
                metadata_json={}, created_by_user_id=USER_ID, created_at=now, updated_at=now,
                closed_at=now if status == "closed" else None,
            ))
        session.commit()
        request = Request({
            "type": "http", "method": "POST", "path": "/api/v1/ed/chat",
            "headers": [(b"cookie", f"symgov_session={token}".encode())],
        })
        settings = SymgovAPISettings(organizations_enabled=True, symbol_sets_enabled=True)

        statements.clear()
        ordinary = module.execute_ed_read_tool(
            session, request, settings,
            module.EdReadToolCall(tool="list_accessible_projects", include_closed=True),
        )
        assert [row.id for row in ordinary] == [str(PROJECT_ID)]
        assert all(not sql.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for sql in statements)

        role.base_role = "admin"
        session.commit()
        admin = module.execute_ed_read_tool(
            session, request, settings,
            module.EdReadToolCall(tool="list_accessible_projects", include_closed=True),
        )
        assert {row.id for row in admin} == {str(PROJECT_ID), str(closed_project_id)}

        for field, value, expected_status in (
            ("revoked_at", now, 401),
            ("expires_at", now - timedelta(seconds=1), 401),
        ):
            setattr(session.get(UserSession, session_id), field, value)
            session.commit()
            with pytest.raises(module.HTTPException) as denied:
                module.execute_ed_read_tool(
                    session, request, settings,
                    module.EdReadToolCall(tool="get_current_user_profile"),
                )
            assert denied.value.status_code == expected_status
            setattr(session.get(UserSession, session_id), field, None if field == "revoked_at" else now + timedelta(hours=1))
            session.commit()

        membership = session.get(OrganizationMembership, membership_id)
        membership.status = "inactive"
        session.commit()
        with pytest.raises(module.HTTPException) as inactive:
            module.execute_ed_read_tool(
                session, request, settings,
                module.EdReadToolCall(tool="list_accessible_projects"),
            )
        assert inactive.value.status_code == 404


def test_real_symbol_joins_and_shared_eligibility_reject_hidden_prompt_targets(monkeypatch):
    from datetime import datetime, timezone

    from sqlalchemy import Boolean, DateTime, Integer, create_engine
    from sqlalchemy.orm import Session
    from symgov_backend.models import (
        GovernedSymbol,
        OrganizationSymbolReviewDecision,
        OrganizationSymbolReviewSubmission,
        Project,
        ProjectSymbolSet,
        SymbolRevision,
        SymbolSet,
        SymbolSetItem,
        UserSessionProjectContext,
    )
    from symgov_backend.symbol_eligibility import current_symbol_revisions as shared_eligibility

    module = _module()
    engine = create_engine("sqlite:///:memory:")
    models = (
        Project, SymbolSet, ProjectSymbolSet, UserSessionProjectContext,
        GovernedSymbol, SymbolRevision, SymbolSetItem,
        OrganizationSymbolReviewSubmission, OrganizationSymbolReviewDecision,
    )
    with engine.begin() as connection:
        for model in models:
            declarations = []
            for column in model.__table__.columns:
                if isinstance(column.type, Boolean):
                    sql_type = "BOOLEAN"
                elif isinstance(column.type, DateTime):
                    sql_type = "DATETIME"
                elif isinstance(column.type, Integer):
                    sql_type = "INTEGER"
                else:
                    sql_type = "TEXT"
                declarations.append(f'"{column.name}" {sql_type}')
            connection.exec_driver_sql(
                f'CREATE TABLE "{model.__tablename__}" ({", ".join(declarations)})'
            )

    now = datetime.now(timezone.utc)
    foreign_org = uuid.uuid4()
    unlinked_set = uuid.uuid4()
    foreign_set = uuid.uuid4()
    approved_symbol, unapproved_symbol, unavailable_symbol, unlinked_symbol, foreign_symbol = (
        uuid.uuid4() for _ in range(5)
    )
    revision_ids = {symbol_id: uuid.uuid4() for symbol_id in (
        approved_symbol, unapproved_symbol, unavailable_symbol, unlinked_symbol, foreign_symbol,
    )}
    with Session(engine, autoflush=False, expire_on_commit=False) as session:
        session.execute(Project.__table__.insert().values(
            id=PROJECT_ID, organization_id=ORG_ID, code="P-01", normalized_code="p-01",
            name="Project", status="active", metadata_json={}, created_by_user_id=USER_ID,
            created_at=now, updated_at=now,
        ))
        session.execute(UserSessionProjectContext.__table__.insert().values(
            user_session_id=_principal().session.id, project_id=PROJECT_ID,
            selected_at=now, updated_at=now,
        ))
        for set_id, owner in ((SET_ID, ORG_ID), (unlinked_set, ORG_ID), (foreign_set, foreign_org)):
            session.execute(SymbolSet.__table__.insert().values(
                id=set_id, owner_organization_id=owner, code=str(set_id)[:8].upper(),
                normalized_code=str(set_id)[:8].lower(), name="Set", disciplines_json=[],
                use_cases_json=[], status="active", created_by_user_id=USER_ID,
                created_at=now, updated_at=now,
            ))
        for set_id in (SET_ID, foreign_set):
            session.execute(ProjectSymbolSet.__table__.insert().values(
                id=uuid.uuid4(), project_id=PROJECT_ID, symbol_set_id=set_id,
                status="active", is_default=set_id == SET_ID, created_by_user_id=USER_ID,
                created_at=now, updated_at=now,
            ))
        symbol_sets = {
            approved_symbol: (SET_ID, ORG_ID, "active"),
            unapproved_symbol: (SET_ID, ORG_ID, "active"),
            unavailable_symbol: (SET_ID, ORG_ID, "unavailable"),
            unlinked_symbol: (unlinked_set, ORG_ID, "active"),
            foreign_symbol: (foreign_set, foreign_org, "active"),
        }
        for index, (symbol_id, (set_id, owner, availability)) in enumerate(symbol_sets.items()):
            revision_id = revision_ids[symbol_id]
            session.execute(GovernedSymbol.__table__.insert().values(
                id=symbol_id, catalog_symbol_id=None, slug=f"private-{index}",
                canonical_name=f"Private {index}", category="Valve", discipline="Piping",
                owner_id=USER_ID, owner_organization_id=owner,
                visibility="organization_private", organization_wide=False,
                current_revision_id=revision_id, created_at=now, updated_at=now,
            ))
            session.execute(SymbolRevision.__table__.insert().values(
                id=revision_id, symbol_id=symbol_id, revision_label="R1",
                lifecycle_state="approved", payload_json={}, author_id=USER_ID, created_at=now,
            ))
            session.execute(SymbolSetItem.__table__.insert().values(
                id=uuid.uuid4(), symbol_set_id=set_id, governed_symbol_id=symbol_id,
                sort_order=index, provenance_json={}, availability_status=availability,
                created_at=now, updated_at=now,
            ))
        for symbol_id in (approved_symbol, unavailable_symbol, unlinked_symbol):
            submission_id = uuid.uuid4()
            session.execute(OrganizationSymbolReviewSubmission.__table__.insert().values(
                id=submission_id, organization_id=ORG_ID, governed_symbol_id=symbol_id,
                symbol_revision_id=revision_ids[symbol_id], submitted_by_user_id=USER_ID,
                submitted_at=now, status="closed", closed_at=now,
            ))
            session.execute(OrganizationSymbolReviewDecision.__table__.insert().values(
                id=uuid.uuid4(), submission_id=submission_id, organization_id=ORG_ID,
                governed_symbol_id=symbol_id, symbol_revision_id=revision_ids[symbol_id],
                decided_by_user_id=USER_ID, decision="approved", decided_at=now,
            ))
        session.commit()

        monkeypatch.setattr(
            module,
            "current_symbol_revisions",
            lambda db, ids, organization_id, **kwargs: shared_eligibility(
                db, ids, organization_id,
                organization_symbols_enabled=kwargs["organization_symbols_enabled"],
                public_resolver=lambda _db, _ids: {},
            ),
        )
        monkeypatch.setattr(module, "governed_symbol_human_readable_id", lambda *_args: "PRIVATE-1")

        visible = module.search_accessible_symbols(session, _principal(), limit=20)
        assert [row.id for row in visible] == [str(approved_symbol)]
        assert module.get_symbol(session, _principal(), approved_symbol) is not None
        for hidden in (unapproved_symbol, unavailable_symbol, unlinked_symbol, foreign_symbol):
            assert module.get_symbol(session, _principal(), hidden) is None
        assert module.search_accessible_symbols(
            session, _principal(), symbol_set_id=unlinked_set,
        ) == ()
        assert module.search_accessible_symbols(
            session, _principal(), symbol_set_id=foreign_set,
        ) == ()
