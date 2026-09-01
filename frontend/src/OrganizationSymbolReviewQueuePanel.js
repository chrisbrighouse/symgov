import { createElement, useCallback, useEffect, useMemo, useState } from 'react';

import { decideOrganizationSymbolReviewSubmission, listOrganizationSymbolDrafts } from './api.js';

const DEFAULT_API = {
  listDrafts: listOrganizationSymbolDrafts,
  decide: decideOrganizationSymbolReviewSubmission,
};

const DECISIONS = [
  { value: 'approved', label: 'Approve' },
  { value: 'rejected', label: 'Reject' },
  { value: 'changes_requested', label: 'Request changes' },
];

function StatusMessage({ status }) {
  if (!status?.message) return null;
  return createElement(
    'p',
    { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode || 'info'}` },
    status.message,
  );
}

export function OrganizationSymbolReviewQueuePanel({ api = DEFAULT_API }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [drafts, setDrafts] = useState([]);
  const [activeSymbolId, setActiveSymbolId] = useState('');
  const [rationaleBySymbolId, setRationaleBySymbolId] = useState({});
  const [decidingSymbolId, setDecidingSymbolId] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next = await api.listDrafts();
      setDrafts(Array.isArray(next?.items) ? next.items : []);
    } catch (err) {
      setError(err.message || 'Organization symbol review queue unavailable.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const queue = useMemo(
    () => drafts.filter((draft) => Boolean(draft.currentRevision?.pendingSubmissionId)),
    [drafts],
  );

  useEffect(() => {
    if (activeSymbolId && !queue.some((draft) => draft.id === activeSymbolId)) {
      setActiveSymbolId('');
    }
  }, [queue, activeSymbolId]);

  const activeDraft = queue.find((draft) => draft.id === activeSymbolId) || queue[0] || null;

  async function decide(draft, decisionValue) {
    const submissionId = draft.currentRevision?.pendingSubmissionId;
    if (!submissionId) return;
    setDecidingSymbolId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      const rationale = (rationaleBySymbolId[draft.id] || '').trim();
      await api.decide(draft.id, submissionId, {
        decision: decisionValue,
        rationale: rationale || undefined,
      });
      setStatus({ mode: 'success', message: `${draft.canonicalName}: ${decisionValue.replace('_', ' ')}.` });
      setRationaleBySymbolId((current) => ({ ...current, [draft.id]: '' }));
      await refresh();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Review decision failed.' });
    } finally {
      setDecidingSymbolId('');
    }
  }

  return createElement(
    'section',
    { className: 'organization-symbol-review-queue-panel', 'aria-labelledby': 'organization-symbol-review-heading' },
    createElement('h2', { id: 'organization-symbol-review-heading' }, 'Organization Symbols'),
    loading ? createElement('p', { role: 'status' }, 'Loading organization symbol review queue…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    !loading && !error && queue.length === 0
      ? createElement('p', { role: 'status' }, 'No organization symbol submissions awaiting review.')
      : null,
    StatusMessage({ status }),
    createElement(
      'div',
      { className: 'organization-symbol-review-layout' },
      createElement(
        'ul',
        { className: 'set-admin-list', 'aria-label': 'Submissions awaiting organization review' },
        queue.map((draft) => createElement(
          'li',
          { key: draft.id, className: 'set-admin-item' },
          createElement('button', {
            type: 'button',
            className: draft.id === activeDraft?.id ? 'active' : '',
            onClick: () => setActiveSymbolId(draft.id),
            'aria-label': `Review ${draft.canonicalName}`,
          },
          createElement('strong', null, `${draft.canonicalName} · ${draft.slug}`),
          createElement('p', { className: 'set-admin-muted' }, `Submitted ${new Date(draft.currentRevision.pendingSubmissionSubmittedAt).toLocaleString()}`),
          ),
        )),
      ),
      activeDraft
        ? createElement(
          'div',
          { className: 'organization-symbol-review-detail' },
          createElement('h3', null, activeDraft.canonicalName),
          createElement('p', { className: 'set-admin-muted' }, `Category: ${activeDraft.category} · Discipline: ${activeDraft.discipline}`),
          createElement('p', null, activeDraft.currentRevision.summary),
          activeDraft.currentRevision.description
            ? createElement('p', { className: 'set-admin-muted' }, activeDraft.currentRevision.description)
            : null,
          createElement('p', { className: 'set-admin-muted' }, `Assets: ${(activeDraft.currentRevision.assets || []).map((asset) => asset.filename).join(', ') || 'none'}`),
          activeDraft.currentRevision.pendingSubmissionRationale
            ? createElement('p', { className: 'set-admin-muted' }, `Submitter rationale: ${activeDraft.currentRevision.pendingSubmissionRationale}`)
            : null,
          createElement('label', { htmlFor: `review-rationale-${activeDraft.id}` },
            'Decision rationale (optional)',
            createElement('textarea', {
              id: `review-rationale-${activeDraft.id}`,
              rows: 2,
              value: rationaleBySymbolId[activeDraft.id] || '',
              onChange: (event) => setRationaleBySymbolId((current) => ({ ...current, [activeDraft.id]: event.target.value })),
            }),
          ),
          createElement(
            'div',
            { className: 'set-admin-actions', role: 'group', 'aria-label': `Decide ${activeDraft.canonicalName}` },
            DECISIONS.map((option) => createElement('button', {
              key: option.value,
              type: 'button',
              disabled: decidingSymbolId === activeDraft.id,
              onClick: () => decide(activeDraft, option.value),
              'aria-label': `${option.label} ${activeDraft.canonicalName}`,
            }, decidingSymbolId === activeDraft.id ? 'Working…' : option.label)),
          ),
        )
        : null,
    ),
    error
      ? createElement('button', { type: 'button', onClick: () => { refresh(); }, 'aria-label': 'Retry organization symbol review queue' }, 'Retry')
      : null,
  );
}
