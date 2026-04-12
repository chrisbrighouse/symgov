# symgov component list and layout grid

## Shared components

- `AppShell` for top-level frame, experience switch, and responsive gutters
- `GlobalSearch` for symbol, pack, page, and queue lookup
- `StatusBadge` for published, in review, ready, blocked, and impact states
- `SymbolCard` with inline SVG thumbnail, symbol ID, title, revision, and compact metadata
- `SymbolStage` for large SVG presentation in detail and compare views
- `FactRow` for compact key-value metadata
- `ActionBar` for primary and secondary actions
- `EmptyState` for no results, no pending changes, or missing route/record states

## Standards View components

- `BrowsePane` with searchable approved results
- `PublishedDetailPane` showing latest approved revision only
- `ClarificationContextPane` bound to current symbol and published page context
- `GuidedLookupRoute` for focused conversational lookup
- `DownloadRoute` for pack and export browsing
- `PublishedSymbolRoute` for a dedicated per-symbol reading page

### Standards desktop grid

- 12-column grid
- Browse pane: columns 1-4
- Published detail pane: columns 5-8
- Clarification context pane: columns 9-12
- Collapse to a single column on mobile with browse, detail, then clarification order

### Standards interaction priority

- Search and scan first
- Confirm latest approved detail second
- Ask clarification third
- Use focused routes only when deeper reading or export actions are needed

## Workspace View components

- `QueueToolbar` with saved views, filters, and bulk-selection controls
- `QueueList` with dense, scan-friendly records
- `QueueSelectCard` with owner, due date, impact count, and status
- `CompareWorkspace` for baseline versus proposed review
- `DeltaSummaryPanel` for changed attributes, notes, and impacted pages/packs
- `ApprovalRail` for direct reviewer actions and visible risk context
- `LinkedClarificationsPanel` for Standards-originated questions attached to the active record
- `ImpactedPagesPanel` for downstream published-page visibility
- `GovernedRecordRoute`, `AuditRoute`, and `PublishRoute` for focused follow-through

### Workspace desktop grid

- 12-column grid
- Queue and bulk tools: columns 1-4
- Compare and change details: columns 5-9
- Approval rail: columns 10-12
- Secondary full-width strip below for clarifications and impacted published pages

### Workspace interaction priority

- Triage many records first
- Review active compare second
- Approve or request changes third
- Use focused routes for audit or full record inspection when needed

## Minimal layout guidance

- Desktop outer gutters: 24 to 32px
- Pane gap: 16 to 20px
- Surface radius: 14 to 20px
- Keep approval and queue context visible on long screens where possible
- Treat detail and compare SVGs as accessible product content with titles and descriptions
