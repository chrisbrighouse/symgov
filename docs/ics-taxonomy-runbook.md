# ISO Open Data ICS import

## Source and boundaries

The authoritative input is `backend/symgov_backend/data/ics-source.json`, shared directly by the backend and Support component. ISO Open Data identifies **edition 7**, first published **2015**, source last updated **2025**. This is not an edition named 2025. The official page was independently read during implementation: https://www.iso.org/open-data.html#iso_ics . Its ICS section offers CSV, XML and OWL; JSONLines belongs to other datasets.

The vendored `ICS.csv` is the official source, not a synthetic fixture. `ICS.manifest.json` records a fresh official pull at `2026-09-15T20:34:34.088686+00:00`, source URL, Last-Modified `Tue, 25 Mar 2025 09:50:52 GMT`, edition, license, counts and crosswalk. SHA-256: `67e27bdc6559289c0d576e7b7b0b07023bc054ed87b907cdedf937e3c95e7434`. The CSV is 169768 bytes and contains 1381 nodes: 40 fields, 401 groups and 940 subgroups. English labels/scope notes are stored in governed nodes; the original French labels/scope notes are retained losslessly in archived source bytes, not exposed as a translated UI.

Taxonomy classification is based on the International Classification for Standards (ICS), 7th edition (2015), © ISO. Made available under the Open Data Commons Attribution License (ODC-By) v1.0.

ICS codes are used here only as a classification taxonomy. The underlying ISO/IEC standards documents catalogued under each code are separate, copyrighted works and are not reproduced in SymGov.

ISO's suggested citation: This work is based on the [iso_ics](https://www.iso.org/open-data.html#iso_ics) dataset from [ISO Open Data](https://www.iso.org/open-data.html), licensed under [ODC Attribution License (ODC-By) v1.0](https://opendatacommons.org/licenses/by/1-0/).

Browse: https://www.iso.org/standards-catalogue/browse-by-ics.html . The CSV excludes references/crosslinks present in XML/OWL. No standards documents, PDF, store download or third-party mirror is ingested. A real hierarchy is `13 → 13.220 → 13.220.20`. `29.020.01` does not exist in this release.

## Inspect and dry-run

Run from the repository root; dependencies are isolated with uv. No database connection is opened for dry-run. The archive directory must not already exist; choose a fresh path for each pull.

```sh
sha256sum backend/symgov_backend/data/ICS.csv
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt python -m symgov_backend.ics_import --help
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt python -m symgov_backend.ics_import --archive /tmp/ics-inspection-01
```

The fixed HTTPS endpoint is fetched with TLS validation, proxies disabled, redirects disabled, 10-second operation timeouts, a 30-second stream budget and a 2 MB byte limit. The exact approved content hash and six-column schema are pinned: any different content, edition or schema fails closed before database mutation. The CSV parser handles the official source's unescaped internal quotation marks; this is a pinned-release reader, not an arbitrary general-purpose CSV parser.

The JSON report and archived manifest include every candidate, its real ISO title, domain, relation, reason and review status. Archive writes are required even for dry-run. Download or validation failures produce a generic safe error rather than leaking driver/network credentials.

## Apply — separately approved production operation

No production apply or migration is authorized by this implementation task. An operator must first obtain approval for the target, run the standard migration release process through `20260916_0059`, and supply the approved database URL securely via the existing `SYMGOV_DATABASE_URL` environment variable. Do not put the URL or credentials in shell arguments, reports or tickets. The database role must have the necessary reference-table/import-ledger write privileges. Use an existing authorized operator's UUID:

```sh
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt python -m symgov_backend.ics_import --archive /tmp/ics-approved-apply-01 --apply --author-id "$ICS_OPERATOR_ID"
```

The CLI never runs migrations. It owns a transaction around the service import. The service validates again before mutation, serializes imports with a transaction advisory lock, and wraps writes in a savepoint; the caller must commit. It creates deterministic `ISO-ICS-7` scheme and node IDs and writes two durable records. `ics_taxonomy_imports` holds one immutable snapshot per `(scheme_id, content_sha256)`: raw source bytes plus each provenance field — dataset, edition, publication and source-update year, source/page/browse/license URLs, license code, attribution, clarification, limitation, `retrieved_at`, `Last-Modified` and digest — as separately queryable, constrained columns rather than one JSON blob. A database trigger rejects any UPDATE or DELETE on that table. `ics_domain_crosswalks` holds one row per domain-to-ICS proposal, each with composite foreign keys to both governed nodes, a `relation` of `broader` or `candidate`, a `review_status`, and a reason. These are browse proposals, **not** semantic concept/symbol assignments: they live in their own table and write nothing to `concept_classification_assignments` or `symbol_revision_classifications`. There is no name matching or auto-verification. Repeated identical imports retain the first successful pull provenance and do not duplicate rows or promote statuses. Each new external archive still records that pull's time.

All imported schemes and nodes begin as **draft**. Existing governance is unchanged. Activation requires separate governance authority; importing does not make these nodes review-assignment choices. Existing `list_classification_nodes(session, scheme_id, top_level_only=True)` and `parent_node_id=...` reads retain all hierarchy levels. The SME options route intentionally continues restricting choices to its existing allowed active schemes; no unrelated taxonomy or file-format/use-case facets are replaced.

Read back through an approved read-only SQL session:

```sql
SELECT scheme_code, version_label, status FROM classification_schemes WHERE scheme_code = 'ISO-ICS-7';
SELECT dataset, edition, publication_year, source_update_year, retrieved_at,
       last_modified, content_sha256, license_code, source_url, length(source_bytes)
FROM ics_taxonomy_imports ORDER BY retrieved_at;

SELECT s.node_code AS domain_code, s.preferred_label AS domain,
       t.node_code AS ics_code, t.preferred_label AS ics_title,
       x.relation, x.review_status, x.reason,
       x.reviewed_by_user_id, x.reviewed_at, x.review_note
FROM ics_domain_crosswalks x
JOIN classification_nodes s ON s.id = x.source_node_id
JOIN classification_nodes t ON t.id = x.target_node_id
ORDER BY s.preferred_label, t.node_code;

SELECT n.node_code, n.preferred_label, p.node_code AS parent_code
FROM classification_nodes n LEFT JOIN classification_nodes p ON p.id = n.parent_node_id
JOIN classification_schemes s ON s.id = n.scheme_id
WHERE s.scheme_code = 'ISO-ICS-7' ORDER BY n.node_code;
```

## Refresh and recovery

Never overwrite the old hierarchy to accommodate changed content. Unknown bytes require deliberate source/license/edition review and a new approved release; a new edition should get a new versioned scheme and retain old provenance. Same-edition content changes require an explicitly designed snapshot version rather than changing the pinned hash alone. A re-import against an existing `ISO-ICS-7` scheme fails closed rather than repairing anything. It requires the matching snapshot row with byte-identical `source_bytes`, all 1381 nodes with unchanged code, label, scope note and parent, and the full crosswalk with unchanged source node, target node, relation and reason. Only the reviewer's own facts are exempt -- `review_status`, `reviewed_by_user_id`, `reviewed_at` and `review_note` -- because human review is expected to move a row to `approved` or `rejected`; that progression stays idempotent. `reason` is the *import's* rationale for proposing the pair and is compared; `review_note` is the *reviewer's* rationale for the disposition and is not. Any other drift raises and leaves the database untouched, so a deleted or edited crosswalk row is a review item, not something a re-run silently rewrites. The ledger/source archive enables replay via `prepare_import(source_bytes, retrieved_at=..., last_modified=...)` and `ingest_taxonomy` in an operator-owned transaction. These are trusted operator/library interfaces, not public API endpoints.

Do not delete history or manufacture an identifier. Downgrade locks the affected tables and refuses if import provenance or dotted codes exist; it only succeeds on an empty ICS installation. If the command fails after a commit but before writing its final report, inspect the ledger before retrying; identical imports are idempotent. The CLI's error is not a claim that a commit was impossible.

## Recording a review decision

`ics_import` proposes; `ics_review` dispositions. They are separate commands because an import reaches the network and writes an archive, while a review decision touches one already-governed row and reaches nothing outside the database. Neither runs migrations. Listing is read-only and safe to run at any time.

```sh
# The outstanding queue, in the terms the decision is actually made in.
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt \
  python -m symgov_backend.ics_review list --review-status needs_review

# One decision, by a named reviewer, with their own rationale.
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt \
  python -m symgov_backend.ics_review approve \
  --crosswalk-id "$CROSSWALK_ID" --reviewer-id "$REVIEWER_ID" \
  --note 'Narrower target is the right browse entry.'
```

`list` prints each proposal's domain name, ICS code and ICS title alongside the `crosswalk_id` the decision commands take, because no reviewer can choose between `13.220 Protection against fire` and `13 Environment. Health protection. Safety` from a pair of UUIDs. Supply `SYMGOV_DATABASE_URL` through the environment, never in shell arguments, reports or tickets. An absent setting is named in the error, because naming a setting leaks nothing; every other failure prints a message matched to the command that failed rather than driver text that could carry credentials.

A disposition is **never anonymous**: `--reviewer-id` is required, and `ck_ics_domain_crosswalks_disposition_attributed` refuses in storage any row that is `approved` or `rejected` without both a reviewer and a decision time. A raw `UPDATE ics_domain_crosswalks SET review_status='approved'` therefore fails closed. Deleting the reviewer's user record sets `reviewed_by_user_id` to null and leaves the decision standing but unattributed; the decision itself is not deleted with them.

A decision may be **corrected but not undone**. A reviewer may move an `approved` row to `rejected` or the reverse, each time recording the new reviewer, moment and note; no transition returns a row to `needs_review` or `initial_broader`. This differs deliberately from `CLASSIFICATION_ASSIGNMENT_TRANSITIONS`, where `rejected` is terminal because the assignment can be proposed afresh. A crosswalk row cannot be proposed afresh -- it is derived from a pinned import, and a re-import leaves `review_status` alone -- so a terminal disposition would strand a mistake behind the very raw SQL this command replaces.

Approving a proposal is a **browse** decision and nothing more. It writes no row to `concept_classification_assignments` or `symbol_revision_classifications`, and does not activate the scheme or its nodes, which remain `draft` until separate governance authority says otherwise.

Downgrading `20260916_0059` locks the table and refuses while any decision is recorded, so review attribution cannot be quietly discarded.

## Mapping decision register

All mappings are broader browse suggestions, never exact semantic equivalences; approving one does
not claim the domain and the ICS field mean the same thing. Chris dispositioned all 22 proposals on
2026-09-16 under a single governing reading: **the crosswalk points at the discipline's subject
matter, not at the standards that govern the discipline's symbols.** That is why, for example,
Electrical resolves to `29 Electrical engineering` rather than to
`01.080.40 Graphical symbols for use on electrical and electronics engineering drawings`.

22 proposals, 16 approved and 6 rejected. Every one of the eleven domains retains at least one
approved target, so no domain drops out of browse.

| Domain | ICS target | Decision | Why |
|---|---|---|---|
| Electrical | `29` Electrical engineering | approved | The subject-matter field for the discipline. |
| Fire & Life Safety | `13.220` Protection against fire | approved | Precise; its children already cover fire-fighting, fire protection, ignitability and fire resistance. |
| Fire & Life Safety | `13` Environment. Health protection. Safety | rejected | Redundant as a browse entry — `13.220` is its child, so wider life safety is reachable through the parent chain. |
| Piping / P&ID | `23` Fluid systems and components for general use | approved | The physical discipline. P&ID is the notation, not the subject. |
| Piping / P&ID | `01.100` Technical drawings | rejected | Drawing conventions, and `01.100` has no P&ID subgroup. |
| Process | `71` Chemical technology | approved | Process is genuinely cross-sector; one target would misfile the others. |
| Process | `75` Petroleum and related technologies | approved | As above. |
| Process | `27` Energy and heat transfer engineering | approved | As above. |
| Instrumentation & Controls | `25.040.40` Industrial process measurement and control | approved | Exactly the discipline, and the only subgroup-level target in the set. |
| Instrumentation & Controls | `17` Metrology and measurement. Physical phenomena | rejected | Measurement as a science, not plant instrumentation. |
| Mechanical | `21` Mechanical systems and components for general use | approved | A symbol library draws components, not production processes. |
| Mechanical | `25` Manufacturing engineering | rejected | About making parts; would also overlap Instrumentation & Controls, whose target sits under `25`. |
| HVAC | `91.140.10` Central heating systems | approved | Building services; covers the H, incl. burners and boilers. |
| HVAC | `91.140.30` Ventilation and air-conditioning systems | approved | Building services; covers the V and the AC, incl. ducts. |
| HVAC | `27` Energy and heat transfer engineering | rejected | Thermal engineering, adjacent but not a building system. |
| Civil / Structural | `91` Construction materials and building | approved | Serves the structural/materials half of a deliberately double-barrelled domain. |
| Civil / Structural | `93` Civil engineering | approved | Serves the civil half — earthworks, roads, bridges, hydraulic construction. |
| Architectural | `91` Construction materials and building | approved | Subject-matter field. Shares `91` with Civil / Structural, which is acceptable for a browse crosswalk: one ICS field may serve two disciplines. |
| Safety / Signage | `13` Environment. Health protection. Safety | approved | The safety half. |
| Safety / Signage | `01.080` Graphical symbols | approved | The signage half; `01.080.10 Public information symbols. Signs. Plates. Labels` is a direct hit. This is the one domain whose name genuinely carries both readings. |
| General / Annotation | `01.100` Technical drawings | approved | Annotation *is* a drawing convention — dimensioning, tolerancing, title blocks, line types. |
| General / Annotation | `01.080` Graphical symbols | rejected | Keeps `01.080` to Safety / Signage. |

**These decisions are not yet recorded in any database.** No ICS import has been applied outside
disposable test PostgreSQL, so the 22 rows do not exist anywhere to disposition. The table above is
the human rendering of `backend/symgov_backend/data/ics-crosswalk-decisions.json`, which is the
machine source `apply-register` reads; `test_ics_decision_register.py` asserts the two agree, and
that the register and `CROSSWALK` describe the same 22 pairs.

Apply the whole register once the import has landed on an approved target. It is a dry run unless
`--apply` is given, exactly like the importer:

```sh
# Resolve and print the plan. Records nothing; exits non-zero on any mismatch.
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt \
  python -m symgov_backend.ics_review apply-register --reviewer-id "$REVIEWER_ID"

# Record all 22 decisions in one transaction.
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt \
  python -m symgov_backend.ics_review apply-register --reviewer-id "$REVIEWER_ID" --apply
```

Each row still passes through `disposition_crosswalk`, so the transition map, the named-reviewer
rule and the storage attribution check apply exactly as they would to a hand-made decision, and the
reviewer id is yours rather than the register's -- the register records decisions, not authority.
`--import-id` scopes the run when more than one import exists.

The application **fails closed** on any disagreement between the register and the rows actually
present: a register entry with no matching row, a live row the register is silent about, or an
ambiguous `(domain, ics_code)` pair. Nothing is applied partially. It is also **idempotent** -- a
row already carrying the register's decision is reported as `already_recorded` and left alone, so
re-running is a no-op. A row carrying the *other* decision is refused unless `--allow-correction`,
so an older register can never silently overwrite a reviewer's later correction.

`crosswalk_id` values cannot be precomputed -- they hash the live `ENGINEERING-DISCIPLINE` node ids
-- which is why the register keys on `(domain, ics_code)` and the ids are resolved at apply time.
To disposition a single row by hand instead, read the ids back with:

```sh
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt \
  python -m symgov_backend.ics_review list --review-status needs_review \
  | jq -r '.[] | [.crosswalk_id, .domain, .ics_code] | @tsv'
```

Re-running the import after the register is applied is safe: `review_status`,
`reviewed_by_user_id`, `reviewed_at` and `review_note` are the reviewer's own facts and are exempt
from the drift check.

A rejected row is a recorded decision, not a deletion -- it stays in the table with its attribution,
and re-import leaves it alone. Approving a row is a browse decision only: it writes nothing to
`concept_classification_assignments` or `symbol_revision_classifications`, and the scheme and its
nodes stay `draft` until separate governance authority activates them.

If a domain's right target is not among its proposals — for instance pointing Electrical at
`01.080.40` after all — that is a `CROSSWALK` code change plus a fresh import, not a disposition,
because `source_node_id` and `target_node_id` are imported facts that a re-import compares.

The full actual `CATALOG_DISCIPLINE_ORDER` is validated on every preparation. No additional separate fixed semantic-domain vocabulary was found during inspection; semantic concepts must not be mistaken for domains by preferred-name matching.

## Verification

```sh
PYTHONPATH=backend uv run --isolated --with-requirements backend/requirements.txt --with-requirements backend/requirements-test.txt python -m pytest tests/test_ics_taxonomy.py tests/test_ics_import_safety.py tests/test_ics_cli.py tests/test_ics_persistence_foundation.py tests/test_ics_crosswalk_review.py tests/test_ics_decision_register.py tests/test_ics_storage.py tests/test_ics_register_storage.py tests/test_classification_data_model.py tests/test_classification_data_model_postgresql.py -q --tb=short
node --test frontend/src/supportDataSources.test.js
npm run test:frontend
SYMGOV_BUILD_OUT_DIR=/tmp/ics-isolated-build npm run build:isolated
```

Docker is required for the repository's disposable PostgreSQL harness. No shared database is used. Storage evidence covers committed import, second-session readback, repeated import/CLI apply, failure on the final ledger insertion with zero partial rows, malformed prepared input, crosswalk drift refused while an approved review status stays idempotent, the full review loop -- queue, decision, correction, refusal of an unattributed or unknown-reviewer decision, CLI disposition with the database URL absent from output, and a downgrade refused while a decision is recorded -- snapshot immutability enforced by trigger, legacy/dotted service and SQL checks, hierarchy readers, zero semantic assignments, empty upgrade/downgrade/re-upgrade and nonempty downgrade refusal. Frontend evidence renders attribution/links and traverses the actual authenticated `/support` App route while preserving local support submission.
