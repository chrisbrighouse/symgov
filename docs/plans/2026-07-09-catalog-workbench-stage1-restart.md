# Catalog Workbench Stage 1 Restart Note — 2026-07-09

## Status

Stage 1 has been started and published to the live static frontend.

Chris asked that the published standards/symbols site be called the **Catalog** from now on and evolve into a professional symbol-finding workbench. This note records the first implemented frontend-only pass and how to continue safely.

## Implemented in this session

### Research / design memory

Created the staged plan:

- `docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md`

It preserves the research summary, professional taxonomy model, Ed guided-search concept, application clipboard model, and staged restart checklist.

### Frontend helper module

Created:

- `frontend/src/catalogWorkbench.js`
- `frontend/src/catalogWorkbench.test.js`

The helper module now supports:

- canonical Catalog discipline normalization
- canonical Catalog category normalization
- available-format extraction from published rows/download assets
- use-case derivation from formats
- full Catalog search text including normalized taxonomy
- local preference serialization
- saved view snapshot/apply shape
- application clipboard add/remove behavior
- preferred-format sort prioritisation

### Catalog UI changes

Modified:

- `frontend/src/App.jsx`
- `frontend/src/styles.css`

Visible changes:

- Catalog hero now says **Published Catalog** / **Find approved engineering symbols**.
- Search placeholder now mentions discipline, category and format.
- Left facets now expose professional normalized Catalog facets:
  - Discipline
  - Category
  - Use case
  - Format
  - Pack
  - Symbol family
- Table columns now show:
  - Catalog categories
  - Catalog disciplines
  - Formats
- Detail drawer now shows normalized Catalog category/discipline plus raw category/discipline for audit/debugging.
- Added an **Engineer workbench** panel with:
  - preferred disciplines
  - preferred categories
  - preferred formats
  - apply-preferences button
  - saved views backed by `localStorage`
  - application-level **Catalog clipboard** backed by `localStorage`
- Clipboard currently collects symbol IDs/names/available formats only. It intentionally does not implement download yet.

## Verification completed

Commands run from `/data/symgov`:

```bash
node --test frontend/src/catalogWorkbench.test.js
```

Result:

- 8 tests passed

```bash
npm run build
```

Result:

- Vite build passed
- output included:
  - `../dist/assets/index-Bz5_BUek.css`
  - `../dist/assets/index-Bah1dGE1.js`

```bash
./scripts/publish-static.sh
```

Result:

- published from `/data/symgov/dist` to `/data/symgov`
- published from `/data/symgov/dist` to `/data/.openclaw/workspace/symgov`

Live bundle marker check:

```bash
curl / Python fetch of https://apps.chrisbrighouse.com/
```

Confirmed live JS bundle contains:

- `Catalog clipboard`
- `Engineer workbench`
- `Catalog categories`

API health:

```bash
docker exec symgov-hermes-api sh -lc 'curl -fsS http://127.0.0.1:8010/api/v1/health'
```

Result:

```json
{"ok":true,"service":"symgov-api","time":"2026-07-09T09:48:20Z"}
```

Whitespace check:

```bash
git diff --check -- frontend/src/App.jsx frontend/src/styles.css frontend/src/catalogWorkbench.js frontend/src/catalogWorkbench.test.js docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md
```

Result: no output / passed.

## Uncommitted state

`git status --short --branch` showed this after the work:

```text
## main...origin/main
 M backend/symgov_backend/agent_queue_reconciliation.py
 M backend/symgov_backend/routes/workspace.py
 M backend/symgov_backend/schemas.py
 M frontend/src/App.jsx
 M frontend/src/api.js
 M frontend/src/styles.css
 M tests/test_agent_queue_state_machine.py
 M tests/test_workspace_rights_review_lane.py
?? docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md
?? frontend/src/catalogWorkbench.js
?? frontend/src/catalogWorkbench.test.js
```

Catalog files changed/created by this session:

- `frontend/src/App.jsx`
- `frontend/src/styles.css`
- `frontend/src/catalogWorkbench.js`
- `frontend/src/catalogWorkbench.test.js`
- `docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md`
- `docs/plans/2026-07-09-catalog-workbench-stage1-restart.md`

The backend/API/test files already shown modified in status were not part of this Catalog pass and should be inspected before any commit to avoid mixing unrelated work.

## Known limitations / next actions

1. The taxonomy cleanup is currently a frontend presentation layer only. Backend data still contains raw values such as `general`, `symbol`, and `process_instrumentation`.
2. Saved views and preferences are local-browser only via `localStorage`.
3. Clipboard is application-level only via `localStorage`, not OS Windows/macOS clipboard integration.
4. Download/bundle behavior is deliberately deferred until the engineering model is agreed.
5. Ed prompt/search is still design-only; no Ed UI prompt has been implemented yet.
6. The current UI is functional but should get UX refinement after Chris tries it.

## Suggested Stage 1 continuation

- Do a browser/manual pass on `https://apps.chrisbrighouse.com/#/standards` or Catalog nav item.
- Check layout on desktop and tablet widths.
- Decide whether preferences should auto-apply on page load or only when the user clicks Apply.
- Add a compact/card view toggle for visual browsing.
- Add a first non-mutating “Ask Ed to find symbols…” prompt that maps keywords to filters.
- Consider backend API exposure of canonical taxonomy arrays once frontend model feels right.

## Copyable restart prompt

Continue the Symgov Catalog workbench work from `/data/symgov`. Load `symgov-agent-operations`, `react-ui-patterns`, and `test-driven-development`. Read `docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md` and `docs/plans/2026-07-09-catalog-workbench-stage1-restart.md`. Inspect `git status --short --branch` first because unrelated backend files were already modified before the Catalog pass. Continue Stage 1: review the live Catalog UI, refine preferences/saved views/application clipboard UX, and start the non-mutating Ed guided-search prompt only after adding focused tests. Verify with `node --test frontend/src/catalogWorkbench.test.js`, `npm run build`, `./scripts/publish-static.sh`, and live bundle marker checks.
