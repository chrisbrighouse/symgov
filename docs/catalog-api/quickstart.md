# Five-minute quickstart

## 1. Set your connection values

Use the API base URL supplied with your customer onboarding and keep the key outside source control.

```bash
export SYMGOV_API_BASE_URL="https://YOUR_SYMGOV_HOST/api/v1"
export SYMGOV_CATALOG_API_KEY="YOUR_CATALOG_API_KEY"
```

## 2. Discover current capabilities

```bash
curl --fail-with-body \
  --header "Authorization: Bearer $SYMGOV_CATALOG_API_KEY" \
  "$SYMGOV_API_BASE_URL/catalog/capabilities"
```

## 3. Search for a symbol

```bash
curl --fail-with-body --get \
  --header "Authorization: Bearer $SYMGOV_CATALOG_API_KEY" \
  --data-urlencode "q=smoke detector" \
  --data-urlencode "limit=5" \
  "$SYMGOV_API_BASE_URL/catalog/symbols"
```

The response contains `items` and an opaque `nextCursor`. Use a human-readable `displayId`, such as `0003-12`, in your interface.

## 4. Read details and a preview

```bash
curl --fail-with-body \
  --header "Authorization: Bearer $SYMGOV_CATALOG_API_KEY" \
  "$SYMGOV_API_BASE_URL/catalog/symbols/0003-12"

curl --fail-with-body \
  --header "Authorization: Bearer $SYMGOV_CATALOG_API_KEY" \
  --output 0003-12-preview \
  "$SYMGOV_API_BASE_URL/catalog/symbols/0003-12/preview"
```

## 5. Ask Catalog Ed to find symbols

This is the Catalog API's symbol-guidance endpoint, not the Developer Hub integration-help chat.

```bash
curl --fail-with-body \
  --request POST \
  --header "Authorization: Bearer $SYMGOV_CATALOG_API_KEY" \
  --header "Content-Type: application/json" \
  --data '{"message":"Find smoke detector symbols for a fire alarm drawing","mode":"auto","context":{"application":"Internal Portal","drawingType":"life_safety_plan","preferredFormats":["DXF"]},"limit":10}' \
  "$SYMGOV_API_BASE_URL/catalog/ed/query"
```

Downloads are not available. `availableFormats` describes Catalog metadata; it is not a download entitlement.

## JavaScript and TypeScript note

Browser calls require an allowed deployment origin. CORS is deployment-dependent. Prefer a customer-controlled backend-for-frontend so the API key remains server-side.

## Python note

Use `requests` or `httpx`, set a timeout, call `raise_for_status()`, and read the key from `SYMGOV_CATALOG_API_KEY`.

## Next step

Use the in-page sandbox to test synthetic requests without changing Catalog data, then follow the task-oriented recipes.
