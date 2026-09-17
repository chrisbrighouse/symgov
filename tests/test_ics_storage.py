"""Actual committed PostgreSQL import, replay and fail-closed boundaries."""
from datetime import datetime, timezone
import json
from pathlib import Path
import uuid

import pytest
from sqlalchemy import event, text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from symgov_backend import ics_taxonomy as api
from symgov_backend.models import (
    ClassificationNode,
    ClassificationScheme,
    ICSDomainCrosswalk,
    ICSTaxonomyImport,
)
from test_classification_data_model_postgresql import classification_database, author_id, _alembic


def test_durable_import_replay_and_rollback(classification_database, author_id, tmp_path, monkeypatch, capsys):
    engine, url = classification_database
    _alembic(url, 'upgrade', '20260916_0059')
    # The review revision is reversible on its own while nothing is decided.
    _alembic(url, 'downgrade', '20260915_0058')
    _alembic(url, 'upgrade', '20260916_0059')
    _alembic(url, 'downgrade', '20260911_0057')
    _alembic(url, 'upgrade', '20260916_0059')
    content = (Path(api.__file__).parent / 'data/ICS.csv').read_bytes()
    prepared = api.prepare_import(content, retrieved_at=datetime.now(timezone.utc), last_modified=None)
    # Fail on the final ledger insert, after all nodes were flushed.
    def fail_ledger(conn, cursor, statement, parameters, context, executemany):
        if statement.startswith('INSERT INTO ics_taxonomy_imports'):
            raise RuntimeError('Injected late import failure')
    event.listen(engine, 'before_cursor_execute', fail_ledger)
    try:
        with Session(engine) as session:
            with pytest.raises(RuntimeError, match='Injected late'):
                api.ingest_taxonomy(session, prepared, author_id=author_id)
            session.commit()
    finally:
        event.remove(engine, 'before_cursor_execute', fail_ledger)
    # A missing operator is also rejected at the storage boundary.
    with Session(engine) as session:
        with pytest.raises(Exception):
            api.ingest_taxonomy(session, prepared, author_id=uuid.uuid4())
        session.rollback()
    with Session(engine) as session:
        assert session.query(ClassificationScheme).filter_by(scheme_code='ISO-ICS-7').count() == 0
    with Session(engine) as session:
        result = api.ingest_taxonomy(session, prepared, author_id=author_id)
        session.commit()
    with Session(engine) as session:
        scheme = session.get(ClassificationScheme, result['scheme_id'])
        assert scheme.status == 'draft'
        nodes = session.scalars(select(ClassificationNode).where(ClassificationNode.scheme_id == scheme.id)).all()
        assert len(nodes) == 1381
        by_code = {n.node_code: n for n in nodes}
        assert by_code['13.220.20'].parent_node_id == by_code['13.220'].id
        assert by_code['01.080'].parent_node_id == by_code['01'].id
        from symgov_backend.classification_schemes import list_classification_nodes
        assert len(list_classification_nodes(session, scheme.id, top_level_only=True)) == 40
        assert by_code['13.220.20'] in list_classification_nodes(session, scheme.id, parent_node_id=by_code['13.220'].id)
        record = session.get(ICSTaxonomyImport, result['import_id'])
        assert record.content_sha256 == prepared['provenance']['sha256']
        assert record.edition == prepared['provenance']['edition']
        assert record.source_url == prepared['provenance']['source_url']
        assert record.attribution == prepared['provenance']['attribution']
        assert bytes(record.source_bytes) == content
        crosswalks = session.scalars(
            select(ICSDomainCrosswalk).where(ICSDomainCrosswalk.import_id == record.id)
        ).all()
        assert len(crosswalks) == len(prepared['crosswalk'])
        assert {row.review_status for row in crosswalks} == {'initial_broader', 'needs_review'}
        assert session.execute(text('SELECT count(*) FROM concept_classification_assignments')).scalar() == 0
        assert session.execute(text('SELECT count(*) FROM symbol_revision_classifications')).scalar() == 0
        second = api.ingest_taxonomy(session, prepared, author_id=author_id)
        assert second == result
        session.commit()
    with Session(engine) as session:
        assert session.execute(text('SELECT count(*) FROM ics_taxonomy_imports')).scalar() == 1
        assert session.execute(text('SELECT count(*) FROM ics_domain_crosswalks')).scalar() == len(prepared['crosswalk'])
        assert session.query(ClassificationNode).filter_by(scheme_id=result['scheme_id']).count() == 1381
        corrupted = dict(prepared, rows=prepared['rows'][:-1])
        with pytest.raises(ValueError):
            api.ingest_taxonomy(session, corrupted, author_id=author_id)
        session.commit()
    # An unattributed disposition is refused in storage, so the raw SQL that
    # was the only way to record one before 20260916_0059 now fails closed.
    with Session(engine) as session:
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(text("UPDATE ics_domain_crosswalks SET review_status='approved'"))
        session.rollback()

    # Human review may progress the crosswalk without breaking idempotency.
    with Session(engine) as session:
        for row in api.list_domain_crosswalks(session, import_id=result['import_id']):
            api.disposition_crosswalk(
                session, row.id, target_status='approved',
                occurred_at=datetime.now(timezone.utc), reviewed_by_user_id=author_id,
            )
        assert api.ingest_taxonomy(session, prepared, author_id=author_id) == result
        session.rollback()
    # Imported crosswalk facts are fail-closed, exactly like the hierarchy.
    for mutation in (
        "DELETE FROM ics_domain_crosswalks WHERE relation='broader'",
        "UPDATE ics_domain_crosswalks SET relation='candidate' WHERE relation='broader'",
        "UPDATE ics_domain_crosswalks SET reason='rewritten'",
    ):
        with Session(engine) as session:
            session.execute(text(mutation))
            with pytest.raises(ValueError, match='crosswalk'):
                api.ingest_taxonomy(session, prepared, author_id=author_id)
            session.rollback()
    with pytest.raises(Exception, match='immutable'):
        with engine.begin() as connection:
            connection.execute(text("UPDATE ics_taxonomy_imports SET dataset='changed'"))
    # Exercise the actual --apply adapter against this disposable DB.
    from symgov_backend import ics_import as cli
    monkeypatch.setenv('SYMGOV_DATABASE_URL', url)
    monkeypatch.setattr(cli, 'fetch_official', lambda: (content, datetime.now(timezone.utc), None))
    assert cli.main(['--apply', '--author-id', str(author_id), '--archive', str(tmp_path / 'applied')]) == 0
    output = capsys.readouterr().out
    assert url not in output
    assert '"node_count": 1381' in output
    # Downgrade must refuse to discard provenance or dotted identifiers.
    assert _alembic(url, 'downgrade', '20260911_0057', check=False).returncode != 0
    with engine.connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM ics_taxonomy_imports')).scalar() == 1

    from symgov_backend.classification_schemes import add_classification_node
    with Session(engine) as session:
        for code in ['A.B', '29..020', 'ABC._DEF']:
            with pytest.raises(ValueError):
                add_classification_node(session, scheme_id=result['scheme_id'], node_code=code, preferred_label='Invalid', added_at=datetime.now(timezone.utc))
            with pytest.raises(IntegrityError), session.begin_nested():
                session.execute(text("UPDATE classification_nodes SET node_code=:code WHERE scheme_id=:id AND node_code='01'"), {'code': code, 'id': result['scheme_id']})
        legacy = add_classification_node(session, scheme_id=result['scheme_id'], node_code='LEGACY_CODE', preferred_label='Legacy test', added_at=datetime.now(timezone.utc))
        session.flush()
        assert legacy.node_code == 'LEGACY_CODE'
        session.rollback()

    # Dotted identifiers remain unavailable to unrelated schemes at both the
    # service and direct-storage boundaries.
    with Session(engine) as session:
        unrelated = session.query(ClassificationScheme).filter_by(
            scheme_code='ENGINEERING-DISCIPLINE'
        ).one()
        with pytest.raises(ValueError):
            add_classification_node(
                session,
                scheme_id=unrelated.id,
                node_code='13.220',
                preferred_label='Not ICS',
                added_at=datetime.now(timezone.utc),
            )
        with pytest.raises(IntegrityError), session.begin_nested():
            session.execute(
                text(
                    "INSERT INTO classification_nodes "
                    "(id,scheme_id,node_code,preferred_label,sort_order,status,created_at,updated_at) "
                    "VALUES (:id,:scheme,'13.220','Not ICS',0,'draft',now(),now())"
                ),
                {'id': uuid.uuid4(), 'scheme': unrelated.id},
            )


def test_crosswalk_disposition_is_recorded_reviewable_and_reversible(
    classification_database, author_id, tmp_path, monkeypatch, capsys
):
    """The review loop end to end: queue, decide, correct, and read back.

    Before 20260916_0059 nothing in the product could move a crosswalk row to
    `approved` or `rejected`; the only writer was the importer's initial
    status. This exercises the service and the operator CLI that close that
    gap, against a real disposable PostgreSQL.
    """
    engine, url = classification_database
    _alembic(url, 'upgrade', '20260916_0059')
    content = (Path(api.__file__).parent / 'data/ICS.csv').read_bytes()
    prepared = api.prepare_import(content, retrieved_at=datetime.now(timezone.utc), last_modified=None)
    with Session(engine) as session:
        result = api.ingest_taxonomy(session, prepared, author_id=author_id)
        session.commit()

    # Every proposal starts undecided and unattributed.
    with Session(engine) as session:
        queue = api.list_domain_crosswalks(session, import_id=result['import_id'])
        assert len(queue) == len(prepared['crosswalk'])
        assert all(row.reviewed_by_user_id is None and row.reviewed_at is None for row in queue)
        assert api.list_domain_crosswalks(session, review_status='approved') == []
        pending = api.list_domain_crosswalks(session, review_status='needs_review')
        assert 0 < len(pending) < len(queue)

    # The queue is readable in the terms the decision is actually made in.
    with Session(engine) as session:
        described = api.describe_domain_crosswalks(session, review_status='needs_review')
        fire = [row for row in described if row['domain'] == 'Fire & Life Safety']
        assert {row['ics_code'] for row in fire} == {'13.220', '13'}
        assert any(row['ics_label'] == 'Protection against fire' for row in fire)
        assert all(row['reviewed_by_user_id'] is None for row in described)
        subject = uuid.UUID(fire[0]['crosswalk_id'])

    # A decision names its reviewer, its moment and its own rationale.
    decided_at = datetime.now(timezone.utc)
    with Session(engine) as session:
        row = api.disposition_crosswalk(
            session, subject, target_status='approved', occurred_at=decided_at,
            reviewed_by_user_id=author_id, review_note='Narrower target is the right browse entry.',
        )
        assert row.review_status == 'approved'
        session.commit()
    with Session(engine) as session:
        row = session.get(ICSDomainCrosswalk, subject)
        assert row.review_status == 'approved'
        assert row.reviewed_by_user_id == author_id
        assert row.reviewed_at == decided_at
        assert row.review_note == 'Narrower target is the right browse entry.'
        # A disposition is a browse decision, never a semantic assignment.
        assert session.execute(text('SELECT count(*) FROM concept_classification_assignments')).scalar() == 0
        assert session.execute(text('SELECT count(*) FROM symbol_revision_classifications')).scalar() == 0

    # A decided row may be corrected, but never returned to undecided.
    with Session(engine) as session:
        for illegal in ('needs_review', 'initial_broader'):
            with pytest.raises(ValueError, match='approved or rejected'):
                api.disposition_crosswalk(
                    session, subject, target_status=illegal,
                    occurred_at=datetime.now(timezone.utc), reviewed_by_user_id=author_id,
                )
        corrected = api.disposition_crosswalk(
            session, subject, target_status='rejected',
            occurred_at=datetime.now(timezone.utc), reviewed_by_user_id=author_id,
        )
        assert corrected.review_status == 'rejected'
        # The note belonged to the decision it explained.
        assert corrected.review_note is None
        with pytest.raises(ValueError, match='cannot move from rejected to rejected'):
            api.disposition_crosswalk(
                session, subject, target_status='rejected',
                occurred_at=datetime.now(timezone.utc), reviewed_by_user_id=author_id,
            )
        session.rollback()

    # An unknown row and an unknown reviewer both fail closed.
    with Session(engine) as session:
        with pytest.raises(LookupError, match='not found'):
            api.disposition_crosswalk(
                session, uuid.uuid4(), target_status='approved',
                occurred_at=datetime.now(timezone.utc), reviewed_by_user_id=author_id,
            )
    with Session(engine) as session:
        remaining = api.list_domain_crosswalks(session, review_status='needs_review')[0].id
        with pytest.raises(IntegrityError), session.begin_nested():
            api.disposition_crosswalk(
                session, remaining, target_status='approved',
                occurred_at=datetime.now(timezone.utc), reviewed_by_user_id=uuid.uuid4(),
            )
        session.rollback()

    # The operator CLI is the surface, and it never echoes the database URL.
    from symgov_backend import ics_review as cli
    monkeypatch.setenv('SYMGOV_DATABASE_URL', url)
    with Session(engine) as session:
        target = api.list_domain_crosswalks(session, review_status='needs_review')[0].id

    assert cli.main(['list', '--review-status', 'approved']) == 0
    listed = json.loads(capsys.readouterr().out)
    assert [row['crosswalk_id'] for row in listed] == [str(subject)]

    assert cli.main([
        'reject', '--crosswalk-id', str(target), '--reviewer-id', str(author_id),
        '--note', 'Drawing conventions belong elsewhere.',
    ]) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded['review_status'] == 'rejected'
    assert recorded['reviewed_by_user_id'] == str(author_id)
    assert recorded['review_note'] == 'Drawing conventions belong elsewhere.'
    assert url not in json.dumps(recorded)

    # An illegal transition is refused without recording anything.
    assert cli.main([
        'approve', '--crosswalk-id', str(uuid.uuid4()), '--reviewer-id', str(author_id),
    ]) == 1
    failure = capsys.readouterr()
    assert url not in failure.out + failure.err

    with Session(engine) as session:
        assert session.get(ICSDomainCrosswalk, target).review_status == 'rejected'

    # A re-import leaves every recorded decision exactly as the reviewer left it.
    with Session(engine) as session:
        assert api.ingest_taxonomy(session, prepared, author_id=author_id) == result
        session.commit()
    with Session(engine) as session:
        assert session.get(ICSDomainCrosswalk, subject).review_status == 'approved'
        assert session.get(ICSDomainCrosswalk, target).review_status == 'rejected'

    # Recorded attribution is not something a downgrade may quietly discard.
    assert _alembic(url, 'downgrade', '20260915_0058', check=False).returncode != 0
    with engine.connect() as connection:
        assert connection.execute(
            text("SELECT count(*) FROM ics_domain_crosswalks WHERE reviewed_by_user_id is not null")
        ).scalar() == 2
