# Tracy agent status report and recommendations

Date: 2026-06-21T14:12:53+00:00
Reviewer: Alfi / Hermes
Scope: live Symgov Tracy provenance-rights agent, downstream routing, runtime/DB state, and repo-managed runner.

## Executive summary

Tracy is operational, but it is still behaving more like a deterministic provenance gate than a mature rights agent. The worker is healthy, direct-runtime processing is enabled, and the active worker is configured to run the repo-managed runner at `/data/symgov/scripts/run_tracy_provenance.py`. Recent intake batches did flow Scott -> Vlad -> Tracy -> Libby, and there is no live Tracy queue backlog.

The main concern is quality and governance rather than uptime: every persisted provenance assessment in the live database currently lands as `rights_status=unknown`, `rights_disposition=unknown_warning`, `processing_outcome=review_required`, `risk_level=medium`. That means Tracy is not yet providing useful discrimination between clearly contributor-owned material, standards-reference-only material, restricted third-party libraries, and genuinely unknown cases. This pushes burden downstream to Libby/human review and can make the provenance lane feel noisy.

There is also a hygiene issue: `/data/.openclaw/workspaces/tracy/run_tracy_provenance.py` differs from the repo-managed runner. The live worker currently uses the repo path, so this is not blocking, but the stale workspace script is a trap for manual runs and should be archived or synchronized.

## Live verification evidence

### Service health

- Host timestamp checked: `2026-06-21T14:12:53+00:00`.
- `symgov-hermes-api` is up and healthy.
- Local API health returned: `{"ok":true,"service":"symgov-api","time":"2026-06-21T14:12:53Z"}`.
- Agent worker environment:
  - `SYMGOV_ENABLE_AGENT_WORKERS=1`
  - `SYMGOV_AGENT_WORKERS=scott,vlad,tracy,libby,daisy,rupert,ed,hannah`
  - `SYMGOV_AGENT_RUNTIME=direct`
  - `SYMGOV_AGENT_WORKER_DRAIN=1`
- Active `AGENT_SPECS['tracy']` from the running API container:
  - runtime root: `/data/.openclaw/workspaces/tracy/runtime`
  - runner path: `/data/symgov/scripts/run_tracy_provenance.py`
  - persist DB: `True`

### Tracy queue and runtime state

Database queue status for Tracy:

- `completed`: 85
- `superseded`: 95
- no `queued`, `running`, `failed`, or `escalated` Tracy DB rows found.

Runtime queue files under `/data/.openclaw/workspaces/tracy/runtime/agent_queue_items`:

- 85 JSON files present.
- all 85 have status `completed`.
- recent batches represented:
  - `subext-20260620T144816Z`: 61 files
  - `subext-20260620T133317Z`: 24 files

Interpretation: Tracy is not blocked. The remaining runtime JSON files are completed residue, not active queue pressure.

### Recent pipeline batches

For recent batches `subext-20260620T133317Z` and `subext-20260620T144816Z`:

- Scott completed: 2 raw submissions + 85 ZIP package members.
- Vlad completed: 85 intake records.
- Tracy completed: 85 intake records.
- Libby downstream from those batches:
  - completed: 40
  - escalated: 45 with `classification_record_requires_human_reviewer`

Interpretation: Tracy handed off downstream correctly enough for the broader intake pipeline to progress. Current human-review pressure is mostly Libby classification confidence, not Tracy queue failure.

### Provenance assessment distribution

Live `provenance_assessments` table:

- total assessments: 180
- all 180 are:
  - `rights_status=unknown`
  - `rights_disposition=unknown_warning`
  - `processing_outcome=review_required`
  - `risk_level=medium`

Assessment dates and linked review cases:

- 2026-06-20: 85 assessments, 61 linked review cases.
- 2026-06-17: 72 assessments, 72 linked review cases.
- older days: all sampled older assessments have linked review cases.

Batch-level Libby handoff review-case coverage:

- `subext-20260620T144816Z`: 61 Libby provenance items, 61 with review-case IDs.
- `subext-20260620T133317Z`: 24 Libby provenance items, 0 with review-case IDs.

Interpretation: the current repo runner has a non-blocking Libby review-case gate, but coverage depends on which runner/version processed the batch. The older batch without review-case IDs is a possible visibility gap if Libby completes without creating another human-visible case.

### Rights review lane

Public endpoint check:

- `GET /api/v1/workspace/rights-review-cases` returned HTTP 200 and an empty list.

Interpretation: there are currently no dedicated provenance/rights review cards. Given all live assessments are `unknown_warning` rather than `restricted`, `conflict`, or `failed`, that is consistent with the current Tracy logic. It also means the dedicated rights lane has not yet been exercised by real restricted/conflict input.

### Repo/runtime drift and tests

Repo status at inspection:

- branch: `main...origin/main`
- latest commit: `92c542d feat: add rights review workflow updates across backend, frontend, scripts, and tests`

Runner comparison:

- repo runner SHA256: `4a5cbd1ba877ad7d0ced55c8e1894ef32922cb8d2047c2bc5480e02f6f99e586`
- old workspace runner SHA256: `02101a8adfd9e699d83231c50346d3da714440ea8cb0e4e4e9e68111d2bc1316`
- files differ.

Key difference: the repo runner contains `review_case_recommendation_for_libby_handoff(...)`, which creates `libby_disposition_review` gates for non-blocking provenance outcomes. The stale workspace script lacks that function.

Focused test run:

- command: `cd /data/symgov && PYTHONPATH=backend pytest -q tests/test_tracy_provenance_flow.py`
- result: `8 passed in 1.81s`

## Current Tracy design assessment

### What is working

1. Tracy is live and not backlogged.
2. The database migration for rights-state separation is present and applied: `rights_disposition` and `processing_outcome` are non-null columns on `provenance_assessments`.
3. Queue processing uses direct in-container runtime and persists DB records.
4. The repo-managed runner preserves `intake_record_id`, fixing the earlier durable-record failure mode.
5. Non-blocking provenance can feed Libby and, in the repo runner, can create a review-case gate so Libby outputs do not disappear from human review.
6. The dedicated rights-review path exists structurally: restricted/conflict/failed inputs should create `provenance_rights_review` review cases and Daisy `provenance_rights_coordination` cards.

### What is weak

1. Tracy is under-discriminating. A useful rights agent should produce a meaningful distribution; live data shows a flat 180/180 `unknown_warning` distribution.
2. The current decision engine is mostly keyword-based over contributor declaration, source references, and local file references. It does not yet behave like an evidence analyst.
3. `review_required` is doing too much work. It covers package-code-only provenance, missing source refs, ambiguous declarations, and possibly legitimate contributor submissions with weak wording.
4. Rights lane is not proven with live examples. The endpoint is healthy but empty, and no current assessment is `restricted`, `conflict`, or `failed`.
5. Review-case coverage is inconsistent across recent batches. `subext-20260620T133317Z` created 24 Libby provenance items without review-case IDs; `subext-20260620T144816Z` created 61 with review-case IDs.
6. Runtime hygiene needs attention: completed Tracy runtime JSON files remain in the active queue directory, and the stale workspace runner can confuse manual operations.

## Recommendations

### Priority 1: synchronize/retire the stale workspace runner

Action:

- Either copy `/data/symgov/scripts/run_tracy_provenance.py` to `/data/.openclaw/workspaces/tracy/run_tracy_provenance.py`, or replace the workspace script with a small wrapper that exits with a clear message pointing operators to the repo-managed runner.
- Update any old docs that still show `/data/.openclaw/workspaces/tracy/run_tracy_provenance.py` as the canonical manual command.

Reason:

- The active worker uses the repo runner, but manual repair commands and old docs may still hit the stale script. That script lacks the non-blocking Libby review-case gate.

Verification:

- Re-run SHA256/cmp between the repo and workspace paths, or verify the workspace path intentionally contains only the wrapper.
- Run `PYTHONPATH=backend pytest -q tests/test_tracy_provenance_flow.py`.

### Priority 2: backfill or triage the 24 recent Libby provenance items without review cases

Action:

- Inspect `subext-20260620T133317Z` Libby provenance items.
- If they are still relevant and not already represented by visible review cases, create the missing `libby_disposition_review` cases and attach/update the Libby queue payloads so completed/escalated outputs can surface properly.

Reason:

- This batch was processed without the review-case gate. It is not a Tracy backlog, but it can become a visibility/coordination gap.

Verification:

- Batch should show `with_review_case = linked_case_exists = 24` for Libby provenance items, or an explicit documented reason why no review cases are needed.
- `/api/v1/workspace/review-cases` should expose any still-actionable downstream cases.

### Priority 3: upgrade Tracy from keyword gate to evidence-weighted rights classifier

Proposed model:

- Keep deterministic policy rules for hard blockers:
  - explicit third-party/no-redistribution/restricted licence -> `restricted` or `conflict`
  - missing required provenance fields -> `failed`
  - contradictory declaration -> `conflict`
- Add structured evidence scoring for non-blocking cases:
  - contributor declaration strength
  - submitter identity/source role
  - source package provenance
  - standards/source references
  - rights documents or attached source notes
  - known source-site licence/auth status from Scott source memory
  - whether the artifact is user-submitted original work, standards-derived redraw, manufacturer CAD reference, or internet-download candidate
- Emit clearer outcomes:
  - `cleared`: contributor-owned/internal/original with enough supporting metadata
  - `unknown_warning`: acceptable to continue to Libby/human classification, but not publication-ready until rights are confirmed
  - `restricted`: likely third-party/library content needing rights review before publication
  - `conflict`: contradictory evidence
  - `failed`: malformed/incomplete provenance payload

Reason:

- A 100% unknown-warning distribution is operationally safe but too blunt. The catalogue needs provenance confidence, not just provenance caution.

### Priority 4: add Tracy quality metrics to the Activity Monitor/operator dashboard

Recommended metrics:

- Tracy queue backlog by status and oldest age.
- Provenance assessment distribution by `rights_disposition` and `processing_outcome`.
- Count of assessments with no linked review case and no terminal downstream outcome.
- Rights lane queue count and oldest age.
- Recent restricted/conflict/failed examples.
- Batch-level coverage: Scott children vs Tracy assessments vs Libby handoffs vs review cases.

Reason:

- Tracy failures have historically looked like invisible routing gaps. The operator needs to see whether Tracy is blocked, noisy, or correctly handing off.

### Priority 5: exercise the dedicated rights-review path with a controlled fixture

Action:

- Create or use a safe test/intake fixture whose declaration says something like: `Third-party licensed symbol; no redistribution allowed.`
- Run Tracy in direct mode against it in a non-production/test fixture or controlled queue item.
- Verify:
  - `rights_disposition=restricted`
  - `processing_outcome=failed`
  - review case `current_stage=provenance_rights_review`
  - Daisy coordination card `source_type=provenance_rights_coordination`
  - downstream Daisy-emitted rights review card appears in `/workspace/rights-review-cases`

Reason:

- The code path exists and tests cover pieces of it, but the live rights lane is empty. Before relying on it operationally, prove the full Tracy -> Daisy -> Rights UI chain.

### Priority 6: archive completed runtime queue JSON out of the active queue directory

Action:

- Move completed Tracy runtime queue files from `runtime/agent_queue_items/` into an archive directory after confirming DB rows are terminal.

Reason:

- Completed files are not blocking, but they make runtime inspection noisy and increase the chance of false stall reports.

## Suggested next implementation sequence

1. Preserve runner consistency: synchronize/archive the stale workspace runner and patch docs.
2. Add a small regression/operational test that confirms `AGENT_SPECS['tracy']['runner_path'] == Path('/data/symgov/scripts/run_tracy_provenance.py')`.
3. Backfill/triage `subext-20260620T133317Z` review-case coverage.
4. Add a controlled restricted-rights smoke test for the full Daisy rights lane.
5. Improve Tracy scoring and add fixtures that produce `cleared`, `unknown_warning`, `restricted`, `conflict`, and `failed` examples.
6. Add dashboard/API summary metrics for provenance disposition and coverage.

## Copyable restart prompt

Continue the Tracy agent hardening work for Symgov. Start by reading `/data/symgov/docs/plans/2026-06-21-tracy-agent-status-report.md`. Use the `symgov-agent-operations` skill. Verify live health, confirm the active Tracy runner is `/data/symgov/scripts/run_tracy_provenance.py`, then address the highest-priority findings: synchronize or retire the stale workspace runner, triage `subext-20260620T133317Z` Libby provenance items that lack review-case IDs, and create a controlled restricted-rights smoke test proving the Tracy -> Daisy -> Rights Review lane end to end. Preserve repo restart notes and run focused tests before reporting back.


## Implementation closeout — 2026-06-21T14:29Z

Implemented all six recommendations from this report.

### Completed changes

1. Runner consistency
   - Synchronized `/data/.openclaw/workspaces/tracy/run_tracy_provenance.py` with the repo-managed runner `/data/symgov/scripts/run_tracy_provenance.py`.
   - Added a regression asserting Tracy worker config uses `/data/symgov/scripts/run_tracy_provenance.py`.

2. Missing review-case coverage
   - Added `symgov_backend.tracy_operations.backfill_provenance_libby_review_cases(...)`.
   - Applied live backfill for `subext-20260620T133317Z`: 24 missing `libby_disposition_review` cases created and Libby queue payloads updated.

3. Better Tracy rights discrimination
   - Added source-context analysis to Tracy.
   - Restricted/reference-only/manufacturer library/no-redistribution language now produces blocking `rights_disposition=restricted`, `processing_outcome=failed`, and `TRACY-RIGHTS-003`.
   - Internal/original company-authored submissions with source refs can now clear as `rights_disposition=cleared`, `processing_outcome=pass`.
   - Blocking rights cases now stop at Daisy/Rights Review and no longer create a Libby classification handoff.

4. Operator status metrics
   - Added `tracy_status_summary(...)`.
   - Added CLI command `backend/manage_symgov.py tracy-status`.
   - Added API endpoint `GET /api/v1/workspace/tracy/status`.
   - Live endpoint verified HTTP 200 after API restart.

5. Controlled rights-lane smoke
   - Created a controlled restricted-rights Tracy smoke item.
   - Verified Tracy persisted `restricted`/`failed`, Daisy coordination completed, and `/api/v1/workspace/rights-review-cases` returns one open `provenance_rights_review` item.
   - Cleaned up superseded v1 smoke artefact so only the v2 controlled rights card remains open.

6. Runtime queue archive
   - Added `archive_agent_runtime_queue(...)` and CLI command `archive-agent-runtime-queue`.
   - Archived 85 completed Tracy runtime queue JSON files plus the two controlled failed smoke queue JSON files out of `runtime/agent_queue_items`.
   - Current Tracy runtime active queue file count is 0.

### Live verification after implementation

- API health: local and public `/api/v1/health` returned OK after `docker restart symgov-hermes-api`.
- Public Tracy status endpoint: `/api/v1/workspace/tracy/status` returned HTTP 200.
- Current Tracy summary after implementation:
  - queueStatusCounts: `completed=85`, `failed=2`, `superseded=95`
  - rightsDispositionCounts: `unknown_warning=180`, `restricted=2`
  - processingOutcomeCounts: `review_required=180`, `failed=2`
  - rightsLaneOpenCount: `1`
  - runtimeQueueFiles: `0`
- Rights review endpoint: `/api/v1/workspace/rights-review-cases` returned 1 open controlled restricted-rights item.
- Focused tests: `33 passed` for Tracy provenance, Tracy metrics/runtime ops, rights review lane, Hannah throttle, and duplicate exception workflow.

### Current repo state before commit

Changed/added repo files:
- `backend/manage_symgov.py`
- `backend/symgov_backend/routes/workspace.py`
- `backend/symgov_backend/schemas.py`
- `backend/symgov_backend/tracy_operations.py`
- `scripts/run_tracy_provenance.py`
- `tests/test_tracy_provenance_flow.py`
- `tests/test_tracy_metrics_and_runtime_ops.py`
- `docs/plans/2026-06-21-tracy-agent-status-report.md`

External live files changed outside git:
- `/data/.openclaw/workspaces/tracy/run_tracy_provenance.py` synchronized to repo runner.
- Tracy runtime queue archives created under `/data/.openclaw/workspaces/tracy/runtime/agent_queue_items_archive/20260621T142523Z`, `20260621T142753Z`, and `20260621T142857Z`.

### Copyable restart prompt

Continue from `/data/symgov/docs/plans/2026-06-21-tracy-agent-status-report.md`. Verify commit/remote state, then decide whether to keep the controlled Tracy restricted-rights smoke review card open for operator UI testing or close it as `controlled_smoke_complete`. Next product step: make the Tracy status summary visible in the Activity Monitor UI and refine `assessmentsMissingReviewCases` so it excludes intentionally closed/superseded legacy assessments.


### Post-review correction — 2026-06-21T14:35Z

Independent review found three issues before commit; all were corrected and re-tested:

- File-only Tracy processing (`persist_db=False`) now initializes the rights-review decision before the DB persistence block, so blocking rights no longer raises an `UnboundLocalError`.
- Backfill candidate detection now treats any existing provenance review case as coverage, rather than only open cases, preventing the backfill helper from resurrecting closed/superseded work. The status payload now distinguishes:
  - `assessmentsMissingReviewCases`: no review case ever existed for the assessment.
  - `assessmentsWithoutOpenReviewCases`: no currently open review case exists.
- Source-context restriction detection now ignores common negated phrases such as “not copied from a manufacturer CAD library” and “not reference-only”, reducing false positive rights blocks for original submissions.
- The Tracy status API now returns a generic 500 detail instead of echoing raw exception text.

Final focused regression after this correction: `35 passed`.
