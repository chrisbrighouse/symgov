import { createElement, useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { canAccessPlatformAdmin, sessionRoles } from './adminJourneys.js';
import { requestJson } from './api.js';
import { symbolIdLabelOrState } from './catalogWorkbench.js';
import { lifecycleLabel, workingRevision } from './OrganizationSymbolDraftsPanel.js';
import { OrganizationSymbolReviewQueuePanel } from './OrganizationSymbolReviewQueuePanel.js';
import { canReviewOrganizationSymbols } from './projectContext.js';
import { ScopeTabs } from './scopeTabs.js';

// Unified submission & review WP4 (spec draft 2 FR11). One Reviews surface with
// a Public / Private filter. Each tab appears only for a role that holds it, in
// whatever session the user is in:
// - Public: the public review queue, for Public Reviewers (and platform admins).
// - Private, in an organization session: that organization's queue, for its Org
//   Reviewers and Org Admins.
// - Private, for a Platform Admin: a read-only support view of any one
//   organization's private symbols (FR10). It has no decision controls.

export function reviewsAccess(auth) {
  const user = auth?.user;
  const workflowV2 = user?.capabilities?.organizationSymbolWorkflowV2Enabled === true;
  const roles = sessionRoles(user);
  const canPublic = roles.includes('admin') || roles.includes('reviewer');
  const canPrivate = workflowV2 && canReviewOrganizationSymbols(auth);
  const canSupport = workflowV2 && !canPrivate && canAccessPlatformAdmin(user);
  return { canPublic, canPrivate, canSupport, any: canPublic || canPrivate || canSupport };
}

const SCOPES = [
  { key: 'public', label: 'Public' },
  { key: 'private', label: 'Private' },
];

function scopeTabId(key) {
  return `reviews-scope-tab-${key}`;
}

export function ReviewsHub({ auth, renderPublic, renderDenied }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const access = reviewsAccess(auth);
  const available = SCOPES.filter((scope) => (scope.key === 'public' ? access.canPublic : access.canPrivate || access.canSupport));
  if (available.length === 0) return renderDenied();

  const requested = searchParams.get('scope');
  const fallback = access.canPrivate ? 'private' : available[0].key;
  const scope = available.some((entry) => entry.key === requested) ? requested : fallback;
  const organizationName = auth?.user?.organization?.displayName || 'your organization';

  function select(key) {
    const next = new URLSearchParams(searchParams);
    next.set('scope', key);
    setSearchParams(next, { replace: true });
  }

  return createElement(
    'div',
    { className: 'reviews-hub' },
    available.length > 1
      ? createElement(ScopeTabs, {
        entries: available.map((entry) => ({
          key: entry.key,
          label: entry.key === 'private' && access.canPrivate ? `Private · ${organizationName}` : entry.label,
        })),
        selected: scope,
        onSelect: select,
        label: 'Review queues',
        idPrefix: 'reviews-scope-tab',
        panelId: 'reviews-scope-panel',
      })
      : null,
    createElement(
      'div',
      { id: 'reviews-scope-panel', role: available.length > 1 ? 'tabpanel' : undefined, 'aria-labelledby': available.length > 1 ? scopeTabId(scope) : undefined },
      scope === 'public' ? renderPublic() : null,
      scope === 'private' && access.canPrivate
        ? createElement('section', { className: 'experience-shell' },
          createElement('div', { className: 'glass-panel pane' },
            createElement('p', { className: 'eyebrow' }, `Private · ${organizationName}`),
            createElement(OrganizationSymbolReviewQueuePanel, { auth })))
        : null,
      scope === 'private' && access.canSupport ? createElement(PrivateSupportView, {}) : null,
    ),
  );
}

async function getJson(path) {
  const result = await requestJson(path, { cache: 'no-store' });
  if (!result.ok) {
    const error = new Error(result.message || 'Request failed.');
    error.status = result.status;
    throw error;
  }
  return result.payload;
}

const DEFAULT_SUPPORT_API = {
  listOrganizations: () => getJson('/platform/organizations?page=1&pageSize=200'),
  listSymbols: (organizationId) => getJson(`/platform/organizations/${encodeURIComponent(organizationId)}/organization-symbols`),
  history: (organizationId, symbolId) => getJson(
    `/platform/organizations/${encodeURIComponent(organizationId)}/organization-symbols/${encodeURIComponent(symbolId)}/history`,
  ),
};

const OUTCOME_LABELS = {
  active: 'Awaiting review',
  withdrawn: 'Withdrawn',
  approved: 'Approved',
  rejected: 'Rejected',
  changes_requested: 'Changes requested',
};

function formatWhen(value) {
  return value ? new Date(value).toLocaleString() : '';
}

export function PrivateSupportView({ api = DEFAULT_SUPPORT_API }) {
  const [organizations, setOrganizations] = useState([]);
  const [organizationId, setOrganizationId] = useState('');
  const [symbols, setSymbols] = useState(null);
  const [history, setHistory] = useState({});
  const [error, setError] = useState('');

  useEffect(() => {
    api.listOrganizations()
      .then((data) => setOrganizations((data?.items || []).filter((org) => !org.isProtected)))
      .catch((err) => setError(err.message));
  }, [api]);

  useEffect(() => {
    setSymbols(null);
    setHistory({});
    if (!organizationId) return;
    setError('');
    api.listSymbols(organizationId)
      .then((data) => setSymbols(data?.items || []))
      .catch((err) => setError(err.message));
  }, [api, organizationId]);

  async function showHistory(symbolId) {
    try {
      const data = await api.history(organizationId, symbolId);
      setHistory((current) => ({ ...current, [symbolId]: data?.items || [] }));
    } catch (err) {
      setError(err.message);
    }
  }

  return createElement(
    'section',
    { className: 'experience-shell' },
    createElement(
      'div',
      { className: 'glass-panel pane private-support-view', 'aria-labelledby': 'private-support-heading' },
      createElement('p', { className: 'eyebrow' }, 'Private · support view'),
      createElement('h2', { id: 'private-support-heading' }, 'Organization private symbols'),
      createElement('p', { className: 'title-support' },
        'Read-only, for supporting an organization. Decisions stay with the organization, and every view here is recorded in the audit trail.'),
      createElement('label', { className: 'field', htmlFor: 'private-support-organization' },
        createElement('span', null, 'Organization'),
        createElement('select', {
          id: 'private-support-organization',
          value: organizationId,
          onChange: (event) => setOrganizationId(event.target.value),
        },
          createElement('option', { value: '' }, 'Choose an organization'),
          ...organizations.map((org) => createElement('option', { key: org.id, value: org.id }, `${org.displayName} (${org.code})`)),
        )),
      error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
      organizationId && symbols === null && !error ? createElement('p', { role: 'status' }, 'Loading…') : null,
      symbols && symbols.length === 0 ? createElement('p', { role: 'status' }, 'This organization has no private symbols.') : null,
      symbols && symbols.length > 0
        ? createElement('ul', { className: 'set-admin-list', 'aria-label': 'Private symbols' },
          symbols.map((symbol) => {
            const working = workingRevision(symbol);
            const items = history[symbol.id];
            return createElement('li', { key: symbol.id, className: 'set-admin-item private-support-item' },
              createElement('div', null,
                createElement('strong', null, `${symbol.canonicalName} · ${symbolIdLabelOrState(symbol)}`),
                createElement('p', { className: 'set-admin-muted' }, `Status: ${lifecycleLabel(symbol)} · Updated ${formatWhen(symbol.updatedAt)}`),
                working.revision?.pendingSubmissionId
                  ? createElement('p', { className: 'set-admin-muted' },
                    `${working.pending ? 'New revision awaiting' : 'Awaiting'} organization review since ${formatWhen(working.revision.pendingSubmissionSubmittedAt)}`)
                  : null,
                items
                  ? createElement('ol', { className: 'organization-symbol-history', 'aria-label': `Review history of ${symbol.canonicalName}` },
                    items.length === 0 ? createElement('li', null, 'Not submitted yet.') : null,
                    ...items.map((entry) => createElement('li', { key: entry.submissionId },
                      createElement('strong', null, OUTCOME_LABELS[entry.outcome] || entry.outcome),
                      ` · submitted by ${entry.submittedByName || 'unknown'} ${formatWhen(entry.submittedAt)}`,
                      entry.decidedByName ? ` · decided by ${entry.decidedByName} ${formatWhen(entry.closedAt)}` : null,
                      entry.decisionRationale ? createElement('p', { className: 'set-admin-muted' }, `Reviewer: ${entry.decisionRationale}`) : null,
                    )))
                  : null,
              ),
              items
                ? null
                : createElement('button', {
                  type: 'button',
                  className: 'action-button compact ghost',
                  onClick: () => showHistory(symbol.id),
                  'aria-label': `Show review history of ${symbol.canonicalName}`,
                }, 'Review history'),
            );
          }))
        : null,
    ),
  );
}
