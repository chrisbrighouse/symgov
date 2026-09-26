import { createElement } from 'react';

import { DEFAULT_SYMBOL_CONTEXT_API, useSymbolContext } from './useSymbolContext.js';

// The Project and Symbol Set pickers, drawn from a `useSymbolContext` state.
// Split from the hook so a page that also shows the selection's contents
// (the Catalog's Set tab) can own the state and still use the same pickers.
export function ProjectContextBarView({ state, headingId = 'project-context-bar-heading' }) {
  if (!state?.canMount) return null;
  const {
    busy,
    error,
    status,
    stale,
    activeProjectId,
    activeSetCode,
    selectedProject,
    projects,
    projectsPage,
    projectPages,
    sets,
  } = state;

  return createElement(
    'section',
    { className: 'project-context-bar', 'aria-labelledby': headingId },
    createElement('div', { className: 'project-context-title-row' },
      createElement('h2', { id: headingId }, 'Project and Symbol Set context'),
      createElement('button', {
        type: 'button',
        className: 'project-context-refresh',
        disabled: busy,
        onClick: () => state.refresh(),
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
          onChange: (event) => state.selectProject(String(event.target.value || '')),
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
          onChange: (event) => state.selectSet(String(event.target.value || '')),
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
        onClick: () => state.setProjectsPage(Math.max(1, projectsPage - 1)),
        disabled: busy || projectsPage <= 1,
        'aria-label': 'Previous Project page',
      }, 'Previous'),
      createElement('span', null, `Page ${projects.page || projectsPage} of ${projectPages}`),
      createElement('button', {
        type: 'button',
        onClick: () => state.setProjectsPage(Math.min(projectPages, projectsPage + 1)),
        disabled: busy || projectsPage >= projectPages,
        'aria-label': 'Next Project page',
      }, 'Next'),
    ),
  );
}

export function ProjectContextBar({ auth, api = DEFAULT_SYMBOL_CONTEXT_API, refreshToken = 0, onContextChanged }) {
  const state = useSymbolContext({ auth, api, refreshToken, onContextChanged });
  return createElement(ProjectContextBarView, { state });
}
