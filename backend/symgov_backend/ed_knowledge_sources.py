from __future__ import annotations

import ast
import hashlib
import json
import re
from enum import StrEnum
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from symgov_backend.ed_knowledge import KnowledgeTopic, ValidationIssue, source_path_issues


_ID_PATTERN = r"^[a-z][a-z0-9._:-]{2,127}$"
_ID = re.compile(_ID_PATTERN)
_MAX_TEXT = 240
_ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?)[ \t]*#*[ \t]*|[ \t]*)$")
_JS_DECLARATION = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
    r"(?P<kind>class|function|interface|type|enum)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
)
_JS_EXPORTED_VARIABLE = re.compile(
    r"^export\s+(?:default\s+)?(?:const|let|var)\s+"
    r"(?P<name>[A-Za-z_$][A-Za-z0-9_$]*)"
)
_OPENAPI_METHODS = frozenset({"delete", "get", "head", "options", "patch", "post", "put", "trace"})
_ALEMBIC_METADATA_NAMES = frozenset({"revision", "down_revision", "branch_labels", "depends_on"})


class SourceKind(StrEnum):
    MARKDOWN = "markdown"
    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    OPENAPI = "openapi"
    ALEMBIC = "alembic"


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceInventoryEntry(_StrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    topic: KnowledgeTopic
    source_path: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=200)
    source_kind: SourceKind
    source_line_start: int | None = Field(default=None, ge=1)
    source_line_end: int | None = Field(default=None, ge=1)
    source_symbol: str | None = Field(default=None, min_length=1, max_length=240)
    source_heading: str | None = Field(default=None, min_length=1, max_length=240)

    @field_validator("title", "source_path", "source_version", "source_symbol", "source_heading")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> SourceInventoryEntry:
        if self.source_line_end is not None and self.source_line_start is None:
            raise ValueError("source_line_start is required when source_line_end is set")
        if (
            self.source_line_start is not None
            and self.source_line_end is not None
            and self.source_line_end < self.source_line_start
        ):
            raise ValueError("source_line_end must not precede source_line_start")
        return self


class SourceInventory(_StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    inventory_version: str = Field(min_length=1, max_length=200)
    sources: tuple[SourceInventoryEntry, ...] = Field(min_length=1)

    @field_validator("inventory_version")
    @classmethod
    def reject_blank_version(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("inventory_version must not be blank")
        return value


class SourceUnit(_StrictModel):
    id: str = Field(pattern=_ID_PATTERN)
    title: str = Field(min_length=1, max_length=240)
    topic: KnowledgeTopic
    source_path: str = Field(min_length=1, max_length=500)
    source_version: str = Field(min_length=1, max_length=200)
    source_kind: SourceKind
    source_line_start: int = Field(ge=1)
    source_line_end: int = Field(ge=1)
    source_symbol: str | None = Field(default=None, min_length=1, max_length=240)
    source_heading: str | None = Field(default=None, min_length=1, max_length=240)
    content: str = Field(min_length=1)

    @field_validator("title", "source_path", "source_version", "source_symbol", "source_heading")
    @classmethod
    def reject_blank_text(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("value must not be blank")
        return value

    @field_validator("content")
    @classmethod
    def reject_blank_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be blank")
        return value

    @model_validator(mode="after")
    def validate_range(self) -> SourceUnit:
        if self.source_line_end < self.source_line_start:
            raise ValueError("source_line_end must not precede source_line_start")
        return self


class ExtractionResult(_StrictModel):
    units: tuple[SourceUnit, ...] = ()
    errors: tuple[ValidationIssue, ...] = ()


class InventoryValidationReport(_StrictModel):
    valid: bool
    errors: tuple[ValidationIssue, ...]
    inventory: SourceInventory | None = None


def _issue(code: str, reference: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, reference=reference, message=message)


def _path_kind(source_path: str) -> SourceKind | None:
    lowered = source_path.casefold()
    suffix = Path(lowered).suffix
    if lowered.startswith("backend/alembic/") and suffix == ".py":
        return SourceKind.ALEMBIC
    if suffix in {".md", ".markdown"}:
        return SourceKind.MARKDOWN
    if suffix == ".py":
        return SourceKind.PYTHON
    if suffix in {".ts", ".tsx"}:
        return SourceKind.TYPESCRIPT
    if suffix in {".js", ".jsx", ".mjs", ".cjs"}:
        return SourceKind.JAVASCRIPT
    if suffix in {".json", ".yaml", ".yml"}:
        return SourceKind.OPENAPI
    return None


def source_locator(entry: SourceInventoryEntry) -> tuple[object, ...]:
    """What an entry reviews: a file plus its line range and symbol or heading."""
    return (
        entry.source_path,
        entry.source_line_start,
        entry.source_line_end,
        entry.source_symbol or entry.source_heading,
    )


def validate_source_inventory(
    value: SourceInventory | dict[str, object], *, repository_root: str | Path | None = None
) -> InventoryValidationReport:
    issues: list[ValidationIssue] = []
    inventory: SourceInventory | None
    try:
        inventory = value if isinstance(value, SourceInventory) else SourceInventory.model_validate(value)
    except ValidationError as exc:
        inventory = None
        for error in exc.errors(include_url=False, include_input=False):
            reference = ".".join(str(part) for part in error.get("loc", ())) or "inventory"
            issues.append(_issue("malformed_inventory", reference, str(error.get("msg", "Invalid value"))))
    if inventory is not None:
        ids: dict[str, int] = {}
        locators: dict[tuple[object, ...], int] = {}
        path_identity: dict[str, set[tuple[str, SourceKind]]] = {}
        for entry in inventory.sources:
            ids[entry.id] = ids.get(entry.id, 0) + 1
            locator = source_locator(entry)
            locators[locator] = locators.get(locator, 0) + 1
            path_identity.setdefault(entry.source_path, set()).add(
                (entry.source_version, entry.source_kind)
            )
        issues.extend(
            _issue("duplicate_source_id", identifier, f"Duplicate source ID: {identifier}")
            for identifier, count in ids.items()
            if count > 1
        )
        # One file may back several reviewed ranges (and so several topics),
        # but the same range twice is a duplicate, and every entry for a file
        # must pin the same bytes and kind.
        issues.extend(
            _issue("duplicate_source_locator", str(locator[0]), f"Duplicate source locator: {locator[0]}")
            for locator, count in locators.items()
            if count > 1
        )
        issues.extend(
            _issue(
                "inconsistent_source_path",
                path,
                f"Entries for {path} must share one source version and kind",
            )
            for path, identities in path_identity.items()
            if len(identities) > 1
        )
        for entry in inventory.sources:
            path_errors = source_path_issues(entry.source_path, entry.id, repository_root)
            if path_errors:
                issues.extend(path_errors)
                continue
            expected_kind = _path_kind(entry.source_path)
            if expected_kind is None:
                issues.append(
                    _issue(
                        "unsupported_source_extension",
                        entry.id,
                        f"Unsupported source extension: {Path(entry.source_path).suffix or '<none>'}",
                    )
                )
            elif expected_kind is not entry.source_kind:
                issues.append(
                    _issue(
                        "source_kind_path_mismatch",
                        entry.id,
                        f"{entry.source_path} requires source kind {expected_kind.value}",
                    )
                )

    ordered = tuple(sorted(issues, key=lambda item: (item.code, item.reference, item.message)))
    return InventoryValidationReport(valid=not ordered, errors=ordered, inventory=inventory)


def load_source_inventory(
    path: str | Path, *, repository_root: str | Path | None = None
) -> InventoryValidationReport:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        issue = _issue(
            "malformed_inventory_json",
            "inventory",
            f"Malformed inventory JSON at line {exc.lineno}, column {exc.colno}",
        )
        return InventoryValidationReport(valid=False, errors=(issue,))
    return validate_source_inventory(payload, repository_root=repository_root)


def _unit(
    entry: SourceInventoryEntry,
    *,
    identity: str,
    title: str,
    start: int,
    end: int,
    content: str,
    symbol: str | None = None,
    heading: str | None = None,
) -> SourceUnit:
    unit_id = f"{entry.id}:{identity}"
    if not _ID.fullmatch(unit_id):
        # A long entry ID plus a long heading slug, or a symbol with capitals,
        # cannot form a valid ID. Keep it unique and stable with a digest of
        # the full identity rather than failing the whole extraction.
        digest = hashlib.sha256(unit_id.encode("utf-8")).hexdigest()[:16]
        unit_id = f"{entry.id[: 128 - len(digest) - 1]}:{digest}"
    return SourceUnit(
        id=unit_id,
        title=title[:_MAX_TEXT],
        topic=entry.topic,
        source_path=entry.source_path,
        source_version=entry.source_version,
        source_kind=entry.source_kind,
        source_line_start=start,
        source_line_end=end,
        source_symbol=symbol,
        source_heading=heading[:_MAX_TEXT] if heading else heading,
        content=content,
    )


def _heading_slug(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.casefold()).strip("-")
    return slug or "heading"


def _extract_markdown(entry: SourceInventoryEntry, text: str) -> tuple[SourceUnit, ...]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return ()
    headings: list[tuple[int, str, str]] = []
    seen: dict[str, int] = {}
    fence: str | None = None
    for line_number, line in enumerate(lines, start=1):
        stripped = line.lstrip(" ")
        fence_match = re.match(r"(`{3,}|~{3,})", stripped)
        if fence_match:
            marker = fence_match.group(1)[0]
            fence = None if fence == marker else marker if fence is None else fence
            continue
        if fence is not None:
            continue
        match = _ATX_HEADING.match(line.rstrip("\r\n"))
        if match is None:
            continue
        title = (match.group(2) or "").strip()
        base_slug = _heading_slug(title)
        seen[base_slug] = seen.get(base_slug, 0) + 1
        slug = base_slug if seen[base_slug] == 1 else f"{base_slug}-{seen[base_slug]}"
        headings.append((line_number, title or "Heading", slug))

    boundaries = [(line_number, title, slug) for line_number, title, slug in headings]
    units: list[SourceUnit] = []
    if not boundaries:
        return (
            _unit(
                entry,
                identity="document",
                title=entry.title,
                start=1,
                end=len(lines),
                content="".join(lines),
            ),
        )
    first_heading = boundaries[0][0]
    if first_heading > 1:
        units.append(
            _unit(
                entry,
                identity="preamble",
                title=f"{entry.title} preamble",
                start=1,
                end=first_heading - 1,
                content="".join(lines[: first_heading - 1]),
            )
        )
    for index, (start, title, slug) in enumerate(boundaries):
        end = boundaries[index + 1][0] - 1 if index + 1 < len(boundaries) else len(lines)
        units.append(
            _unit(
                entry,
                identity=f"heading:{slug}",
                title=title,
                start=start,
                end=end,
                content="".join(lines[start - 1 : end]),
                heading=slug,
            )
        )
    return tuple(units)


def _extract_javascript(entry: SourceInventoryEntry, text: str) -> tuple[SourceUnit, ...]:
    """Extract stable declaration slices without evaluating JavaScript/TypeScript."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return ()
    units = [
        _unit(
            entry,
            identity="module",
            title=entry.title,
            start=1,
            end=len(lines),
            content=text,
            symbol="<module>",
        )
    ]
    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if line.startswith(("//", "/*", "*", "#")):
            continue
        match = _JS_DECLARATION.match(line) or _JS_EXPORTED_VARIABLE.match(line)
        if match is None:
            continue
        name = match.group("name")
        identity = re.sub(r"[^a-z0-9._-]+", "-", name.casefold()).strip("-") or "declaration"
        units.append(
            _unit(
                entry,
                identity=f"symbol:{identity}:{line_number}",
                title=name,
                start=line_number,
                end=line_number,
                content=raw_line,
                symbol=name,
            )
        )
    return tuple(units)


def _extract_openapi(entry: SourceInventoryEntry, text: str) -> ExtractionResult:
    if Path(entry.source_path).suffix.casefold() != ".json":
        return ExtractionResult(
            errors=(_issue(
                "openapi_format_not_supported",
                entry.id,
                "OpenAPI extraction currently supports JSON documents only",
            ),)
        )
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        return ExtractionResult(errors=(_issue(
            "openapi_json_error",
            f"{entry.id}:{exc.lineno}",
            f"OpenAPI JSON error on line {exc.lineno}: {exc.msg}",
        ),))
    if not isinstance(document, dict):
        return ExtractionResult(errors=(_issue(
            "openapi_document_shape", entry.id, "OpenAPI document must be a JSON object"
        ),))
    lines = text.splitlines(keepends=True)
    units = [
        _unit(
            entry,
            identity="document",
            title=entry.title,
            start=1,
            end=max(1, len(lines)),
            content=text,
            symbol="<document>",
        )
    ]
    paths = document.get("paths", {})
    if paths is None:
        paths = {}
    if not isinstance(paths, dict):
        return ExtractionResult(errors=(_issue(
            "openapi_paths_shape", entry.id, "OpenAPI paths must be a JSON object"
        ),))
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, dict):
            continue
        methods = _OPENAPI_METHODS.intersection(path_item)
        for method in sorted(methods, key=str.casefold):
            operation = path_item[method]
            safe_path = re.sub(r"[^a-z0-9._-]+", "-", path.casefold()).strip("-") or "root"
            units.append(
                _unit(
                    entry,
                    identity=f"operation:{method}:{safe_path}",
                    title=f"{method.upper()} {path}",
                    start=1,
                    end=max(1, len(lines)),
                    content=json.dumps(operation, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    symbol=f"paths.{method.upper()} {path}",
                )
            )
    return ExtractionResult(units=tuple(units))


PythonDefinition = ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef


class _DefinitionVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.scope: tuple[str, ...] = ()
        self.definitions: list[tuple[PythonDefinition, str]] = []

    def _visit_definition(self, node: PythonDefinition) -> None:
        qualified_name = ".".join((*self.scope, node.name))
        self.definitions.append((node, qualified_name))
        previous_scope = self.scope
        self.scope = (*self.scope, node.name)
        self.generic_visit(node)
        self.scope = previous_scope

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        self._visit_definition(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        self._visit_definition(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        self._visit_definition(node)


def _python_definitions(tree: ast.Module) -> tuple[tuple[PythonDefinition, str], ...]:
    visitor = _DefinitionVisitor()
    visitor.visit(tree)
    return tuple(visitor.definitions)


def _extract_python(
    entry: SourceInventoryEntry, text: str, tree: ast.Module
) -> tuple[SourceUnit, ...]:
    lines = text.splitlines(keepends=True)
    if not lines:
        return ()
    units = [
        _unit(
            entry,
            identity="module",
            title=entry.title,
            start=1,
            end=len(lines),
            content=text,
            symbol="<module>",
        )
    ]
    for node, qualified_name in _python_definitions(tree):
        start = min(
            (decorator.lineno for decorator in node.decorator_list),
            default=node.lineno,
        )
        end = node.end_lineno or node.lineno
        identity = re.sub(r"[^a-z0-9._-]+", "-", qualified_name.casefold()).strip("-")
        units.append(
            _unit(
                entry,
                identity=f"symbol:{identity}",
                title=qualified_name,
                start=start,
                end=end,
                content="".join(lines[start - 1 : end]),
                symbol=qualified_name,
            )
        )
    return tuple(units)


SourceAdapter = Callable[[SourceInventoryEntry, str], ExtractionResult]


def _markdown_adapter(entry: SourceInventoryEntry, text: str) -> ExtractionResult:
    if not text.strip():
        return ExtractionResult(
            errors=(_issue("empty_source", entry.id, "Approved source file is empty"),)
        )
    return ExtractionResult(units=_extract_markdown(entry, text))


def _python_adapter(entry: SourceInventoryEntry, text: str) -> ExtractionResult:
    if not text.strip():
        return ExtractionResult(
            errors=(_issue("empty_source", entry.id, "Approved source file is empty"),)
        )
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        line_number = exc.lineno or 1
        return ExtractionResult(
            errors=(
                _issue(
                    "python_syntax_error",
                    f"{entry.id}:{line_number}",
                    f"Python syntax error on line {line_number}: {exc.msg}",
                ),
            )
        )
    return ExtractionResult(units=_extract_python(entry, text, tree))


def _javascript_adapter(entry: SourceInventoryEntry, text: str) -> ExtractionResult:
    if not text.strip():
        return ExtractionResult(errors=(_issue("empty_source", entry.id, "Approved source file is empty"),))
    return ExtractionResult(units=_extract_javascript(entry, text))


def _openapi_adapter(entry: SourceInventoryEntry, text: str) -> ExtractionResult:
    if not text.strip():
        return ExtractionResult(errors=(_issue("empty_source", entry.id, "Approved source file is empty"),))
    return _extract_openapi(entry, text)


def _extract_alembic(entry: SourceInventoryEntry, text: str, tree: ast.Module) -> tuple[SourceUnit, ...]:
    """Extract migration metadata and Python definitions without importing a migration."""
    lines = text.splitlines(keepends=True)
    units = [
        _unit(
            entry,
            identity="module",
            title=entry.title,
            start=1,
            end=len(lines),
            content=text,
            symbol="<module>",
        )
    ]
    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else (node.target,)
        names = {
            target.id
            for target in targets
            if isinstance(target, ast.Name) and target.id in _ALEMBIC_METADATA_NAMES
        }
        for name in sorted(names):
            start = node.lineno
            end = node.end_lineno or start
            units.append(
                _unit(
                    entry,
                    identity=f"metadata:{name}",
                    title=name,
                    start=start,
                    end=end,
                    content="".join(lines[start - 1 : end]),
                    symbol=name,
                )
            )
    for node, qualified_name in _python_definitions(tree):
        start = min((decorator.lineno for decorator in node.decorator_list), default=node.lineno)
        end = node.end_lineno or node.lineno
        units.append(
            _unit(
                entry,
                identity=f"symbol:{qualified_name.casefold()}",
                title=qualified_name,
                start=start,
                end=end,
                content="".join(lines[start - 1 : end]),
                symbol=qualified_name,
            )
        )
    return tuple(units)


def _alembic_adapter(entry: SourceInventoryEntry, text: str) -> ExtractionResult:
    if not text.strip():
        return ExtractionResult(errors=(_issue("empty_source", entry.id, "Approved source file is empty"),))
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        line_number = exc.lineno or 1
        return ExtractionResult(errors=(_issue(
            "python_syntax_error",
            f"{entry.id}:{line_number}",
            f"Python syntax error on line {line_number}: {exc.msg}",
        ),))
    return ExtractionResult(units=_extract_alembic(entry, text, tree))


SOURCE_ADAPTERS: Mapping[SourceKind, SourceAdapter | None] = MappingProxyType(
    {
        SourceKind.MARKDOWN: _markdown_adapter,
        SourceKind.PYTHON: _python_adapter,
        SourceKind.TYPESCRIPT: _javascript_adapter,
        SourceKind.JAVASCRIPT: _javascript_adapter,
        SourceKind.OPENAPI: _openapi_adapter,
        SourceKind.ALEMBIC: _alembic_adapter,
    }
)


def extract_source(
    entry: SourceInventoryEntry, *, repository_root: str | Path
) -> ExtractionResult:
    errors = source_path_issues(entry.source_path, entry.id, repository_root)
    if errors:
        return ExtractionResult(errors=errors)
    source = Path(repository_root) / entry.source_path
    text = source.read_text(encoding="utf-8")
    adapter = SOURCE_ADAPTERS[entry.source_kind]
    if adapter is not None:
        return adapter(entry, text)
    return ExtractionResult(
        errors=(
            ValidationIssue(
                code="adapter_not_supported",
                reference=entry.id,
                message=f"{entry.source_kind.value} source adapter is not yet supported",
            ),
        )
    )