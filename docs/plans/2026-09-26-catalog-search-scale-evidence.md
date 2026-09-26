# Catalog and Set tab search: scale evidence

Step 3 of the Set/Catalog tab design, recorded 2026-09-26.

## What was measured

`tests/test_catalog_search_scale_postgresql.py`, which is opt-in (`SYMGOV_CATALOG_SCALE_TEST=1`), run against a disposable `postgres:16` container on the 4-core development host. That host also runs Langfuse and `symgov-hermes-api`. The data is synthetic and exists only in the container:

- 50,000 public symbols across 10 packs, 1 in 20 also published in a second pack (53,001 Catalog rows including private ones), cycling through the product's real category and discipline codes and seven download-format mixes;
- 501 organization-wide private symbols in one organization (500 seeded, one through the real approval API);
- one Symbol Set at the product's ceiling of 1,000 items, built through the real items API (Chris kept the 1,000-item cap on 2026-09-25);
- a plain organization member making every request through the HTTP endpoint.

Each query gets one discarded warm-up call (its time is reported as the first call), then 15 timed calls.

Targets are WP11.5's P95 goals. The Catalog tab has to answer within 1.5 s. The Set tab is held to the effective-palette goal of 1 s, which Chris chose on 2026-09-26 over the 750 ms Project/set switch goal.

## Results (passing run)

| Query | P95 | Median | First call | Rows |
|---|---|---|---|---|
| Catalog, first page | 0.97 s | 0.87 s | 0.99 s | 53,001 |
| Catalog, text search | 0.99 s | 0.95 s | 0.96 s | 4,666 |
| Catalog, rare text search | 1.03 s | 0.96 s | 0.95 s | 1 |
| Catalog, one discipline | 1.00 s | 0.87 s | 0.96 s | 5,861 |
| Catalog, two categories and a format | 1.48 s | 1.01 s | 4.05 s | 3,214 |
| Catalog, sort by name | 1.03 s | 0.97 s | 1.02 s | 53,001 |
| Catalog, deep page (800) | 1.09 s | 1.00 s | 0.97 s | 53,001 |
| Catalog, favourites | 1.21 s | 0.93 s | 0.91 s | 1 |
| Catalog, column filter | 0.96 s | 0.92 s | 0.94 s | 16 |
| Catalog, preferred formats | 0.94 s | 0.91 s | 0.89 s | 53,001 |
| Set, first page | 0.78 s | 0.61 s | 0.64 s | 1,501 |
| Set, one group | 0.80 s | 0.63 s | 0.54 s | 250 |
| Set, text search | 0.92 s | 0.67 s | 0.62 s | 567 |
| Set, sort by name | 0.80 s | 0.68 s | 0.61 s | 1,501 |

Other measurements:

- Seeding: 182 s.
- `backfill-catalog-facets --apply` filled all 50,501 facet rows in 16 s.
- The search right after one revision changed took 1.48 s, because it recomputes that row and builds the candidates twice.
- Without the backfill, the first search computes every row itself (15.7 s in a profiling run). That is why the deploy script now runs the backfill.

**Variance.** P95 figures moved by up to 0.6 s between otherwise identical runs, because the host is shared. Medians stayed between 0.84 s and 1.05 s for the Catalog tab and between 0.60 s and 0.71 s for the Set tab across the last four runs. The 4.05 s first call and the 1.48 s P95 of the two-categories query were one such spike: the same query's median was 1.0 s, and its P95 was 0.94–1.02 s in the other runs. A single spike on a 15-sample P95 can cross the target, so treat a lone failure of this test as noise until it repeats.

## What the first runs exposed, and the fixes (commit `b698988`)

The first two runs timed out at 50 minutes.

1. **`PUBLISHED_SYMBOLS_SQL` and `PUBLIC_SYMBOL_ELIGIBILITY_SQL` join the publication tables twice**: once directly, and once through `active_public_symbol_projections`. The planner estimates a single row for the result and chooses nested loops. The joins on `published_pages.current_symbol_revision_id` and `pack_entries.symbol_revision_id` have no index, so each probe scans the whole table, about 8 ms per symbol.
   - The Catalog build took 988 ms with nested loops and 336 ms with hash joins.
   - A 1,000-item Set build took 40 s.
   - `current_public_symbols` took 7.7 s for 1,000 symbols, and 0.23 s with hash joins. That lookup also serves the existing palette route, which took 8.5 s for a 1,000-item set at this size.

   The fix switches nested loops off only for those statements: the search's single candidate build, and eligibility lookups of 50 or more symbols. Small lookups keep nested loops, which cost a few milliseconds each.
2. **Indexes on those two columns were tried and rejected.** They let the planner pick nested loops for the full public scan, which then did not finish.
3. **JIT**: the counts query unnests every facet array, the planner assumes 100 elements per array, and the cost crosses the JIT threshold. About 700 ms went on compiling a query that runs in about 400 ms. JIT is off for the search transaction.
4. **Visibility evaluated once**: the search builds a temporary table of candidate rows, with their facet values joined in, and runs the stale check, the counts and the page query against that table.
5. **Set tab members**: their publication rows are fetched directly, under the projection view's conditions, rather than by scanning every public row (0.41 s saved). The rows a page returns are still loaded through `PUBLISHED_SYMBOLS_SQL`.

## Deploying

`scripts/deploy-release.sh` now runs `manage_symgov.py backfill-catalog-facets --apply` after the migration and before the switch-over:

- It fills the store before the new code serves a search.
- It runs as the app role, so a missing privilege on `catalog_symbol_facets` stops the release while the old code, which never reads the table, is still serving. The same check applies to migration `20260925_0063`'s tables, whose app-role privileges have not been exercised in production yet.
- It does nothing when no facet rows are missing.

## Rerunning

```sh
SYMGOV_CATALOG_SCALE_TEST=1 SYMGOV_TEST_POSTGRES_IMAGE=postgres:16 \
SYMGOV_CATALOG_SCALE_EVIDENCE=/tmp/catalog-scale.json \
  uv run --isolated --with-requirements backend/requirements.txt \
  --with-requirements backend/requirements-test.txt \
  env PYTHONPATH=backend SYMGOV_ENVIRONMENT=test python -m pytest -s tests/test_catalog_search_scale_postgresql.py
```

The test takes about 7 minutes. If it is killed, its container is left behind; remove it with `docker rm --force --volumes <name>`.
