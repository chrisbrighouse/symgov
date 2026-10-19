# Hannah: concept-level photo curation (plan)

*Date: 2026-10-19. Based on `origin/main` at `f099edf` (live as `stage11-f099edf`). The only Alembic head is `20261015_0073`. Local `main` is 25 commits behind `origin/main`, so branch from `origin/main`. Line numbers refer to `origin/main` unless marked.*

## Decisions this plan rests on (Chris, 2026-10-19)

- **One photo set per semantic concept.** Every symbol assigned to a concept shows that concept's photos.
- **Any source and any licence.** The photos are illustrative only. They are not downloadable and not part of the licensed record. We still show the author and licence when the source gives them, because it costs nothing.
- **Automatic attachment is allowed later**, once the vision check has been calibrated. The photos are less critical than symbol graphics.
- **The vision check runs through the existing OpenRouter connection.** There is no direct Claude API use. Hannah gets her own `featureModels` entries (`services/llm.py:156`).
- **All old Hannah photos are removed.** Done 2026-10-19; see §0.
- **Hannah stays off the automatic worker until this process works.** After that she is re-enabled.

## 0. Where things stand

**Done on 2026-10-19:**
1. `hannah` was removed from `SYMGOV_AGENT_WORKERS` in `/docker/symgov-hermes/docker-compose.yml`. The backup is `docker-compose.yml.pre-hannah-off-20261019T161610Z`. Re-enabling means adding `hannah` back and recreating `symgov-api`.
2. `manage_symgov.py purge-hannah-photos --apply` ran in production at 16:21 UTC. It removed:
   - 14 MinIO objects and their 14 attachment rows;
   - 1,367 candidate rows;
   - 929 curation-state rows.

   It also cancelled 12 unfinished queue cards and wrote one `hannah_photos_purged` audit event. The code (`backend/symgov_backend/hannah_photo_purge.py`, `tests/test_hannah_photo_purge.py`) is uncommitted on local `main` and moves onto this work's branch.

**Why the old Hannah failed** (`scripts/run_hannah_curation.py`):
- She searched per symbol, using only name, category and discipline.
- She judged candidates by keyword overlap in text metadata and never looked at the image.
- She attached images automatically with no human step.

**Production facts this plan is sized on:**

| Measure | Value |
|---|---|
| Public published symbols | 926 |
| …with a verified primary semantic concept | 843 |
| Distinct concepts on those symbols | 515 |
| Concepts with `concept_kind = physical_equipment` | 214 (456 symbols) |
| Concepts with `concept_kind = other` | 262 (303 symbols) |
| Concepts with `function` / `annotation` / `connection` | 27 / 7 / 5 |

**Three findings shape the design:**
1. **`concept_kind` is not reliable enough to decide what is photographable.** The 262 `other` concepts came from the Commons import and are mostly physical things, such as `CoolingTower`, `Manhole`, `FluidizedBed` and `SeparatorCycloneType`. Hannah needs her own photographable decision, and must not edit `concept_kind`, which is under platform concept governance.
2. **Preferred names are CamelCase codes.** Usable search terms have to come from the definition and aliases. For example, "Separator, cyclone type" gives "cyclone separator".
3. **`llm_usage_events` restricts `use_case` with a CHECK constraint.** Migration 0067 had to add `ed_guru`, so Hannah's use cases need a migration too.

## 1. Pipeline

There is one queue card per concept, using the existing queue, runtime bridge and stop pattern.

```
public symbol ─┐
               ├─> concept target ─> Wikidata link ─> candidates ─> pre-filter ─> vision check ─> proposed (top 2)
verified concept┘     (photographable?        (QID, P18,      (Commons category,       (OpenRouter)       │
                       search terms)           Commons cat.)   P18, Commons search,                       ▼
                                                                Wikipedia, web)               steward approve / reject
                                                                                               (later: auto ≥ threshold)
                                                                                                          │
                                                                                     Symbol Details + Catalog camera icon
```

### 1.1 Concept targets
A target is a semantic concept with a verified primary assignment on a public, active projection. These rules use the visibility floor that `load_eligible_symbols` already applies.

- **Deciding whether a concept is photographable:**
  - `physical_equipment` is **yes**.
  - `function`, `state`, `property`, `action`, `annotation`, `connection` and `safety_function` are **no**.
  - `other` is classified by a cheap text-only LLM call from the definition and aliases, giving yes, no or unsure.
  - A steward can override any of these.
- **Search terms.** The same LLM call returns one to three plain English names, such as "cyclone separator". A deterministic CamelCase split is the fallback.
- **Wikidata link.** `wbsearchentities` returns up to 5 hits with descriptions, and the LLM picks one QID or none. We then read:
  - P18, the item's image;
  - P373, its Commons category;
  - its English Wikipedia sitelink.

  The QID is stored on Hannah's target row only (Q3).

### 1.2 Candidate sources (in order, stopping at 12 per concept)
1. The Wikidata P18 image.
2. Files in the Commons category, namespace 6, at most 30. One level of subcategories is included if the category is small.
3. A Commons search on each search term.
4. The lead image of the Wikipedia sitelink.
5. *(Deferred, Q2.)* A web image search, used only for concepts that still have nothing. It is not built in this plan.

For each candidate we keep:
- source and source URL;
- image URL;
- title;
- author, licence and licence URL (from Commons `extmetadata` Artist, LicenseShortName and LicenseUrl), where known.

### 1.3 Cheap pre-filters (no LLM)
A candidate is dropped before the vision check if:
- it is not JPEG, PNG or WebP (SVG, PDF and DjVu are dropped);
- its shorter side is under 400 px, or its aspect ratio is extreme;
- it is a duplicate by URL or by SHA-256 of the downloaded thumbnail.

### 1.4 Vision check
We send a roughly 768 px thumbnail as a base64 data URL, through `request_llm_completion` (`services/llm_router.py:445`). Settings:
- model `resolve_model_for_feature("hannah_vision")`;
- `response_format = json_object`, temperature 0;
- `use_case="hannah_vision"`, `agent_slug="hannah"`, and `initiator_kind` set to `scheduled_worker` for worker runs or `admin` for runs started from the Workspace or CLI. These are existing values, so no new kind is needed.

The prompt carries the concept name, definition and search terms. The model returns:

```json
{"is_photograph": true, "shows_concept": "yes|partial|no", "depicts": "…",
 "is_diagram_or_symbol": false, "text_or_watermark_heavy": false,
 "quality": 1-5, "confidence": 0.0-1.0, "reason": "…"}
```

- **Hard rejects:** `is_photograph = false`, `is_diagram_or_symbol = true`, or `shows_concept = "no"`.
- **Score:** `confidence × quality/5`, with a 0.7 multiplier for `partial`.
- **Selection:** the top 2 become `proposed`.

The raw verdict is stored with each candidate, so later threshold changes need no new model calls.

### 1.5 Review and attachment
- **Approve.** The image is fetched at up to 1,600 px and stored under `hannah/concept-photos/<concept_code>/<photo_id>.<ext>`. The record notes who approved it and when.
- **Reject.** It is recorded and never proposed again for that concept.
- **Remove.** Takes an approved photo back down.
- **Automatic mode (WP-H6).** A candidate scoring at or above the calibrated threshold is approved by `hannah`. It stays visible in the tab and can be removed.

### 1.6 Display
- **Lookup.** `load_supplemental_photos` (`routes/published.py:426`) changes to: symbol → current revision → verified primary concept → approved photos, at most 2, steward's order first and then score. The response shape stays the same, so the frontend needs no data changes.
- **Credit line.** `SupplementalPhotoStrip` (`App.jsx:4478`) gets a small credit line under each photo: author, licence and source link, each only when known.
- **Preview route.** A new route `GET /published/concept-photos/{photo_id}/preview` serves a photo only if it is approved and its concept has at least one public active projection. The existing preview route is retired.
- **Catalog camera icon.** `_PHOTO_COUNT` (`catalog_browse_search.py:87-94`) changes to count by concept.
- **Organisation-private rows** keep `supplementalPhotos: []` (`published.py:273`).

### 1.7 Changes from the H3 trials (2026-10-20, production dry runs, `anthropic/claude-haiku-5.5`)

- **Reply limit:** the vision call needs `max_tokens` of about 1,500. At 400, the model's hidden reasoning used up the allowance, and about a quarter of the replies were cut off mid-JSON.
- **Captions:** the model is shown the file's own title and Commons description as a caption, marked as possibly wrong. It decides on photograph-ness from the image alone.
- **Broad categories:** head-noun Wikidata links give broad categories ("Machines", "Filters", "Pipes"), and their files come back alphabetically. The run now:
  - takes at most 20 files from a category;
  - vision-checks the Wikidata image first, then files whose title or description shares a *distinctive* search word. Words also in the category name don't count, unless nothing else is left.
  - checks at most 3 unmatched files per run and filters the rest as `no_term_match`.
- **Proposal threshold:** set to 0.35, because proposals go to a steward. Automatic approval gets its own calibrated threshold in WP-H6.
- **Results:** on 5 sampled concepts, duct, siphon, HEPA filter and magnetic separator each got two sensible proposals. The bag filling machine got none, because Commons has no photo of one. That is the case the deferred web source (Q2) would cover.

## 2. Work packages

### WP-H1: Schema (S). Migration `20261020_0074_hannah_concept_photos`

**`hannah_concept_targets`**

| Columns | Notes |
|---|---|
| `id`, `semantic_concept_id` | `semantic_concept_id` is unique, FK RESTRICT |
| `photographable` | `yes` / `no` / `unsure` |
| `photographable_basis` | `concept_kind` / `llm` / `steward` |
| `search_terms_json` | |
| `wikidata_qid`, `commons_category`, `wikipedia_url` | |
| `link_confidence` | |
| `status` | `pending` / `searched` / `no_candidates` / `has_photos` / `excluded` |
| `attempt_count`, `last_attempt_at`, `next_eligible_at` | |
| `notes_json`, `updated_by_user_id` | |
| timestamps | |

**`hannah_concept_photos`**

| Columns | Notes |
|---|---|
| `id`, `semantic_concept_id` | |
| `source`, `source_url`, `image_url` | unique (`semantic_concept_id`, `image_url`) |
| `title`, `author`, `license_label`, `license_url` | |
| `width`, `height`, `sha256` | |
| `vision_model`, `vision_verdict_json`, `score` | |
| `status` | `candidate` / `proposed` / `approved` / `rejected` / `removed` / `filtered` |
| `rank` | |
| `object_key`, `content_type`, `size_bytes` | |
| `decided_by_user_id`, `decided_by_agent`, `decided_at` | |
| `queue_item_id`, `first_seen_at`, `last_seen_at` | |

The photo stays in its own row, with no `attachments` row: `attachments` parents are revisions, and these belong to concepts.

**Other changes in the same migration**
- Extend the `llm_usage_events` checks: `use_case` gains `hannah_vision` and `hannah_targets`, and `agent_slug` gains `hannah`. The telemetry allowlist in `llm_telemetry.py` changes to match.
- Bump the sole-head assertions (seven files, per the ICS memory note).

**Tests:** ORM round-trip, plus a PostgreSQL migration upgrade/downgrade test.

### WP-H2: Target builder (M)
- New module `backend/symgov_backend/hannah_targets.py`.
- CLI `manage_symgov.py hannah-build-targets [--limit N] [--apply]`, which is a dry run by default.
- **Idempotent:** a re-run only fills targets that are new or still `pending`, and never overwrites a `steward` basis.
- **LLM calls:** `featureModels["hannah_targets"]`, which can be a cheap text model.
- **Tests:**
  - photographable mapping per `concept_kind`;
  - CamelCase fallback;
  - Wikidata pick parsing (stubbed HTTP and stubbed LLM);
  - the visibility floor (private-only concepts are excluded).

### WP-H3: Concept runner (L)
- Rewrite `scripts/run_hannah_curation.py` to take one concept per card: sources (§1.2), then pre-filters (§1.3), then the vision check (§1.4), then proposals.
- **Kept from the old runner:**
  - runtime artifacts (`hannah_curation_report`, schema `hannah-curation-v2`);
  - per-call throttling (Commons asks for 1–1.5 s);
  - honouring 429 responses with a `retry_later` result.
- **Cooldown:** 30 days for `no_candidates`, set through `next_eligible_at`.
- **Seeder:** cards are per concept, with `source_type = concept_photo_curation`.
- **Budget guard:** `SYMGOV_HANNAH_MAX_VISION_CALLS_PER_DAY`, default 500, checked against `llm_usage_events`.
- **Calibration CLI:** `manage_symgov.py hannah-run --concept <code>|--sample N` runs without the worker and prints the report.
- **Tests:** source parsing from recorded fixtures, pre-filters, verdict parsing and hard rejects, scoring, cooldown, budget guard. There are no live network calls in tests.

### WP-H4: Workspace review (M)
- **Routes** (admin only, recorded in the audit trail):
  - `GET /workspace/hannah/concepts`: targets with their proposed and approved photos, filterable by status.
  - `POST /workspace/hannah/photos/{id}/approve | reject | remove`.
  - `PATCH /workspace/hannah/concepts/{id}`: photographable override, search-term edit, re-queue.
  - `POST /workspace/hannah/concepts/{id}/search`: run now for one concept.
- **Fix the stop route.** It filters on the wrong `source_type` (`routes/workspace.py:3119` on local `main`), so the Stop button fails for every queued card. It should accept the new `concept_photo_curation` cards.
- **Curation tab rebuild:**
  - one row per concept, showing the human-readable concept name, symbol count and example symbol IDs;
  - a thumbnail strip of proposals with the verdict reason and credit;
  - Approve, Reject and Remove buttons, keyboard reachable with visible focus.
- **Tests:** route tests (admin and non-admin), state transitions, frontend tab tests.

### WP-H5: Public display (M)
- Concept-based `load_supplemental_photos`, the new preview route, `_PHOTO_COUNT`, and the credit line in `SupplementalPhotoStrip`.
- **Tests:**
  - a public symbol shows its concept's approved photos;
  - a private-only concept shows none, and the preview route returns 404;
  - a rejected or removed photo is never served;
  - two symbols on one concept share photos;
  - the tenant matrix files need updating (see the WP1.6 memory).

### WP-H6: Calibration, automation, re-enable (S, plus Chris's time)
1. Run `hannah-run --sample 40` on concepts of mixed difficulty with `anthropic/claude-haiku-5.5`. Chris approves or rejects in the tab, and those decisions are the labels.
2. Re-score the same candidates with `google/gemini-3.1-flash-lite`. The stored verdicts make this one CLI pass.
3. Pick the model, and pick the threshold that would have produced no approvals Chris rejected.
4. Turn on auto-approve (`SYMGOV_HANNAH_AUTO_APPROVE_MIN_SCORE`, unset means off).
5. With Chris's approval, add `hannah` back to `SYMGOV_AGENT_WORKERS`.

### WP-H7: Retire the old path (S, later release)
- Drop `hannah_photo_candidates`, `hannah_symbol_curation_states` and the unused `hannah_curation_source_sites`.
- Delete the old runner functions and the DuckDuckGo Lite scraper.
- Remove the old preview route.

**Order:** H1 → H2 → H3 → (H4 ∥ H5) → H6 → H7. H1–H5 can ship in one release with the worker still off. Nothing is public until a photo is approved.

## 3. Cost estimate (illustrative)

These figures use OpenRouter list prices read on 2026-10-19 and assume:
- about 400 concepts in scope (214 physical, plus most of the 262 `other` ones);
- at most 12 vision checks per concept;
- about 1,000 input tokens per image (thumbnail plus prompt) and about 150 output tokens.

| | Haiku 5.5 ($0.10 / $0.50 per M) | Gemini 3.1 Flash-Lite ($0.25 / $1.50 per M) |
|---|---|---|
| Full first pass, about 4,800 checks | about $0.85 | about $2.30 |
| Target classification, about 400 text calls | under $0.10 | under $0.25 |

Re-runs after the first pass cost far less, because rejected candidates are never checked again.

## 4. Settled questions (Chris, 2026-10-19)

- **Q1. Photographable `other` concepts.** The model decides and a steward can override in the tab.
- **Q2. Web image source.** Start with Wikimedia only (sources 1–4). Measure how many concepts end up empty before choosing a web source.
- **Q3. Wikidata QIDs.** Stored on Hannah's own target rows, not in `concept_external_references`.
- **Q4. Photos per concept.** 2.
- **Q5. Symbols with no concept.** They get no photos until they are given a concept. There is no per-symbol fallback.

## 5. Production steps (each needs Chris's explicit approval at the time)

- Deploying the H1–H5 release, which includes migration 0074.
- Running `hannah-build-targets --apply` and the calibration sample in production.
- Setting the `featureModels` entries `hannah_vision` and `hannah_targets` in the LLM settings.
- Re-enabling the worker (WP-H6 step 5).
