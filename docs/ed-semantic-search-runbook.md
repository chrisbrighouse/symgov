# Ed meaning-based Catalog search: runbook

Ed's `search_catalog` tool matches the words a person types. This adds a
second, meaning-based pass so "a valve that stops backflow" finds a check
valve. It ships **dark**: nothing changes until the index is built and the
flag is turned on.

## What it is

- One vector per **currently published public** symbol revision, in
  `catalog_symbol_embeddings` (migration `20261010_0071`). Vectors are
  little-endian float32 bytes, unit length, keyed by `(revision, model)`. There
  is no pgvector: production runs plain `postgres:16`, and a few thousand rows
  are scored in the application with numpy (plain Python if numpy is absent).
- Text embedded per symbol: name, description, category, discipline, use
  cases, keywords, pack and the Catalog's own filter labels. No identifiers,
  URLs, people or storage keys.
- **Private symbols are never indexed and never returned as similar matches.**
  Organization-wide private symbols are still found by the keyword search, as
  before.
- The index only ranks. Every candidate is re-checked against the live
  public-Catalog rule (`PUBLISHED_SYMBOLS_SQL`) and the caller's filters before
  it is shown, so a stale vector can never make a symbol visible.
- Keyword matches come first. The meaning-based pass runs only when the keyword
  search filled fewer than `limit` results and the query has content words, and
  its matches are labelled `similar` with a similarity score. If embedding
  fails, Ed gets the keyword result and `semantic_search: "unavailable"`.
- Each question that reaches the meaning-based pass makes one embedding call,
  recorded in the usage ledger as use_case `catalog_embedding`, request_kind
  `embedding`, with no text in the event.

## Settings

| Variable | Default | Meaning |
|---|---|---|
| `SYMGOV_ED_SEMANTIC_SEARCH_ENABLED` | off | Turns the meaning-based pass on. Read at start, so changing it means a restart. |
| `SYMGOV_CATALOG_EMBEDDING_MODEL` | `baai/bge-m3` | An OpenRouter embedding model. Not taken from the chat model settings. |
| `SYMGOV_ED_SEMANTIC_MIN_SIMILARITY` | `0.50` | Cosine below which a match is not reported. bge-m3 scores compress high: in a first live probe a true match scored 0.70, a related symbol 0.58 and unrelated text 0.31 to 0.44, so 0.50 is a starting point. **Calibrate it** (below). |

The provider key is the one the chat calls already use
(`SYMGOV_OPENROUTER_API_KEY` or the Hermes profile `.env`).

## Activating (each step needs Chris's approval: it writes to the database and calls the provider)

Run the commands in the API container from the release's `backend` directory:

    docker exec -w /data/symgov-releases/stage11-<sha>/backend symgov-hermes-api \
      python3 manage_symgov.py catalog-embeddings <action> ...

1. **Deploy** the release. The migration adds the table; with the flag off,
   Ed behaves as before.
2. **Status** (read-only): `catalog-embeddings status` shows published versus
   indexed counts.
3. **Dry run**: `catalog-embeddings index` shows how many symbols would be
   embedded and a sample of the text. Nothing is written or sent.
4. **Build**: `catalog-embeddings index --apply`. It embeds in batches of 32,
   commits per batch, and can be re-run safely: only new or changed text is
   embedded, and revisions that left the public Catalog are pruned. Cost is
   cents for the whole catalog.
5. **Calibrate**: `catalog-embeddings probe "<query>" --top 10` prints the
   nearest symbols with scores, with no floor. Try real questions (a synonym, a
   vague description, an off-topic word such as "teleporter") and set
   `SYMGOV_ED_SEMANTIC_MIN_SIMILARITY` just above where noise starts.
6. **Enable**: set `SYMGOV_ED_SEMANTIC_SEARCH_ENABLED=true` (and the minimum
   similarity) in `/docker/symgov-hermes/docker-compose.yml`, then
   `up -d symgov-api`. Never `restart`.

## Keeping it current

After new symbols are published (or a description changes), re-run
`index --apply`. Until then a new symbol is found by keyword only. Changing the
model is a new index: run `index --apply --model <new>` after setting
`SYMGOV_CATALOG_EMBEDDING_MODEL`; the old model's rows can be deleted with
`DELETE FROM catalog_symbol_embeddings WHERE model = '<old>'`.

## Turning it off

Unset or set the flag to `false` and recreate the API container. The table can
stay. Rolling the release back needs no schema change: the old code never reads
the table.

## Limits

- Public symbols only, by decision. A private-symbol index needs its own
  per-organization design.
- The index is a snapshot: it is not updated when a symbol is published.
- Similarity floors differ between models. Re-calibrate after any model change.
