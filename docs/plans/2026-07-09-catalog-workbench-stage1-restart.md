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

## 2026-07-09 continuation update

Implemented the first non-mutating Ed guided-search prompt in the Catalog workbench.

Changed:

- `frontend/src/catalogWorkbench.js`
- `frontend/src/catalogWorkbench.test.js`
- `frontend/src/App.jsx`
- `frontend/src/styles.css`
- `docs/plans/2026-07-09-catalog-workbench-stage1-restart.md`

Behavior added:

- Ed prompt card labelled **Ask Ed to find symbols**.
- Natural-language prompt is interpreted locally into Catalog search + filters only.
- Initial mappings include fire/life-safety, detectors/sensors, electrical, P&ID/piping, valves, pumps, CAD/markup/documentation use-cases, and explicit formats such as DXF/PNG/PDF.
- UI explicitly states Ed changes no records.
- Ed preferred formats feed the existing preferred-format prioritisation.

Verification completed from `/data/symgov`:

```bash
node --test frontend/src/catalogWorkbench.test.js
```

Result: 9 tests passed.

```bash
git diff --check -- frontend/src/App.jsx frontend/src/styles.css frontend/src/catalogWorkbench.js frontend/src/catalogWorkbench.test.js
```

Result: no output / passed.

```bash
npm run build
```

Result:

- Vite build passed.
- Output included:
  - `../dist/assets/index-DynNVAtd.css`
  - `../dist/assets/index-BrAbx_Vw.js`

```bash
./scripts/publish-static.sh
```

Result:

- published from `/data/symgov/dist` to `/data/symgov`
- published from `/data/symgov/dist` to `/data/.openclaw/workspace/symgov`

Live bundle marker check with browser-like User-Agent confirmed the public JS bundle `./assets/index-BrAbx_Vw.js` contains:

- `Ask Ed to find symbols`
- `No records are changed`
- `Ed mapped`
- `Catalog clipboard`

API health checks passed:

```json
{"ok":true,"service":"symgov-api","time":"2026-07-09T10:01:36Z"}
```

Browser/manual note: direct navigation to `https://apps.chrisbrighouse.com/#/standards` in the automation browser currently lands on the sign-in screen, so the authenticated visual pass still needs Chris/session credentials or a logged-in browser context. Public static bundle verification succeeded.

## 2026-07-09 compact-browsing update

Implemented Catalog browsing refinements requested by Chris:

- The Engineer workbench / preferences panel is now collapsible and starts collapsed so more symbol records are visible immediately.
- The Catalog result area now defaults to a **Compact cards** view with a Table toggle retained for detailed column filtering/sorting.
- Compact cards show symbol short ID, name, preview, normalized categories/disciplines, available format chips, and 📷/💬 indicators.
- Added `buildCatalogCardSummary()` to keep compact-card identity/taxonomy/format fields testable in the pure helper module.

Verification completed from `/data/symgov`:

```bash
node --test frontend/src/catalogWorkbench.test.js
```

Result: 10 tests passed.

```bash
git diff --check -- frontend/src/App.jsx frontend/src/styles.css frontend/src/catalogWorkbench.js frontend/src/catalogWorkbench.test.js
```

Result: no output / passed.

```bash
npm run build
```

Result:

- Vite build passed.
- Output included:
  - `../dist/assets/index-CNff4Msy.css`
  - `../dist/assets/index-R3qbNAVg.js`

```bash
./scripts/publish-static.sh
```

Result:

- published from `/data/symgov/dist` to `/data/symgov`
- published from `/data/symgov/dist` to `/data/.openclaw/workspace/symgov`

Live bundle marker check with browser-like User-Agent confirmed:

- public JS bundle `./assets/index-R3qbNAVg.js` contains `Compact cards`, `Show preferences`, and `Collapse preferences`.
- public CSS bundle `./assets/index-CNff4Msy.css` contains `catalog-symbol-card`, `catalog-view-toggle`, and `catalog-workbench-panel.collapsed`.

API health check passed at `2026-07-09T10:14:31Z`:

```json
{"ok":true,"service":"symgov-api","time":"2026-07-09T10:14:31Z"}
```

Browser/manual note remains: direct automation browser navigation to `https://apps.chrisbrighouse.com/#/standards` requires an authenticated session; public static bundle verification succeeded.

Current uncommitted Catalog state after the compact-browsing continuation:

```text
## main...origin/main [ahead 1]
 M docs/plans/2026-07-09-catalog-workbench-stage1-restart.md
 M frontend/src/App.jsx
 M frontend/src/catalogWorkbench.js
 M frontend/src/catalogWorkbench.test.js
 M frontend/src/styles.css
```

## Known limitations / next actions

1. The taxonomy cleanup is currently a frontend presentation layer only. Backend data still contains raw values such as `general`, `symbol`, and `process_instrumentation`.
2. Saved views and preferences are local-browser only via `localStorage`.
3. Clipboard is application-level only via `localStorage`, not OS Windows/macOS clipboard integration.
4. Download/bundle behavior is deliberately deferred until the engineering model is agreed.
5. Ed prompt/search is now frontend-only and non-mutating; mappings are simple local keyword rules, not an LLM or backend Ed workflow.
6. The current UI is functional but should get UX refinement after Chris tries it in an authenticated browser session.

## Suggested Stage 1 continuation

- Do a browser/manual pass on `https://apps.chrisbrighouse.com/#/standards` or Catalog nav item.
- Check layout on desktop and tablet widths.
- Decide whether preferences should auto-apply on page load or only when the user clicks Apply.
- Add a compact/card view toggle for visual browsing.
- Add a first non-mutating “Ask Ed to find symbols…” prompt that maps keywords to filters.
- Consider backend API exposure of canonical taxonomy arrays once frontend model feels right.

## Copyable restart prompt

Continue the Symgov Catalog workbench work from `/data/symgov`. Load `symgov-agent-operations`, `react-ui-patterns`, and `test-driven-development`. Read `docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md` and `docs/plans/2026-07-09-catalog-workbench-stage1-restart.md`. Inspect `git status --short --branch` first. Current Stage 1 continuation adds a frontend-only, non-mutating Ed guided-search prompt plus tests and has been published live. Next useful work: do an authenticated browser/manual pass on the Catalog, refine preferences/saved views/application clipboard/Ed UX from real use, then consider compact/card browsing before backend taxonomy work or download bundles. Verify with `node --test frontend/src/catalogWorkbench.test.js`, `npm run build`, `./scripts/publish-static.sh`, live bundle marker checks, and API health checks.
