# Integration recipes

## Internal customer portal

Recommended flow:

1. Keep `SYMGOV_CATALOG_API_KEY` in the portal backend.
2. Cache `/api/v1/catalog/taxonomy` for filter labels.
3. Proxy an allowlisted set of Catalog requests from the browser.
4. Search with `GET /api/v1/catalog/symbols` for predictable filters.
5. Use `POST /api/v1/catalog/search` when application or drawing context should affect ranking.
6. Display `displayId` such as `0003-12` before UUIDs or slugs.
7. Render preview URLs through your authenticated backend.

Do not expose the key in JavaScript delivered to the browser. CORS is deployment-dependent.

## JavaScript and TypeScript service

```ts
const apiKey = process.env.SYMGOV_CATALOG_API_KEY;
if (!apiKey) throw new Error('Missing Catalog API key');

const response = await fetch(`${process.env.SYMGOV_API_BASE_URL}/catalog/symbols?q=valve&limit=25`, {
  headers: { Authorization: `Bearer ${apiKey}` }
});
if (!response.ok) throw new Error(`Catalog request failed: ${response.status}`);
const page = await response.json();
```

Pass `nextCursor` back as `cursor` without interpreting it. New response fields may be added in v1; ignore fields your integration does not use.

## Python automation

```python
import os
import requests

base_url = os.environ["SYMGOV_API_BASE_URL"].rstrip("/")
api_key = os.environ["SYMGOV_CATALOG_API_KEY"]
response = requests.get(
    f"{base_url}/catalog/symbols",
    headers={"Authorization": f"Bearer {api_key}"},
    params={"q": "smoke detector", "limit": 25},
    timeout=20,
)
response.raise_for_status()
items = response.json()["items"]
```

Retry only transient transport failures and documented `5xx` responses with bounded exponential backoff. Do not automatically retry feedback submissions unless you can prevent duplicates.

## Apryse Viewer and drawing-review applications

Use a backend-for-frontend between Apryse Viewer and Symgov:

1. The viewer sends selected drawing context—not credentials—to your backend.
2. Your backend calls `POST /api/v1/catalog/search` with an allowlisted context object.
3. Show thumbnails and the human-readable ID beside each result.
4. Let a reviewer submit `comment`, `usage_question`, `issue`, `request_alternative`, `not_found`, or `standards_question` feedback.
5. Put `send_for_review` behind a distinct confirmation because it requests workflow/state change.

Example contextual body:

```json
{
  "query": "smoke detector near stairwell",
  "context": {
    "application": "Apryse Viewer",
    "applicationVersion": "customer-managed",
    "drawingType": "life_safety_plan",
    "selectedLayer": "FIRE_ALARM",
    "units": "mm",
    "preferredFormats": ["PNG"]
  },
  "limit": 20
}
```

## Choosing search endpoints

- Use `GET /symbols` for explicit filters, pagination, and repeatable lists.
- Use `POST /search` for contextual ranking from an authoring or review tool.
- Use `POST /ed/query` when a person provides a natural-language Catalog need or asks symbol guidance.

## Missing symbol or issue

Use `/api/v1/catalog/symbols/{symbolRef}/feedback` when a known symbol exists. Use the existing `/support` experience for integration-support escalation. Never include credentials.

Downloads are not available through any of these recipes.
