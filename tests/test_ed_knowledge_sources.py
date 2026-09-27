from __future__ import annotations

import importlib
import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from symgov_backend.ed_knowledge import KnowledgeTopic


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
INVENTORY_FIXTURE = (
    REPOSITORY_ROOT
    / "backend"
    / "symgov_backend"
    / "data"
    / "ed_knowledge"
    / "source_inventory.example.json"
)


def _sources_module():
    return importlib.import_module("symgov_backend.ed_knowledge_sources")


def _entry(module, **overrides):
    values = {
        "id": "source:guide:v1",
        "title": "Guide",
        "topic": KnowledgeTopic.APPLICATION,
        "source_path": "docs/guide.md",
        "source_version": "commit:abc123",
        "source_kind": module.SourceKind.MARKDOWN,
    }
    values.update(overrides)
    return module.SourceInventoryEntry(**values)


def test_markdown_adapter_extracts_preamble_and_atx_sections_with_exact_ranges(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "docs" / "guide.md"
    source.parent.mkdir()
    source.write_text(
        "Preamble.\n\n# First heading\nFirst body.\n## Child heading\nChild body.\n",
        encoding="utf-8",
    )

    result = module.extract_source(_entry(module), repository_root=tmp_path)

    assert result.errors == ()
    assert [
        (
            unit.source_heading,
            unit.source_line_start,
            unit.source_line_end,
            unit.content,
        )
        for unit in result.units
    ] == [
        (None, 1, 2, "Preamble.\n\n"),
        ("first-heading", 3, 4, "# First heading\nFirst body.\n"),
        ("child-heading", 5, 6, "## Child heading\nChild body.\n"),
    ]
    assert len({unit.id for unit in result.units}) == 3


def test_markdown_without_headings_is_one_repeatable_document_unit(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "docs" / "guide.md"
    source.parent.mkdir()
    source.write_text("First line.\nSecond line.\n", encoding="utf-8")
    entry = _entry(module)

    first = module.extract_source(entry, repository_root=tmp_path)
    second = module.extract_source(entry, repository_root=tmp_path)

    assert first == second
    assert first.errors == ()
    assert [
        (unit.id, unit.source_line_start, unit.source_line_end, unit.source_heading)
        for unit in first.units
    ] == [("source:guide:v1:document", 1, 2, None)]


@pytest.mark.parametrize("content", ["", "  \n\t\n"])
def test_blank_markdown_is_a_deterministic_extraction_issue(tmp_path: Path, content: str):
    module = _sources_module()
    source = tmp_path / "docs" / "guide.md"
    source.parent.mkdir()
    source.write_text(content, encoding="utf-8")

    result = module.extract_source(_entry(module), repository_root=tmp_path)

    assert result.units == ()
    assert [(issue.code, issue.reference) for issue in result.errors] == [
        ("empty_source", "source:guide:v1")
    ]


def test_python_adapter_extracts_symbols_with_exact_ranges_without_execution(tmp_path: Path):
    module = _sources_module()
    sentinel = tmp_path / "executed.txt"
    source = tmp_path / "backend" / "symgov_backend" / "sample.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "from pathlib import Path\n"
        f"Path({str(sentinel)!r}).write_text('executed')\n"
        "class Widget:\n"
        "    def method(self):\n"
        "        return 1\n"
        "async def run():\n"
        "    return 2\n",
        encoding="utf-8",
    )
    entry = _entry(
        module,
        source_path="backend/symgov_backend/sample.py",
        source_kind=module.SourceKind.PYTHON,
    )

    result = module.extract_source(entry, repository_root=tmp_path)

    assert result.errors == ()
    assert sentinel.exists() is False
    assert [
        (unit.source_symbol, unit.source_line_start, unit.source_line_end)
        for unit in result.units
    ] == [
        ("<module>", 1, 7),
        ("Widget", 3, 5),
        ("Widget.method", 4, 5),
        ("run", 6, 7),
    ]


def test_python_adapter_includes_decorators_and_definitions_inside_control_blocks(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "backend" / "symgov_backend" / "structured.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "@decorate\n"
        "class Widget:\n"
        "    @decorate\n"
        "    def method(self):\n"
        "        return 1\n"
        "if enabled:\n"
        "    async def conditional():\n"
        "        return 2\n",
        encoding="utf-8",
    )
    entry = _entry(
        module,
        source_path="backend/symgov_backend/structured.py",
        source_kind=module.SourceKind.PYTHON,
    )

    result = module.extract_source(entry, repository_root=tmp_path)

    assert result.errors == ()
    assert [
        (unit.source_symbol, unit.source_line_start, unit.source_line_end)
        for unit in result.units
    ] == [
        ("<module>", 1, 8),
        ("Widget", 1, 5),
        ("Widget.method", 3, 5),
        ("conditional", 7, 8),
    ]


def test_inventory_rejects_duplicate_ids_paths_and_kind_path_mismatches(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "docs" / "guide.py"
    source.parent.mkdir()
    source.write_text("# not markdown\n", encoding="utf-8")
    first = _entry(module, source_path="docs/guide.py")
    duplicate = first.model_copy(update={"title": "Duplicate"})
    inventory = module.SourceInventory(
        inventory_version="commit:abc123",
        sources=(first, duplicate),
    )

    report = module.validate_source_inventory(inventory, repository_root=tmp_path)

    assert report.valid is False
    assert [issue.code for issue in report.errors] == [
        "duplicate_source_id",
        "duplicate_source_path",
        "source_kind_path_mismatch",
        "source_kind_path_mismatch",
    ]


def test_inventory_reuses_shared_private_path_and_symlink_policy(tmp_path: Path):
    module = _sources_module()
    private = tmp_path / ".claude" / "settings.local.json"
    private.parent.mkdir()
    private.write_text("private", encoding="utf-8")
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "linked.md").symlink_to(private)
    linked = _entry(module, source_path="docs/linked.md")
    private_entry = _entry(
        module,
        id="source:private:v1",
        source_path=".claude/settings.local.json",
    )
    inventory = module.SourceInventory(
        inventory_version="commit:abc123",
        sources=(linked, private_entry),
    )

    report = module.validate_source_inventory(inventory, repository_root=tmp_path)

    assert [(issue.code, issue.reference) for issue in report.errors] == [
        ("disallowed_source_path", "source:guide:v1"),
        ("disallowed_source_path", "source:private:v1"),
    ]
    assert all(".claude" not in issue.message for issue in report.errors)


def test_inventory_reports_missing_files_and_unsupported_extensions_in_sorted_order(tmp_path: Path):
    module = _sources_module()
    unsupported = tmp_path / "docs" / "notes.txt"
    unsupported.parent.mkdir()
    unsupported.write_text("notes", encoding="utf-8")
    inventory = module.SourceInventory(
        inventory_version="commit:abc123",
        sources=(
            _entry(module, id="source:missing:v1", source_path="docs/missing.md"),
            _entry(module, id="source:unsupported:v1", source_path="docs/notes.txt"),
        ),
    )

    report = module.validate_source_inventory(inventory, repository_root=tmp_path)

    assert [(issue.code, issue.reference) for issue in report.errors] == [
        ("missing_source", "source:missing:v1"),
        ("unsupported_source_extension", "source:unsupported:v1"),
    ]
    assert report == module.validate_source_inventory(inventory, repository_root=tmp_path)


def test_python_syntax_error_is_a_deterministic_extraction_issue(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "backend" / "symgov_backend" / "broken.py"
    source.parent.mkdir(parents=True)
    source.write_text("def broken(:\n", encoding="utf-8")
    entry = _entry(
        module,
        source_path="backend/symgov_backend/broken.py",
        source_kind=module.SourceKind.PYTHON,
    )

    first = module.extract_source(entry, repository_root=tmp_path)
    second = module.extract_source(entry, repository_root=tmp_path)

    assert first == second
    assert first.units == ()
    assert [(issue.code, issue.reference) for issue in first.errors] == [
        ("python_syntax_error", "source:guide:v1:1")
    ]
    assert str(tmp_path) not in first.errors[0].message


def test_typescript_adapter_extracts_declarations_without_execution(tmp_path: Path):
    module = _sources_module()
    sentinel = tmp_path / "executed.txt"
    source = tmp_path / "frontend" / "src" / "sample.ts"
    source.parent.mkdir(parents=True)
    source.write_text(
        "interface User { id: string }\n"
        f"const sideEffect = (() => {{ require({str(sentinel)!r}); }})();\n"
        "export function greet(user: User): string { return user.id }\n",
        encoding="utf-8",
    )
    entry = _entry(
        module,
        id="source:typescript:v1",
        source_path="frontend/src/sample.ts",
        source_kind=module.SourceKind.TYPESCRIPT,
    )

    result = module.extract_source(entry, repository_root=tmp_path)

    assert result.errors == ()
    assert sentinel.exists() is False
    assert [unit.source_symbol for unit in result.units] == ["<module>", "User", "greet"]


def test_javascript_adapter_extracts_named_declarations(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "frontend" / "src" / "sample.js"
    source.parent.mkdir(parents=True)
    source.write_text(
        "export class Widget {}\n"
        "export const makeWidget = () => new Widget();\n"
        "export default function render() { return null; }\n",
        encoding="utf-8",
    )
    entry = _entry(
        module,
        id="source:javascript:v1",
        source_path="frontend/src/sample.js",
        source_kind=module.SourceKind.JAVASCRIPT,
    )

    result = module.extract_source(entry, repository_root=tmp_path)

    assert result.errors == ()
    assert [unit.source_symbol for unit in result.units] == ["<module>", "Widget", "makeWidget", "render"]


def test_openapi_json_adapter_extracts_path_operations(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "docs" / "openapi.json"
    source.parent.mkdir(parents=True)
    source.write_text(
        '{"openapi":"3.1.0","info":{"title":"Demo","version":"1"},'
        '"paths":{"/widgets":{"get":{"summary":"List"},"post":{"summary":"Create"}}}}',
        encoding="utf-8",
    )
    entry = _entry(
        module,
        id="source:openapi:v1",
        source_path="docs/openapi.json",
        source_kind=module.SourceKind.OPENAPI,
    )

    result = module.extract_source(entry, repository_root=tmp_path)

    assert result.errors == ()
    assert [unit.source_symbol for unit in result.units] == [
        "<document>",
        "paths.GET /widgets",
        "paths.POST /widgets",
    ]


def test_alembic_adapter_extracts_revision_metadata_and_migration_functions(tmp_path: Path):
    module = _sources_module()
    source = tmp_path / "backend" / "alembic" / "versions" / "20260927_0001_demo.py"
    source.parent.mkdir(parents=True)
    source.write_text(
        "revision = '20260927_0001'\n"
        "down_revision = '20260926_0001'\n"
        "def upgrade():\n    pass\n"
        "def downgrade():\n    pass\n",
        encoding="utf-8",
    )
    entry = _entry(
        module,
        id="source:alembic:v1",
        source_path="backend/alembic/versions/20260927_0001_demo.py",
        source_kind=module.SourceKind.ALEMBIC,
    )

    result = module.extract_source(entry, repository_root=tmp_path)

    assert result.errors == ()
    assert [unit.source_symbol for unit in result.units] == [
        "<module>",
        "revision",
        "down_revision",
        "upgrade",
        "downgrade",
    ]


def test_representative_inventory_loads_with_truthful_markdown_and_python_versions():
    module = _sources_module()

    report = module.load_source_inventory(INVENTORY_FIXTURE, repository_root=REPOSITORY_ROOT)

    assert report.valid is True
    assert report.errors == ()
    assert report.inventory is not None
    assert {entry.source_kind for entry in report.inventory.sources} == {
        module.SourceKind.MARKDOWN,
        module.SourceKind.PYTHON,
    }
    for entry in report.inventory.sources:
        digest = hashlib.sha256((REPOSITORY_ROOT / entry.source_path).read_bytes()).hexdigest()
        assert entry.source_version == f"sha256:{digest}"


def test_inventory_models_are_strict_frozen_and_reject_blank_values():
    module = _sources_module()
    with pytest.raises(ValidationError):
        module.SourceInventoryEntry(
            id="source:strict:v1",
            title=" ",
            topic="application",
            source_path="docs/README.md",
            source_version="commit:abc123",
            source_kind="markdown",
            unexpected=True,
        )
    entry = _entry(module)
    with pytest.raises(ValidationError):
        entry.title = "Changed"
    unit_values = entry.model_dump()
    unit_values.update(source_line_start=3, source_line_end=2, content="content")
    with pytest.raises(ValidationError):
        module.SourceUnit(**unit_values)
    unit_values.update(source_line_start=1, source_line_end=1, content=" ")
    with pytest.raises(ValidationError):
        module.SourceUnit(**unit_values)


def test_inventory_loader_reports_malformed_json_without_crashing(tmp_path: Path):
    module = _sources_module()
    inventory_path = tmp_path / "inventory.json"
    inventory_path.write_text('{"sources": [', encoding="utf-8")

    report = module.load_source_inventory(inventory_path, repository_root=tmp_path)

    assert report.valid is False
    assert report.inventory is None
    assert [(issue.code, issue.reference) for issue in report.errors] == [
        ("malformed_inventory_json", "inventory")
    ]
    assert str(tmp_path) not in report.errors[0].message
