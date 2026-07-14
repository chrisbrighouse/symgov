# Symgov Langfuse isolated synthetic POC

This directory is a Phase 1 proof of concept only. It is not a production deployment and is not connected to Symgov application traffic.

## Isolation and access boundary

- Compose project: `langfuse-poc`
- Docker network: dedicated bridge `langfuse-poc-internal` only; it is not `symgov-hermes-public` or `ai-stack`. Docker `internal: true` is intentionally not used because fresh testing showed it prevents the required loopback publication on this host.
- Services: Langfuse v3 web/worker, dedicated POC PostgreSQL (`langfuse_poc`), ClickHouse, Redis, and MinIO.
- Object storage: dedicated POC MinIO bucket `langfuse-poc-synthetic`, using `events/`, `media/`, and `exports/` prefixes and POC-generated credentials.
- The only host port is `127.0.0.1:13000 -> Langfuse web:3000`. There are no Traefik labels or public bindings.
- The POC initial login uses the `.invalid` identity in the generated local `.env`. It is not integrated with Symgov roles. The future production admin-role-only rule is intentionally not implemented here.

Do not put provider credentials, documents, prompts, image files, customer data, real identities, or production secrets into this POC.

## Startup

```bash
cd /data/symgov
chmod 700 langfuse-poc/scripts/create_poc_env.sh
./langfuse-poc/scripts/create_poc_env.sh       # only when .env does not yet exist
docker compose --project-name langfuse-poc --env-file langfuse-poc/.env -f langfuse-poc/docker-compose.yml config --quiet
docker compose --project-name langfuse-poc --env-file langfuse-poc/.env -f langfuse-poc/docker-compose.yml up -d
```

`langfuse-poc/.env` is gitignored, generated with mode `0600`, and contains random POC-only values. Infrastructure credential variables are `POC_`-prefixed so generic host variables such as `POSTGRES_PASSWORD` cannot override the generated POC values. Never run Compose with a production env file. If the POC env contract changes, tear down the disposable POC volumes, remove `langfuse-poc/.env`, regenerate it, and start again.

## Health and access checks

```bash
curl -fsS http://127.0.0.1:13000/api/public/health
docker compose --project-name langfuse-poc --env-file langfuse-poc/.env -f langfuse-poc/docker-compose.yml ps
docker inspect langfuse-poc-langfuse-web-1 --format '{{json .HostConfig.PortBindings}}'
docker inspect langfuse-poc-langfuse-web-1 --format '{{range $k, $_ := .NetworkSettings.Networks}}{{$k}} {{end}}'
```

Expected: `/api/public/health` returns `status=OK`; the only published binding is `127.0.0.1:13000`; only `langfuse-poc-internal` appears as the network. Do not add Traefik, a DNS record, a public port, or a live Symgov network.

## Synthetic verification

```bash
cd /data/symgov
PYTHONPATH=langfuse-poc/scripts pytest langfuse-poc/tests/test_synthetic_contract.py -q
python3 langfuse-poc/scripts/verify_poc.py \
  --base-url http://127.0.0.1:13000 \
  --env-file langfuse-poc/.env \
  --evidence-file langfuse-poc/evidence/phase-1-verification.json
```

The verifier uses only `fixtures/synthetic_events.json`, the POC public ingestion/query/delete API, and generated POC keys. It does not call OpenRouter, Gemini, LiteLLM, or any Symgov route/runner. The evidence JSON is deliberately secret-free.

## Retention and backup

POC retention is explicitly operational rather than an automatic Langfuse TTL: this Compose stack does not claim or emit an unconsumed retention environment variable. Each verifier run creates a synthetic deletion fixture, confirms it is visible, deletes it through the Langfuse public API, and polls until it is absent. This proves the deletion lifecycle, not automatic age-based expiry. Langfuse deletion is asynchronous and can take up to two minutes. The operator must use the teardown command below when the disposable POC is no longer needed; teardown removes all POC data volumes.

Do not make normal backups of this disposable POC. If a diagnostic snapshot is genuinely needed, it may include only the POC volumes and must remain locally encrypted; never mix it with Symgov production backups. The normal retention action is teardown.

## Teardown

```bash
cd /data/symgov
docker compose --project-name langfuse-poc --env-file langfuse-poc/.env -f langfuse-poc/docker-compose.yml down -v --remove-orphans
rm -f langfuse-poc/.env
```

This removes only `langfuse-poc_*` volumes and `langfuse-poc-internal`; it does not target the production Compose project, containers, volumes, MinIO, PostgreSQL, or networks.
