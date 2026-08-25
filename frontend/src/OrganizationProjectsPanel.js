import { createElement, useCallback, useEffect, useMemo, useState } from 'react';

import {
  codePointCount,
  projectMutationPayload as normalizeProjectMutationPayload,
  validateProjectShortDescription,
} from './projectContext.js';
import {
  createOrganizationProject,
  listOrganizationProjects,
  updateOrganizationProject,
} from './api.js';

export function projectMutationPayload(input, isCreate) {
  return normalizeProjectMutationPayload(input, isCreate);
}

const DEFAULT_API = {
  listProjects: listOrganizationProjects,
  createProject: createOrganizationProject,
  updateProject: updateOrganizationProject,
};

function emptyForm() {
  return {
    code: '',
    name: '',
    shortDescription: '',
    externalReference: '',
    metadata: '',
  };
}

function StatusMessage({ status }) {
  if (!status?.message) return null;
  return createElement('p', { role: status.mode === 'error' ? 'alert' : 'status', className: `project-admin-status ${status.mode || 'info'}` }, status.message);
}

export function OrganizationProjectsPanel({ isAdmin, api = DEFAULT_API, onContextChanged = null }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [includeClosed, setIncludeClosed] = useState(false);
  const [projects, setProjects] = useState({ items: [], page: 1, pageSize: 50, total: 0 });
  const [editingProjectId, setEditingProjectId] = useState('');
  const [form, setForm] = useState(emptyForm());
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next = await api.listProjects({ page: 1, pageSize: 50, includeClosed });
      setProjects(next || { items: [], page: 1, pageSize: 50, total: 0 });
    } catch (err) {
      setError(err.message || 'Projects unavailable.');
    } finally {
      setLoading(false);
    }
  }, [api, includeClosed]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const activeProjects = useMemo(
    () => projects.items.filter((project) => project.status === 'active'),
    [projects.items],
  );

  function resetForm() {
    setEditingProjectId('');
    setForm(emptyForm());
  }

  function openEdit(project) {
    setEditingProjectId(project.id);
    setForm({
      code: project.code,
      name: project.name,
      shortDescription: project.shortDescription || '',
      externalReference: project.externalReference || '',
      metadata: JSON.stringify(project.metadata || {}, null, 2),
    });
  }

  async function saveProject(event) {
    event?.preventDefault?.();
    setSaving(true);
    setStatus({ mode: '', message: '' });
    setError('');
    try {
      validateProjectShortDescription(form.shortDescription);
      const payload = projectMutationPayload(form, !editingProjectId);
      if (editingProjectId) {
        await api.updateProject(editingProjectId, payload);
        setStatus({ mode: 'success', message: 'Project updated.' });
      } else {
        await api.createProject(payload);
        setStatus({ mode: 'success', message: 'Project created.' });
      }
      resetForm();
      await refresh();
      if (typeof onContextChanged === 'function') onContextChanged();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Project save failed.' });
    } finally {
      setSaving(false);
    }
  }

  async function closeProject(project) {
    setSaving(true);
    setError('');
    setStatus({ mode: '', message: '' });
    try {
      await api.updateProject(project.id, { status: 'closed' });
      await refresh();
      setStatus({ mode: 'success', message: `Project ${project.code} closed.` });
      if (typeof onContextChanged === 'function') onContextChanged();
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Project close failed.' });
    } finally {
      setSaving(false);
    }
  }

  const shortDescriptionCount = codePointCount(form.shortDescription || '');

  return createElement(
    'section',
    { className: 'organization-projects-panel', 'aria-labelledby': 'organization-projects-heading' },
    createElement('h2', { id: 'organization-projects-heading' }, 'Projects'),
    createElement('label', { className: 'project-admin-toggle' },
      createElement('input', {
        type: 'checkbox',
        checked: includeClosed,
        onChange: (event) => setIncludeClosed(Boolean(event.target.checked)),
        'aria-label': 'Show closed Projects',
      }),
      'Show closed Projects',
    ),
    loading ? createElement('p', { role: 'status' }, 'Loading Projects…') : null,
    error ? createElement('p', { role: 'alert', className: 'project-admin-status error' }, error) : null,
    !loading && !error && activeProjects.length === 0 && !includeClosed
      ? createElement('p', { role: 'status' }, 'No active Projects.')
      : null,
    StatusMessage({ status }),
    createElement('ul', { className: 'project-admin-list' },
      projects.items.map((project) => createElement('li', { key: project.id, className: 'project-admin-item' },
        createElement('div', null,
          createElement('strong', null, `${project.code} · ${project.name}`),
          createElement('p', { className: 'project-admin-muted' }, project.shortDescription || 'No description.'),
          createElement('p', { className: 'project-admin-muted' }, `Status: ${project.status}`),
        ),
        isAdmin
          ? createElement('div', { className: 'project-admin-actions' },
            createElement('button', {
              type: 'button',
              onClick: () => openEdit(project),
              'aria-label': `Edit Project ${project.code}`,
            }, 'Edit'),
            project.status === 'active'
              ? createElement('button', {
                type: 'button',
                disabled: saving,
                onClick: () => { closeProject(project); },
                'aria-label': `Close Project ${project.code}`,
              }, 'Close')
              : null,
          )
          : null,
      )),
    ),
    error
      ? createElement('button', {
        type: 'button',
        onClick: () => { refresh(); },
        'aria-label': 'Retry Projects',
      }, 'Retry')
      : null,
    isAdmin
      ? createElement('form', { className: 'project-admin-form', onSubmit: saveProject },
        createElement('h3', null, editingProjectId ? 'Edit Project' : 'Create Project'),
        createElement('label', { htmlFor: 'project-admin-code' },
          'Project code',
          createElement('input', {
            id: 'project-admin-code',
            value: form.code,
            disabled: Boolean(editingProjectId),
            onChange: (event) => setForm((current) => ({ ...current, code: event.target.value })),
            required: !editingProjectId,
          }),
        ),
        createElement('label', { htmlFor: 'project-admin-name' },
          'Project name',
          createElement('input', {
            id: 'project-admin-name',
            value: form.name,
            onChange: (event) => setForm((current) => ({ ...current, name: event.target.value })),
            required: true,
          }),
        ),
        createElement('label', { htmlFor: 'project-admin-short-description' },
          'Project description',
          createElement('input', {
            id: 'project-admin-short-description',
            value: form.shortDescription,
            onChange: (event) => setForm((current) => ({ ...current, shortDescription: event.target.value })),
            'aria-describedby': 'project-admin-short-description-count',
          }),
        ),
        createElement('p', { id: 'project-admin-short-description-count', role: 'status', className: 'project-admin-muted' }, `${shortDescriptionCount}/50 code points`),
        createElement('label', { htmlFor: 'project-admin-external-reference' },
          'External reference',
          createElement('input', {
            id: 'project-admin-external-reference',
            value: form.externalReference,
            onChange: (event) => setForm((current) => ({ ...current, externalReference: event.target.value })),
          }),
        ),
        createElement('label', { htmlFor: 'project-admin-metadata' },
          'Metadata (JSON)',
          createElement('textarea', {
            id: 'project-admin-metadata',
            rows: 4,
            value: form.metadata,
            onChange: (event) => setForm((current) => ({ ...current, metadata: event.target.value })),
          }),
        ),
        createElement('div', { className: 'project-admin-actions' },
          createElement('button', { type: 'submit', disabled: saving, 'aria-label': 'Create Project' }, saving ? 'Saving…' : editingProjectId ? 'Save Project' : 'Create Project'),
          editingProjectId
            ? createElement('button', {
              type: 'button',
              onClick: resetForm,
            }, 'Cancel')
            : null,
        ),
      )
      : null,
  );
}
