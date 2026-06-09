# Hannah feedback and sourcing session notes — 2026-06-07

## Status

Hannah has been updated so operators can see what happened during curation searches instead of seeing an apparently static queue.

Live service state after deployment:
- `symgov-hermes-api` was restarted and reported healthy.
- Public health endpoint `https://apps.chrisbrighouse.com/api/v1/health` returned OK during verification.
- Frontend production bundle was rebuilt by `npm run build` from `/data/symgov` and written to the live mounted `dist` directory.

## Code changes

Changed files:
- `backend/symgov_backend/routes/workspace.py`
- `backend/symgov_backend/schemas.py`
- `frontend/src/App.jsx`
- `scripts/run_hannah_curation.py`
- `tests/test_hannah_quality_filters.py`

Functional changes:
- Curation start response now includes:
  - `createdCount`
  - `skippedCount`
  - `message`
- The UI displays the curation-start message, including cooldown/no-eligible-symbol explanations.
- The Hannah candidate grid includes a `Feedback` column backed by candidate `description`.
- Candidate listing supports `description` sort/filter plumbing.
- Hannah persists operator-visible feedback rows:
  - `candidate`: possible photo/source for review;
  - `rejected`: image-like result was found but rejected by quality filters;
  - `no_candidate_found`: configured sources returned nothing image-like.
- Rejected rows carry reasons in `description` and `evidence_json.quality_reasons` and are kept at low relevance so they do not outrank real candidates.

## Source strategy changes

Hannah now combines these no-API-key / low-friction sources:
1. Wikimedia Commons API search.
2. Commons MediaSearch REST endpoint.
3. Wikipedia page search with lead-image extraction.
4. DuckDuckGo Lite scraping fallback.

The dispatcher de-duplicates candidate image URLs across sources. DuckDuckGo application/icon assets are filtered out so browser chrome does not become candidate evidence.

## Verification performed

Targeted Python tests were run inside the live API container:

```bash
docker exec symgov-hermes-api sh -lc 'cd /data/symgov && python -m unittest tests.test_hannah_quality_filters tests.test_hannah_queue_cards tests.test_hannah_candidate_persistence'
```

Observed result:
- `Ran 15 tests ... OK`

Frontend build was run:

```bash
cd /data/symgov
npm run build
```

Observed result:
- Vite build completed successfully and produced the live hashed JS/CSS bundle.

Live smoke result:
- A manual Hannah card was processed for a published Gate Valves symbol.
- `hannah_photo_candidates` gained visible rows:
  - `candidate`: 3
  - `rejected`: 3
- Public candidate API returned rows including `description` feedback and `evidence.quality_reasons`.

## Important operating interpretation

Hannah being idle is not necessarily a worker failure. If the curation start response says no cards were seeded because no public published symbols are eligible outside cooldown, Hannah is healthy but has no current work.

Current visible rows should be treated as review/diagnostic output. The smoke test also showed the current taxonomy problem: a weak canonical name such as `01-CommonValves Region 15` can attract loosely related Wikipedia candidates. That is useful feedback, but not safe enough for auto-attachment.

## Taxonomy dependency

Hannah needs better upstream taxonomy to find better photos:
- Scott should preserve/source better sheet-level context during intake and normalization.
- Libby should classify symbols into meaningful engineering names/categories/disciplines before Hannah runs.
- Strong examples: `Gate Valve`, `Solenoid Valve`, `Centrifugal Pump`; category `Gate Valves`; discipline `Piping`.
- Weak examples: region placeholders, `symbol`, `general`, or OCR-only labels with no engineering context.

When Hannah surfaces irrelevant candidates, first inspect the symbol's canonical name/category/discipline before assuming the source strategy is broken.

## Updated docs/skills

The `symgov-agent-operations` skill was updated, including:
- main Hannah curation queue-card section;
- `references/hannah-source-strategy-20260531.md`;
- `references/hannah-idle-no-queue-updates-20260607.md`;
- `references/hannah-curation-patterns-20260530.md`.

## Uncommitted state

At the end of the implementation/verification work, the repo had uncommitted modifications in the five code/test files listed above plus this session note. Review and commit when ready.

## Suggested next work

1. Improve Scott/Libby taxonomy extraction so Hannah receives meaningful equipment names and engineering categories.
2. Add a Hannah/admin retry override for selected symbols once taxonomy is repaired, rather than waiting for the 7-day cooldown.
3. Strengthen Hannah scoring with taxonomy-aware penalties for weak placeholder names.
4. Consider a review workflow that shows rejected/no-candidate rows separately from true photo candidates.

## Restart prompt

Continue the Hannah curation improvement work in `/data/symgov`. Start by loading the `symgov-agent-operations` skill and reading `docs/plans/2026-06-07-hannah-feedback-and-sourcing.md`. The last completed work added Hannah curation feedback rows, broader no-key sourcing, UI feedback, tests, and live deployment verification. Next focus: improve Scott/Libby taxonomy so Hannah searches for meaningful equipment names rather than placeholder region labels, then add a targeted retry path for repaired symbols.
