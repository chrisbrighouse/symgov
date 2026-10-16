# Organization API keys, key limits and usage analytics

Gap-plan steps 4 and 5. Migrations `20261014_0072` and `20261015_0073`.

## What changed in production behaviour on deploy

These apply as soon as the release is live, with no flag:

- **Every Catalog API key now has a holder.** A key whose holder is deactivated stops working. Keys made by the operator CLI for nobody in particular (`created_by` empty) are **revoked by migration 0072**, one audit event each. Check the live list first (`catalog_api_keys where created_by is null and status = 'active'`) and warn any integrator who uses one.
- **Rate limits are enforced.** 60 requests a minute for a personal key, 120 for an organization key, or the key's own limit, never above 600. Over it the API answers `429` with `Retry-After`.
- **Allowed origins are enforced.** A request with an `Origin` header must come from Symgov's own origin (`SYMGOV_CSRF_TRUSTED_ORIGINS`) or an origin the key lists. Requests without `Origin` (servers) are unaffected.
- **Usage events are recorded** (views, downloads, adds to a Set or the clipboard, API fetches, searches) into `symbol_usage_events`. Nothing reads them until the analytics flag is on.
- **A maintenance worker starts** (`SYMGOV_USAGE_MAINTENANCE_SECONDS`, default 3600, `0` turns it off). Its first run, two minutes after start-up, refreshes the Stage 9 rollups that nothing refreshed before, and purges Stage 9 raw events past 90 days and symbol events past 60 days.
- `register_status` is no longer served on an organization-private symbol row.
- `POST /catalog/ed/query` carries `"engine": "rule_based"`.

## Flags (compose, default off)

| Setting | Turns on |
|---|---|
| `SYMGOV_ORGANIZATION_API_KEYS_ENABLED=1` | Organization keys: the Integrations tab, "Your organization API keys" on Profile, `/org/me/api-keys`, `/profile/api-keys`, `/catalog/organization/...` |
| `SYMGOV_SYMBOL_USAGE_ANALYTICS_ENABLED=1` | The Usage and Audit tabs (organization and platform), `/org/me/usage`, `/org/me/audit[.csv]`, `/platform/usage/...`, `/platform/audit[.csv]` |

Tunables: `SYMGOV_CATALOG_API_PERSONAL_RATE_LIMIT_PER_MINUTE` (60), `SYMGOV_CATALOG_API_ORGANIZATION_RATE_LIMIT_PER_MINUTE` (120), `SYMGOV_CATALOG_API_MAX_RATE_LIMIT_PER_MINUTE` (600).

## Rules worth knowing

- An organization key is issued by an organization admin (PIN step-up required) to a member. The member collects it from their profile and sees the secret **once**. It stops working, permanently, when the member leaves the organization, the organization is suspended, or the user is deleted. A new key must then be issued.
- A personal key's activity never counts toward an organization, whoever the user is. Search text is stored only for organization activity.
- Organization usage names people. That is a deliberate departure from the pseudonymous-analytics rule, decided 2026-10-13. The platform roll-up carries totals only.
- Organization keys read private symbols only through `/catalog/organization/...`; the public `/catalog/...` routes are unchanged. Downloading an organization-private symbol is not built (the signed-in Catalog cannot do it either).

## Deploy notes

- Run the full preflight with explicit PostgreSQL targets, including `test_catalog_api_key_holders_migration_postgresql.py`, `test_symbol_usage_migration_postgresql.py` and `test_symbol_usage_events_postgresql.py`.
- Migration 0072 is the first to change data (it revokes keys). The pre-deploy dump is the restore point; `--rollback` is code only and does not un-revoke.
- The release edits two sources the Ed knowledge bundle cites (`routes/published.py`, `ProfilePage.jsx`). Until the steward signs the rebuilt bundle and compose points at it, Ed withholds the two claims that cite them and answers the rest.
