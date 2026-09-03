import { createElement, useCallback, useEffect, useState } from 'react';

import {
  listOrganizationSymbolDrafts,
  listOrganizationSymbolPromotionRequests,
  submitOrganizationSymbolPromotionRequest,
  withdrawOrganizationSymbolPromotionRequest,
} from './api.js';

const DEFAULT_API = {
  listDrafts: listOrganizationSymbolDrafts,
  listPromotionRequests: listOrganizationSymbolPromotionRequests,
  submitPromotionRequest: submitOrganizationSymbolPromotionRequest,
  withdrawPromotionRequest: withdrawOrganizationSymbolPromotionRequest,
};

const OPEN_STATUSES = new Set(['submitted', 'triage', 'in_review', 'changes_requested']);
const ELIGIBLE_LIFECYCLE_STATES = new Set(['approved', 'withdrawn']);

function StatusMessage({ status }) {
  if (!status?.message) return null;
  return createElement('p', { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode || 'info'}` }, status.message);
}

export function PromotionSubmissionPanel({ isAdmin, api = DEFAULT_API }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [drafts, setDrafts] = useState([]);
  const [requestsBySymbolId, setRequestsBySymbolId] = useState({});
  const [reasonBySymbolId, setReasonBySymbolId] = useState({});
  const [acknowledgedBySymbolId, setAcknowledgedBySymbolId] = useState({});
  const [busySymbolId, setBusySymbolId] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next = await api.listDrafts();
      const items = Array.isArray(next?.items) ? next.items : [];
      setDrafts(items);
      const eligible = items.filter((draft) => ELIGIBLE_LIFECYCLE_STATES.has(draft.currentRevision?.lifecycleState));
      const requestLists = await Promise.all(eligible.map((draft) => api.listPromotionRequests(draft.id)));
      const nextRequests = {};
      eligible.forEach((draft, index) => {
        nextRequests[draft.id] = requestLists[index]?.items || [];
      });
      setRequestsBySymbolId(nextRequests);
    } catch (err) {
      setError(err.message || 'Promotion-eligible symbols unavailable.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function submit(draft) {
    const reason = (reasonBySymbolId[draft.id] || '').trim();
    const acknowledged = Boolean(acknowledgedBySymbolId[draft.id]);
    if (!reason || !acknowledged) return;
    setBusySymbolId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      await api.submitPromotionRequest(draft.id, { reason, sharingAcknowledgment: acknowledged });
      setStatus({ mode: 'success', message: `${draft.canonicalName} submitted for public promotion.` });
      setReasonBySymbolId((current) => ({ ...current, [draft.id]: '' }));
      setAcknowledgedBySymbolId((current) => ({ ...current, [draft.id]: false }));
      await refresh();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Promotion request submission failed.' });
    } finally {
      setBusySymbolId('');
    }
  }

  async function withdraw(draft, request) {
    if (!window.confirm(`Withdraw the pending promotion request for ${draft.canonicalName}?`)) return;
    setBusySymbolId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      await api.withdrawPromotionRequest(draft.id, request.id, {});
      setStatus({ mode: 'success', message: `Promotion request for ${draft.canonicalName} withdrawn.` });
      await refresh();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Promotion request withdrawal failed.' });
    } finally {
      setBusySymbolId('');
    }
  }

  if (!isAdmin) {
    return createElement(
      'section',
      { className: 'promotion-submission-panel', 'aria-labelledby': 'promotion-submission-heading' },
      createElement('h2', { id: 'promotion-submission-heading' }, 'Public promotion'),
      createElement('p', { role: 'status' }, 'Organization Admin privileges are required to submit symbols for public promotion.'),
    );
  }

  const eligibleDrafts = drafts.filter((draft) => ELIGIBLE_LIFECYCLE_STATES.has(draft.currentRevision?.lifecycleState));

  return createElement(
    'section',
    { className: 'promotion-submission-panel', 'aria-labelledby': 'promotion-submission-heading' },
    createElement('h2', { id: 'promotion-submission-heading' }, 'Public promotion'),
    loading ? createElement('p', { role: 'status' }, 'Loading promotion-eligible symbols…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    !loading && !error && eligibleDrafts.length === 0
      ? createElement('p', { role: 'status' }, 'No approved organization symbols are currently eligible for public promotion.')
      : null,
    StatusMessage({ status }),
    createElement(
      'ul',
      { className: 'set-admin-list' },
      eligibleDrafts.map((draft) => {
        const openRequests = (requestsBySymbolId[draft.id] || []).filter((request) => OPEN_STATUSES.has(request.status));
        const busy = busySymbolId === draft.id;
        return createElement(
          'li',
          { key: draft.id, className: 'set-admin-item' },
          createElement(
            'div',
            null,
            createElement('strong', null, `${draft.canonicalName} · ${draft.slug}`),
            createElement('p', { className: 'set-admin-muted' }, `Revision status: ${draft.currentRevision?.lifecycleState || 'none'}`),
          ),
          openRequests.length > 0
            ? createElement(
                'div',
                null,
                openRequests.map((request) => createElement(
                  'div',
                  { key: request.id, className: 'set-admin-actions' },
                  createElement('p', { className: 'set-admin-muted' }, `Promotion request pending: ${request.status}, submitted ${new Date(request.submittedAt).toLocaleString()}`),
                  createElement('button', {
                    type: 'button',
                    disabled: busy,
                    onClick: () => withdraw(draft, request),
                    'aria-label': `Withdraw promotion request for ${draft.canonicalName}`,
                  }, busy ? 'Working…' : 'Withdraw promotion request'),
                )),
              )
            : createElement(
                'div',
                { className: 'set-admin-form' },
                createElement('label', { htmlFor: `promotion-reason-${draft.id}` },
                  'Reason for public promotion',
                  createElement('textarea', {
                    id: `promotion-reason-${draft.id}`,
                    rows: 2,
                    value: reasonBySymbolId[draft.id] || '',
                    onChange: (event) => setReasonBySymbolId((current) => ({ ...current, [draft.id]: event.target.value })),
                    required: true,
                  }),
                ),
                createElement('label', { htmlFor: `promotion-ack-${draft.id}` },
                  createElement('input', {
                    id: `promotion-ack-${draft.id}`,
                    type: 'checkbox',
                    checked: Boolean(acknowledgedBySymbolId[draft.id]),
                    onChange: (event) => setAcknowledgedBySymbolId((current) => ({ ...current, [draft.id]: event.target.checked })),
                  }),
                  ' I acknowledge this symbol will be shared freely with the Symgov community.',
                ),
                createElement('button', {
                  type: 'button',
                  disabled: busy || !(reasonBySymbolId[draft.id] || '').trim() || !acknowledgedBySymbolId[draft.id],
                  onClick: () => submit(draft),
                  'aria-label': `Submit ${draft.canonicalName} for public promotion`,
                }, busy ? 'Submitting…' : 'Submit for public promotion'),
              ),
        );
      }),
    ),
  );
}
