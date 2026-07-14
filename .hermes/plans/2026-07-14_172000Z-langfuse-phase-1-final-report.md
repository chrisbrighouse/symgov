# Langfuse Phase 1 isolated synthetic POC — final report

## Status

Implemented and verified as an isolated synthetic POC. The initial Phase 0/1 artifact set was committed as `71da256dc66942a22a683d8af4b1865cc55c7890`; this report includes the subsequent evidence-correctness remediation. Nothing was pushed or exposed publicly, and no production Compose, route, runner, network, data, or credential was changed or used.

## Exact repository files added/changed

- `.hermes/plans/2026-07-14_170000Z-langfuse-phase-1-isolated-poc.md`
- `langfuse-poc/.gitignore`
- `langfuse-poc/.env.example`
- `langfuse-poc/docker-compose.yml`
- `langfuse-poc/README.md`
- `langfuse-poc/fixtures/synthetic_events.json`
- `langfuse-poc/scripts/create_poc_env.sh`
- `langfuse-poc/scripts/langfuse_poc_contract.py`
- `langfuse-poc/scripts/verify_poc.py`
- `langfuse-poc/tests/test_synthetic_contract.py`
- `langfuse-poc/evidence/phase-1-verification.md`

Generated but intentionally ignored/not committed:

- `langfuse-poc/.env` (POC-only credentials, mode `0600`)
- `langfuse-poc/evidence/phase-1-verification.json` (regenerated secret-free runtime evidence)

## Commands run and result

- `PYTHONPATH=langfuse-poc/scripts pytest langfuse-poc/tests/test_synthetic_contract.py -q` → `12 passed`.
- `python3 langfuse-poc/scripts/verify_poc.py --base-url http://127.0.0.1:13000 --env-file langfuse-poc/.env --evidence-file langfuse-poc/evidence/phase-1-verification.json` → passed.
- `python3 -m py_compile langfuse-poc/scripts/langfuse_poc_contract.py langfuse-poc/scripts/verify_poc.py` → passed.
- POC Compose config validation → passed.
- `git diff --check` → passed.

The verifier recorded only five synthetic events and confirmed: OpenRouter-like text telemetry; Gemini-like vision telemetry; image-edit image units without text tokens; persisted approved trace metadata; no fake email/bearer/long-note leakage; two retry observations; reproducible UTC week/month aggregates; exactly `$5.000000` reconciliation delta (not greater than the threshold); asynchronous API deletion of a synthetic deletion fixture; and synthetic-only operation. The deletion lifecycle does not prove automatic age-based expiry.

## Current isolated POC container status

Running under project `langfuse-poc`:

- `langfuse-poc-clickhouse-1` — healthy
- `langfuse-poc-minio-1` — healthy
- `langfuse-poc-postgres-1` — healthy
- `langfuse-poc-redis-1` — healthy
- `langfuse-poc-langfuse-web-1` — running at `127.0.0.1:13000` only
- `langfuse-poc-langfuse-worker-1` — running

No POC data-service port is published. The web container is attached only to the dedicated, unshared `langfuse-poc-internal` bridge; it is not attached to `symgov-hermes-public` or `ai-stack`. Docker `internal: true` is intentionally absent because fresh testing showed it prevents the required loopback publication on this host.

## Production invariants

- `/docker/symgov-hermes/docker-compose.yml` SHA-256 is still `e60112b2f687ba036e51b5736503f671da6c494fb928b5d29f4f82f13d7502a9`.
- `symgov-hermes-api` remains healthy.
- `symgov-postgres` and `symgov-minio` remain running.
- No production database, object-store bucket, secret, application route, or runner was changed.

## Repository state

The only unrelated uncommitted path is the pre-existing untracked `backend/nginx.conf`; it was not modified or staged by this work. `langfuse-poc/.env` and generated JSON evidence remain ignored.

## Restart prompt

Continue Symgov Langfuse Phase 1 from `/data/symgov`. Read `.hermes/plans/2026-07-14_170000Z-langfuse-phase-1-isolated-poc.md`, `langfuse-poc/README.md`, and `langfuse-poc/evidence/phase-1-verification.md`. The isolated POC stack may be running on loopback `127.0.0.1:13000`; do not use or inspect production credentials, data, routes, runners, networks, buckets, databases, or `/docker/symgov-hermes/docker-compose.yml` beyond a read-only SHA check. Re-run `PYTHONPATH=langfuse-poc/scripts pytest langfuse-poc/tests/test_synthetic_contract.py -q` and the `verify_poc.py` command before any further change. Do not commit, push, expose publicly, or integrate the application without explicit approval.
