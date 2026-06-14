# DXF intake/review fix restart notes — 2026-06-14

Updated: 2026-06-14T16:45:28Z

## Status

Completed this continuation of the DXF intake/review fix.

Implemented/verified in the `/data/symgov` git worktree:

- Vlad worker logic is now present in the git tree at `scripts/run_vlad_validation.py`.
- `backend/symgov_backend/agent_queue_worker.py` points Vlad at `/data/symgov/scripts/run_vlad_validation.py` instead of the old `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` runtime copy.
- `scripts/run_vlad_validation.py` derives `BACKEND_ROOT` from the repository path (`Path(__file__).resolve().parents[1] / "backend"`) so the tracked worker imports the repo backend.
- Workspace review preview handling resolves preview assets through the shared asset manifest logic and can use the latest `ValidationReport` for provenance review cases, fixing DXF single-file review previews after Vlad produces a derivative SVG.
- External submission handling groups files with the same stem, e.g. `pump.dxf` + `pump.jpg`, as one symbol intake with `visual_assets.preview`, `visual_assets.source_assets`, and grouped `attachment_ids`.
- The fragile `tests/test_dxf_phase1.py` module cleanup no longer deletes `fastapi`, `pydantic`, or `pydantic_core` from `sys.modules`; it only clears `symgov_backend*`, avoiding the `pydantic_core._pydantic_core` collection break while still forcing repo-backend imports after legacy worker imports.

Important synchronization note:

- `/data/symgov/scripts/run_vlad_validation.py` and `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` are not byte-identical by design.
- The only observed diff is `BACKEND_ROOT`: the tracked git-tree worker uses the repo-relative backend path, while the legacy external copy still points at `/data/.openclaw/workspace/symgov/backend`.
- Production Vlad worker routing now points at the tracked git-tree worker path, so the old external copy is not the configured production Vlad runner.

## Verification run

All commands were run from `/data/symgov` unless stated otherwise.

- `python3 -m pytest tests/test_dxf_phase1.py -q`
  - Result: `7 passed in 0.96s`
- `python3 -m pytest tests/test_workspace_asset_preview.py -q`
  - Result: `5 passed in 0.98s`
- `python3 -m pytest tests/test_symbol_asset_manifest.py tests/test_published_symbol_feedback.py tests/test_workspace_asset_preview.py tests/test_dxf_phase1.py -q`
  - Result: `41 passed in 1.50s`
- `python3 -m pytest tests -q`
  - Result: `107 passed in 2.00s`
- Production container restart/health:
  - `docker restart symgov-hermes-api`
  - Docker health reached `healthy` on poll 4.
  - In-container health endpoint returned: `{"ok":true,"service":"symgov-api","time":"2026-06-14T16:44:50Z"}`
- Production runtime module verification after restart:
  - `symgov_backend.routes.workspace` loaded from `/data/symgov/backend/symgov_backend/routes/workspace.py`, sha256 `b015eabe1177365cffebcc50933b30c3e832dab0975efff1a805e8f0e6c3d806`.
  - `symgov_backend.services.external_submissions` loaded from `/data/symgov/backend/symgov_backend/services/external_submissions.py`, sha256 `f6c6acc46d4a7d4cf2f6d07c2260a77afc3b573dfb62bb65173b3536c0c49767`.
  - `symgov_backend.agent_queue_worker` loaded from `/data/symgov/backend/symgov_backend/agent_queue_worker.py`, sha256 `6d11027c0f3be07f3a1cc7757400f6cf86494bd6b3bc3f7cbf22d1f883031b55`.
  - Runtime `AGENT_SPECS["vlad"]["runner_path"]` is `/data/symgov/scripts/run_vlad_validation.py`.
  - Production container has `/data/symgov/scripts/run_vlad_validation.py`, sha256 `55efadc5312e077be858f255eb9bbe61033dee725743a4bef772a997d8d26f3d`, and `python /data/symgov/scripts/run_vlad_validation.py --help` exits 0.

## Production deployment state

Production is now using the new code after the `symgov-hermes-api` container restart.

Evidence:

- The running container bind-mounts `/docker/openclaw-hz0t/data` to `/data`, and the API working directory is `/data/symgov/backend`.
- After restart, imports resolve to `/data/symgov/backend/...`, not stale `/app/...` copies.
- The runtime checksums inside the container match the host worktree checksums for the changed backend files.
- The in-container module has the new preview helpers (`latest_validation_report_for_intake`, `choose_workspace_source_preview_asset`) and the Vlad worker spec points at the tracked git-tree runner.
- API health is green after restart.

## Git/worktree state

Current uncommitted state includes changes from this and earlier related DXF/published-review work. `git status --short --branch` showed:

```text
## main...origin/main
 M backend/requirements.txt
 M backend/symgov_backend/agent_queue_worker.py
 M backend/symgov_backend/routes/published.py
 M backend/symgov_backend/routes/workspace.py
 M backend/symgov_backend/services/external_submissions.py
 M scripts/run_scott_intake.py
 M tests/test_published_symbol_feedback.py
?? backend/symgov_backend/asset_manifest.py
?? docs/plans/2026-06-13-dxf-zip-symbol-intake-implementation-plan.md
?? docs/plans/2026-06-14-dxf-derivative-persistence-restart.md
?? docs/plans/2026-06-14-dxf-preview-and-multiformat-symbol-assets.md
?? docs/plans/2026-06-14-dxf-intake-review-fix-restart.md
?? scripts/run_vlad_validation.py
?? tests/test_dxf_phase1.py
?? tests/test_symbol_asset_manifest.py
?? tests/test_workspace_asset_preview.py
```

## Remaining actions

1. Review the full diff before commit, including the earlier published-symbol feedback changes that are in the same worktree.
2. Commit/push the tracked Vlad worker and related backend/tests/docs when ready.
3. Optionally prune or clearly label the old `/data/.openclaw/workspaces/vlad/run_vlad_validation.py` runtime copy to avoid future confusion; do not repoint production back to it.

## Restart prompt

Continue from `/data/symgov`. The DXF intake/review fixes are implemented, tests are green (`107 passed`), and production `symgov-hermes-api` was restarted and verified healthy using `/data/symgov` modules. First inspect `git status --short --branch` and review the complete uncommitted diff. Key files are `scripts/run_vlad_validation.py`, `backend/symgov_backend/agent_queue_worker.py`, `backend/symgov_backend/routes/workspace.py`, `backend/symgov_backend/services/external_submissions.py`, `backend/symgov_backend/asset_manifest.py`, `tests/test_dxf_phase1.py`, and `tests/test_workspace_asset_preview.py`.
