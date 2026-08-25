import { createElement, useCallback, useEffect, useMemo, useState } from 'react';

import {
  normalizeFacetValues,
  symbolSetMutationPayload as normalizeSymbolSetMutationPayload,
} from './projectContext.js';
import {
  clearOrganizationDefaultSymbolSet,
  copyOrganizationSymbolSet,
  createOrganizationSymbolSet,
  listOrganizationSymbolSets,
  setOrganizationDefaultSymbolSet,
  updateOrganizationSymbolSet,
} from './api.js';

export function symbolSetMutationPayload(input, isCreate) {
  return normalizeSymbolSetMutationPayload(input, isCreate);
}

const DEFAULT_API = {
  listSymbolSets: listOrganizationSymbolSets,
  createSymbolSet: createOrganizationSymbolSet,
  updateSymbolSet: updateOrganizationSymbolSet,
  copySymbolSet: copyOrganizationSymbolSet,
  setOrganizationDefaultSymbolSet,
  clearOrganizationDefaultSymbolSet,
};

function StatusMessage({ status }) {
  if (!status?.message) return null;
  return createElement('p', { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode || 'info'}` }, status.message);
}

function emptyForm() {
  return {
    code: '',
    name: '',
    description: '',
    disciplines: '',
    useCases: '',
  };
}

export function OrganizationSymbolSetsPanel({ isAdmin, api = DEFAULT_API, onContextChanged = null }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [sets, setSets] = useState({ items: [], page: 1, pageSize: 50, total: 0 });
  const [editingSetId, setEditingSetId] = useState('');
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next = await api.listSymbolSets({ page: 1, pageSize: 50 });
      setSets(next || { items: [], page: 1, pageSize: 50, total: 0 });
    } catch (err) {
      setError(err.message || 'Symbol Sets unavailable.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const activeSets = useMemo(
    () => sets.items.filter((setRow) => setRow.status === 'active'),
    [sets.items],
  );

  function resetForm() {
    setEditingSetId('');
    setForm(emptyForm());
  }

  function openEdit(setRow) {
    setEditingSetId(setRow.id);
    setForm({
      code: setRow.code,
      name: setRow.name,
      description: setRow.description || '',
      disciplines: (setRow.disciplines || []).join(', '),
      useCases: (setRow.useCases || []).join(', '),
    });
  }

  async function saveSet(event) {
    event?.preventDefault?.();
    setSaving(true);
    setStatus({ mode: '', message: '' });
    setError('');
    try {
      const payload = symbolSetMutationPayload(form, !editingSetId);
      if (editingSetId) {
        await api.updateSymbolSet(editingSetId, payload);
        setStatus({ mode: 'success', message: 'Symbol Set updated.' });
      } else {
        await api.createSymbolSet(payload);
        setStatus({ mode: 'success', message: 'Symbol Set created.' });
      }
      resetForm();
      await refresh();
      if (typeof onContextChanged === 'function') onContextChanged();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Symbol Set save failed.' });
    } finally {
      setSaving(false);
    }
  }

  async function archiveSet(setRow) {
    setSaving(true);
    setStatus({ mode: '', message: '' });
    try {
      await api.updateSymbolSet(setRow.id, { status: 'archived' });
      await refresh();
      setStatus({ mode: 'success', message: `Symbol Set ${setRow.code} archived.` });
      if (typeof onContextChanged === 'function') onContextChanged();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Symbol Set archive failed.' });
    } finally {
      setSaving(false);
    }
  }

  async function makeDefault(setRow) {
    setSaving(true);
    setStatus({ mode: '', message: '' });
    try {
      await api.setOrganizationDefaultSymbolSet(setRow.id);
      setStatus({ mode: 'success', message: `Organization default set to ${setRow.code}.` });
      if (typeof onContextChanged === 'function') onContextChanged();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Could not set Organization default.' });
    } finally {
      setSaving(false);
    }
  }

  async function copySet(setRow) {
    setSaving(true);
    setStatus({ mode: '', message: '' });
    try {
      await api.copySymbolSet(setRow.id, { code: `${setRow.code}-COPY`, name: `${setRow.name} Copy` });
      await refresh();
      setStatus({ mode: 'success', message: `Copied Symbol Set ${setRow.code}.` });
      if (typeof onContextChanged === 'function') onContextChanged();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Symbol Set copy failed.' });
    } finally {
      setSaving(false);
    }
  }

  async function clearDefault() {
    setSaving(true);
    setStatus({ mode: '', message: '' });
    try {
      await api.clearOrganizationDefaultSymbolSet();
      setStatus({ mode: 'success', message: 'Organization default Symbol Set cleared.' });
      if (typeof onContextChanged === 'function') onContextChanged();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Default clear failed.' });
    } finally {
      setSaving(false);
    }
  }

  const normalizedDisciplines = normalizeFacetValues(form.disciplines);
  const normalizedUseCases = normalizeFacetValues(form.useCases);

  return createElement(
    'section',
    { className: 'organization-symbol-sets-panel', 'aria-labelledby': 'organization-symbol-sets-heading' },
    createElement('h2', { id: 'organization-symbol-sets-heading' }, 'Symbol Sets'),
    loading ? createElement('p', { role: 'status' }, 'Loading Symbol Sets…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    !loading && !error && activeSets.length === 0
      ? createElement('p', { role: 'status' }, 'No active Symbol Sets.')
      : null,
    StatusMessage({ status }),
    createElement('ul', { className: 'set-admin-list' },
      sets.items.map((setRow) => createElement('li', { key: setRow.id, className: 'set-admin-item' },
        createElement('div', null,
          createElement('strong', null, `${setRow.code} · ${setRow.name}`),
          createElement('p', { className: 'set-admin-muted' }, setRow.description || 'No description.'),
          createElement('p', { className: 'set-admin-muted' }, `Disciplines: ${(setRow.disciplines || []).join(', ') || 'none'}`),
          createElement('p', { className: 'set-admin-muted' }, `Use cases: ${(setRow.useCases || []).join(', ') || 'none'}`),
          createElement('p', { className: 'set-admin-muted' }, `Status: ${setRow.status}`),
        ),
        isAdmin
          ? createElement('div', { className: 'set-admin-actions' },
            createElement('button', {
              type: 'button',
              onClick: () => openEdit(setRow),
              'aria-label': `Edit Symbol Set ${setRow.code}`,
            }, 'Edit'),
            createElement('button', {
              type: 'button',
              disabled: saving,
              onClick: () => { archiveSet(setRow); },
              'aria-label': `Archive Symbol Set ${setRow.code}`,
            }, 'Archive'),
            createElement('button', {
              type: 'button',
              disabled: saving,
              onClick: () => { makeDefault(setRow); },
              'aria-label': `Set ${setRow.code} as Organization default`,
            }, 'Set default'),
            createElement('button', {
              type: 'button',
              disabled: saving,
              onClick: () => { copySet(setRow); },
              'aria-label': `Copy Symbol Set ${setRow.code}`,
            }, 'Copy'),
          )
          : null,
      )),
    ),
    error
      ? createElement('button', {
        type: 'button',
        onClick: () => { refresh(); },
        'aria-label': 'Retry Symbol Sets',
      }, 'Retry')
      : null,
    isAdmin
      ? createElement('form', { className: 'set-admin-form', onSubmit: saveSet },
        createElement('h3', null, editingSetId ? 'Edit Symbol Set' : 'Create Symbol Set'),
        createElement('label', { htmlFor: 'set-admin-code' },
          'Symbol Set code',
          createElement('input', {
            id: 'set-admin-code',
            value: form.code,
            disabled: Boolean(editingSetId),
            onChange: (event) => setForm((current) => ({ ...current, code: event.target.value })),
            required: !editingSetId,
          }),
        ),
        createElement('label', { htmlFor: 'set-admin-name' },
          'Symbol Set name',
          createElement('input', {
            id: 'set-admin-name',
            value: form.name,
            onChange: (event) => setForm((current) => ({ ...current, name: event.target.value })),
            required: true,
          }),
        ),
        createElement('label', { htmlFor: 'set-admin-description' },
          'Description',
          createElement('input', {
            id: 'set-admin-description',
            value: form.description,
            onChange: (event) => setForm((current) => ({ ...current, description: event.target.value })),
          }),
        ),
        createElement('label', { htmlFor: 'set-admin-disciplines' },
          'Disciplines',
          createElement('textarea', {
            id: 'set-admin-disciplines',
            rows: 2,
            value: form.disciplines,
            onChange: (event) => setForm((current) => ({ ...current, disciplines: event.target.value })),
          }),
        ),
        createElement('p', { className: 'set-admin-muted', role: 'status' }, `Normalized disciplines: ${normalizedDisciplines.join(', ') || 'none'}`),
        createElement('label', { htmlFor: 'set-admin-use-cases' },
          'Use cases',
          createElement('textarea', {
            id: 'set-admin-use-cases',
            rows: 2,
            value: form.useCases,
            onChange: (event) => setForm((current) => ({ ...current, useCases: event.target.value })),
          }),
        ),
        createElement('p', { className: 'set-admin-muted', role: 'status' }, `Normalized use cases: ${normalizedUseCases.join(', ') || 'none'}`),
        createElement('div', { className: 'set-admin-actions' },
          createElement('button', { type: 'submit', disabled: saving, 'aria-label': 'Create Symbol Set' }, saving ? 'Saving…' : editingSetId ? 'Save Symbol Set' : 'Create Symbol Set'),
          createElement('button', { type: 'button', disabled: saving, onClick: clearDefault, 'aria-label': 'Clear Organization default Symbol Set' }, 'Clear default'),
          editingSetId
            ? createElement('button', { type: 'button', onClick: resetForm }, 'Cancel')
            : null,
        ),
      )
      : null,
  );
}
