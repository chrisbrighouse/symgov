# symgov SVG rendering patterns

## Principles

- Use the same SVG source consistently across browse cards, detail stages, compare panels, and exports.
- Treat SVG as product content, not decoration. It needs stable sizing, legible strokes, and predictable loading.
- Published pages render only the latest approved revision asset.

## When to use inline SVG

Use inline SVG for:

- Symbol cards where the SVG is part of the clickable information hierarchy
- Large detail stages that need accessible titles and descriptions
- Compare views that require overlays, highlighted deltas, or state-specific styling
- Representative prototypes and any UI that needs per-record color, stroke, or label variation

Benefits:

- Direct CSS control for stroke, fill, hover, and focus states
- Easy accessibility metadata with `title` and `desc`
- Straightforward annotation layers for compare and review tools

Costs:

- Larger DOM when many symbols are rendered at once

## When to use SVG sprite references

Use `<symbol>` sprite references for:

- Repeated, simple icons inside controls and badges
- Very large queue lists where the same symbol shape appears many times without variation
- Cases where thumbnail interactivity is limited to selection rather than per-path styling

Avoid sprite references for compare overlays or any screen that needs path-level highlighting.

## Lazy-loading approach

- Render above-the-fold cards inline on initial load.
- Defer off-screen browse results with list virtualization or intersection-based hydration when the queue becomes large.
- In Workspace lists, prefer placeholder frames with dimensions reserved to avoid layout shift.
- Preload the active record SVG and its baseline/proposed pair when a queue item is selected.

## Sizing and rendering rules

- Standardize viewBox usage so browse, detail, and compare states scale from the same source geometry.
- Keep stroke widths proportional to screen density. Do not let thumbnails become muddy through automatic scaling alone.
- Reserve whitespace around the symbol stage. The art should not touch card edges.
- Use `preserveAspectRatio="xMidYMid meet"` for most catalog and detail cases.

## Accessibility

- Add `role="img"` plus `title` and `desc` on detail-stage SVGs.
- Hide purely redundant thumbnail SVGs from assistive tech when the card text already identifies the symbol.
- Ensure status and revision are text, not embedded into the SVG art.

## Published-page rule

- The public Standards page should request and render the latest approved SVG only.
- Historical approved revisions remain available to Workspace audit and compare tools, not the published detail page.
- If a new approval supersedes a page, the public URL should resolve to the new latest-approved payload without exposing draft intermediates.

## Lightweight implementation recommendation

- Initial pass: inline SVG for Standards browse/detail and for the active Workspace compare record.
- Optional optimization later: sprite or cached SVG fragments for very large Workspace queues.
