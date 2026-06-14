# DXF derivative persistence restart note — 2026-06-14

## Status

Completed the pending Task 2 persistence slice for DXF derivative previews.

Implemented/verified:

- Re-ran the existing helper test for `persist_dxf_derivative_assets(...)` and confirmed it passes.
- Added a regression test that exercises Vlad `process_queue_item(..., persist_db=True)` end-to-end with a fake persistence bridge.
- Wired `persist_dxf_derivative_assets(...)` into `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` before the validation report and artifact records are written/persisted.
- Reused a single `RuntimePersistenceBridge` instance for the DB-persistence path so derivative attachments and report persistence happen through the same bridge instance.
- Updated `validation_report["normalized_payload_json"]` and `output_artifact_record["payload_json"]` after DXF derivative annotation so runtime JSON and DB durable payloads include attachment/object-storage metadata.
- The DXF derivative manifest additional artifact shares the mutated manifest payload, so its runtime and DB artifact payloads include `attachment_id`, `attachment_object_key`, and `attachment_storage`.

Important external file caveat:

- `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` is outside the `/data/symgov` git worktree and is not shown by `git status` inside `/data/symgov`.

## Verification run

All commands were run from `/data/symgov` unless stated otherwise.

- `python3 -m pytest tests/test_dxf_phase1.py::test_vlad_persists_dxf_derivative_preview_to_storage -q`
  - Result: `1 passed in 0.67s`
- `python3 -m pytest tests/test_dxf_phase1.py::test_vlad_process_queue_persists_dxf_derivative_before_report_payload -q`
  - RED before code change: failed because the process path instantiated two persistence bridges and had not wired the DXF derivative upload into the pre-report payload path.
  - GREEN after code change: `1 passed in 0.63s`
- `python3 -m pytest tests/test_dxf_phase1.py -q`
  - Result: `6 passed in 0.58s`
- `python3 -m pytest tests/test_symbol_asset_manifest.py tests/test_published_symbol_feedback.py tests/test_workspace_asset_preview.py tests/test_dxf_phase1.py -q`
  - Result: `39 passed in 1.21s`
- `python3 -m pytest tests -q`
  - Result: `105 passed in 1.35s`
- `python3 -m py_compile /data/.openclaw/workspaces/vlad/run_vlad_validation.py`
  - Result: passed with no output.

## Files changed in this continuation

Inside `/data/symgov` git worktree:

- `tests/test_dxf_phase1.py`
  - Added `json` import.
  - Added `test_vlad_process_queue_persists_dxf_derivative_before_report_payload`.
- `docs/plans/2026-06-14-dxf-derivative-persistence-restart.md`
  - This restart note.

Outside `/data/symgov` git worktree:

- `/data/.openclaw/workspaces/vlad/run_vlad_validation.py`
  - Added `should_persist_db` and single bridge reuse.
  - Calls `persist_dxf_derivative_assets(...)` for `artifact["normalized_technical_metadata"].get("dxf_derivative")` before runtime/DB report persistence.
  - Refreshes `validation_report["normalized_payload_json"]` and `output_artifact_record["payload_json"]` after persistence annotation.

Pre-existing changed/untracked files from earlier work remain in the repo and were not fully reviewed in this continuation:

- `backend/requirements.txt`
- `backend/symgov_backend/routes/published.py`
- `backend/symgov_backend/routes/workspace.py`
- `backend/symgov_backend/services/external_submissions.py`
- `scripts/run_scott_intake.py`
- `tests/test_published_symbol_feedback.py`
- `backend/symgov_backend/asset_manifest.py`
- `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
- `docs/plans/2026-06-14-dxf-preview-and-multiformat-symbol-assets.md`
- `tests/test_symbol_asset_manifest.py`
- `tests/test_workspace_asset_preview.py`

## Next actions

1. Review the complete repo diff plus the external Vlad worker diff carefully before committing/deploying.
2. Decide whether `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` should be copied/synced into a tracked source location, because git inside `/data/symgov` will not protect or show that runtime-worker edit.
3. If deploying, run a live-ish Vlad queue item with real storage env against a staging/test report to confirm object storage and DB attachment rows are created as expected.
4. Commit the Symgov worktree changes and record how the external Vlad worker change is deployed/versioned.

## Restart prompt

Continue from `/data/symgov`. The DXF derivative persistence slice is implemented and tests are green (`105 passed`). First inspect `git status --short`, review `tests/test_dxf_phase1.py`, and separately review `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` around the persistence block. Remember that the Vlad worker file is outside the repo, so decide how to sync or track it before release.
