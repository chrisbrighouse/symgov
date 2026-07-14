# Symgov Langfuse Phase 1: Isolated Synthetic POC Implementation Plan

> **For Hermes:** Execute the test/script work with strict RED → GREEN → REFACTOR. Do not commit, push, or alter any live Symgov resource.

**Goal:** Run and verify an isolated self-hosted Langfuse project containing only synthetic telemetry that demonstrates all ten approved Phase 1 acceptance criteria.

**Architecture:** Create a standalone Docker Compose project at `langfuse-poc/`, independent of `/docker/symgov-hermes/docker-compose.yml`, all live Symgov networks, and live Symgov storage. The POC runs Langfuse v3 web/worker, PostgreSQL, ClickHouse, Redis, and a dedicated POC MinIO instance on the unshared `langfuse-poc-internal` bridge; only Langfuse web is loopback-bound at `127.0.0.1:13000`. The bridge deliberately does not set Docker `internal: true`: fresh testing on this host showed that flag prevents the required loopback publication from accepting connections. Isolation instead comes from exclusive POC membership, no production/external network attachment, loopback-only publishing, POC-prefixed environment variables, and synthetic-only data. A throwaway synthetic verifier posts fixture events using POC-generated Langfuse API keys, queries/deletes the resulting traces, and writes secret-free evidence.

**Tech Stack:** Docker Compose v5, Langfuse v3, PostgreSQL 17, ClickHouse, Redis 7, MinIO, Python standard library HTTP client, pytest.

---

## Scope lock

Allowed changes:

- New repository-owned files under `/data/symgov/langfuse-poc/`.
- This Phase-1 operational plan and a final report/restart note under `/data/symgov/.hermes/plans/`.
- A generated, ignored `/data/symgov/langfuse-poc/.env` containing POC-only random credentials.
- Docker resources whose Compose project is explicitly `langfuse-poc` (containers, network, named volumes).

Forbidden changes:

- `/docker/symgov-hermes/docker-compose.yml` (read-only baseline SHA-256 `e60112b2f687ba036e51b5736503f671da6c494fb928b5d29f4f82f13d7502a9`).
- All existing containers, including `symgov-hermes-api`, `symgov-postgres`, `symgov-minio`, and `traefik-traefik-1`.
- Existing Docker networks `symgov-hermes-public` and `ai-stack`, production volumes, production PostgreSQL, production MinIO, Traefik configuration, routes, application sources/runners, and any production credential.
- Public port publishing: no Traefik labels and no `0.0.0.0` ports. POC web is loopback only.

## Prerequisites confirmed 2026-07-14

- Docker `29.6.1`, Docker Compose `v5.3.1`; 121 GiB disk and 13 GiB available RAM.
- Live Symgov containers are healthy/running and will not be modified.
- No existing Langfuse, ClickHouse, or Redis POC containers, volumes, or target directory exist.
- Current production Compose validation reports an unrelated existing schema error (`applications-web.networks.0 must be a string`); Phase 1 must neither correct nor rewrite that file.
- Upstream Langfuse v3 Compose requirements were read from the current official source. POC maps its host port range away from live MinIO (`9000/9001`) and PostgreSQL (`5432`).

## Operational retention decision for this synthetic POC

- All records are synthetic and marked `environment=poc`; this Compose stack does not configure or claim an automatic Langfuse TTL.
- The verifier creates a synthetic deletion fixture, confirms it is visible, deletes it through the public API, and polls until absent. This proves the deletion lifecycle, not automatic age-based expiry.
- The operator runbook requires `docker compose ... down -v` when the disposable POC is no longer needed; this removes every POC database/object-store volume. Automatic Phase 0 development/production retention enforcement remains future deployment work.

## Implementation tasks

### Task 1: Add the isolated Compose and environment contract

**Files:**
- Create: `langfuse-poc/docker-compose.yml`
- Create: `langfuse-poc/.env.example`
- Create: `langfuse-poc/.gitignore`
- Create: `langfuse-poc/scripts/create_poc_env.sh`

1. Define a single external loopback binding, `127.0.0.1:${POC_LANGFUSE_WEB_PORT}:3000`; all data services have no host ports.
2. Explicitly name the dedicated, unshared bridge `langfuse-poc-internal`; do not attach any existing external network. Do not set Docker `internal: true`, because it prevents the required loopback publication on this host.
3. Use a POC-only PostgreSQL database/user, POC-only MinIO root/user/password and bucket `langfuse-poc-synthetic` with `events/`, `media/`, and `exports/` prefixes.
4. Generate POC-only random credentials locally. Do not emit `.env` content or values to logs/evidence.
5. Preconfigure Langfuse initial org/project/user/API keys with `.invalid` POC identity and disable Langfuse product telemetry.

### Task 2: Define the synthetic contract and fixtures under TDD

**Files:**
- Create: `langfuse-poc/tests/test_synthetic_contract.py`
- Create: `langfuse-poc/fixtures/synthetic_events.json`
- Create: `langfuse-poc/scripts/verify_poc.py`

1. Write test-first assertions that fixtures contain exactly approved metadata, valid required contract fields, no forbidden key/value leakage, no fabricated image text-token counts, two retry attempts, UTC timestamps, and expected aggregate/reconciliation values.
2. Run the test and record the expected RED failure before implementation.
3. Add minimal fixture data for OpenRouter-like text, Gemini-like vision, image-edit units, two retry observations, fake secret/email/long-note redaction probes, UTC weekly/monthly aggregate expectations, and a `$5.00` reconciliation fixture.
4. Run test GREEN without network access.

### Task 3: Build the live POC verifier

**Files:**
- Modify: `langfuse-poc/scripts/verify_poc.py`
- Create: `langfuse-poc/scripts/healthcheck.sh`

1. Write a failing live test/check that needs the Langfuse public ingestion and query API.
2. Implement the smallest standard-library HTTP client that creates synthetic traces/observations only, waits for ingestion, queries trace data with approved metadata filters, verifies redaction by scanning Langfuse API output, writes a deterministic UTC aggregate from the submitted events, and tests deletion of a trace.
3. Use a server-provided public API only; do not query POC databases directly for application assertions.
4. Keep API keys out of artifacts, stdout, fixtures, source, and evidence.

### Task 4: Start and verify only the POC Compose project

1. Generate ignored `.env` and create the POC Compose project using `--project-name langfuse-poc` and `--env-file langfuse-poc/.env`.
2. Run `docker compose config` for the POC, `up -d`, and service health checks.
3. Confirm running POC containers are scoped to project `langfuse-poc`, the sole published POC port is `127.0.0.1:13000`, and no POC container joins `symgov-hermes-public` or `ai-stack`.
4. Run the synthetic verifier with no provider SDK, provider credential, production document/prompt, or real identity.

### Task 5: Write operations and evidence documentation

**Files:**
- Create: `langfuse-poc/README.md`
- Create: `langfuse-poc/evidence/phase-1-verification.md`
- Create: `.hermes/plans/2026-07-14_...-langfuse-phase-1-final-report.md`

1. Document startup, health checks, loopback-only access restriction, POC-only backup caveat, deletion lifecycle/teardown action, evidence reproduction, and full teardown.
2. Record all exact commands and fresh output summaries, changed files, POC container status, acceptance-criterion evidence, and repository status.
3. Never put generated secrets, raw API output containing an API key, or `.env` contents in documentation.

## Required verification

```bash
cd /data/symgov
PYTHONPATH=. pytest langfuse-poc/tests/test_synthetic_contract.py -q
docker compose --project-name langfuse-poc --env-file langfuse-poc/.env -f langfuse-poc/docker-compose.yml config
docker compose --project-name langfuse-poc --env-file langfuse-poc/.env -f langfuse-poc/docker-compose.yml ps
docker inspect ... # confirm POC-only network and loopback binding
python3 langfuse-poc/scripts/verify_poc.py --base-url http://127.0.0.1:13000 --env-file langfuse-poc/.env --evidence ...
git -C /data/symgov status --short
sha256sum /docker/symgov-hermes/docker-compose.yml
```

## Acceptance mapping

1. Synthetic OpenRouter text: fixture + public ingestion/query assertion.
2. Synthetic Gemini vision: fixture + usage-schema assertion with omitted payloads.
3. Synthetic image edit: fixture rejects `input_tokens`/`output_tokens` and verifies image units/cost basis.
4. Filtering: query/assert `agent`, `usecase`, `queueitemid`, `symboldisplayid` only.
5. Redaction: fixture values and all exported API output scanned for email/bearer/long note.
6. Retry: two distinct observation IDs/attempt numbers and expected cost sum.
7. Aggregates: deterministic UTC week/month group-by output checked against fixture.
8. Reconciliation: expected event/provider statement delta exactly `$5.00`, therefore not greater than the investigation threshold.
9. Retention/deletion: create/query/delete/query-missing trace under the documented POC deletion procedure. This validates API deletion, not automatic age-based expiry.
10. Isolation: static secret/fixture checks plus Docker network, port, project and production-Compose hash checks.

## Risks and controls

- Upstream image/API changes: pin a tested v3 image release/digest after preflight pull; record it in evidence.
- Ingestion API shape mismatch: use the official Langfuse SDK only in an isolated verifier container if public HTTP API cannot complete the required operation; never add it to Symgov application dependencies.
- Resource pressure: POC is low volume; inspect container health/disk and remove with `down -v` after handoff unless user asks to retain it.
- Authentication: access is host-loopback only, protected by Langfuse POC credentials, and deliberately not integrated with Symgov identities. Production admin-only access is a future requirement, not implemented here.
