# Catalog API changelog

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
