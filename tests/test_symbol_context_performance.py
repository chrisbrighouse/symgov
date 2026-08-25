from __future__ import annotations

import uuid

from sqlalchemy import event, text

from test_project_symbol_set_postgresql import wp1_database
from test_projects_api import _stage4_client
from test_symbol_set_availability import _active_set, _project


def _index_names(plan):
    names = set()
    if isinstance(plan, dict):
        if "Index Name" in plan:
            names.add(plan["Index Name"])
        for value in plan.values():
            names.update(_index_names(value))
    elif isinstance(plan, list):
        for value in plan:
            names.update(_index_names(value))
    return names


def test_context_get_query_count_is_constant_and_bounded():
    client, Session = _stage4_client()
    project_id = _project(client)
    set_id = _active_set(client)
    assert client.put(
        f"/api/v1/org/me/symbol-sets/{set_id}/projects",
        json={"projects": [{"projectId": project_id, "isDefault": True}]},
    ).status_code == 200
    assert client.put("/api/v1/org/me/symbol-context/project", json={"projectId": project_id}).status_code == 200

    statements = []
    engine = Session.kw["bind"]

    def record(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        response = client.get("/api/v1/org/me/symbol-context")
    finally:
        event.remove(engine, "before_cursor_execute", record)

    assert response.status_code == 200
    assert response.json()["reason"] == "project_default"
    assert len(statements) <= 16
    assert not any("symbol_set_items" in statement.lower() for statement in statements)


def test_context_lookups_use_frozen_postgresql_indexes(wp1_database):
    probes = (
        (
            "SELECT * FROM user_session_project_contexts WHERE user_session_id=:session",
            {"session": uuid.uuid4()},
            {"pk_user_session_project_contexts"},
        ),
        (
            "SELECT * FROM user_project_set_selections WHERE user_id=:user AND project_id=:project",
            {"user": uuid.uuid4(), "project": uuid.uuid4()},
            {"pk_user_project_set_selections", "ix_user_project_set_selections_project_user"},
        ),
        (
            "SELECT * FROM project_symbol_sets WHERE project_id=:project AND status='active' AND symbol_set_id=:symbol_set",
            {"project": uuid.uuid4(), "symbol_set": uuid.uuid4()},
            {
                "uq_project_symbol_sets_project_set",
                "ix_project_symbol_sets_project_status_set",
                "ix_project_symbol_sets_set_status_project",
            },
        ),
    )
    with wp1_database.begin() as connection:
        connection.execute(text("SET LOCAL enable_seqscan=off"))
        for sql, params, accepted in probes:
            explained = connection.execute(text(f"EXPLAIN (FORMAT JSON) {sql}"), params).scalar_one()
            assert _index_names(explained) & accepted
