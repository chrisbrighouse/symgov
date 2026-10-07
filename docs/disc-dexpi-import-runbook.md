# DISC DEXPI symbol library import runbook

The DISC DEXPI library (pack `disc-dexpi`, "DISC DEXPI symbol library (DISC Profile 0.6.3)") is imported by
`python -m symgov_backend.disc_dexpi_ingest`, from an extracted import package. It follows the DEXPI
TrainingTestCases pilot importer (`dexpi_ingest`) and adds a dry run, revisions on change, and the checks below.

Nothing here is run without the owner's approval of that specific operation: a backup first, the migration,
the import, the rights approval and publication are each their own step.

## The package

`~/imports/disc_dexpi/` holds `manifest.json`, `svg/`, `dexpi_class_scheme.json`, `held_back.csv`, `report.json`,
`config.json` and `convert_disc.py`. The importer refuses a package that disagrees with itself (a digest, a count,
a held-back symbol present, the attribution wording not identical everywhere).

## Order

```
python -m symgov_backend.disc_dexpi_ingest apply   --package DIR --dry-run --output dry.json   # reads only; exit 2 if blocked
python -m symgov_backend.disc_dexpi_ingest apply   --package DIR --actor-id UUID --output apply.json
python -m symgov_backend.disc_dexpi_ingest upload  --package DIR [--dry-run] --storage-env-file ENV
# a named person approves the rights record in Semantic Review (#/semantic-review)
python -m symgov_backend.disc_dexpi_ingest gate    --package DIR --actor-id UUID
python -m symgov_backend.disc_dexpi_ingest publish --package DIR --actor-id UUID --storage-env-file ENV --output publish.json
python -m symgov_backend.disc_dexpi_ingest verify  --package DIR --expected-pilot-count 174 --brief-spot-checks --output verify.json
python -m symgov_backend.disc_dexpi_ingest facet-changes --output facets.json
python -m symgov_backend.disc_dexpi_ingest report  --package DIR --out IMPORT_REPORT.md --apply-report apply.json ...
```

`SYMGOV_DATABASE_URL` selects the database. The migration (`20261007_0070`) must be applied first.

## What the dry run stops on

* the package disagreeing with itself;
* two DEXPI names that would become one node code (package nodes take a `PACKAGE_` prefix because the package
  `Equipment` and the class `Equipment` share a spelling);
* one of the 16 expected shared classes with no existing concept, or a DISC concept that nearly matches an
  existing one (it would duplicate it);
* two different exact RDL identifiers for one concept and system;
* a discipline or category label the catalogue schemes do not have.

A concept that matches an existing one exactly but is not among the 16 is reused and reported (`extra_matches`).

## Re-running

The key is the manifest's `source_key`, lower-cased as the slug (`disc-dexpi-nd0004`). A symbol whose primary
digest, option digests or geometry signature changed gets a new revision (`r2`, ...) and is published by `publish`,
which retires the previous page and marks the previous revision `deprecated`. An unchanged symbol is left exactly
as it is. When DISC maps the held-back symbols, re-run the converter and `apply`: they arrive as new symbols.

## Attribution

The wording is stored once, in the evidence of the source package's rights record
(`attribution_text`, `attribution_is_placeholder`). Responses fill `payload.dexpi.attribution` from it; the Support
page and `GET /published/data-sources` read it from it. The SVG files carry it in their `<metadata>` because they are
the delivered files. To replace the placeholder: edit `attribution_text` in `config.json`, set
`attribution_is_placeholder` to false, re-run the converter, update the record's evidence, and re-run `apply`.

## Option (state) variants

Stored as `attachments` with `asset_role = 'option'`, an `option_index` and the source's `condition` (which two
register entries leave empty). They are never a symbol's preview or default download. The SVG download takes
`includeStateVariants: true` to return a zip of the primary, its options and a `state-variants.json`.
