# symgov Product Brief

Last updated: 2026-07-30 (source snapshot `182430932ae315f472b9e3611d54ad4f08cee038`)

## Product summary

`symgov` is a symbol-governance and reference product for engineering symbols and published standards content. It is not a broad drawing-management or document-management system.

It has two core surfaces:

- `Governance Workspace` for internal review, comparison, approval, publication, and audit
- `Standards View` for external or downstream consumers of approved published standards

The product is designed to keep internal governance workflows separate from published consumption workflows.

## Problem statement

Engineering organizations need a controlled way to:

- govern symbol revisions
- compare company variants against standards baselines
- publish only trusted approved outputs
- preserve traceability
- capture downstream clarification requests without exposing draft or review-state material to consumers

Symgov addresses that by separating the internal system of record from the published portal while keeping clarification and publication links explicit.

## Primary users

### Governance Workspace users

- standards owners
- methods leads
- QA and admin users
- reviewers and approvers

### Standards View users

- engineers
- contractors
- reviewers consuming approved content
- engineers and integrators consuming governed symbol references or packs

## Product surfaces

### Governance Workspace

Purpose:

- manage draft and review-state symbols
- triage change requests
- compare proposed changes against approved baselines
- review downstream impact on packs and published pages
- approve, reject, reassign, audit, and publish

Current layout direction:

- queue-first main review route
- active compare context
- approval rail
- linked clarification and impacted-page context

Focused supporting routes:

- governed record detail
- variant compare
- audit trail
- publish flow

### Standards View

Purpose:

- help users find the right approved symbol quickly
- show latest approved guidance and metadata
- expose downloads and pack context
- capture clarification questions tied to published symbol and page context

Current layout direction:

- browse/detail/clarification home route
- published-only content
- focused routes for full symbol reading, guided lookup, and downloads

## Product rules

- Standards shows only the latest approved published revision.
- Draft, in-review, and historical detail stay in Workspace.
- Clarifications raised from Standards route into governance review.
- Invalid Standards symbol routes must show a not-found state.
- Detail and compare SVGs are treated as accessible product content.
- Voting and proposal mechanics are out of scope for the current phase.

## Domain model

The current architecture centers on:

- `governed_symbols`
- `symbol_revisions`
- `change_requests`
- `review_decisions`
- `publication_packs`
- `pack_entries`
- `published_pages`
- `published_symbol_views`
- `impacted_page_links`
- `clarification_records`
- `clarification_links`
- `audit_events`

This keeps publication, clarification, and downstream page impact explicit instead of implicit.

## Core workflows

### Governance workflow

1. Draft or changed symbol content enters review.
2. The queue-first Workspace highlights owner, due date, risk, and downstream impact.
3. Reviewers compare baseline versus proposed content.
4. Clarifications tied to the affected published page or symbol are visible in the same review context.
5. Approvers decide whether to approve, request changes, reassign, or publish.

### Published consumption workflow

1. A user searches or browses approved symbols.
2. The main Standards route keeps the active published record visible while adjacent items remain browsable.
3. The active detail pane shows revision, pack, effective date, and page context.
4. The user can open downloads, full symbol reading, or guided lookup without leaving the published-only contract.
5. If clarification is needed, the user submits it against the current symbol and published page context.

## Experience and UI direction

The design direction is operational, broad-canvas, and pane-based rather than centered around narrow cards.

Key interface principles:

- queue density and review throughput matter in Workspace
- confirmation of latest-approved context matters in Standards
- symbol rendering must stay consistent across browse, detail, compare, and queue contexts
- SVG should be treated as content, not decoration
- accent usage should communicate state and action priority rather than visual flair

## Technical direction

Repository implementation at the audited source snapshot:

- React/Vite static SPA with authenticated, role-gated application routes
- FastAPI application server under `/api/v1`, with selected legacy `/api` aliases
- PostgreSQL/SQLAlchemy/Alembic system of record
- S3-compatible object storage for source, preview, derivative, and export assets
- specialist queue workers and repository-managed runners with durable database records plus bounded runtime-file handoffs
- individual Free/Plus subscriptions; self-service Plus is £50/year and does not grant privileged roles

Source code establishes implemented behavior, not whether a particular production deployment is active or current.

Recommended first-phase deployment profile:

- use an S3-compatible store such as local MinIO until a managed external option is justified; verify the selected environment independently

## Current implementation baseline

The repository source and current architecture documentation are aligned around:

- route-based implementation
- queue-first Workspace review
- browse/detail/clarification Standards home
- published-only Standards contract
- clarification loop back into governance review
- no voting workflow in scope

## Supplemental operating model

The specialist operating model now has implemented slices for intake, provenance, validation, classification, coordination, publication, curation, experience/feedback, intelligence, and Reggie queue-control/audit visibility. Broader automated compliance monitoring remains a planned extension. Historical spreadsheets and plans remain design evidence, not authority over current source.

## Recommended near-term roadmap

- continue the controlled-trial backlog in `docs/plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md`
- keep publication and withdrawal under explicit human governance
- benchmark classification and conversion quality before expanding automation authority
- preserve the individual £50/year Plus model unless a separate product decision changes it

## Short positioning statement

Symgov is a governance-first publishing system for engineering symbols. It gives internal standards teams a queue-driven review and publication workspace while giving downstream users a clean published portal with strong traceability, explicit page context, and a governed clarification loop.
