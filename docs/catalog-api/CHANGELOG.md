# Catalog API changelog

## 2026-10-16 — Key limits, origins and organization keys

- **Rate limit enforced.** Every key now has a per-minute budget: 60 requests for a personal key, 120 for an organization key, or the key's own limit up to 600. Over it, the API answers `429` with `Retry-After`, `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`.
- **Allowed origins enforced.** A request that carries an `Origin` header must come from Symgov's own origin or an origin the key lists; otherwise it is refused with `403`. Server-to-server calls send no `Origin` and are unaffected.
- **A key now belongs to a person.** A deactivated user's key stops working. Keys made by the operator CLI for no one in particular were revoked.
- **Organization keys.** An organization admin can issue a key to a member. The member collects it once from their profile. It stops working, for good, if the member leaves the organization or the organization is suspended.
- **Personal keys** may set their own rate limit and allowed origins when generated.
- `POST /api/v1/catalog/ed/query` now includes `"engine": "rule_based"`. It interprets the question with fixed rules and is described as the Catalog query interpreter.

## 2026-10-13 — Source register status no longer served

- The `disc` block in symbol detail responses no longer carries `register_status`, the source register's own data-quality flags (origo, sizing, label, connections, mapping). These flags are kept for Symgov operators only.
- Every other `disc` field is unchanged.

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
