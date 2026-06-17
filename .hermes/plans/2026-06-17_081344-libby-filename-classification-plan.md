# Libby filename-derived symbol naming/classification plan

> For Hermes: planning only. No implementation in this turn.

**Goal:** Improve Libby so single-symbol submissions, especially ZIP-contained DXF/JPG pairs, can derive a strong provisional symbol name/title and discipline from filenames such as `Elec_FireAlarm_BreakGlass.dxf` before or alongside image-based classification.

**Architecture:** Add a shared filename-inference layer that turns compact engineering filenames into structured hints (`inferred_name`, `inferred_title`, `discipline_hint`, evidence/confidence). Feed those hints into Scott intake metadata and Libby classification so Libby can use filename semantics as first-class evidence, then let the vision LLM refine/confirm rather than starting blind.

**Tech Stack:** Python backend, Scott direct runner, Libby direct runner, existing review-symbol-property option helpers, repo tests under `/data/symgov/tests`.

---

## What I found in the current code

1. Filename titles are already generated upstream, but too naively.
   - `/data/symgov/backend/symgov_backend/services/external_submissions.py`
   - `candidate_title(filename)` currently just replaces `_`/`-` with spaces and applies `.title()`.
   - That means `FireAlarm` becomes `Firealarm`, so useful embedded casing is lost.

2. ZIP child tasks also generate titles naively.
   - `/data/symgov/scripts/run_scott_intake.py`
   - `build_zip_member_task(...)` sets `candidate_title` from the filename stem using the same simple underscore/hyphen replacement + `.title()` pattern.

3. Libby’s heuristic classifier mostly ignores the richer intent encoded in filenames.
   - `/data/.openclaw/workspaces/libby/run_libby_classification.py`
   - `infer_classification(...)` uses `origin_file_name`, `candidate_symbol_id`, OCR labels, and notes, but it does not perform engineering-aware filename parsing.
   - It has only broad keyword cues today (`mechanical`, `valve`, `symbol`).

4. Libby already has an image LLM enrichment step that can be improved rather than replaced.
   - Same Libby runner, `enrich_classification_with_symbol_image(...)` and `call_gemini_symbol_property_review(...)`.
   - Today the prompt asks for `name`, `description`, `category`, and `discipline`, but gives no structured filename hint/context.

5. There is no obvious dedicated persisted `title` column for symbol-review properties.
   - Review symbol properties currently persist `name`, `description`, `category`, `discipline`, `format`.
   - Practical interpretation: if Chris wants “Title property”, the least-invasive first version is to populate/upgrade `candidate_title` in intake/classification evidence and use the inferred name to seed review symbol property `name` when appropriate.
   - If a separate durable title field is truly required later, that becomes a schema/UI change and should be treated as phase 2.

---

## Requirement interpretation

My reading of the requirement is:

1. When a submission is effectively one symbol per file (or one symbol represented by a same-stem pair like DXF + JPG), the filename is often meaningful metadata, not noise.
2. Libby should parse those filenames into a likely human-readable name.
3. Libby should also extract likely discipline from filename prefixes/tokens where confidence is good enough.
4. If the image model is available, filename inference should guide the LLM prompt and be reconciled with the image result.
5. If the image result is weak or absent, filename inference should still improve `name`, `title`, aliases/search terms, and possibly discipline.
6. This should especially help ZIP-library flows like the Fire Alarms example, where Scott has already identified a paired single-symbol candidate.

I do understand the requirement, and it fits the current architecture well.

---

## Proposed implementation approach

### Phase 1: Introduce a shared filename inference helper

Create a backend utility module, likely something like:
- `/data/symgov/backend/symgov_backend/filename_inference.py`

Responsibilities:
- split filenames on `_`, `-`, whitespace, and extension boundaries;
- preserve internal camel-case segments where possible (`FireAlarm`, `BreakGlass`, `DP`, `HVAC`, `MCC`, etc.);
- detect common discipline cues from prefixes/tokens;
- generate structured output such as:
  - `raw_stem`
  - `normalized_tokens`
  - `display_tokens`
  - `inferred_name`
  - `inferred_title`
  - `discipline_hint`
  - `confidence`
  - `evidence`

Example target behavior:
- `Elec_FireAlarm_BreakGlass.dxf`
  - inferred name: `Electrical FireAlarm BreakGlass`
  - inferred title: `Electrical FireAlarm BreakGlass`
  - discipline hint: `Electrical`
- `Mech_Pressure_Relief_Valve.svg`
  - inferred name: `Mechanical Pressure Relief Valve`
  - discipline hint: `Mechanical`

Important rule: do not over-normalize engineering words into bad English. Better to preserve `FireAlarm` than degrade it to `Firealarm`.

### Phase 2: Use the helper in Scott intake metadata generation

Update both places that currently derive candidate titles from filenames:
- `/data/symgov/backend/symgov_backend/services/external_submissions.py`
- `/data/symgov/scripts/run_scott_intake.py`

Changes:
- replace naive title generation with shared filename inference;
- keep `candidate_symbol_id` behavior as-is unless there is a strong reason to change it;
- enrich normalized payloads with structured filename metadata, for example:
  - `candidate_title`
  - `filename_inference`
  - possibly `discipline_hint`

Why here:
- this gives Libby better inputs even before the image step;
- it also improves downstream UX wherever `candidate_title` is surfaced.

### Phase 3: Add a Libby filename-enrichment stage before/around the vision LLM

Update `/data/.openclaw/workspaces/libby/run_libby_classification.py`.

Add a new step in or near `infer_classification(...)`:
- inspect `origin_file_name`, `candidate_title`, `candidate_symbol_id`, package-member metadata, and companion-file context;
- run the shared filename parser;
- decide whether the filename is “strong” enough to influence naming/discipline;
- write the result into artifact evidence, for example `artifact["evidence"]["filename_inference"]`.

Expected behavior:
- if filename strongly indicates a human-readable name, set or seed:
  - `artifact["symbol_name"]`
  - aliases/search terms
  - classification summary wording
- if filename strongly indicates a discipline, raise that discipline confidence and use it unless stronger contradictory evidence exists.

### Phase 4: Pass filename hints into Libby’s image LLM prompt

Extend `call_gemini_symbol_property_review(...)` so the prompt can include:
- original filename
- inferred filename name/title
- inferred discipline hint
- note that filename-derived hints are advisory and should be corrected if the image clearly contradicts them

This is the most valuable part:
- Libby will stop treating the filename and the image as separate worlds;
- instead, the filename becomes grounded context for the vision pass.

Prompt shape should encourage this logic:
- prefer the filename-derived name when it looks like a compact symbol label;
- expand abbreviations only when confidence is high;
- preserve engineering compound words if they are likely intentional;
- return a corrected name/discipline if the image clearly disagrees.

### Phase 5: Persist and expose the inferred title/name safely

Given the current schema, I recommend this first-pass persistence strategy:
- keep inferred title in intake/classification evidence and normalized submission metadata (`candidate_title` / `filename_inference`);
- use the inferred/best-final name to seed `ReviewSymbolProperty.name`;
- use the image/filename summary for `ReviewSymbolProperty.description` only if useful;
- do not add a DB migration for a separate symbol-title column in phase 1 unless you specifically want title distinct from name throughout the product.

This keeps the change lower-risk while still delivering the classification improvement you want.

---

## Suggested decision rules

1. Apply filename-derived naming only when the submission is likely a single symbol:
   - direct single-file symbol submission;
   - ZIP child task that Scott already grouped as `paired_dxf_raster_symbol`;
   - not obvious sheet/legend/multi-symbol page cases.

2. Prefer filename discipline hints when they come from strong prefixes/tokens:
   - examples: `Elec`, `Electrical`, `Mech`, `Mechanical`, `HVAC`, `Fire`, `Piping`, `Instr`, etc.

3. Keep confidence tiering explicit:
   - high: same-stem paired single symbol + strong filename cue;
   - medium: single file with plausible filename cue;
   - low: filename mostly generic (`symbol`, `sheet`, `drawing`, `legend`, `image001`).

4. Never let filename parsing clobber obviously better image evidence.
   - The final result should be reconciliation, not blind filename trust.

5. Record provenance of the inference.
   - We want future debugging to show whether a name/discipline came from filename, image, or human review.

---

## Files likely to change

Primary:
- `/data/symgov/backend/symgov_backend/services/external_submissions.py`
- `/data/symgov/scripts/run_scott_intake.py`
- `/data/.openclaw/workspaces/libby/run_libby_classification.py`
- `/data/symgov/backend/symgov_backend/routes/workspace.py` (only if review-name seeding needs adjustment)
- `/data/symgov/backend/symgov_backend/workspace.py` (mirror if both modules must stay aligned)

New helper module:
- `/data/symgov/backend/symgov_backend/filename_inference.py`

Tests:
- `/data/symgov/tests/test_zip_phase2.py`
- `/data/symgov/tests/test_review_symbol_name_defaults.py`
- `/data/symgov/tests/test_libby_symbol_vision.py`
- likely new test file: `/data/symgov/tests/test_filename_inference.py`

---

## Test plan

1. Unit-test filename parsing:
   - `Elec_FireAlarm_BreakGlass.dxf`
   - `Elec_FireAlarms_Sounder.dxf`
   - `Mech_Pressure_Relief_Valve.svg`
   - weak/generic names like `sheet_01.jpg`

2. Regression-test Scott ZIP child task metadata:
   - paired DXF/JPG tasks should carry improved `candidate_title` and `filename_inference`
   - generic sheet images should not be over-promoted to single-symbol names

3. Regression-test Libby heuristic classification:
   - filename-only case without image still improves symbol name / discipline
   - image+filename case passes hints into LLM path
   - weak filename case does not override better evidence

4. Review-name seeding tests:
   - review symbol property default name should prefer inferred/humanized symbol names over package IDs

5. Existing smoke tests:
   - `tests/test_zip_phase2.py`
   - `tests/test_libby_symbol_vision.py`
   - any impacted review-workspace tests

---

## Risks and tradeoffs

1. Overfitting to filename conventions
   - Some filenames are junk. We need explicit confidence gating.

2. Abbreviation expansion mistakes
   - `Elec` -> `Electrical` is probably safe.
   - Others may not be. Keep the mapping table conservative.

3. Live runner is outside the repo
   - Current Libby runner path is `/data/.openclaw/workspaces/libby/run_libby_classification.py`.
   - If we implement there, we should either preserve/sync it into the repo or explicitly document the live-file delta before closing.

4. “Title property” ambiguity
   - Today the system clearly has `candidate_title` and symbol `name`, but not an obvious dedicated review-symbol `title` field.
   - I recommend phase 1 uses `candidate_title` + `name` rather than a migration.

---

## Open questions I would keep narrow before coding

1. Do you want a conservative discipline map in phase 1, or should Libby only infer discipline from a small approved prefix list initially?
2. Should `Elec` always expand to `Electrical`, or do you prefer preserving the exact token unless an LLM confirms expansion?
3. Do you want “Title” to remain an intake/classification field (`candidate_title`) for now, or do you want a distinct durable symbol-title property introduced product-wide?

My recommendation for implementation is:
- conservative prefix mapping;
- preserve engineering compounds like `FireAlarm` and `BreakGlass` rather than “prettifying” them too aggressively;
- phase 1 without a schema migration.

---

## Recommended implementation order

1. Build and test shared filename inference helper.
2. Replace naive `candidate_title` generation in external submissions and Scott ZIP child tasks.
3. Add Libby filename-enrichment evidence + discipline/name seeding.
4. Feed filename hints into the Libby image LLM prompt.
5. Add/adjust review-name seeding only where still needed.
6. Run targeted regression tests.

---

## Expected outcome

After this change, cases like the Fire Alarms ZIP should arrive at Libby with materially better starting metadata, and Libby should be able to classify a single symbol more accurately even before Daisy/human review. That should improve the whole downstream chain because better early naming and discipline assignment will make review, publication, and later curation more reliable.
