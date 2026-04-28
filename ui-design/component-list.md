# symgov component list and layout grid

## Shared components

- `AppShell` for top-level frame, experience switch, and responsive gutters
- `TopBanner` for the full-width light header with engineering-symbol logo, primary `Submissions`/`Reviews`/`Standards` area buttons, version/date pill, and cog link to Workspace
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

- `WorkspaceTitlebar` with `ADMIN WORKSPACE` / `Activity Monitors`, search, and live/seeded feed status
- `MonitorSummaryRow` with compact counts for visible, active, escalated, and published activity
- `QueueMonitorBoard` with eight vertical lanes for Scott, Vlad, Tracy, Libby, Daisy, Human Review, Rupert, and Ed
- `MonitorColumn` with count, stage label, scrollable card stack, and compact footer status
- `MonitorCard` with label, truncated title, source metadata, status, and priority dot
- `GovernedRecordRoute`, `AuditRoute`, and `PublishRoute` for focused follow-through

### Workspace desktop grid

- Full-width admin canvas
- Summary metrics above the monitor board
- Eight monitor lanes across HD desktop screens
- Horizontal board overflow on narrower desktop screens
- Single-column stacking on small screens

### Workspace interaction priority

- Monitor processing activity first
- Search by batch, file, agent, status, or case second
- Approve or request changes third
- Use focused routes for audit or full record inspection when needed

## Minimal layout guidance

- The top banner spans the viewport width and keeps Workspace as an icon-only administrative entry point so the primary text navigation stays focused on Submissions, Reviews, and Standards.
- Desktop outer gutters: 24 to 32px
- Pane gap: 16 to 20px
- Surface radius: 14 to 20px
- Keep approval and queue context visible on long screens where possible
- Treat detail and compare SVGs as accessible product content with titles and descriptions
