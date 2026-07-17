# Symgov Catalog Integration API

The Catalog Integration API lets customer applications search approved Catalog metadata, inspect symbol details and previews, ask Catalog Ed for symbol guidance, and submit structured feedback. Downloads are not available through the integration API in this release.

## Access

The Integrator Hub is available at `#/integrator/catalog` to signed-in users with the `integrator` role (administrators also retain access). Reading the integration documentation does not require a Catalog API key.

An integrator can select **Generate API key** in the Hub and provide a customer label, integration name, one or more scopes, and an optional expiry. Self-service generation is free during the initial release and is limited to one active key per account. The raw key is displayed once after creation and cannot be retrieved later; save it immediately in a secure secret store. **Clear and revoke key** permanently revokes the active key before another can be generated.

Send the saved key to protected Catalog API operations as:

```http
Authorization: Bearer YOUR_CATALOG_API_KEY
```

The Developer Hub keeps a submitted key in page memory only. It does not save it to local or session storage. For production integrations, load keys from a server-side secret store or process environment; never place them in source code, URLs, logs, browser storage, Ed prompts, or support requests.

## Start here

1. Follow the [five-minute quickstart](quickstart.md).
2. Choose a task in [integration recipes](integration-recipes.md).
3. Read [errors and security](errors-and-security.md).
4. Import `symgov-catalog-api.postman_collection.json` if you use Postman.
5. Review [the changelog](CHANGELOG.md) before upgrading.

## Current API surface

| Method | Path | Scope | Purpose |
| --- | --- | --- | --- |
| GET | `/api/v1/catalog/capabilities` | `catalog.read` | Discover current API capabilities. |
| GET | `/api/v1/catalog/taxonomy` | `catalog.read` | Load canonical facets. |
| GET | `/api/v1/catalog/symbols` | `catalog.read` | Filter and paginate published symbols. |
| GET | `/api/v1/catalog/symbols/{symbolRef}` | `catalog.read` | Read one published symbol. |
| GET | `/api/v1/catalog/symbols/{symbolRef}/thumbnail` | `catalog.read` | Retrieve its thumbnail. |
| GET | `/api/v1/catalog/symbols/{symbolRef}/preview` | `catalog.read` | Retrieve its preview. |
| POST | `/api/v1/catalog/search` | `catalog.read` | Search using drawing/application context. |
| POST | `/api/v1/catalog/ed/query` | `catalog.ed.query` | Ask for Catalog guidance or symbol discovery. |
| POST | `/api/v1/catalog/symbols/{symbolRef}/feedback` | `catalog.feedback.write` | Submit feedback or an explicit review request. |

Use human-readable IDs such as `0003-12` as the primary label in integrator interfaces.

## Current boundaries

- Downloads are not available.
- Conversation history is not persisted.
- The Developer Hub sandbox is deterministic synthetic data, not a production clone.
- CORS support is deployment-dependent; do not assume browser-origin access is enabled.
- Rate limits are not currently published.
- Self-service key generation is initially free. A subscription entitlement will be connected in a future release.

## Support

Ask Ed in the Developer Hub for documentation-grounded integration help. If Ed cannot answer, use the existing Symgov `/support` experience. Never include API keys or other credentials in a support request.
