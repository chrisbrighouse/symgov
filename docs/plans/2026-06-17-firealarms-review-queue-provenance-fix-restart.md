# FireAlarms review queue provenance fix restart note

Timestamp: 2026-06-17T16:33:32Z

## Status

- Live API was restarted so the running process imports the current asset format/content-type normalization and review queue filtering code.
- Normal human review queue now excludes `provenance_review` cases; human-visible stages are `classification_review` and `raster_split_review` only.
- Tracy external worker was changed so ambiguous/unknown-rights provenance results no longer create `provenance_review` stop cards. Restricted/conflict/fail rights outcomes can still create provenance review recommendations.
- Ambiguous Tracy results still build a queued Libby handoff with rights status context and no review case/current stage, so classification can proceed.

## Files changed in this pass

- `/data/symgov/tests/test_duplicate_exception_workflow.py`
  - Updated the human-visible review stage expectation to assert `provenance_review` is not surfaced in the normal queue.
- `/data/symgov/tests/test_tracy_provenance_flow.py`
  - New regression coverage for package-code-only ambiguous rights and Libby handoff without a provenance review case.
- `/data/.openclaw/workspaces/tracy/run_tracy_provenance.py`
  - External Tracy runtime worker edit: provenance review recommendations are now limited to restricted/conflict/fail rights states, not unknown/ambiguous package-code-only cards.
- `/data/symgov/scripts/run_tracy_provenance.py`
  - Vendored the Tracy worker into the repo so the formerly external runtime change is now commit-tracked.
- `/data/symgov/backend/symgov_backend/agent_queue_worker.py`
  - Tracy queue processing now points at the repo-vendored runner path.

## Verification run

Commands run from `/data/symgov`:

```bash
PYTHONPATH=backend pytest tests/test_tracy_provenance_flow.py tests/test_duplicate_exception_workflow.py::DuplicateExceptionWorkflowTests::test_only_human_actionable_case_stages_are_visible_in_review_queue -q
# 3 passed in 1.69s

PYTHONPATH=backend pytest tests/test_symbol_asset_manifest.py tests/test_workspace_asset_preview.py -q
# 26 passed in 1.29s

docker restart symgov-hermes-api
# health became healthy

docker exec symgov-hermes-api python -c "import symgov_backend.routes.workspace as w, symgov_backend.asset_manifest as a; print('workspace', w.__file__); print('human_visible', sorted(w.HUMAN_VISIBLE_REVIEW_CASE_STAGES)); print('asset_manifest', a.__file__)"
# workspace /data/symgov/backend/symgov_backend/routes/workspace.py
# human_visible ['classification_review', 'raster_split_review']
# asset_manifest /data/symgov/backend/symgov_backend/asset_manifest.py

docker exec symgov-hermes-api python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://127.0.0.1:8010/api/v1/workspace/review-cases',timeout=10)); items=d['items']; stages=sorted({i.get('currentStage') or i.get('current_stage') for i in items}); print('review_cases_count', len(items)); print('stages', stages); print('has_provenance_review', 'provenance_review' in stages)"
# review_cases_count 86
# stages ['awaiting_decision', 'classification_review', 'duplicate_exception', 'returned_for_review']
# has_provenance_review False

docker exec symgov-hermes-api python -c "import urllib.request,json; d=json.load(urllib.request.urlopen('http://127.0.0.1:8010/api/v1/workspace/review-cases',timeout=10)); found=[]\nfor i in d['items']:\n    for a in i.get('sourceAssets') or []:\n        if a.get('format') in ('dxf','svg') or (a.get('filename') or '').lower().endswith(('.dxf','.svg')):\n            found.append((i.get('displayName'), i.get('currentStage'), a.get('filename'), a.get('format'), a.get('contentType'), a.get('previewable')))\nprint('asset_matches', len(found)); print(json.dumps(found[:10], indent=2))"
# asset_matches 2
# 0017 classification_review 01-Mech_Heating+Cooling_Boiler_Steam_Elec.dxf dxf application/dxf false
# 0017 classification_review 01-Mech_Heating+Cooling_Boiler_Steam_Elec.svg svg image/svg+xml true

docker exec symgov-hermes-api python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://127.0.0.1:8010/api/v1/health', timeout=10)))"
# {'ok': True, 'service': 'symgov-api', 'time': '2026-06-17T16:33:28Z'}
```

## Uncommitted state warning

The repo already had many uncommitted changes before this pass. `git status --short` still shows broad modified/untracked work across backend, frontend, scripts, docs, and tests. Do not assume all uncommitted changes are from this pass.

This pass specifically added/changed the files named above, plus this restart note.

## Next actions

1. If desired, run a live Tracy queue item from a FireAlarms/package-code-only case and confirm the persisted DB side has no new `provenance_review` case while Libby receives/consumes the handoff.
2. Tracy has now been vendored into the repo at `/data/symgov/scripts/run_tracy_provenance.py`; keep the runtime worker and the repo copy in sync if the legacy external path is still used directly anywhere.
3. Clean up or separately commit the larger pre-existing Symgov worktree changes before merging.

## Restart prompt

Continue the FireAlarms review queue fix from `/data/symgov`. Load the systematic debugging and TDD skills. Read `/data/symgov/docs/plans/2026-06-17-firealarms-review-queue-provenance-fix-restart.md`, inspect `git status --short`, and remember Tracy is now vendored at `/data/symgov/scripts/run_tracy_provenance.py` while the legacy external copy remains at `/data/.openclaw/workspaces/tracy/run_tracy_provenance.py`. Verify live `/api/v1/workspace/review-cases` still has no `provenance_review` stages before doing further queue-flow work.
