# Errors and security

## Authentication and authorization

Use `Authorization: Bearer YOUR_CATALOG_API_KEY`.

- `401` means the key is missing, unknown, expired, revoked, or otherwise inactive.
- `403` means the key is valid but lacks the required scope.
- `404` means the public Catalog resource was not found.
- `400` or `422` means the request shape or value was rejected; read `detail` and, where present, `issues`.
- `5xx` means the service could not complete the request.

The Integrator Hub requires a Symgov login with the `integrator` role (administrators are also allowed), but its documentation does not require a Catalog API key. Sandbox, Catalog Ed, and protected Catalog API operations still require an active key with the relevant scope. The customer name entered during self-service generation is a key label; it does not establish or imply a separate customer-membership relationship.

## Key handling

- Self-service generation permits one active key per account. Clearing a key permanently revokes it.
- The raw key is displayed once after a successful database commit and cannot be retrieved later.
- Keep keys in a server-side secret store or environment variable.
- Never place a key in a URL, source file, screenshot, project reference, User-Agent, Ed message, context field, or support request.
- The Developer Hub keeps its validation key in memory only and clears it when locked or unloaded.
- Rotate or revoke a key that may have been exposed.

## Request metadata

Useful optional headers include:

```http
X-Symgov-Application: Customer Portal
X-Symgov-Application-Version: 1.0.0
X-Request-ID: customer-generated-opaque-id
```

Do not put credentials in metadata headers. Usage events may retain sanitized/truncated query and application metadata.

## Body bounds and context

Ed and feedback messages contain 1–2000 characters. Their context is bounded and allowlisted. Do not send secrets, connection strings, personal data, or unrestricted drawing content.

## Feedback warning

Most feedback records a question or comment. `send_for_review` explicitly requests review and may change published workflow/state. Require a clear human confirmation before using it.

## Sandbox boundary

The Developer Hub sandbox:

- runs on the current Symgov host;
- returns deterministic synthetic data;
- accepts allowlisted operations only;
- performs no Catalog mutations or downloads;
- is not a production-data clone and is not evidence that a symbol exists in production.

## Browser access

CORS is deployment-dependent. Do not ship a Catalog key to browser JavaScript. Use a backend-for-frontend for customer portals and Apryse Viewer integrations.

## Limits and retries

Rate limits are not currently published. Use conservative request concurrency, a client timeout, bounded exponential backoff for transport/`5xx` failures, and request correlation IDs. Do not assume `429` or `Retry-After` behavior until it is documented.

## Support

Ask Ed in the Developer Hub first. If the answer is uncertain or no documentation citation is available, use the existing `/support` experience without including credentials.
