# Local accessibility pass (axe-core)

`scripts/a11y-axe.mjs` loads each Symgov surface in headless Chromium, runs
axe-core 4.13.0 (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`) and writes one
JSON result and one screenshot per route. The route list is Submissions,
Workspace, Reviews, the Catalog, each Details tab, the organization admin page
and the profile page.

**Never point it at production.** Run it only against the disposable local
stack described here. The script refuses any base URL or API origin that is
not `localhost` or `127.0.0.1`, and `scripts/a11y-local-api.py` refuses any
database that is not on `127.0.0.1` or `localhost`. Do not use the production
compose file, its containers or the env files under `/docker/symgov-hermes`.

## 1. Disposable PostgreSQL

`postgres:16` is the image available on the dev box. Other tags cannot be
pulled.

```sh
docker run --rm --detach --name symgov-a11y-pg \
  --env POSTGRES_PASSWORD=a11y-local-password --env POSTGRES_DB=symgov_a11y \
  --publish 127.0.0.1:55432:5432 postgres:16
docker exec symgov-a11y-pg pg_isready -U postgres -h 127.0.0.1   # repeat until accepting
docker exec symgov-a11y-pg psql -U postgres -d symgov_a11y -c "CREATE ROLE symgov_app"

export A11Y_DB=postgresql+psycopg://postgres:a11y-local-password@127.0.0.1:55432/symgov_a11y
(cd backend && env -i PATH="$PATH" HOME="$HOME" PYTHONPATH="$PWD" \
  SYMGOV_DATABASE_URL="$A11Y_DB" SYMGOV_MIGRATION_DATABASE_URL="$A11Y_DB" alembic upgrade head)
```

## 2. Seed and serve the local API

```sh
export A11Y_STATE=/path/to/scratch/a11y-state      # anything disposable
python3 scripts/a11y-local-api.py seed  --database-url "$A11Y_DB" --state-dir "$A11Y_STATE"
python3 scripts/a11y-local-api.py serve --database-url "$A11Y_DB" --state-dir "$A11Y_STATE" \
  --port 8031 --web-origin http://localhost:8030 &
curl -s http://127.0.0.1:8031/api/v1/health
```

`seed` reuses the test helpers. It publishes the four DISC-style symbols from
`tests/disc_dexpi_fixtures.py` (S-1 to S-4, with geometry, connection points
and states), the DEXPI pilot symbol TST-1 and a legacy symbol LEG-1. It also
creates three accounts, each with PIN `1234`:

| Account | Use |
| --- | --- |
| `a11y-reader@example.test` | Personal session, no roles: Catalog, Details, Profile |
| `a11y-staff@example.test` | Global admin, reviewer and submitter (Plus): Submissions, Workspace, Reviews |
| `a11y-org-admin@example.test` | Admin of the active organization ACME: organization admin page |

Before any backend module is imported, the helper runs in `local` mode with the
organization, symbol-set, platform-admin and semantic-review flags on. It also
redirects these into `--state-dir`:

- the default database and storage env files;
- the Hermes env file;
- every `/data/.openclaw` agent runtime path.

SVGs are written to and served from `<state-dir>/objects`, so previews work
without object storage. No agent, email or embedding worker starts, and Ed is
off. The Workspace and Reviews queues are therefore empty.

## 3. Build and run the pass

```sh
npm run build                       # writes dist/
SYMGOV_A11Y_EMAIL=a11y-reader@example.test SYMGOV_A11Y_PIN=1234 \
SYMGOV_A11Y_STAFF_EMAIL=a11y-staff@example.test SYMGOV_A11Y_STAFF_PIN=1234 \
SYMGOV_A11Y_ORG_ADMIN_EMAIL=a11y-org-admin@example.test SYMGOV_A11Y_ORG_ADMIN_PIN=1234 \
SYMGOV_A11Y_SYMBOL=S-1 \
node scripts/a11y-axe.mjs --base-url http://localhost:8030 --out-dir /path/to/scratch/a11y-out \
  --serve dist --api http://127.0.0.1:8031
```

With `--serve`, the script serves `dist/` at the base URL and proxies `/api/`
to the local API with the `Host` header kept. That matches production, where
the frontend and the API share one origin. Without `--serve`, point
`--base-url` at a local server that already does this. `--only catalog,profile`
limits the run to some routes. `SYMGOV_A11Y_CHROME` overrides the Chromium
binary (default: the Playwright Chromium 1223 build under `/root/.cache`).

The output directory holds `<route>.json` (violations with targets, HTML and
check data, plus the needs-review results), `<route>.png` and `summary.json`.
The script exits with:

- `1` when any route has a serious or critical violation;
- `2` on a setup failure, such as a failed sign-in, a route that never settles, or Chromium not starting;
- `0` otherwise.

## 4. Clean up

```sh
kill %1                                         # the serve process
docker rm -f --volumes symgov-a11y-pg
rm -rf "$A11Y_STATE"
```
