# symgov Governance — Architecture

Last updated: 2026-04-12

## Summary

Purpose
- `symgov` governs engineering symbols and publishes trusted standards content.
- The product stays split between internal `Governance Workspace` workflows and external `Standards View` consumption.

Implementation target
- The local frontend source is now a React/Vite application developed inside the `openclaw` container.
- The public VPS frontend may still reflect an older published bundle until the React/Vite build is intentionally published.
- `Standards View` uses a published-only home surface that combines browse, latest-approved detail, and clarification context, plus focused routes for symbol pages, downloads, and guided lookup.
- `Governance Workspace` uses a queue-first review surface with compare and approval context, plus focused routes for record detail, audit, and publish flow.

Product rules
- Standards shows only the latest approved published revision.
- Draft, review, and historical detail stay in Workspace.
- Clarifications raised from Standards create governance inputs tied to the published page and symbol context.
- Voting is out of scope for this phase.

## Core domain model

### Governance records

- `users`
  - id, email, display_name, role, created_at
- `governed_symbols`
  - id, slug, canonical_name, category, discipline, owner_id, current_revision_id, created_at
- `symbol_revisions`
  - id, symbol_id, revision_label, lifecycle_state, payload_json, rationale, author_id, created_at
- `attachments`
  - id, parent_type, parent_id, filename, object_key, content_type, size
- `audit_events`
  - id, entity_type, entity_id, action, actor_id, payload_json, created_at

### Source package and standards context

- `source_packages`
  - id, package_code, title, provider, package_type, status, created_at
- `source_package_entries`
  - id, source_package_id, symbol_revision_id, sort_order, source_label
- `standards`
  - id, standard_code, title, issuing_body, status, created_at
- `standard_versions`
  - id, standard_id, version_label, effective_date, status, created_at
- `symbol_standard_links`
  - id, symbol_revision_id, standard_version_id, relationship_type

This keeps provenance and reference context separate from publication output. A symbol revision may be associated with multiple source packages, multiple standards, or both.
Source-package membership may also be captured before normalization at intake/import time and later propagated into revision-level links.

### Review and publish workflow

- `change_requests`
  - id, symbol_id, change_type, revision_delta, status, priority, owner_id, due_date, reviewer_note
- `review_decisions`
  - id, change_request_id, decision, actor_id, note, created_at
- `publication_packs`
  - id, pack_code, title, audience, effective_date, status
- `pack_entries`
  - id, pack_id, symbol_revision_id, published_page_id, sort_order

### Published content model

- `published_pages`
  - id, page_code, title, pack_id, current_symbol_revision_id, effective_date
- `published_symbol_views`
  - materialized read model or API composition over the latest approved revision per symbol/page
- `impacted_page_links`
  - id, change_request_id, published_page_id, impact_type

### Clarification loop

- `clarification_records`
  - id, symbol_id, published_page_id, source, kind, status, submitted_by, detail, created_at
- `clarification_links`
  - id, clarification_id, change_request_id, linked_at

This model keeps published page membership, pack membership, clarification capture, and impacted-page relationships first-class instead of implicit.

## API shape

### Standards View

- `GET /api/v1/published/symbols`
  - list latest approved published records with search and pack filters
- `GET /api/v1/published/symbols/{symbol_id}`
  - latest approved symbol detail only
- `GET /api/v1/published/pages/{page_code}`
  - published page metadata and latest approved symbol/page payload
- `GET /api/v1/published/packs`
  - current published packs and export metadata
- `POST /api/v1/published/clarifications`
  - log a clarification or issue tied to a symbol/page context

Phase-1 published API rule:

- public Standards endpoints must only return `publication_packs.status = 'published'`
- public Standards endpoints must only return `publication_packs.audience = 'public'`
- `internal_preview` content remains a Workspace-only concern
- published page codes are generated from durable symbol and pack metadata rather than accepted from frontend input

### Governance Workspace

- `GET /api/v1/workspace/queue`
  - queue-first review list with owner, due date, risk, impacted pages, and impacted packs
- `GET /api/v1/workspace/change-requests/{id}`
  - active compare context, linked clarifications, and review metadata
- `POST /api/v1/workspace/change-requests/{id}/decision`
  - approve, request changes, mark ready, or reassign
- `GET /api/v1/workspace/review-cases`
  - Daisy-visible review cases with source preview context and latest decision summary
  - open split children are projected as first-class human-review items with `reviewItemType: "split_item"`, `parentReviewCaseId`, one child payload, and the child preview as the primary visual while their `review_split_items.status` is `awaiting_decision` or `returned_for_review`
- `POST /api/v1/workspace/review-cases/{id}/decisions`
  - record a whole-case human decision for non-split review cases, route approval to Rupert, and route non-approval outcomes to Libby
- `POST /api/v1/workspace/review-cases/{id}/split-items/process-decisions`
  - process only decided raster split child items, route approved children to Rupert and non-approved children to Libby, leave undecided children open, and close the parent split case only after all child items are processed
- `GET /api/v1/workspace/symbols/{symbol_id}`
  - governed record detail across lifecycle states
- `GET /api/v1/workspace/symbols/{symbol_id}/audit`
  - revision and publication traceability
- `POST /api/v1/workspace/publications`
  - publish approved scope into pack/page outputs

## Rendering and accessibility rules

- Treat SVG used in detail and compare surfaces as content, not decoration.
- Detail and compare SVGs must include `role="img"`, `title`, and `desc`.
- Redundant browse thumbnails can remain hidden from assistive technology when adjacent text identifies the symbol.
- Standards routes must never silently resolve an invalid symbol ID to another published record.

## Deployment direction

Initial backend target remains:
- FastAPI application server
- PostgreSQL for system of record
- Redis for cache and lightweight async coordination when needed
- S3-compatible object storage for SVG and export assets
- a frontend application developed inside the `openclaw` container, with the current VPS continuing to serve the published bundle until a new publication step is chosen

Frontend transition note for this phase:
- keep the current public frontend and nginx routing stable while the React/Vite frontend is developed locally in-container
- do not lock the final VPS publication shape yet
- choose the eventual VPS publication path later, once the React/Vite frontend is ready to publish

Operational simplifications for this phase:
- no proposal/vote subsystem
- no tally workers
- background processing only where export generation or notifications justify it

## Concrete database and storage draft

This section turns the current architecture into a first-pass backend persistence target. It is still a draft, but it is now concrete enough to drive schema design and migration planning.

### Persistence boundaries

- PostgreSQL remains the system of record for workflow state, metadata, approvals, clarifications, and agent runtime records.
- S3-compatible object storage remains the durable store for raw uploads, SVG assets, publication exports, and any large generated files.
- Redis remains optional and should stay out of the authoritative record path for phase 1.
- The first backend pass should prefer a single PostgreSQL database with one logical application schema family, not a fleet of specialized databases.

### Column conventions

- Use `uuid` primary keys for durable tables.
- Use `timestamptz` for all time fields.
- Add `created_at` to all durable records and `updated_at` where rows are expected to mutate.
- Use short text status columns with `CHECK` constraints rather than PostgreSQL enum types in phase 1 so workflow states can evolve through normal migrations.
- Use `jsonb` for flexible or evidence-heavy fields such as:
  - `payload_json`
  - `report_json`
  - `evidence_json`
  - `artifact_manifest_json`
  - `aliases_json`
  - `search_terms_json`
- Use explicit foreign keys for all `_id` relationships except where polymorphic links intentionally use `entity_type` plus `entity_id`.
- Add `deleted_at` only if soft delete becomes necessary later. For now, prefer immutable history plus status changes.

### Core relational tables

#### `users`

- `id uuid primary key`
- `email text not null unique`
- `display_name text not null`
- `role text not null`
- `created_at timestamptz not null`

Recommended constraints:

- `role` checked against the first role set used by Workspace
- unique lowercased email

#### `governed_symbols`

- `id uuid primary key`
- `slug text not null unique`
- `canonical_name text not null`
- `category text not null`
- `discipline text not null`
- `owner_id uuid not null references users(id)`
- `current_revision_id uuid null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Recommended notes:

- `current_revision_id` should point to the latest approved or active revision, not an arbitrary draft
- `slug` should be the stable public-facing identifier used by Standards routes

#### `symbol_revisions`

- `id uuid primary key`
- `symbol_id uuid not null references governed_symbols(id)`
- `revision_label text not null`
- `lifecycle_state text not null`
- `payload_json jsonb not null`
- `rationale text null`
- `author_id uuid not null references users(id)`
- `created_at timestamptz not null`

Recommended notes:

- `payload_json` should hold the structured symbol definition, not only free-form text
- `lifecycle_state` should cover draft, review, approved, published, deprecated at minimum
- add a unique constraint on `(symbol_id, revision_label)`

#### `attachments`

- `id uuid primary key`
- `parent_type text not null`
- `parent_id uuid not null`
- `filename text not null`
- `object_key text not null unique`
- `content_type text not null`
- `size_bytes bigint not null`
- `sha256 text null`
- `created_at timestamptz not null`

Recommended notes:

- use this table for durable references into object storage
- avoid storing large SVG or export blobs directly in PostgreSQL

#### `audit_events`

- `id uuid primary key`
- `entity_type text not null`
- `entity_id uuid not null`
- `action text not null`
- `actor_id uuid null references users(id)`
- `payload_json jsonb not null default '{}'::jsonb`
- `created_at timestamptz not null`

Recommended notes:

- treat audit rows as append-only
- use `payload_json` for diff fragments, routing detail, and external evidence references

### Source package and standards tables

These are distinct from `publication_packs`. `publication_packs` are Symgov-controlled release outputs. `source_packages` and `standards` represent upstream origin, grouping, and reference relationships.

#### `source_packages`

- `id uuid primary key`
- `package_code text not null unique`
- `title text not null`
- `provider text null`
- `package_type text not null`
- `status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Recommended notes:

- use this for named imported sets, supplier packages, standards appendices, or other upstream bundles
- `package_type` should distinguish imported source bundles from internal convenience groupings if both are allowed later
- package membership may begin at intake/import time before a submission is normalized into one or more symbol revisions

#### `source_package_entries`

- `id uuid primary key`
- `source_package_id uuid not null references source_packages(id)`
- `symbol_revision_id uuid not null references symbol_revisions(id)`
- `sort_order integer null`
- `source_label text null`
- `created_at timestamptz not null`

Recommended constraints:

- unique `(source_package_id, symbol_revision_id)`

Recommended notes:

- use this table for normalized revision-level package membership
- pre-normalization intake/package relationships should be captured separately on intake-side records and then reconciled into these rows during normalization

#### `standards`

- `id uuid primary key`
- `standard_code text not null unique`
- `title text not null`
- `issuing_body text null`
- `status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Recommended notes:

- examples include ISA, ISO, company standards, client standards, or regulated-house standards
- do not force one symbol to map to only one standard

#### `standard_versions`

- `id uuid primary key`
- `standard_id uuid not null references standards(id)`
- `version_label text not null`
- `effective_date date null`
- `status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Recommended constraints:

- unique `(standard_id, version_label)`

#### `symbol_standard_links`

- `id uuid primary key`
- `symbol_revision_id uuid not null references symbol_revisions(id)`
- `standard_version_id uuid not null references standard_versions(id)`
- `relationship_type text not null`
- `clause_reference text null`
- `notes text null`
- `created_at timestamptz not null`

Recommended constraints:

- unique `(symbol_revision_id, standard_version_id, relationship_type, clause_reference)`

Recommended notes:

- link at the revision level, not only the symbol level, because standard relationships can change across revisions
- `relationship_type` can cover values such as `derived_from`, `aligned_with`, `required_by`, or `referenced_by`

### Review and publication tables

#### `change_requests`

- `id uuid primary key`
- `symbol_id uuid not null references governed_symbols(id)`
- `proposed_revision_id uuid not null references symbol_revisions(id)`
- `base_revision_id uuid null references symbol_revisions(id)`
- `change_type text not null`
- `revision_delta text not null`
- `status text not null`
- `priority text not null`
- `owner_id uuid null references users(id)`
- `due_date date null`
- `reviewer_note text null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

#### `review_decisions`

- `id uuid primary key`
- `change_request_id uuid not null references change_requests(id)`
- `decision text not null`
- `actor_id uuid not null references users(id)`
- `note text null`
- `created_at timestamptz not null`

#### `publication_packs`

- `id uuid primary key`
- `pack_code text not null unique`
- `title text not null`
- `audience text not null`
- `effective_date date not null`
- `status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

#### `pack_entries`

- `id uuid primary key`
- `pack_id uuid not null references publication_packs(id)`
- `symbol_revision_id uuid not null references symbol_revisions(id)`
- `published_page_id uuid not null references published_pages(id)`
- `sort_order integer not null`
- `created_at timestamptz not null`

Recommended constraints:

- unique `(pack_id, symbol_revision_id, published_page_id)`

### Published content tables

#### `published_pages`

- `id uuid primary key`
- `page_code text not null unique`
- `title text not null`
- `pack_id uuid not null references publication_packs(id)`
- `current_symbol_revision_id uuid not null references symbol_revisions(id)`
- `effective_date date not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Recommended notes:

- in phase 1, a `published_page` is the canonical published detail record for one symbol revision in one publication context
- Standards search, browse, navigation, and listing surfaces should be implemented from read models and API composition around published records rather than by treating navigation pages as `published_pages` rows
- if a future release needs curated landing pages or multi-symbol documents as first-class managed content, model them separately instead of overloading `published_pages`
- phase-1 generated page code format is:
  - `<symbol_slug>-<revision_label>-<pack_code>`

#### `impacted_page_links`

- `id uuid primary key`
- `change_request_id uuid not null references change_requests(id)`
- `published_page_id uuid not null references published_pages(id)`
- `impact_type text not null`
- `created_at timestamptz not null`

#### `published_symbol_views`

- implement as a materialized view or publish-time read model table keyed by:
  - `symbol_id`
  - `page_id`
  - `pack_id`
- include denormalized fields needed for Standards browse:
  - slug
  - canonical name
  - category
  - discipline
  - revision label
  - effective date
  - current page code
  - current pack code
  - export availability

The initial recommendation is:

- use a materialized view if publish events are relatively infrequent
- switch to a write-through read model table only if browse latency or refresh cost becomes a problem

Decision for phase 1:

- start `published_symbol_views` as a materialized view refreshed by publication events
- revisit a write-through table only if refresh timing or browse scale proves it necessary
- current implementation note:
  - Rupert `--persist-db` writes the authoritative publication tables first
  - `refresh_published_symbol_views()` is a migration-owned security-definer function that lets the app role refresh the materialized view without owning it
  - public published APIs compose directly from `publication_packs`, `published_pages`, `pack_entries`, `symbol_revisions`, and `governed_symbols`; the refreshed materialized view remains available for future browse optimization

### Clarification and linkage tables

#### `clarification_records`

- `id uuid primary key`
- `symbol_id uuid not null references governed_symbols(id)`
- `published_page_id uuid not null references published_pages(id)`
- `source text not null`
- `kind text not null`
- `status text not null`
- `submitted_by uuid null references users(id)`
- `external_submitter_id uuid null references external_identities(id)`
- `detail text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Recommended constraints:

- require exactly one of `submitted_by` or `external_submitter_id`

Recommended notes:

- internal governance staff should link through `users`
- published-portal engineers, contractors, or other outside contributors should use lightweight external identities with no direct database access

#### `clarification_links`

- `id uuid primary key`
- `clarification_id uuid not null references clarification_records(id)`
- `change_request_id uuid not null references change_requests(id)`
- `linked_at timestamptz not null`

### Agent runtime tables

The backend database should adopt the agent record families already defined in the agent architecture.

#### `agent_definitions`

- `id uuid primary key`
- `slug text not null unique`
- `display_name text not null`
- `role text not null`
- `model text not null`
- `status text not null`
- `queue_family text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

#### `agent_queue_items`

- `id uuid primary key`
- `agent_id uuid not null references agent_definitions(id)`
- `source_type text not null`
- `source_id uuid not null`
- `status text not null`
- `priority text not null`
- `payload_json jsonb not null`
- `confidence numeric(5,4) null`
- `escalation_reason text null`
- `created_at timestamptz not null`
- `started_at timestamptz null`
- `completed_at timestamptz null`

Recommended notes:

- `source_type` plus `source_id` deliberately allows queue items to point at `intake_records`, `symbol_revisions`, `change_requests`, and later `review_cases`
- do not overload queue rows with full agent output; outputs belong in durable output tables

Decision for phase 1:

- keep `source_type` and `source_id` as separate polymorphic fields
- do not split agent runtime tables into typed foreign-key columns yet
- constrain allowed `source_type` values in application logic and database checks as the supported source families become stable

#### `agent_runs`

- `id uuid primary key`
- `queue_item_id uuid not null references agent_queue_items(id)`
- `model text not null`
- `prompt_version text not null`
- `tool_trace_json jsonb not null default '[]'::jsonb`
- `result_status text not null`
- `started_at timestamptz not null`
- `completed_at timestamptz not null`

#### `agent_output_artifacts`

- `id uuid primary key`
- `queue_item_id uuid not null references agent_queue_items(id)`
- `artifact_type text not null`
- `schema_version text not null`
- `payload_json jsonb not null`
- `created_at timestamptz not null`

Decision for phase 1:

- keep agent artifacts in PostgreSQL `jsonb` by default
- move only oversized artifacts to object storage later, using `attachments` or explicit object references when a real size or retention problem appears

#### `external_identities`

- `id uuid primary key`
- `display_name text not null`
- `email text null`
- `organization text null`
- `identity_type text not null`
- `status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

Recommended constraints:

- unique lowercased email where email is present

Recommended notes:

- use this table for lightweight non-staff identities that can submit clarifications or intake metadata without becoming Workspace application users
- these records are contact and provenance references only, not direct login principals for phase 1

#### `intake_records`

- `id uuid primary key`
- `queue_item_id uuid not null references agent_queue_items(id)`
- `source_type text not null`
- `source_ref text not null`
- `submitter text not null`
- `submission_kind text not null`
- `intake_status text not null`
- `eligibility_status text not null`
- `source_package_id uuid null references source_packages(id)`
- `raw_object_key text null`
- `normalized_submission_json jsonb not null`
- `routing_recommendation_json jsonb not null`
- `report_json jsonb not null`
- `created_at timestamptz not null`

Recommended notes:

- `source_package_id` is the first-class pre-normalization link when a raw submission belongs to a named imported or supplied package
- if one intake payload represents multiple packaged members before symbol splitting is complete, keep the full incoming membership detail in `normalized_submission_json` until downstream normalization creates revision-level `source_package_entries`
- keep `raw_object_key` in phase 1 even if `attachments` also exists, because intake handling benefits from a direct pointer to the original uploaded object before broader attachment linkage is normalized

#### `provenance_assessments`

- `id uuid primary key`
- `queue_item_id uuid not null references agent_queue_items(id)`
- `intake_record_id uuid not null references intake_records(id)`
- `rights_status text not null`
- `risk_level text not null`
- `confidence numeric(5,4) not null`
- `summary text not null`
- `evidence_json jsonb not null`
- `report_json jsonb not null`
- `assessed_at timestamptz not null`

#### `validation_reports`

- `id uuid primary key`
- `queue_item_id uuid not null references agent_queue_items(id)`
- `source_type text not null`
- `source_id uuid not null`
- `validation_status text not null`
- `defect_count integer not null`
- `normalized_payload_json jsonb not null`
- `report_json jsonb not null`
- `created_at timestamptz not null`

#### `classification_records`

- `id uuid primary key`
- `queue_item_id uuid null references agent_queue_items(id)`
- `source_id uuid not null`
- `source_type text not null`
- `category text not null`
- `discipline text not null`
- `aliases_json jsonb not null default '[]'::jsonb`
- `search_terms_json jsonb not null default '[]'::jsonb`
- `confidence numeric(5,4) not null`
- `created_at timestamptz not null`

### Coordination tables

#### `review_cases`

- `id uuid primary key`
- `source_entity_type text not null`
- `source_entity_id uuid not null`
- `current_stage text not null`
- `owner_id uuid null references users(id)`
- `escalation_level text not null`
- `opened_at timestamptz not null`
- `closed_at timestamptz null`

#### `review_split_items`

- `id uuid primary key`
- `review_case_id uuid not null references review_cases(id)`
- `child_key text not null`
- `proposed_symbol_id text not null`
- `proposed_symbol_name text not null`
- `file_name text not null`
- `parent_file_name text not null`
- `name_source text null`
- `attachment_object_key text null`
- `status text not null default 'awaiting_decision'`
- `latest_action text null`
- `latest_note text null`
- `latest_details text null`
- `latest_decision_id uuid null references human_review_decisions(id)`
- `latest_action_id uuid null references review_case_actions(id)`
- `downstream_agent_slug text null`
- `downstream_queue_item_id text null`
- `payload_json jsonb not null default '{}'::jsonb`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`
- `processed_at timestamptz null`

`review_split_items` materializes Vlad `derivative_manifest.children` for raster split review cases. Once materialized, each split child has its own human-review lifecycle. The open review list exposes child items with `awaiting_decision` or `returned_for_review` as individual split-item review records, including parent review-case lineage and a one-child decision payload. Processed children leave the human-review queue after routing to Rupert or Libby, while remaining or returned children stay available for later SME decisions even if the original parent sheet review has already closed.

#### `publication_jobs`

- `id uuid primary key`
- `pack_id uuid not null references publication_packs(id)`
- `status text not null`
- `requested_by uuid not null references users(id)`
- `approved_by uuid null references users(id)`
- `artifact_manifest_json jsonb not null`
- `created_at timestamptz not null`
- `completed_at timestamptz null`

#### `control_exceptions`

- `id uuid primary key`
- `source_type text not null`
- `source_id uuid not null`
- `severity text not null`
- `rule_code text not null`
- `detail text not null`
- `status text not null`
- `created_at timestamptz not null`
- `updated_at timestamptz not null`

### Object storage contract

The first backend pass should use one bucket per environment with stable prefixes:

- `raw-intake/`
  - original uploads before normalization
- `symbol-assets/`
  - approved and in-review SVG assets
- `publication-exports/`
  - generated publication bundles, PDFs, legends, mapping sheets
- `agent-artifacts/`
  - optional large agent outputs that are too large for direct `jsonb` storage

Recommended key shape:

- `<prefix>/<entity-family>/<entity-id>/<timestamp>-<filename>`

Examples:

- `raw-intake/intake-records/<id>/20260409T120000Z-contractor-upload.svg`
- `symbol-assets/symbol-revisions/<id>/current.svg`
- `publication-exports/publication-jobs/<id>/pack.zip`

Recommended object metadata:

- `content-type`
- `sha256`
- `source-system=symgov`
- `entity-type`
- `entity-id`

Current VPS-first implementation choice for early phases:

- use local MinIO as the S3-compatible object store before introducing a managed external provider
- run MinIO in the separate compose project at `/docker/symgov-minio`
- reach it from application containers on `ai-stack` at:
  `http://symgov-minio:9000`
- keep the MinIO console bound to loopback on the VPS at:
  `http://127.0.0.1:9001`
- keep one environment bucket per deployment; the current bucket is:
  `symgov-dev`

### Index and query strategy

Add these indexes in the first relational pass:

- unique:
  - `users(email)`
  - `governed_symbols(slug)`
  - `publication_packs(pack_code)`
  - `published_pages(page_code)`
  - `agent_definitions(slug)`
  - `symbol_revisions(symbol_id, revision_label)`
  - `external_identities(lower(email)) where email is not null`
- operational:
  - `source_packages(package_code)`
  - `standards(standard_code)`
  - `standard_versions(standard_id, effective_date desc)`
  - `symbol_revisions(symbol_id, created_at desc)`
  - `source_package_entries(source_package_id, sort_order)`
  - `source_package_entries(symbol_revision_id, source_package_id)`
  - `symbol_standard_links(symbol_revision_id, standard_version_id)`
  - `change_requests(status, priority, due_date)`
  - `change_requests(proposed_revision_id, status, created_at desc)`
  - `change_requests(base_revision_id, created_at desc)`
  - `review_decisions(change_request_id, created_at desc)`
  - `clarification_records(symbol_id, published_page_id, created_at desc)`
  - `clarification_records(external_submitter_id, created_at desc)`
  - `pack_entries(pack_id, sort_order)`
  - `impacted_page_links(change_request_id, published_page_id)`
  - `agent_queue_items(agent_id, status, priority, created_at)`
  - `agent_runs(queue_item_id, started_at desc)`
  - `agent_output_artifacts(queue_item_id, artifact_type, created_at desc)`
  - `intake_records(intake_status, eligibility_status, created_at desc)`
  - `provenance_assessments(intake_record_id, assessed_at desc)`
  - `validation_reports(source_type, source_id, created_at desc)`

Use `GIN` indexes on `jsonb` only after a real query path justifies them. The default posture should be relational lookup first, `jsonb` search second.

### Migration order

Recommended implementation order:

1. Base governance tables:
   - `users`
   - `governed_symbols`
   - `symbol_revisions`
   - `attachments`
   - `audit_events`
   - `external_identities`
2. Source context tables:
   - `source_packages`
   - `standards`
   - `standard_versions`
   - `source_package_entries`
   - `symbol_standard_links`
3. Review and publication tables:
   - `change_requests`
   - `review_decisions`
   - `publication_packs`
   - `published_pages`
   - `pack_entries`
   - `impacted_page_links`
4. Clarification tables:
   - `clarification_records`
   - `clarification_links`
5. Agent runtime tables:
   - `agent_definitions`
   - `agent_queue_items`
   - `agent_runs`
   - `agent_output_artifacts`
6. Agent durable output tables:
   - `intake_records`
   - `provenance_assessments`
   - `validation_reports`
   - `classification_records`
7. Coordination tables:
   - `review_cases`
   - `publication_jobs`
   - `control_exceptions`
8. Read models:
   - `published_symbol_views`

Migration note for the initial foreign-key cycle:

- create `governed_symbols.current_revision_id` as nullable in the first `governed_symbols` migration
- create `symbol_revisions` with the forward foreign key to `governed_symbols`
- backfill `current_revision_id` only after the first revision rows exist
- add the foreign key from `governed_symbols.current_revision_id` to `symbol_revisions(id)` in a follow-up migration after both tables are present

### Remaining design decisions

These items should be resolved before installation and migration planning:

- none at the schema-shape level for the first backend pass

### Search posture for phase 1

- defer full-text search indexes initially
- rely first on relational indexes, denormalized published read models, and structured retrieval fields for browse and agent/chat workflows
- revisit PostgreSQL full-text search only after we see a real corpus size or query pattern that cannot be handled well by the initial indexed model

## Notes for future implementation

- Keep Standards read models optimized around latest-approved published content.
- Keep Workspace APIs optimized around queue review, compare context, and impacted-page visibility.
- Clarifications should be linkable from both the published page context and the workspace queue item they influence.

## Installation requirements for the first backend pass

This section defines what must exist in an environment before Symgov backend installation is considered complete.

### Required runtime components

#### 1. Application runtime

- Python 3.12 or newer
- FastAPI application server
- ASGI process runner such as `uvicorn` or `gunicorn` with `uvicorn` workers
- database migration tool such as Alembic

Recommended baseline:

- use one application image or runtime for:
  - API serving
  - migration execution
  - lightweight background jobs if they are introduced

#### 2. PostgreSQL

Minimum role in phase 1:

- authoritative system of record for all relational data
- host for materialized views such as `published_symbol_views`
- host for agent runtime and durable output records

Recommended baseline requirements:

- PostgreSQL 16 preferred
- one database per environment
- one application schema family
- one migration-capable application role
- one read/write application connection path

Recommended extensions:

- `pgcrypto` for UUID generation if database-side UUID defaults are used

Expected database capabilities:

- transactional DDL for migrations
- `jsonb`
- materialized views
- standard btree and optional gin indexes

#### 3. Object storage

Minimum role in phase 1:

- store raw intake uploads
- store SVG assets
- store publication exports
- optionally store oversized agent artifacts later

Recommended baseline requirements:

- S3-compatible object API
- one bucket per environment
- application credentials limited to the environment bucket
- support for server-side encryption and lifecycle policies

Required bucket prefixes:

- `raw-intake/`
- `symbol-assets/`
- `publication-exports/`
- `agent-artifacts/`

#### 4. Redis

Phase 1 requirement:

- optional, not required for first installation

Use only if needed for:

- cache of read-heavy published browse responses
- lightweight job coordination
- short-lived background work state

Redis must not become the source of truth for review, publication, or agent records.

### Environment configuration requirements

The application installation should expect these configuration families:

#### Database

- `SYMGOV_DATABASE_URL`
- `SYMGOV_DB_POOL_SIZE`
- `SYMGOV_DB_MAX_OVERFLOW`

Example backend database-only environment snippet:

- `.env.backend.database.example`
- `.env.backend.database`

Expected use on this VPS:

- `SYMGOV_DATABASE_URL` should point at the least-privilege runtime role:
  `symgov_app`
- schema migration commands should use the separate `symgov_migrator` role
- both roles connect to the Docker hostname `symgov-postgres` on `ai-stack`

#### Object storage

- `SYMGOV_S3_ENDPOINT`
- `SYMGOV_S3_REGION`
- `SYMGOV_S3_BUCKET`
- `SYMGOV_S3_ACCESS_KEY_ID`
- `SYMGOV_S3_SECRET_ACCESS_KEY`
- `SYMGOV_S3_USE_SSL`

Example backend storage environment snippet:

- `.env.backend.storage`

Expected use on this VPS for the first backend phase:

- `SYMGOV_S3_ENDPOINT` should point at:
  `http://symgov-minio:9000`
- `SYMGOV_S3_BUCKET` currently points at:
  `symgov-dev`
- `SYMGOV_S3_USE_SSL` is currently:
  `false`
- MinIO is provisioned by:
  `/docker/symgov-minio/setup-symgov-minio.sh`

#### Application

- `SYMGOV_ENV`
- `SYMGOV_LOG_LEVEL`
- `SYMGOV_API_BASE_URL`
- `SYMGOV_PUBLIC_BASE_URL`

#### Optional Redis

- `SYMGOV_REDIS_URL`

#### Security and operations

- `SYMGOV_SECRET_KEY`
- `SYMGOV_CORS_ORIGINS`

### Database installation checklist

Before the application starts normally, the environment should have:

1. PostgreSQL reachable from the application runtime.
2. Application database created.
3. Migration role and runtime role created.
4. Required extension set installed:
   - `pgcrypto` if UUID defaults depend on it.
5. First migration set applied successfully.
6. Materialized view definitions created.
7. Baseline indexes present.
8. Initial seed data loaded where needed:
   - at minimum one admin-capable user
   - optional baseline `agent_definitions` for `Scott`, `Vlad`, and `Tracy` once backend queue wiring begins

### Object storage installation checklist

Before upload or publication flows are enabled, the environment should have:

1. Environment bucket created.
2. Required prefixes available by convention.
3. Application credentials verified for:
   - object put
   - object get
   - object head
   - object list on the environment bucket
4. Server-side encryption policy decided and enforced.
5. Lifecycle policy decided for:
   - retained raw intake uploads
   - publication exports
   - oversized artifacts if that path is used later

Current VPS implementation status as of 2026-04-09:

- local MinIO is provisioned for early Symgov phases
- bucket created:
  `symgov-dev`
- required prefixes created:
  - `raw-intake/`
  - `symbol-assets/`
  - `publication-exports/`
  - `agent-artifacts/`
- application-facing storage env snippet created:
  `.env.backend.storage`

### Application bootstrap order

Recommended first install order:

1. Provision PostgreSQL.
2. Provision object storage bucket and credentials.
3. Set environment variables and secrets.
4. Run database migrations.
5. Seed baseline users and optional agent definitions.
6. Start the FastAPI application.
7. Run a smoke check that verifies:
   - database connectivity
   - object storage connectivity
   - migration head is current
   - ability to read the published browse view

### Minimal environment profiles

#### Local development

- PostgreSQL required
- object storage required, but local S3-compatible service is acceptable
- Redis omitted unless a specific background/cache path is under development
- one application process is sufficient
- on this VPS, local S3-compatible storage is currently provided by MinIO in `/docker/symgov-minio`

#### Shared dev / staging

- PostgreSQL required
- object storage required
- Redis optional
- migrations run automatically or via deployment job
- smoke checks required after deploy

#### Production

- managed or operationally backed PostgreSQL strongly preferred
- durable S3-compatible object storage required
- Redis only if justified by actual cache/job use
- separate migration execution step required
- backups, restore testing, and secret rotation required

### Operational requirements

The first backend installation is not complete unless these are defined:

- PostgreSQL backup schedule and restore procedure
- object storage retention and deletion policy
- migration ownership and rollback policy
- application health checks
- basic audit log retention policy
- environment-specific secret management path

### What is not required for phase 1 installation

- full-text search service
- search-engine cluster
- proposal/voting subsystem
- heavy async worker fleet
- premium external model infrastructure

The first backend pass should stay intentionally small: one app runtime, one PostgreSQL database, one environment bucket, optional Redis, and migrations that match the schema already defined above.
