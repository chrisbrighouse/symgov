import { createElement, useCallback, useEffect, useMemo, useState } from 'react';

import {
  canMountProjectContext,
  contextStatusMessage,
} from './projectContext.js';
import {
  clearActiveSymbolSetSelection,
  clearProjectSelection,
  fetchSymbolContext,
  listOrganizationProjects,
  listOrganizationSymbolSets,
  selectActiveSymbolSet,
  selectProjectContext,
} from './api.js';

const DEFAULT_API = {
  getContext: fetchSymbolContext,
  listProjects: listOrganizationProjects,
  listSymbolSets: listOrganizationSymbolSets,
  selectProject: selectProjectContext,
  clearProject: clearProjectSelection,
  selectActiveSet: selectActiveSymbolSet,
  clearActiveSet: clearActiveSymbolSetSelection,
};

function totalPages(total, pageSize) {
  return Math.max(1, Math.ceil(Number(total || 0) / Math.max(1, Number(pageSize || 1))));
}

export function ProjectContextBar({ auth, api = DEFAULT_API, refreshToken = 0 }) {
  const canMount = canMountProjectContext(auth);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [stale, setStale] = useState(false);
  const [context, setContext] = useState({ selectedProject: null, activeSet: null, reason: 'none' });
  const [projectsPage, setProjectsPage] = useState(1);
  const [projects, setProjects] = useState({ items: [], page: 1, pageSize: 25, total: 0 });
  const [sets, setSets] = useState({ items: [], page: 1, pageSize: 200, total: 0 });

  const activeProjectId = context?.selectedProject?.id || '';
  const activeSetCode = context?.activeSet?.code || '';

  const loadContext = useCallback(async (message = '') => {
    if (!canMount) return;
    setBusy(true);
    setError('');
    try {
      const nextContext = await api.getContext();
      setContext(nextContext || { selectedProject: null, activeSet: null, reason: 'none' });
      setStale(false);
      if (message) setStatus(message);
      else setStatus(contextStatusMessage(nextContext));
    } catch (err) {
      setError(err.message || 'Context could not be refreshed.');
      setStale(true);
    } finally {
      setBusy(false);
    }
  }, [api, canMount]);

  const loadProjects = useCallback(async (page = 1) => {
    if (!canMount) return;
    const data = await api.listProjects({ page, pageSize: 25, includeClosed: false });
    setProjects(data || { items: [], page, pageSize: 25, total: 0 });
    setProjectsPage(page);
  }, [api, canMount]);

  const loadSets = useCallback(async (projectId) => {
    if (!canMount || !projectId) {
      setSets({ items: [], page: 1, pageSize: 200, total: 0 });
      return;
    }
    const data = await api.listSymbolSets({ page: 1, pageSize: 200, status: 'active', projectId });
    setSets(data || { items: [], page: 1, pageSize: 200, total: 0 });
  }, [api, canMount]);

  const refreshAll = useCallback(async (message = '') => {
    if (!canMount) return;
    setBusy(true);
    setError('');
    try {
      const nextContext = await api.getContext();
      const nextProjectId = nextContext?.selectedProject?.id || '';
      const [projectResult, setResult] = await Promise.all([
        api.listProjects({ page: projectsPage, pageSize: 25, includeClosed: false }),
        nextProjectId
          ? api.listSymbolSets({ page: 1, pageSize: 200, status: 'active', projectId: nextProjectId })
          : Promise.resolve({ items: [], page: 1, pageSize: 200, total: 0 }),
      ]);
      setContext(nextContext || { selectedProject: null, activeSet: null, reason: 'none' });
      setProjects(projectResult || { items: [], page: projectsPage, pageSize: 25, total: 0 });
      setSets(setResult || { items: [], page: 1, pageSize: 200, total: 0 });
      setStale(false);
      setStatus(message || contextStatusMessage(nextContext));
    } catch (err) {
      setError(err.message || 'Context refresh failed.');
      setStale(true);
    } finally {
      setBusy(false);
    }
  }, [api, canMount, projectsPage]);

  useEffect(() => {
    if (!canMount) return;
    refreshAll();
  }, [canMount, refreshAll, refreshToken]);

  useEffect(() => {
    if (!canMount) return;
    loadProjects(projectsPage).catch((err) => {
      setError(err.message || 'Projects could not be loaded.');
      setStale(true);
    });
  }, [canMount, loadProjects, projectsPage]);

  useEffect(() => {
    if (!canMount) return;
    loadSets(activeProjectId).catch((err) => {
      setError(err.message || 'Symbol Sets could not be loaded.');
      setStale(true);
    });
  }, [activeProjectId, canMount, loadSets]);

  const selectedProject = useMemo(
    () => projects.items.find((project) => project.id === activeProjectId) || context.selectedProject,
    [projects.items, activeProjectId, context.selectedProject],
  );

  useEffect(() => {
    if (!activeProjectId || !activeSetCode || !sets.items.length) return;
    const stillAvailable = sets.items.some((setRow) => setRow.code === activeSetCode);
    if (!stillAvailable) {
      setStatus('Active Symbol Set is no longer available for this Project. Context refreshed.');
    }
  }, [activeProjectId, activeSetCode, sets.items]);

  if (!canMount) return null;

  async function handleProjectChange(event) {
    const projectId = String(event.target.value || '');
    setError('');
    if (!projectId) {
      await api.clearProject();
      await refreshAll('Project cleared.');
      return;
    }
    const selectedContext = await api.selectProject(projectId);
    if (selectedContext) {
      setContext(selectedContext);
      setStatus(contextStatusMessage(selectedContext));
    }
    await refreshAll('Project selected.');
  }

  async function handleSetChange(event) {
    const setCode = String(event.target.value || '');
    setError('');
    if (!setCode) {
      const cleared = await api.clearActiveSet();
      await refreshAll(contextStatusMessage(cleared, 'clear-set'));
      return;
    }
    const selected = await api.selectActiveSet(setCode);
    await refreshAll(contextStatusMessage(selected, 'set'));
  }

  const pages = totalPages(projects.total, projects.pageSize);

  return createElement(
    'section',
    { className: 'project-context-bar', 'aria-labelledby': 'project-context-bar-heading' },
    createElement('div', { className: 'project-context-title-row' },
      createElement('h2', { id: 'project-context-bar-heading' }, 'Project and Symbol Set context'),
      createElement('button', {
        type: 'button',
        className: 'project-context-refresh',
        disabled: busy,
        onClick: () => loadContext().then(() => loadProjects(projectsPage)).then(() => loadSets(activeProjectId)).catch((err) => {
          setError(err.message || 'Context refresh failed.');
          setStale(true);
        }),
        'aria-label': 'Refresh Project and Symbol Set context',
      }, busy ? 'Refreshing…' : 'Refresh'),
    ),
    error
      ? createElement('p', { role: 'alert', className: 'project-context-alert' }, error)
      : null,
    status
      ? createElement('p', { role: stale ? 'alert' : 'status', className: stale ? 'project-context-alert' : 'project-context-status' }, status)
      : null,
    createElement('div', { className: 'project-context-controls' },
      createElement('label', { htmlFor: 'project-context-project-select' },
        'Project',
        createElement('select', {
          id: 'project-context-project-select',
          'aria-label': 'Active Project',
          value: activeProjectId,
          onChange: (event) => { handleProjectChange(event).catch((err) => setError(err.message || 'Project selection failed.')); },
          disabled: busy,
        },
        createElement('option', { value: '' }, 'Select a Project'),
        projects.items.map((project) => createElement('option', { key: project.id, value: project.id }, `${project.code} · ${project.name}`)),
        ),
      ),
      createElement('label', { htmlFor: 'project-context-set-select' },
        'Symbol Set',
        createElement('select', {
          id: 'project-context-set-select',
          'aria-label': 'Active Symbol Set',
          value: activeSetCode,
          onChange: (event) => { handleSetChange(event).catch((err) => setError(err.message || 'Symbol Set selection failed.')); },
          disabled: busy || !activeProjectId,
        },
        createElement('option', { value: '' }, activeProjectId ? 'No Symbol Set' : 'Select a Project first'),
        sets.items.map((setRow) => createElement('option', { key: setRow.id, value: setRow.code }, `${setRow.code} · ${setRow.name}`)),
        ),
      ),
    ),
    selectedProject
      ? createElement('p', { className: 'project-context-project-description' },
        selectedProject.shortDescription
          ? `Project description: ${selectedProject.shortDescription}`
          : 'Project description: none.',
      )
      : null,
    activeProjectId && sets.total === 0
      ? createElement('p', { role: 'status', className: 'project-context-status' }, 'No active Symbol Sets are available for this Project.')
      : null,
    createElement('div', { className: 'project-context-pagination', 'aria-label': 'Project list pagination' },
      createElement('button', {
        type: 'button',
        onClick: () => setProjectsPage((current) => Math.max(1, current - 1)),
        disabled: busy || projectsPage <= 1,
        'aria-label': 'Previous Project page',
      }, 'Previous'),
      createElement('span', null, `Page ${projects.page || projectsPage} of ${pages}`),
      createElement('button', {
        type: 'button',
        onClick: () => setProjectsPage((current) => Math.min(pages, current + 1)),
        disabled: busy || projectsPage >= pages,
        'aria-label': 'Next Project page',
      }, 'Next'),
    ),
  );
}
