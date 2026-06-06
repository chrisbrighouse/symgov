# Symgov restart note — agent feedback events + queue state machine

Date: 2026-06-06

## Status

Completed and verified the next two architecture steps:

1. **Structured agent feedback events from human corrections**
   - Added observational `agent_feedback_events` schema/model/migration.
   - Added `symgov_backend.agent_feedback` helpers to build and persist feedback events without applying them to prompts/rules.
   - Wired human review symbol-property corrections to record Libby/Vlad observational feedback.
   - Wired duplicate exception decisions to record Rupert + Libby observational feedback for both duplicate confirmations and false-positive duplicate overrides.

2. **Explicit queue state machine and DB/runtime ambiguity suggestions**
   - Added explicit queue status groups in `agent_queue_reconciliation.py`.
   - Added Reggie-oriented control suggestion builder for DB/runtime mismatches.
   - Suggestions are observational only: they include detail/evidence/suggested remediation but do not auto-fix queue rows, runtime JSON, or control exception records.

## Verification performed

- Targeted tests:
  - `python -m unittest tests.test_agent_feedback_events tests.test_agent_queue_state_machine tests.test_duplicate_exception_workflow`
  - Result: `Ran 14 tests ... OK`
- Compile check:
  - `python -m compileall -q backend/symgov_backend tests/test_agent_feedback_events.py tests/test_agent_queue_state_machine.py`
  - Result: OK / no output
- Full current test suite:
  - `python -m unittest discover -s tests`
  - Result: `Ran 44 tests ... OK`
- Live DB migration:
  - First `alembic upgrade head` failed under `symgov_app` because that user lacks `CREATE` privilege on schema `public`.
  - Re-ran correctly with migration credentials via `SYMGOV_ALEMBIC_USE_MIGRATION_DB=1 alembic upgrade head`.
  - Verified `agent_feedback_events` exists with indexes.
- Live smoke check:
  - Flushed an `AgentFeedbackEvent` row in a transaction and rolled back successfully.
  - Restarted `symgov-hermes-api` with `docker restart symgov-hermes-api`.
  - Health checks passed:
    - internal `/api/v1/health`: OK
    - public `https://apps.chrisbrighouse.com/api/v1/health`: HTTP 200
- Queue reconciliation dry-run smoke:
  - `runtime_records_seen=310`
  - `db_active_rows_inspected=0`
  - `runtime_orphan_count=12`
  - `control_suggestion_count=12`
  - No auto-fixes applied.

## Current uncommitted state before commit

Expected files changed/added:

- `backend/alembic/versions/20260606_0015_agent_feedback_events.py`
- `backend/symgov_backend/agent_feedback.py`
- `backend/symgov_backend/agent_queue_reconciliation.py`
- `backend/symgov_backend/models/__init__.py`
- `backend/symgov_backend/models/schema.py`
- `backend/symgov_backend/routes/workspace.py`
- `tests/test_agent_feedback_events.py`
- `tests/test_agent_queue_state_machine.py`
- this restart note

## Next actions

Recommended next step after this commit:

1. Add a small Reggie/operator endpoint or UI surface for `control_suggestions` / queue reconciliation findings.
2. Optionally persist Reggie suggestions as `control_exceptions` only after deciding de-duplication behaviour so repeated dry-runs do not create noisy duplicate exceptions.
3. Later, add a separate operator-approved pathway that applies selected safe remediations; do not auto-apply from Reggie yet.

## Restart prompt

Continue Symgov architecture work from commit containing observational agent feedback events and explicit queue-state/Reggie suggestions. Start by checking `git status`, confirming the live API is healthy, then implement a Reggie/operator surface for queue reconciliation findings without auto-fixing queue rows.
