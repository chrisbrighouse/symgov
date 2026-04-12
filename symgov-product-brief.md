# symgov Product Brief

Last updated: 2026-04-09

## Product summary

`symgov` is a symbol-governance product for engineering symbols and published standards content.

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
- document-control or pack consumers

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

Current state:

- static HTML, CSS, and JavaScript prototype
- route-based navigation
- local demo data in `app.js`

Backend direction:

- FastAPI application server
- PostgreSQL system of record
- Redis where lightweight async coordination is justified
- S3-compatible object storage for SVG and export assets
- static front-end build behind a reverse proxy

Current first-phase VPS choice:

- use local MinIO for S3-compatible object storage until a managed external option is justified

## Current implementation baseline

The local prototype, architecture doc, README, and UI design packet are aligned around:

- route-based implementation
- queue-first Workspace review
- browse/detail/clarification Standards home
- published-only Standards contract
- clarification loop back into governance review
- no voting workflow in scope

## Supplemental operating model

The spreadsheet in `Documentation/Symgov` adds a future operating model with named specialist agents covering intake, provenance, validation, classification, coordination, publication, curation, audit, intelligence, and documentation.

Those agents are not implemented yet.

The spreadsheet should be treated as future operating-model guidance, not as a replacement for the current Symgov architecture or prototype.

## Recommended near-term roadmap

- browser-pass validation of current desktop and mobile prototype behavior
- decision on long-term role of guided lookup relative to the Standards clarification rail
- first agentization slice using independent agents with owned queues and durable outputs
- initial focus on `Vlad`, then `Scott`, `Tracy`, and `Daisy`

## Short positioning statement

Symgov is a governance-first publishing system for engineering symbols. It gives internal standards teams a queue-driven review and publication workspace while giving downstream users a clean published portal with strong traceability, explicit page context, and a governed clarification loop.
