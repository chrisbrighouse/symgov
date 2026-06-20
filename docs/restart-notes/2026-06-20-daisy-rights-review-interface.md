# Daisy Rights review interface restart note

Status
- Implemented a dedicated Daisy-coordinated Rights review UI path for Tracy rights escalations.
- Added a first-class top-nav `Rights` button and `/rights` route.
- Rights lane cards in the Workspace Activity Monitor now open `/rights?review=<case-id>` instead of using the generic Reviews rights filter.
- Added a dedicated `RightsReviewPage` with:
  - left Rights queue;
  - centre source/Tracy/Daisy evidence panel;
  - right Rights Decision panel.
- Rights reviewers can correct the problem fields directly on the Rights review screen:
  - corrected rights status;
  - corrected rights disposition;
  - corrected processing outcome;
  - licence/permission label;
  - source URL/evidence link;
  - rights evidence note.
- Added rights-specific action options:
  - `clear_rights` / Clear rights;
  - `restrict_publication` / Restrict publication;
  - `request_rights_evidence` / Request rights evidence;
  - `mark_conflict` / Mark conflict;
  - `defer_rights` / Defer rights.
- Added backend rights review API support:
  - `GET /api/v1/workspace/rights-review-cases`;
  - `POST /api/v1/workspace/rights-review-cases/{review_case_id}/decisions`.
- Added explicit rights evidence/rights decision schemas and Tracy provenance evidence extraction.
- Rights decisions are persisted through `HumanReviewDecision`, `ReviewCaseAction`, and `AuditEvent(action='rights_review_decision_recorded')`.
- Corrected rights fields are now applied to the canonical `ProvenanceAssessment` fields and reviewer correction metadata is stored in `ProvenanceAssessment.evidence_json`, so downstream code and subsequent Rights API responses can see the human correction rather than only Tracy's original problematic values.

Files changed for this feature
- `backend/symgov_backend/routes/workspace.py`
- `backend/symgov_backend/schemas.py`
- `frontend/src/App.jsx`
- `frontend/src/api.js`
- `frontend/src/styles.css`
- `tests/test_workspace_rights_review_lane.py`
- `tests/test_workspace_rights_review_api.py`
- `.hermes/plans/2026-06-20_131100-daisy-rights-review-interface.md`
- `docs/restart-notes/2026-06-20-daisy-rights-review-interface.md`

Verification performed at 2026-06-20T13:28:01Z
```bash
cd /data/symgov
PYTHONPATH=backend pytest tests/test_workspace_rights_review_lane.py tests/test_workspace_rights_review_api.py -q
python3 -m py_compile backend/symgov_backend/routes/workspace.py backend/symgov_backend/schemas.py
PYTHONPATH=backend pytest tests/test_tracy_provenance_flow.py tests/test_workspace_rights_review_lane.py tests/test_daisy_rights_review_coordination.py tests/test_workspace_rights_review_api.py tests/test_duplicate_exception_workflow.py::DuplicateExceptionWorkflowTests::test_only_human_actionable_case_stages_are_visible_in_review_queue tests/test_workspace_asset_preview.py::test_provenance_review_case_uses_latest_dxf_validation_derivative_preview -q
npm run build
```

Results
- Rights-focused pytest subset: `11 passed` before adding the successful correction-persistence test.
- Rights API tests after correction-persistence test: `4 passed`.
- Python compile check passed with no output.
- Focused integration/regression pytest subset: `22 passed in 1.63s`.
- Vite build succeeded and produced `index-CwSyI7K4.js`.
- Static scan of added diff lines for hardcoded secrets, shell injection, eval/exec, pickle, and obvious SQL string formatting returned no findings.
- Independent code review was run. Important issue found: corrected rights fields were initially only in decision payloads. This was fixed by updating `ProvenanceAssessment` and adding a regression test.

Current uncommitted state
- This note was written while the repo also still contained pre-existing Tracy cleanup changes:
  - `M scripts/run_tracy_provenance.py`
  - `M tests/test_tracy_provenance_flow.py`
  - `?? docs/restart-notes/2026-06-20-tracy-escalation-reason-cleanup.md`
- New/current Rights review interface changes are in the files listed above.
- `git diff --stat` does not include untracked files; remember to inspect `git status --short` before committing.

Next actions
1. If deploying, run:
```bash
cd /data/symgov
./scripts/publish-static.sh
cd /docker/symgov-hermes
docker compose build symgov-api
docker compose up -d --no-deps --force-recreate symgov-api
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
curl -fsS https://apps.chrisbrighouse.com/api/v1/health
curl -fsS https://apps.chrisbrighouse.com/api/v1/workspace/rights-review-cases
```
2. Browser-check `https://apps.chrisbrighouse.com/#/workspace` after static publish: click a Rights lane card and confirm it opens `#/rights?review=<case-id>`.
3. Browser-check `https://apps.chrisbrighouse.com/#/rights`: confirm the Rights queue, evidence panel, corrective fields, and submit controls render correctly.
4. Use a disposable/test case before submitting a live rights decision because the endpoint now updates the canonical `ProvenanceAssessment` fields.
5. Decide whether to add a later dedicated `rights_review_overrides` table if reporting needs immutable before/after rights corrections outside decision/audit payloads.

Restart prompt
"Continue Symgov development from the Daisy Rights review interface implementation. The code adds `/rights`, Rights top-nav, Workspace Rights-card routing, backend `/workspace/rights-review-cases` list/decision APIs, rights evidence schemas, and reviewer-correctable rights fields persisted back to `ProvenanceAssessment`. Verify current `git status --short`, keep the pre-existing Tracy cleanup changes separate if needed, then deploy with `npm run build`, `./scripts/publish-static.sh`, and symgov-api rebuild/recreate if ready."
