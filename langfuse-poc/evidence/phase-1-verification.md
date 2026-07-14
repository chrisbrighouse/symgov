# Phase 1 synthetic verification evidence

Fresh live verification was run against the private loopback endpoint `http://127.0.0.1:13000` using only generated POC keys and `fixtures/synthetic_events.json`.

- Langfuse health endpoint: HTTP 200, Langfuse `3.213.0`.
- Offline TDD contract suite: `12 passed`.
- Fixture events ingested: 5, all marked `environment=poc` and carrying only approved metadata.
- OpenRouter-like text: `workspace_chat`, provider `openrouter`, 120 input / 40 output synthetic usage and `provider_reported` USD basis.
- Gemini-like vision: `symbol_property_vision`, provider `google`, 300 input / 20 output synthetic usage plus one image-input unit; no prompt/image payload supplied.
- Gemini-like image edit: `vlad_graphic_edit`, separate input/output image units and `price_snapshot` cost `$0.040000`; no text token fields exist.
- Metadata persistence/filter evidence: `agent=libby`, `usecase=symbol_property_vision`, `queueitemid=poc-queue-vision`, and `symboldisplayid=0003-12` were returned on the stored trace.
- Redaction evidence: the fake `.invalid` email, fake bearer value, and deliberately long synthetic source note were absent from the exported trace API data.
- Retry evidence: `/api/public/observations?traceId=poc-trace-retry` returned exactly `poc-observation-retry-1` and `poc-observation-retry-2`; aggregate retry cost is `$0.012000`.
- UTC aggregate evidence: deterministic weekly `2026-W29` and monthly `2026-07` aggregates are in `phase-1-verification.json`.
- Reconciliation fixture: event total `$0.078000`, provider statement `$5.078000`, delta `$5.000000`; this is not greater than the approved investigation threshold.
- Retention/deletion: a synthetic deletion fixture was ingested, confirmed visible, deleted via the public API, and polled until no longer returned. The Langfuse worker processes this asynchronously; the verifier allows 120 seconds. This proves API deletion, not automatic age-based expiry.
- Isolation evidence: only Compose project `langfuse-poc`, dedicated unshared bridge `langfuse-poc-internal`, and host binding `127.0.0.1:13000` were used. Docker `internal: true` is intentionally absent because it prevents loopback publication on this host. No production container or network is attached. Production Compose SHA-256 stayed `e60112b2f687ba036e51b5736503f671da6c494fb928b5d29f4f82f13d7502a9`.

See `phase-1-verification.json` for machine-readable, secret-free values.
