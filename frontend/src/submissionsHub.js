import { createElement } from 'react';
import { useSearchParams } from 'react-router-dom';

import { sessionRoles } from './adminJourneys.js';
import { OrganizationSymbolDraftsPanel } from './OrganizationSymbolDraftsPanel.js';
import { PromotedSymbolsPanel } from './PromotedSymbolsPanel.js';
import { canCreateOrganizationSymbolDrafts, canReviewOrganizationSymbols } from './projectContext.js';
import { ScopeTabs } from './scopeTabs.js';

// Unified submission & review WP5. One Submissions entry point with a target
// selector. Each target appears only for a role that holds it, in whatever
// session the user is in (D12):
// - Private: the organization's private library, for Org Submitters
//   (`contributor`) and Org Admins in their organization's session. It hosts the
//   private draft form and the caller's own submissions with their history;
//   Org Admins and Org Reviewers can also list every member's symbols. Symbols
//   promoted to public leave that list and are shown after it (WP7).
// - Public: today's public intake form and agent pipeline, unchanged, for Public
//   Submitters (and admins).

export function submissionsAccess(auth) {
  const user = auth?.user;
  const workflowV2 = user?.capabilities?.organizationSymbolWorkflowV2Enabled === true;
  const roles = sessionRoles(user);
  const canPublic = roles.includes('admin') || roles.includes('submitter');
  const canPrivate = workflowV2 && canCreateOrganizationSymbolDrafts(auth);
  return { canPublic, canPrivate, any: canPublic || canPrivate };
}

export function SubmissionsHub({ auth, renderPublic, renderDenied }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const access = submissionsAccess(auth);
  const organizationName = auth?.user?.organization?.displayName || 'your organization';
  const available = [
    access.canPrivate ? { key: 'private', label: `Private library · ${organizationName}` } : null,
    access.canPublic ? { key: 'public', label: 'Public' } : null,
  ].filter(Boolean);
  if (available.length === 0) return renderDenied();

  const requested = searchParams.get('target');
  const target = available.some((entry) => entry.key === requested) ? requested : available[0].key;

  function select(key) {
    const next = new URLSearchParams(searchParams);
    next.set('target', key);
    setSearchParams(next, { replace: true });
  }

  return createElement(
    'div',
    { className: 'reviews-hub' },
    available.length > 1
      ? createElement(ScopeTabs, {
        entries: available,
        selected: target,
        onSelect: select,
        label: 'Submission target',
        idPrefix: 'submissions-target-tab',
        panelId: 'submissions-target-panel',
      })
      : null,
    createElement(
      'div',
      {
        id: 'submissions-target-panel',
        role: available.length > 1 ? 'tabpanel' : undefined,
        'aria-labelledby': available.length > 1 ? `submissions-target-tab-${target}` : undefined,
      },
      target === 'public' ? renderPublic() : null,
      target === 'private'
        ? createElement('section', { className: 'experience-shell' },
          createElement('div', { className: 'glass-panel pane' },
            createElement('p', { className: 'eyebrow' }, `Private library · ${organizationName}`),
            createElement('p', { className: 'title-support' },
              `Private symbols stay inside ${organizationName}. Once approved they join the organization library; they never enter the public Catalog unless promoted.`),
            createElement(OrganizationSymbolDraftsPanel, {
              canCreate: true,
              workflowV2: true,
              submissionsView: true,
              canViewAll: canReviewOrganizationSymbols(auth),
              currentUserId: auth?.user?.id || '',
            }),
            createElement(PromotedSymbolsPanel, {})))
        : null,
    ),
  );
}
