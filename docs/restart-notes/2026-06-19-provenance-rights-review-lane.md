# Provenance/Rights Review Lane Restart Notes

## Status

Implemented the first pass of a separate Provenance/Rights Review queue lane coordinated by Daisy.

Key behavior now in place:

- Tracy restricted/conflict/fail rights findings produce a review recommendation for `provenance_rights_review`.
- When Tracy persists a provenance assessment and creates a review case, it also creates a Daisy queue item for the rights review work.
- That Daisy queue item carries:
  - `source_type: provenance_rights_review`
  - `payload_json.review_queue_family: rights_review`
  - `payload_json.review_queue_label: Provenance/Rights Review`
  - Tracy rights status, risk level, summary, defects, recommended actions, source context, and review case ID.
- Workspace queue API maps Daisy queue items with `payload_json.review_queue_family == "rights_review"` to `queueFamily: rights_review` while leaving ordinary Daisy items in `review_coordination`.
- Workspace monitor now has a separate `Rights` / `Provenance/Rights Review` lane between Daisy Coordination and Human Review.
- Frontend routing checks `queueFamily === 'rights_review'` before Daisy `agentId`, so Daisy-coordinated rights cards appear in the rights lane, not the generic Daisy lane.

## Files changed for this feature

- `scripts/run_tracy_provenance.py`
- `backend/symgov_backend/routes/workspace.py`
- `frontend/src/App.jsx`
- `tests/test_tracy_provenance_flow.py`
- `tests/test_workspace_rights_review_lane.py`

Note: The working tree also still contains earlier manual submission Source/source_notes changes in:

- `backend/symgov_backend/schemas.py`
- `backend/symgov_backend/services/external_submissions.py`
- `frontend/src/api.js`
- `tests/test_dxf_phase1.py`
- `tests/test_submission_ui_zip_acceptance.py`

## Verification performed

Passed:

```bash
pytest tests/test_tracy_provenance_flow.py tests/test_workspace_rights_review_lane.py
```

Result: 5 passed.

Passed:

```bash
pytest tests/test_duplicate_exception_workflow.py::DuplicateExceptionWorkflowTests::test_only_human_actionable_case_stages_are_visible_in_review_queue tests/test_workspace_asset_preview.py::test_provenance_review_case_uses_latest_dxf_validation_derivative_preview
```

Result: 2 passed.

Passed:

```bash
python3 -m py_compile scripts/run_tracy_provenance.py backend/symgov_backend/routes/workspace.py
```

Result: no output / exit 0.

Passed:

```bash
npm run build
```

Result: Vite build succeeded.

One attempted pytest node used the wrong unittest class name and failed with “not found”; it was rerun with the correct node above and passed.

## Current uncommitted state

As of this note, `git status --short` showed modified/untracked files from both the earlier manual Source field work and this rights review lane work:

```text
M backend/symgov_backend/routes/workspace.py
M backend/symgov_backend/schemas.py
M backend/symgov_backend/services/external_submissions.py
M frontend/src/App.jsx
M frontend/src/api.js
M scripts/run_tracy_provenance.py
M tests/test_dxf_phase1.py
M tests/test_submission_ui_zip_acceptance.py
M tests/test_tracy_provenance_flow.py
?? tests/test_workspace_rights_review_lane.py
?? docs/restart-notes/2026-06-19-provenance-rights-review-lane.md
```

## Next actions

1. Decide whether `provenance_rights_review` should be added to any blocking publication policy set. It is currently kept separate from the normal Human Review visible stages.
2. Build the dedicated rights review UI, accessible from:
   - clicking cards in the rights review lane;
   - a separate UI button.
3. Add backend endpoint(s) for rights review case details/decisions if the existing review-case endpoint is not enough.
4. DONE in continuation below: Daisy now actively transforms a generic coordination item into a rights review item during Daisy processing.
5. Consider adding richer display metadata/tool summary for rights review cards, e.g. Tracy defect codes, source URL/notes, risk level, and rights status.

## 2026-06-19 continuation: stricter Tracy -> Daisy -> Rights Review handoff

Implemented the stricter workflow requested after the first pass:

- Tracy no longer creates the visible Rights lane card directly.
- Tracy creates a Daisy-owned coordination card first:
  - `source_type: provenance_rights_coordination`
  - `payload_json.review_queue_family: review_coordination`
  - `payload_json.coordination_step: daisy_rights_review_coordination`
  - `payload_json.target_review_queue_family: rights_review`
  - `payload_json.target_review_queue_label: Provenance/Rights Review`
- Daisy processing now emits the Daisy-owned visible Rights lane card after coordination:
  - `source_type: provenance_rights_review`
  - `payload_json.review_queue_family: rights_review`
  - `payload_json.review_queue_label: Provenance/Rights Review`
  - `payload_json.coordination_source_queue_item_id` traces back to the Daisy coordination card.
- The Daisy runtime runner was updated at `/data/.openclaw/workspaces/daisy/run_daisy_coordination.py` (external/live worker path; not repo-tracked) to build that downstream rights review card and leave it queued.
- `backend/symgov_backend/agent_queue_worker.py` now mirrors Daisy-created downstream queue cards into the DB when processing Daisy with a DB env file.
- `backend/symgov_backend/routes/workspace.py` now centralizes queue-family override logic in `queue_family_for_agent_queue_item(...)`: Daisy cards only move to the Rights lane when the emitted card has `review_queue_family == "rights_review"`; the upstream coordination card remains in Daisy Coordination.

Additional files changed for this continuation:

- `backend/symgov_backend/agent_queue_worker.py`
- external live worker: `/data/.openclaw/workspaces/daisy/run_daisy_coordination.py`
- `tests/test_daisy_rights_review_coordination.py`

Additional verification performed:

```bash
python3 -m py_compile scripts/run_tracy_provenance.py backend/symgov_backend/routes/workspace.py backend/symgov_backend/agent_queue_worker.py /data/.openclaw/workspaces/daisy/run_daisy_coordination.py
pytest tests/test_tracy_provenance_flow.py tests/test_workspace_rights_review_lane.py tests/test_daisy_rights_review_coordination.py tests/test_duplicate_exception_workflow.py::DuplicateExceptionWorkflowTests::test_only_human_actionable_case_stages_are_visible_in_review_queue tests/test_workspace_asset_preview.py::test_provenance_review_case_uses_latest_dxf_validation_derivative_preview -q
npm run build
cd /docker/symgov-hermes && docker compose build symgov-api
cd /docker/symgov-hermes && docker compose up -d --no-deps --force-recreate symgov-api
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
curl -fsS https://apps.chrisbrighouse.com/api/v1/health
```

Results:

- Python compile checks passed.
- Pytest subset: `10 passed in 1.01s`.
- Vite build succeeded.
- API image rebuilt and `symgov-hermes-api` recreated.
- Local and public health checks returned `{"ok":true,"service":"symgov-api",...}`.

Current caveat:

- The Daisy runner change lives in the external worker path and is not represented in repo `git diff`. If/when Daisy is repo-managed, copy this behavior into the repo-managed runner before relying on a clean repo alone as deployment evidence.

## 2026-06-19 continuation: Rupert queue moved to second monitor screen

Moved Rupert/Publication off the first Activity Monitors screen and onto the second queue screen immediately to the left of Hannah:

- `WORKSPACE_MONITOR_SCREENS.pipeline` is now Scott, Vlad, Tracy, Libby, Daisy, Rights, Review.
- `WORKSPACE_MONITOR_SCREENS.intelligence` now starts with `publication`, followed by Hannah/Curation, Reggie, Whitney, and Ed.
- Added regression coverage in `tests/test_workspace_rights_review_lane.py` to pin Rupert left of Hannah on the second screen.

Verification performed:

```bash
pytest tests/test_workspace_rights_review_lane.py -q
npm run build
./scripts/publish-static.sh
```

Results:

- Workspace queue layout test: `4 passed`.
- Vite build succeeded and produced `index-BYLl7alz.js`.
- Static publish succeeded to `/data/symgov` and `/data/.openclaw/workspace/symgov`.
- Live bundle marker check confirmed:
  - `pipeline:["intake","validation","provenance","classification","review_coordination","rights_review","human_review"]`
  - `intelligence:["publication","curation","control_audit","market_intelligence","ux_feedback"]`
- Browser verification on `https://apps.chrisbrighouse.com/#/workspace`: clicking the next queue-screen chevron shows Rupert as the first column and Hannah as the second.

## 2026-06-19 continuation: dedicated Rights review UI entry path

Implemented the first dedicated Provenance/Rights Review UI path:

- `provenance_rights_review` is now included in the review-case stages returned by `/api/v1/workspace/review-cases`.
- Rights review cases built from Tracy provenance assessments now use a rights-specific title (`Review provenance/rights for ...`) and owner (`Rights reviewer`).
- Activity Monitor cards now preserve `reviewCaseId`, `queueFamily`, and `columnId` for live queue cards.
- Rights lane cards are clickable and route to `/reviews?review=<case-id>&queue=rights`.
- The Reviews page now has a dedicated `Rights` queue filter button between `New` and `Returned`.
- Rights cases are shown in the Rights lane, while normal classification/split/returned reviews remain in Human Review.
- Added/updated regression coverage in:
  - `tests/test_workspace_rights_review_lane.py`
  - `tests/test_duplicate_exception_workflow.py`

Verification performed at 2026-06-19 10:03Z:

```bash
python3 -m py_compile backend/symgov_backend/routes/workspace.py
pytest tests/test_workspace_rights_review_lane.py tests/test_tracy_provenance_flow.py tests/test_daisy_rights_review_coordination.py tests/test_duplicate_exception_workflow.py::DuplicateExceptionWorkflowTests::test_only_human_actionable_case_stages_are_visible_in_review_queue tests/test_workspace_asset_preview.py::test_provenance_review_case_uses_latest_dxf_validation_derivative_preview -q
npm run build
./scripts/publish-static.sh
cd /docker/symgov-hermes && docker compose build symgov-api
cd /docker/symgov-hermes && docker compose up -d --no-deps --force-recreate symgov-api
```

Results:

- Python compile check passed.
- Focused pytest subset: `13 passed in 1.34s`.
- Vite build succeeded and produced `index-1I9WjFuz.js`.
- Static publish succeeded to `/data/symgov` and `/data/.openclaw/workspace/symgov`.
- API image rebuilt and `symgov-hermes-api` recreated successfully.
- Local health check returned `{"ok":true,"service":"symgov-api",...}`.
- Public health check returned `{"ok":true,"service":"symgov-api",...}`.
- Public `/api/v1/workspace/review-cases` returned HTTP 200.
- Public bundle marker check confirmed `queue=rights`, `provenance_rights_review`, and `Rights` in `./assets/index-1I9WjFuz.js`.

Current caveats:

- The Daisy runner change still lives in external live worker path `/data/.openclaw/workspaces/daisy/run_daisy_coordination.py` and is not represented in repo `git diff`.
- The dedicated Rights page is currently a Reviews-page filter, not a fully separate route/component; this satisfies lane-card click-through and a visible Rights button, but a richer rights-specific detail layout can still be added next.
- The working tree remains uncommitted and still includes earlier manual Source/source_notes changes plus the provenance-rights lane changes.

## 2026-06-19 continuation: Tracy historical unknown provenance residue cleanup

Cleaned the old Tracy ambiguous/unknown provenance residue that was no longer actionable by the worker. This was an operational inspection cleanup only: Tracy direct processing only picks up `queued` runtime files, and there were no queued Tracy runtime files before cleanup.

Backup created before mutation:

- `/data/symgov/docs/restart-notes/backups/tracy-unknown-provenance-cleanup-20260619T100944Z/`

Backup contents:

- `tracy_escalated_unknown_agent_queue_items.jsonl`: 95 DB Tracy queue rows backed up.
- `open_provenance_review_cases.jsonl`: 195 open legacy `provenance_review` review cases backed up.
- `tracy_runtime_escalated_files_manifest.jsonl`: manifest for 219 archived Tracy runtime files.
- Per-file copies of the 219 moved runtime queue JSON files.

Runtime cleanup:

- Moved 219 non-queued Tracy runtime files out of `/data/.openclaw/workspaces/tracy/runtime/agent_queue_items/`.
- Archive location: `/data/.openclaw/workspaces/tracy/runtime/archived_agent_queue_items/unknown-provenance-cleanup-20260619T100944Z/`.
- Verification after cleanup: Tracy runtime `agent_queue_items` had 0 files remaining.

Database cleanup:

- Superseded 95 Tracy `agent_queue_items` where:
  - agent was `tracy`;
  - status was `escalated`;
  - linked `provenance_assessments.rights_status` was `unknown`.
- Closed/superseded 195 open legacy `review_cases` where:
  - `source_entity_type='provenance_assessment'`;
  - `current_stage='provenance_review'`;
  - `closed_at IS NULL`.
- New review-case stage used for historical closure: `superseded_unknown_provenance`.
- Audit events written:
  - `tracy_unknown_provenance_superseded`: 95.
  - `unknown_provenance_review_case_superseded`: 195.

Verification after cleanup:

```bash
docker exec symgov-postgres psql -U symgov_app -d symgov -c "SELECT aq.status, aq.source_type, count(*) FROM agent_queue_items aq JOIN agent_definitions ad ON ad.id=aq.agent_id WHERE ad.slug='tracy' GROUP BY aq.status, aq.source_type ORDER BY aq.status, aq.source_type;"
```

Result:

- Tracy now has 95 `superseded` historical DB rows and no `queued` or `escalated` Tracy DB rows.

```bash
python3 - <<'PY'
from pathlib import Path
import json
root=Path('/data/.openclaw/workspaces/tracy/runtime/agent_queue_items')
counts={}
if root.exists():
  for p in root.glob('*.json'):
    data=json.loads(p.read_text())
    counts[str(data.get('status') or 'missing')]=counts.get(str(data.get('status') or 'missing'),0)+1
print(counts)
PY
```

Result: `{}` / 0 files remaining.

Open provenance-assessment review cases after cleanup:

- `changes_requested`: 1.
- `classification_review`: 4.
- `libby_deletion_review`: 10.
- `libby_duplicate_review`: 2.
- `provenance_review`: 0.

Health verification:

- Container API health returned `{"ok":true,"service":"symgov-api",...}`.
- Public API health returned `{"ok":true,"service":"symgov-api",...}`.

## Restart prompt

Continue Symgov Provenance/Rights Review lane work. Start by reading `docs/restart-notes/2026-06-19-provenance-rights-review-lane.md`, then inspect `scripts/run_tracy_provenance.py`, `backend/symgov_backend/routes/workspace.py`, `backend/symgov_backend/agent_queue_worker.py`, `frontend/src/App.jsx`, and external live worker `/data/.openclaw/workspaces/daisy/run_daisy_coordination.py`. The stricter Tracy -> Daisy coordination -> Daisy Rights lane emission workflow is implemented and live; the first dedicated UI path is live via Rights lane card click-through and the Reviews page `Rights` filter button. Historical Tracy `unknown` provenance residue has been backed up, superseded in DB, and archived out of the active runtime queue directory. Next focus is a richer rights-specific detail/decision layout and any publication-blocking policy decision for `provenance_rights_review`. Preserve Daisy as the coordinator for review activity routing.
