# Symgov architecture improvements — first two steps restart notes

Updated: 2026-06-06T14:17:53Z

## Scope completed in this pass

Started the first two architecture-improvement steps from the COO roadmap:

1. **Daisy duplicate-exception workflow**
   - `duplicate_exception` split items now appear in the workspace review queue instead of being hidden from the normal split-review decision lane.
   - Workspace review responses expose Libby's duplicate-resolution payload as `children[].duplicateReview`, so the UI/API can show the reason Daisy is reviewing the exception.
   - Duplicate-exception actions now have explicit backend outcomes:
     - `Approve` = human false-duplicate override; publication handoff skips the graphical duplicate gate for that decision and records `duplicate_gate_override` on the Rupert queue/action payload.
     - `Duplicate` = confirmed duplicate; marks the split item `duplicate_resolved` and records an audit event/action payload rather than publishing.
     - `Request Changes`, `Rename/Classify`, `More Evidence`, `Delete`, `Defer`, etc. route to the existing Libby follow-up lane.
   - Frontend labels duplicate-exception cards and shows a details textarea for duplicate/metadata/evidence/defer decisions, not only request-changes.

2. **Queued Rupert reconciliation**
   - Fixed the post-publication handoff status overwrite that could relabel synchronously-published split items back to `queued_rupert`.
   - Reconciled the two existing stale `queued_rupert` rows to `published` because their completed Rupert actions already listed them in `published_split_item_ids`:
     - `01-PUMPS-AND-TURBINE-REGION-06`
     - `4-WAY-VALVE`
   - Current DB check shows `queued_rupert = 0`.

## Files changed

- `backend/symgov_backend/publication_handoff.py`
  - Added `publication_duplicate_override_for_decision()`.
  - Publication handoff honours human false-duplicate override before graphical duplicate detection.
  - Rupert queue/action payloads record duplicate override evidence.

- `backend/symgov_backend/routes/workspace.py`
  - Added `duplicate_exception` to open split-item statuses.
  - Added `split_item_status_after_handoff()` to preserve durable `published`, `duplicate_pending`, and `duplicate_resolved` outcomes after handoff.
  - Split workspace cards include duplicate-review payload and duplicate-exception summary copy.
  - Split decision processing now handles duplicate-exception approve/duplicate/follow-up outcomes explicitly.

- `backend/symgov_backend/schemas.py`
  - Added `WorkspaceReviewChildResponse.duplicateReview`.

- `frontend/src/App.jsx`
  - Shows duplicate-exception queue label.
  - Shows decision details textarea for duplicate, rename/classify, more-evidence, and defer decisions.

- `tests/test_duplicate_exception_workflow.py`
  - Added regression tests for false-duplicate override detection and handoff status preservation.

## Verification run

Commands run successfully:

```bash
docker exec -w /data/symgov symgov-hermes-api python -m unittest tests.test_duplicate_exception_workflow tests.test_publication_handoff_split_status
# Ran 8 tests — OK

docker exec -w /data/symgov symgov-hermes-api python -m unittest discover -s tests
# Ran 34 tests — OK

npm run build
# Vite production build completed successfully

docker compose up -d --no-deps --force-recreate symgov-api
# symgov-hermes-api recreated and started

docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
# {"ok":true,"service":"symgov-api",...}

curl -fsS -o /dev/null -w 'public_http=%{http_code}\n' https://apps.chrisbrighouse.com/api/v1/health
# public_http=200
```

Live workspace API check after restart:

- Review items returned: 83
- Split statuses include: `awaiting_decision`, `duplicate_exception`, `returned_for_review`
- Duplicate exceptions visible:
  - `01-MECHANICAL-SYMBOLS-REGION-12`
  - `ADJUSTABLE`
  - `DOUBLE-ACTING-MAGNETIC`
  - `PORTS`

Current split status snapshot:

```text
awaiting_decision   | daisy  | 18
awaiting_decision   |        | 55
deleted             | libby  | 2
deleted             |        | 41
duplicate_exception | daisy  | 4
duplicate_resolved  |        | 1
published           | rupert | 61
rejected            |        | 3
returned_for_review |        | 3
queued_rupert       |        | 0
```

## Uncommitted state

At the time of this note, the repo has uncommitted changes:

```text
 M backend/symgov_backend/publication_handoff.py
 M backend/symgov_backend/routes/workspace.py
 M backend/symgov_backend/schemas.py
 M frontend/src/App.jsx
?? docs/plans/2026-06-06-symgov-architecture-first-two-steps-restart.md
?? tests/test_duplicate_exception_workflow.py
```

No credentials were intentionally read or recorded.

## Step 3 progress — ReviewSplitItem lifecycle clarity

Updated: 2026-06-06T15:05:43Z

Started step 3 of the COO roadmap by making split-item lifecycle grouping explicit in `backend/symgov_backend/routes/workspace.py`:

- Added `REVIEW_SPLIT_STATUS_GROUPS` with clear groups:
  - `active_review`: `awaiting_decision`, `returned_for_review`, `duplicate_exception`
  - `active_downstream`: `queued_rupert`, `queued_libby`, `duplicate_pending`
  - `terminal_publication`: `published`
  - `terminal_duplicate`: `duplicate_resolved`
  - `terminal_non_publication`: `deleted`, `rejected`, `blocked`, `deferred`
- Added helper functions:
  - `split_item_status_group(status)`
  - `is_open_split_item_status(status)`
  - `is_terminal_split_item_status(status)`
- Updated split-review processing to use `is_open_split_item_status(...)` rather than scattered literal open-status checks.
- Strengthened `split_item_status_after_handoff(...)` so Libby follow-up states such as `returned_for_review`, `queued_libby`, and terminal dispositions are preserved instead of being overwritten by generic fallback states.
- Added lifecycle regression coverage to `tests/test_duplicate_exception_workflow.py`.

Verification after step 3 changes:

```bash
docker exec -w /data/symgov symgov-hermes-api python -m unittest tests.test_duplicate_exception_workflow
# Ran 6 tests — OK

docker exec -w /data/symgov symgov-hermes-api python -m unittest discover -s tests
# Ran 36 tests — OK

npm run build
# Vite production build completed successfully

cd /docker/symgov-hermes && docker compose up -d --no-deps --force-recreate symgov-api
# symgov-hermes-api recreated and started

docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
# {"ok":true,"service":"symgov-api","time":"2026-06-06T15:05:31Z"}

curl -fsS -o /dev/null -w 'public_http=%{http_code}\n' https://apps.chrisbrighouse.com/api/v1/health
# public_http=200
```

Live split status snapshot remains clean; notably there are still no `queued_rupert` rows:

```text
awaiting_decision   | daisy  | 18
awaiting_decision   |        | 54
deleted             | libby  | 2
deleted             |        | 42
duplicate_exception | daisy  | 4
duplicate_resolved  |        | 1
published           | rupert | 61
rejected            |        | 3
returned_for_review |        | 3
queued_rupert       |        | 0
```

## Live duplicate-exception smoke test

Updated: 2026-06-06T15:10:31Z

Chris approved mutating a live item to smoke-test the duplicate-exception workflow. I used the conservative **confirm duplicate** path, which avoids publishing a new symbol.

Mutated split item:

```text
review_split_items.id: b35baf0c-8e91-5ff4-8cb2-bff95b1a1744
review_case_id:       8d26d1b6-3796-4f9e-93f3-26be01be174e
proposed_symbol_id:   01-MECHANICAL-SYMBOLS-REGION-12
matched symbol:       double-acting-cylinder
```

Decision submitted through the live API:

```text
action: duplicate
decider: Alfi COO smoke test
result: HTTP 200
processedCount: 1
item status: duplicate_resolved
decisionId: dbc29c35-0a04-4349-8e02-f09388dfcd5a
```

DB verification:

```text
01-MECHANICAL-SYMBOLS-REGION-12 -> duplicate_resolved
latest_action -> duplicate_confirmed
downstream_agent_slug -> null
downstream_queue_item_id -> null
processed_at -> 2026-06-06 15:09:48+00
```

Audit verification recorded both:

```text
split_child_review_decision_recorded
duplicate_exception_confirmed
```

Workspace list verification:

```text
review_items 82
ADJUSTABLE duplicate_exception
DOUBLE-ACTING-MAGNETIC duplicate_exception
PORTS duplicate_exception
```

So the mutated item left the duplicate-exception lane, and duplicate exceptions dropped from 4 to 3.

Post-mutation verification:

```bash
docker exec -w /data/symgov symgov-hermes-api python -m unittest discover -s tests
# Ran 36 tests — OK

docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
# {"ok":true,"service":"symgov-api","time":"2026-06-06T15:10:24Z"}

curl -fsS -o /dev/null -w 'public_http=%{http_code}\n' https://apps.chrisbrighouse.com/api/v1/health
# public_http=200
```

Note: the parent review case still reports `current_stage = libby_deletion_review`, which appears to be pre-existing case-level stage noise on a multi-child split review. The split-item-level lifecycle state is correct and the workspace queue correctly removed the resolved duplicate item.

## Suggested next actions

1. Commit the current coherent checkpoint.
2. Continue step 3 by extracting lifecycle helpers into a small backend module if more routes/runners need them, then replace remaining scattered literal status checks outside `routes/workspace.py`.
3. Move next to catalogue quality score / published taxonomy exposure.

## Restart prompt

Continue Symgov architecture hardening in `/docker/openclaw-hz0t/data/symgov`. Load `symgov-agent-operations` and `symgov-architecture-improvement-roadmap`. Inspect `docs/plans/2026-06-06-symgov-architecture-first-two-steps-restart.md`, run `git status --short`, and verify `docker exec -w /data/symgov symgov-hermes-api python -m unittest discover -s tests`. Current uncommitted work implements Daisy duplicate-exception workflow, queued Rupert reconciliation, and the first ReviewSplitItem lifecycle grouping/helpers. Next: UI smoke test a duplicate-exception decision if safe, commit the checkpoint, then continue lifecycle cleanup or start catalogue quality scoring.
