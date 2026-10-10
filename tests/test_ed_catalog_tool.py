"""Ed's Catalog search tool: scope, visibility and what the model is given."""

from __future__ import annotations

import importlib
import uuid
from types import SimpleNamespace

import pytest
from pydantic import ValidationError


ORG_ID = uuid.UUID("11111111-1111-1111-1111-111111111111")
OTHER_ORG_ID = uuid.UUID("99999999-9999-9999-9999-999999999999")
USER_ID = uuid.UUID("22222222-2222-2222-2222-222222222222")


def _module():
    return importlib.import_module("symgov_backend.ed_catalog_tool")


class _Query:
    def __init__(self, rows):
        self.rows = rows

    def join(self, *args, **kwargs):
        return self

    def filter(self, *args, **kwargs):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def query(self, *args):
        return _Query(self.rows)


def _symbol(*, visibility="public", owner=None, wide=False, name="Ball valve", catalog_id="S-000001"):
    return SimpleNamespace(
        id=uuid.uuid4(),
        visibility=visibility,
        owner_organization_id=owner,
        organization_wide=wide,
        catalog_symbol_id=catalog_id,
        slug=name.lower().replace(" ", "-"),
        canonical_name=name,
        category="Valves",
        discipline="Piping",
    )


def _row(symbol, **payload):
    revision = SimpleNamespace(id=uuid.uuid4(), payload_json=payload or {"summary": "A ball valve."})
    return symbol, revision


def _entry(row, source="public"):
    return {"source": source, "symbol_revision_id": row[1].id}


def _settings(**overrides):
    values = {"organizations_enabled": True, "organization_symbols_enabled": True}
    values.update(overrides)
    return SimpleNamespace(**values)


def _page(entries, total=None, facets=None):
    return SimpleNamespace(entries=entries, total=len(entries) if total is None else total, facets=facets or {})


@pytest.fixture
def stub(monkeypatch):
    module = _module()
    seen = {}

    def install(*, entries=(), rows=(), organization_id=ORG_ID, page=None, error=None):
        principal = SimpleNamespace(organization_id=organization_id) if organization_id else None
        monkeypatch.setattr(
            module, "_resolve_ed_read_authority",
            lambda *args: SimpleNamespace(user=SimpleNamespace(id=USER_ID), principal=principal),
        )

        def fake_search(session, scope, request):
            seen["scope"], seen["request"] = scope, request
            if error:
                raise error
            return page if page is not None else _page(list(entries))

        monkeypatch.setattr(module, "search_catalog", fake_search)
        monkeypatch.setattr(module, "governed_symbol_human_readable_id", lambda session, symbol: "ACME-0001")
        return _Session(rows)

    install.seen = seen
    return install


def _call(**arguments):
    module = _module()
    return module.EdCatalogSearchCall(tool="search_catalog", **arguments)


def _run(session, *, call=None, settings=None):
    return _module().search_catalog_for_ed(session, object(), settings or _settings(), call or _call(query="valve"))


def test_keywords_drop_question_words_but_keep_the_subject():
    keywords = _module().catalog_keywords

    assert keywords("What pump symbols are there?") == "pump"
    assert keywords("Do you have a ball valve?") == "ball valve"
    assert keywords("Show me the DEXPI heat exchanger symbols, please") == "dexpi heat exchanger"
    assert keywords("S-000123") == "s-000123"
    assert keywords("what are the") == ""
    assert keywords(None) == ""


@pytest.mark.parametrize(
    "arguments",
    [
        {"organization_id": str(ORG_ID)},
        {"user_id": str(USER_ID)},
        {"project_id": str(uuid.uuid4())},
        {"limit": 0},
        {"limit": 26},
        {"query": "x" * 201},
    ],
)
def test_the_call_cannot_name_a_scope_or_exceed_its_bounds(arguments):
    with pytest.raises(ValidationError):
        _call(**arguments)


def test_the_tool_name_is_fixed():
    with pytest.raises(ValidationError):
        _module().EdCatalogSearchCall(tool="search_accessible_symbols")


def test_a_public_match_comes_back_with_a_summary_first_and_two_kinds_of_citation(stub):
    row = _row(_symbol())
    session = stub(entries=[_entry(row)], rows=[row])

    result = _run(session)

    summary, found = result
    assert summary.total_matches == 1 and summary.shown == 1
    assert summary.citation["record_type"] == "catalog_search"
    dumped = found.model_dump(mode="json")
    assert dumped["citation"]["record_type"] == "symbol"
    assert dumped["display_id"] == "S-000001"
    assert dumped["source"] == "public"
    assert dumped["summary"] == "A ball valve."
    # The model never receives the internal UUID, only the opaque reference.
    assert str(row[0].id) not in str(dumped)
    assert "id" not in dumped and "key" not in summary.model_dump(mode="json")


def test_a_symbol_has_the_same_reference_here_as_in_the_project_scoped_tool(stub):
    from symgov_backend.ed_read_tools import EdSymbolRead

    row = _row(_symbol())
    found = _run(stub(entries=[_entry(row)], rows=[row]))[1]
    other = EdSymbolRead(
        id=str(row[0].id), display_id="S-000001", name="Ball valve", slug="ball-valve",
        category="Valves", discipline="Piping", source="public",
    )

    assert found.citation["record_ref"] == other.citation["record_ref"]


def test_the_query_is_cleaned_and_filters_map_to_catalog_facets(stub):
    session = stub()

    _run(session, call=_call(query="What pump symbols are there?", discipline="Piping", use_case="P&ID", format="svg"))

    request = stub.seen["request"]
    assert request.query == "pump"
    assert request.facets == {
        "catalogDisciplines": ["Piping"],
        "useCases": ["P&ID"],
        "availableFormats": ["svg"],
    }
    assert request.page_size == 10
    assert stub.seen["scope"].user_id == USER_ID


@pytest.mark.parametrize(
    ("organization_id", "settings", "expected"),
    [
        (ORG_ID, _settings(), ORG_ID),
        (None, _settings(), None),
        (ORG_ID, _settings(organizations_enabled=False), None),
        (ORG_ID, _settings(organization_symbols_enabled=False), None),
    ],
)
def test_organization_scope_comes_only_from_the_session_and_the_flags(stub, organization_id, settings, expected):
    _run(stub(organization_id=organization_id), settings=settings)

    assert stub.seen["scope"].organization_id == expected


def test_this_organizations_wide_private_symbol_is_shown_with_its_own_display_id(stub):
    row = _row(_symbol(visibility="organization_private", owner=ORG_ID, wide=True, name="Acme skid"))

    found = _run(stub(entries=[_entry(row, "organization_private")], rows=[row]))[1]

    assert found.source == "organization_private"
    assert found.display_id == "ACME-0001"


@pytest.mark.parametrize(
    "symbol",
    [
        _symbol(visibility="organization_private", owner=OTHER_ORG_ID, wide=True),
        _symbol(visibility="organization_private", owner=ORG_ID, wide=False),
        _symbol(visibility="public", owner=None),
    ],
    ids=["another-tenant", "not-organization-wide", "public-row-claimed-private"],
)
def test_a_private_symbol_outside_the_callers_scope_is_dropped_even_if_the_search_returned_it(stub, symbol):
    row = _row(symbol)

    result = _run(stub(entries=[_entry(row, "organization_private")], rows=[row]))

    assert [type(item).__name__ for item in result] == ["EdCatalogSearchSummary"]
    assert result[0].shown == 0


def test_a_private_symbol_is_dropped_in_a_session_with_no_organization(stub):
    row = _row(_symbol(visibility="organization_private", owner=ORG_ID, wide=True))

    result = _run(stub(entries=[_entry(row, "organization_private")], rows=[row], organization_id=None))

    assert result[0].shown == 0 and len(result) == 1


def test_a_public_entry_whose_symbol_is_not_public_is_dropped(stub):
    row = _row(_symbol(visibility="organization_private", owner=OTHER_ORG_ID, wide=True))

    result = _run(stub(entries=[_entry(row, "public")], rows=[row]))

    assert len(result) == 1


def test_no_match_still_returns_a_citable_summary(stub):
    result = _run(stub(page=_page([], total=0)))

    assert len(result) == 1
    assert result[0].total_matches == 0
    assert result[0].citation["record_ref"].startswith("live:catalog_search:")


def test_a_rejected_search_is_an_empty_summary_not_an_error(stub):
    from symgov_backend.catalog_browse_search import CatalogSearchInputError

    result = _run(stub(error=CatalogSearchInputError("Unknown filter: x.")))

    assert len(result) == 1 and result[0].total_matches == 0


def test_facet_counts_are_bounded_and_ordered_by_count(stub):
    facets = {
        "catalogDisciplines": [{"value": f"D{index:02d}", "count": index + 1} for index in range(20)],
        "catalogCategories": [{"value": "Empty", "count": 0}, {"value": "Valves", "count": 4}],
    }

    summary = _run(stub(page=_page([], total=0, facets=facets)))[0]

    disciplines = summary.facets["disciplines"]
    assert len(disciplines) == 12
    assert disciplines[0].value == "D19" and disciplines[0].count == 20
    assert [item.value for item in summary.facets["categories"]] == ["Valves"]


def test_summary_text_and_keywords_are_bounded(stub):
    row = _row(_symbol(), summary="word " * 200, keywords=[f"k{index}" for index in range(20)] + [3, None])

    found = _run(stub(entries=[_entry(row)], rows=[row]))[1]

    assert len(found.summary) <= 200
    assert len(found.keywords) == 6
