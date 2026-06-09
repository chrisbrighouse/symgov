# Symgov restart note — Reggie queue-control operator surface

Date: 2026-06-07
Updated: 2026-06-07T17:12:22Z

## Status

Completed the next recommended Reggie step: queue reconciliation/control suggestions are now exposed to operators, still observational-only.

## What changed

1. **Backend API surface**
   - Added `GET /api/v1/workspace/reggie/queue-controls` (plus legacy route alias) in `backend/symgov_backend/routes/workspace.py`.
   - Endpoint calls `reconcile_agent_queue_state(..., apply=False, active_only=True)` and never applies remediations or writes `control_exceptions`.
   - Added response shaping helper `_build_reggie_queue_control_response(...)` so snake_case reconciliation output becomes camelCase UI/API payload.
   - Added Pydantic response models in `backend/symgov_backend/schemas.py`:
     - `WorkspaceReggieQueueControlSuggestionResponse`
     - `WorkspaceReggieQueueControlListResponse`

2. **Frontend/operator UI surface**
   - Added `fetchReggieQueueControls()` in `frontend/src/api.js`.
   - Workspace auto-refresh now loads Reggie queue-control suggestions alongside queue items, review cases, and Daisy reports.
   - Reggie suggestions appear in the existing Reggie / Audit-Control monitor column with:
     - severity label,
     - rule code,
     - detail,
     - suggested remediation,
     - searchable evidence text.
   - The Workspace refresh summary now includes Reggie's queue-control status.

3. **Tests**
   - Extended `tests/test_agent_queue_state_machine.py` to verify the API response shape remains observational-only and camelCase.

## Verification performed

- Live API was healthy before changes:
  - internal `/api/v1/health`: `{"ok":true,"service":"symgov-api","time":"2026-06-07T17:06:48Z"}`
  - public health: HTTP 200

- Targeted Reggie/queue tests in live container:
  - Command: `docker exec symgov-hermes-api sh -lc 'cd /data/symgov && python -m unittest tests.test_agent_queue_state_machine'`
  - Result: `Ran 5 tests ... OK`

- Targeted regression set + compile check:
  - Command: `docker exec symgov-hermes-api sh -lc 'cd /data/symgov && python -m unittest tests.test_agent_queue_state_machine tests.test_agent_feedback_events tests.test_duplicate_exception_workflow && python -m compileall -q backend/symgov_backend tests/test_agent_queue_state_machine.py'`
  - Result: `Ran 15 tests ... OK`; compileall produced no output.

- Full current backend test suite:
  - Command: `docker exec symgov-hermes-api sh -lc 'cd /data/symgov && python -m unittest discover -s tests'`
  - Result: `Ran 47 tests ... OK`

- Frontend build:
  - Command: `npm run build`
  - Result: Vite built successfully.
  - Live bundle now references Reggie markers:
    - `Reggie found=yes`
    - `queue-controls=yes`
    - `Observational only=yes`

- Live API restart and smoke checks:
  - Restarted: `docker restart symgov-hermes-api`
  - Internal health after restart: `{"ok":true,"service":"symgov-api","time":"2026-06-07T17:11:44Z"}`
  - Public health: `public_health=200`
  - Reggie endpoint smoke result:
    - `dryRun=true`
    - `activeOnly=true`
    - `runtimeRecordsSeen=326`
    - `dbActiveRowsInspected=0`
    - `missingRuntimeCount=0`
    - `runtimeOrphanCount=12`
    - `controlSuggestionCount=12`
    - First item showed `observationalOnly=true` and rule `agent_queue_runtime_without_db_mirror`.

## Current uncommitted state

`git status --short` after this work includes:

```text
M backend/symgov_backend/routes/workspace.py
M backend/symgov_backend/schemas.py
M frontend/src/App.jsx
M frontend/src/api.js
M scripts/run_hannah_curation.py
M tests/test_agent_queue_state_machine.py
M tests/test_hannah_quality_filters.py
?? docs/plans/2026-06-07-hannah-feedback-and-sourcing.md
?? docs/plans/2026-06-07-reggie-queue-control-surface-restart.md
```

Notes:
- The Reggie work changed:
  - `backend/symgov_backend/routes/workspace.py`
  - `backend/symgov_backend/schemas.py`
  - `frontend/src/App.jsx`
  - `frontend/src/api.js`
  - `tests/test_agent_queue_state_machine.py`
  - this restart note.
- Hannah sourcing/feedback files were already modified in the working tree before this Reggie step and were left intact:
  - `scripts/run_hannah_curation.py`
  - `tests/test_hannah_quality_filters.py`
  - `docs/plans/2026-06-07-hannah-feedback-and-sourcing.md`

## Operational interpretation

Reggie is now visible to an operator as a control-room monitor. The live system currently reports 12 runtime queue records without DB mirrors. These are suggestions only; no queue row, runtime JSON, or control exception was changed by the endpoint.

## Recommended next actions

1. Decide de-duplication semantics for persisted Reggie findings, then optionally persist queue-control suggestions as `control_exceptions` without creating repeated noisy duplicates on every refresh.
2. Add a separate operator-approved remediation path for selected safe actions (for example archive stale runtime orphan JSON or reconcile a verified terminal runtime status to DB). Keep this separate from the observational endpoint.
3. Consider a Reggie detail drawer/table for evidence fields if the card UI becomes too compact for operators.
4. Commit the combined working tree once Chris is happy with both the Hannah and Reggie changes.

## Restart prompt

Continue Symgov Reggie control work from the working tree containing `GET /workspace/reggie/queue-controls` and the Workspace Reggie monitor cards. Start by checking `git status`, reviewing this note, and smoke-checking `https://apps.chrisbrighouse.com/api/v1/workspace/reggie/queue-controls`. Next, design persisted `control_exceptions` de-duplication and an operator-approved remediation path without auto-applying fixes from Reggie.
