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

- `BrowsePane` with searchable approved results; visible table labels use `ID` for the symbol identifier and `Name` for the published payload name
- `PublishedDetailPane` showing latest approved revision only; when opened from the browse grid it sits in the right side of the browser grid and uses the same height cap as the approved-symbol grid
- `SupplementalPhotoStrip` for Hannah-attached real-world equipment photo references. It is separate from the schematic symbol preview and displays at most two public attached photos.
- `ClarificationContextPane` bound to current symbol and published page context
- `GuidedLookupRoute` for focused conversational lookup
- `DownloadRoute` for pack and export browsing
- `PublishedSymbolRoute` for a dedicated per-symbol reading page

### Standards desktop grid

- 12-column grid
- Facets pane: left columns
- Approved-symbol grid: center columns
- Published detail pane: right columns when a row is selected, scrolling internally to keep `Close` reachable
- Collapse to a single column on mobile with facets, grid, then detail order

### Standards interaction priority

- Search and scan first
- Confirm latest approved detail second
- Ask clarification third
- Use focused routes only when deeper reading or export actions are needed

## Workspace View components

- `WorkspaceTitlebar` as a persistent `ADMIN WORKSPACE` bar with `Agents`, `Sources`, `Curation`, and `Intelligence` tabs
- `WorkspaceContentTitlebar` for tab-specific headings: `Activity Monitors` on Agents with chevron monitor-screen navigation, queue search, and refresh status controls; `Sources` on Sources; `Curation` on Hannah's curation workspace; and `Demand Sensing` on Whitney's market intelligence workspace
- `WorkspaceMonitorStatusRow` as a compact single-line live status control in the Agents content titlebar, including last refresh time and `Auto-refresh 5s` state while polling is active
- `QueueMonitorBoard` with two monitor screens: `Pipeline` shows Scott, Vlad, Tracy, Libby, Daisy, Human Review, and Rupert; `Intelligence` shows Hannah, Whitney, and Ed from left to right
- `MonitorColumn` with count, stage label, and an internally scrollable card stack; duplicate footer counts are omitted
- `MonitorCard` with London-local `HH:MM DDMMMYY` time/date label as the first visible row where live timestamps are available, short package/symbol display name as the second visible row, source metadata, optional Vlad `Process` line, optional Standards target for published Rupert cards, status on its own line under the activity string, and priority dot. Agent queue cards use `createdAt`; Human Review cards use review `openedAt`, including individual split-item review records.
- `ScottSourceDiscoveryPanel` for the Workspace `Sources` tab. It renders the always-visible source memory grid, showing URL first with status and title prominent on the left, and supports sortable/filterable columns with infinite scroll instead of paging. It includes a `Next run` checkbox column with checked/unchecked filtering and a candidate-only Scott prompt editor for source access/download instructions.
- `HannahCurationPanel` for the Workspace `Curation` tab. It renders Hannah's scored published-symbol photo candidates with source/domain, rights status, license, score, symbol context, sortable/filterable columns, and infinite scroll. Its titlebar controls mirror Scott's source-search pattern: `Start search`, a running countdown, and a red `Stop` button while a curation run is active.
- `WhitneyDemandSignalsPanel` for the Workspace `Intelligence` tab. It renders Whitney's demand signals with signal type, market segment, demand score, confidence, recommendation, source, symbol/page context, sortable/filterable columns, and infinite scroll. Its titlebar controls mirror Scott and Hannah: `Start scan`, a running countdown, and a red `Stop` button while a demand scan is active.
- Human Review cards link to the matching Reviews item. Rupert cards become clickable only after durable public publication exists, display `PUBLISHED`, and link to Standards View through the backend-provided published symbol target.
- Workspace display names use backend-provided `displayName`: submitted sheets and single-symbol packages show the 4-character uppercase hex package ID such as `0001`, while extracted symbols show `{packageId}-{sequence}` such as `0001-1` or `0001-999`. Long filenames and proposed symbol names stay in detail/search context rather than the compact card title.
- `GovernedRecordRoute`, `AuditRoute`, and `PublishRoute` for focused follow-through

### Workspace desktop grid

- Full-width admin canvas
- Persistent Workspace tabs in the Admin Workspace bar
- Agents content titlebar with chevron monitor-screen navigation, search, and status controls above the monitor board
- Equal-height monitor lanes across HD desktop screens, split into a seven-lane first screen for Scott through Rupert and a second screen for Hannah, Whitney, and Ed that keeps the same lane width with blank space to the right
- Sources tab uses the same full-width admin canvas for the source-memory grid
- Curation tab uses the same full-width admin canvas for Hannah's curation controls and candidate grid
- Intelligence tab uses the same full-width admin canvas for Whitney's demand scan controls and demand-signal grid
- Horizontal board overflow on narrower desktop screens
- Single-column stacking on small screens

### Workspace interaction priority

- Monitor processing activity first
- Search by batch, status, or case second
- Inspect Scott source-discovery progress from the Sources grid when administering web-search inputs, mark source rows for Scott's next run, and add candidate-only prompts that help Scott retrieve symbol files from selected sources
- Inspect Hannah curation progress and photo candidates from the Curation grid when administering published Standards quality
- Inspect Whitney demand signals from the Intelligence grid when prioritizing catalogue coverage or market-driven follow-up
- Approve or request changes third
- Use focused routes for audit or full record inspection when needed

## Reviews View components

- `ReviewFilterGrid` for stage, reviewer, priority, action, and search-based triage
- `ReviewQueuePane` for Daisy-visible cases, compact queue cards, and previous/next movement through filtered items
- `ReviewSourceVisual` for source-image evidence, child-preview fallback, glyph fallback, and the reviewer-editable symbol properties beside the graphic
- `ReviewFocusPane` for source facts, classification facts, Libby summary, and child-symbol decision cards
- `SplitReviewCard` for per-child preview, metadata, open review status, action buttons, reviewer note, and requested detail
- `ReviewDecisionPane` for case-level action buttons, reviewer identity, case comment, decision note, latest decision, submit state, review notes, and Daisy coordination on non-split cases.
- For raster split cases, `ReviewDecisionPane` becomes a simpler `Process Symbols` panel with Ready / Waiting / Total counts, selected child-action counts, and a `Process Selected Symbols` button that stays disabled until at least one child has a non-pending decision. It does not show whole-file case action controls because split batches can contain mixed outcomes.
- `ReviewDecisionPane` should surface the recorded downstream action after submit: Rupert handoff for approval, Libby follow-up for non-approval, and later Vlad graphic-change routing only when Libby requests it
- The symbol identifier is labelled `ID`; the review properties are `Name`, `Description`, editable `Category`, and editable `Discipline`, with read-only `Format` rendered as a compact file-format badge under the description. Category and Discipline expose explicit saved-value selectors beside free-text inputs.

### Reviews desktop grid

- Three-column reviewer workbench
- Left: review queue
- Center: visual evidence, facts, and child-symbol decisions
- Right: sticky decision rail
- Collapse to a single column on tablet and mobile with queue, review item, then decision order

### Reviews interaction priority

- Pick or advance to a review case first
- Inspect the visual evidence and classification/source facts second
- Record child-symbol actions and notes third
- For raster split cases, process any decided child-symbol subset without deciding the whole parent sheet.
- For non-split cases, record the case decision and Daisy-aware comments without leaving the workbench.

## Minimal layout guidance

- The top banner spans the viewport width and keeps Workspace as an icon-only administrative entry point so the primary text navigation stays focused on Submissions, Reviews, and Standards.
- Desktop outer gutters: 24 to 32px
- Pane gap: 16 to 20px
- Surface radius: 14 to 20px
- Keep approval and queue context visible on long screens where possible
- Treat detail and compare SVGs as accessible product content with titles and descriptions
