"""Guard rails on the ICS crosswalk disposition service.

Every case here is refused *before* the session is touched, so each test
passes a sentinel object as the session: if validation ever regressed into
loading the row first, these would raise AttributeError rather than pass.
Behaviour that needs real storage -- the attribution check constraint, the
transition lock, re-import idempotency -- lives in `test_ics_storage.py`
against a disposable PostgreSQL.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from symgov_backend import ics_taxonomy as api


class _UntouchedSession:
    """Any attribute access means validation let an invalid call through."""

    def __getattr__(self, name):  # pragma: no cover - only on regression
        raise AssertionError(f"session.{name} reached with invalid arguments")


NOW = datetime(2026, 9, 16, 9, 0, tzinfo=timezone.utc)


def _disposition(**overrides):
    call = dict(
        crosswalk_id=uuid.uuid4(),
        target_status="approved",
        occurred_at=NOW,
        reviewed_by_user_id=uuid.uuid4(),
    )
    call.update(overrides)
    identifier = call.pop("crosswalk_id")
    return api.disposition_crosswalk(_UntouchedSession(), identifier, **call)


def test_a_disposition_always_names_a_reviewer():
    """There is no deterministic crosswalk method, so nothing may go unattributed.

    A `source_mapping` classification assignment may be verified with no named
    reviewer under section 8.4's explicit policy. Every crosswalk disposition
    is a human semantic judgement between competing ICS targets, so the
    reviewer is required rather than optional.
    """
    with pytest.raises(ValueError, match="named reviewer"):
        _disposition(reviewed_by_user_id=None)
    with pytest.raises(ValueError, match="named reviewer"):
        _disposition(reviewed_by_user_id=str(uuid.uuid4()))


def test_only_a_decision_may_be_recorded():
    for target in ("needs_review", "initial_broader", "verified", "", None):
        with pytest.raises(ValueError, match="approved or rejected"):
            _disposition(target_status=target)


def test_decision_time_must_be_unambiguous():
    with pytest.raises(ValueError, match="timezone-aware"):
        _disposition(occurred_at=datetime(2026, 9, 16, 9, 0))
    with pytest.raises(ValueError, match="timezone-aware"):
        _disposition(occurred_at="2026-09-16T09:00:00+00:00")


def test_crosswalk_id_must_be_a_uuid():
    with pytest.raises(ValueError, match="must be a UUID"):
        _disposition(crosswalk_id=str(uuid.uuid4()))


def test_review_note_is_optional_but_never_blank_or_oversized():
    for blank in ("", "   ", "\n\t"):
        with pytest.raises(ValueError, match="non-blank"):
            _disposition(review_note=blank)
    with pytest.raises(ValueError, match="non-blank"):
        _disposition(review_note=42)
    with pytest.raises(ValueError, match="storage boundary"):
        _disposition(review_note="x" * (api.MAX_REVIEW_NOTE + 1))


def test_transitions_allow_correcting_a_decision_but_never_un_reviewing_one():
    """A crosswalk row cannot be proposed afresh, so a decision is correctable.

    `CLASSIFICATION_ASSIGNMENT_TRANSITIONS` makes `rejected` terminal because
    a reviewer who changes their mind proposes the assignment again. A
    crosswalk row is derived from a pinned import and an idempotent re-import
    deliberately leaves `review_status` alone, so the same terminality would
    strand a mistaken disposition behind raw SQL -- exactly what this service
    replaces.
    """
    transitions = api.CROSSWALK_REVIEW_TRANSITIONS

    assert set(transitions) == api.CROSSWALK_REVIEW_STATUSES
    for undecided in api.CROSSWALK_UNDECIDED_STATUSES:
        assert transitions[undecided] == api.CROSSWALK_DECIDED_STATUSES
    assert transitions["approved"] == frozenset({"rejected"})
    assert transitions["rejected"] == frozenset({"approved"})

    # No state may return to undecided: a review cannot be un-done.
    assert not any(
        api.CROSSWALK_UNDECIDED_STATUSES & targets for targets in transitions.values()
    )
    assert (
        api.CROSSWALK_UNDECIDED_STATUSES | api.CROSSWALK_DECIDED_STATUSES
        == api.CROSSWALK_REVIEW_STATUSES
    )


def test_review_statuses_match_the_storage_constraint():
    """The service vocabulary and the check constraint cannot drift apart."""
    from symgov_backend.models import ICSDomainCrosswalk
    from sqlalchemy import CheckConstraint

    constraint = next(
        item
        for item in ICSDomainCrosswalk.__table__.constraints
        if isinstance(item, CheckConstraint)
        and item.name == "ck_ics_domain_crosswalks_review_status"
    )
    for status in api.CROSSWALK_REVIEW_STATUSES:
        assert f"'{status}'" in str(constraint.sqltext)


def test_listing_rejects_an_unknown_review_status():
    with pytest.raises(ValueError, match="invalid crosswalk review status"):
        api.list_domain_crosswalks(_UntouchedSession(), review_status="verified")
    with pytest.raises(ValueError, match="must be a UUID"):
        api.list_domain_crosswalks(_UntouchedSession(), import_id="not-a-uuid")


def test_cli_names_an_absent_setting_but_never_a_credential(monkeypatch, capsys):
    """A missing setting is safe to name; a driver's exception text is not."""
    from symgov_backend import ics_review as cli

    monkeypatch.delenv("SYMGOV_DATABASE_URL", raising=False)
    assert cli.main(["list"]) == 1
    failure = capsys.readouterr()
    assert "SYMGOV_DATABASE_URL is required" in failure.err
    assert failure.out == ""


def test_cli_failure_text_matches_the_command_that_failed(monkeypatch, capsys):
    """A read-only listing must not report that no decision was recorded."""
    from symgov_backend import ics_review as cli

    monkeypatch.setenv("SYMGOV_DATABASE_URL", "postgresql://unreachable.invalid/db")

    assert cli.main(["list"]) == 1
    listing = capsys.readouterr().err
    assert "could not list the queue" in listing
    assert "decision" not in listing

    assert cli.main([
        "approve", "--crosswalk-id", str(uuid.uuid4()), "--reviewer-id", str(uuid.uuid4()),
    ]) == 1
    deciding = capsys.readouterr().err
    assert "no decision is recorded" in deciding

    # Neither path echoes anything from the connection string.
    assert "unreachable.invalid" not in listing + deciding


def test_cli_requires_a_named_reviewer_for_every_decision(capsys):
    """argparse refuses the call outright rather than defaulting to anonymous."""
    from symgov_backend import ics_review as cli

    for missing in (
        ["approve", "--crosswalk-id", str(uuid.uuid4())],
        ["reject", "--crosswalk-id", str(uuid.uuid4())],
    ):
        with pytest.raises(SystemExit) as exit_code:
            cli.main(missing)
        assert exit_code.value.code == 2
        assert "--reviewer-id" in capsys.readouterr().err


def test_apply_register_requires_a_reviewer_and_is_dry_run_by_default(capsys):
    """The register records decisions, not authority, so a reviewer is required."""
    from symgov_backend import ics_review as cli

    with pytest.raises(SystemExit) as exit_code:
        cli.main(["apply-register"])
    assert exit_code.value.code == 2
    assert "--reviewer-id" in capsys.readouterr().err

    # `--apply` is opt-in, mirroring ics_import: the bare command cannot write.
    parser_help = None
    with pytest.raises(SystemExit):
        cli.main(["apply-register", "--help"])
    parser_help = capsys.readouterr().out
    assert "--apply" in parser_help
    assert "dry-run" in parser_help or "nothing is recorded" in parser_help


def test_apply_register_failure_text_is_its_own(monkeypatch, capsys):
    from symgov_backend import ics_review as cli

    monkeypatch.delenv("SYMGOV_DATABASE_URL", raising=False)
    assert cli.main(["apply-register", "--reviewer-id", str(uuid.uuid4())]) == 1
    assert "SYMGOV_DATABASE_URL is required" in capsys.readouterr().err

    monkeypatch.setenv("SYMGOV_DATABASE_URL", "postgresql://unreachable.invalid/db")
    assert cli.main(["apply-register", "--reviewer-id", str(uuid.uuid4())]) == 1
    failure = capsys.readouterr().err
    assert "register not applied" in failure
    assert "unreachable.invalid" not in failure
