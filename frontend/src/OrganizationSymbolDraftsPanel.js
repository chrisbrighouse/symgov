import { createElement, useCallback, useEffect, useState } from 'react';

import {
  attachOrganizationSymbolAsset,
  createOrganizationSymbolDraft,
  decideOrganizationSymbolReviewSubmission,
  discardOrganizationSymbolPendingRevision,
  fetchOrganizationReviewPolicy,
  fetchOrganizationSymbolHistory,
  listOrganizationSymbolDrafts,
  removeOrganizationSymbolAsset,
  startOrganizationSymbolRevision,
  submitOrganizationSymbolDraftForReview,
  updateOrganizationSymbolDraft,
  withdrawOrganizationSymbolSubmission,
} from './api.js';
import { CATALOG_DISCIPLINE_ORDER, symbolIdLabelOrState } from './catalogWorkbench.js';
import { normalizeFacetValues } from './projectContext.js';

const DEFAULT_API = {
  listDrafts: listOrganizationSymbolDrafts,
  createDraft: createOrganizationSymbolDraft,
  attachAsset: attachOrganizationSymbolAsset,
  submitForReview: submitOrganizationSymbolDraftForReview,
  updateDraft: updateOrganizationSymbolDraft,
  removeAsset: removeOrganizationSymbolAsset,
  withdrawSubmission: withdrawOrganizationSymbolSubmission,
  fetchHistory: fetchOrganizationSymbolHistory,
  startRevision: startOrganizationSymbolRevision,
  discardPendingRevision: discardOrganizationSymbolPendingRevision,
  fetchReviewPolicy: fetchOrganizationReviewPolicy,
  decide: decideOrganizationSymbolReviewSubmission,
};

// The revision being worked on: a published symbol's pending revision when it
// has one, otherwise its current revision.
export function workingRevision(draft) {
  if (draft?.pendingRevision) return { id: draft.pendingRevisionId, revision: draft.pendingRevision, pending: true };
  return { id: draft?.currentRevisionId || '', revision: draft?.currentRevision || null, pending: false };
}

const LIFECYCLE_LABELS = {
  draft: 'Draft',
  review: 'In review',
  approved: 'Approved',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
};

export function lifecycleLabel(draft) {
  const current = draft?.currentRevision?.lifecycleState;
  if (current === 'approved' && draft.organizationWide) return 'Published to the organization library';
  if (current === 'approved') return 'Approved (Set-only)';
  return LIFECYCLE_LABELS[current] || current || 'none';
}

const HISTORY_OUTCOME_LABELS = {
  active: 'Awaiting review',
  withdrawn: 'Withdrawn',
  approved: 'Approved',
  rejected: 'Rejected',
  changes_requested: 'Changes requested',
};

function formatWhen(value) {
  return value ? new Date(value).toLocaleString() : '';
}

function editFormFor(draft, revision) {
  return {
    name: revision?.name || draft.canonicalName || '',
    category: revision?.category || draft.category || '',
    discipline: revision?.discipline || draft.discipline || '',
    summary: revision?.summary || '',
    description: revision?.description || '',
    aliases: (revision?.aliases || []).join(', '),
    keywords: (revision?.keywords || []).join(', '),
  };
}

function StatusMessage({ status }) {
  if (!status?.message) return null;
  return createElement(
    'p',
    { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode || 'info'}` },
    status.message,
  );
}

function emptyForm() {
  return {
    name: '',
    category: '',
    discipline: '',
    summary: '',
    description: '',
    aliases: '',
    keywords: '',
  };
}

function readFileAsBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = () => reject(new Error('Could not read the selected file.'));
    reader.onload = () => {
      const raw = String(reader.result || '');
      const separatorIndex = raw.indexOf(',');
      resolve(separatorIndex >= 0 ? raw.slice(separatorIndex + 1) : raw);
    };
    reader.readAsDataURL(file);
  });
}

export function OrganizationSymbolDraftsPanel({ canCreate = false, workflowV2 = false, currentUserId = '', api = DEFAULT_API }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [drafts, setDrafts] = useState([]);
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [busyDraftId, setBusyDraftId] = useState('');
  const [rationaleByDraftId, setRationaleByDraftId] = useState({});
  const [reviewPolicy, setReviewPolicy] = useState(null);

  useEffect(() => {
    if (!workflowV2 || !api.fetchReviewPolicy) return;
    api.fetchReviewPolicy().then(setReviewPolicy).catch(() => setReviewPolicy(null));
  }, [api, workflowV2]);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next = await api.listDrafts();
      setDrafts(Array.isArray(next?.items) ? next.items : []);
    } catch (err) {
      setError(err.message || 'Organization symbol drafts unavailable.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function createDraft(event) {
    event?.preventDefault?.();
    setSaving(true);
    setStatus({ mode: '', message: '' });
    try {
      const payload = {
        name: form.name.trim(),
        category: form.category.trim(),
        discipline: form.discipline.trim(),
        summary: form.summary.trim(),
        description: form.description.trim() || undefined,
        aliases: normalizeFacetValues(form.aliases),
        keywords: normalizeFacetValues(form.keywords),
      };
      await api.createDraft(payload);
      setForm(emptyForm());
      setStatus({ mode: 'success', message: 'Draft created.' });
      await refresh();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Draft create failed.' });
    } finally {
      setSaving(false);
    }
  }

  async function runAction(draft, action, successMessage) {
    setBusyDraftId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      await action();
      setStatus({ mode: 'success', message: successMessage });
      await refresh();
      return true;
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'That did not work.' });
      return false;
    } finally {
      setBusyDraftId('');
    }
  }

  async function uploadAsset(draft, file) {
    const working = workingRevision(draft);
    if (!file || !working.id) return;
    setBusyDraftId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      const contentBase64 = await readFileAsBase64(file);
      await api.attachAsset(draft.id, working.id, {
        filename: file.name,
        contentType: file.type || 'application/octet-stream',
        contentBase64,
        role: 'source',
      });
      setStatus({ mode: 'success', message: `Asset ${file.name} attached to ${draft.canonicalName}.` });
      await refresh();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Asset upload failed.' });
    } finally {
      setBusyDraftId('');
    }
  }

  async function submitForReview(draft) {
    const working = workingRevision(draft);
    if (!working.id) return;
    setBusyDraftId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      const rationale = (rationaleByDraftId[draft.id] || '').trim();
      await api.submitForReview(draft.id, working.id, rationale ? { rationale } : {});
      setStatus({ mode: 'success', message: `${draft.canonicalName} submitted for organization review.` });
      setRationaleByDraftId((current) => ({ ...current, [draft.id]: '' }));
      await refresh();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Submission failed.' });
    } finally {
      setBusyDraftId('');
    }
  }

  const normalizedAliases = normalizeFacetValues(form.aliases);
  const normalizedKeywords = normalizeFacetValues(form.keywords);

  return createElement(
    'section',
    { className: 'organization-symbol-drafts-panel', 'aria-labelledby': 'organization-symbol-drafts-heading' },
    createElement('h2', { id: 'organization-symbol-drafts-heading' }, 'Organization Symbol Drafts'),
    loading ? createElement('p', { role: 'status' }, 'Loading organization symbol drafts…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    !loading && !error && drafts.length === 0
      ? createElement('p', { role: 'status' }, 'No organization symbol drafts yet.')
      : null,
    StatusMessage({ status }),
    createElement(
      'ul',
      { className: 'set-admin-list' },
      drafts.map((draft) => createElement(DraftItem, {
        key: draft.id,
        draft,
        workflowV2,
        currentUserId,
        selfApprovalAllowed: reviewPolicy?.allowSelfApproval === true,
        api,
        busy: busyDraftId === draft.id,
        rationale: rationaleByDraftId[draft.id] || '',
        onRationaleChange: (value) => setRationaleByDraftId((current) => ({ ...current, [draft.id]: value })),
        onUploadAsset: (file) => uploadAsset(draft, file),
        onSubmit: () => submitForReview(draft),
        runAction: (action, message) => runAction(draft, action, message),
      })),
    ),
    error
      ? createElement('button', { type: 'button', onClick: () => { refresh(); }, 'aria-label': 'Retry organization symbol drafts' }, 'Retry')
      : null,
    canCreate
      ? createElement(
        'form',
        { className: 'set-admin-form', onSubmit: createDraft },
        createElement('h3', null, 'Create Draft'),
        createElement('label', { htmlFor: 'org-symbol-draft-name' },
          'Symbol name',
          createElement('input', {
            id: 'org-symbol-draft-name',
            value: form.name,
            onChange: (event) => setForm((current) => ({ ...current, name: event.target.value })),
            required: true,
          }),
        ),
        createElement('label', { htmlFor: 'org-symbol-draft-category' },
          'Category',
          createElement('input', {
            id: 'org-symbol-draft-category',
            value: form.category,
            onChange: (event) => setForm((current) => ({ ...current, category: event.target.value })),
            required: true,
          }),
        ),
        createElement('label', { htmlFor: 'org-symbol-draft-discipline' },
          'Discipline',
          // Only the Catalog's standard names are accepted (X-04).
          createElement('select', {
            id: 'org-symbol-draft-discipline',
            value: form.discipline,
            onChange: (event) => setForm((current) => ({ ...current, discipline: event.target.value })),
            required: true,
          },
            createElement('option', { value: '' }, 'Choose a discipline'),
            ...CATALOG_DISCIPLINE_ORDER.map((name) => createElement('option', { key: name, value: name }, name)),
          ),
        ),
        createElement('label', { htmlFor: 'org-symbol-draft-summary' },
          'Summary',
          createElement('textarea', {
            id: 'org-symbol-draft-summary',
            rows: 2,
            value: form.summary,
            onChange: (event) => setForm((current) => ({ ...current, summary: event.target.value })),
            required: true,
          }),
        ),
        createElement('label', { htmlFor: 'org-symbol-draft-description' },
          'Description (optional)',
          createElement('textarea', {
            id: 'org-symbol-draft-description',
            rows: 3,
            value: form.description,
            onChange: (event) => setForm((current) => ({ ...current, description: event.target.value })),
          }),
        ),
        createElement('label', { htmlFor: 'org-symbol-draft-aliases' },
          'Aliases',
          createElement('textarea', {
            id: 'org-symbol-draft-aliases',
            rows: 2,
            value: form.aliases,
            onChange: (event) => setForm((current) => ({ ...current, aliases: event.target.value })),
          }),
        ),
        createElement('p', { className: 'set-admin-muted', role: 'status' }, `Normalized aliases: ${normalizedAliases.join(', ') || 'none'}`),
        createElement('label', { htmlFor: 'org-symbol-draft-keywords' },
          'Keywords',
          createElement('textarea', {
            id: 'org-symbol-draft-keywords',
            rows: 2,
            value: form.keywords,
            onChange: (event) => setForm((current) => ({ ...current, keywords: event.target.value })),
          }),
        ),
        createElement('p', { className: 'set-admin-muted', role: 'status' }, `Normalized keywords: ${normalizedKeywords.join(', ') || 'none'}`),
        createElement('div', { className: 'set-admin-actions' },
          createElement('button', { type: 'submit', disabled: saving, 'aria-label': 'Create organization symbol draft' }, saving ? 'Saving…' : 'Create draft'),
        ),
      )
      : null,
  );
}

function DraftItem({ draft, workflowV2, currentUserId, selfApprovalAllowed = false, api, busy, rationale, onRationaleChange, onUploadAsset, onSubmit, runAction }) {
  const [editForm, setEditForm] = useState(null);
  const [history, setHistory] = useState(null);
  const [historyError, setHistoryError] = useState('');
  const working = workingRevision(draft);
  const revision = working.revision;
  const isDraft = Boolean(working.id) && revision?.lifecycleState === 'draft';
  const current = draft.currentRevision;
  const canStartRevision = workflowV2 && !draft.pendingRevision
    && ['approved', 'rejected'].includes(current?.lifecycleState);
  const canWithdraw = workflowV2 && revision?.pendingSubmissionId
    && revision.pendingSubmissionSubmittedByUserId === currentUserId;
  // Spec draft 2 FR8: with self-approval on, the submitter decides their own.
  const canSelfApprove = canWithdraw && selfApprovalAllowed;
  const name = draft.canonicalName;

  async function saveEdit(event) {
    event?.preventDefault?.();
    const saved = await runAction(
      () => api.updateDraft(draft.id, working.id, {
        name: editForm.name.trim(),
        category: editForm.category.trim(),
        discipline: editForm.discipline.trim(),
        summary: editForm.summary.trim(),
        description: editForm.description.trim(),
        aliases: normalizeFacetValues(editForm.aliases),
        keywords: normalizeFacetValues(editForm.keywords),
      }),
      `${editForm.name.trim() || name} saved.`,
    );
    if (saved) setEditForm(null);
  }

  async function toggleHistory() {
    if (history) {
      setHistory(null);
      return;
    }
    setHistoryError('');
    try {
      const next = await api.fetchHistory(draft.id);
      setHistory(next.items || []);
    } catch (err) {
      setHistoryError(err.message || 'Review history unavailable.');
    }
  }

  function field(key, label, { multiline = false, required = false } = {}) {
    const id = `org-symbol-edit-${draft.id}-${key}`;
    return createElement('label', { htmlFor: id },
      label,
      createElement(multiline ? 'textarea' : 'input', {
        id,
        rows: multiline ? 2 : undefined,
        value: editForm[key],
        required,
        onChange: (event) => setEditForm((form) => ({ ...form, [key]: event.target.value })),
      }),
    );
  }

  return createElement(
    'li',
    { className: 'set-admin-item' },
    createElement(
      'div',
      null,
      createElement('strong', null, `${name} · ${symbolIdLabelOrState(draft)}`),
      createElement('p', { className: 'set-admin-muted' }, `Category: ${draft.category} · Discipline: ${draft.discipline}`),
      createElement('p', { className: 'set-admin-muted' }, workflowV2 ? `Status: ${lifecycleLabel(draft)}` : `Revision status: ${current?.lifecycleState || 'none'}`),
      working.pending
        ? createElement('p', { className: 'set-admin-muted' }, `New revision in progress: ${LIFECYCLE_LABELS[revision?.lifecycleState] || revision?.lifecycleState}. The published version stays in the library until it is approved.`)
        : null,
      revision?.pendingSubmissionId
        ? createElement('p', { className: 'set-admin-muted' }, `Awaiting organization review (submitted ${formatWhen(revision.pendingSubmissionSubmittedAt)}).`)
        : null,
      canSelfApprove
        ? createElement('p', { className: 'set-admin-muted' }, 'Your organization lets you approve your own submission; it is recorded as self-approved.')
        : null,
      workflowV2
        ? createElement('div', { className: 'set-admin-muted' },
          'Files: ',
          (revision?.assets || []).length === 0 ? 'none' : null,
          ...(revision?.assets || []).map((asset) => createElement('span', { key: asset.id, className: 'organization-symbol-asset' },
            asset.filename,
            isDraft
              ? createElement('button', {
                type: 'button',
                className: 'action-button compact',
                disabled: busy,
                onClick: () => runAction(() => api.removeAsset(draft.id, working.id, asset.id), `${asset.filename} removed.`),
                'aria-label': `Remove ${asset.filename} from ${name}`,
              }, 'Remove')
              : null,
          )),
        )
        : createElement('p', { className: 'set-admin-muted' }, `Assets: ${(revision?.assets || []).map((asset) => asset.filename).join(', ') || 'none'}`),
      createElement('p', { className: 'set-admin-muted' }, `Updated: ${formatWhen(draft.updatedAt)}`),
    ),
    editForm
      ? createElement('form', { className: 'set-admin-form', onSubmit: saveEdit, 'aria-label': `Edit ${name}` },
        field('name', 'Symbol name', { required: true }),
        field('category', 'Category', { required: true }),
        createElement('label', { htmlFor: `org-symbol-edit-${draft.id}-discipline` },
          'Discipline',
          createElement('select', {
            id: `org-symbol-edit-${draft.id}-discipline`,
            value: editForm.discipline,
            required: true,
            onChange: (event) => setEditForm((form) => ({ ...form, discipline: event.target.value })),
          },
            // Keep a stored value that is not a standard name selectable, so
            // saving other fields never silently changes the discipline.
            ...(CATALOG_DISCIPLINE_ORDER.includes(editForm.discipline) || !editForm.discipline
              ? []
              : [createElement('option', { key: editForm.discipline, value: editForm.discipline }, editForm.discipline)]),
            ...CATALOG_DISCIPLINE_ORDER.map((option) => createElement('option', { key: option, value: option }, option)),
          ),
        ),
        field('summary', 'Summary', { multiline: true, required: true }),
        field('description', 'Description (optional)', { multiline: true }),
        field('aliases', 'Aliases', { multiline: true }),
        field('keywords', 'Keywords', { multiline: true }),
        createElement('div', { className: 'set-admin-actions' },
          createElement('button', { type: 'submit', className: 'action-button compact primary', disabled: busy }, busy ? 'Saving…' : 'Save changes'),
          createElement('button', { type: 'button', className: 'action-button compact ghost', onClick: () => setEditForm(null) }, 'Cancel'),
        ),
      )
      : null,
    createElement(
      'div',
      { className: 'set-admin-actions' },
      isDraft
        ? createElement('label', { htmlFor: `asset-upload-${draft.id}` },
          'Attach asset',
          createElement('input', {
            id: `asset-upload-${draft.id}`,
            type: 'file',
            disabled: busy,
            onChange: (event) => {
              const file = event.target.files?.[0];
              event.target.value = '';
              if (file) onUploadAsset(file);
            },
            'aria-label': `Attach asset to ${name}`,
          }),
        )
        : null,
      isDraft && workflowV2 && !editForm
        ? createElement('button', {
          type: 'button',
          className: 'action-button compact',
          disabled: busy,
          onClick: () => setEditForm(editFormFor(draft, revision)),
          'aria-label': `Edit details of ${name}`,
        }, 'Edit details')
        : null,
      isDraft
        ? createElement('label', { htmlFor: `submit-rationale-${draft.id}` },
          'Submission rationale (optional)',
          createElement('input', {
            id: `submit-rationale-${draft.id}`,
            value: rationale,
            onChange: (event) => onRationaleChange(event.target.value),
          }),
        )
        : null,
      isDraft
        ? createElement('button', {
          type: 'button',
          className: 'action-button compact primary',
          disabled: busy,
          onClick: onSubmit,
          'aria-label': `Submit ${name} for organization review`,
        }, busy ? 'Working…' : 'Submit for review')
        : null,
      canSelfApprove
        ? createElement('button', {
          type: 'button',
          disabled: busy,
          className: 'action-button compact primary',
          onClick: () => runAction(
            () => api.decide(draft.id, revision.pendingSubmissionId, { decision: 'approved' }),
            `${name} approved and published to the organization library.`,
          ),
          'aria-label': `Approve your own submission of ${name}`,
        }, 'Approve')
        : null,
      canWithdraw
        ? createElement('button', {
          type: 'button',
          className: 'action-button compact',
          disabled: busy,
          onClick: () => runAction(() => api.withdrawSubmission(draft.id, revision.pendingSubmissionId), `${name} withdrawn from review.`),
          'aria-label': `Withdraw ${name} from review`,
        }, 'Withdraw')
        : null,
      canStartRevision
        ? createElement('button', {
          type: 'button',
          className: 'action-button compact',
          disabled: busy,
          onClick: () => runAction(() => api.startRevision(draft.id), `New revision of ${name} started.`),
          'aria-label': `Start a new revision of ${name}`,
        }, 'Start new revision')
        : null,
      workflowV2 && working.pending && isDraft && !revision?.pendingSubmissionId
        ? createElement('button', {
          type: 'button',
          className: 'action-button compact',
          disabled: busy,
          onClick: () => runAction(() => api.discardPendingRevision(draft.id), `New revision of ${name} discarded.`),
          'aria-label': `Discard the new revision of ${name}`,
        }, 'Discard new revision')
        : null,
      workflowV2
        ? createElement('button', {
          type: 'button',
          className: 'action-button compact ghost',
          onClick: toggleHistory,
          'aria-expanded': Boolean(history),
          'aria-label': `${history ? 'Hide' : 'Show'} review history of ${name}`,
        }, history ? 'Hide history' : 'Review history')
        : null,
    ),
    historyError ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, historyError) : null,
    history
      ? createElement('ol', { className: 'organization-symbol-history', 'aria-label': `Review history of ${name}` },
        history.length === 0 ? createElement('li', null, 'Not submitted yet.') : null,
        ...history.map((entry) => createElement('li', { key: entry.submissionId },
          createElement('strong', null, HISTORY_OUTCOME_LABELS[entry.outcome] || entry.outcome),
          ` · submitted by ${entry.submittedByName || 'unknown'} ${formatWhen(entry.submittedAt)}`,
          entry.closedAt && entry.outcome !== 'active'
            ? ` · ${entry.outcome === 'withdrawn' ? 'withdrawn' : `decided by ${entry.decidedByName || 'unknown'}`} ${formatWhen(entry.closedAt)}`
            : null,
          entry.submissionRationale ? createElement('p', { className: 'set-admin-muted' }, `Submitter: ${entry.submissionRationale}`) : null,
          entry.decisionRationale ? createElement('p', { className: 'set-admin-muted' }, `Reviewer: ${entry.decisionRationale}`) : null,
        )),
      )
      : null,
  );
}
