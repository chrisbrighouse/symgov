import re
import uuid
from contextlib import AbstractContextManager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql.dml import Insert

from symgov_backend import catalog_symbol_ids as catalog_symbol_ids_service
from symgov_backend.catalog_symbol_ids import (
    CATALOG_SYMBOL_ID_ALLOCATION_ATTEMPTS,
    CATALOG_SYMBOL_ID_PATTERN,
    POSTGRESQL_BIGINT_MAX,
    ensure_catalog_symbol_id,
    format_allocated_catalog_symbol_id,
    normalize_catalog_symbol_id,
)
from symgov_backend.models import CatalogSymbolIdentifier, GovernedSymbol


class _ScalarResult:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one(self) -> object:
        return self.value


class _UniqueViolation(Exception):
    def __init__(self, constraint_name: str) -> None:
        super().__init__(constraint_name)
        self.sqlstate = "23505"
        self.diag = SimpleNamespace(constraint_name=constraint_name)


class _NestedTransaction(AbstractContextManager[None]):
    def __init__(self, session: "AllocationSession") -> None:
        self.session = session
        self.pending_snapshot: list[object] = []
        self.symbol_snapshot: dict[str, object] = {}
        self.registry_snapshots: list[tuple[object, dict[str, object]]] = []
        self.database_snapshot: dict[str, dict[str, object]] = {}

    def __enter__(self) -> None:
        self.pending_snapshot = list(self.session.pending)
        self.symbol_snapshot = vars(self.session.symbol).copy()
        registry_rows = getattr(self.session, "registry_rows", {}).values()
        self.registry_snapshots = [(row, vars(row).copy()) for row in registry_rows]

    def __exit__(self, exc_type, exc_value, traceback) -> bool:
        if exc_type is not None:
            self.session.pending[:] = self.pending_snapshot
            vars(self.session.symbol).update(self.symbol_snapshot)
            for row, snapshot in self.registry_snapshots:
                vars(row).update(snapshot)
            if self.database_snapshot:
                self.session.database_rows = {
                    identifier: state.copy()
                    for identifier, state in self.database_snapshot.items()
                }
        return False


class AllocationSession:
    """Small transactional session fake for PostgreSQL-only allocation SQL."""

    def __init__(
        self,
        symbol: object,
        *,
        sequence_values: tuple[int, ...] = (),
        colliding_identifiers: tuple[str, ...] = (),
        collision_constraint_name: str = "pk_catalog_symbol_identifiers",
        pre_savepoint_flush_error: IntegrityError | None = None,
        insert_errors: dict[str, IntegrityError] | None = None,
    ) -> None:
        self.symbol = symbol
        self.sequence_values = list(sequence_values)
        self.colliding_identifiers = set(colliding_identifiers)
        self.collision_constraint_name = collision_constraint_name
        self.pre_savepoint_flush_error = pre_savepoint_flush_error
        self.insert_errors = dict(insert_errors or {})
        self.pending: list[object] = []
        self.durable: list[object] = []
        self.lock_requests: list[tuple[object, object, bool]] = []
        self.sequence_calls = 0
        self.commit_calls = 0
        self._original_catalog_symbol_id = symbol.catalog_symbol_id

    def get(self, model, key, *, with_for_update=False):
        self.lock_requests.append((model, key, with_for_update))
        return self.symbol if model is GovernedSymbol and key == self.symbol.id else None

    def execute(self, statement):
        if "nextval('catalog_symbol_id_seq')" in str(statement):
            self.sequence_calls += 1
            return _ScalarResult(self.sequence_values.pop(0))

        assert isinstance(statement, Insert)
        assert statement.table.name == CatalogSymbolIdentifier.__tablename__
        values = statement.compile().params
        identifier = values["identifier"]
        if identifier in self.insert_errors:
            raise self.insert_errors.pop(identifier)
        if identifier in self.colliding_identifiers:
            self.colliding_identifiers.remove(identifier)
            original = _UniqueViolation(self.collision_constraint_name)
            raise IntegrityError("insert", values, original)
        self.pending.append(SimpleNamespace(**values))
        return _ScalarResult(None)

    def begin_nested(self) -> _NestedTransaction:
        # SQLAlchemy unconditionally flushes before establishing a savepoint.
        self.flush()
        return _NestedTransaction(self)

    def add(self, row: object) -> None:
        raise AssertionError("registry rows must be inserted with a Core INSERT")

    def flush(self) -> None:
        if self.pre_savepoint_flush_error is not None:
            error = self.pre_savepoint_flush_error
            self.pre_savepoint_flush_error = None
            raise error


    def commit(self) -> None:
        self.commit_calls += 1
        self.durable.extend(self.pending)
        self.pending.clear()
        self._original_catalog_symbol_id = self.symbol.catalog_symbol_id

    def rollback(self) -> None:
        self.pending.clear()
        self.symbol.catalog_symbol_id = self._original_catalog_symbol_id


class CorrectionSession(AllocationSession):
    """Transactional fake for reviewed correction state and savepoints."""

    def __init__(
        self,
        symbol: object,
        registry_rows: list[object],
        *,
        insert_errors: dict[str, IntegrityError] | None = None,
    ) -> None:
        super().__init__(symbol, insert_errors=insert_errors)
        self.registry_rows = {row.identifier: row for row in registry_rows}
        self._original_registry_state = {
            identifier: vars(row).copy()
            for identifier, row in self.registry_rows.items()
        }

    def get(self, model, key, *, with_for_update=False):
        self.lock_requests.append((model, key, with_for_update))
        if model is GovernedSymbol:
            return self.symbol if key == self.symbol.id else None
        if model is CatalogSymbolIdentifier:
            return self.registry_rows.get(key)
        return None

    def rollback(self) -> None:
        super().rollback()
        for identifier, state in self._original_registry_state.items():
            vars(self.registry_rows[identifier]).update(state)


class AutoflushFalseCorrectionSession(CorrectionSession):
    """Correction fake with database-visible partial canonical uniqueness."""

    def __init__(self, symbol: object, registry_rows: list[object]) -> None:
        super().__init__(symbol, registry_rows)
        self.database_rows = {
            identifier: vars(row).copy()
            for identifier, row in self.registry_rows.items()
        }

    def flush(self) -> None:
        super().flush()
        for identifier, row in self.registry_rows.items():
            self.database_rows[identifier] = vars(row).copy()

    def execute(self, statement):
        if isinstance(statement, Insert):
            values = statement.compile().params
            if any(
                row["role"] == "canonical"
                and row["governed_symbol_id"] == values["governed_symbol_id"]
                for row in self.database_rows.values()
            ):
                original = _UniqueViolation(
                    "uq_catalog_symbol_identifiers_canonical_governed_symbol"
                )
                raise IntegrityError("insert", values, original)
        result = super().execute(statement)
        if isinstance(statement, Insert):
            values = statement.compile().params
            self.database_rows[values["identifier"]] = dict(values)
        return result

    def begin_nested(self) -> _NestedTransaction:
        nested = super().begin_nested()
        nested.database_snapshot = {
            identifier: state.copy()
            for identifier, state in self.database_rows.items()
        }
        return nested


def _registry_row(
    identifier: str,
    role: str,
    governed_symbol_id: uuid.UUID | None,
) -> object:
    return SimpleNamespace(
        identifier=identifier,
        role=role,
        governed_symbol_id=governed_symbol_id,
        allocation_source="global_sequence",
        allocated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        changed_at=None,
        changed_by=None,
        change_reason=None,
    )


def test_reviewed_correction_preserves_old_identifier_as_historical_alias() -> None:
    correct_catalog_symbol_id = getattr(
        catalog_symbol_ids_service, "correct_catalog_symbol_id", None
    )
    assert callable(correct_catalog_symbol_id)
    symbol_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    changed_at = datetime(2026, 8, 2, 13, 45, tzinfo=timezone.utc)
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    session = CorrectionSession(symbol, [old_row])

    result = correct_catalog_symbol_id(
        session,
        symbol_id,
        "s-900001",
        actor_id=actor_id,
        reason="Reviewer corrected a transposed legacy ID",
        preserve_old_link=True,
        changed_at=changed_at,
    )

    assert result == "S-900001"
    assert symbol.catalog_symbol_id == "S-900001"
    assert old_row.role == "historical_alias"
    assert old_row.governed_symbol_id == symbol_id
    assert old_row.changed_at == changed_at
    assert old_row.changed_by == actor_id
    assert old_row.change_reason == "Reviewer corrected a transposed legacy ID"
    assert len(session.pending) == 1
    new_row = session.pending[0]
    assert vars(new_row) == {
        "identifier": "S-900001",
        "role": "canonical",
        "governed_symbol_id": symbol_id,
        "allocation_source": "reviewed_correction",
        "allocated_at": changed_at,
        "changed_at": changed_at,
        "changed_by": actor_id,
        "change_reason": "Reviewer corrected a transposed legacy ID",
    }
    assert session.lock_requests == [
        (GovernedSymbol, symbol_id, True),
        (CatalogSymbolIdentifier, "S-000001", True),
        (CatalogSymbolIdentifier, "S-900001", True),
    ]
    assert session.commit_calls == 0


def test_reviewed_correction_flushes_old_canonical_before_core_insert() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    session = AutoflushFalseCorrectionSession(symbol, [old_row])

    result = catalog_symbol_ids_service.correct_catalog_symbol_id(
        session,
        symbol_id,
        "S-900001",
        actor_id=uuid.uuid4(),
        reason="Reviewer corrected a transposed legacy ID",
        preserve_old_link=True,
        changed_at=datetime(2026, 8, 2, 13, 45, tzinfo=timezone.utc),
    )

    assert result == "S-900001"
    assert session.database_rows["S-000001"]["role"] == "historical_alias"
    assert session.database_rows["S-900001"]["role"] == "canonical"


def _run_correction(
    session: CorrectionSession,
    symbol_id: uuid.UUID,
    **overrides: object,
) -> str:
    arguments = {
        "actor_id": uuid.uuid4(),
        "reason": "Approved catalog identity correction",
        "preserve_old_link": True,
        "changed_at": datetime(2026, 8, 2, 14, tzinfo=timezone.utc),
    }
    arguments.update(overrides)
    return catalog_symbol_ids_service.correct_catalog_symbol_id(
        session, symbol_id, "S-900002", **arguments
    )


def test_reviewed_correction_can_permanently_tombstone_old_identifier() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    session = CorrectionSession(symbol, [old_row])

    result = _run_correction(session, symbol_id, preserve_old_link=False)

    assert result == "S-900002"
    assert old_row.role == "tombstone"
    assert old_row.governed_symbol_id is None


@pytest.mark.parametrize("role", ["canonical", "historical_alias", "tombstone"])
def test_reviewed_correction_rejects_every_reserved_registry_role_without_mutation(
    role: str,
) -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    collision_target = None if role == "tombstone" else uuid.uuid4()
    collision = _registry_row("S-900002", role, collision_target)
    session = CorrectionSession(symbol, [old_row, collision])

    with pytest.raises(ValueError, match="permanently reserved"):
        _run_correction(session, symbol_id)

    assert symbol.catalog_symbol_id == "S-000001"
    assert old_row.role == "canonical"
    assert old_row.governed_symbol_id == symbol_id
    assert session.pending == []


@pytest.mark.parametrize("new_identifier", ["bad_identifier", "S-000001"])
def test_reviewed_correction_rejects_malformed_or_current_identifier(
    new_identifier: str,
) -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    session = CorrectionSession(symbol, [old_row])

    with pytest.raises(ValueError):
        catalog_symbol_ids_service.correct_catalog_symbol_id(
            session,
            symbol_id,
            new_identifier,
            actor_id=uuid.uuid4(),
            reason="Reviewed correction",
            preserve_old_link=True,
            changed_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

    assert symbol.catalog_symbol_id == "S-000001"
    assert old_row.role == "canonical"
    assert session.pending == []


def test_reviewed_correction_rejects_missing_governed_symbol() -> None:
    existing_id = uuid.uuid4()
    symbol = SimpleNamespace(id=existing_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", existing_id)
    session = CorrectionSession(symbol, [old_row])
    missing_id = uuid.uuid4()

    with pytest.raises(LookupError, match=str(missing_id)):
        _run_correction(session, missing_id)

    assert session.lock_requests == [(GovernedSymbol, missing_id, True)]


def test_reviewed_correction_requires_a_current_catalog_identifier() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = CorrectionSession(symbol, [])

    with pytest.raises(ValueError, match="no current"):
        _run_correction(session, symbol_id)

    assert session.pending == []


def test_reviewed_correction_requires_current_registry_row() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    session = CorrectionSession(symbol, [])

    with pytest.raises(ValueError, match="registry row is missing"):
        _run_correction(session, symbol_id)

    assert symbol.catalog_symbol_id == "S-000001"


@pytest.mark.parametrize(
    ("role", "linked_symbol"),
    [("historical_alias", "same"), ("canonical", "other")],
)
def test_reviewed_correction_rejects_inconsistent_current_registry_row(
    role: str,
    linked_symbol: str,
) -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    governed_symbol_id = symbol_id if linked_symbol == "same" else uuid.uuid4()
    old_row = _registry_row("S-000001", role, governed_symbol_id)
    session = CorrectionSession(symbol, [old_row])

    with pytest.raises(ValueError, match="inconsistent"):
        _run_correction(session, symbol_id)

    assert symbol.catalog_symbol_id == "S-000001"
    assert session.pending == []


@pytest.mark.parametrize(
    "overrides",
    [
        {"actor_id": None},
        {"actor_id": uuid.UUID(int=0)},
        {"actor_id": "not-a-uuid"},
        {"changed_at": datetime(2026, 8, 2)},
        {"reason": None},
        {"reason": ""},
        {"reason": "   "},
        {"reason": "x" * 501},
        {"preserve_old_link": 1},
    ],
)
def test_reviewed_correction_validates_audit_boundaries_before_locking(
    overrides: dict[str, object],
) -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    session = CorrectionSession(symbol, [old_row])

    with pytest.raises(ValueError):
        _run_correction(session, symbol_id, **overrides)

    assert session.lock_requests == []
    assert session.pending == []


def test_reviewed_correction_accepts_and_strips_bounded_reason() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    session = CorrectionSession(symbol, [old_row])

    _run_correction(session, symbol_id, reason=" " + ("x" * 500) + " ")

    assert old_row.change_reason == "x" * 500
    assert session.pending[0].change_reason == "x" * 500


def test_caller_rollback_restores_reviewed_correction_state() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    session = CorrectionSession(symbol, [old_row])

    _run_correction(session, symbol_id, preserve_old_link=False)
    session.rollback()

    assert symbol.catalog_symbol_id == "S-000001"
    assert old_row.role == "canonical"
    assert old_row.governed_symbol_id == symbol_id
    assert old_row.changed_at is None
    assert old_row.changed_by is None
    assert old_row.change_reason is None
    assert session.pending == []
    assert session.durable == []
    assert session.commit_calls == 0


def test_concurrent_identifier_pk_collision_is_clear_and_does_not_retire_old_row() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    error = IntegrityError(
        "insert", {}, _UniqueViolation("pk_catalog_symbol_identifiers")
    )
    session = CorrectionSession(
        symbol, [old_row], insert_errors={"S-900002": error}
    )

    with pytest.raises(ValueError, match="permanently reserved"):
        _run_correction(session, symbol_id, preserve_old_link=False)

    assert symbol.catalog_symbol_id == "S-000001"
    assert old_row.role == "canonical"
    assert old_row.governed_symbol_id == symbol_id
    assert old_row.changed_at is None
    assert session.pending == []


def test_unrelated_correction_integrity_error_propagates() -> None:
    class ForeignKeyViolation(Exception):
        sqlstate = "23503"

    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    error = IntegrityError("insert", {}, ForeignKeyViolation())
    session = CorrectionSession(
        symbol, [old_row], insert_errors={"S-900002": error}
    )

    with pytest.raises(IntegrityError) as caught:
        _run_correction(session, symbol_id)

    assert caught.value is error


def test_unrelated_pre_savepoint_correction_flush_error_propagates_without_mutation() -> None:
    class UniqueViolation(Exception):
        sqlstate = "23505"

    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000001")
    old_row = _registry_row("S-000001", "canonical", symbol_id)
    error = IntegrityError("unrelated pending insert", {}, UniqueViolation())
    session = CorrectionSession(symbol, [old_row])
    session.pre_savepoint_flush_error = error

    with pytest.raises(IntegrityError) as caught:
        _run_correction(session, symbol_id, preserve_old_link=False)

    assert caught.value is error
    assert symbol.catalog_symbol_id == "S-000001"
    assert old_row.role == "canonical"
    assert session.pending == []


def test_catalog_symbol_id_pattern_has_required_grammar() -> None:
    assert isinstance(CATALOG_SYMBOL_ID_PATTERN, re.Pattern)
    assert (
        CATALOG_SYMBOL_ID_PATTERN.pattern
        == r"^[A-Z0-9](?:[A-Z0-9-]{0,30}[A-Z0-9])?$"
    )


def test_postgresql_bigint_max_matches_sequence_domain() -> None:
    assert POSTGRESQL_BIGINT_MAX == 9_223_372_036_854_775_807


def test_normalize_preserves_valid_uppercase_identifier() -> None:
    assert normalize_catalog_symbol_id("0003-12") == "0003-12"


def test_normalize_uppercases_valid_identifier() -> None:
    assert normalize_catalog_symbol_id("s-000001") == "S-000001"


@pytest.mark.parametrize("value", ["A1", "A" * 32])
def test_normalize_accepts_length_boundaries(value: str) -> None:
    assert normalize_catalog_symbol_id(value) == value


def test_normalize_accepts_consecutive_internal_hyphens() -> None:
    assert normalize_catalog_symbol_id("A--B") == "A--B"


def test_normalize_rejects_invalid_ascii_grammar() -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id("S_000001")


@pytest.mark.parametrize("value", [" S-000001", "S-000001 ", "\tS-000001", "S-000001\n"])
def test_normalize_rejects_leading_or_trailing_whitespace(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id(value)


@pytest.mark.parametrize("character", ["/", "\\", "%", "?", "#"])
def test_normalize_rejects_path_and_url_delimiters(character: str) -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id(f"S{character}000001")


@pytest.mark.parametrize("character", ["\x00", "\x1f", "\x7f"])
def test_normalize_rejects_control_characters(character: str) -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id(f"S-{character}000001")


@pytest.mark.parametrize("value", ["Ｓ-000001", "S-٠٠٠٠٠١", "S‐000001"])
def test_normalize_rejects_unicode_lookalikes(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id(value)


@pytest.mark.parametrize("value", ["-S000001", "S000001-"])
def test_normalize_rejects_leading_or_trailing_hyphen(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id(value)


def test_normalize_rejects_one_character_identifier() -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id("S")


def test_normalize_rejects_empty_identifier() -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id("")


def test_normalize_rejects_identifier_over_32_characters() -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id("S" * 33)


@pytest.mark.parametrize("value", [True, False, None, 1, 1.5, [], {}, object()])
def test_normalize_rejects_non_strings_without_stringifying(value: object) -> None:
    with pytest.raises(ValueError):
        normalize_catalog_symbol_id(value)


def test_format_allocated_catalog_symbol_id_zero_pads_sequence_value() -> None:
    assert format_allocated_catalog_symbol_id(1) == "S-000001"


def test_format_allocated_catalog_symbol_id_does_not_truncate_large_value() -> None:
    assert format_allocated_catalog_symbol_id(1_000_000) == "S-1000000"


def test_format_allocated_catalog_symbol_id_formats_postgresql_bigint_maximum() -> None:
    assert (
        format_allocated_catalog_symbol_id(POSTGRESQL_BIGINT_MAX)
        == "S-9223372036854775807"
    )


def test_format_allocated_catalog_symbol_id_rejects_value_above_postgresql_bigint_maximum() -> None:
    with pytest.raises(ValueError):
        format_allocated_catalog_symbol_id(POSTGRESQL_BIGINT_MAX + 1)


@pytest.mark.parametrize("value", [True, False])
def test_format_allocated_catalog_symbol_id_rejects_bool(value: bool) -> None:
    with pytest.raises(ValueError):
        format_allocated_catalog_symbol_id(value)


@pytest.mark.parametrize("value", [None, "1", 1.5, object()])
def test_format_allocated_catalog_symbol_id_rejects_non_int(value: object) -> None:
    with pytest.raises(ValueError):
        format_allocated_catalog_symbol_id(value)  # type: ignore[arg-type]


def test_format_allocated_catalog_symbol_id_rejects_zero() -> None:
    with pytest.raises(ValueError):
        format_allocated_catalog_symbol_id(0)


def test_format_allocated_catalog_symbol_id_rejects_negative_value() -> None:
    with pytest.raises(ValueError):
        format_allocated_catalog_symbol_id(-1)


def test_ensure_catalog_symbol_id_returns_existing_identifier_without_allocating() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id="S-000123")
    session = AllocationSession(symbol)

    assert ensure_catalog_symbol_id(
        session,
        symbol_id,
        allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    ) == "S-000123"
    assert session.lock_requests == [(GovernedSymbol, symbol_id, True)]
    assert session.sequence_calls == 0
    assert session.commit_calls == 0


def test_ensure_catalog_symbol_id_allocates_first_sequence_value_atomically() -> None:
    symbol_id = uuid.uuid4()
    allocated_at = datetime(2026, 8, 2, 12, 30, tzinfo=timezone.utc)
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(symbol, sequence_values=(1,))

    identifier = ensure_catalog_symbol_id(
        session,
        symbol_id,
        allocated_at=allocated_at,
    )

    assert identifier == "S-000001"
    assert symbol.catalog_symbol_id == identifier
    assert session.sequence_calls == 1
    assert session.commit_calls == 0
    assert len(session.pending) == 1
    registry_row = session.pending[0]
    assert registry_row.identifier == identifier
    assert registry_row.role == "canonical"
    assert registry_row.governed_symbol_id == symbol_id
    assert registry_row.allocation_source == "global_sequence"
    assert registry_row.allocated_at == allocated_at


def test_locked_lookup_is_idempotent_after_committed_assignment_is_visible() -> None:
    symbol_id = uuid.uuid4()
    first_symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    first_caller = AllocationSession(first_symbol, sequence_values=(1,))

    first_identifier = ensure_catalog_symbol_id(
        first_caller,
        symbol_id,
        allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    first_caller.commit()

    visible_symbol = SimpleNamespace(
        id=symbol_id,
        catalog_symbol_id=first_identifier,
    )
    second_caller = AllocationSession(visible_symbol, sequence_values=(2,))
    second_identifier = ensure_catalog_symbol_id(
        second_caller,
        symbol_id,
        allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert first_identifier == second_identifier == "S-000001"
    assert first_caller.lock_requests == [(GovernedSymbol, symbol_id, True)]
    assert second_caller.lock_requests == [(GovernedSymbol, symbol_id, True)]
    assert first_caller.sequence_calls == 1
    assert second_caller.sequence_calls == 0
    assert len(first_caller.durable) == 1


def test_caller_rollback_discards_pending_catalog_symbol_assignment() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(symbol, sequence_values=(1,))

    ensure_catalog_symbol_id(
        session,
        symbol_id,
        allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )
    assert symbol.catalog_symbol_id == "S-000001"
    assert len(session.pending) == 1

    session.rollback()

    assert symbol.catalog_symbol_id is None
    assert session.pending == []
    assert session.durable == []
    assert session.commit_calls == 0


def test_identifier_primary_key_violation_consumes_gap_and_retries() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(
        symbol,
        sequence_values=(1, 2),
        colliding_identifiers=("S-000001",),
    )

    identifier = ensure_catalog_symbol_id(
        session,
        symbol_id,
        allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert identifier == "S-000002"
    assert symbol.catalog_symbol_id == "S-000002"
    assert session.sequence_calls == 2
    assert [row.identifier for row in session.pending] == ["S-000002"]


def test_identifier_primary_key_violation_supports_legacy_driver_fields() -> None:
    class LegacyUniqueViolation(Exception):
        pgcode = "23505"
        constraint_name = "pk_catalog_symbol_identifiers"

    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(
        symbol,
        sequence_values=(1, 2),
        insert_errors={
            "S-000001": IntegrityError("insert", {}, LegacyUniqueViolation())
        },
    )

    identifier = ensure_catalog_symbol_id(
        session,
        symbol_id,
        allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
    )

    assert identifier == "S-000002"
    assert session.sequence_calls == 2


def test_unrelated_pre_savepoint_flush_error_is_not_caught_or_retried() -> None:
    class UniqueViolation(Exception):
        sqlstate = "23505"

    error = IntegrityError("unrelated pending insert", {}, UniqueViolation())
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(
        symbol,
        sequence_values=(1, 2),
        pre_savepoint_flush_error=error,
    )

    with pytest.raises(IntegrityError) as caught:
        ensure_catalog_symbol_id(
            session,
            symbol_id,
            allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

    assert caught.value is error
    assert session.sequence_calls == 0
    assert symbol.catalog_symbol_id is None


def test_canonical_per_symbol_unique_violation_is_not_retried() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(
        symbol,
        sequence_values=(1, 2),
        colliding_identifiers=("S-000001",),
        collision_constraint_name=(
            "uq_catalog_symbol_identifiers_canonical_governed_symbol"
        ),
    )

    with pytest.raises(IntegrityError):
        ensure_catalog_symbol_id(
            session,
            symbol_id,
            allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

    assert session.sequence_calls == 1
    assert symbol.catalog_symbol_id is None


@pytest.mark.parametrize(
    ("allocated_at", "allocation_source"),
    [
        (datetime(2026, 8, 2), "global_sequence"),
        (datetime(2026, 8, 2, tzinfo=timezone.utc), "manual"),
    ],
)
def test_ensure_catalog_symbol_id_rejects_invalid_allocation_metadata(
    allocated_at: datetime,
    allocation_source: str,
) -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(symbol, sequence_values=(1,))

    with pytest.raises(ValueError):
        ensure_catalog_symbol_id(
            session,
            symbol_id,
            allocated_at=allocated_at,
            allocation_source=allocation_source,
        )

    assert session.lock_requests == []
    assert session.sequence_calls == 0


def test_non_unique_integrity_error_is_not_retried() -> None:
    class ForeignKeyViolation(Exception):
        sqlstate = "23503"

    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    error = IntegrityError("insert", {}, ForeignKeyViolation())
    session = AllocationSession(
        symbol,
        sequence_values=(1, 2),
        insert_errors={"S-000001": error},
    )

    with pytest.raises(IntegrityError) as caught:
        ensure_catalog_symbol_id(
            session,
            symbol_id,
            allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

    assert caught.value is error
    assert session.sequence_calls == 1
    assert symbol.catalog_symbol_id is None


def test_final_identifier_primary_key_collision_is_bounded_and_re_raised() -> None:
    symbol_id = uuid.uuid4()
    symbol = SimpleNamespace(id=symbol_id, catalog_symbol_id=None)
    session = AllocationSession(
        symbol,
        sequence_values=(1, 2, 3, 4),
        colliding_identifiers=("S-000001", "S-000002", "S-000003"),
    )

    with pytest.raises(IntegrityError) as caught:
        ensure_catalog_symbol_id(
            session,
            symbol_id,
            allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

    assert session.sequence_calls == CATALOG_SYMBOL_ID_ALLOCATION_ATTEMPTS == 3
    assert symbol.catalog_symbol_id is None
    assert getattr(caught.value.orig, "sqlstate", None) == "23505"
    assert (
        getattr(getattr(caught.value.orig, "diag", None), "constraint_name", None)
        == "pk_catalog_symbol_identifiers"
    )


def test_missing_governed_symbol_fails_after_locked_lookup() -> None:
    class MissingSymbolSession:
        def __init__(self) -> None:
            self.lock_requests: list[tuple[object, object, bool]] = []

        def get(self, model, key, *, with_for_update=False):
            self.lock_requests.append((model, key, with_for_update))
            return None

    symbol_id = uuid.uuid4()
    session = MissingSymbolSession()

    with pytest.raises(LookupError, match=str(symbol_id)):
        ensure_catalog_symbol_id(
            session,
            symbol_id,
            allocated_at=datetime(2026, 8, 2, tzinfo=timezone.utc),
        )

    assert session.lock_requests == [(GovernedSymbol, symbol_id, True)]
