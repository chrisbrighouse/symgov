# DEXPI Symbol Library — Pilot Ingestion Implementation Plan

**Written 2026-09-22, at `d4a9004`, for the first priority of the pilot plan:
a DEXPI symbol library of 150–200 sample symbols.**

This plan ingests **174 symbols** from the DEXPI public example P&ID corpus as
SymGov's first `authoritative_library` source package — and therefore as the
first data ever to pass through the SM-P0-08 publication gate.

Five decisions were taken by Chris on 2026-09-22 and are recorded in §3;
D7, D8 and D9 followed on 2026-09-23 as the work packages measured what the
earlier decisions had assumed. Nothing below re-opens them.

---

## 1. Where the symbols come from, and why not from anywhere else

**DEXPI publishes no symbol library, by design.** The DEXPI 2.0 specification
defines `ShapeCatalogue` and `Shape` as the *container* for symbol geometry; it
ships no shapes. `gitlab.com/dexpi/Specification` carries 193 blobs and zero
shape, symbol or graphic artifacts. The normative artwork is behind ISO 10628
and ISO 14617, which are paywalled.

Four candidate sources were measured:

| Source | Symbols | Format | Licence |
|---|---|---|---|
| `gitlab.com/dexpi/Specification` | 0 | — | no licence file |
| **`gitlab.com/dexpi/TrainingTestCases`** | **573 names / 201 geometries** | Proteus XML `ShapeCatalogue` | **CC BY 4.0** |
| `github.com/ToniaPedersen/DISCDEXPI` | 307 Origo + 313 Detail | SVG + `Symbols.xlsm` | **none** |
| `github.com/equinor/NOAKADEXPI` | 124 Origo + 248 Detail | SVG | **none**, archived 2026-09-15 |
| pyDEXPI | 0 | parser/renderer only | **AGPL-3.0** |

`TrainingTestCases` is the only DEXPI symbol source carrying a licence, and CC
BY 4.0 is attribution-only. It is therefore the source.

**pyDEXPI is AGPL-3.0 and must not be linked into the backend.** It ships no
symbols in any case — `data/` is one reference P&ID and 34 pickled sample
patterns. Its SVG renderer is a useful reference implementation to read, and
nothing more. This is why WP1 writes our own converter rather than wrapping it.

**DISCDEXPI is the better library and remains blocked.** It is the live
successor to the archived Equinor NOAKA work, was pushed to on 2026-09-22, is
the right size, and already carries a Symbol ID ↔ DEXPI class mapping. It has
no LICENSE file, which means all rights reserved. Asking its maintainers to
add one is cheap, independent of this plan, and would make it the preferred
source for a second phase. It is not on this plan's critical path.

### 1.1 The corpus is DEXPI 1.2 and 1.3, not 2.0

`TrainingTestCases` contains exactly two specification directories, `dexpi 1.2`
and `dexpi 1.3`. **No DEXPI 2.0 example set has been published.** The pilot plan
says 2.0; this corpus cannot satisfy that wording.

The shape geometry is version-stable across 1.2 → 2.0 — the primitives and the
`ShapeCatalogue` structure are unchanged — so this is a provenance caveat rather
than a data-quality one. It is recorded in three places, all existing fields, no
schema change:

- `SourcePackage.release_version` — `"DEXPI 1.2/1.3 example corpus"`, the field
  the CFIHOS seed used to pin an edition and the key a later 2.0 corpus would
  reconcile against
- `SourcePackage.package_metadata` — the structured caveat: specification
  versions present, that 2.0 publishes no example set, the harvest date
- each revision's `payload_json` — a visible note, because that is what the
  Catalogue renders

---

## 2. Baseline — measured, not assumed

Every number here was computed from the corpus at clone time on 2026-09-22, and
every code claim read out of the repository at `d4a9004`.

### 2.1 What the corpus actually contains

147 XML files carry a `ShapeCatalogue`. Within them:

- **877 shape instances** across **573 distinct `ComponentName`s**
- **185 of those names are `Nozzle` variants** — connection stubs, not catalogue
  symbols — leaving 378 candidate names
- collapsing on normalised geometry (primitive kinds + coordinates rounded to
  6dp, sorted, ignoring `Presentation` colour and line weight) yields
  **201 distinct geometries**
- five primitive types only: `Line` (2366), `PolyLine` (697), `Circle` (543),
  `TrimmedCurve` (317), `Text` (182), plus 7 nested `Shape` references

201 lands just above the 150–200 target without any curation, and D1's naming
rule brings the ingested set to 174.

**The tolerance matters and an earlier draft of this plan had it wrong.** A
first pass rounded coordinates to 3dp and reported 192 geometries. 82 of the
selected symbols come from metre-declared files whose *entire* symbol spans
0.0025 units, so 3dp merged genuinely different drawings. 6dp is the figure of
record; every count below is measured at it.

### 2.2 Cross-vendor consolidation does not exist in this corpus

The filenames encode the contributing vendor — `C01V04-VER.EX01.xml`,
`C01V01-HEX.EX01.xml` — giving six codes: SAG 42, AUD 25, HEX 24, AVV 24, VER 3,
ING 2, plus DXI.

**Only 1 of the 201 geometries is drawn identically by more than one vendor.**
Each vendor draws its own geometry. 76 geometries carry more than one
`ComponentName`, but those extra names are *within* a single vendor and are
generated internal identifiers, not alternative human names:

```
SAG Chamber          19, 20, 21, 22, 23, 24
SAG PressureFunction -1704003447, @30|M00|A60|A10|A10|L, …
AUD (no class)       A5052ContRoom, A5052ContRoomXMP_20363, …
```

This is the single most consequential measurement in this plan. It means there
is no rich vendor-alias vocabulary to reconcile, and **naming, not
deduplication, is the real problem.** `source_package_entries` still records
provenance, but it is not reconciling competing names.

### 2.3 Name quality is poor except in DEXPI's own reference solutions

Distinct-name quality across the 378 non-nozzle names:

| Vendor | Instances | Character |
|---|---|---|
| SAG | 200 | 95 vendor codes, 63 numeric IDs, 0 readable |
| AUD | 152 | 76 mixed, 42 upper, 34 generated suffixes |
| HEX | 134 | all mixed-readable |
| AVV | 64 | 19 mixed, 16 upper, 29 other |
| **VER** | **47** | **37 clean `*_SHAPE` names** |
| ING | 2 | — |

VER is DEXPI's own reference solution set and is the only source of names like
`CENTRIFUGAL_PUMP_SHAPE`. Across the whole corpus only 72 distinct names are
human-meaningful.

Naming basis for the 201 geometries:

- **131** carry a DEXPI `ComponentClass`
- **43** appear in the VER reference set
- **174 namable** from one or the other; **27 from neither**

**One vendor drops out entirely.** AUD contributes 152 catalogue entries, but
they collapse to only 15 distinct geometries and not one carries a
`ComponentClass` or appears in the reference set. D1 therefore excludes all of
AUD. This is the rule working rather than a defect, but it is worth stating: the
selection draws on HEX (60), VER (43), SAG (43) and AVV (28).

### 2.4 Standards coverage is thin

Of the 174 namable geometries, **26 carry an ISO 10628 registration number**
(`SymbolRegistrationNumberAssignmentClass`), 21 distinct, in two spellings that
need normalising:

```
ISO10628:2012-2322-A     (15 of the 21)
ISO10628-X2322A-A01      ( 6 of the 21)
```

**The two spellings are not merged, and that is measured rather than cautious.**
No geometry in the corpus carries both, so nothing in the data establishes the
equivalence. Only the colon form states an edition, and a `SymbolStandardLink`
points at a `standard_version` — an edition — so only those 19 can be pinned to
ISO 10628:2012. The other 7 keep their registration number in
`source_symbol_identifier`; they simply cannot claim an edition nobody wrote
down.

All 19 edition-pinned registrations belong to VER symbols, so the union is
exactly the reference set. **43 with a genuine normative source; 131 vendor
renderings with none.**

### 2.5 Semantic coverage

**57 distinct `ComponentClass` values** cover the 131 class-named geometries,
and that is the number of `SemanticConcept` rows WP3 seeds. The 43 VER
geometries carry `*_SHAPE` names that map onto the same concepts. Where a
geometry's occurrences disagree on class, every observed class is reported
rather than resolved.

The vendor `ComponentName`s observed across the selection become those concepts'
aliases under D3.

### 2.6 The gate is live, blocks, and has never fired

`enforce_publication_gate` is called from both promotion paths
(`runtime.py:31`, `organization_promotion_handoff.py:82`) and genuinely refuses:
"a gate that never blocks is not a gate".

It is harmless today only because `GATED_PACKAGE_TYPES` is
`{authoritative_library}` (`publication_gate.py:218`) and every package in
production is created by `runtime.ensure_source_package_for_intake` as
`submission_sheet`. **This plan registers the first `authoritative_library`
package in SymGov's history, so its symbols are gated from the first one.**

Two gate facts that are easy to get wrong, both read from the source:

- **Any of the eight relationship types satisfies `graphical_authority`**, not
  only the two in `AUTHORITATIVE_RELATIONSHIP_TYPES`. `publication_gate.py:499`
  is explicit: "the dimension asks that the relationship be *stated*, not that
  it be normative." `vendor_implementation` passes. The authoritative types feed
  the §13.2 traceability level as evidence only.
- **The link must be `verified`, not `proposed`** (`publication_gate.py:826`).
  `import_manifest` and `source_api` are the two auto-verifying methods
  (`standard_sources.py:88`).

### 2.7 All six dimensions now have a production write path

The `publication_gate` module docstring states that rights and semantic identity
are unsatisfiable because nothing imports `rights_provenance` or
`semantic_concepts`. **That is out of date.** SM-P1-01 shipped
`routes/semantic_review.py` and was activated in production on 2026-09-18:

| Dimension | Route |
|---|---|
| semantic_identity | `POST /concepts`, `/concepts/{id}/revisions`, `/concept-revisions/{id}/transition`, `/symbol-revisions/{id}/semantic-assignments`, `/semantic-assignments/{id}/decision` |
| classification | `POST /symbol-revisions/{id}/classifications`, `/symbol-classifications/{id}/decision` |
| rights | `POST /rights-records`, `/rights-records/{id}/decision` |
| source | `add_source_package_entry` (`source_package_acquisition.py:294`) — no caller yet |
| graphical_authority | `assert_symbol_standard_link` (`standard_sources.py:358`) — no caller yet |
| integrity | asset SHA-256 |

Correcting that docstring is WP5.

### 2.8 One structural constraint

`uq_source_package_entries_package_revision` is UNIQUE on
`(source_package_id, symbol_revision_id)`: **one entry per revision per
package.** N source attributions for one symbol would need N packages. Given
§2.2, each symbol traces to exactly one vendor, so one package suffices — this
is D4.

---

## 3. Decisions

D1-D6 taken by Chris on 2026-09-22; D7 and D8 on 2026-09-23, when writing
WP3 measured two things D3 had assumed; D9 on 2026-09-23, when WP4 met three
NOT NULL columns the corpus does not fill.

### D1 — Ingest the namable geometries

**As implemented: 174 selected — 43 named from the VER reference set, 131 from
`ComponentClass` — and 27 excluded.** The decision was taken against a first
measurement of 169/23; the rule is unchanged and the counts moved when WP2
replaced the ad-hoc 3dp geometry signature with the converter's 6dp one (§2.1).
174 remains inside the 150–200 target.

The geometries with no naming basis are **excluded**, and the exclusion is recorded rather than worked
around, the same way migration `20260910_0055` recorded the 67 CFIHOS rows it
could not split. Synthesising names for them would invent data.

*Rejected:* all 201 with 27 invented names; the VER-only set (too small);
class-canonical symbols (discards the vendor drawing variety that makes this a
governance pilot).

**D1 does not make names unique, and a catalogue needs them to be.** 31 base
names are shared by 120 of the 174 — 11 distinct geometries whose only naming
basis is `Tank`, 12 more that are all `ProcessInstrumentationFunction`. WP2
disambiguates with the vendor and a stable index (`Tank (HEX 1)`), keeping
`base_name` for the D3 concept mapping: the suffix distinguishes the *drawing*,
not the meaning.

### D2 — Honest relationship types; all 174 publish

**As implemented: 43 and 131**, against the 45/124 estimated when the decision
was taken. The split moved for the reason §2.4 gives — only 19 registrations
state an edition, and all 19 belong to VER symbols, so the normative set is
exactly the 43 reference drawings.

- **43** asserted as `normative_definition` — 19 against ISO 10628:2012 carrying
  the registration number in `source_symbol_identifier`, the rest against the
  DEXPI P&ID specification edition
- **131** asserted as `vendor_implementation` against the DEXPI specification
  edition. 7 of these carry an ISO registration number that states no edition:
  the number still reaches `source_symbol_identifier`, so the traceability
  survives even though the relationship type is the honest weaker one

All 174 pass the gate per §2.6. The 131 carry a lower §13.2 traceability level,
which is **accurate rather than a penalty**.

*Rejected:* asserting `normative_equivalent` for all of them. It would publish the
same 174 while claiming, as a `verified` assertion, that each vendor's own
drawing is a normative equivalent of a standard symbol — which no source states,
in the one dimension whose stated purpose is "no ambiguous
'standard-associated' label".

### D3 — Seed all 57 semantic concepts

**As implemented: 57**, against the 58 estimated, for the same §2.1 reason.

One `SemanticConcept` per DEXPI `ComponentClass`, each carrying the relevant
vendor `ComponentName`s in its `aliases` field. Every one of the 174 gets a
verified primary assignment. **No `semantic_identity` waivers.**

Two reasons this beats the waiver. §9.2 scopes the exception to
"non-engineering/annotation symbols" and requires a named approver plus a
written reason for each — valves and pumps are not that, so 174 waivers would
mostly sit outside the exception's own scope, and would not be cheaper. And the
concepts are where the vendor names finally belong: "Hexagon draws this,
Siemens calls it `@30|M00|…`" is a durable, searchable statement about a
*concept*, not about a symbol.

This will be **the first production `SemanticConcept` content in SymGov.**

### D4 — One corpus package

A single `authoritative_library` package, `DEXPI-TTC-1.2-1.3`. One rights
record, one human approval. Each symbol gets one entry:

```
source_path              dexpi 1.3/example pids/…/C01V04-VER.EX01.xml
provider_entry_identifier CENTRIFUGAL_PUMP_SHAPE
original_asset_sha256     <SHA-256 of the source XML>
```

The vendor is derivable from the path. This matches the licence, which is one
CC BY 4.0 grant over the whole repository, and §2.2 means no symbol needs a
second attribution.

*Rejected:* seven vendor packages. Rights approval **cannot be automated** —
"there is no controlled-system rights decision", `transition_rights_record`
demands a named decider unconditionally — so package count equals human
approvals. Seven identical approvals of one grant buys only the single
cross-vendor geometry.

### D5 — Attribution embedded in the SVG, plus a source panel

The converter writes creator, licence notice and source URI into each SVG's
`<metadata>` at conversion time, so attribution travels with every download and
API response **by construction rather than by UI discipline**. A
`SupportDataSources`-style panel follows, reading a `dexpi-source.json` in the
same shape as `ics-source.json`.

CC BY 4.0 §3(a) attaches to *sharing*, which includes the Catalogue download and
the Catalog API, not only the Standards view. A panel alone would leave a
downloaded SVG carrying nothing.

### D6 — The first full run is a disposable-PostgreSQL rehearsal

Taken 2026-09-22, when readiness was assessed rather than assumed.

**There is no non-production database.** The only Postgres serving SymGov on
this host is `symgov-postgres` (postgres:16), running alongside
`symgov-hermes-api:stage11-e5418a7` — the live API, one commit behind `main` at
`d4a9004`. `.env.backend.database.example` points at exactly that instance.

So the first ingestion runs against a **disposable PostgreSQL**, using the
harness the `*_postgresql.py` suites already use: `_database()` in
`tests/test_organization_symbol_postgresql.py:53` starts
`postgres:16-alpine` with `docker run --rm` on a random name and port. The
rehearsal runs the migration, the 57 concepts, the 174 symbols and the gate, and
**the gate evaluations are read before anything reaches production.**

This matters more than ordinary caution. §2.6 establishes that this package is
the first `authoritative_library` in SymGov's history, so the gate has never
fired on real data. It refuses rather than warns. Learning what it refuses in a
container that is thrown away afterwards costs a few minutes; learning it in the
live Catalogue costs a withdrawal.

Production ingestion is a **separate, explicitly approved operation** after the
rehearsal is understood, per CLAUDE.md's rule on migrations and live mutations.

#### Rehearsal result, 2026-09-23

Run on a disposable `postgres:16` on the development VPS — `postgres:16-alpine`
is absent from that box and cannot be pulled, so the harness now reads
`SYMGOV_TEST_POSTGRES_IMAGE` and this ran under the override. Migration to
`20260919_0061` took 7m17s, the 83-concept seed 3m53s, the 174-symbol
ingestion 11m13s and the gate pass 7m52s.

**The gate refused all 174, on one dimension: `rights_undecided`.** Five of
the six were satisfied on every symbol — `semantic_identity`, `source`,
`graphical_authority`, `integrity`, `classification` — and **no waiver was
used anywhere**, which is what decisions D3 and D7 were for. Traceability
stood at T2 for all 174, because section 13.2's T3 rung requires the rights
disposition to be established.

Approving the single rights record then permitted all 174 and lifted every one
to **T5**, so the human approval is the only thing standing between this
package and publication. That approval was taken in the disposable database
for the rehearsal and is not a production decision.

### D7 — Concepts from the reference shape names too

**D3 covered 131 of the 174.** One concept per `ComponentClass` leaves out
every geometry that carries no class, and 43 of the selection do — all of them
DEXPI's own `VER` reference shapes, which name a geometry precisely and
classify it not at all. Under D3 alone those 43 would have reached the
publication gate with no verified primary concept and been refused on
`semantic_identity` (`publication_gate.py:454`). Measured, not predicted: the
selection manifest carries `component_classes: []` for exactly those 43.

**A reference shape name is a naming basis, so it names a concept.** D1 already
trusts `BLIND_COVER_SHAPE` to name the *symbol*; there is no reason it cannot
name the meaning. Dropping the `SHAPE` token and case-folding merges 8 of the
43 into the class concept they were always the same meaning as —
`BALL_VALVE_SHAPE` is `BallValve` — which is a merge the corpus supports rather
than a rename. 26 distinct reference names remain.

**Five classes are seeded that name no geometry.** `MeasurementFunctionLevel`,
`MeasurementFunctionManualInput`, `PressureFunction`, `TemperatureFunction` and
`VesselWithDishedHeadsWithOptionalAgitator` are each observed only alongside
another class on the same drawing, which took the name. They are meanings the
corpus asserts, so D3's "all 57" seeds them; their definition says they have no
catalogue geometry of their own rather than implying one.

**57 + 26 = 83 concepts, and all 174 symbols get one.**

*Rejected:* a section 9.2 `semantic_identity` waiver per unclassed symbol. The
exception is scoped to "non-engineering/annotation symbols" and most of these
are valves, vessels and exchangers, so the waivers would sit outside the
exception's own scope — the same reasoning that settled D3.

**Concept kind comes from the DEXPI element, not the name.** The corpus records
which element owns each catalogue entry, and the 15 observed elements map onto
SymGov's nine kinds. Eight instrument classes are filed under both
`ProcessInstrument` and `ProcessInstrumentationFunction`, on the same two
entries, so neither reading is in a majority; there, DEXPI's own `Function`
token in the class name settles it. Anything still ambiguous takes `other` and
records the disagreement in the concept's notes.

### D8 — The concept seed is a command, not a migration

**WP3 could not be a migration.** `semantic_concepts.created_by_user_id` and
`semantic_concept_revisions.author_id` are both NOT NULL foreign keys to
`users` (`models/schema.py:2606`, `:2659`). The CFIHOS seed `20260909_0052` had
no such problem because classification nodes have no author. 83 concepts cannot
be seeded without naming who created them, a migration has nobody to name, and
it would fail outright in the D6 rehearsal, where Alembic runs against a
database with no users at all.

So the seed is `symgov_backend/dexpi_seed.py`, in the shape of `ics_import` and
`ics_review`: `--actor-id` required, `plan` reads and `apply` writes, it owns
its transaction, and it never rewrites a concept it did not create. It adds no
migration, so no head assertion moves.

*Rejected:* a migration that inserts its own "DEXPI import" service user. It
would be deterministic, but it puts a non-human account behind the first 83
governed revisions in the product and makes a schema migration write to
`users`.

### D9 — Catalogue category, discipline and the classification node

**Taken 2026-09-23, when WP4 met three NOT NULL columns the corpus does not
fill.** `governed_symbols.category` and `.discipline` are both NOT NULL, and
section 9.2's classification dimension wants a governed assignment, so all 174
symbols need three values that the DEXPI corpus never states. Inventing a
per-symbol judgement is what CLAUDE.md forbids, so all three are derived from
something the corpus does assert:

- **Discipline is the corpus.** `TrainingTestCases` is a P&ID corpus, so every
  symbol is `Piping / P&ID` — except the 22 whose concept kind is `function`,
  which are instrumentation functions rather than pipework and take
  `Instrumentation & Controls`.
- **Category comes from the concept kind first, the concept name second.** The
  kind is corpus-asserted, being read from the DEXPI element that owns the
  catalogue entry, so `annotation`, `connection` and `function` decide the
  category outright. Only the remaining kinds fall through to a name table,
  matched on whole CamelCase words so `SwingCheckValve` is a valve rather than
  an item of equipment. A concept neither recognises takes `Equipment`, and
  the driver reports how many did: **13 of 174.** Measured distribution —
  Valves 45, Instruments 27, Vessels/Tanks 20, Pumps 20, Pipework/Fittings 18,
  Equipment 13, Annotations/Tags 12, Actuators 11, Drawing Symbols 8.
- **The classification assignment goes to CFIHOS Representation Type's
  `INTELLIGENT_VECTOR_DRAWING_CAD`,** the node migration `20260909_0052`
  seeded. It is the one representation-oriented vocabulary a migrated database
  actually has, and it is true of all 174 by construction, because the
  converter emits vector SVG and nothing else — which is why the method is
  `rule` and the assignment verifies without a reviewer.

*Rejected:* assigning against the catalogue's own `SYMBOL-CATEGORY-FAMILY`
scheme. `seed_classification_schemes` is called from `tests/` only, so those
three schemes exist in no migrated database and the assignment would point at
a node that is not there. The stored `category` string still reaches the
catalogue facet, because `catalog_taxonomy.normalize_catalog_category` passes
an exact label through unchanged.

*Rejected:* CFIHOS Document Type `2365` "piping and instrumentation diagram".
These symbols appear *on* a P&ID; they are not P&IDs, and a document-type
classification of a symbol asserts something the corpus does not.

### D10 — Storage and publication are part of the pilot

**Taken 2026-09-24, when preparing the production run showed WP1–WP5 never
reach the Catalogue.** "Permitted at T5" is a gate verdict, not a
publication. `gate` only records evaluations and the revisions stay
`approved`; the Catalogue reads `published_pages`, `pack_entries` and
`publication_packs` through `active_public_symbol_projections`, which only
Rupert's publication handoff wrote. And the payload's asset had no
`object_key`, so even a published symbol would have had no preview or
download (`routes/catalog.py` serves an asset by its key).

So WP6 adds both halves, and keeps the operator in charge of each:

- **Content-addressed keys, decided in the plan.** `dexpi/<package>/<slug>/<sha256>.svg`.
  Known before the upload, the same PUT on a re-run, and never shared between
  revisions, which `ensure_preview_authorization` would refuse.
- **`apply` writes an `Attachment` parented to the revision** — the lineage
  preview authorization trusts without further proof — and so requires
  `--harvest`, because `attachments.size_bytes` is NOT NULL.
- **`upload` is its own command** and touches no database. It re-checks every
  digest and runs the stored-image check organisation drafts use.
- **`publish` is all-or-nothing.** In one transaction it reads every stored
  object back against its recorded digest, evaluates the gate for every
  symbol, and only if all pass writes the pack, job, pages and entries
  through `runtime.publish_revision_to_pack` — extracted from Rupert's
  handoff so the two paths share one routine. `--actor-id` is recorded as the
  job's requester and approver: publishing to the public Catalogue is a named
  person's decision, like the rights approval.

*Rejected:* driving the symbols through Rupert's handoff. It is bound to a
review case, a queue item and an agent run that an ingested package does not
have, and inventing them would record a review that never happened.

---

## 4. Work packages

### WP1 — `dexpi_converter.py`

`backend/symgov_backend/services/dexpi_converter.py`, mirroring
`btx_converter.py` (`services/btx_converter.py:283`) closely enough that the two
read as siblings:

- a **pure file service** — "callers own queues, storage, and access control"
- `DexpiConversionError(code, detail)` plus `_warning()` dicts, with **per-symbol
  failure isolation**: one malformed shape records a failure and the run
  continues
- hard bounds mirroring `MAX_INPUT_BYTES` / `MAX_EXPANDED_BYTES` / `MAX_SYMBOLS`
- **the XXE guard carries straight over.** BTX rejects `<!DOCTYPE` and
  `<!ENTITY` before parsing; Proteus XML is parsed from the same untrusted
  position and needs the same treatment
- `convert_dexpi(input_path, output_dir, *, attribution, formats=("svg",))`
  returning a manifest with `schema_version`, `source_sha256` and per-symbol
  records

**SVG only, deliberately.** BTX emits SVG, DXF and PNG; this converter emits
SVG and refuses any other request with `unsupported_format` rather than
ignoring it. SVG is the previewable, attribution-bearing format the pilot
needs, and DXF/PNG would be additional work no decision in §3 asks for. Adding
them later is contained — the primitives are already parsed into a neutral
structure.

**Attribution is a required argument.** The converter refuses to emit without
creator, licence and source, which makes D5 a property of the code rather than
a convention a caller can forget.

Two normalisations were found necessary once the converter was run over the
whole corpus, and both are declared in the manifest rather than applied
silently:

- **Stroke colour → `currentColor`.** 718 of 3629 strokes are pure white and a
  further 590 are saturated green or cyan — CAD dark-canvas palettes, invisible
  or garish on a light catalogue page. `currentColor` also lets the page theme
  the symbol for light and dark. Source colours are kept per symbol.
- **Line weight clamped to 0.4%–6% of the bounding-box diagonal.** The median
  declared weight is a reasonable 1.6% of the diagonal, but the 99th percentile
  is 179% and the worst is over 17000×. Source weights are kept per symbol.

Neither is geometry, so neither invents data; both decline to carry a vendor's
canvas assumption into a different rendering context.

Two things make this smaller than BTX: the geometry is already vector primitives
rather than a PDF content stream, so there is no tokeniser; and the only real
transform is the Y-axis flip plus the 7 nested `Shape` references. Presentation
colour and line weight are read but excluded from the geometry signature.

Per D5 the SVG writer emits a `<metadata>` block carrying `dc:creator`,
`dc:license` (CC BY 4.0) and `dc:source`.

SVG is already a first-class previewable type (`asset_manifest.py:6`), so
nothing downstream needs teaching about the format.

### WP2 — Harvest and selection

Reads the corpus, computes the geometry signature, applies D1, and emits the
selection manifest that drives WP4: geometry signature, chosen canonical name
and its basis (VER or ComponentClass), `ComponentClass`, contributing vendor,
source path, source SHA-256, every observed `ComponentName`, and any ISO
registration number normalised across the two spellings in §2.4.

The 23 excluded geometries are written to the manifest as exclusions with their
reason, not dropped silently.

### WP3 — Concept seed command

**Delivered 2026-09-23.** Two pieces, split the way WP1 and WP2 are:

`services/dexpi_concepts.py` decides, as a pure function of the WP2 selection
manifest — 83 concepts under D3 and D7, the kind of each from its DEXPI
element, aliases from every vendor name observed on its geometries, and the
concept each of the 174 symbols belongs to. Definitions state provenance and
say plainly that DEXPI publishes no definitional text, rather than inventing
engineering prose the corpus does not contain.

`dexpi_seed.py` records it, per D8: `plan` resolves the seed against what is
already stored and writes nothing, `apply` creates each concept through
`create_semantic_concept` (`semantic_concepts.py:162`) and walks its revision
draft → review → approved → published (`:304`), which is what makes the concept
`active` and therefore assignable. `--output` writes the
`concept_key` → `SGC-########` map WP4 consumes.

**Re-running is safe by check, not by luck.** A concept's identity is a
sequence-allocated code, so a second run cannot recognise its own earlier work
by key. Existing concepts are indexed by preferred name — across all revisions,
so a run that created concepts but failed before publishing them is still
recognised — and a name already present is never rewritten. Content that no
longer matches the plan is reported as drift and exits non-zero, because
changing a published concept is a reviewer's governed revision, not a seed's
decision.

No migration, so no head assertion moves.

### WP4 — Ingestion driver

**Delivered 2026-09-23.** Two pieces, split the way WP1/WP2 and WP3 are:
`services/dexpi_ingestion.py` decides, as a pure function of the WP2 selection
and the concept map, and `dexpi_ingest.py` records it — `plan` reads,
`apply` writes, `gate` evaluates section 9.2 and stores the evaluation.
`--actor-id` is required to write, it owns its transaction, it is idempotent
by slug, and it adds no migration.

Two things the writing measured:

- **`governed_symbols.category` and `.discipline` are NOT NULL**, and the
  corpus states neither. That is decision D9.
- **The converter now names itself.** `asset_transformations` wants a tool
  *and* a version, and `dexpi_converter` had neither as a constant, so
  `CONVERTER_NAME`/`CONVERTER_VERSION` were added and the manifest now carries
  them. Nothing else about WP1 changed and its 76 tests still pass.

Consumes the WP1 manifest and the WP2 selection and writes, per symbol:

1. `GovernedSymbol` + `SymbolRevision` with the payload, the §1.1 version note,
   and `aliases` populated for catalogue search (`catalog_search.py:161` already
   indexes `aliases`, `keywords` and `search_terms`)
2. the package entry — `add_source_package_entry` per D4
3. `record_asset_transformation` (`rights_provenance.py:511`): source XML
   SHA-256 → tool name and version → SVG SHA-256. This is Appendix B.2's chain
   and it feeds the integrity dimension
4. `assert_symbol_standard_link` per D2, verified with method `import_manifest`
   so the 174 links self-verify from the manifest rather than queueing 174
   manual verifications
5. the semantic assignment against its WP3 concept, and a classification
   assignment

The package itself is registered once via `register_source_package`
(`source_package_acquisition.py:169`) with `record_package_acquisition`
(`:213`), acquisition method `public_download`.

The single rights record is proposed with status `open`, disposition
`distribute`, licence reference the CC BY 4.0 URL — then **approved by a named
reviewer through the SM-P1-01 UI**. That approval is the one irreducibly manual
step in this plan.

### WP5 — Attribution surface and docstring correction

**Delivered 2026-09-24.** `backend/symgov_backend/data/dexpi-source.json` sits
beside `ics-source.json`, and `SupportDataSources.jsx` renders a second
"Symbol library / Data sources" section from it on the Support page. It
carries the modifications statement CC BY 4.0 §3(a)(1)(B) asks for (the SVG
conversion and the colour and line-weight normalisations), as well as the
creator, source and licence. `tests/test_dexpi_ingestion.py` pins the file to
`PACKAGE_CODE`, `PACKAGE_SOURCE_URI`, `LICENCE_REFERENCE` and the selection's
attribution, so the panel cannot drift from what the package records.

`dexpi-source.json` plus a panel in the shape of `SupportDataSources.jsx`. And
correct the `publication_gate` module docstring, which still states that rights
and semantic identity are unsatisfiable — true when written, false since
2026-09-18, and actively misleading to the next reader of the gate.

### WP6 — Storage upload and publication

**Delivered 2026-09-24,** per decision D10: `asset_object_key` in
`services/dexpi_ingestion.py`; the `Attachment` row, `upload` and `publish` in
`dexpi_ingest.py`; `runtime.upload_object_bytes` and
`runtime.publish_revision_to_pack` lifted to module level, with Rupert's
handoff now calling the latter. The production sequence becomes: `dexpi_seed
apply`, `dexpi_ingest apply --harvest`, `upload`, the named rights approval,
`gate`, then `publish`.

---

## 5. What this plan does not do

- It does not touch the submission-intake path. Every existing package stays
  `submission_sheet` and every currently public symbol stays grandfathered.
- It does not change the gate's policy, vocabularies or waivable set.
- It does not pursue DISCDEXPI licensing (§1), which is a parallel conversation.
- It does not claim DEXPI 2.0 coverage (§1.1).

## 6. Validation

- `npm run test:frontend` for WP5
- `scripts/test-backend.sh` for WP1–WP4 — the supported runner, default timeout
  2700s
- the converter gets fixture-based tests in the shape of
  `tests/test_btx_integration.py`
- WP3 splits its cover the way its code splits: `tests/test_dexpi_concepts.py`
  for the plan, which needs no database, and
  `tests/test_dexpi_seed_postgresql.py` for the seed, which needs a real one —
  the codes come from a sequence, `aliases_json` is a checked JSONB array and
  `current_revision_id` is a circular foreign key, none of which exist on
  SQLite
- WP4 splits the same way: `tests/test_dexpi_ingestion.py` for the plan, which
  needs no database and pins decisions D2 and D9's counts, and
  `tests/test_dexpi_ingest_postgresql.py` for the driver, which ingests a
  trimmed selection carrying all four assertion bases rather than holding the
  sweep for ten minutes to re-count what the portable file already pins
- WP3 and WP4 add no migration, so no PostgreSQL fixture pin moves
- the D6 rehearsal reuses `_database()` from
  `tests/test_organization_symbol_postgresql.py`; it starts its container
  with `docker run --rm`, but see the recorded teardown-leak trap and check `df`
  if `_postgresql` modules start failing oddly
- **the image that harness starts is now `SYMGOV_TEST_POSTGRES_IMAGE`,**
  defaulting to the `postgres:16-alpine` every module hard-coded before. The
  development box cannot pull that tag, so every `*_postgresql` module there
  needs `SYMGOV_TEST_POSTGRES_IMAGE=postgres:16`; the readiness and Alembic
  timeouts widen automatically, because the Debian image's `initdb` is slower
  and migrating to head on it takes 7m17s against a 240-second ceiling

## 7. Sequence

1. **WP1 + WP2** — no database involvement, nothing to approve
2. **WP3 + WP4** — written but not run against anything live. WP3's `plan`
   command reaches a database only to read, so it is safe to point at anything;
   `apply` is a write and waits for the rehearsal like everything else
3. **D6 rehearsal** on disposable PostgreSQL; read the gate evaluations
4. fix whatever the gate refuses, and re-rehearse
5. **only then** seek explicit approval for the production migration and
   ingestion
6. **WP6** (decision D10) before that approval is sought, because without it
   the production run would record 174 symbols the Catalogue cannot show
