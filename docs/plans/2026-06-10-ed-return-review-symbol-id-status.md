# 2026-06-10 Ed return-to-review symbol ID/status fix

## Status

Implemented and deployed live.

## User request

When Ed handles a published symbol `send_for_review` request and hands it to Daisy/human review, the review page must treat the item as a return, and review/queue cards should show the canonical human-readable symbol ID (`####-#`) rather than the symbol name. Queue-card rendering should standardize on symbol IDs across the board.

## Changes made

- Backend queue card label resolution now prefers short symbol IDs (`####-#`) before display/name fallbacks for all agent queue items.
- Published-symbol review items now derive their display ID from the current symbol revision payload (`package_display_id` + `package_symbol_sequence`) before falling back to the slug.
- Published-symbol return review items now expose:
  - `reviewItemType = published_symbol`
  - `splitChildStatus = returned_for_review`
  - `status = Returned`
  - `symbolId` / `displayName` as the canonical short ID
- Published-symbol preview URLs still use the slug/id route key, not the display ID, so non-short slugs keep working.
- Frontend `displaySymbolId(...)` now prefers any short symbol ID candidate over names/display labels.
- Frontend was rebuilt into `/data/symgov/dist`; public bundle is now `./assets/index-DzHFBbjC.js`.
- Live API container `symgov-hermes-api` was rebuilt and recreated from `/docker/symgov-hermes`.

## Files changed

- `backend/symgov_backend/routes/workspace.py`
- `frontend/src/App.jsx`
- `tests/test_published_symbol_feedback.py`
- Existing uncommitted Ed workflow files are still present from the prior Ed feedback work:
  - `backend/symgov_backend/agent_queue_worker.py`
  - `backend/symgov_backend/routes/published.py`
  - `tests/test_published_symbol_review_workflow.py` (untracked)

## Verification performed

Focused backend regression tests in the live API container:

```bash
docker exec symgov-hermes-api sh -lc 'cd /data/symgov && python -m unittest tests.test_published_symbol_feedback tests.test_published_symbol_review_workflow -v'
```

Result: 11 tests ran and passed.

Frontend build:

```bash
cd /docker/openclaw-hz0t/data/symgov
npm run build
```

Result: Vite build completed successfully and wrote `/data/symgov/dist/assets/index-DzHFBbjC.js`.

Backend deploy:

```bash
cd /docker/symgov-hermes
docker compose build symgov-api
docker compose up -d --no-deps --force-recreate symgov-api
```

Result: `symgov-hermes-api` recreated and started.

Health checks:

```bash
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
curl -fsS https://apps.chrisbrighouse.com/api/v1/health
curl -fsS -o /dev/null -w 'OK (HTTP %{http_code})\n' https://apps.chrisbrighouse.com/api/v1/workspace/review-cases
```

Results:

- Local API health returned `{"ok":true,"service":"symgov-api",...}`
- Public API health returned `{"ok":true,"service":"symgov-api",...}`
- Public review-cases endpoint returned `OK (HTTP 200)`

Public bundle marker check:

```bash
curl -fsS https://apps.chrisbrighouse.com/assets/index-DzHFBbjC.js | grep -aoE 'returned_for_review|publishedDisplayId' | sort -u
```

Result showed both markers.

## Blocked / not performed

A final ad-hoc `curl | python` inspection of current published-symbol review rows was blocked by the command approval guard. No further retry was attempted.

## Current uncommitted state

As of this note, repo has modified files and one untracked test file. Run:

```bash
cd /docker/openclaw-hz0t/data/symgov
git status --short
git diff --stat
```

## Restart prompt

Continue Symgov Ed return-to-review queue-card verification. Load `symgov-agent-operations`. Inspect `/docker/openclaw-hz0t/data/symgov/docs/plans/2026-06-10-ed-return-review-symbol-id-status.md`, then verify any live published-symbol return item displays as `Returned` and uses its `####-#` symbol ID on the review page and agent queue cards. Preserve the existing uncommitted Ed workflow changes unless Chris explicitly asks to revert or commit.
