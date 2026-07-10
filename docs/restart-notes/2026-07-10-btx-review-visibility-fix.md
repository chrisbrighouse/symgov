# BTX review-visibility fix — restart note

## Status

Bluebeam `.btx` submissions now follow the supported BTX flow documented in `docs/btx-submission-workflow.md`:

- submission accepts and preserves the original BTX;
- Scott routes accepted BTX to Vlad and Tracy;
- Vlad converts every supported symbol to SVG, DXF, and PNG;
- successful conversion escalates into the existing `raster_split_review` child-review stage rather than ending as a non-reviewable `pass`;
- Vlad creates a standard `derivative_manifest` child per symbol, using its PNG as the browser preview;
- generated assets use the canonical `visual_assets.derivatives` key, not the ignored `derived_assets` key;
- with `--persist-db` and a storage env, the generated assets are attached/uploaded before the durable validation report is persisted.

## Root cause

The converter itself generated valid SVG, DXF, and PNG derivatives for `Doors.btx`, but the handoff stopped short of the review contract:

1. successful BTX validation returned `pass` with no `review_recommendation`, so no validation review case was opened; using a novel `btx_library_review` stage would also hide it because the Workspace exposes the established `raster_split_review` child-review stage;
2. BTX assets were placed under `derived_assets`, while Symgov's asset-manifest and preview selection contract recognizes `derivatives`;
3. no standard `derivative_manifest.children` existed, so the workspace could not materialize `review_split_items` for each BTX symbol.

The output format is SVG, DXF, and PNG. The review UI displays the persisted PNG derivative; SVG and DXF are retained as vector/CAD derived assets.

## Files changed in this BTX slice

- `backend/symgov_backend/services/btx_converter.py` (existing converter, added in the prior uncommitted BTX implementation)
- `backend/symgov_backend/services/external_submissions.py`
- `backend/symgov_backend/agent_queue_worker.py`
- `scripts/run_scott_intake.py`
- `scripts/run_vlad_validation.py`
- `frontend/src/App.jsx`
- `backend/requirements.txt`
- `tests/test_btx_integration.py`
- `tests/test_submission_ui_zip_acceptance.py`
- `docs/btx-submission-workflow.md`
- `README.md`
- `backend/README.md`

## Verification

Run from `/data/symgov`:

```bash
PYTHONPATH=backend pytest -q \
  tests/test_btx_integration.py \
  tests/test_submission_ui_zip_acceptance.py \
  tests/test_workspace_asset_preview.py \
  tests/test_workspace_split_items.py
```

Latest verification:

- `PYTHONPATH=backend pytest -q tests/test_btx_integration.py tests/test_submission_ui_zip_acceptance.py tests/test_workspace_asset_preview.py tests/test_workspace_split_items.py tests/test_published_symbol_review_workflow.py` → `24 passed`.
- Direct conversion of the handoff fixture yielded `5` symbols, `0` failures, and `15` generated derivative files: SVG, DXF, and PNG for each symbol.
- `npm run build` in `frontend/` → passed.
- `python3 -m py_compile` for the converter, submission service, queue bridge, Scott runner, and Vlad runner → passed; `git diff --check` → clean.

A complete `PYTHONPATH=backend pytest -q` run reached `278 passed` but ended with three existing order/environment-sensitive failures in LLM route mocks and the catalog preview test (it tried to resolve Docker-only `symgov-minio`). Re-running those exact five tests in isolation passed. This is not attributed to the BTX change, but a clean full-suite run remains a release gate.

## Working tree

This repository already contained an uncommitted BTX implementation when this review began. Do not discard unrelated uncommitted changes. Inspect `git status --short` and `git diff` before staging. In particular, retain the existing converter and test additions while reviewing the new review-manifest changes.

## Next actions

1. Run the full backend test suite and `npm run build` after the final docs edits.
2. Perform a controlled production-style `Doors.btx` queue run with `--persist-db` and `--storage-env-file`; verify five uploaded PNG keys and five visible review children through the workspace preview endpoint/browser.
3. Deploy/restart the API worker only after the clean verification run, then check health and a cache-busted live review page.
4. Commit the BTX implementation and this documentation together once the working tree has been reviewed.

## July 10 continuation — duplicate outputs and persistence diagnostics

### Current implementation status

This uncommitted continuation adds two BTX durability repairs without deploying or touching live queue/review records:

- `btx_converter.convert_btx` now uses a deterministic per-stem counter (`_2`, `_3`, …) for duplicate subjects, preserving each symbol ordinal while preventing SVG/DXF/PNG filename and object-key collisions.
- Vlad records `btx_conversion_trace` in the normalized technical metadata: queue ID, source SHA-256, source filename/object key, BTX title/version, total/success/failure counts, output directory, and each converted symbol’s ordinal, subject, internal name, filename/hash/size/object-key asset entries. `btx_conversion` evidence events explicitly record success or failure.
- BTX review children now retain the preview size, SHA-256, path, and content type required by the existing persistence bridge.
- Vlad’s persistence boundary now records correlation ID, queue/report IDs, expected vs actual derivative/child/Libby counts, and phase events for persistence start, attachment creation, upload, validation-report persistence, Libby queue persistence, review-case creation, and terminal durable outcome. A failure raises an attributable error containing the failing phase and correlations.

### Confirmed runtime/DB split cause

The pre-repair BTX path wrote the runtime queue status (`escalated`) before attempting persistence. Its `derivative_manifest.children` omitted `size_bytes`, `sha256`, `path`, and `content_type`; the persistence loop then raised `KeyError: 'size_bytes'` while creating the first PNG attachment. This stopped subsequent DB/report/artifact persistence, leaving the runtime and PostgreSQL queue states split. The new regression test proves this boundary reports an upload-phase failure with correlation/count diagnostics; the missing child persistence fields are now supplied.

### Fresh verification

Run from `/data/symgov` after the final code edit:

```bash
PYTHONPATH=backend pytest -q \
  tests/test_btx_integration.py \
  tests/test_submission_ui_zip_acceptance.py \
  tests/test_workspace_asset_preview.py \
  tests/test_workspace_split_items.py \
  tests/test_agent_queue_state_machine.py \
  tests/test_filename_inference.py \
  tests/test_libby_symbol_vision.py
cd frontend && npm run build
python3 -m py_compile scripts/run_vlad_validation.py backend/symgov_backend/services/btx_converter.py
git diff --check
```

Results before this restart-note edit:

- focused BTX suite: `12 passed in 1.17s`;
- broader BTX/workspace/worker/classification suite: `44 passed in 1.55s`;
- `npm run build`: passed (`vite build`, 46 modules);
- Python compilation and `git diff --check`: passed.

These results must be rerun after this documentation edit before handoff.

### Working-tree and recovery constraints

The working tree contains substantial pre-existing uncommitted BTX, worker, taxonomy, review-UI, and documentation work. Nothing has been staged or committed. Do not discard or overwrite unrelated changes.

No deployment, restart, requeue, live queue/review mutation, history deletion, or recovery has occurred. Package `0088` remains unrecovered. Before any approved normal-path reprocess, deploy/restart the verified worker code, prove the active import path and worker health, then verify persisted report/artifact/attachment/review-child/Libby rows and PNG previews for the newly processed record. Do not manually manufacture review children.

### Next actions

1. Re-run the verification commands above after this note edit; run the full backend suite if release/deployment is requested.
2. Independently review the uncommitted Vlad/converter/test changes before staging or committing.
3. Only with explicit authorization: use the approved live-change procedure, prove imports and worker health, then perform an auditable normal-path reprocess rather than mutating `0088` in place.
4. Verify DB/runtime state agreement, report/manifest persistence, all three stored derivatives per successfully converted symbol, review child counts, and each PNG preview endpoint before declaring live recovery complete.

## Copyable restart prompt

Continue the Symgov BTX submission/review work from `/data/symgov`. First read `docs/btx-submission-workflow.md` and `docs/restart-notes/2026-07-10-btx-review-visibility-fix.md`, then inspect `git status --short` and the full uncommitted diff without discarding prior BTX work. The converter correctly produces SVG, DXF, and PNG from `Doors.btx`; the review fix escalates successful libraries into the established `raster_split_review` child-review stage and materializes PNG-backed `derivative_manifest` children. Run the focused BTX tests, then the full suite and `npm run build`. Before production claims, execute a controlled persisted `Doors.btx` queue job and prove five derivative uploads plus five Workspace review child previews. Keep original BTX provenance, do not call SVG “SVF,” do not widen BTX compatibility claims beyond the verified Version 1 stamp-snapshot structure, and do not publish automatically.
