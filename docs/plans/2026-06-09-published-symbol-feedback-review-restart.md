# Published symbol feedback and Ed-managed review restart note

Updated: 2026-06-09T20:04:25Z

## Goal

Allow users on the published Standards view to select one or more published symbols, limited to five at a time, and run an initial command menu with:

- Comment
- Send for Review

Both commands show a dialog listing the selected symbol IDs and names, plus a comment box. Comment uses Cancel/Post. Send for Review uses Cancel/Send. Published symbol rows also show comment and photo indicator icons rather than text-only status.

Chris clarified that collecting comments and sending published records back for review can be managed by Ed as part of his duties. The implementation therefore records Ed as the system/process owner for this workflow.

## Implemented changes

### Backend

Files changed for this task:

- `backend/symgov_backend/routes/published.py`
- `backend/symgov_backend/routes/workspace.py`
- `tests/test_published_symbol_feedback.py`

Implemented:

- `MAX_PUBLISHED_SYMBOL_COMMAND_SELECTION = 5`
- Published symbol command validation via `normalize_published_symbol_command_request(...)`
- Published symbol response fields:
  - `hasComments`
  - `commentCount`
- Comment count loading from `clarification_records`
- New command endpoint:
  - `POST /api/v1/published/symbols/commands`
  - Body currently expects the wrapped form `{ "payload": { "command": ..., "symbolIds": [...], "comment": ... } }` because FastAPI validates the parameter as `body.payload` on this route.
- `comment` command:
  - creates a `clarification_records` row with `kind='comment'`
  - writes an `audit_events` row
- `send_for_review` command:
  - creates a `clarification_records` row with `kind='review_request'`
  - creates or reuses an open `review_cases` row with `source_entity_type='published_symbol'`
  - assigns the review case to Ed system user (`ed@symgov.local`, display name `Ed`)
  - creates a `review_case_actions` row assigned to Ed
  - creates an Ed `agent_queue_items` row if `agent_definitions.slug='ed'` exists
  - writes an `audit_events` row
- Workspace review queue now handles `review_cases.source_entity_type == 'published_symbol'` and builds a human-visible review item via `build_published_symbol_workspace_item(...)`.

### Frontend

Files changed for this task:

- `frontend/src/App.jsx`
- `frontend/src/api.js`
- `frontend/src/styles.css`

Implemented:

- Checkbox selection column on the left of the published symbols table.
- UI limit of five selected symbols.
- Command bar that activates when one or more rows are selected.
- Initial command buttons:
  - Comment
  - Send for Review
- Dialogs for both commands:
  - list selected symbol ID and name
  - comment textarea
  - Cancel/Post for Comment
  - Cancel/Send for Send for Review
- API helper `submitPublishedSymbolCommand(...)`.
- Comments indicator column using a speech-bubble icon.
- Photos column now uses a camera icon; muted when no photos exist, highlighted when photos exist.
- `None yet` text was removed from the Photos table column.

## Verification completed

### API/container health

Command:

```bash
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
```

Result:

```json
{"ok":true,"service":"symgov-api","time":"2026-06-09T20:03:34Z"}
```

Command:

```bash
curl -fsS https://apps.chrisbrighouse.com/api/v1/health
```

Earlier result after rebuild:

```json
{"ok":true,"service":"symgov-api","time":"2026-06-09T20:02:35Z"}
```

### Backend focused tests

Command:

```bash
docker exec symgov-hermes-api sh -lc 'cd /data/symgov && python -m unittest tests.test_published_symbol_feedback -v'
```

Result:

```text
test_command_request_accepts_comment_and_send_for_review_commands ... ok
test_command_request_rejects_more_than_five_symbols ... ok
test_published_symbol_review_case_builds_human_queue_payload ... ok
test_published_symbol_row_exposes_comment_indicator_fields ... ok

----------------------------------------------------------------------
Ran 4 tests in 0.001s

OK
```

### Endpoint validation smoke tests

Raw body smoke initially returned 422 because this route expects `body.payload`.

Wrapped over-limit smoke command:

```bash
docker exec symgov-hermes-api sh -lc "curl -sS -o /tmp/resp.json -w '%{http_code}' -H 'Content-Type: application/json' -d '{\"payload\":{\"command\":\"comment\",\"symbolIds\":[\"a\",\"b\",\"c\",\"d\",\"e\",\"f\"],\"comment\":\"too many\"}}' http://127.0.0.1:8010/api/v1/published/symbols/commands; echo; cat /tmp/resp.json"
```

Result:

```text
400
{"error":"request_error","detail":"Select no more than 5 published symbols at a time."}
```

Missing symbol smoke command:

```bash
docker exec symgov-hermes-api sh -lc "curl -sS -o /tmp/resp.json -w '%{http_code}' -H 'Content-Type: application/json' -d '{\"payload\":{\"command\":\"comment\",\"symbolIds\":[\"definitely-not-a-symbol\"],\"comment\":\"smoke test missing symbol\"}}' http://127.0.0.1:8010/api/v1/published/symbols/commands; echo; cat /tmp/resp.json"
```

Result:

```text
404
{"error":"not_found","detail":"Published symbol not found: definitely-not-a-symbol"}
```

No valid command was posted during verification to avoid creating a real comment/review request on a live published symbol just for smoke testing.

### Published symbols endpoint

Command run earlier:

```bash
docker exec symgov-hermes-api sh -lc "tmp=$(mktemp); curl -fsS http://127.0.0.1:8010/api/v1/published/symbols -o $tmp && python -c 'import json,sys; p=json.load(open(sys.argv[1])); items=p.get(\"items\") or []; print(\"count\", len(items)); print({k:items[0].get(k) for k in [\"id\",\"symbolId\",\"hasComments\",\"commentCount\",\"supplementalPhotos\"]} if items else {})' $tmp; rm -f $tmp"
```

Result:

```text
count 62
{'id': 'justable', 'symbolId': '862d8828-63d5-5c07-9d41-6b43f2c698f0', 'hasComments': False, 'commentCount': 0, 'supplementalPhotos': []}
```

### Frontend build and live asset marker

Command:

```bash
npm run build
```

Latest result:

```text
vite v7.3.2 building client environment for production...
✓ 44 modules transformed.
../dist/index.html                   0.90 kB │ gzip:   0.44 kB
../dist/assets/index-BJKOWToZ.css   42.73 kB │ gzip:   8.82 kB
../dist/assets/index-P8axnY2j.js   350.22 kB │ gzip: 103.65 kB
✓ built in 1.50s
```

Live frontend marker check:

```text
./assets/index-P8axnY2j.js
Send for Review True
published-command-bar True
commentStatus True
payload:{command True
```

This confirms the live page is serving the rebuilt bundle containing the new command UI and wrapped API payload helper.

### Backend deployment

The live API image was rebuilt and `symgov-api` was recreated from `/docker/symgov-hermes`:

```bash
cd /docker/symgov-hermes
docker compose build symgov-api && docker compose up -d --no-deps --force-recreate symgov-api
```

Result: image built and container `symgov-hermes-api` recreated/started successfully.

## Current git/uncommitted state

Latest observed `git status --short` included these files:

```text
 M backend/symgov_backend/routes/published.py
 M backend/symgov_backend/routes/workspace.py
 M backend/symgov_backend/schemas.py
 M frontend/src/App.jsx
 M frontend/src/api.js
 M frontend/src/styles.css
 M scripts/run_hannah_curation.py
 M tests/test_agent_queue_state_machine.py
 M tests/test_duplicate_exception_workflow.py
 M tests/test_hannah_quality_filters.py
?? docs/plans/2026-06-07-hannah-feedback-and-sourcing.md
?? docs/plans/2026-06-07-reggie-queue-control-surface-restart.md
?? tests/test_published_symbol_feedback.py
```

Important: the files below appear to be pre-existing/unrelated work from earlier Symgov changes, not part of this published-symbol feedback task:

- `backend/symgov_backend/schemas.py` contains Reggie/Hannah schema changes.
- `scripts/run_hannah_curation.py`
- `tests/test_agent_queue_state_machine.py`
- `tests/test_duplicate_exception_workflow.py`
- `tests/test_hannah_quality_filters.py`
- `docs/plans/2026-06-07-hannah-feedback-and-sourcing.md`
- `docs/plans/2026-06-07-reggie-queue-control-surface-restart.md`

Task-relevant files are:

- `backend/symgov_backend/routes/published.py`
- `backend/symgov_backend/routes/workspace.py`
- `frontend/src/App.jsx`
- `frontend/src/api.js`
- `frontend/src/styles.css`
- `tests/test_published_symbol_feedback.py`
- this restart note

## Known caveat / follow-up

The API endpoint currently expects the wrapped body `{ "payload": { ... } }`; the frontend helper sends that shape and smoke tests verify it. If desired later, the backend can be refactored to accept both raw and wrapped bodies by introducing a small Pydantic request model or a manually parsed `Request` body. For now, the live UI and endpoint agree.

No valid live comment/send-for-review action was posted during verification to avoid polluting production published-symbol records with a test comment.

## Suggested next actions

1. Open the live Standards page and manually verify the table interaction:
   - select 1 to 5 rows
   - verify 6th row is disabled / blocked
   - open Comment dialog
   - open Send for Review dialog
   - verify selected ID/name list and comment box
2. Use one real symbol that genuinely needs attention and submit a real Comment or Send for Review.
3. Verify database effects for that real action:

```sql
SELECT id, symbol_id, kind, status, detail, created_at
FROM clarification_records
WHERE source='published_symbol_command_menu'
ORDER BY created_at DESC
LIMIT 10;
```

For Send for Review:

```sql
SELECT id, source_entity_type, source_entity_id, current_stage, owner_id, opened_at, closed_at
FROM review_cases
WHERE source_entity_type='published_symbol'
ORDER BY opened_at DESC
LIMIT 10;
```

```sql
SELECT aq.id, ad.slug, aq.source_type, aq.status, aq.payload_json, aq.created_at
FROM agent_queue_items aq
JOIN agent_definitions ad ON ad.id = aq.agent_id
WHERE ad.slug='ed'
ORDER BY aq.created_at DESC
LIMIT 10;
```

4. Decide whether Ed should merely manage/triage the review request, or actively kick Daisy/human-review routing. The current implementation creates the human-visible review case directly and also queues Ed if the Ed agent exists.
5. Later: implement viewing comment history from the comments indicator column.
6. Later: add the future Download command to the same command menu.

## Copyable restart prompt

Continue the Symgov published-symbol feedback workflow from `/data/symgov`. Load the `symgov-agent-operations` skill. Review `docs/plans/2026-06-09-published-symbol-feedback-review-restart.md`. The implementation currently adds checkbox selection, Comment and Send for Review dialogs, icon indicators for comments/photos, `POST /api/v1/published/symbols/commands`, clarification/audit persistence, Ed-managed send-for-review review cases, and published-symbol workspace queue items. Verification passed focused unittest, frontend build, health checks, over-limit validation, missing-symbol validation, and live bundle marker checks. Do not post a fake valid comment to production; use a real symbol only if Chris identifies one. Next: manually QA the live Standards page, verify a real action end-to-end if available, then inspect final git diff and prepare clean commit grouping while preserving unrelated Hannah/Reggie changes already in the working tree.
