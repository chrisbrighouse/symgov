import { createElement, useCallback, useEffect, useState } from 'react';

import {
  attachOrganizationSymbolAsset,
  createOrganizationSymbolDraft,
  listOrganizationSymbolDrafts,
  submitOrganizationSymbolDraftForReview,
} from './api.js';
import { normalizeFacetValues } from './projectContext.js';

const DEFAULT_API = {
  listDrafts: listOrganizationSymbolDrafts,
  createDraft: createOrganizationSymbolDraft,
  attachAsset: attachOrganizationSymbolAsset,
  submitForReview: submitOrganizationSymbolDraftForReview,
};

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

export function OrganizationSymbolDraftsPanel({ canCreate = false, api = DEFAULT_API }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [drafts, setDrafts] = useState([]);
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);
  const [busyDraftId, setBusyDraftId] = useState('');
  const [rationaleByDraftId, setRationaleByDraftId] = useState({});

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

  async function uploadAsset(draft, file) {
    if (!file || !draft.currentRevisionId) return;
    setBusyDraftId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      const contentBase64 = await readFileAsBase64(file);
      await api.attachAsset(draft.id, draft.currentRevisionId, {
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
    if (!draft.currentRevisionId) return;
    setBusyDraftId(draft.id);
    setStatus({ mode: '', message: '' });
    try {
      const rationale = (rationaleByDraftId[draft.id] || '').trim();
      await api.submitForReview(draft.id, draft.currentRevisionId, rationale ? { rationale } : {});
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
      drafts.map((draft) => {
        const revision = draft.currentRevision;
        const canAct = draft.currentRevisionId && revision?.lifecycleState === 'draft';
        return createElement(
          'li',
          { key: draft.id, className: 'set-admin-item' },
          createElement(
            'div',
            null,
            createElement('strong', null, `${draft.canonicalName} · ${draft.slug}`),
            createElement('p', { className: 'set-admin-muted' }, `Category: ${draft.category} · Discipline: ${draft.discipline}`),
            createElement('p', { className: 'set-admin-muted' }, `Revision status: ${revision?.lifecycleState || 'none'}`),
            revision?.pendingSubmissionId
              ? createElement('p', { className: 'set-admin-muted' }, `Awaiting organization review (submitted ${new Date(revision.pendingSubmissionSubmittedAt).toLocaleString()}).`)
              : null,
            createElement('p', { className: 'set-admin-muted' }, `Assets: ${(revision?.assets || []).map((asset) => asset.filename).join(', ') || 'none'}`),
            createElement('p', { className: 'set-admin-muted' }, `Updated: ${new Date(draft.updatedAt).toLocaleString()}`),
          ),
          canAct
            ? createElement(
              'div',
              { className: 'set-admin-actions' },
              createElement('label', { htmlFor: `asset-upload-${draft.id}` },
                'Attach asset',
                createElement('input', {
                  id: `asset-upload-${draft.id}`,
                  type: 'file',
                  disabled: busyDraftId === draft.id,
                  onChange: (event) => {
                    const file = event.target.files?.[0];
                    event.target.value = '';
                    if (file) uploadAsset(draft, file);
                  },
                  'aria-label': `Attach asset to ${draft.canonicalName}`,
                }),
              ),
              createElement('label', { htmlFor: `submit-rationale-${draft.id}` },
                'Submission rationale (optional)',
                createElement('input', {
                  id: `submit-rationale-${draft.id}`,
                  value: rationaleByDraftId[draft.id] || '',
                  onChange: (event) => setRationaleByDraftId((current) => ({ ...current, [draft.id]: event.target.value })),
                }),
              ),
              createElement('button', {
                type: 'button',
                disabled: busyDraftId === draft.id,
                onClick: () => submitForReview(draft),
                'aria-label': `Submit ${draft.canonicalName} for organization review`,
              }, busyDraftId === draft.id ? 'Working…' : 'Submit for review'),
            )
            : null,
        );
      }),
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
          createElement('input', {
            id: 'org-symbol-draft-discipline',
            value: form.discipline,
            onChange: (event) => setForm((current) => ({ ...current, discipline: event.target.value })),
            required: true,
          }),
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
