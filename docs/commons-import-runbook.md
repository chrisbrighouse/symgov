# Wikimedia Commons P&ID import runbook

The curated Wikimedia Commons P&ID symbols (pack `commons-pid`, "Wikimedia Commons P&ID symbols (public domain)",
source package `WIKIMEDIA-COMMONS-PID-20261013`) are imported by `python -m symgov_backend.commons_ingest` from the
harvest package. It follows the DISC DEXPI importer (`disc_dexpi_ingest`): a dry run, revisions on change, the
publication gate, and the acceptance checks below. **No migration is needed.**

The product owner runs every live step. Each is its own decision: the backup and deploy of the release that carries
the importer, the import, the rights approval, and publication.

## The package

`/docker/openclaw-hz0t/data/symgov-commons-harvest-20261013/` holds `manifest.json` (licence evidence per file),
`curation/worksheet.csv` (one confirmed row per file: 385 `import`, 10 `import_after_fix`, 21 `drop`), `svg/` (the
downloads) and, once `fix-svgs` has run, `svg-fixed/` with `fixes.json`. The curated names, disciplines, categories and
concepts are final; the importer refuses a value it cannot honour and never corrects one.

## What the importer records

* One governed symbol per imported row, slug `commons-<pageid>`, revision `r1`, its Catalog ID from the normal allocator
  at publication. The payload carries a `commons` block: author, licence, licence templates, Commons URL, page id,
  Commons SHA-1, the original SHA-256, the retrieval date and the fixes applied.
* **SVG fixes**, each one step of the revision's transformation chain from the Commons original's SHA-256:
  `removed_prologue_comment` (260 files: an XML comment before `<svg>` makes the backend's own image validation refuse
  the file), `added_viewbox` (106 files, from `width` and `height`) and `removed_embedded_raster` (the 10
  `import_after_fix` files). A file left with nothing visible is excluded and reported, never uploaded blank.
* **Rights:** one record on the source package, `open` / `distribute`, method `licence_document`, *proposed*. Its
  evidence lists every file (page id, Commons URL, author, licence short name and templates, Commons SHA-1, retrieved
  2026-10-13) and an attribution line for Support's Data sources. Attribution is not required; authors are credited
  anyway.
* **Standards:** a file whose name or title cites ISO 10628-2 gets a verified `vendor_implementation` link to the
  existing ISO 10628 / 2012 record (281 files). A file whose own Commons description cites DIN EN 12792 (the 40
  ventilation files) or DIN 2429 (4 files) gets the same kind of link to that standard, registered with the edition
  "edition not stated". Never `normative_definition`.
* **Concepts:** an existing key is reused (the symbol then appears under "Other drawings of this concept" next to the
  DEXPI and DISC drawings); a `NEW:<Key>` creates one published concept, however many rows share it. Near misses are
  reported in the dry run and not merged. A `NEW:` key that exactly names an existing concept stops the import.
* **Classification**, all `rule`, verified: the curated discipline and category, the vector-drawing representation
  type, and for the fire, smoke and fire-and-smoke dampers a **secondary** ENGINEERING-DISCIPLINE assignment to
  Fire & Life Safety. The symbol's own discipline stays HVAC. The Catalog's discipline facet lists the dampers under
  HVAC and under Fire & Life Safety: from facet rules version 5 the facet also reads a symbol's **verified secondary**
  discipline assignments (a `proposed` secondary does not count). Existing rows need the facet backfill to pick this up;
  the deploy script runs it.

## What blocks, and what is held back

The dry run (exit 2) and `apply` stop on: a worksheet value that is not canonical, an action other than
`import`/`import_after_fix`/`drop`, an unconfirmed row, a licence family other than public domain or CC0, a file that
does not match its SHA-256, an `<image>` without its fixed file, a fixed file that differs from what `fix-svgs`
produces, an SVG that fails the harvester's safety scan or `validate_stored_image`, an existing concept key that does
not exist, or a `NEW:` key that already exists.

**70 symbols cite no standard**, so the gate refuses them with `graphical_authority_unasserted` even after the rights
approval. `publish` publishes all or nothing; `publish --permitted-only` publishes the rest and leaves those 70
approved but unpublished, listed in its report. It never skips a rights refusal. Publishing them needs a decision about
their graphical authority.

## Live run (production, by the product owner)

Prerequisites: a release that contains this importer is deployed (`scripts/deploy-release.sh`, which takes its own
backup), and `fix-svgs` has been run on the package (the rehearsal did this; `svg-fixed/` is in the package).

Set two shell variables on the host:

```
REL=<the deployed release directory name, e.g. stage11-xxxxxxx>
ACTOR=4d41c6b3-0cff-4cb2-a200-b616db6fa68b   # the product owner's user id
```

The API container does not set `SYMGOV_DATABASE_URL`; every command below derives it from the container's own
settings, the way the DEXPI and DISC imports were run. Define the wrapper once:

```
cig() { docker exec -w /data/symgov-releases/$REL/backend symgov-hermes-api sh -c \
  'SYMGOV_DATABASE_URL=$(python -c "from symgov_backend.db import get_database_url, normalize_database_url as n; print(n(get_database_url()))") exec python -m symgov_backend.commons_ingest "$@"' sh "$@"; }
```

1. Copy the package into the container and check it:
   ```
   docker cp /docker/openclaw-hz0t/data/symgov-commons-harvest-20261013 symgov-hermes-api:/tmp/commons-pid
   cig fix-svgs --package /tmp/commons-pid --dry-run      # expect "written": 0, "already_present": 308, no errors
   ```
2. Dry run (reads only; exit 2 if blocked):
   ```
   cig apply --package /tmp/commons-pid --dry-run --output /tmp/commons-dry.json
   ```
   Expect 395 to create, 68 existing concepts reused, 277 to create, no near misses, no blocking reasons.
3. Apply, then re-run the dry run (expect `unchanged: 395`):
   ```
   cig apply --package /tmp/commons-pid --actor-id $ACTOR --output /tmp/commons-apply.json
   ```
4. Upload the SVGs (dry run first if wished):
   ```
   cig upload --package /tmp/commons-pid --output /tmp/commons-upload.json
   ```
5. Gate; it **must refuse** every symbol with `rights_undecided` (and 70 also with `graphical_authority_unasserted`):
   ```
   cig gate --package /tmp/commons-pid --actor-id $ACTOR --output /tmp/commons-gate-before.json
   ```
6. **Approve the rights record** in Symgov: `#/semantic-review` → "Rights records" → `WIKIMEDIA-COMMONS-PID-20261013`.
   Nothing automates this.
7. Gate again: expect 325 `permitted`, 70 refused for `graphical_authority_unasserted` only.
   ```
   cig gate --package /tmp/commons-pid --actor-id $ACTOR --output /tmp/commons-gate.json
   ```
8. Publish (dry run first), then for real:
   ```
   cig publish --package /tmp/commons-pid --actor-id $ACTOR --permitted-only --dry-run
   cig publish --package /tmp/commons-pid --actor-id $ACTOR --permitted-only --output /tmp/commons-publish.json
   ```
9. Verify and write the report:
   ```
   cig verify --package /tmp/commons-pid --output /tmp/commons-verify.json
   cig report --package /tmp/commons-pid --out /tmp/IMPORT_REPORT.md --apply-report /tmp/commons-apply.json \
     --gate-report /tmp/commons-gate.json --publish-report /tmp/commons-publish.json --verify-report /tmp/commons-verify.json \
     --commit $REL --rights-approver "<name>"
   docker cp symgov-hermes-api:/tmp/IMPORT_REPORT.md /docker/openclaw-hz0t/data/symgov-commons-harvest-20261013/IMPORT_REPORT.md
   ```
   `verify` exits 1 while the 70 unlinked symbols are unpublished: `all-published` fails and names them. Every other
   check should pass. The dHash near-duplicate check is `skipped` in the container (no headless Chromium there);
   the rehearsal report carries its result. Run it on the dev box with `--chromium <path>` against a database and
   bucket that hold the published SVGs.

## Re-running

The key is the Commons page id. Re-running `apply` changes nothing. A changed SVG, name or concept makes a new
revision (`r2`, ...), which `publish` publishes, retiring the previous page. Concepts this import created are
recognised on a re-run by their notes and reused, not duplicated.
