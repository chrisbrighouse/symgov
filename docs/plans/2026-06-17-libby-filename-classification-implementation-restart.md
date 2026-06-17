# Libby filename classification improvement restart note

## Status
- Implemented a shared filename inference helper at `backend/symgov_backend/filename_inference.py`.
- Wired filename inference into upstream intake metadata in:
  - `backend/symgov_backend/services/external_submissions.py`
  - `scripts/run_scott_intake.py`
- Updated the live Libby worker at `/data/.openclaw/workspaces/libby/run_libby_classification.py` so filename hints are first-class evidence for:
  - symbol name
  - discipline
  - aliases/search terms
  - description/classification-summary fallback when no better description exists
- Passed filename hints into the Gemini image prompt as advisory context.
- Updated review-name defaulting in `backend/symgov_backend/routes/workspace.py` so engineering compounds from filenames are preserved.
- Added/updated tests for filename inference, ZIP DXF/JPG paired cases, Libby filename-aware classification, prompt construction, and review symbol name defaults.
- Committed the repo-side change set with commit message `Improve filename-derived symbol classification`.
- Saved reusable Hermes skill `software-development/symgov-filename-classification`.
- Ran an end-to-end local smoke test from Scott ZIP intake into Libby classification using a paired `Elec_FireAlarm_BreakGlass.dxf` + `.jpg` package.

## Verification
- `pytest -q /data/symgov/tests/test_filename_inference.py /data/symgov/tests/test_zip_phase2.py /data/symgov/tests/test_libby_symbol_vision.py /data/symgov/tests/test_review_symbol_name_defaults.py`
  - Passed: 16/16.
- `pytest -q /data/symgov/tests/test_dxf_phase1.py`
  - Passed: 7/7.
- `pytest -q /data/symgov/tests`
  - Passed: 123/123.

## Files changed for this task
- `/data/symgov/backend/symgov_backend/filename_inference.py`
- `/data/symgov/backend/symgov_backend/services/external_submissions.py`
- `/data/symgov/scripts/run_scott_intake.py`
- `/data/.openclaw/workspaces/libby/run_libby_classification.py`
- `/data/symgov/backend/symgov_backend/routes/workspace.py`
- `/data/symgov/tests/test_filename_inference.py`
- `/data/symgov/tests/test_zip_phase2.py`
- `/data/symgov/tests/test_libby_symbol_vision.py`
- `/data/symgov/tests/test_review_symbol_name_defaults.py`

## Uncommitted state
- Repo-side filename-classification changes are committed under the current local `main` HEAD with message `Improve filename-derived symbol classification`.
- `git status --short --branch` in `/data/symgov` still shows unrelated pre-existing dirty paths:
  - `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
  - `frontend/src/App.jsx`
  - `.hermes/plans/2026-06-17_081344-libby-filename-classification-plan.md`
  - `docs/ops/`
  - `tests/test_submission_ui_zip_acceptance.py`
  - `tests/test_workspace_split_items.py`
- The live Libby worker change is outside the repo and cannot be included in the `/data/symgov` git commit:
  - `/data/.openclaw/workspaces/libby/run_libby_classification.py`

## Next actions
1. Push the current local `main` HEAD if you want the repo-side change published upstream.
2. Capture or sync `/data/.openclaw/workspaces/libby/run_libby_classification.py` into its canonical source if you want that live-worker change versioned.
3. If desired, run a live service-backed Scott → Vlad → Libby submission through the deployed stack, not just the local script smoke path.

## Restart prompt
Continue from `/data/symgov`. Read `docs/plans/2026-06-17-libby-filename-classification-implementation-restart.md`, run `git status --short --branch`, inspect `/data/.openclaw/workspaces/libby/run_libby_classification.py` if you need to sync the external worker, then either push the repo commit or run a deployed-stack Scott/Vlad/Libby smoke test.
