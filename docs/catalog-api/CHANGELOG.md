# Catalog API changelog

## 2026-09-26 — Catalog symbol IDs without leading zeros

- Every canonical Catalog symbol ID is now `S-<n>` with no zero padding: `S-000001` is `S-1`, and new IDs grow without a width limit.
- **Breaking:** the padded IDs are retired. A request for `S-000001` now returns not found; use `S-1`. Re-read stored IDs from `catalogSymbolId` in any search or detail response.
- `displayId` and `catalogSymbolId` are always the canonical `S-<n>` ID for a published symbol.

## 2026-07-21 — Catalog downloads and self-service keys

- Added `POST /api/v1/catalog/symbols/download` for one direct asset or a ZIP of up to ten symbols in one available format.
- Added Integrator Hub self-service API-key creation and revocation, limited to one active key per account.
- Download availability is now reported by capabilities, taxonomy and symbol detail responses.

## 2026-07-16 — Developer Hub milestone 1

Added documentation and integration tooling for the current v1 Catalog API:

- login plus Catalog API-key gated Developer Hub;
- Catalog-only OpenAPI reference;
- five-minute quickstart and integration recipes;
- curl, JavaScript/TypeScript, Python, and C# examples;
- deterministic read-only sandbox on the current host;
- stateless documentation-grounded Ed integration help;
- Postman collection and support escalation guidance.

Boundaries at that milestone were:

- Downloads are not available.
- Conversation history is not persisted.
- Self-service registration is planned rather than current.
- CORS is deployment-dependent.
- Rate limits are not currently published.
