**SYMGOV**

Symbol Set Management Spec

Personal and commercial users, organizations, projects, private symbols and community contribution

**Draft v0.3**
6 August 2026

<table>
<colgroup>
<col style="width: 100%" />
</colgroup>
<thead>
<tr class="header">
<th><strong>Purpose of this draft</strong><br />
Establish a product and technical baseline that can be iterated with stakeholders and then converted into implementation epics and Codex tasks. This revision adds personal users without organizations, the reserved Symgov organization and protected platform-administrator model, project descriptions, generated organization icons and Hermes-agent oversight. Product-specific viewer integrations have been removed so the specification focuses on generic Symgov functionality.</th>
</tr>
</thead>
<tbody>
</tbody>
</table>

Prepared for the Symgov project

# Document control

| **Field**          | **Value**                                                                                                      |
|--------------------|----------------------------------------------------------------------------------------------------------------|
| Document title     | Symbol Set Management Spec                                                                                     |
| Version            | Draft v0.3                                                                                                     |
| Date               | 6 August 2026                                                                                                  |
| Status             | Product decisions incorporated; for continued iteration                                                       |
| Primary audience   | Symgov product owner, developer, tester, organization administrators and Codex                                |
| Related capability | Public Catalog, governed symbols, publication workflow, Favorites, organizations, projects and Symbol Sets    |

# Contents

- 1\. Executive summary
- 2\. Scope and design principles
- 3\. Current Symgov alignment
- 4\. Terminology and conceptual model
- 5\. Personas and roles
- 6\. Product use cases
- 7\. Functional requirements
- 8\. Data model
- 9\. Permissions and access resolution
- 10\. Symbol, set and administration lifecycles
- 11\. User experience
- 12\. Contribution and gamification
- 13\. Analytics and telemetry
- 14\. Security, compliance and non-functional requirements
- 15\. Migration and delivery phases
- 16\. Acceptance criteria
- 17\. Decisions and questions for iteration
- Appendix A. Draft API surface
- Appendix B. Initial Event Catalog
- Appendix C. Current-code reference points

# 1. Executive summary

Symgov must support two valid account patterns. A non-commercial user has no organization membership and continues to use the Public Catalog through the existing Free or Plus subscription model. A commercial user belongs to one or more organizations and works inside a selected organization context. The same User account can therefore remain the common identity model without forcing every user into a commercial tenant.

After email/PIN authentication, a commercial user with more than one active organization membership selects one organization. The issued session is locked to that organization, and changing organization requires sign-out and a new sign-in. A user with one organization can enter it automatically. A user with no organizations enters personal mode and does not see organization projects, private symbols or Symbol Sets.

Commercial customers need reusable groups of symbols that match a project or task, combining approved public symbols with symbols owned privately by their organization. All active users in the selected organization may choose from that organization's active projects; no per-user project assignment is required. Each project has a name and an optional short description of no more than 50 characters to help users distinguish similar projects.

Within a project, a user has one active Symbol Set at a time and can switch easily among sets made available to that project. The effective palette consists of the active set plus any approved organization-owned symbols marked organization-wide. Organization-owned and public symbols use the same governed symbol record. Publication changes visibility rather than creating a copy, so existing set references remain valid.

Organization governance is deliberately simple. The first user in a new commercial organization becomes an Organization Administrator, and every organization must retain at least one administrator. Administrators may appoint Organization Reviewers; any one reviewer may explicitly approve an organization symbol. When an organization is created without an uploaded icon, Symgov generates a unique temporary icon that can later be replaced.

Platform administration is governed through a reserved organization with the code `symgov`. A user must be an administrator of this Symgov organization before they can be assigned the separate Platform Administrator role. At least one Platform Administrator must always exist. Platform Administrators can appoint additional administrators to the Symgov organization and assign eligible users as Platform Administrators, subject to last-administrator safeguards.

Hermes-agent oversight should support the new model. An Organization Steward capability can monitor reviewer coverage, review backlogs, generated-icon replacement and administrator continuity within one organization. A Platform Governance capability can monitor platform-admin continuity, suspicious cross-tenant attempts and unresolved governance issues. Agents advise, summarize and escalate through approved services; they do not autonomously approve symbols, grant roles, demote public content or bypass deterministic authorization.

Gamification initially provides reputation, badges and progress measures. Public attribution identifies the contributing company only. Real commercial benefits may be introduced later after the scoring model and economics have been validated.

<table>
<colgroup><col style="width: 100%" /></colgroup>
<thead><tr class="header"><th><strong>Primary recommendation</strong><br />
Maintain personal Free/Plus accounts as a first-class mode, and implement Organization → Project → Symbol Set as the commercial working hierarchy. Protect global administration through the reserved Symgov organization, scope each commercial session to one organization, overlay approved organization-wide symbols, and use Hermes agents for oversight without delegating governed decisions.</th></tr></thead><tbody></tbody>
</table>

# 2. Scope and design principles

## 2.1 In scope

- Non-commercial users with no organization membership using the existing Free/Plus model.
- Commercial users with one or more organization memberships.
- Organization selection during sign-in and an organization-scoped user session.
- A reserved Symgov organization and protected Platform Administrator assignment.
- Projects available for selection by all active users in the signed-in organization.
- An optional project description of no more than 50 characters.
- Reusable Symbol Sets containing public and organization-owned symbols.
- Assignment of Symbol Sets to one or more organization projects.
- One active Symbol Set at a time, with rapid switching among project-available sets.
- Organization-wide symbols automatically available across the organization.
- Organization-private symbol submission, explicit organization review and optional Public Catalog submission.
- A single governed symbol record whose visibility may move between organization-private and public.
- Automatic creation of a unique temporary organization icon when no icon is supplied.
- Contribution scores, badges and organization-level recognition.
- Usage, contribution and adoption analytics.
- Hermes-agent oversight for organization health, platform governance and issue escalation.
- A future-capable data model for immutable Symbol Set releases, without requiring release UI in the first implementation.

## 2.2 Out of scope for the first implementation

- Per-user project assignment or project-access administration inside Symgov.
- Combining several Symbol Sets into one active palette.
- Immutable Symbol Set release creation and management in the first delivery, although the design must not block it.
- Real-time collaborative editing of symbol artwork.
- A public marketplace or direct payment to contributors.
- Commercial rewards linked to contribution points.
- Offline Symbol Set use, offline private-content caching or queued offline activity.
- Replacing the current email/PIN model with SSO or service credentials.
- Product-specific viewer or document-management integration behavior.
- Autonomous agent approval, role assignment, publication, demotion or tenant-policy changes.

## 2.3 Design principles

| **Principle**                              | **Implication**                                                                                                      |
|--------------------------------------------|----------------------------------------------------------------------------------------------------------------------|
| Personal mode remains first-class          | Users without organizations retain the existing Free/Plus experience and are not forced into a tenant.              |
| Reference, do not copy                     | Sets point to governed symbols; publication changes visibility and never creates a duplicate public symbol.          |
| One organization per commercial session    | Multi-organization users choose at sign-in and must sign out before changing organization.                           |
| Projects are organization contexts         | Active organization users can choose any active project; no user-project assignment table is required.              |
| One active set                             | A user works with one set at a time but can switch rapidly among sets available to the selected project.             |
| Organization-wide means automatic          | Approved organization-wide symbols are available in every project/set context without repeated assignment.           |
| System administration is explicitly rooted | Platform Administrators must be eligible administrators of the reserved Symgov organization.                         |
| Governed and personal are separate         | Symbol Sets are shared and curated; Favorites and recent symbols remain personal convenience features.              |
| Explicit approval                          | Any one appointed organization reviewer may approve, but approval must always be an affirmative recorded action.     |
| Generated identity has a safe fallback     | Every organization has an icon immediately; uploaded branding can replace the generated temporary icon.             |
| Agents advise; services authorize          | LLM agents may detect, summarize and recommend, but deterministic application services enforce every permission.     |
| Quality beats volume                       | Contribution rewards are based on accepted, useful outcomes rather than raw submissions.                             |
| Online first                               | Initial use requires Symgov connectivity; offline set packages are deferred.                                          |
| American English UI                        | Product labels use American spelling, including Catalog, Organization, Favorite, Authorized and Localization.        |
| Audit every governed change                | Membership, role, set, symbol scope, approval, publication, demotion and agent-raised actions are auditable.          |

# 3. Current Symgov alignment

This proposal extends existing Symgov concepts rather than replacing them. The current model already provides foundations that should be reused:

| **Existing foundation** | **Current capability**                                                                            | **Proposed extension**                                                                                 |
|-------------------------|---------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------------------------------|
| Users and roles         | Current roles include admin, integrator, submitter and reviewer.                                  | Keep platform roles and add organization membership roles plus protected Platform Administrator assignment. |
| Subscriptions           | Users currently have Free or Plus subscriptions.                                                  | Preserve these for personal/non-commercial use; add organization entitlements separately.             |
| Favorites               | A user-to-symbol favorite relationship already exists.                                            | Retain as personal convenience, not as the governed set implementation.                                |
| Catalog API keys        | Keys already capture customer, integration, scopes, origins, limits and expiry.                   | Retain as a generic future API foundation; do not use Symbol Set Codes as credentials.                  |
| Usage telemetry         | API events already capture application, version, route, latency, query and symbol reference.      | Add account mode, organization, project, set and symbol revision dimensions where applicable.          |
| Governed symbols        | Symbols have stable identifiers, owner, discipline/category and current revision.                 | Add organization ownership, visibility and organization-wide scope.                                    |
| Symbol revisions        | Revisions already move through draft, review, approved, published and deprecated states.          | Reuse this lifecycle; add organization approval and public-submission states where necessary.          |
| Audit events            | Generic entity/action audit records already exist.                                                | Emit events for all new governed entities, assignments and agent escalations.                           |
| Contextual search       | Search accepts application, discipline, drawing type, layer, units and preferred formats.         | Add personal/organization/project/set filters before contextual ranking.                               |
| Hermes agents           | Hermes orchestrates specialist agents; Alfi and Ed already support issue and user-support flows.  | Add logical Organization Steward and Platform Governance oversight capabilities with scoped access.    |

Existing implementation identifiers such as `CatalogFavourite` and `catalog_favourites.py` may retain their current spelling. All new product-facing UI copy shall use American English.

# 4. Terminology and conceptual model

## 4.1 Recommended terminology

| **Concept**                         | **Meaning**                                                                                                           |
|-------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| Personal account mode               | A signed-in user with no active organization context; uses Public Catalog and Free/Plus personal capabilities         |
| Commercial user                     | A user with one or more active organization memberships                                                              |
| Public Catalog                      | Published, reviewed symbols visible to the Symgov community                                                           |
| Organization                        | A commercial tenant containing users, projects, private symbols, Symbol Sets and contribution identity               |
| Active organization                 | The single organization selected during sign-in and embedded in the current commercial session                       |
| Symgov organization                 | Reserved system organization with code `symgov`, used to establish eligibility for Platform Administrator assignment |
| Platform Administrator              | Separate platform role assignable only to an eligible administrator of the Symgov organization                       |
| Project                             | An organization work context with name, code and optional description of up to 50 characters                          |
| Symbol Set                          | A reusable governed selection of public and organization-owned symbols                                               |
| Project Set Availability            | Makes a Symbol Set selectable while a user is working in one or more projects                                         |
| Organization-wide symbol            | An approved organization-owned symbol automatically available in every project and active set context                |
| Generated organization icon         | Temporary deterministic icon created when an organization has not uploaded its own icon                              |
| Set Release                         | A possible future immutable, version-pinned snapshot for repeatable regulated use                                     |
| Personal Favorites                  | User-managed convenience list; not governed and not shared as a project configuration                                 |
| Organization Steward                | Hermes-agent capability scoped to one organization for governance health, summaries and escalation                   |
| Platform Governance                 | Hermes-agent capability scoped to platform administration and cross-tenant security signals                          |

Recommended replacement for “symbol set key”: **Symbol Set Code**. A code such as `IDOX-NUCLEAR-PID` is human-readable and safe to expose because it is not an authentication secret. “Key” should be avoided because Symgov already has genuine Catalog API keys.

## 4.2 Conceptual hierarchy

| **Personal session** | **Free/Plus entitlement** | **Public Catalog and personal Favorites** |
|----------------------|---------------------------|-------------------------------------------|

| **Commercial session** | **Active Organization** | **Selected Project** | **One Active Symbol Set** | **Effective Symbol Palette** |
|------------------------|-------------------------|----------------------|---------------------------|------------------------------|

The effective Symbol Palette contains:

1. the public and permitted organization-owned symbols explicitly referenced by the active Symbol Set; and
2. all approved organization-owned symbols marked organization-wide.

The active Symbol Context is calculated at runtime. In personal mode it contains the authenticated user and subscription entitlement. In commercial mode it additionally contains the active organization, selected project, available sets, active set, permissions and resolved effective symbol list.

## 4.3 Cardinality and invariant rules

- A user may belong to zero, one or many organizations.
- A user with no active organization membership signs in to personal mode and continues under Free/Plus entitlement.
- A commercial user session belongs to exactly one active organization.
- Changing active organization requires sign-out and a new sign-in.
- An organization may contain many projects, Symbol Sets, members and organization-owned symbols.
- Every active organization user may select any active project in that organization; users are not assigned to projects.
- A project has an optional plain-text description of no more than 50 characters, including spaces.
- A project may make several Symbol Sets available and may nominate one default set.
- A user has at most one active Symbol Set in the current project context.
- A Symbol Set may be available in several projects and may contain many public and organization-owned symbols.
- A symbol may belong to many sets without duplication.
- An organization-owned symbol may be organization-wide or available only through sets that include it.
- A symbol has one stable governed identity whether it is organization-private or public.
- Every organization has an active icon: uploaded when supplied, otherwise generated automatically.
- The reserved Symgov organization must always exist and cannot be deleted through normal administration.
- At least one active Platform Administrator must exist.
- A Platform Administrator must also be an active Organization Administrator of the Symgov organization.
- A future Symbol Set release may pin revisions, but releases are not required for the first implementation.
- Agent recommendations never replace explicit human decisions required by the symbol or role governance workflows.

# 5. Personas and roles

| **Role/capability**          | **Scope**            | **Main responsibility**                                                                                                      |
|------------------------------|----------------------|------------------------------------------------------------------------------------------------------------------------------|
| Non-commercial User          | Personal account     | Browse/use the Public Catalog under Free or Plus, maintain Favorites and use other personal capabilities.                   |
| Commercial User              | Organization         | Choose projects, select/switch Symbol Sets, use symbols and maintain personal Favorites.                                    |
| Contributor                  | Organization         | Upload or edit organization-owned symbol drafts and respond to review comments.                                             |
| Organization Reviewer        | Organization         | Explicitly approve or reject organization-owned symbol submissions for organization use.                                    |
| Organization Administrator   | Organization         | Manage members, appoint/remove admins and reviewers, manage projects, Symbol Sets, branding and organization policy.        |
| Public Catalog Reviewer      | Global / discipline  | Review organization contributions for Public Catalog suitability under the existing Symgov review model.                   |
| Platform Administrator       | Global               | Manage organizations, platform policy, Public Catalog moderation, platform roles, demotion and exceptional access.          |
| Organization Steward agent   | One organization     | Monitor governance health, review coverage, backlogs, generated icon status and unresolved organization issues.             |
| Platform Governance agent    | Platform / Symgov org| Monitor platform-admin continuity, cross-tenant security signals and unresolved platform governance issues.                 |
| Integrator                   | Platform/API         | Support current or future API credentials and client configuration within authorized scopes.                                |

> **Organization role rules:** The first active user in a new commercial organization is automatically an Organization Administrator. An organization must always have at least one active administrator. Administrators may transfer or share administration, but the last active administrator cannot remove or downgrade themselves until another administrator exists. Organization Administrators are not automatically Organization Reviewers; they may appoint themselves if required.

> **Platform role rules:** The reserved Symgov organization is created through deployment/bootstrap data. A user must be an active administrator of that organization before a Platform Administrator can assign them the Platform Administrator role. At least one active Platform Administrator must remain. Only Platform Administrators may appoint or remove administrators of the Symgov organization or assign/remove the Platform Administrator role. The final eligible Platform Administrator cannot be suspended, downgraded or removed.

# 6. Product use cases

| **ID** | **Use case**                       | **Expected outcome**                                                                                                                                                 |
|--------|------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| UC-01  | Personal Free/Plus use             | A user with no organization memberships signs in directly to personal mode and continues to use the Public Catalog under the existing Free/Plus model.              |
| UC-02  | Multi-organization sign-in         | A user belonging to two companies signs in, selects one organization, and cannot switch to the other organization without signing out.                              |
| UC-03  | Choose organization project        | The user selects any active project in the signed-in organization without an administrator assigning it to them.                                                    |
| UC-04  | Distinguish similar projects       | Project choices show name plus an optional description of up to 50 characters, helping the user identify the correct project.                                       |
| UC-05  | Switch task context                | The same user switches from a P&ID set to a Construction Review set; only one set is active at a time.                                                               |
| UC-06  | Mixed public/private set           | An administrator builds a set from Public Catalog symbols and approved organization-owned symbols.                                                                  |
| UC-07  | Organization-wide symbol           | A company logo stamp is marked organization-wide and automatically appears in every organization project/set context.                                               |
| UC-08  | Cross-project reuse                | A private symbol is referenced by several sets made available to different projects; the symbol is stored only once.                                                |
| UC-09  | Generated organization icon        | A new organization without uploaded branding immediately receives a unique temporary icon; an admin later replaces it with the company icon.                        |
| UC-10  | Organization approval              | A contributor submits a private symbol; any one appointed Organization Reviewer explicitly approves it for company use.                                             |
| UC-11  | Public contribution                | The company submits the same approved symbol for public review. On publication, its stable symbol ID is unchanged and existing company sets continue to reference it.|
| UC-12  | Public demotion                    | A Platform Administrator demotes a contributed public symbol to organization-private and records the reason and impact.                                             |
| UC-13  | Company attribution                | The Public Catalog attributes the contribution to the organization, not to the individual employee.                                                                 |
| UC-14  | Platform-admin continuity          | The system prevents removal of the final Platform Administrator and allows an existing Platform Administrator to establish another eligible administrator safely.   |
| UC-15  | Agent oversight                    | Hermes identifies an organization with no active reviewer and an aging review queue, then raises an advisory issue without changing roles or approving content.      |
| UC-16  | Personal convenience               | A user adds frequently used symbols to Favorites; this does not alter the governed active set.                                                                       |
| UC-17  | Usage reporting                    | Organization administrators can see set adoption, searches, downloads, formats and contribution outcomes.                                                           |

# 7. Functional requirements

## 7.1 Accounts, memberships and session context

| **ID**     | **Requirement**                                                                                                                                                         |
|------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-ACC-001 | A User account may have zero, one or many active organization memberships.                                                                                              |
| FR-ACC-002 | A user with no active organization membership shall enter personal mode after email/PIN authentication.                                                                 |
| FR-ACC-003 | Personal mode shall continue to use the existing Free/Plus subscription and entitlement model.                                                                          |
| FR-ACC-004 | Organization membership and commercial entitlement shall be modeled separately from a user's personal Free/Plus subscription.                                           |
| FR-ORG-001 | The system shall create and manage organizations with unique name, code, status, locale, entitlement, icon and audit history.                                            |
| FR-ORG-002 | A user may belong to multiple organizations.                                                                                                                             |
| FR-ORG-003 | A user with more than one active organization membership shall select one organization during sign-in.                                                                  |
| FR-ORG-004 | A user with exactly one active organization membership may have that organization selected automatically.                                                               |
| FR-ORG-005 | A commercial session shall contain exactly one active organization ID and reject attempts to change it in-place.                                                       |
| FR-ORG-006 | To change organization, the user shall sign out and sign in again.                                                                                                       |
| FR-ORG-007 | The first active user in a newly created commercial organization shall automatically become an Organization Administrator.                                             |
| FR-ORG-008 | Every commercial organization shall always have at least one active Organization Administrator.                                                                           |
| FR-ORG-009 | The system shall prevent removal, suspension or downgrade of the last active Organization Administrator.                                                                 |
| FR-ORG-010 | Organization Administrators shall appoint and remove Organization Reviewers from active organization members.                                                            |
| FR-ORG-011 | Organization membership shall have status, roles, invitation/activation dates and audit history.                                                                            |
| FR-ORG-012 | Organization data shall be tenant-isolated in application queries, search, assets, agents and telemetry.                                                                    |
| FR-ORG-013 | When no icon is uploaded at organization creation, the system shall generate a unique temporary icon automatically.                                                        |
| FR-ORG-014 | The generated icon shall be deterministic from a non-secret unique seed, suitable for display at small sizes, and stored as the active fallback icon.                           |
| FR-ORG-015 | An Organization Administrator shall be able to upload a replacement icon; removing it shall restore the generated fallback.                                                |
| FR-ORG-016 | Uploaded icons shall be validated, sanitized, size-limited and converted to approved display variants.                                                                     |

## 7.2 Platform administration and the Symgov organization

| **ID**     | **Requirement**                                                                                                                                                         |
|------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-PLT-001 | Deployment shall create one reserved organization with the immutable normalized code `symgov`.                                                                          |
| FR-PLT-002 | The Symgov organization shall not be deletable through normal organization administration.                                                                               |
| FR-PLT-003 | A user must be an active Organization Administrator of the Symgov organization before being assigned the Platform Administrator role.                                  |
| FR-PLT-004 | Being an administrator of the Symgov organization shall establish eligibility but shall not automatically grant Platform Administrator authority.                     |
| FR-PLT-005 | At least one active Platform Administrator shall always exist.                                                                                                           |
| FR-PLT-006 | Only a Platform Administrator shall assign or remove administrators of the Symgov organization.                                                                            |
| FR-PLT-007 | Only a Platform Administrator shall assign or remove the Platform Administrator role.                                                                                     |
| FR-PLT-008 | The system shall prevent any action that would leave zero active Platform Administrators or make the remaining Platform Administrator ineligible.                           |
| FR-PLT-009 | Platform role and Symgov-organization membership changes shall use transactions, concurrency protection, re-authentication for sensitive actions and complete audit events.  |
| FR-PLT-010 | Bootstrap creation of the first Platform Administrator shall be an explicit deployment/migration operation rather than ordinary self-registration.                           |

## 7.3 Projects

| **ID**     | **Requirement**                                                                                                                                  |
|------------|--------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-PRJ-001 | An organization shall have projects with a Symgov ID, code, name, optional short description, status and optional external reference metadata.  |
| FR-PRJ-002 | The project description shall be plain text and no more than 50 characters including spaces.                                                     |
| FR-PRJ-003 | Project lists and selectors shall display the description when present.                                                                          |
| FR-PRJ-004 | Every active member of the signed-in organization shall be able to list and select every active project in that organization.                    |
| FR-PRJ-005 | The system shall not require per-user project assignment, project invitations or project-access administration.                                  |
| FR-PRJ-006 | Projects may be created manually or through a future trusted import; this does not affect user eligibility.                                          |
| FR-PRJ-007 | A project shall have zero or more available Symbol Sets and at most one default set.                                                                |
| FR-PRJ-008 | Closing a project shall prevent new selection while preserving usage and audit history.                                                              |

## 7.4 Symbol Sets

| **ID**     | **Requirement**                                                                                                                                      |
|------------|------------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-SET-001 | An Organization Administrator shall create a Symbol Set with name, unique Set Code, description, status, owner organization and optional metadata.  |
| FR-SET-002 | A set shall contain references to public symbols and organization-owned symbols; it shall never copy a symbol record.                               |
| FR-SET-003 | Adding the same symbol twice to one set shall be prevented.                                                                                          |
| FR-SET-004 | Set items may specify sort order, section/group, display label, notes and preferred format.                                                          |
| FR-SET-005 | A set may be copied as a new set while retaining reference lineage.                                                                                  |
| FR-SET-006 | A set may be made available to one or more projects in its owning organization.                                                                       |
| FR-SET-007 | The system shall not require user-level or team-level assignment of Symbol Sets in the first implementation.                                        |
| FR-SET-008 | The system shall distinguish draft, active, archived and superseded sets.                                                                              |
| FR-SET-009 | A user shall have one active set at a time in the selected project context.                                                                            |
| FR-SET-010 | A user shall be able to switch rapidly among active sets available to the selected project without re-authentication.                                 |
| FR-SET-011 | A project or organization default set may be configured for initial selection, but the user may change it.                                           |
| FR-SET-012 | The effective palette shall include active-set items plus all approved organization-wide symbols.                                                      |
| FR-SET-013 | The Public Catalog shall remain browseable independently of the active set, subject to product entitlement.                                        |
| FR-SET-014 | The data model shall permit future immutable set releases that pin exact symbol revisions; release UI is deferred.                                   |

## 7.5 Organization-owned symbols and organization review

| **ID**     | **Requirement**                                                                                                                                       |
|------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-SYM-001 | An authorized contributor shall create an organization-owned symbol draft through the governed submission workflow.                                  |
| FR-SYM-002 | The symbol shall identify its owning organization and visibility as organization-private or public.                                                    |
| FR-SYM-003 | An approved organization-private symbol may be marked organization-wide or be available through Symbol Sets that reference it.                         |
| FR-SYM-004 | Organization-wide symbols shall appear in every project/active-set context for the owning organization without explicit addition.                       |
| FR-SYM-005 | Drafts shall be visible only to the creator, Organization Administrators and Organization Reviewers until approved.                                    |
| FR-SYM-006 | Any one active Organization Reviewer may approve or reject a submitted organization symbol.                                                             |
| FR-SYM-007 | Approval shall require an explicit recorded decision; the system shall not infer approval from time, quorum or lack of response.                         |
| FR-SYM-008 | Organization approval shall be separate from Public Catalog review.                                                                                    |
| FR-SYM-009 | An approved organization-private symbol may be included in multiple sets and reused across projects.                                                  |
| FR-SYM-010 | Every asset shall retain provenance, creator, checksum, format and dimensions.                                                                            |
| FR-SYM-011 | Existing references shall retain symbol ID and revision when the symbol is updated, archived, made public or demoted to private.                      |

## 7.6 Public Catalog contribution and visibility transition

| **ID**     | **Requirement**                                                                                                                                       |
|------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-PUB-001 | An Organization Administrator or other authorized submitter shall submit an approved organization-private symbol revision for public review.         |
| FR-PUB-002 | Submission shall include proposed public metadata, reason for contribution and acknowledgment that it is shared freely with the Symgov community.    |
| FR-PUB-003 | Public review shall use the existing Symgov review model and shall not expose project names, set composition or unrelated organization data.          |
| FR-PUB-004 | Public review statuses shall include submitted, triage, in review, changes requested, accepted, rejected and withdrawn as appropriate.                 |
| FR-PUB-005 | Acceptance shall change visibility on the same stable symbol record; it shall not create a second public copy.                                           |
| FR-PUB-006 | Existing Symbol Set references shall continue to point to the same symbol after publication.                                                              |
| FR-PUB-007 | Public attribution shall display the contributing company/organization only, not an individual user.                                                  |
| FR-PUB-008 | A Platform Administrator may demote an eligible public contribution to organization-private with an explicit reason and audit trail. A symbol is ineligible while another organization references it from a Symbol Set. |
| FR-PUB-009 | Demotion shall be rejected while any Symbol Set owned by another organization references the public symbol. Removing all such external set items shall restore eligibility, subject to every other governance check. |

## 7.7 Runtime access, project selection and switching

| **ID**     | **Requirement**                                                                                                                                          |
|------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-CTX-001 | Personal mode shall not require or expose an active organization, project or Symbol Set.                                                                  |
| FR-CTX-002 | Commercial mode shall resolve the active organization from the immutable session context, not from an arbitrary client-supplied organization ID.       |
| FR-CTX-003 | The system shall list all active projects belonging to the active organization.                                                                            |
| FR-CTX-004 | Selecting a project shall list the active Symbol Sets made available to it.                                                                                 |
| FR-CTX-005 | The system shall maintain at most one active set for the current user/project context.                                                                        |
| FR-CTX-006 | The user shall be able to switch the active set easily without signing in again.                                                                              |
| FR-CTX-007 | The effective palette shall fail closed if organization, project or set context is invalid.                                                                   |
| FR-CTX-008 | Personal Favorites shall remain available according to symbol visibility and shall not change governed set membership.                                       |
| FR-CTX-009 | Initial operation shall be online only; no offline package shall be implied by browser caching.                                                               |

## 7.8 Hermes-agent oversight

| **ID**     | **Requirement**                                                                                                                                          |
|------------|----------------------------------------------------------------------------------------------------------------------------------------------------------|
| FR-AGT-001 | Hermes shall be able to orchestrate logical Organization Steward and Platform Governance agent capabilities.                                            |
| FR-AGT-002 | An Organization Steward shall be scoped to one organization and may inspect membership health, reviewer coverage, review backlog, generated icon status, project/set health and unresolved references. |
| FR-AGT-003 | A Platform Governance agent shall be scoped to the platform/Symgov organization and may inspect platform-admin continuity, duplicate organization indicators, cross-tenant authorization failures and unresolved governance exceptions. |
| FR-AGT-004 | Agents may create summaries, recommendations, notifications and issues through approved service interfaces.                                               |
| FR-AGT-005 | Agents shall not directly approve/reject symbols, grant/remove roles, alter organization membership, publish/demote symbols or change tenant policy.      |
| FR-AGT-006 | Every agent read and proposed action shall be tenant-scoped, permission-checked and auditable.                                                               |
| FR-AGT-007 | Agent recommendations that lead to a governed change shall require an authorized human to execute or explicitly confirm the change.                       |
| FR-AGT-008 | The design shall allow each agent to reference its own logical LLM model alias/configuration when the planned per-agent model capability is introduced.  |
| FR-AGT-009 | Permission and invariant checks shall remain deterministic application logic and shall never depend on an LLM response.                                   |
| FR-AGT-010 | Hermes may route agent findings to existing support/issue capabilities such as Ed or Alfi without exposing data outside the authorized scope.              |

# 8. Data model

The proposed tables extend the current SQLAlchemy/PostgreSQL model. UUID primary keys, timestamped records, constraints, indexes and generic AuditEvent usage should follow existing conventions.

| **Entity**                | **Indicative fields**                                                                                                      | **Purpose**                                                                                     |
|---------------------------|----------------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------|
| Organization              | id, code, name, status, default_locale, settings_json, entitlement, icon_asset_id, generated_icon_seed, icon_source, created_at, updated_at | Commercial tenant/customer or reserved system organization. |
| OrganizationMembership    | organization_id, user_id, status, joined_at, removed_at                                                                    | Many-to-many membership.                                                                         |
| OrganizationMemberRole    | organization_id, user_id, role, created_by, created_at                                                                     | Roles: organization_admin, organization_reviewer, contributor and user.                           |
| PlatformRoleAssignment    | user_id, role, assigned_by, assigned_at, revoked_at                                                                         | Separate protected platform roles, initially Platform Administrator.                              |
| Project                   | id, organization_id, code, name, short_description varchar(50), status, external_ref nullable, metadata_json               | Work context selectable by all active organization users.                                        |
| SymbolSet                 | id, organization_id, code, name, description, status, discipline, use_case, created_by, updated_at                         | Reusable governed collection.                                                                    |
| SymbolSetItem             | symbol_set_id, symbol_id, group_name, display_label, preferred_format, sort_order                                           | Reference to a public or permitted organization-owned symbol.                                    |
| SymbolSetProject          | symbol_set_id, project_id, is_default, priority, effective dates                                                            | Makes a set available to a project; no user assignment is required.                              |
| SymbolSetRelease (future) | id, symbol_set_id, release_label, status, changelog, released_by, released_at                                                | Optional future immutable release header.                                                        |
| SymbolSetReleaseItem      | release_id, symbol_id, revision_id, order/group/label snapshot                                                              | Optional future exact release membership and revisions.                                          |
| PublicationSubmission     | id, organization_id, symbol_id, revision_id, status, sharing_acknowledgment, proposed_metadata_json, reviewer fields        | Workflow that changes the same symbol from organization-private to public.                       |
| ContributionEvent         | id, organization_id, user_id nullable, submission_id, event_type, points, reason, occurred_at                               | Append-only reputation ledger; public attribution remains organization-only.                     |
| AgentConfiguration        | id, logical_agent_name, scope_type, scope_id nullable, enabled, model_alias nullable, allowed_capabilities_json, updated_at | Future-compatible Hermes agent binding and scope policy.                                         |
| AgentFinding              | id, agent_config_id, severity, finding_type, entity_type/id, summary, status, created_at, resolved_by, resolved_at          | Auditable advisory finding or issue; not a governed decision.                                    |

## 8.1 Changes to existing entities

| **Existing entity**  | **Recommended change**                                                                                                                                                                      |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| UserSession          | Add session_mode (`personal` or `organization`) and nullable active_organization_id. The organization value is immutable for the session.                                                    |
| GovernedSymbol       | Add owner_organization_id (nullable for legacy/platform records), visibility (`organization_private` or `public`), organization_wide flag and contribution metadata.                         |
| SymbolRevision       | Retain the existing revision lifecycle. Public/private is a symbol visibility property, not a second symbol/revision tree.                                                                   |
| CatalogApiKey        | Add organization_id only when generic commercial API authentication is introduced or retained; do not use Set Code as a credential.                                                        |
| CatalogApiUsageEvent | Add session_mode, organization_id, project_id, symbol_set_id, symbol_revision_id and event_type where available.                                                                            |
| CatalogFavourite     | No structural change required except tenant-aware visibility validation when an organization-private symbol is favorited.                                                                   |
| AuditEvent           | Include before/after summaries for session mode, roles, icons, approvals, visibility, set/project availability, demotion and agent finding resolution.                                      |

## 8.2 Derived palette query

The runtime set listing shall resolve symbols as the union of:

- active SymbolSetItem rows for the selected set; and
- approved GovernedSymbol rows where owner_organization_id equals the active organization and organization_wide is true.

The query shall deduplicate by stable symbol ID. A symbol explicitly present in the set may retain the set item's ordering, grouping and preferred-format metadata; an organization-wide symbol not explicitly present should appear in a consistent “Organization-wide” group or configured position.

## 8.3 Key database constraints

- Unique normalized organization code according to the naming policy; `symgov` is reserved and immutable.
- Organization name uniqueness policy shall avoid accidental duplicates while allowing legitimate same-name legal entities when disambiguated.
- A commercial organization must have at least one active `organization_admin`; enforce with transactional service logic and concurrency tests.
- At least one active eligible Platform Administrator must exist.
- A PlatformRoleAssignment for Platform Administrator requires an active `organization_admin` role in the Symgov organization.
- UserSession.active_organization_id is null for personal mode and must match an active membership for organization mode.
- Active organization cannot be changed on an existing session row/token.
- Project short_description has a database maximum length of 50 characters and matching application validation.
- Unique project code within organization and unique non-null external reference within organization when used.
- Unique Symbol Set Code within organization.
- Unique symbol per set.
- Unique set/project availability pair and only one default set per project.
- Organization-owned symbol and Symbol Set organizations must match unless the symbol is public.
- Public/private transition does not change the governed symbol primary key.
- Every organization has either an uploaded active icon or generated fallback icon metadata.
- A future released set and its release items are immutable.
- No cross-organization foreign-key combinations; enforce in service logic and, where practical, through composite constraints.

# 9. Permissions and access resolution

## 9.1 Permission matrix

Personal users can use the Public Catalog and personal capabilities according to their Free/Plus entitlement, but have no organization, project, set or private-symbol permissions.

| **Action**                               | **Platform admin** | **Org admin** | **Org reviewer** | **Contributor** | **Commercial user** |
|------------------------------------------|--------------------|---------------|------------------|-----------------|---------------------|
| Use Public Catalog                       | Yes                | Yes           | Yes              | Yes             | Yes                 |
| Select any active project in active org  | N/A                | Yes           | Yes              | Yes             | Yes                 |
| View approved org-wide symbols           | Exceptional        | Yes           | Yes              | Yes             | Yes                 |
| View submitted/draft private symbols     | Exceptional        | Yes           | Yes              | Own drafts      | No                  |
| Create private draft                     | No                 | Yes           | Optional         | Yes             | No                  |
| Approve for organization use             | No                 | Only if reviewer | Yes           | No              | No                  |
| Create/edit Symbol Set                   | No                 | Yes           | No               | No              | No                  |
| Make sets available to projects          | No                 | Yes           | No               | No              | No                  |
| Upload/replace organization icon         | No                 | Yes           | No               | No              | No                  |
| Appoint org admins/reviewers             | Symgov org only or exceptional | Yes except Symgov org | No | No | No |
| Assign Platform Administrator            | Yes                | No            | No               | No              | No                  |
| Submit approved symbol for public review | No                 | Yes           | Optional by policy | No            | No                  |
| Decide Public Catalog review             | Reviewer/admin policy | No         | No               | No              | No                  |
| Demote public contribution to private    | Yes                | No            | No               | No              | No                  |
| Resolve agent finding                    | Scope-dependent    | Organization findings | Advisory | Advisory | No |

## 9.2 Sign-in and account context

1. Authenticate using the current email/PIN model.
2. Load active organization memberships.
3. If none exist, issue a personal-mode session governed by the user's Free/Plus entitlement.
4. If one exists, select it automatically unless a future policy requires confirmation.
5. If several exist, require the user to choose one organization before issuing the full commercial session.
6. Bind the selected active organization to the session/token.
7. Do not expose an in-session organization switch operation. The user must sign out to enter another organization.
8. Platform Administrator capability is loaded only when the platform-role assignment and Symgov-organization eligibility are both valid.

## 9.3 Project and Symbol Set resolution

1. Personal mode does not resolve projects or Symbol Sets.
2. In organization mode, list all active projects belonging to the session organization.
3. Accept the selected project only when it belongs to the session organization and is active.
4. List all active Symbol Sets made available to that project.
5. Use an explicitly supplied Set Code only when it appears in that list.
6. Otherwise use the user's last active set for that project, if still available.
7. Otherwise use the project default set, then the organization default set if configured and valid.
8. Resolve the effective palette as active-set items plus approved organization-wide symbols.
9. Permit separate browsing of the Public Catalog according to entitlement.

> **Fail closed for private content:** A session, project or set reference from another organization must never be accepted. An unresolved organization context may expose only personal/Public Catalog capabilities. Organization-private and organization-wide symbols require a valid organization session and selected project context.

## 9.4 Agent access resolution

- Organization Steward access is resolved from its configured organization scope and cannot be widened by prompt content.
- Platform Governance access is resolved from a platform-scoped service identity and approved capability list.
- Agents call the same application services as human users or purpose-built read-only reporting services; they do not query unrestricted tenant tables directly.
- A finding may recommend an action, but the subsequent governed action is re-authorized against the human actor who confirms it.
- Prompt injection, model output or agent memory cannot change tenant scope, role eligibility or last-administrator invariants.

# 10. Symbol, set and administration lifecycles

## 10.1 Organization-owned symbol lifecycle

| **Draft** | **Submitted for organization review** | **Approved organization-private** | **Submitted for public review** | **Public** | **Demoted to organization-private** |
|-----------|---------------------------------------|-----------------------------------|---------------------------------|------------|-------------------------------------|

Any one appointed Organization Reviewer can explicitly approve or reject the organization submission. Approval means “safe and suitable for this organization to use”; it does not automatically make the symbol public.

Public review operates on the same symbol and revision. Acceptance changes the symbol's visibility to public while retaining the same governed symbol ID, revisions and Symbol Set references. A Platform Administrator may demote it back to organization-private status, recording the reason, only when no Symbol Set owned by another organization currently references it and the remaining eligibility rules pass.

## 10.2 Symbol Set lifecycle

| **Draft** | **Active rolling set** | **Superseded** | **Archived** |
|-----------|------------------------|----------------|--------------|

The first implementation uses active rolling sets. Administrators control set membership, and users see one active set at a time. The database and service boundaries should permit a later extension to immutable, version-pinned releases without changing stable set or symbol IDs.

## 10.3 Visibility transition and reference impact

- Publication and demotion change visibility on the same GovernedSymbol record.
- Existing sets in the owning organization continue to reference the symbol without migration or replacement.
- A current reference from a Symbol Set owned by another organization prevents demotion of that governed-symbol identity to organization-private visibility.
- Favorites, project selection or use, previews, searches, views, downloads and API reads do not affect demotion eligibility.
- Removing every cross-organization Symbol Set item restores demotion eligibility, subject to all other governance checks.
- Symbol Set item creation/removal and demotion use the same database serialization boundary for the governed-symbol identity, so no concurrent change can produce a private symbol referenced by another organization's set.
- Previously downloaded/exported files are not retroactively modified, but future downloads must respect current visibility.
- Publication and demotion preserve an audit trail including contributing organization and platform decision reason.
- Public Catalog attribution displays the company name only.

## 10.4 Organization and platform-administrator lifecycle

- A commercial organization begins with one automatically assigned Organization Administrator.
- Additional organization admins may be assigned before the original administrator is removed or downgraded.
- A generated organization icon is active from creation until an uploaded icon replaces it; the generated icon remains the fallback.
- The Symgov organization is bootstrapped and cannot be deleted or converted into a normal commercial organization.
- A user becomes eligible for Platform Administrator only after becoming an active admin of the Symgov organization.
- Platform Administrator assignment is a separate explicit step and is fully audited.
- Removal of Symgov-organization admin eligibility shall first require removal of Platform Administrator authority unless another rule-preserving transaction performs both safely.
- No transaction may leave zero eligible active Platform Administrators.

# 11. User experience

## 11.1 Sign-in and account mode

- Email/PIN remains the initial authentication model.
- A user with no organizations enters personal mode directly and sees the existing Free/Plus experience.
- A user with one organization may enter it automatically.
- A user with multiple organizations sees a clear organization-selection screen showing icon, name and code.
- The screen explains that changing organization requires sign-out.
- The active organization is always visible in commercial-mode navigation.
- Platform Administrator status is visually distinct from organization roles and is never inferred merely from being in the Symgov organization.

## 11.2 Organization administration

- Organization profile shows name, code, status, icon, locale and entitlement summary.
- A generated temporary icon appears immediately when no icon is uploaded and is visibly marked as generated to administrators.
- Organization Administrators can upload, crop/preview, replace or remove a custom icon; removal restores the generated fallback.
- Member management shows active roles, invitation/status and clear last-admin safeguards.
- The Symgov organization uses a protected administration view available only to Platform Administrators for admin-role changes.
- Agent findings appear as advisory items with severity, evidence summary, recommended action and resolution status.

## 11.3 Project administration and selection

- Project create/edit forms include code, name, optional short description and status.
- The description field shows a live 50-character counter and rejects longer values before submission and at the API.
- Project selectors show icon/context, project name and description when present.
- All active organization users can select all active projects; there is no project-member assignment UI.
- Closed/archived projects remain visible in administration and reports but cannot be selected for new work.

## 11.4 Symbol Set builder

- Create/edit fields: name, Set Code, description, status, disciplines/use cases and optional default behavior.
- Search both Public Catalog and approved organization-owned symbols.
- Drag/drop or keyboard-friendly ordering, batch add/remove, search/filter, duplicate prevention and counts by discipline/category/format.
- Optional sections such as “Process”, “Electrical”, “Safety” and “Construction Review”.
- Organization-wide symbols are shown as inherited/automatic and do not need to be added to the set.
- Warnings for deprecated, unavailable, unapproved or demoted symbols.
- Make the set available to one or more projects from the same workflow.
- Immutable release controls are not shown in the first implementation but may be added later.

## 11.5 Catalog experience

- Personal mode shows subscription status, Public Catalog search, Favorites and profile capabilities.
- Commercial mode shows a persistent context banner: Organization / Project / Active Symbol Set.
- Project selector lists every active project in the signed-in organization with its short description.
- One active-set switcher lists the sets available to the selected project.
- Search scope control uses American-English labels such as **Active Palette** and **Public Catalog**.
- Clear badges for Public, Organization Private, Organization-wide, Draft, Deprecated and Demoted.
- Favorites and recently used symbols are personal shortcuts and do not alter the active set.
- Symbol detail shows stable ID, revision, formats, standards links, company attribution where public and relevant usage information.

## 11.6 UI language standard

The source UI locale shall use American English. New UI copy, labels, errors, help text and API-facing display names use **Catalog**, **Organization**, **Favorite**, **Authorized**, **Behavior** and **Localization**. Existing internal class/file identifiers may retain historical spelling until separately refactored.

# 12. Contribution and gamification

Gamification should encourage organizations to contribute useful, publishable content without weakening governance. The launch model uses reputation, badges and progress measures. Raw upload counts should not earn points, and no financial or subscription benefit is attached initially.

## 12.1 Recommended scoring model

| **Event**                                       | **Illustrative points** | **Guardrail**                                                                                   |
|-------------------------------------------------|-------------------------|-------------------------------------------------------------------------------------------------|
| Public submission accepted                      | +100                    | Award only when the public reviewer accepts the contribution.                                   |
| Accepted significant revision                   | +40                     | Material improvement to an existing public symbol.                                              |
| Accepted format/accessibility improvement       | +20                     | For example a clean SVG or corrected metadata/alt description.                                  |
| Review response completed promptly              | +5                      | Capped per submission; no reward for unnecessary review cycles.                                 |
| Cross-organization adoption milestone           | +25                     | Triggered by meaningful use across independent organizations; configurable and fraud-resistant. |
| Submission withdrawn as duplicate before review | 0                       | No penalty when caught during contributor self-check.                                           |
| Rejected/spam/invalid contribution               | 0 or reversal           | Reverse awards for invalidated content; avoid public negative shaming.                           |

## 12.2 Recognition

- Organization badges: First Contribution, Contributor Organization, Multi-Discipline Contributor, Metadata Improver and Community Partner.

- Organization dashboard statistics: accepted contributions, acceptance rate, symbols currently public, downstream use and review turnaround.

- Individual users may see private contribution/activity statistics in their profile, but public catalog attribution shows the company only.

- Opt-in leaderboards only; default to achievement/progress views rather than competition.

- Demotion or invalidation may reverse contribution events through append-only correction records.

## 12.3 Future real benefits

Real benefits are intentionally deferred. A later product decision may connect trusted contribution reputation to benefits such as increased private capacity, API quota, trial extensions or other commercial recognition. The specific benefits, thresholds and caps are not part of this specification version.

## 12.4 Anti-gaming controls

- No points for uploads, likes, self-downloads or activity within the submitting organization alone.

- Deduplicate submissions before review using metadata, visual similarity and standards links.

- Reviewer decisions remain independent and do not display point values during review.

- Contribution events are append-only; corrections use reversal entries.

- Rate-limit repeated submissions and flag suspicious linked accounts or organizations.

- Popularity never overrides technical approval or safety/standards status.

# 13. Analytics and telemetry

## 13.1 Required measures

- Personal versus organization session counts and active users.
- Organization, project and Symbol Set adoption.
- Search volume, zero-result searches and search-to-download conversion.
- Symbol preview and download counts, including downloaded formats.
- Organization-wide symbol use and set membership.
- Review queue size, age and turnaround for organization and public workflows.
- Public contribution acceptance, demotion and downstream adoption.
- Generated versus uploaded organization icon status.
- Organization-admin, reviewer and Platform Administrator coverage.
- Agent findings by type, severity, age and resolution outcome.

## 13.2 Event semantics

Events should distinguish preview, asset download, Favorite changes, project selection, set selection, organization approval, public-submission activity, public demotion, icon generation/replacement, role changes and agent findings. Downloads remain the primary generic product-use measure until product-specific placement integrations are designed separately.

## 13.3 Privacy and attribution

- Use internal IDs or pseudonymous user identifiers in analytics.
- Public contribution attribution displays the organization only.
- Do not place private symbol images, sensitive project details or full agent prompts in general telemetry.
- Project short descriptions are customer content and should not be copied into broad platform analytics unless specifically required.
- Support retention policies and aggregate reporting thresholds.
- Separate operational logs, product analytics, security signals and contribution reputation.
- Agent telemetry records configuration, scope, action type and outcome without exposing hidden model reasoning.

# 14. Security, compliance and non-functional requirements

## 14.1 Tenant, session and authorization security

- Every organization-scoped query derives organization context from the authenticated session or trusted service identity.
- Personal sessions cannot supply an organization ID to gain commercial access.
- Organization switching requires session revocation and new authentication flow.
- Cross-tenant object references return a safe authorization error without disclosing whether the object exists.
- Last-organization-admin and last-Platform-Administrator protections are transactional and concurrency-tested.
- Platform Administrator assignment requires current Symgov-organization admin eligibility and step-up re-authentication.
- The reserved Symgov organization code and protected status cannot be changed through normal APIs.
- Agent service identities use least privilege, explicit scope and the same tenant filters as interactive services.
- LLM output is treated as untrusted input and cannot directly authorize any action.

## 14.2 Asset and icon safety

- Sanitize SVG and reject scripts, external references and unsafe embedded content.
- Validate MIME type, extension, file signature, dimensions and size; malware-scan uploads.
- Store checksum and provenance for every source and derived asset.
- Generate temporary organization icons from a deterministic non-secret seed without calling an external image service or LLM.
- Generated icons must not encode email addresses or other personal information.
- Uploaded organization icons are displayed only after processing to approved variants.
- Public submission records the organization's acknowledgment that the symbol is shared freely with the Symgov community.
- Do not expose private source documents merely because a derived symbol is submitted publicly.

## 14.3 Performance and availability targets (provisional)

| **Measure**                         | **Provisional target**                                                                 |
|-------------------------------------|----------------------------------------------------------------------------------------|
| Personal/Public Catalog search      | P95 under 1.5 seconds for normal filters and cached metadata                            |
| Organization context resolution     | P95 under 500 ms excluding authentication                                              |
| Project/set switch                  | P95 under 750 ms for context and palette metadata                                      |
| Effective palette query             | P95 under 1 second for representative set sizes                                        |
| Organization icon generation        | Complete synchronously or within the organization-creation transaction response         |
| Authorization failure               | Fail closed; never fall back to private content                                        |
| Agent advisory processing           | Asynchronous-compatible, but never blocks core sign-in, Catalog or approval workflows  |

These targets are initial engineering goals rather than contractual SLAs and should be validated against realistic workloads.

## 14.4 Accessibility and localization

- All organization/project/set administration must be keyboard-accessible and screen-reader labeled.
- Organization icons require accessible text based on organization name; decorative variants must be marked appropriately.
- The sign-in organization selection screen must clearly identify the session effect.
- Character counters and validation messages for project descriptions must be accessible.
- Symbol metadata should support translated names/descriptions while retaining one stable ID.
- Organization/set/project names and descriptions are customer content and are not automatically translated.
- The feature shall support Symgov target languages and locale-aware dates/numbers.
- American English is the canonical source locale for UI terminology; translations should preserve product meaning rather than British spelling variants.

# 15. Migration and delivery phases

## 15.1 Migration approach

- Preserve all existing users and Free/Plus subscriptions; users without organization memberships remain in personal mode.
- Treat currently published Catalog symbols as public symbols with no owning commercial organization unless provenance is known.
- Keep current personal Favorites and validate visibility if organization-private symbols are later favorited.
- Create the reserved Symgov organization through a controlled migration/seed step.
- Create the initial Platform Administrator through deployment configuration and verify the role/eligibility invariant.
- Create an Organization record for each commercial customer when onboarding begins.
- The first user activated in each new commercial organization becomes its Organization Administrator.
- Generate a temporary icon for every organization lacking an uploaded icon.
- Extend sessions with session_mode and nullable active organization after existing email/PIN authentication.
- Do not retrofit projects/sets into historical usage events unless a reliable mapping exists.
- Introduce feature flags so the existing personal Public Catalog behavior remains unchanged until organization features are enabled.

## 15.2 Recommended phases

| **Phase** | **Scope**                                                                                                                                                                      |
|-----------|--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| 1         | Account/session modes, Organization entities, memberships, role invariants, reserved Symgov organization, Platform Administrator bootstrap and generated icons.               |
| 2         | Projects with 50-character descriptions, Symbol Sets, project availability, active-set switching and organization-wide palette overlay.                                      |
| 3         | Organization symbol submission/review, stable public/private visibility transition and demotion handling.                                                                     |
| 4         | Organization administration UI, contribution reporting, generated/custom icon management and expanded acceptance tests.                                                       |
| 5         | Hermes Organization Steward and Platform Governance findings, routing to Ed/Alfi, audit dashboards and per-agent model-alias integration seam.                                |
| 6         | Future immutable set releases, offline investigation, mature API/SSO options and real contribution benefits.                                                                   |

## 15.3 Implementation guidance for Codex

- Create migrations and models first, with cross-tenant, personal-mode, active-session-organization and last-admin concurrency tests before routes/UI.
- Seed the Symgov organization and first Platform Administrator idempotently; prevent normal application code from recreating or deleting it.
- Add an AccountContext object with `session_mode` and optional OrganizationContext; do not accept arbitrary organization IDs for protected operations.
- Make organization selection part of session creation and omit an organization-switch endpoint.
- Preserve existing Free/Plus behavior for users with no active organization membership.
- Generate organization icons locally using a deterministic identicon algorithm and store both fallback metadata and uploaded active asset references.
- Enforce project description length in database, schema validation and UI.
- Model Symbol Set availability through project links only for v1; do not create user/team assignment complexity.
- Implement the effective-palette query as active set items union approved organization-wide symbols, deduplicated by stable symbol ID.
- Extend the existing governed symbol and publication workflow so public/private is a visibility transition on one symbol record.
- Extend existing Catalog search/result mapping rather than creating a second independent public Catalog API.
- Use the existing AuditEvent pattern for all mutations and agent finding state changes.
- Introduce agent service interfaces after deterministic governance services are complete; agents must never write directly to role, approval or visibility tables.
- Store a logical `model_alias` in AgentConfiguration but keep selection optional until per-agent LLM configuration is implemented.
- Add contract tests for personal sign-in, organization selection, project/set resolution, icon fallback, platform-admin eligibility and agent scope.
- Implement feature flags and seed/demo data for personal users, two commercial organizations, the Symgov organization and several contrasting projects.

# 16. Acceptance criteria

| **ID** | **Acceptance criterion**                                                                                                                                                                      |
|--------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| AC-01  | A user with no organization memberships signs in to personal mode and retains the existing Free/Plus Public Catalog experience.                                                             |
| AC-02  | A user with two organizations must select one during sign-in and cannot change it without signing out.                                                                                       |
| AC-03  | A personal-mode session cannot access organization projects, sets or private symbols by supplying IDs manually.                                                                              |
| AC-04  | The first active user in a new commercial organization becomes an Organization Administrator.                                                                                                |
| AC-05  | The system refuses to remove, suspend or downgrade the last active Organization Administrator.                                                                                              |
| AC-06  | The reserved Symgov organization exists with code `symgov` and cannot be deleted or renamed through normal administration.                                                                  |
| AC-07  | A user cannot be assigned Platform Administrator unless they are an active admin of the Symgov organization.                                                                                 |
| AC-08  | The system refuses any operation that would leave zero eligible active Platform Administrators.                                                                                             |
| AC-09  | A new organization created without an icon receives a unique generated icon immediately; uploading/removing a custom icon switches between custom and fallback correctly.                    |
| AC-10  | Every active organization user can list and choose every active project in that organization without individual assignment.                                                                 |
| AC-11  | A project description accepts 0–50 characters, appears in the selector and is rejected at 51 characters in UI and API validation.                                                           |
| AC-12  | An Organization Administrator creates a set containing both a public symbol and an approved organization-private symbol without duplicating either record.                                  |
| AC-13  | Only one set is active at a time, and the user can switch among sets available to the selected project.                                                                                      |
| AC-14  | An approved organization-wide symbol appears in every project and active-set context in its owning organization without explicit set membership.                                            |
| AC-15  | Any one appointed Organization Reviewer can explicitly approve a submitted symbol; no elapsed time or missing response becomes approval.                                                    |
| AC-16  | Publishing an organization-private symbol changes visibility on the same governed symbol ID and existing owner-organization sets continue to reference it.                                  |
| AC-17  | The Public Catalog attributes a published contribution to the organization only.                                                                                                            |
| AC-18  | A Platform Administrator can demote an otherwise eligible public contribution with reason and audit history only when no Symbol Set owned by another organization currently references it; removing all such references restores eligibility. |
| AC-19  | Supplying a project or Set Code from another organization returns no private data and a safe authorization error.                                                                            |
| AC-20  | Organization and platform agents can create scoped findings and summaries but cannot directly change roles, approve symbols, publish or demote content.                                    |
| AC-21  | An agent prompt attempting to override tenant scope or role rules does not change resolved permissions.                                                                                      |
| AC-22  | Usage reports distinguish personal and organization sessions and aggregate downloads by organization, project, active set and format where applicable.                                    |
| AC-23  | All new UI labels use American English, including Catalog, Organization and Favorite.                                                                                                        |
| AC-24  | Initial organization Symbol Set functionality requires online access and does not advertise or create an offline package.                                                                    |

# 17. Decisions and questions for iteration

## 17.1 Resolved product decisions

| **ID** | **Decision**                                                                                                                                                                      |
|--------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| D1     | Non-commercial users may have no organization and continue under the existing Free/Plus model.                                                                                  |
| D2     | Commercial users may belong to one or more organizations; one is selected at sign-in and locked for the session.                                                               |
| D3     | All active organization users may choose all active projects in that organization; no per-user assignment is required.                                                         |
| D4     | Each project has an optional short description with a maximum of 50 characters.                                                                                                 |
| D5     | One Symbol Set is active at a time, with easy switching among sets available to the selected project.                                                                           |
| D6     | Approved organization-owned symbols may be marked organization-wide and are automatically included in every organization project/set context.                                  |
| D7     | The first commercial organization user becomes an admin; every commercial organization must retain at least one admin.                                                         |
| D8     | Organization admins appoint Organization Reviewers; any one reviewer may explicitly approve a submitted organization symbol.                                                   |
| D9     | Private and public use the same symbol record; publication changes visibility rather than creating a copy.                                                                       |
| D10    | Public contributions are shared freely with the Symgov community and attributed publicly to the company only.                                                                   |
| D11    | A Platform Administrator may demote a public contribution back to organization-private only while no Symbol Set owned by another organization references it and every other eligibility rule passes. |
| D12    | A reserved Symgov organization establishes eligibility for Platform Administrator assignment, and at least one Platform Administrator must always exist.                       |
| D13    | A new organization receives a unique generated temporary icon when no custom icon is supplied.                                                                                  |
| D14    | Product-specific integration content is removed from this generic product specification.                                                                  |
| D15    | Hermes-agent oversight is included for governance monitoring and escalation, but agents do not autonomously make governed decisions.                                           |
| D16    | The current email/PIN model remains for now.                                                                                                                                     |
| D17    | Initial operation is online only; offline Symbol Sets may be considered later.                                                                                                   |
| D18    | Initial gamification uses reputation/badges; real benefits are a future decision.                                                                                                |
| D19    | Product-facing UI uses American English, including Catalog, Organization and Favorite.                                                                                                |

## 17.2 Remaining future decisions

| **ID** | **Question**                                                                                                      | **Current recommendation**                                                                                  |
|--------|-------------------------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------------------------|
| O1     | What real commercial benefits, if any, should later be attached to trusted contribution reputation?              | Defer until contribution quality, economics and anti-gaming controls are validated.                        |
| O2     | Should future offline support package one active set, multiple sets, or a project snapshot?                       | Investigate only after the online model and release/versioning approach are stable.                         |
| O3     | When should SSO or service credentials supplement the email/PIN model?                                            | Treat as a separate authentication initiative after generic organization functionality is established.    |
| O4     | What final names should be used for the Organization Steward and Platform Governance agent capabilities?          | Keep these as logical capability names; map them to existing or new Hermes agents during implementation.   |
| O5     | Which logical LLM model alias should each agent use after per-agent model configuration is available?             | Configure by environment and task; do not hard-code a provider or model in this specification.             |
| O6     | Should immutable Symbol Set releases be introduced for regulated customers, and at what stage?                   | Keep the data model future-capable and add release UI only when a concrete customer need is confirmed.     |

## 17.3 Additional decision support (accepted post v0.3)

The following sections record decisions and semantic clarifications accepted after the v0.3 draft was published. They form part of the implementation contract for the Symbol Set Management capability.

### Project semantics

A **Project** represents a real piece of work, contract, programme, asset-development activity, or comparable customer undertaking.

Examples include an airport extension, nuclear power station construction programme, rail upgrade contract, or other defined piece of work undertaken by the organization.

Projects are not intended merely as generic discipline groupings or arbitrary user workspaces.

An Organization Admin creates, updates and closes Projects. Organization Users may select every active Project in their organization, including a Project with no available Symbol Sets, but do not administer Project definitions. After Project selection, only the active Symbol Sets made available to that Project are offered; having no available set is a valid context.

### Public-to-private symbol eligibility

An organization-owned private symbol may be promoted to the Public Catalog through the defined public governance and review process while retaining its stable governed-symbol identity.

After publication, the originating organization may request that the symbol be returned to organization-private visibility **only while no Symbol Set owned by another organization references it**.

A public symbol is eligible to become private only when all of the following are true:

* the requesting organization is the organization that originally created and owns the symbol;
* no Symbol Set owned by another organization currently references the symbol; and
* the check is performed under the shared race-safe serialization boundary immediately before the transition.

While another organization includes a public symbol in one or more of its Symbol Sets, the symbol **cannot be made private**.

This restriction reflects current governed set membership rather than historical use. Removing the symbol from every Symbol Set owned by other organizations restores eligibility for private visibility, subject to the remaining governance checks. Favorites, project selection or use, previews, searches, views, downloads and API reads do not create this restriction.

The public-to-private workflow must therefore perform a deterministic eligibility check before allowing the governance action. While a current cross-organization Symbol Set reference exists, the action is unavailable and the UI must explain why, for example:

> **This symbol cannot be made private.** Another organization has added it to a Symbol Set, so it must remain available in the Public Catalog.

The system must not resolve this situation by removing another organization's Symbol Set membership, breaking an existing reference, cloning the symbol, or creating a new private identity.

### Governance principle

**Private → Public:** permitted subject to the defined public review and governance process.

**Public → Private:** permitted only for an organization-owned symbol that is not currently referenced by any Symbol Set owned by another organization.

**Current membership in another organization's Symbol Set requires continued public availability; removing all such memberships restores demotion eligibility.**

### Implementation impact

**Stage 4 — Projects and Symbol Sets**

Treat Projects as real customer work/contract/programme contexts. Retain the existing many-to-many relationship whereby a Symbol Set can be made available to multiple Projects.

**Stage 7 — Publication/demotion**

Replace generic "demotion blast-radius handling" with an explicit public-to-private eligibility service/check.

Before offering or executing public-to-private transition, determine whether any current Symbol Set item owned by another organization references the stable governed-symbol identity.

If any current cross-organization Symbol Set reference exists, reject the transition.

Symbol Set item creation/removal and public-to-private transitions must lock the same governed-symbol serialization boundary before checking or changing state. The demotion transaction must re-check current cross-organization set membership under that lock. Add/remove and demotion actions remain actor-attributed and audited, but historical membership does not itself block a later transition after every external set item has been removed.

Tests must include:

* originating organization publishes and subsequently makes an unused symbol private — allowed;
* another organization adds the public symbol to a Symbol Set — private transition rejected;
* another organization adds the public symbol to Favorites, selects or uses it in a Project, previews, searches, views, downloads or reads it through an API without adding it to a Symbol Set — this alone does not affect private-transition eligibility;
* the symbol is removed from every Symbol Set owned by other organizations — private transition becomes eligible again, subject to the other checks;
* organization other than the originating owner requests private transition — rejected;
* concurrent cross-organization Symbol Set addition and private-transition request — cannot result in a private symbol referenced by another organization.

# Appendix A. Draft API surface

| **Method**       | **Route**                                                        | **Purpose**                                                                                         |
|------------------|------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------|
| POST             | /api/v1/auth/session                                             | Authenticate email/PIN; return personal session, organization choices or organization-scoped session. |
| POST             | /api/v1/auth/session/select-organization                         | Complete sign-in by selecting one organization before the full commercial session is issued.       |
| POST             | /api/v1/auth/logout                                              | Revoke the current session; required before selecting another organization.                         |
| GET/POST         | /api/v1/organizations                                            | List/create organizations under authorized scope.                                                   |
| GET/PATCH        | /api/v1/organizations/{orgId}                                    | Organization details/settings/icon metadata.                                                        |
| POST/DELETE      | /api/v1/organizations/{orgId}/icon                               | Upload/remove a custom icon; removal restores generated fallback.                                   |
| GET/POST         | /api/v1/organizations/{orgId}/members                            | Membership management.                                                                              |
| PUT/DELETE       | /api/v1/organizations/{orgId}/members/{userId}/roles/{role}      | Assign/remove organization roles with last-admin protection.                                        |
| GET/PUT/DELETE   | /api/v1/platform-admins/{userId}                                 | Platform Administrator lifecycle with Symgov-org eligibility and last-admin protection.             |
| GET/POST         | /api/v1/organizations/{orgId}/projects                           | Project list/create; all active members can list/select active projects.                             |
| GET/PATCH        | /api/v1/projects/{projectId}                                     | Project metadata including 50-character short description.                                          |
| GET/POST         | /api/v1/organizations/{orgId}/symbol-sets                        | Set list/create.                                                                                     |
| GET/PATCH/DELETE | /api/v1/symbol-sets/{setId}                                      | Set lifecycle and metadata.                                                                          |
| GET/PUT          | /api/v1/symbol-sets/{setId}/items                                | List/replace or batch-edit set contents.                                                             |
| GET/PUT          | /api/v1/symbol-sets/{setId}/projects                             | Make the set available to projects and nominate defaults.                                           |
| GET              | /api/v1/catalog/context?projectId=…&setCode=…                     | Resolve organization-scoped project/set context.                                                    |
| GET              | /api/v1/catalog/symbols?projectId=…&setCode=…                     | Return effective palette: active-set items plus organization-wide symbols.                           |
| POST             | /api/v1/catalog/usage                                            | Report preview, download, Favorite and switching events.                                            |
| POST             | /api/v1/organization-symbols                                     | Create organization-private symbol draft.                                                           |
| POST             | /api/v1/organization-symbols/{symbolId}/submit-review            | Submit for organization review.                                                                     |
| POST             | /api/v1/organization-symbols/{symbolId}/review-decisions         | Record explicit approval/rejection by an Organization Reviewer.                                     |
| POST             | /api/v1/symbols/{symbolId}/publication-submissions               | Submit the same approved symbol for public review.                                                   |
| GET/PATCH        | /api/v1/publication-submissions/{id}                             | Public review workflow.                                                                              |
| POST             | /api/v1/symbols/{symbolId}/demote                                | Platform-admin demotion from public to organization-private.                                        |
| GET              | /api/v1/organizations/{orgId}/contributions                      | Organization contribution reputation and badges.                                                    |
| GET              | /api/v1/organizations/{orgId}/agent-findings                     | Scoped Organization Steward findings.                                                               |
| GET/PATCH        | /api/v1/agent-findings/{findingId}                               | View/resolve an advisory finding; governed actions use separate authorized endpoints.                |

## Appendix A.1 Example organization-scoped context response

<table>
<colgroup><col style="width: 100%" /></colgroup>
<thead><tr class="header"><th>{<br />
"sessionMode": "organization",<br />
"organization": {"id": "&lt;uuid&gt;", "code": "IDOX", "iconSource": "generated"},<br />
"project": {"id": "&lt;uuid&gt;", "code": "AIRPORT-02", "name": "Airport Construction", "shortDescription": "Terminal 2 expansion"},<br />
"availableSets": [<br />
{"id": "&lt;uuid&gt;", "code": "AIRPORT-ELEC", "name": "Airport Electrical"},<br />
{"id": "&lt;uuid&gt;", "code": "CONSTRUCTION-REVIEW", "name": "Construction Review Stamps"}<br />
],<br />
"activeSet": {"code": "AIRPORT-ELEC", "reason": "project_default"},<br />
"effectivePalette": {"setItemCount": 164, "organizationWideCount": 7, "deduplicatedCount": 169},<br />
"searchScopes": ["active_palette", "public_catalog"],<br />
"onlineRequired": true<br />
}</th></tr></thead><tbody></tbody>
</table>

# Appendix B. Initial Event Catalog

| **Event**                                      | **Meaning**                                                                                 |
|------------------------------------------------|---------------------------------------------------------------------------------------------|
| personal_session_started                       | User signs in without an active organization context.                                       |
| organization_selected                          | Organization chosen during sign-in and bound to the session.                                |
| organization_created                           | Commercial or reserved organization created.                                                |
| organization_icon_generated                    | Temporary fallback icon generated.                                                          |
| organization_icon_uploaded / removed           | Custom icon activated or generated fallback restored.                                       |
| organization_role_changed                      | Admin/reviewer/contributor role changed.                                                     |
| platform_admin_assigned / removed              | Protected platform role lifecycle event.                                                    |
| project_created / updated / archived           | Project lifecycle including short-description changes.                                      |
| project_selected                               | User selects an active project in the session organization.                                 |
| context_resolved                               | Organization/project/set/effective-palette resolution completed.                            |
| set_selected                                   | User changes the one active set.                                                             |
| set_created / set_updated / set_archived       | Governed set lifecycle.                                                                      |
| set_project_availability_changed               | A set is added to or removed from a project.                                                 |
| symbol_previewed                               | Thumbnail or preview shown.                                                                  |
| symbol_downloaded                              | Symbol asset downloaded, including format.                                                   |
| favorite_changed                               | User adds or removes a Favorite.                                                             |
| private_symbol_created                         | Organization draft created.                                                                  |
| organization_review_submitted                  | Symbol sent to appointed Organization Reviewers.                                             |
| organization_review_decided                    | Explicit organization approval or rejection recorded.                                       |
| organization_wide_changed                      | Organization-wide flag enabled or disabled.                                                  |
| publication_submitted                          | Same symbol sent to public review.                                                            |
| publication_decided                            | Accepted/rejected/changes requested.                                                          |
| symbol_visibility_changed                      | Same symbol moved between organization-private and public.                                   |
| public_symbol_demoted                          | Platform-admin demotion recorded, including reason and affected-reference summary.           |
| contribution_awarded / contribution_reversed   | Reputation ledger event.                                                                      |
| agent_finding_created / updated / resolved     | Hermes oversight finding lifecycle; no governed action is implied.                           |

# Appendix C. Current-code reference points

| **Code area**                                 | **Relevance**                                                                                                                                         |
|-----------------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------|
| backend/symgov_backend/models/schema.py       | User/UserRole/UserSubscription; CatalogFavourite; CatalogApiKey; CatalogApiUsageEvent; GovernedSymbol; SymbolRevision; AuditEvent.                    |
| backend/symgov_backend/catalog_search.py      | Published Catalog summary, filters and contextual ranking using application, discipline, drawing type, selected layer, units and preferred formats.   |
| backend/symgov_backend/routes/catalog.py      | Current Catalog route surface.                                                                                                                        |
| backend/symgov_backend/catalog_api_auth.py    | Existing Catalog API authentication foundation.                                                                                                       |
| backend/symgov_backend/catalog_api_keys.py    | API key lifecycle and management.                                                                                                                     |
| backend/symgov_backend/catalog_usage.py       | Catalog usage capture.                                                                                                                                |
| backend/symgov_backend/publication_handoff.py | Existing publication workflow/handoff foundation.                                                                                                     |
| backend/symgov_backend/catalog_favourites.py  | Existing personal Favorites service; filename retains historical spelling.                                                                            |
| Hermes agent framework                       | Orchestration foundation for Organization Steward and Platform Governance capabilities, model aliases, findings and routing to Ed/Alfi.               |

The code identifiers `CatalogFavourite` and `catalog_favourites.py` are existing implementation names and are intentionally shown exactly. Product-facing UI terminology uses American English.
