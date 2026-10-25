import { createElement, useCallback, useEffect, useState } from 'react';

import {
  fetchPromotionReviewQueue,
  openOrganizationSymbolPromotionReview,
  submitWorkspaceReviewDecision,
} from './api.js';

// Unified submission & review WP7 (spec FR14/FR15, D1 kept). Promotion requests
// in the public Reviews queue, replacing the Platform Admin form that needed
// hand-typed IDs. The rules are unchanged: a Public Reviewer from a different
// organization opens the request, then accepts it, which publishes the same
// symbol. Accept-only: reject and request-changes are not built for promotion
// review cases (the decisions route refuses them).

const DEFAULT_API = {
  listQueue: fetchPromotionReviewQueue,
  openReview: openOrganizationSymbolPromotionReview,
  decide: submitWorkspaceReviewDecision,
};

const STATUS_LABELS = {
  submitted: 'Waiting for a reviewer',
  triage: 'Open for review',
  in_review: 'In review',
  changes_requested: 'Changes requested',
};

function formatWhen(value) {
  return value ? new Date(value).toLocaleString() : '';
}

function StatusMessage({ status }) {
  if (!status?.message) return null;
  return createElement(
    'p',
    { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode || 'info'}` },
    status.message,
  );
}

export function PromotionReviewQueuePanel({ api = DEFAULT_API }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });

  const refresh = useCallback(async () => {
    setError('');
    try {
      const next = await api.listQueue();
      setItems(next.items);
      return next.items;
    } catch (err) {
      setError(err.message || 'Promotion review queue unavailable.');
      setItems([]);
      return null;
    }
  }, [api]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function openReview(item) {
    setBusyId(item.id);
    setStatus({ mode: '', message: '' });
    try {
      await api.openReview(item.governedSymbolId, item.id);
      setStatus({ mode: 'success', message: `${item.symbolName} is open for your review.` });
      await refresh();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Opening the promotion request failed.' });
    } finally {
      setBusyId('');
    }
  }

  async function accept(item) {
    if (!item.reviewCaseId) return;
    if (!window.confirm(`Accept the promotion of ${item.symbolName} from ${item.organizationName} and publish it to the public Catalog?`)) return;
    setBusyId(item.id);
    setStatus({ mode: '', message: '' });
    try {
      const result = await api.decide(item.reviewCaseId, { decisionCode: 'approve' });
      const failed = (result?.actions || []).some((action) => action.actionStatus === 'failed');
      const remaining = await refresh();
      const stillOpen = Array.isArray(remaining) && remaining.some((entry) => entry.id === item.id);
      setStatus(failed || stillOpen
        ? { mode: 'error', message: `${item.symbolName} was not published: the publication step refused it. The request is still open.` }
        : { mode: 'success', message: `Accepted. ${item.symbolName} is now public.` });
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Accepting the promotion request failed.' });
    } finally {
      setBusyId('');
    }
  }

  return createElement(
    'section',
    { className: 'glass-panel pane promotion-review-queue', 'aria-labelledby': 'promotion-review-queue-heading' },
    createElement('p', { className: 'eyebrow' }, 'Public · promotion requests'),
    createElement('h2', { id: 'promotion-review-queue-heading' }, 'Private symbols proposed for the public Catalog'),
    createElement('p', { className: 'title-support' },
      'An organization asks for one of its approved private symbols to become public. Accepting publishes that same symbol with a new S-number. A reviewer from the submitting organization cannot decide its request.'),
    items === null ? createElement('p', { role: 'status' }, 'Loading promotion requests…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    items && items.length === 0 && !error ? createElement('p', { role: 'status' }, 'No promotion requests are waiting.') : null,
    StatusMessage({ status }),
    items && items.length > 0
      ? createElement('ul', { className: 'set-admin-list', 'aria-label': 'Promotion requests' },
        items.map((item) => {
          const busy = busyId === item.id;
          return createElement('li', { key: item.id, className: 'set-admin-item promotion-review-item' },
            createElement('div', null,
              createElement('strong', null, item.symbolName),
              createElement('p', { className: 'set-admin-muted' },
                `From ${item.organizationName} · submitted by ${item.submittedByName || 'unknown'} ${formatWhen(item.submittedAt)}`),
              createElement('p', { className: 'set-admin-muted' },
                `${STATUS_LABELS[item.status] || item.status} · ${item.category} · ${item.discipline}${item.revision?.revisionLabel ? ` · revision ${item.revision.revisionLabel}` : ''}`),
              item.revision?.summary ? createElement('p', null, item.revision.summary) : null,
              createElement('p', null, createElement('span', { className: 'set-admin-muted' }, 'Reason: '), item.reason),
              item.possibleDuplicateOfSlug
                ? createElement('p', { className: 'set-admin-status info' }, `Possible duplicate of public symbol ${item.possibleDuplicateOfSlug}.`)
                : null,
              item.canDecide ? null : createElement('p', { className: 'set-admin-muted' }, item.blockedReason),
            ),
            item.canDecide && item.status === 'submitted'
              ? createElement('button', {
                type: 'button',
                className: 'action-button compact',
                disabled: busy,
                onClick: () => openReview(item),
                'aria-label': `Open the promotion of ${item.symbolName} for review`,
              }, busy ? 'Working…' : 'Open for review')
              : null,
            item.canDecide && item.status !== 'submitted' && item.reviewCaseId
              ? createElement('button', {
                type: 'button',
                className: 'action-button compact primary',
                disabled: busy,
                onClick: () => accept(item),
                'aria-label': `Accept the promotion of ${item.symbolName}`,
              }, busy ? 'Working…' : 'Accept and publish')
              : null,
          );
        }))
      : null,
  );
}
