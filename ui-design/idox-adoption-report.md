# Idox design system — adoption report

How the Idox design system in `ui-design/idox-design-system/` was applied to the
symgov frontend, and what could not be applied.

## Approach

The Idox skill targets Tailwind CSS v4 and expresses every component as a string
of utility classes. symgov is React 19 on Vite with a hand-written stylesheet and
no Tailwind. Rather than introduce Tailwind, the token layer was vendored as
plain CSS custom properties and each Idox component specification was applied to
the existing class names.

| File | Role |
|---|---|
| `frontend/src/idox-tokens.css` | Vendored copy of `design-tokens.css`, verified identical token-for-token. Re-sync from source when Idox ships a new version. |
| `frontend/src/styles.css` | Imports the tokens, maps the legacy palette aliases onto them, and carries the component rules. |
| `frontend/src/catalogDeveloper.css` | Developer hub palette remapped onto the same tokens. |
| `frontend/index.html` | DM Sans at 400, 500, 600 and 700. |

## What was applied

- **Colour.** Every hard-coded colour in the frontend now resolves to an
  `--idox-*` token. The retired brand teal, the warm accent palette, the
  glassmorphic translucency and the ambient orb backdrop are all gone.
- **Components.** Buttons follow §buttons-primary, §buttons-secondary and
  §buttons-ghost. Inputs, selects and textareas follow §3, §8 and §10. Panels
  follow §panel-default, tables §table-basic, tabs §tabs-default, badges
  §badge-semantic, callouts §callout-*, dialogs §dialog and tooltips §tooltip.
- **Navigation.** The top banner is now the Idox navbar on blue-800. The side
  rail has no Idox equivalent and is styled as an Idox panel using the
  sidebar-item selection tokens.
- **Typography.** DM Sans throughout. Every `font-weight` snapped to the four
  weights the system defines; the previous stylesheet used 650, 750, 800, 850
  and 900, none of which DM Sans is loaded at.
- **Accessibility.** A global `focus-visible` treatment, `scope="col"` added to
  the four table headers that lacked it, and all decorative animation disabled
  under `prefers-reduced-motion`.

## Token gaps

The system defines four semantic ramps (info, success, warning, error) and no
categorical palette. Three things could not be assembled from what exists.

1. **Workspace lane identity.** The processing board previously gave each of its
   eleven lanes a distinct header tint. Those tints are now uniform. Lanes remain
   distinguishable by heading and position. A categorical palette would restore
   the previous wayfinding.
2. **Purple.** Used previously for "rename and classify" decisions and for
   set-membership and status-scope badges. Now neutral.
3. **Orange.** Used previously for "duplicate" decisions. Now mapped to error,
   which matches how duplicate is treated everywhere else.

A fourth gap is a **destructive button**. Idox defines no danger variant, so
`.action-button.danger` is assembled from the error status tokens.

## Icon gap

The system specifies Font Awesome with `fa-light` as the default weight. That
weight is Font Awesome Pro, available only through the Idox kit, which is
domain-restricted. symgov keeps its existing sixteen inline SVG icons, restyled
to the Idox stroke weight and colour tokens. The select chevron, which Idox draws
with a Font Awesome icon, is inlined as a background image in the same stroke
colour.

To close this gap, symgov's domain needs adding to the Idox Font Awesome kit
allowlist. The icons can then be swapped in one pass.

## Deviations from the reference

- **Table padding.** Idox specifies `px-5` for its three-column example. The
  symgov operator grids run to a dozen columns, so horizontal padding uses
  `space-3`. Vertical rhythm and all colours follow the system.
- **Zebra striping removed.** Idox tables use row hover only. The previous
  even-row tint is gone. Reversible if operators find dense grids harder to scan.
- **Segmented control kept.** Idox has no segmented-control component. The
  existing form is retained with Idox selection tokens.

## Defects found and fixed along the way

- `.ghost-button` had no CSS rule anywhere, so the header sign-in, sign-out and
  switch-organisation controls rendered as unstyled browser buttons.
- The organisation selection screen targeted a dark theme through custom
  properties that were never defined, so it rendered as a dark island in a light
  application.
- `--text-muted`, `--text`, `--accent-strong`, `--shadow-soft` and `--radius-sm`
  were referenced but never defined. All now resolve.
- `.field select` used `appearance: none` with no chevron, so selects were
  indistinguishable from text inputs.

## Verification

- `npm run build` — green.
- `npm run test:frontend` — 366 tests, 366 pass.
- Every token pair in use meets WCAG 2.2 AA. The only pair below 4.5:1 is
  disabled text on the disabled surface, which is the system's own definition and
  is exempt under WCAG 1.4.3.

## Not applied

- Font Awesome icons, as described under **Icon gap**.
- All-caps micro-labels remain on status values and format codes. Idox asks for
  sentence case, but those strings come from data whose casing was not verified,
  so changing them needs a content review.
