# Tracy rights review screen improvements — 2026-06-21

## Status
- Implemented and deployed the requested rights review screen changes:
  - `frontend/src/App.jsx` now renders corrected rights status as a select/picklist instead of free text.
  - `frontend/src/App.jsx` now renders corrected rights disposition as a select/picklist instead of free text.
  - `frontend/src/App.jsx` also renders corrected processing outcome as a select/picklist using the database-safe values `pass`, `review_required`, and `failed`; this was necessary because the previous free-text placeholder suggested invalid values such as `continue`, which the live DB rejected.
  - `backend/symgov_backend/routes/workspace.py` now closes rights review cases after any rights decision transition is recorded, so submitted items leave the open rights queue once actioned.
  - `backend/symgov_backend/routes/workspace.py` normalizes legacy/stale UI values (`rights_cleared` -> `cleared`, `continue` -> `pass`, etc.) before persistence to avoid database check-constraint failures.
- Added regression coverage:
  - `tests/test_workspace_rights_review_lane.py::test_rights_screen_uses_picklists_for_corrected_status_and_disposition`
  - `tests/test_workspace_rights_review_api.py::test_rights_review_decision_closes_review_case_so_it_leaves_rights_queue`
  - `tests/test_workspace_rights_review_api.py::test_rights_review_decision_normalizes_legacy_ui_values_to_database_safe_values`

## Verification performed
- RED check before implementation:
  - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_lane.py::test_rights_screen_uses_picklists_for_corrected_status_and_disposition tests/test_workspace_rights_review_api.py::test_rights_review_decision_closes_review_case_so_it_leaves_rights_queue -q`
  - Result: failed as expected: picklist constants/selects were absent and rights decisions did not close the review case.
- Follow-up RED after live-log discovery of DB constraint failure:
  - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_api.py::test_rights_review_decision_normalizes_legacy_ui_values_to_database_safe_values -q`
  - Result: failed as expected before backend normalization; `rights_cleared` remained unnormalized.
- Targeted green checks:
  - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_lane.py::test_rights_screen_uses_picklists_for_corrected_status_and_disposition tests/test_workspace_rights_review_api.py::test_rights_review_decision_normalizes_legacy_ui_values_to_database_safe_values -q`
  - Result: `2 passed in 1.12s`
- Rights review related suite:
  - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_lane.py tests/test_workspace_rights_review_api.py tests/test_daisy_rights_review_coordination.py -q`
  - Result: `17 passed in 1.07s`
- Full Python suite:
  - `PYTHONPATH=backend pytest -q`
  - Result: `175 passed in 2.70s`
- Frontend production build and publish:
  - `npm run build:publish`
  - Result: Vite build succeeded; published bundle `../dist/assets/index-Cs5LO0IB.js` to `/data/symgov` and `/data/.openclaw/workspace/symgov`.
- Backend restart and health:
  - `docker restart symgov-hermes-api`
  - `docker exec symgov-hermes-api curl -sS http://127.0.0.1:8010/api/v1/health`
  - Result: `{"ok":true,"service":"symgov-api","time":"2026-06-21T16:43:42Z"}`
- Live queue inspection after restart:
  - `GET /api/v1/workspace/rights-review-cases` inside `symgov-hermes-api`
  - Result: one open rights review case remains: `11d7203f-cba6-4cc2-adc2-23a1a5464015`, display `0085-61`, status/disposition/outcome `restricted/restricted/failed`.

## Uncommitted state
- Modified:
  - `backend/symgov_backend/routes/workspace.py`
  - `frontend/src/App.jsx`
  - `tests/test_workspace_rights_review_api.py`
  - `tests/test_workspace_rights_review_lane.py`
  - published static files under the workspace root may also be updated by `npm run build:publish` (`index.html`, `assets/`, `submit/index.html`) depending on git tracking.
- Added:
  - `docs/restart-notes/2026-06-21-tracy-rights-review-screen-improvements.md`

## Next actions
- Manually verify in the browser against the current rights queue item: choose `Clear rights`, choose corrected status `Cleared`, corrected disposition `Cleared`, corrected processing outcome `Pass`, submit, and confirm the item disappears from the rights queue.
- Do not manually mutate the live rights item from the agent unless explicitly asked; the reviewer should make the rights decision.

## Restart prompt
Continue Tracy rights review screen improvements from `/data/symgov`. Review `docs/restart-notes/2026-06-21-tracy-rights-review-screen-improvements.md`, run `git diff`, and verify the live rights queue item `11d7203f-cba6-4cc2-adc2-23a1a5464015` can now be actioned through the browser without DB check-constraint errors.
