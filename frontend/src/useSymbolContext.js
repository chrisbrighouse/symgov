import { useCallback, useEffect, useRef, useState } from 'react';

import { canMountProjectContext, contextStatusMessage } from './projectContext.js';
import {
  clearActiveSymbolSetSelection,
  clearProjectSelection,
  fetchSymbolContext,
  listOrganizationProjects,
  listOrganizationSymbolSets,
  selectActiveSymbolSet,
  selectProjectContext,
} from './api.js';

export const DEFAULT_SYMBOL_CONTEXT_API = {
  getContext: fetchSymbolContext,
  listProjects: listOrganizationProjects,
  listSymbolSets: listOrganizationSymbolSets,
  selectProject: selectProjectContext,
  clearProject: clearProjectSelection,
  selectActiveSet: selectActiveSymbolSet,
  clearActiveSet: clearActiveSymbolSetSelection,
};

const EMPTY_CONTEXT = { selectedProject: null, activeSet: null, reason: 'none' };
const PROJECTS_PAGE_SIZE = 25;
const SETS_PAGE_SIZE = 200;

function emptyProjects(page = 1) {
  return { items: [], page, pageSize: PROJECTS_PAGE_SIZE, total: 0 };
}

function emptySets() {
  return { items: [], page: 1, pageSize: SETS_PAGE_SIZE, total: 0 };
}

export function totalPages(total, pageSize) {
  return Math.max(1, Math.ceil(Number(total || 0) / Math.max(1, Number(pageSize || 1))));
}

// The signed-in session's Project and Symbol Set selection, which the server
// stores. Shared by the pickers (`ProjectContextBar`) and anything that shows
// what the selection contains, such as the Catalog's Set tab.
//
// `contextVersion` goes up each time a fresh context from the server is
// applied: after a selection, a refresh, or a change of `refreshToken`. A
// consumer reloads when it changes, so it follows the pickers without a
// Refresh button. Only the most recently started refresh is applied, so a
// slow reply can never overwrite a newer one. Paging through Projects is not
// a context change and leaves the version alone.
export function useSymbolContext({
  auth,
  api = DEFAULT_SYMBOL_CONTEXT_API,
  refreshToken = 0,
  onContextChanged,
} = {}) {
  const canMount = canMountProjectContext(auth);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [status, setStatus] = useState('');
  const [stale, setStale] = useState(false);
  const [context, setContext] = useState(EMPTY_CONTEXT);
  const [contextVersion, setContextVersion] = useState(0);
  const [projectsPage, setProjectsPage] = useState(1);
  const [projects, setProjects] = useState(() => emptyProjects());
  const [sets, setSets] = useState(emptySets);

  const refreshSequenceRef = useRef(0);
  const setsSequenceRef = useRef(0);
  const projectsPageRef = useRef(projectsPage);
  projectsPageRef.current = projectsPage;
  const onContextChangedRef = useRef(onContextChanged);
  onContextChangedRef.current = onContextChanged;

  const activeProjectId = context?.selectedProject?.id || '';
  const activeSetCode = context?.activeSet?.code || '';

  const refresh = useCallback(async (message = '') => {
    if (!canMount) return;
    const sequence = refreshSequenceRef.current + 1;
    refreshSequenceRef.current = sequence;
    const isLatest = () => sequence === refreshSequenceRef.current;
    setBusy(true);
    setError('');
    try {
      const nextContext = (await api.getContext()) || EMPTY_CONTEXT;
      const nextProjectId = nextContext?.selectedProject?.id || '';
      const page = projectsPageRef.current;
      const [projectResult, setResult] = await Promise.all([
        api.listProjects({ page, pageSize: PROJECTS_PAGE_SIZE, includeClosed: false }),
        nextProjectId
          ? api.listSymbolSets({ page: 1, pageSize: SETS_PAGE_SIZE, status: 'active', projectId: nextProjectId })
          : Promise.resolve(emptySets()),
      ]);
      if (!isLatest()) return;
      setProjects(projectResult || emptyProjects(page));
      setSets(setResult || emptySets());
      setContext(nextContext);
      setContextVersion((current) => current + 1);
      setStale(false);
      setStatus(message || contextStatusMessage(nextContext));
      onContextChangedRef.current?.(nextContext);
    } catch (err) {
      if (!isLatest()) return;
      setError(err.message || 'Context refresh failed.');
      setStale(true);
    } finally {
      if (isLatest()) setBusy(false);
    }
  }, [api, canMount]);

  useEffect(() => {
    if (!canMount) return;
    refresh();
  }, [canMount, refresh, refreshToken]);

  const loadProjectsPage = useCallback(async (page) => {
    if (!canMount) return;
    try {
      const data = await api.listProjects({ page, pageSize: PROJECTS_PAGE_SIZE, includeClosed: false });
      if (page !== projectsPageRef.current) return;
      setProjects(data || emptyProjects(page));
    } catch (err) {
      setError(err.message || 'Projects could not be loaded.');
      setStale(true);
    }
  }, [api, canMount]);

  const changeProjectsPage = useCallback((page) => {
    setProjectsPage(page);
    projectsPageRef.current = page;
    loadProjectsPage(page);
  }, [loadProjectsPage]);

  // Keeps the Symbol Set list in step when the Project changes by some route
  // other than `refresh`, such as an optimistic selection.
  useEffect(() => {
    if (!canMount) return;
    if (!activeProjectId) {
      setSets(emptySets());
      return;
    }
    const sequence = setsSequenceRef.current + 1;
    setsSequenceRef.current = sequence;
    api.listSymbolSets({ page: 1, pageSize: SETS_PAGE_SIZE, status: 'active', projectId: activeProjectId })
      .then((data) => {
        if (sequence === setsSequenceRef.current) setSets(data || emptySets());
      })
      .catch((err) => {
        if (sequence !== setsSequenceRef.current) return;
        setError(err.message || 'Symbol Sets could not be loaded.');
        setStale(true);
      });
  }, [activeProjectId, api, canMount]);

  useEffect(() => {
    if (!activeProjectId || !activeSetCode || !sets.items.length) return;
    if (!sets.items.some((setRow) => setRow.code === activeSetCode)) {
      setStatus('Active Symbol Set is no longer available for this Project. Context refreshed.');
    }
  }, [activeProjectId, activeSetCode, sets.items]);

  const selectProject = useCallback(async (projectId) => {
    setError('');
    try {
      if (!projectId) {
        await api.clearProject();
        await refresh('Project cleared.');
        return;
      }
      const selected = await api.selectProject(projectId);
      if (selected) {
        // Shown at once; the refresh below applies it as the new context.
        setContext(selected);
        setStatus(contextStatusMessage(selected));
      }
      await refresh('Project selected.');
    } catch (err) {
      setError(err.message || 'Project selection failed.');
    }
  }, [api, refresh]);

  const selectSet = useCallback(async (setCode) => {
    setError('');
    try {
      if (!setCode) {
        const cleared = await api.clearActiveSet();
        await refresh(contextStatusMessage(cleared, 'clear-set'));
        return;
      }
      const selected = await api.selectActiveSet(setCode);
      await refresh(contextStatusMessage(selected, 'set'));
    } catch (err) {
      setError(err.message || 'Symbol Set selection failed.');
    }
  }, [api, refresh]);

  const selectedProject = projects.items.find((project) => project.id === activeProjectId) || context.selectedProject;

  return {
    canMount,
    busy,
    error,
    status,
    stale,
    context,
    contextVersion,
    activeProjectId,
    activeSetCode,
    selectedProject,
    projects,
    projectsPage,
    projectPages: totalPages(projects.total, projects.pageSize),
    sets,
    refresh,
    selectProject,
    selectSet,
    setProjectsPage: changeProjectsPage,
  };
}
