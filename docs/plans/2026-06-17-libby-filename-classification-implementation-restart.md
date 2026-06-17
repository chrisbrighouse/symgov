# Libby filename classification improvement restart note

## Status
- Implemented a shared filename inference helper at `backend/symgov_backend/filename_inference.py`.
- Wired filename inference into upstream intake metadata in:
  - `backend/symgov_backend/services/external_submissions.py`
  - `scripts/run_scott_intake.py`
- Vendored the Libby worker into the repo at `scripts/run_libby_classification.py` and kept the live workspace copy at `/data/.openclaw/workspaces/libby/run_libby_classification.py` in sync so filename hints are first-class evidence for:
  - symbol name
  - discipline
  - aliases/search terms
  - description/classification-summary fallback when no better description exists
- Passed filename hints into the Gemini image prompt as advisory context.
- Updated review-name defaulting in `backend/symgov_backend/routes/workspace.py` so engineering compounds from filenames are preserved.
- Added/updated tests for filename inference, ZIP DXF/JPG paired cases, Libby filename-aware classification, prompt construction, and review symbol name defaults.
- Committed the repo-side change sets with messages `Improve filename-derived symbol classification` and `Vendor Libby worker into repo`, then pushed them to `origin/main`.
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
- `/data/symgov/scripts/run_libby_classification.py`
- `/data/.openclaw/workspaces/libby/run_libby_classification.py`
- `/data/symgov/backend/symgov_backend/routes/workspace.py`
- `/data/symgov/tests/test_filename_inference.py`
- `/data/symgov/tests/test_zip_phase2.py`
- `/data/symgov/tests/test_libby_symbol_vision.py`
- `/data/symgov/tests/test_review_symbol_name_defaults.py`

## Uncommitted state
- Repo-side filename-classification changes, including the vendored Libby worker, are committed and pushed on `main`.
- `git status --short --branch` in `/data/symgov` still shows unrelated pre-existing dirty paths:
  - `docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md`
  - `frontend/src/App.jsx`
  - `.hermes/plans/2026-06-17_081344-libby-filename-classification-plan.md`
  - `docs/ops/`
  - `tests/test_submission_ui_zip_acceptance.py`
  - `tests/test_workspace_split_items.py`
- The repo now vendors the Libby worker at:
  - `/data/symgov/scripts/run_libby_classification.py`
- The live runtime still executes the synchronized workspace copy at:
  - `/data/.openclaw/workspaces/libby/run_libby_classification.py`

## Next actions
1. If desired, run a live service-backed Scott → Vlad → Libby submission through the deployed stack, not just the local script smoke path.
2. Decide separately what to do with the unrelated dirty/untracked repo paths listed above.

## Restart prompt
Continue from `/data/symgov`. Read `docs/plans/2026-06-17-libby-filename-classification-implementation-restart.md`, run `git status --short --branch`, verify `scripts/run_libby_classification.py` still matches `/data/.openclaw/workspaces/libby/run_libby_classification.py`, then either run a deployed-stack Scott/Vlad/Libby smoke test or clean up the unrelated dirty/untracked repo paths.
