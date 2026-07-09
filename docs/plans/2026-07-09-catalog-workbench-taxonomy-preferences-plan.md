# Catalog Workbench Taxonomy, Preferences, Saved Views, and Clipboard Plan

> Restart note: Chris asked that the published standards/symbols site is now referred to as the **Catalog** and should evolve into a professional symbol-finding workbench, not just a set of published records.

## Goal

Make the Symgov Catalog more useful to a working engineer by adding a clean taxonomy presentation layer, local user preferences, saved views, and an application clipboard model for collecting symbols before later download support is implemented.

## Current context

- User-facing terminology should say **Catalog**. Existing route/code may still use `/standards` and `StandardsPage` until a larger route migration is justified.
- Current Catalog frontend: `/data/symgov/frontend/src/App.jsx`, with `StandardsPage()` around lines 799 onward.
- Backend published-symbol API: `/data/symgov/backend/symgov_backend/routes/published.py`.
- Current live published count checked on 2026-07-09: 69 records.
- Current raw taxonomy is inconsistent and should not be exposed as the long-term professional model:
  - disciplines include `general`, `process_instrumentation`, mixed case values.
  - categories include `symbol`, `symbol_sheet`, and mixed quality values.
- Existing Catalog features include search, facets, table filters, detail drawer, comments, feedback/send-for-review, photo and comment icons, preview, and download asset metadata.

## Research summary to preserve

Professional engineering symbol libraries are typically consumed through these workflows:

1. Drawing production: insert a reusable CAD/vector symbol into AutoCAD, Plant 3D, electrical CAD, QElectroTech, etc.
2. Drawing markup/review: use PNG/SVG/PDF-friendly symbols while reviewing PDFs or annotating drawings.
3. Discipline-specific browsing: engineers think by discipline first, then category/equipment family.
4. Format-driven use: DXF/DWG for editable CAD, SVG for scalable vector/web, PNG/JPG for markup/reporting, PDF for documentation, BIM formats later.
5. Standards/provenance confidence: engineers need to know whether a symbol is approved, company standard, standard-derived, manufacturer-specific, superseded, or review-needed.

Recommended professional filters:

- Disciplines: Electrical, Fire & Life Safety, Piping / P&ID, Process, Instrumentation & Controls, Mechanical, HVAC, Civil / Structural, Architectural, Safety / Signage, General / Annotation.
- Categories: Valves, Pumps, Vessels / Tanks, Pipework / Fittings, Instruments, Sensors / Detectors, Fire Alarm Devices, Motors / Drives, Electrical Devices, Switchgear / Distribution, Lighting, Controls, Actuators, Heating / HVAC, Safety Devices, Annotations / Tags, Drawing Symbols, Equipment, Miscellaneous / Unclassified.
- Formats/use cases: CAD editable, Web/vector, Markup/image, Documentation, BIM/future.
- Advanced filters later: standards basis, governance state, asset quality, drawing context, industry/domain, project/company pack.

Ed assistant concept:

- Brand the prompt as **Ed**.
- First version should translate natural language into filters/search terms only, with no silent record mutation.
- Later Ed can route explicit improvement tasks to Libby/Scott/Hannah/Tracy/Daisy/Ed feedback workflows.

Application clipboard concept:

- Start with an in-app Catalog clipboard backed by local storage.
- It collects symbol IDs/display names and selected available formats/preferences.
- It is not yet a system OS clipboard integration and should not imply download is implemented.
- Later it can support bundle download, copy manifest, or handoff to engineering tools.

## Staged implementation

### Stage 1 — frontend-only workbench foundation

Objective: deliver visible value without schema migration.

Files likely to change:

- `frontend/src/catalogWorkbench.js` — new pure helper module.
- `frontend/src/catalogWorkbench.test.js` — node:test unit tests for helpers.
- `frontend/src/App.jsx` — integrate helper module into `StandardsPage()`.
- `frontend/src/styles.css` — layout/visual treatment for workbench controls.
- `docs/plans/2026-07-09-catalog-workbench-taxonomy-preferences-plan.md` — keep updated.

Behavior:

- Rename remaining visible “Standards” labels in Catalog route/panels to “Catalog”.
- Add taxonomy cleanup presentation helpers:
  - raw discipline/category values remain unchanged in the database.
  - UI exposes `catalogDisciplines`, `catalogCategories`, `catalogUseCases`, and `availableFormats` derived from raw row fields/assets.
- Add preferred format control:
  - preferred format filters/sorts/prioritises records, not destructive download behavior.
- Add local preferences:
  - preferred disciplines
  - preferred categories
  - preferred formats
  - preferred use cases
  - preference profile stored in `localStorage`.
- Add saved views:
  - current filters/query/preferred formats saved under a name into `localStorage`.
  - user can apply/delete saved views.
- Add application clipboard:
  - add selected or active symbol to app clipboard.
  - clipboard stored in `localStorage`.
  - show count and contents by display ID/name/available formats.
  - provide clear/remove controls.
  - label as “Catalog clipboard”, not OS clipboard.

Verification:

- `node --test frontend/src/catalogWorkbench.test.js`
- existing relevant frontend pure tests if present
- `npm run build`

### Stage 2 — backend/API support for richer taxonomy

Objective: make taxonomy durable and queryable server-side.

Potential files:

- `backend/symgov_backend/routes/published.py`
- new backend helper such as `backend/symgov_backend/catalog_taxonomy.py`
- focused Python tests under `tests/test_published_symbol_feedback.py` or new `tests/test_catalog_taxonomy.py`

Behavior:

- Expose canonical taxonomy arrays and available format/use-case metadata in published-symbol API rows.
- Add query parameters for canonical discipline/category/format/use-case so large catalogs can be filtered server-side later.
- Preserve raw values for audit/debugging.

Verification:

- `PYTHONPATH=backend pytest tests/test_catalog_taxonomy.py -q`
- API smoke via `docker exec symgov-hermes-api curl .../api/v1/published/symbols`

### Stage 3 — Ed guided search MVP

Objective: add the user-facing Ed prompt as a guide, not a mutating agent.

Behavior:

- UI prompt: “Ask Ed to find symbols…”
- Initial local parser may map keywords to filters without LLM calls.
- Later backend endpoint can use Ed with a safe contract:
  - input: prompt + available facets
  - output: interpreted filters/search/ranking explanation
  - no mutation unless explicit command path.

### Stage 4 — download/bundle model

Objective: after the engineer model is sound, add downloads.

Behavior:

- Use the Catalog clipboard as the source of a bundle/download manifest.
- Support preferred format fallback and warnings.
- Do not start this until Stage 1 and taxonomy model feel right.

## Restart checklist

If a session restarts:

1. Read this plan.
2. Inspect current `git status --short --branch` in `/data/symgov`.
3. Load `symgov-agent-operations`, `react-ui-patterns`, and `test-driven-development` skills.
4. Check whether `frontend/src/catalogWorkbench.js` and `.test.js` exist.
5. Run `node --test frontend/src/catalogWorkbench.test.js` if present.
6. Continue Stage 1 before touching backend schema.

## Current next action

Proceed with Stage 1 using TDD:

1. Add failing tests for taxonomy normalization, preferences serialization, saved view shape, clipboard add/remove behavior.
2. Implement `catalogWorkbench.js` helpers.
3. Integrate helpers into `StandardsPage()`.
4. Add minimal CSS.
5. Run tests and build.
6. Update this plan and produce a restart-ready status note.
