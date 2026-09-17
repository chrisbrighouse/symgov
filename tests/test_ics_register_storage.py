"""Committed application of the reviewed decision register, on real PostgreSQL.

This owns a separate module-scoped database from `test_ics_storage.py` on
purpose: that module records its own dispositions, and a recorded decision
cannot be moved back to undecided, so the two would fight over the same rows.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from symgov_backend import ics_taxonomy as api
from symgov_backend.models import ICSDomainCrosswalk

from test_classification_data_model_postgresql import classification_database, author_id, _alembic


def test_register_application_is_committed_idempotent_and_fails_closed(
    classification_database, author_id, monkeypatch, capsys
):
    engine, url = classification_database
    _alembic(url, 'upgrade', '20260916_0059')
    content = (Path(api.__file__).parent / 'data/ICS.csv').read_bytes()
    prepared = api.prepare_import(content, retrieved_at=datetime.now(timezone.utc), last_modified=None)
    with Session(engine) as session:
        result = api.ingest_taxonomy(session, prepared, author_id=author_id)
        session.commit()

    register = api.load_decision_register()
    approvals = sum(row['decision'] == 'approved' for row in register['decisions'])
    rejections = sum(row['decision'] == 'rejected' for row in register['decisions'])
    assert approvals + rejections == len(prepared['crosswalk'])

    from symgov_backend import ics_review as cli
    monkeypatch.setenv('SYMGOV_DATABASE_URL', url)

    # A dry run resolves the whole plan and records nothing.
    assert cli.main(['apply-register', '--reviewer-id', str(author_id)]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry['mode'] == 'dry-run'
    assert dry['problems'] == []
    assert dry['registered'] == len(prepared['crosswalk'])
    assert dry['recorded'] == len(prepared['crosswalk'])
    assert dry['already_recorded'] == 0
    with Session(engine) as session:
        assert session.query(ICSDomainCrosswalk).filter(
            ICSDomainCrosswalk.reviewed_by_user_id.isnot(None)
        ).count() == 0

    # Applying records every decision, each one attributed.
    assert cli.main(['apply-register', '--reviewer-id', str(author_id), '--apply']) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied['mode'] == 'apply'
    assert applied['recorded'] == len(prepared['crosswalk'])
    assert url not in json.dumps(applied)

    expected = {
        (row['domain'], row['ics_code']): (row['decision'], row.get('note'))
        for row in register['decisions']
    }
    with Session(engine) as session:
        described = api.describe_domain_crosswalks(session, import_id=result['import_id'])
        assert len(described) == len(expected)
        for row in described:
            decision, note = expected[(row['domain'], row['ics_code'])]
            assert row['review_status'] == decision
            assert row['review_note'] == note
            assert row['reviewed_by_user_id'] == str(author_id)
            assert row['reviewed_at'] is not None
        statuses = [row['review_status'] for row in described]
        assert statuses.count('approved') == approvals
        assert statuses.count('rejected') == rejections
        # A browse decision, never a semantic assignment.
        assert session.execute(text('SELECT count(*) FROM concept_classification_assignments')).scalar() == 0
        assert session.execute(text('SELECT count(*) FROM symbol_revision_classifications')).scalar() == 0

    # Re-running is a no-op rather than a second round of decisions.
    assert cli.main(['apply-register', '--reviewer-id', str(author_id), '--apply']) == 0
    again = json.loads(capsys.readouterr().out)
    assert again['recorded'] == 0
    assert again['already_recorded'] == len(prepared['crosswalk'])
    assert again['problems'] == []

    # The import stays idempotent once every decision is recorded.
    with Session(engine) as session:
        assert api.ingest_taxonomy(session, prepared, author_id=author_id) == result
        session.commit()

    # A register that disagrees with a recorded decision is refused outright.
    with Session(engine) as session:
        flipped = dict(register)
        flipped['decisions'] = [
            {**row, 'decision': 'rejected' if row['decision'] == 'approved' else 'approved'}
            for row in register['decisions']
        ]
        with pytest.raises(ValueError, match='already'):
            api.apply_decision_register(
                session, reviewed_by_user_id=author_id,
                occurred_at=datetime.now(timezone.utc), register=flipped,
            )
        session.rollback()

    # A register naming a pair the import does not have is refused too, and
    # the refusal names domains and codes rather than driver text.
    with Session(engine) as session:
        unknown = {'decisions': register['decisions'] + [
            {'domain': 'Nonexistent', 'ics_code': '99', 'decision': 'approved'}
        ]}
        with pytest.raises(ValueError, match='not in the queue'):
            api.apply_decision_register(
                session, reviewed_by_user_id=author_id,
                occurred_at=datetime.now(timezone.utc), register=unknown,
            )
        session.rollback()

    # Every decision still stands, so no refusal applied anything partially.
    with Session(engine) as session:
        assert session.query(ICSDomainCrosswalk).filter(
            ICSDomainCrosswalk.reviewed_by_user_id.isnot(None)
        ).count() == len(expected)

    # A correction is possible, but only when asked for explicitly. Revise one
    # approved row to rejected and nothing else; exactly that row moves.
    with Session(engine) as session:
        revised = dict(register)
        revised['decisions'] = [
            {**row, 'decision': 'rejected', 'note': 'corrected on review'}
            if (row['domain'], row['ics_code']) == ('Mechanical', '21')
            else row
            for row in register['decisions']
        ]
        plan = api.apply_decision_register(
            session, reviewed_by_user_id=author_id,
            occurred_at=datetime.now(timezone.utc), register=revised,
            allow_correction=True,
        )
        assert [(a['domain'], a['ics_code'], a['from'], a['to']) for a in plan['planned']] == [
            ('Mechanical', '21', 'approved', 'rejected')
        ]
        assert len(plan['unchanged']) == len(expected) - 1
        corrected = session.get(ICSDomainCrosswalk, uuid.UUID(plan['planned'][0]['crosswalk_id']))
        assert corrected.review_status == 'rejected'
        assert corrected.review_note == 'corrected on review'
        session.rollback()

    # Rolled back, so the register's own decision is what remains on record.
    with Session(engine) as session:
        mechanical = [
            row for row in api.describe_domain_crosswalks(session)
            if (row['domain'], row['ics_code']) == ('Mechanical', '21')
        ]
        assert [row['review_status'] for row in mechanical] == ['approved']
