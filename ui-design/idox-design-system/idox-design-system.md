---
description: Generate UI using the Idox design system (Tailwind v4, Font Awesome, DM Sans). Produces design-system-compliant HTML markup.
argument-hint: <component-or-scenario>
---

# Skill: Idox Design System

## Invocation behaviour

**With an argument** (e.g. `/idox-design-system date picker`): generate the named component or scenario using the design system. Output ready-to-use markup or component code, then a brief generation report noting any token gaps, icon gaps, or source conflicts encountered.

**Without an argument**: summarise the design system (tech stack, token set, available component sections) and await a specific instruction.

In both cases: read `component-examples.html` in full before generating any markup. That file is the authoritative source for all component patterns. When anything in this skill conflicts with `component-examples.html`, the HTML file wins.

## Files

| File | Purpose |
|---|---|
| `design-tokens.css` | Source of truth — all `--idox-*` CSS custom properties. Link in `<head>` before your Tailwind stylesheet. |
| `component-examples.html` | Canonical component patterns and copyable `ai-example-start/end` blocks. |
| `page-example.html` | Reference full-page layout implementation. |
| `assets/idox_logo_reversed.png` | White Idox logo for dark navbars. |

## ai-example-start / ai-example-end convention

`component-examples.html` contains documentation wrappers (labels, variant headers, outer demo containers) that are not part of the copyable markup. The only HTML to copy for code generation is the content between:

```html
<!-- ai-example-start: <id> -->
…
<!-- ai-example-end: <id> -->
```

All markup outside those markers is display-only. Copy these blocks verbatim; do not adapt the surrounding demo HTML.

## Tech stack

| Concern | Technology |
|---|---|
| CSS framework | Tailwind CSS v4 — NOT v3 |
| Interactive components | `@tailwindplus/elements@1` — required for dropdown, split button, and dialog interactions |
| Icons | Font Awesome only — no custom SVGs |
| Font | DM Sans (400, 500, 600, 700) |
| Tokens | `--idox-*` CSS custom properties from `design-tokens.css` |
| Token aliases | `@theme inline` block in CSS maps `--idox-*` to Tailwind utility names |

## Font and icons in index.html

```html
<!-- DM Sans font -->
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:ital,opsz,wght@0,9..40,400;0,9..40,500;0,9..40,600;0,9..40,700&display=swap" rel="stylesheet">
<!-- Font Awesome -->
<script src="https://kit.fontawesome.com/84881d618d.js" crossorigin="anonymous"></script>
```

## Token class names — complete reference

### Surface and text

| Class | Resolves to | `--idox-*` value |
|---|---|---|
| `bg-surface-default` | white | `#ffffff` — individual panels, inputs |
| `bg-surface-light` | near-white | `#fcfdfe` — page body background for full-page layouts |
| `bg-surface-base` | slate-50 | `#f8fafc` — table headers, hover backgrounds, secondary backgrounds |
| `bg-surface-subtle` | slate-100 | `#f1f5f9` — hover backgrounds, table row tints |
| `bg-surface-muted` | slate-200 | `#e2e8f0` |
| `text-primary` | slate-900 | `#0f172a` |
| `text-secondary` | slate-700 | `#334155` |
| `text-tertiary` | slate-500 | `#627288` |
| `text-inverse` | white | `#ffffff` |
| `text-brand` | blue-800 | `#0A1F8F` |
| `text-brand-accent` | blue-600 | `#195FD2` |

### Borders and focus

| Class | Resolves to | `--idox-*` value |
|---|---|---|
| `border-default` | slate-300 | `#cbd5e1` — cards, panels |
| `border-subtle` | slate-200 | `#e2e8f0` — dividers |
| `border-strong` | slate-400 | `#94a3b8` — form input borders |
| `border-focus` | blue-600 | `#195FD2` — focus ring AND border colour |

### Links

| Class | Resolves to |
|---|---|
| `text-link` | blue-600 |
| `text-link-hover` | blue-700 |
| `text-link-active` | blue-800 |

Link full class set: `text-link hover:text-link-hover active:text-link-active underline rounded-sm outline-none focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-accent`

### Interactive (strong — filled backgrounds)

| Class | Use |
|---|---|
| `bg-interactive-default` | Primary/active filled background |
| `bg-interactive-hover` | Hover state for filled interactive |
| `bg-interactive-active` | Active/pressed state for filled interactive |
| `text-interactive-text` | Text on filled interactive background (white) |
| `text-interactive-default` | Blue text for active/selected state indicators (e.g. active tab label, selected sidebar item) |

### Interactive (subtle — tinted backgrounds)

| Class | Use |
|---|---|
| `bg-interactive-subtle` | blue-50 — icon-only button default, selected item default |
| `bg-interactive-subtle-hover` | blue-100 — hover on ghost/icon-only buttons |
| `bg-interactive-subtle-active` | blue-200 — pressed on ghost/icon-only buttons |
| `bg-interactive-subtle-selected` | blue-100 — sidebar active link |

### List rows

| Class | Use |
|---|---|
| `bg-list-hover` | Hover row in dropdown/autocomplete list |
| `bg-list-active` | Pressed row in dropdown/autocomplete list |

### Buttons (semantic tokens — always use these, never raw colours)

| Class | Use |
|---|---|
| `bg-btn-primary` | Primary button background |
| `bg-btn-primary-hover` | Primary button hover |
| `bg-btn-primary-active` | Primary button pressed |
| `text-btn-primary-text` | Primary button label (white) |
| `bg-btn-secondary` | Secondary button background (slate-50) |
| `bg-btn-secondary-hover` | Secondary button hover |
| `bg-btn-secondary-active` | Secondary button pressed |
| `text-btn-secondary-text` | Secondary button label (slate-900) |

### Navbar

| Class | Use |
|---|---|
| `bg-navbar-bg` | Navbar background (blue-800) |
| `text-navbar-text` | Navbar text (white) |
| `bg-navbar-hover` | Navbar item hover |
| `bg-navbar-active` | Navbar item active |
| `text-navbar-icon` | Navbar icon (white) |
| `text-navbar-icon-hover` | Navbar icon hover (white) |

### Status

Each status has six utility classes following the same suffix pattern (`-bg`, `-border`, `-icon`, `-text`, `-heading`, `-text-color`):

| Prefix | Use |
|---|---|
| `info-` | Informational — blue tones |
| `success-` | Success / approved — green tones |
| `warning-` | Warning / on hold — amber tones |
| `error-` | Error / refused / validation failure — red tones |

### Overlay and disabled

| Class | Use |
|---|---|
| `bg-overlay` | Dialog/modal backdrop (slate-950 at 50% opacity) |
| `bg-surface-disabled` | Disabled form control background |
| `text-disabled` | Disabled form control text |

**Body background:** Full-page layouts use `bg-surface-light` (near-white body), not `bg-surface-base`. Individual panels use `bg-surface-default` (white).

## Spacing

The spacing scale is defined as `--idox-space-*` CSS custom properties in `design-tokens.css` (2px–64px, matching the Tailwind default scale). Use standard Tailwind utilities in markup — `p-4`, `gap-2`, `mt-1.5` etc. The tokens exist as the single source of truth for the scale values and as the foundation for future density control.

## Border radius

| Context | Class |
|---|---|
| Card / panel container | `rounded-xl` |
| Input, button, dropdown, select | `rounded-lg` |
| Navbar icon button | `rounded-md` |
| Dropdown / split-button menu item | `rounded-md` |
| Date-picker footer action button | `rounded-md` |
| Toast dismiss button | `rounded-md` |
| Table row action icon button | `rounded-md` |
| Custom checkbox / radio | `rounded` |
| Dialog close button | `rounded` |

## UX component choice guide

Use this table to select the right component for a given interaction before generating any markup. If the scenario is ambiguous, apply the tiebreaker notes.

### Input controls

| Scenario | Component | Section |
|---|---|---|
| Short free text (name, reference number, UPRN) | Text input | §3 |
| Long free text (description, notes, reason) | Textarea | §10 |
| One choice from ≤15 known, fixed options | Select | §8 |
| One choice from a large or searchable space (contacts, agents, organisations) | Combobox lookup | §11 in `component-examples.html` |
| Date entry | Date picker | §9 |
| Number or text with a fixed prefix/unit (£, E, N) | Prefix input | §6 |
| Address search or any input requiring a lookup action | Input with action button | §7 |
| Binary on/off, independent of other fields | Checkbox | §22 |
| Mutually exclusive choice from ≤5 options shown inline | Radio buttons | §23 |

### Actions

| Scenario | Component | Notes |
|---|---|---|
| Primary save / submit / confirm | Primary button | One per form/section maximum |
| Cancel / back / secondary dismiss | Secondary button | |
| Low-emphasis supplementary action (share, export) | Ghost button | Must include an icon — text-only → use secondary |
| Single action in a dense toolbar | Icon-only button | Must have `aria-label` |
| Inline action on a field (calendar open, postcode search) | Input action button | Part of the composite input pattern |
| Dropdown of multiple actions on a record | Dropdown menu | §17 |

**Primary button budget:** one primary button per panel/form. If two equal-weight actions compete, both become secondary.

## UI generation rules

- Match token usage, class patterns, and structure exactly to `component-examples.html`
- Compose new UI from existing patterns and tokens only — do not introduce new visual styles, colours, or components. Flag anything that cannot be assembled from what exists in `component-examples.html`
- Flag any deviation from reference files explicitly in the generation report
- Before submitting any component, verify that `hover:`, `active:`, and `cursor-pointer` are present on all interactive elements — these are invisible in static previews and easy to omit

## Writing guidelines

- Sentence case for all UI text — capitalise proper nouns, brand names, and established acronyms (WCAG, API, UPRN)
- End punctuation in body text and error messages; omit from headings and button labels
- Active voice, present tense. No exclamation marks
- Do not use: `please`, `sorry`, `thank you`, `&`, `e.g.`, `i.e.`, `etc.`, `oops`, `forbidden`, `illegal`, `invalid`
- Avoid directional language — use "previous" not "above"; "following" not "below"
- Use device-independent language — use "select" or "choose", not "click"

## Accessibility — WCAG 2.2 AA

- All interactive controls must be focusable and operable via keyboard
- Use `aria-label` on icon-only buttons
- All form inputs must have an associated `<label>` element
- Placeholders are supplementary only when they add necessary context — never use placeholder as the sole label
- Focus styles differ by element type — see Per-component class rules for exact strings: inputs/selects use `focus:ring-1 focus:ring-focus`; buttons use `focus-visible:outline-solid focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand-accent`; checkboxes/radios use `peer-focus-visible:ring-2 peer-focus-visible:ring-focus`
- Colour contrast: 4.5:1 for text, 3:1 for UI components
- All `<th>` elements must have `scope="col"` — no exceptions
- ARIA roles must follow WAI-ARIA Authoring Practices
- Use semantic HTML landmarks: `<header>`, `<nav>`, `<main>`, `<footer>`
- Decorative icons use `aria-hidden="true"`; meaningful icons require visible or `sr-only` text

## Hard rules

Current AI models fill in gaps by interpolating from the broader web, not this design system. Each rule below addresses a failure that recurs in practice without it.

- Never use arbitrary value syntax (`bg-[#4F46E5]`, `mt-[13px]`) — raise as a token gap. Exception: `text-[10px]` and `text-[4px]` are permitted for decorative chevron and dot separator icons where no standard token exists; dot separators at `text-[4px]` may also use `fa-solid` (filled dot is the correct visual at this size)
- Never use `<div>` or `<span>` as interactive elements — use `<button>` or `<a>`
- `fa-light` is the default FA icon weight. Use `fa-solid` only for active/selected state indicators (e.g. active sort carets, `aria-current` items). Use `fa-regular` for `fa-ellipsis` and for checkbox/radio checkmarks. Never use `fa-solid` for decorative or neutral icons
- Hardcoded colour classes are forbidden — `bg-blue-600`, `text-white`, `border-red-500` must all become token equivalents. Error states: `border-error-border`, `text-error-icon`. Status panels: `bg-success-bg`, `text-error-heading`, etc. Flag as a token gap rather than use a raw Tailwind ramp
- Never reference undefined tokens — verify every `bg-*`, `text-*`, `border-*` class against the token reference above. `bg-surface-raised` is NOT defined (use `bg-surface-subtle`). Flag any unrecognised token rather than use it




