# API and Application Integrations Development Plan

> Restart note: Chris wants Symgov Catalog search, Ed question-answering/discussion, and symbol-finding capabilities exposed through APIs for customer/integration use. Initial integration access should be customer API-key based, with usage monitoring/reporting added over time. Do not implement symbol download yet.

## Goal

Create a stable, customer-facing Symgov Catalog Integration API that lets external applications, CAD authoring tools, drawing review/markup tools, and customer systems search for approved symbols, ask Ed questions, discover relevant symbol expertise, preview symbol metadata, and submit usage feedback without requiring the existing browser UI.

## Scope for this plan

In scope:

- Read/search access to published Catalog symbol metadata.
- API-backed Ed discussion for:
  - natural-language symbol search
  - questions about symbols, standards context, use cases, and selection guidance
  - early lightweight answers with richer Ed capability later
- Customer-level API keys for integrations.
- Usage logging for future reporting.
- Integration-friendly taxonomy/facet metadata.
- Preview/thumbnail and deep-link metadata, but not asset download.
- Feedback/usage-question submission from integrations.
- OpenAPI documentation and smoke tests.

Out of scope for now:

- Direct CAD/DXF/DWG/SVG/PNG/PDF download bundles.
- Native AutoCAD/Revit/Bluebeam plugins.
- Billing automation.
- Full OAuth/device-code login.
- Full LLM-powered Ed expertise engine, beyond a safe API contract that can be enriched later.

## Current context

Relevant existing files:

- Backend published Catalog route:
  - `backend/symgov_backend/routes/published.py`
- Backend app/router registration:
  - `backend/symgov_backend/app.py`
- Auth/session helpers:
  - `backend/symgov_backend/auth.py`
  - `backend/symgov_backend/dependencies.py`
- API schemas:
  - `backend/symgov_backend/schemas.py`
- Frontend Catalog API client:
  - `frontend/src/api.js`
- Frontend Catalog workbench UI:
  - `frontend/src/App.jsx`
- Frontend taxonomy/search helpers:
  - `frontend/src/catalogWorkbench.js`
  - `frontend/src/catalogWorkbench.test.js`
- Current Catalog workbench plan/restart notes:
  - `docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md`
  - `docs/plans/2026-07-09-catalog-workbench-stage1-restart.md`

Observed current behavior:

- `/api/v1/health` is live and returns OK.
- `/api/v1/published/symbols` is currently protected by browser session auth and returns `401 Authentication required` without a session cookie.
- Existing published-symbol list supports only `q` and `pack` query parameters.
- Canonical Catalog taxonomy is currently mostly frontend-derived in `frontend/src/catalogWorkbench.js`.
- Existing Ed-related Catalog search is local-only and non-mutating via `interpretEdCatalogPrompt()`.
- Existing command endpoint supports comments/send-for-review from the published Catalog UI, but it is not shaped as a customer integration API.

## Product principles

1. Treat Symgov as the preferred source of symbols and symbol expertise, not just a file store.
2. Make integrations useful before downloads exist: search, preview, questions, guidance, provenance, and feedback are valuable on their own.
3. Keep Ed safe in integration contexts: Ed may answer, interpret, suggest, and explain; it must not mutate records unless routed through explicit review/feedback workflows.
4. Use human-readable symbol display IDs, such as `0003-12`, as the primary external label. UUIDs and slugs remain available for stability and joins.
5. API keys are customer/integration credentials, not individual browser sessions.
6. Log API usage from day one so reporting does not require retrofitting later.
7. Keep download intentionally unavailable in the first API version while exposing available format metadata.

## Proposed API namespace

Use a new integration-facing namespace rather than stretching `/published`:

- `/api/v1/catalog/...`
- `/api/v1/catalog/integrations/...` if customer/integration administration is needed later

Suggested initial routes:

- `GET /api/v1/catalog/capabilities`
- `GET /api/v1/catalog/taxonomy`
- `GET /api/v1/catalog/symbols`
- `GET /api/v1/catalog/symbols/{symbol_ref}`
- `GET /api/v1/catalog/symbols/{symbol_ref}/thumbnail`
- `GET /api/v1/catalog/symbols/{symbol_ref}/preview`
- `POST /api/v1/catalog/search`
- `POST /api/v1/catalog/ed/query`
- `POST /api/v1/catalog/symbols/{symbol_ref}/feedback`
- `GET /api/v1/catalog/usage` or admin/reporting equivalent later

## Authentication model

### Customer API keys

Add API keys that are assigned to a customer or integration. A customer may have multiple keys, for example:

- `Acme Engineering - AutoCAD pilot`
- `Acme Engineering - Bluebeam review team`
- `Acme Engineering - internal portal`

Recommended request header:

```http
Authorization: Bearer sk_symgov_...
```

Optionally also support:

```http
X-Symgov-Api-Key: sk_symgov_...
```

Prefer bearer tokens in documentation.

### API key record fields

Add a new database table, likely via Alembic:

`catalog_api_keys`

Fields:

- `id` UUID primary key
- `customer_name` text not null
- `integration_name` text not null
- `key_prefix` text not null, indexed, safe to display
- `key_hash` text not null, unique
- `scopes` JSON/text array not null
- `status` text not null: `active`, `disabled`, `revoked`
- `contact_name` text nullable
- `contact_email` text nullable
- `allowed_origins` JSON/text array nullable, for browser integrations later
- `rate_limit_per_minute` integer nullable
- `expires_at` timestamptz nullable
- `last_used_at` timestamptz nullable
- `created_by` UUID nullable references users if practical
- `created_at` timestamptz not null
- `updated_at` timestamptz not null
- `revoked_at` timestamptz nullable
- `notes` text nullable

Never store raw API keys after creation. Store only a hash and a display prefix.

### Initial scopes

- `catalog.read`
- `catalog.preview`
- `catalog.ed.query`
- `catalog.feedback.write`
- `catalog.usage.read` for admin/customer reporting later

Initial implementation can require only `catalog.read` for read endpoints and `catalog.ed.query` for Ed endpoints.

## Usage monitoring model

Add usage logging early, even if reporting UI comes later.

Create `catalog_api_usage_events` table.

Fields:

- `id` UUID primary key
- `api_key_id` UUID references `catalog_api_keys`
- `customer_name_snapshot` text not null
- `integration_name_snapshot` text not null
- `scope_used` text nullable
- `method` text not null
- `path` text not null
- `route_name` text nullable
- `status_code` integer not null
- `latency_ms` integer nullable
- `request_id` text nullable
- `query_text` text nullable, sanitized/truncated
- `symbol_ref` text nullable
- `result_count` integer nullable
- `ed_query_type` text nullable
- `user_agent` text nullable
- `client_ip_hash` text nullable, not raw IP unless explicitly required
- `application_name` text nullable from integration header
- `application_version` text nullable from integration header
- `created_at` timestamptz not null

Suggested integration headers:

```http
X-Symgov-Application: AutoCAD Plugin
X-Symgov-Application-Version: 0.1.0
X-Symgov-Project: optional customer/project string
```

Reporting use cases later:

- searches by customer/integration
- top query terms
- symbols opened/previewed most often
- no-result searches
- Ed questions asked
- feedback volume
- API errors/rate limits
- adoption trends over time

## API response contracts

### `GET /api/v1/catalog/capabilities`

Purpose: let external tools discover what the integration API supports.

Response shape:

```json
{
  "apiVersion": "v1",
  "catalogName": "Symgov Catalog",
  "downloadAvailable": false,
  "supports": {
    "keywordSearch": true,
    "facetSearch": true,
    "contextualSearch": true,
    "edQuestions": true,
    "previews": true,
    "feedback": true,
    "usageReporting": true
  },
  "auth": {
    "methods": ["api_key"],
    "preferredHeader": "Authorization: Bearer <api_key>"
  },
  "scopes": [
    "catalog.read",
    "catalog.preview",
    "catalog.ed.query",
    "catalog.feedback.write"
  ],
  "links": {
    "taxonomy": "/api/v1/catalog/taxonomy",
    "symbols": "/api/v1/catalog/symbols",
    "edQuery": "/api/v1/catalog/ed/query"
  }
}
```

### `GET /api/v1/catalog/taxonomy`

Purpose: make canonical Catalog taxonomy server-owned.

Response should include:

- disciplines
- categories
- formats
- use cases
- packs, if cheap to include
- raw-to-canonical mapping metadata where practical

### `GET /api/v1/catalog/symbols`

Purpose: paginated external search.

Query parameters:

- `q`
- `discipline`
- `category`
- `useCase`
- `format`
- `pack`
- `symbolFamily`
- `hasPreview`
- `updatedSince`
- `limit`, default 25, max 100
- `cursor`
- `include`, comma-separated: `taxonomy,preview,evidence,facets`

Response shape:

```json
{
  "items": [
    {
      "displayId": "0003-12",
      "symbolId": "...",
      "slug": "...",
      "name": "Smoke Detector",
      "summary": "Approved fire alarm smoke detector symbol.",
      "catalogDisciplines": ["Fire & Life Safety"],
      "catalogCategories": ["Fire Alarm Devices", "Sensors / Detectors"],
      "useCases": ["Insert into CAD drawing", "Mark up / annotate drawing"],
      "availableFormats": ["DXF", "SVG", "PNG"],
      "downloadAvailable": false,
      "preview": {
        "thumbnailUrl": "/api/v1/catalog/symbols/0003-12/thumbnail",
        "previewUrl": "/api/v1/catalog/symbols/0003-12/preview"
      },
      "links": {
        "catalog": "https://apps.chrisbrighouse.com/catalog?symbol=0003-12",
        "api": "/api/v1/catalog/symbols/0003-12"
      }
    }
  ],
  "nextCursor": null,
  "totalEstimate": 1,
  "query": {
    "q": "smoke detector",
    "filters": {}
  }
}
```

### `GET /api/v1/catalog/symbols/{symbol_ref}`

Resolve by:

- display ID, e.g. `0003-12`
- slug
- UUID

Return a detail object with:

- identity fields
- taxonomy fields
- raw category/discipline for audit
- governance/revision metadata
- preview links
- available format metadata
- provenance/evidence summary, if available
- supplemental photo metadata, if available
- deep link to Catalog UI

### `POST /api/v1/catalog/search`

Purpose: contextual search from CAD/review tools.

Request:

```json
{
  "query": "smoke detector near stairwell",
  "context": {
    "application": "AutoCAD",
    "discipline": "fire_life_safety",
    "drawingType": "life_safety_plan",
    "preferredFormats": ["DXF", "DWG"],
    "selectedLayer": "FIRE_ALARM",
    "units": "mm"
  },
  "limit": 20
}
```

Response:

- `items`, same symbol summary shape as list endpoint
- `interpretedFilters`
- `rankingExplanation`
- `warnings`
- `noDownloadNotice` while download remains out of scope

### `POST /api/v1/catalog/ed/query`

Purpose: make Ed discussion available through the API from the start.

This endpoint should support both “find symbols” and “answer a question” modes with a safe contract.

Request:

```json
{
  "message": "Which smoke detector symbol should I use for a fire alarm layout in CAD?",
  "mode": "auto",
  "context": {
    "application": "AutoCAD",
    "discipline": "fire_life_safety",
    "preferredFormats": ["DXF"],
    "drawingType": "life_safety_plan"
  },
  "conversationId": null,
  "limit": 10
}
```

`mode` values:

- `auto`: Ed decides whether to answer, search, or both
- `find_symbols`: prioritize search/facet interpretation
- `question`: prioritize explanatory answer

Response:

```json
{
  "conversationId": "edconv_...",
  "mode": "find_symbols",
  "answer": "For a fire alarm layout, start with approved Fire & Life Safety detector symbols. I found likely matches below. Download is not available through this API yet.",
  "searchQuery": "smoke detector fire alarm DXF",
  "interpretedFilters": {
    "catalogDisciplines": ["Fire & Life Safety"],
    "catalogCategories": ["Fire Alarm Devices", "Sensors / Detectors"],
    "availableFormats": ["DXF"],
    "useCases": ["Insert into CAD drawing"]
  },
  "symbols": [],
  "citations": [],
  "suggestedFollowups": [
    "Show markup-friendly symbols instead",
    "Limit to symbols with PNG previews"
  ],
  "mutatesRecords": false
}
```

Initial implementation may be deterministic and lightweight, reusing the current `interpretEdCatalogPrompt()` behavior moved to the backend. Later implementation can add richer LLM-backed answers, conversation memory, citations, and routed expertise workflows.

Ed endpoint requirements:

- Must require `catalog.ed.query` scope.
- Must log usage with `ed_query_type`, query text, result count, status, latency.
- Must not mutate symbol/review records.
- Must clearly state when it is providing guidance rather than a formal standards decision.
- Must include found symbols when available.
- Must gracefully handle no-result searches and log them for reporting.

### `POST /api/v1/catalog/symbols/{symbol_ref}/feedback`

Purpose: allow integrations to submit comments/questions/issues that improve Symgov expertise.

Request:

```json
{
  "kind": "usage_question",
  "message": "Is this detector symbol suitable for UK fire alarm layout drawings?",
  "context": {
    "application": "Bluebeam",
    "drawingType": "fire_alarm_review"
  }
}
```

Kinds:

- `comment`
- `usage_question`
- `issue`
- `request_alternative`
- `not_found`
- `standards_question`
- `send_for_review`

Initial implementation can store feedback similarly to `ClarificationRecord`, with source `catalog_integration_api`, and optionally create/reuse review follow-up flows only for explicit review-request kinds.

## Implementation tasks

### Task 1: Add backend Catalog taxonomy helper — DONE 2026-07-10

Objective: move canonical taxonomy/search helper logic from frontend-only code to backend-owned Python.

Files:

- Created: `backend/symgov_backend/catalog_taxonomy.py`
- Created: `tests/test_catalog_taxonomy.py`

Behavior implemented:

- Normalizes raw discipline/category to canonical Catalog labels.
- Extracts available formats from published symbol payload/download assets.
- Derives use cases from formats.
- Preserves raw values separately as `raw_disciplines` and `raw_categories`.

Verification passed:

```bash
PYTHONPATH=backend pytest tests/test_catalog_taxonomy.py -q
# 5 passed

node --test frontend/src/catalogWorkbench.test.js
# 17 passed

npm run build
# vite build completed successfully
```

Next: start with Task 2.

Task 2 status: complete in this workstream. The `catalog_api_keys` model and Alembic migration now store customer/integration API key metadata without raw key storage.

### Task 2: Add API key data model and migration

Objective: create storage for customer/integration API keys.

Files:

- Modify: `backend/symgov_backend/models/schema.py`
- Modify: `backend/symgov_backend/models/__init__.py`
- Create: `backend/alembic/versions/<timestamp>_catalog_api_keys.py`
- Test: `tests/test_catalog_api_keys.py`

Behavior:

- Store key hash, display prefix, customer/integration labels, scopes, status, expiry, timestamps.
- Do not store raw API keys.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_catalog_api_keys.py -q
```

Expected: model/helper tests pass.

### Task 3: Add API key auth helper/dependency — DONE 2026-07-10

Objective: allow integration endpoints to authenticate with customer API keys and scope checks.

Status: complete in this workstream. `backend/symgov_backend/catalog_api_auth.py` now provides customer/integration API-key auth helpers and FastAPI dependencies.

Files:

- Created: `backend/symgov_backend/catalog_api_auth.py`
- Test: `tests/test_catalog_api_auth.py`

Behavior implemented:

- Read bearer token from `Authorization`.
- Optionally accept `X-Symgov-Api-Key`.
- Hash and lookup token.
- Reject missing, unknown, disabled, revoked, or expired keys with 401.
- Reject insufficient-scope keys with 403.
- Return an authenticated integration context with API key ID, customer/integration metadata, scopes, and safe key prefix.
- Keep raw API keys out of storage/loggable context; lookup uses a SHA-256 digest of the presented token.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_catalog_api_auth.py -q
```

Expected: valid key passes; invalid/missing/expired/insufficient-scope cases fail with 401/403 as appropriate.

Task 3 verification passed:

```bash
PYTHONPATH=backend pytest tests/test_catalog_api_auth.py -q
# 12 passed

PYTHONPATH=backend pytest tests/test_catalog_api_keys.py tests/test_auth_service.py tests/test_auth_dependencies.py tests/test_route_auth_enforcement.py -q
# 18 passed, 16 warnings

npm run build
# vite build completed successfully
```

### Task 4: Add usage event logging — done 2026-07-10

Objective: log API usage for future customer reporting.

Files delivered:

- Modified: `backend/symgov_backend/models/schema.py`
- Modified: `backend/symgov_backend/models/__init__.py`
- Created: `backend/alembic/versions/20260710_0019_catalog_api_usage_events.py`
- Created: `backend/symgov_backend/catalog_usage.py`
- Created: `tests/test_catalog_usage_logging.py`

Behavior implemented:

- Adds `CatalogApiUsageEvent` / `catalog_api_usage_events` for API key usage reporting.
- Snapshots customer/integration metadata from `IntegrationAuthContext` using `api_key_id`, `customer_name_snapshot`, and `integration_name_snapshot`.
- Captures scope, method, path, route name, status code, latency, request ID, symbol ref, result count, Ed query type, user agent, integration application headers, and creation time.
- Sanitizes/truncates query/message text, including API-key and bearer-token redaction.
- Hashes client IP addresses into `client_ip_hash`; no raw IP column is present.
- Does not store raw API keys or key hashes in usage events.
- Keeps usage logging best-effort through `log_catalog_usage_event_best_effort()`, rolling back/swallowing logging failures so endpoint responses can still succeed.

Task 4 TDD/verification passed:

```bash
PYTHONPATH=backend pytest tests/test_catalog_usage_logging.py -q
# 6 passed

PYTHONPATH=backend pytest tests/test_catalog_api_auth.py tests/test_catalog_api_keys.py -q
# 16 passed

npm run build
# vite build completed successfully
```

Expected: events are created with sanitized/truncated fields and adjacent API key/auth behavior remains green.

### Task 5: Create Catalog integration route module — DONE 2026-07-10

Objective: introduce `/api/v1/catalog` endpoints without changing the existing `/published` UI endpoints.

Files delivered:

- Created: `backend/symgov_backend/routes/catalog.py`
- Modified: `backend/symgov_backend/app.py`
- Created: `tests/test_catalog_routes_auth.py`
- Shared schemas were not changed; Task 5 uses route-local response dictionaries for the thin initial API shell.

Initial endpoints delivered:

- `GET /api/v1/catalog/capabilities`
- `GET /api/v1/catalog/taxonomy`

Behavior implemented:

- Both routes are registered only under the curated public/customer integration namespace `/api/v1/catalog/`.
- Both routes require a valid Catalog API key with `catalog.read` through the existing API-key auth dependency.
- Missing or invalid keys return 401.
- Valid keys without `catalog.read` return 403.
- Capabilities describes the public Catalog integration menu only: current Task 5 endpoints plus intended future Catalog integration capabilities.
- Capabilities does not link or advertise private/admin/workspace/browser/published APIs.
- Capabilities and taxonomy both state `downloadAvailable: false`.
- Taxonomy returns backend-owned facet metadata from `backend/symgov_backend/catalog_taxonomy.py` constants.
- Successful route usage is logged through the Task 4 best-effort usage logger with route/status/scope/latency/request/application metadata.
- Usage logging failures are swallowed so successful route responses still return 200.

Task 5 TDD/verification passed:

```bash
PYTHONPATH=backend pytest tests/test_catalog_routes_auth.py::test_catalog_capabilities_requires_valid_catalog_read_key -q
# RED before implementation: failed as expected with 404 Not Found for the missing route.

PYTHONPATH=backend pytest tests/test_catalog_routes_auth.py -q
# 6 passed, 28 warnings

PYTHONPATH=backend pytest tests/test_catalog_api_auth.py tests/test_catalog_api_keys.py tests/test_catalog_usage_logging.py -q
# 22 passed

npm run build
# vite build completed successfully
```

### Task 6: Add paginated symbol search endpoint

Objective: expose integration-ready symbol search/list API.

Files:

- Modify: `backend/symgov_backend/routes/catalog.py`
- Test: `tests/test_catalog_symbol_search.py`

Behavior:

- Support query parameters listed above.
- Return integration-friendly symbol summary shape.
- Use backend taxonomy helper.
- Include pagination with `limit` and `cursor` or initial offset-style pagination if cursor is too much for first pass.
- Keep download unavailable but expose available format metadata.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_catalog_symbol_search.py -q
```

Expected:

- Search by keyword works.
- Filters by canonical discipline/category/format work.
- Response includes `displayId`, taxonomy, preview links, `downloadAvailable: false`.
- Usage event includes query and result count.

### Task 7: Add symbol detail and preview aliases — DONE 2026-07-10

Objective: let integrations resolve one symbol and get preview URLs by display ID, slug, or UUID.

Files delivered:

- Modified: `backend/symgov_backend/routes/catalog.py`
- Created: `tests/test_catalog_symbol_detail.py`

Endpoints delivered:

- `GET /api/v1/catalog/symbols/{symbol_ref}`
- `GET /api/v1/catalog/symbols/{symbol_ref}/thumbnail`
- `GET /api/v1/catalog/symbols/{symbol_ref}/preview`

Behavior implemented:

- The public/customer integration API remains limited to the curated `/api/v1/catalog/` namespace for this task.
- Detail and preview/thumbnail aliases require a valid Catalog API key with `catalog.read`; missing/invalid keys return 401 and valid keys without `catalog.read` return 403.
- Symbol refs resolve by human-readable display ID, slug, or UUID.
- Detail responses include `displayId`, `symbolId`, `slug`, name/summary, backend-derived taxonomy, raw category/discipline audit fields, governance/revision metadata, available formats, `downloadAvailable: false`, preview links where available, curated/provenance fields, and curated `/api/v1/catalog/` links only.
- Preview and thumbnail aliases reuse the existing published preview asset selection path (`choose_published_preview_asset`) and stream the selected asset without exposing `/api/v1/published`, admin, workspace, or download links.
- Usage logging remains best-effort and records route/symbol-ref/result metadata without failing endpoint responses if logging fails.

Task 7 TDD/verification passed:

```bash
PYTHONPATH=backend pytest tests/test_catalog_symbol_detail.py::test_catalog_symbol_detail_requires_valid_catalog_read_key -q
# RED before implementation: failed as expected with 404 Not Found for the missing route.

PYTHONPATH=backend pytest tests/test_catalog_symbol_detail.py -q
# 6 passed, 40 warnings

PYTHONPATH=backend pytest tests/test_catalog_symbol_detail.py tests/test_catalog_symbol_search.py tests/test_catalog_routes_auth.py -q
# 17 passed, 84 warnings

PYTHONPATH=backend pytest tests/test_catalog_api_auth.py tests/test_catalog_api_keys.py tests/test_catalog_usage_logging.py -q
# 22 passed

npm run build
# vite build completed successfully
```

### Task 8: Add contextual search endpoint

Objective: support CAD/review-tool search context such as application, discipline, drawing type, layer, and preferred formats.

Files:

- Modify: `backend/symgov_backend/routes/catalog.py`
- Create or modify: `backend/symgov_backend/catalog_search.py`
- Test: `tests/test_catalog_contextual_search.py`

Endpoint:

- `POST /api/v1/catalog/search`

Behavior:

- Accept query + context.
- Merge explicit context into filters/ranking.
- Return ranked symbols and explanation.
- Log application headers/context summary.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_catalog_contextual_search.py -q
```

Expected: context influences interpreted filters and preferred formats without mutating records.

### Task 9: Add Ed query endpoint

Objective: expose Ed discussion/question/symbol-finding through the API.

Files:

- Create: `backend/symgov_backend/catalog_ed.py`
- Modify: `backend/symgov_backend/routes/catalog.py`
- Test: `tests/test_catalog_ed_query.py`

Endpoint:

- `POST /api/v1/catalog/ed/query`

Initial behavior:

- Deterministic interpretation copied conceptually from `frontend/src/catalogWorkbench.js::interpretEdCatalogPrompt()`.
- `mode=auto` chooses answer/search shape based on prompt and whether symbols are found.
- Include `answer`, `searchQuery`, `interpretedFilters`, `symbols`, `suggestedFollowups`, `mutatesRecords: false`.
- Require `catalog.ed.query` scope.
- Log usage with `ed_query_type`, sanitized message, result count, latency.

Future extension points:

- Add LLM-backed answer generation.
- Add citations from symbol metadata/provenance.
- Add conversation persistence.
- Add routed handoff to Ed/Daisy/Libby workflows for explicit feedback/review actions.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_catalog_ed_query.py -q
```

Expected:

- “fire alarm detector DXF for CAD” maps to Fire & Life Safety, Fire Alarm Devices/Sensors, DXF, CAD use case.
- Question-style prompt returns a useful answer and matching symbols when available.
- Missing key or missing `catalog.ed.query` scope is rejected.
- Response always has `mutatesRecords: false`.

### Task 10: Add integration feedback endpoint

Objective: let external tools submit usage questions, issues, and review requests.

Files:

- Modify: `backend/symgov_backend/routes/catalog.py`
- Possibly modify: `backend/symgov_backend/routes/published.py` to share normalization/storage helpers
- Test: `tests/test_catalog_feedback.py`

Endpoint:

- `POST /api/v1/catalog/symbols/{symbol_ref}/feedback`

Behavior:

- Require `catalog.feedback.write` scope.
- Resolve symbol ref.
- Store feedback with source `catalog_integration_api`.
- Include customer/integration identity in detail metadata/payload where appropriate.
- For explicit `send_for_review`, reuse or align with existing review-return behavior, but keep this as a separate step if risky.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_catalog_feedback.py -q
```

Expected: feedback is recorded and usage event is logged.

### Task 11: Add minimal admin/key management path

Objective: make it possible for Symgov admins to create/revoke/list customer API keys.

Options:

1. CLI-first initial implementation:
   - Add management commands to `backend/manage_symgov.py`.
2. Admin API/UI later:
   - Add `/api/v1/admin/catalog-api-keys` endpoints and frontend panel.

Recommended initial route: CLI-first to keep the integration API work focused.

Files:

- Modify: `backend/manage_symgov.py`
- Test: `tests/test_manage_catalog_api_keys.py`

Commands:

```bash
PYTHONPATH=backend python backend/manage_symgov.py create-catalog-api-key \
  --customer "Customer Name" \
  --integration "AutoCAD Pilot" \
  --scope catalog.read \
  --scope catalog.preview \
  --scope catalog.ed.query
```

Output should show the raw key once only plus prefix and scopes.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_manage_catalog_api_keys.py -q
```

Expected: create/list/revoke flows work and raw key is not persisted.

### Task 12: Add OpenAPI examples and docs

Objective: document the integration contract clearly for future customers/tool developers.

Files:

- Create: `docs/api/catalog-integrations.md`
- Update: `backend/symgov_backend/routes/catalog.py` docstrings/Pydantic examples where useful

Documentation should include:

- Authentication header examples.
- Scope list.
- Search examples.
- Ed question examples.
- Feedback examples.
- Current limitation: no symbol download yet.
- Expected rate-limit behavior once enabled.
- Integration headers for reporting.

Verification:

```bash
PYTHONPATH=backend pytest tests/test_catalog_*.py -q
```

Manual check:

- Open `/api/v1/openapi.json` locally or via test client and confirm catalog routes appear with useful schemas.

### Task 13: Live rollout and smoke verification

Objective: safely deploy once tests pass.

Steps:

1. Run focused backend tests:

```bash
PYTHONPATH=backend pytest tests/test_catalog_taxonomy.py tests/test_catalog_api_auth.py tests/test_catalog_symbol_search.py tests/test_catalog_ed_query.py -q
```

2. Run broader relevant tests:

```bash
PYTHONPATH=backend pytest tests/test_route_auth_enforcement.py tests/test_published_symbol_feedback.py -q
```

3. Run Alembic migration locally/staging.

4. Restart live Symgov API runtime/container.

5. Smoke unauthenticated behavior:

```bash
curl -i https://apps.chrisbrighouse.com/api/v1/catalog/capabilities
```

Expected: 401.

6. Create a test customer API key.

7. Smoke authenticated behavior:

```bash
curl -i \
  -H "Authorization: Bearer <key>" \
  -H "X-Symgov-Application: curl smoke test" \
  https://apps.chrisbrighouse.com/api/v1/catalog/capabilities
```

Expected: 200 with capabilities JSON.

8. Smoke Ed query:

```bash
curl -i \
  -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  -d '{"message":"fire alarm detector DXF for CAD","mode":"auto"}' \
  https://apps.chrisbrighouse.com/api/v1/catalog/ed/query
```

Expected: 200, interpreted filters, answer, `mutatesRecords: false`.

9. Verify usage event was recorded for the smoke test key.

## Risks and tradeoffs

### API key security

Risk: leaked customer key grants Catalog access.

Mitigations:

- Store only hashed keys.
- Prefix-only display.
- Scopes.
- Revocation.
- Expiry support.
- Rate limits later.

### Customer identity vs individual user identity

Customer keys are easy for integrations but do not identify individual engineers.

Initial decision:

- Accept customer/integration-level attribution first.
- Allow optional `X-Symgov-User` or integration context later if a customer system can safely supply it.
- Consider OAuth/device-code later for user-specific access.

### Usage logging privacy

Risk: query text may contain project-sensitive information.

Mitigations:

- Sanitize/truncate query/message fields.
- Avoid raw IP storage initially.
- Document what is logged.
- Consider customer-specific retention controls later.

### Ed answer authority

Risk: integrations treat Ed answer as formal engineering approval.

Mitigations:

- Include wording that Ed provides Catalog guidance, not a formal standards decision, unless backed by published standard metadata.
- Include citations/provenance later.
- Keep `mutatesRecords: false`.
- Provide feedback/escalation path for uncertainty.

### Duplicate taxonomy logic

Risk: frontend and backend taxonomy drift.

Mitigation:

- Move canonical taxonomy to backend.
- Later update frontend to consume `/api/v1/catalog/taxonomy` or share generated constants.

### Download expectations

Risk: CAD users expect immediate insert/download.

Mitigation:

- API response clearly states `downloadAvailable: false`.
- Still expose available format metadata so users understand readiness.
- Add links back to Catalog UI.

## Open questions

1. Should customer API keys be self-managed in the Admin UI in the first version, or is CLI-only acceptable for the pilot?
2. Should every Ed API response include a standard disclaimer about guidance vs formal approval?
3. Should API keys be tied to public Catalog only, or should some customers eventually get private/customer-specific packs?
4. What reporting dimensions will matter most to Chris/customers first: searches, no-result searches, Ed questions, symbol views, or integration adoption?
5. Should `POST /api/v1/catalog/ed/query` persist conversation history now, or return stateless `conversationId` placeholders until richer Ed work begins?
6. Should CORS/browser-origin restrictions be part of phase 1, or wait until a browser-based external integration exists?

## Suggested first implementation milestone

Milestone 1 should be deliberately small but useful:

- Backend taxonomy helper.
- API key table and auth dependency.
- Usage event table/logger.
- `/api/v1/catalog/capabilities`.
- `/api/v1/catalog/taxonomy`.
- `/api/v1/catalog/symbols` basic paginated search.
- `/api/v1/catalog/ed/query` deterministic first version.
- CLI command to create/revoke API keys.
- Tests and smoke docs.

This gives Symgov a credible customer integration story without waiting for symbol downloads or native CAD plugins.

## Restart status

Tasks 1, 2, 3, 4, 5, 6, and 7 are complete in this workstream.

Task 2 delivered customer/integration Catalog API key storage in `backend/symgov_backend/models/schema.py` as `CatalogApiKey`, exported from `backend/symgov_backend/models/__init__.py`, and backed by Alembic revision `backend/alembic/versions/20260710_0018_catalog_api_keys.py`. Tests live in `tests/test_catalog_api_keys.py` and assert key hash/prefix storage, customer/integration labels, scope metadata, status/expiry/timestamps, and no raw key columns.

Task 3 delivered API key auth helpers/dependencies in `backend/symgov_backend/catalog_api_auth.py`, with TDD coverage in `tests/test_catalog_api_auth.py`. The helper treats API keys as customer/integration credentials, prefers the Authorization bearer-token header, optionally supports `X-Symgov-Api-Key`, hashes presented tokens before lookup, returns `IntegrationAuthContext`, rejects missing/unknown/disabled/revoked/expired keys with 401, and rejects insufficient scopes with 403.

Task 4 delivered usage event logging in `backend/symgov_backend/catalog_usage.py`, the `CatalogApiUsageEvent` ORM model in `backend/symgov_backend/models/schema.py`, export wiring in `backend/symgov_backend/models/__init__.py`, Alembic revision `backend/alembic/versions/20260710_0019_catalog_api_usage_events.py`, and TDD coverage in `tests/test_catalog_usage_logging.py`. Usage events snapshot customer/integration metadata from `IntegrationAuthContext`, capture route/status/latency/request/application metadata, sanitize/truncate query/message text, hash client IPs, avoid raw API keys, and use best-effort logging so endpoint responses are not failed by usage logging problems.

Task 5 delivered the first public/customer Catalog integration route module in `backend/symgov_backend/routes/catalog.py`, registered from `backend/symgov_backend/app.py`, with TDD coverage in `tests/test_catalog_routes_auth.py`. Only the curated `/api/v1/catalog/` public integration namespace was exposed. The initial endpoints are `GET /api/v1/catalog/capabilities` and `GET /api/v1/catalog/taxonomy`; both require a valid Catalog API key with `catalog.read`, return 401 for missing/invalid keys, and return 403 for valid keys without `catalog.read`. Capabilities advertises only current/future public Catalog integration capabilities and links, not private/admin/workspace/browser/published APIs. Taxonomy returns backend-owned facet constants from `backend/symgov_backend/catalog_taxonomy.py`. Both endpoints state `downloadAvailable: false` and log successful route usage best-effort.

Task 6 delivered `GET /api/v1/catalog/symbols` in `backend/symgov_backend/routes/catalog.py`, with strict-TDD coverage in `tests/test_catalog_symbol_search.py`. The endpoint lives only under the curated `/api/v1/catalog/` integration namespace, requires a valid Catalog API key with `catalog.read`, returns 401 for missing/invalid keys, and returns 403 for valid keys without `catalog.read`. It supports keyword search, discipline/category/useCase/format/pack/symbolFamily/hasPreview/updatedSince filters, capped `limit` pagination with an offset-style `cursor`, and returns integration-friendly summaries with human-readable `displayId`, stable symbol IDs/slugs, name/summary, backend-derived taxonomy fields, available formats, `downloadAvailable: false`, preview/thumbnail links where a preview is available, and curated `/api/v1/catalog/` links only. Usage logging remains best-effort and records query/result-count metadata when practical.

Task 7 delivered symbol detail and preview aliases in `backend/symgov_backend/routes/catalog.py`, with strict-TDD coverage in `tests/test_catalog_symbol_detail.py`. The implemented endpoints are `GET /api/v1/catalog/symbols/{symbol_ref}`, `GET /api/v1/catalog/symbols/{symbol_ref}/thumbnail`, and `GET /api/v1/catalog/symbols/{symbol_ref}/preview`. They remain within the curated `/api/v1/catalog/` namespace, require a valid Catalog API key with `catalog.read`, return 401 for missing/invalid keys, and return 403 for valid keys without `catalog.read`. Symbol refs resolve by human-readable display ID (for example `0003-12`), slug, or UUID. Detail responses include identity fields, backend-derived taxonomy, raw category/discipline audit fields, governance/revision metadata, available formats, `downloadAvailable: false`, curated/provenance fields, preview links where available, and curated `/api/v1/catalog/` links only. Preview and thumbnail aliases reuse the existing published preview asset selection path and stream the selected preview asset without exposing private `/published`, `/admin`, `/workspace`, or download links. Usage logging remains best-effort and records route/symbol-ref/result metadata where practical.

Latest verification after Task 7:

```bash
PYTHONPATH=backend pytest tests/test_catalog_symbol_detail.py::test_catalog_symbol_detail_requires_valid_catalog_read_key -q
# RED before implementation: failed as expected with 404 Not Found for the missing route.

PYTHONPATH=backend pytest tests/test_catalog_symbol_detail.py -q
# 6 passed, 40 warnings

PYTHONPATH=backend pytest tests/test_catalog_symbol_detail.py tests/test_catalog_symbol_search.py tests/test_catalog_routes_auth.py -q
# 17 passed, 84 warnings

PYTHONPATH=backend pytest tests/test_catalog_api_auth.py tests/test_catalog_api_keys.py tests/test_catalog_usage_logging.py -q
# 22 passed

npm run build
# vite build completed successfully
```

Next session should start at Task 8: add the contextual Catalog search endpoint. Continue to keep the public/customer integration API limited to `/api/v1/catalog/`. Do not expose/administer/document private Symgov APIs as public integration APIs unless they are intentionally promoted into `/api/v1/catalog/`, protected by Catalog API-key scopes, documented/linked, and covered by tests.

Task 8 target:

- Endpoint: `POST /api/v1/catalog/search`
- Files to modify: `backend/symgov_backend/routes/catalog.py`; create or modify `backend/symgov_backend/catalog_search.py` if helpful.
- Test to create: `tests/test_catalog_contextual_search.py`
- Scope: require `catalog.read` unless a more specific search scope is intentionally introduced.
- Auth behavior: missing/invalid key 401; valid key without required scope 403.
- Request behavior: accept query plus context such as application, discipline, drawing type, selected layer, units, and preferred formats.
- Search behavior: merge explicit context into filters/ranking and reuse existing Catalog symbol summary/detail helpers where practical.
- Response behavior: return ranked symbols, interpreted filters, ranking explanation/warnings, `downloadAvailable: false`, and curated `/api/v1/catalog/` links only.
- Usage logging: best-effort route/status/scope/latency/request metadata plus sanitized query/result count/context summary where practical.

Suggested next-session prompt:

```text
Use strict TDD for Task 8 in /data/symgov.

Plan doc:
docs/plans/2026-07-10-api-and-application-integrations.md

Task 8: Add contextual search endpoint.

Important API surface decision:
- Keep the public/customer integration API limited to the /api/v1/catalog/ namespace.
- Implement only POST /api/v1/catalog/search for this task.
- Do not expose/administer/document internal Symgov APIs as public integration APIs.
- Existing /api/v1/published, /api/v1/admin, /api/v1/workspace, browser UI, and agent/operator endpoints are private unless explicitly promoted into /api/v1/catalog/.

Requirements:
- First inspect the completed Task 7 work:
  - backend/symgov_backend/routes/catalog.py
  - tests/test_catalog_symbol_search.py
  - tests/test_catalog_symbol_detail.py
  - backend/symgov_backend/routes/published.py
  - backend/symgov_backend/catalog_taxonomy.py
  - docs/plans/2026-07-10-api-and-application-integrations.md
- Create tests first in tests/test_catalog_contextual_search.py.
- Require valid Catalog API key with catalog.read unless a more specific search scope is intentionally introduced.
- Missing/invalid key returns 401.
- Valid key without required scope returns 403.
- Accept query plus context such as application, discipline, drawing type, selected layer, units, and preferred formats.
- Merge explicit context into filters/ranking and return ranked symbols, interpreted filters, ranking explanation/warnings, downloadAvailable: false, and curated /api/v1/catalog/ links only.
- Reuse existing Catalog symbol summary/detail helpers where practical.
- Use the Task 4 usage logger best-effort; endpoint responses must not fail if usage logging fails.

Strict TDD flow:
1. Add failing tests in tests/test_catalog_contextual_search.py first.
2. Run the specific test and confirm it fails for the expected missing endpoint/implementation reason.
3. Implement the minimal route behavior.
4. Re-run targeted tests until green.
5. Run an appropriate targeted Catalog suite including contextual search, symbol detail, symbol search, and route auth tests.
6. Also run adjacent auth/key/usage tests.
7. Run npm run build.
8. Update docs/plans/2026-07-10-api-and-application-integrations.md to mark Task 8 done and add restart notes for Task 9.
9. Commit only after verification passes.
```

Uncommitted state expected after this Task 7 handoff: `backend/symgov_backend/routes/catalog.py`, `tests/test_catalog_symbol_detail.py`, and this plan doc unless the current session commits them.

## Restart checklist

If continuing this work in a new session:

1. Read this plan and confirm Tasks 1, 2, 3, 4, 5, 6, and 7 are marked done.
2. Inspect current repo state:

```bash
cd /data/symgov
git status --short --branch
```

3. Load relevant skills:

- `test-driven-development`
- `implementation-verification`
- `github-operations` before committing
- `requesting-code-review` if a pre-commit review is requested
- `subagent-driven-development` if delegating implementation

4. Review current Catalog files:

- `backend/symgov_backend/routes/catalog.py`
- `backend/symgov_backend/routes/published.py`
- `backend/symgov_backend/catalog_taxonomy.py`
- `tests/test_catalog_routes_auth.py`
- `tests/test_catalog_symbol_search.py`
- `tests/test_catalog_symbol_detail.py`
- `frontend/src/catalogWorkbench.js`

5. Start with Task 8 and keep each task independently tested.
