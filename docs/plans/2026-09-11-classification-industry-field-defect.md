# `classification_records.industry` is a dead field

Recorded 2026-09-11, while assessing sources for the Industry/Application
classification scheme that SM-P0-07 (§9.3) needs. Raised as a defect by
Chris on the same day.

This is a data/product defect, not a UI gap: the field exists end to end,
is populated on every classification run, and is returned by the review
API — it simply cannot carry useful information in its current form.

## What was found

`industry` has exactly one writer and no editor.

| Stage | Location | Behaviour |
|---|---|---|
| Produced | `scripts/run_libby_classification.py` | four hard-coded heuristic branches |
| Persisted | `backend/symgov_backend/runtime.py:2829` | `record.industry = durable_record.get("industry")` |
| Returned | `workspace.py:1030`, `routes/workspace.py:1613`, `schemas.py:679` | read-only in the review payload |
| Edited | nowhere | no write path, no frontend reference |

`run_libby_classification.py` is the production Libby runner
(`agent_queue_worker.py:48`) and the sole producer of the
`classification_record` durable kind. The complete set of values it can
emit is:

| Value | Set at | Condition |
|---|---|---|
| `general_industry` | `:469` | the default, used whenever no cue matches |
| `mechanical_engineering` | `:532` | mechanical cues in filename/notes |
| `process_engineering` | `:542`, `:552` | valve cues, or a generic symbol sheet |
| `architectural` | `:572` | authoritative BTX subject match |

Three consequences, each verified rather than inferred:

1. **Three of the four values are discipline names, not industries.**
   The field's real axis is engineering discipline, which the catalogue
   already has as `CATALOG_DISCIPLINE_ORDER` / the `ENGINEERING-DISCIPLINE`
   classification scheme seeded by `20260909_0051`.
2. **The fourth is already treated as an absence of a value.**
   `automation_policy.py:17` lists `general_industry` in
   `PLACEHOLDER_DISCIPLINES`, so the codebase itself does not regard the
   default branch as a classification.
3. **Nothing can correct it.** The vision LLM review never writes the
   field — `parse_symbol_property_response` (`:118`) returns only `name`,
   `description`, `category` and `discipline`, and
   `apply_symbol_property_description` (`:132`) applies only those. The
   review UI exposes `category`, `discipline` and `format` for editing;
   `industry` appears nowhere in `frontend/src`. A wrong value written at
   intake stays wrong.

## Why it surfaced now

§9.3 of the Semantic Model & Classification Change Specification maps
`industry` to a `SymbolRevisionClassificationAssignment` against an
Industry/Application scheme. That scheme does not exist, and
`classification_schemes.py:78-82` already records why: there is no
hard-coded order to seed it from, and inventing one is forbidden by
`CLAUDE.md`. Migration `20260909_0052` reached the same conclusion from
the other direction — CFIHOS cannot supply it, being a single-industry
standard by construction. Re-checked 2026-09-11 against all 21 CSVs in
`CORE-CFIHOS-CSV-v2.0.zip`: no industry, sector, domain or application
column exists in the release.

The seed-from-existing-data option is therefore also closed. The distinct
values in the column are the four above, so a scheme derived from them
would be three disciplines and a placeholder.

## Decisions taken 2026-09-11 (Chris)

* **No Industry/Application scheme is seeded in SM-P0-07.** The field is
  preserved in `evidence_json` with an explicit "no governed vocabulary
  exists" marker plus a line in the mapping report, which satisfies
  §16.1's criterion that no §9.3 field is silently dropped. This follows
  the precedent of `20260909_0052`, which left the gap visible rather
  than filling it.
* **ICS (International Classification for Standards, ISO, edition 7) is
  the intended source** when the scheme is eventually created. It is a
  taxonomy of industrial and technological fields of application (99
  divisions, ~40 in use, three levels), its stated purpose is to
  structure catalogues of standards — which is what §7.6 asks a scheme to
  be — and every standard SymGov models already carries ICS codes, so
  `industry` can be derived from `standards_source` provenance rather
  than guessed. It is not one of the twenty sources in the Engineering
  Symbol Sources research report; that report surveys symbol corpora and
  plant-object backbones, and contains no industry taxonomy at all.
* **Chris is investigating ICS licensing separately.** ISO holds
  copyright in ICS (ISBN 978-92-67-10652-6) and the publication page
  refuses automated fetches, so the terms for embedding node labels in
  the product UI and API are unconfirmed. The scheme should not be
  created until that returns.

## Wanted

Not scoped, and deliberately not folded into SM-P0-07:

1. Decide whether `industry` is an industry axis at all, or whether the
   field should be retired in favour of the discipline scheme it
   currently duplicates.
2. If it stays, give it a real vocabulary (ICS, pending licensing) and a
   writer that can select from it — the current four branches cannot.
3. Give reviewers a way to correct it, as they can for `category` and
   `discipline`.
