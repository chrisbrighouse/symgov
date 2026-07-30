# Symgov documentation map

This map describes documentation at source snapshot `182430932ae315f472b9e3611d54ad4f08cee038`. It does not claim that an external production deployment matches the repository. Runtime, migration, gateway, and public-service state require separate operational evidence.

Symgov is an engineering-symbol governance and reference product, not a broad drawing-management or document-management system. Where commercial positioning is relevant, the current model is individual Free/Plus access with Plus at **£50/year**; Plus does not itself grant privileged roles.

## Classifications

- **canonical** — maintained description of current source behavior, product boundaries, API contracts, or operating commands.
- **current-plan** — active forward plan whose status should be reconciled as source changes.
- **historical** — point-in-time plan, restart note, decision, verification record, or evidence. Its verdicts and runtime claims are preserved as historical facts rather than rewritten as current state.
- **generated-or-reference** — design packet, handoff/reference material, task list, or generated-style supporting documentation. Use it as input, not as current implementation authority.

The machine-readable file-by-file inventory is [`audit-current-state.json`](audit-current-state.json). It lists every tracked Markdown file, its classification, whether this refresh changed it, and a concise reason.

## Canonical current documentation

- [Repository overview](../README.md) — source-backed product and implementation overview, quality gates, and deployment boundary.
- [Product brief](../symgov-product-brief.md) — product purpose, users, boundaries, and £50/year commercial context.
- [Governance architecture](../symgov-governance-architecture.md) — domain and API architecture; draft/recommended sections remain explicitly non-implemented guidance.
- [Agent architecture](../symgov-agent-architecture.md) — specialist-agent responsibilities and queue/runtime contracts.
- [Review decision orchestration](../symgov-review-decision-orchestration.md) — human decision, Libby/Daisy/Vlad, and publication-handoff rules.
- [Backend guide](../backend/README.md) — backend commands, authentication/subscription contracts, API notes, and external-runtime boundary.
- [BTX workflow](btx-submission-workflow.md) — supported Bluebeam BTX intake and conversion contract.
- [Catalog Integration API](catalog-api/README.md) — API entry point, with [quickstart](catalog-api/quickstart.md), [recipes](catalog-api/integration-recipes.md), [errors/security](catalog-api/errors-and-security.md), and [changelog](catalog-api/CHANGELOG.md).
- [Langfuse isolated POC](../langfuse-poc/README.md) — synthetic-only POC boundary and commands; it explicitly is not production.

## Current plan

- [Trial-readiness implementation backlog](plans/2026-07-26-symgov-trial-readiness-implementation-backlog.md) — active source-status and dependency sequence. At this snapshot F0.1–F0.4 are complete in source, F0.4 is not deployment-verified by repository evidence, and F0.5 is next.

## Historical records

The dated files under [`plans/`](plans/) and [`restart-notes/`](restart-notes/) are immutable point-in-time plans, handoffs, completion records, and operational snapshots. The `.hermes/plans/` files and `artifacts/reviews/` report are also historical. They may intentionally contradict later source or later operational evidence; follow the current plan and canonical docs for present guidance, while preserving the old verdict in its original context.

## Generated and reference material

- [`../ui-design/`](../ui-design/) contains the design packet and prototypes; it is reference material, not a claim that every surface is implemented.
- [`../integrations/btx/SymGov_BTX_Integration_Handoff/`](../integrations/btx/SymGov_BTX_Integration_Handoff/) contains the original BTX handoff, fixtures, and format analysis. The canonical operating contract is the BTX workflow above.
- [`../references/task-list.md`](../references/task-list.md) is a supporting task reference, not the current roadmap.

## Documentation rules

1. Ground “current” claims in source, config, migrations, and tests at a named commit.
2. Never infer production availability, migration state, or deployment version from source alone.
3. Preserve historical decisions and test/deployment evidence; add a newer record or update the current plan instead of rewriting old verdicts.
4. Use repository-relative commands where possible. Keep host-specific paths only when they are an explicit external-runner or operational contract.
5. Update canonical docs and this map when routes, commands, environment names, product boundaries, or commercial terms change.
