# Daisy Rights Review Interface Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a dedicated Daisy-coordinated Rights review interface for Tracy escalations, reachable from a top-level `Rights` button and from Rights queue cards, with workflows suited to reviewing/updating item rights rather than normal symbol/content review.

**Architecture:** Keep Tracy/Daisy queue orchestration already implemented, but split the UI and API contract so Rights reviews are first-class records with rights-specific evidence, fields, and decisions. The existing `/reviews?queue=rights` filter is a useful bridge, but the target should be a separate `/rights` route/component backed by the same `provenance_rights_review` review cases plus richer `ProvenanceAssessment.report_json` metadata. Rights decisions should persist as `HumanReviewDecision`/`ReviewCaseAction` records and route follow-up to Rupert, Libby, Tracy, or Scott according to rights outcome.

**Tech Stack:** FastAPI + SQLAlchemy backend in `/data/symgov/backend/symgov_backend`, React/Vite frontend in `/data/symgov/frontend/src`, pytest regression tests in `/data/symgov/tests`, current Daisy worker in `/data/.openclaw/workspaces/daisy/run_daisy_coordination.py`.

---

## Current context

Read before implementation:

- `/data/symgov/docs/restart-notes/2026-06-20-tracy-escalation-reason-cleanup.md`
- `/data/symgov/docs/restart-notes/2026-06-19-provenance-rights-review-lane.md`
- `/data/symgov/frontend/src/App.jsx`
- `/data/symgov/frontend/src/api.js`
- `/data/symgov/backend/symgov_backend/routes/workspace.py`
- `/data/symgov/backend/symgov_backend/schemas.py`
- `/data/symgov/tests/test_workspace_rights_review_lane.py`
- `/data/symgov/tests/test_daisy_rights_review_coordination.py`

Facts established by inspection:

- Tracy now only creates blocking escalation reasons for `restricted`, `conflict`, and `failed`; `unknown_warning` is non-blocking and should not enter this Rights review workflow.
- Tracy creates Daisy coordination work; Daisy emits visible Rights queue cards with `payload_json.review_queue_family == "rights_review"` and `source_type == "provenance_rights_review"`.
- Workspace Activity Monitor already has a Rights lane between Daisy and Human Review.
- Rights cards currently route to `/reviews?review=<case-id>&queue=rights`.
- Reviews page currently has a `Rights` segmented filter inside the general review UI.
- Backend already includes `provenance_rights_review` in `HUMAN_VISIBLE_REVIEW_CASE_STAGES` and exposes `rightsStatus`, `rightsDisposition`, `processingOutcome`, source assets, Daisy reports, and symbol properties on `WorkspaceReviewCaseResponse`.
- `DECISION_TRANSITIONS` is currently generic human-review logic. A rights-specific workflow should not blindly treat rights approval as equivalent to normal symbol approval unless the rights reviewer explicitly clears publication.
- Working tree at plan time has uncommitted Tracy cleanup files only:
  - `M scripts/run_tracy_provenance.py`
  - `M tests/test_tracy_provenance_flow.py`
  - `?? docs/restart-notes/2026-06-20-tracy-escalation-reason-cleanup.md`

## Product requirements

1. Top-level navigation has separate `Rights` button, visually parallel to `Reviews`.
2. `/rights` opens a dedicated Rights review experience, not just a Reviews page filter.
3. Clicking a Rights lane card opens the selected case in the Rights screen, e.g. `/rights?review=<case-id>`.
4. The Rights queue is Daisy-coordinated and only shows `currentStage === 'provenance_rights_review'` cases, plus any Daisy report-only fallback that explicitly targets rights review.
5. The Rights review screen supports a reviewer deciding the rights state of the item, not editing ordinary symbol content as the primary job.
6. UI must expose Tracy evidence clearly: rights status, rights disposition, processing outcome, risk level, defects, summary, recommended actions, source context, source URL/notes if present, and source file/preview.
7. Reviewer can update rights fields and record evidence/notes.
8. Reviewer decision options should be rights-specific:
   - `clear_rights`: rights are acceptable; proceed to existing downstream classification/publication path.
   - `restrict_publication`: do not publish or block publication until rights are resolved.
   - `request_rights_evidence`: route back for more source/licence evidence.
   - `mark_conflict`: keep blocked and escalate/coordinate a conflict resolution.
   - `defer_rights`: leave open but record why.
9. All decisions must be auditable via `HumanReviewDecision`, `ReviewCaseAction`, and `AuditEvent`.
10. The existing general Reviews workflow must continue to work unchanged for classification, split, duplicate, and returned published-symbol reviews.

---

## Proposed approach

### UI shape

Create a new `RightsReviewPage` rather than expanding `ReviewsPage` further.

Layout:

- Left: `Rights Queue`
  - Cards show human-readable ID (`displaySymbolId`), Tracy status (`restricted`/`conflict`/`failed`), risk, source file/domain, Daisy owner/reviewer, age/due date.
  - Previous/next navigation.
  - Search by ID, source file, source URL/domain, rights status, summary.
- Centre: `Rights Evidence`
  - Source preview using existing `ReviewSourceVisual` or a rights-specific wrapper.
  - Source file/assets and submission context.
  - Tracy evidence panel with status/disposition/outcome/risk and report defects.
  - Daisy coordination panel with assignment proposals and evidence requests.
- Right: `Rights Decision`
  - Editable rights status/disposition fields.
  - Licence/source fields where available: source URL, source notes, licence label, evidence URL, reviewer confidence.
  - Rights-specific action buttons.
  - Required decision note for blocking or evidence-request outcomes.
  - Submit button writes a rights decision and refreshes the queue.

### Backend shape

Add a rights-specific response and decision endpoint while reusing review case storage:

- `GET /api/v1/workspace/rights-review-cases`
  - returns only `provenance_rights_review` cases.
  - can internally reuse `build_provenance_workspace_item(...)` but should enrich response with Tracy report/evidence fields.
- `POST /api/v1/workspace/rights-review-cases/{review_case_id}/decisions`
  - validates only rights-specific decision codes.
  - persists `HumanReviewDecision` with `decider_role='rights_reviewer'` by default.
  - writes `ReviewCaseAction` with rights-specific `action_code`, target agent, and target stage.
  - writes `AuditEvent(action='rights_review_decision_recorded')`.

Potential transitions:

- `clear_rights`
  - `to_stage`: `rights_cleared`
  - `action_code`: `rights_clearance_recorded`
  - `target_agent_slug`: `libby` if classification is still pending; otherwise `rupert` only if item is otherwise publish-ready.
  - Keep this conservative: do not route directly to Rupert unless existing publication prerequisites are proven.
- `restrict_publication`
  - `to_stage`: `rights_restricted`
  - `action_code`: `publication_blocked_by_rights`
  - `target_agent_slug`: `libby` or `tracy` depending whether resolution is content metadata vs provenance re-check.
- `request_rights_evidence`
  - `to_stage`: `waiting_for_rights_evidence`
  - `action_code`: `request_rights_evidence`
  - `target_agent_slug`: `scott` or `tracy`; choose Scott if source discovery/download is needed, Tracy if assessment rerun is enough.
- `mark_conflict`
  - `to_stage`: `rights_conflict`
  - `action_code`: `escalate_rights_conflict`
  - `target_agent_slug`: `daisy` for coordination or `tracy` for reassessment. Daisy is preferred because user asked for Daisy to control this review flow.
- `defer_rights`
  - `to_stage`: `rights_deferred`
  - `action_code`: `defer_rights_review`
  - `target_agent_slug`: `daisy`

### Data contract additions

Add to schemas rather than overloading generic review fields:

- `WorkspaceRightsEvidenceResponse`
  - `provenanceAssessmentId: str`
  - `tracyQueueItemId: str | None`
  - `rightsStatus: str`
  - `rightsDisposition: str`
  - `processingOutcome: str`
  - `riskLevel: str`
  - `confidence: float | None`
  - `summary: str`
  - `defects: list[dict]`
  - `recommendedActions: list[str]`
  - `sourceContext: dict`
  - `evidence: dict`
  - `report: dict`
- `WorkspaceRightsReviewCaseResponse`
  - Either extends/copies `WorkspaceReviewCaseResponse` fields plus `rightsEvidence`, or wraps `{ reviewCase, rightsEvidence, daisyReports }`.
- `WorkspaceRightsReviewDecisionRequest`
  - `decisionCode: Literal[...]`
  - `rightsStatus: str | None`
  - `rightsDisposition: str | None`
  - `licenseLabel: str | None`
  - `sourceUrl: str | None`
  - `evidenceNote: str`
  - `deciderName: str = 'Human'`
  - `deciderRole: str = 'rights_reviewer'`
- `WorkspaceRightsReviewDecisionResponse`
  - `reviewCaseId`, `decision`, `actions`, `updatedRights`.

Do not add a database migration unless there is a clear need to store mutable rights fields outside decision payloads. First pass can persist reviewer updates in `HumanReviewDecision.decision_payload_json` and audit payloads; later work can add a dedicated `rights_review_overrides` table if reporting requires queryable columns.

---

## Step-by-step implementation plan

### Task 1: Pin current behavior with tests

**Objective:** Ensure existing Rights lane and current Reviews filter behavior are protected before adding a new page.

**Files:**
- Modify: `/data/symgov/tests/test_workspace_rights_review_lane.py`

**Steps:**
1. Add assertions that current Rights lane card routing is still present before changing it:
   - Current marker: `&queue=rights` in `App.jsx`.
2. Add a new pending/xfail-style target test or plain source assertion for future route markers:
   - `NavLink to="/rights"`
   - `Route path="/rights" element={<RightsReviewPage />}`
   - `item.queueFamily === 'rights_review'` routing to `/rights?review=`.
3. Run:
   - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_lane.py -q`
4. Expected now:
   - Existing tests pass.
   - New target assertions fail until UI is implemented. Commit after GREEN, not while failing.

### Task 2: Add backend rights evidence schemas

**Objective:** Define explicit API objects for Rights review evidence and decisions.

**Files:**
- Modify: `/data/symgov/backend/symgov_backend/schemas.py`
- Test: `/data/symgov/tests/test_workspace_rights_review_lane.py` or new `/data/symgov/tests/test_workspace_rights_review_api.py`

**Steps:**
1. Add Pydantic models listed above.
2. Keep fields optional where existing Tracy report payloads may lack details.
3. Add test source assertions or endpoint tests validating schema class names exist.
4. Run:
   - `python3 -m py_compile backend/symgov_backend/schemas.py`

### Task 3: Build rights evidence extraction helpers

**Objective:** Convert `ProvenanceAssessment` into a stable UI payload.

**Files:**
- Modify: `/data/symgov/backend/symgov_backend/routes/workspace.py`
- Test: `/data/symgov/tests/test_workspace_rights_review_api.py`

**Implementation notes:**

Add helpers near `build_provenance_notes(...)`:

- `build_rights_evidence_payload(provenance_assessment: ProvenanceAssessment) -> WorkspaceRightsEvidenceResponse`
- Extract defects from `(report_json or {}).get('defects')`.
- Extract recommended actions from either `report_json.recommended_actions`, `report_json.recommendedActions`, or payload fields Daisy already emits.
- Preserve raw `evidence_json` and `report_json` in response so the UI can display fallback facts while the contract matures.

**Verification:**
- Unit test with fake `ProvenanceAssessment` object or DB fixture if available.
- Run:
  - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_api.py -q`

### Task 4: Add `GET /workspace/rights-review-cases`

**Objective:** Provide a backend list endpoint dedicated to Rights reviews.

**Files:**
- Modify: `/data/symgov/backend/symgov_backend/routes/workspace.py`
- Modify: `/data/symgov/backend/symgov_backend/schemas.py`
- Test: `/data/symgov/tests/test_workspace_rights_review_api.py`

**Steps:**
1. Query open `ReviewCase` rows with `current_stage == 'provenance_rights_review'` and `closed_at IS NULL`.
2. Reuse `build_provenance_workspace_item(...)` for base review details.
3. Attach `rightsEvidence` from Task 3.
4. Attach latest Daisy reports if cheap; otherwise the frontend can continue fetching `fetchWorkspaceDaisyReports()`.
5. Add legacy route alias only if existing API mounting patterns require it.
6. Run:
   - `python3 -m py_compile backend/symgov_backend/routes/workspace.py backend/symgov_backend/schemas.py`
   - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_api.py tests/test_workspace_rights_review_lane.py -q`

### Task 5: Add rights-specific decision endpoint

**Objective:** Persist rights decisions without abusing generic SME review actions.

**Files:**
- Modify: `/data/symgov/backend/symgov_backend/routes/workspace.py`
- Modify: `/data/symgov/backend/symgov_backend/schemas.py`
- Test: `/data/symgov/tests/test_workspace_rights_review_api.py`

**Steps:**
1. Define `RIGHTS_DECISION_TRANSITIONS` separately from `DECISION_TRANSITIONS`.
2. Implement `create_workspace_rights_review_decision(...)` at `POST /workspace/rights-review-cases/{review_case_id}/decisions`.
3. Reject non-`provenance_rights_review` cases with 422.
4. Require non-empty `evidenceNote` for `restrict_publication`, `request_rights_evidence`, `mark_conflict`, and `defer_rights`.
5. Persist `HumanReviewDecision` with:
   - `decision_code` rights-specific value.
   - `decider_role` default `rights_reviewer`.
   - `from_stage='provenance_rights_review'`.
   - `to_stage` from `RIGHTS_DECISION_TRANSITIONS`.
   - `decision_payload_json` containing updated rights fields and evidence note.
6. Persist a `ReviewCaseAction` using the rights transition.
7. Persist `AuditEvent(action='rights_review_decision_recorded')`.
8. For the first pass, do not automatically mutate `ProvenanceAssessment.rights_status` unless there is a deliberate need; keep reviewer changes in decision payload to avoid rewriting Tracy’s assessment record.
9. Run endpoint tests.

### Task 6: Add frontend API helpers

**Objective:** Give React a clean Rights review data layer.

**Files:**
- Modify: `/data/symgov/frontend/src/api.js`

**Add:**
- `fetchWorkspaceRightsReviewCases()`
- `submitWorkspaceRightsReviewDecision(reviewCaseId, payload)`

**Reuse patterns:**
- `parseJson(...)`
- `workspaceUrl(...)`
- validation fallback handling from existing POST/PATCH helpers where useful.

**Verification:**
- `npm run build`

### Task 7: Add top-level Rights route and button

**Objective:** Make Rights a first-class navigation area.

**Files:**
- Modify: `/data/symgov/frontend/src/App.jsx`
- Modify: `/data/symgov/tests/test_workspace_rights_review_lane.py`

**Steps:**
1. Import/use new API helpers.
2. Add route:
   - `<Route path="/rights" element={<RightsReviewPage />} />`
3. Add nav button after Reviews:
   - `<NavLink to="/rights" ...>Rights</NavLink>`
4. Keep `/reviews?queue=rights` backward-compatible initially, but prefer `/rights` for new links.
5. Update `openQueueItem`/queue-card click logic so Rights lane cards route to `/rights?review=<case-id>`.
6. Run source assertion tests:
   - `PYTHONPATH=backend pytest tests/test_workspace_rights_review_lane.py -q`

### Task 8: Implement `RightsReviewPage`

**Objective:** Build the dedicated screen.

**Files:**
- Modify: `/data/symgov/frontend/src/App.jsx`
- Modify: `/data/symgov/frontend/src/styles.css`

**Implementation notes:**

Prefer extracting small components rather than growing `ReviewsPage` further:

- `RightsReviewPage`
- `RightsReviewQueueCard`
- `RightsEvidencePanel`
- `RightsDecisionPanel`
- `RightsStatusBadge`

State needed:

- `query`
- `activeId`
- `rightsState` from `fetchWorkspaceRightsReviewCases()`
- `daisyState` from `fetchWorkspaceDaisyReports()` or included response
- `decisionState` per active case:
  - `decisionCode`
  - `rightsStatus`
  - `rightsDisposition`
  - `licenseLabel`
  - `sourceUrl`
  - `evidenceNote`
  - `deciderName`
  - `deciderRole`

Reuse:

- `SectionHeading`
- `Fact`
- `ReviewSourceVisual` if it does not make the page feel classification-oriented.
- `DaisyReportCard` for Daisy context.
- `displaySymbolId(...)` and source preview helpers.

Avoid:

- Normal SME action labels (`Approve`, `Reject`, `Rename/Classify`, `Duplicate`, `Delete`) as the primary Rights actions.
- Displaying child-symbol split controls on Rights cases.

### Task 9: Add rights-specific CSS

**Objective:** Visually distinguish the Rights screen without breaking the existing Reviews layout.

**Files:**
- Modify: `/data/symgov/frontend/src/styles.css`

**Add classes:**
- `.rights-workbench-grid`
- `.rights-queue-pane`
- `.rights-evidence-pane`
- `.rights-decision-pane`
- `.rights-status-grid`
- `.rights-evidence-card`
- `.rights-decision-actions`
- `.rights-review-card`

Design direction:

- Engineering/admin clarity rather than legal-heavy language.
- Use warning/high-risk tone for `restricted`, `conflict`, `failed`.
- Make `clear_rights` visually positive but not identical to normal `Approve`.
- Put Daisy guidance and Tracy evidence above generic classification facts.

### Task 10: Seed/fallback data for local demo mode

**Objective:** Keep the app usable when API root is not configured.

**Files:**
- Modify: `/data/symgov/frontend/src/data.js` if needed.
- Modify: `/data/symgov/frontend/src/App.jsx`.

**Steps:**
1. Add a small seeded rights review item or derive one from `changeQueue` with `currentStage='provenance_rights_review'`.
2. Include realistic Tracy fields.
3. Ensure no live decision persistence is implied in seeded mode.

### Task 11: Update tests for frontend source markers

**Objective:** Lock the new entry paths and prevent regression back to Reviews-only.

**Files:**
- Modify: `/data/symgov/tests/test_workspace_rights_review_lane.py`

**Assertions:**
- Top nav contains `to="/rights"` and text `Rights`.
- Route contains `/rights` and `RightsReviewPage`.
- Workspace Rights card click uses `/rights?review=`.
- Reviews page may retain Rights filter, but test should assert the dedicated route exists.
- Rights page uses rights-specific labels: `Clear rights`, `Restrict publication`, `Request rights evidence`, `Mark conflict`, `Defer rights`.

### Task 12: Add backend decision tests

**Objective:** Verify persistence/audit semantics.

**Files:**
- Create/modify: `/data/symgov/tests/test_workspace_rights_review_api.py`

**Test cases:**
1. `GET /workspace/rights-review-cases` only returns `provenance_rights_review` and excludes normal classification/split reviews.
2. Response includes Tracy evidence fields from `ProvenanceAssessment.report_json` and `evidence_json`.
3. `POST clear_rights` creates `HumanReviewDecision`, `ReviewCaseAction`, and `AuditEvent` with expected codes.
4. `POST restrict_publication` requires an evidence note and creates blocking action.
5. `POST` to a non-rights review case returns 422.
6. Existing generic `POST /workspace/review-cases/{id}/decisions` tests still pass.

### Task 13: End-to-end verification

Run in this order:

```bash
cd /data/symgov
python3 -m py_compile backend/symgov_backend/routes/workspace.py backend/symgov_backend/schemas.py
PYTHONPATH=backend pytest tests/test_tracy_provenance_flow.py tests/test_workspace_rights_review_lane.py tests/test_daisy_rights_review_coordination.py tests/test_workspace_rights_review_api.py -q
PYTHONPATH=backend pytest tests/test_duplicate_exception_workflow.py::DuplicateExceptionWorkflowTests::test_only_human_actionable_case_stages_are_visible_in_review_queue tests/test_workspace_asset_preview.py::test_provenance_review_case_uses_latest_dxf_validation_derivative_preview -q
npm run build
```

If deploying:

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

Browser verification:

1. Open `https://apps.chrisbrighouse.com/#/workspace`.
2. Confirm Rights lane exists between Daisy and Review.
3. Click a Rights card; verify URL becomes `#/rights?review=<case-id>`.
4. Confirm selected Rights card opens on the dedicated screen.
5. Click top-level `Rights` button; verify it opens `/rights`.
6. Submit a non-destructive test decision only against a disposable/test case, not a live valuable item.

---

## Files likely to change

Backend:

- `/data/symgov/backend/symgov_backend/schemas.py`
- `/data/symgov/backend/symgov_backend/routes/workspace.py`

Frontend:

- `/data/symgov/frontend/src/App.jsx`
- `/data/symgov/frontend/src/api.js`
- `/data/symgov/frontend/src/styles.css`
- `/data/symgov/frontend/src/data.js` if seeded demo data is needed

Tests:

- `/data/symgov/tests/test_workspace_rights_review_lane.py`
- New: `/data/symgov/tests/test_workspace_rights_review_api.py`
- Existing regression set:
  - `/data/symgov/tests/test_tracy_provenance_flow.py`
  - `/data/symgov/tests/test_daisy_rights_review_coordination.py`
  - `/data/symgov/tests/test_duplicate_exception_workflow.py`
  - `/data/symgov/tests/test_workspace_asset_preview.py`

Docs/restart notes:

- Add closeout note under `/data/symgov/docs/restart-notes/` after implementation.

---

## Risks and tradeoffs

1. **Rights cleared does not always mean publish-ready.** The endpoint must not route directly to Rupert unless classification/validation prerequisites are already satisfied.
2. **Daisy runner lives outside repo.** Current Daisy rights-card emission is in `/data/.openclaw/workspaces/daisy/run_daisy_coordination.py`; repo-only changes do not fully represent live Daisy behavior.
3. **Mutable rights data storage.** First pass can store reviewer updates in decision payloads; if operational reporting needs queryable rights overrides, add a proper table in a later migration.
4. **Avoid queue duplication.** `/rights` should consume the same `provenance_rights_review` cases rather than creating a second independent queue source.
5. **Existing `/reviews?queue=rights` links.** Keep them working temporarily or redirect to `/rights` to avoid breaking already-deployed card links.
6. **Legal wording.** The UI should present engineering rights/provenance review controls without implying formal legal advice.

## Open questions for Chris / product decision

1. Should `clear_rights` route to Libby by default or attempt to resume the exact pre-Tracy downstream target?
2. Should `restrict_publication` close the review case as blocked, or leave it open under Daisy until evidence changes?
3. Should reviewer rights updates overwrite Tracy’s `ProvenanceAssessment` fields, or remain as a human override attached to the review decision?
4. Do we want a later dedicated `rights_review_overrides` table for reporting and filtering?

## Suggested first implementation slice

Start with the smallest valuable integrated slice:

1. Add `/rights` route and top nav button.
2. Route Rights lane cards to `/rights?review=<case-id>`.
3. Build `RightsReviewPage` using existing `/workspace/review-cases` data filtered to `provenance_rights_review`.
4. Show rights-specific evidence and decision controls in UI, but initially submit through the existing generic decision endpoint only if a safe interim mapping is agreed.
5. Then add the dedicated backend rights API/decision endpoint before using it for real operational decisions.

This avoids blocking the UI/navigation work while still keeping the persistence model conservative and auditable before live use.
