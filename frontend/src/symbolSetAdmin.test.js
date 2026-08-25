import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { OrganizationProjectsPanel, projectMutationPayload } from './OrganizationProjectsPanel.js';
import { OrganizationSymbolSetsPanel, symbolSetMutationPayload } from './OrganizationSymbolSetsPanel.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function projectsApi() {
  const calls = [];
  return {
    calls,
    listProjects: async ({ includeClosed }) => ({ items: [{ id: 'p-1', code: 'P-01', name: 'Plant', shortDescription: 'North works', status: includeClosed ? 'closed' : 'active' }], page: 1, pageSize: 50, total: 1 }),
    createProject: async (payload) => { calls.push(['create', payload]); return { id: 'p-2', ...payload, status: 'active' }; },
    updateProject: async (id, payload) => { calls.push(['update', id, payload]); return { id, code: 'P-01', ...payload }; },
  };
}

function setsApi() {
  const calls = [];
  return {
    calls,
    listSymbolSets: async () => ({ items: [{ id: 's-1', code: 'SET-01', name: 'Electrical', description: null, disciplines: ['Electrical'], useCases: [], status: 'active' }], page: 1, pageSize: 50, total: 1 }),
    createSymbolSet: async (payload) => { calls.push(['create', payload]); return { id: 's-2', ...payload, status: 'draft' }; },
    updateSymbolSet: async (id, payload) => { calls.push(['update', id, payload]); return { id, code: 'SET-01', ...payload }; },
    copySymbolSet: async (id, payload) => { calls.push(['copy', id, payload]); return { id: 's-3', ...payload, status: 'draft' }; },
    setOrganizationDefaultSymbolSet: async (id) => { calls.push(['default', id]); return { defaultSymbolSetId: id }; },
    clearOrganizationDefaultSymbolSet: async () => { calls.push(['clear-default']); },
  };
}

describe('admin payload normalization', () => {
  it('normalizes Project optional fields and validates the Unicode description limit', () => {
    assert.deepEqual(projectMutationPayload({ code: ' P-01 ', name: ' Plant ', shortDescription: ' North ', externalReference: ' ', metadata: '' }, true), {
      code: 'P-01', name: 'Plant', shortDescription: 'North', externalReference: null, metadata: {},
    });
    assert.throws(() => projectMutationPayload({ code: 'P', name: 'N', shortDescription: '😀'.repeat(51), metadata: '{}' }, true), /50 characters/);
  });

  it('normalizes and clears disciplines and use cases for create and edit', () => {
    assert.deepEqual(symbolSetMutationPayload({ code: ' SET-01 ', name: ' Electrical ', description: ' ', disciplines: ' Electrical, electrical ', useCases: '' }, true), {
      code: 'SET-01', name: 'Electrical', description: null, disciplines: ['Electrical'], useCases: [],
    });
    assert.deepEqual(symbolSetMutationPayload({ name: 'Changed', description: '', disciplines: '', useCases: ' Design\n design ' }, false), {
      name: 'Changed', description: null, disciplines: [], useCases: ['Design'],
    });
  });
});

describe('mounted Stage 4 administration panels', () => {
  it('shows loading then Project description and hides every Project mutation from non-admin users', async () => {
    const api = projectsApi();
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationProjectsPanel, { isAdmin: false, api, onContextChanged() {} })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /North works/);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Create Project' }).length, 0);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Edit Project P-01' }).length, 0);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Close Project P-01' }).length, 0);
    await act(async () => renderer.unmount());
  });

  it('provides admin-only create, edit and close Project actions with status feedback', async () => {
    const api = projectsApi();
    let refreshes = 0;
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationProjectsPanel, { isAdmin: true, api, onContextChanged() { refreshes += 1; } })); });
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Create Project' }));
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Edit Project P-01' }));
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Close Project P-01' }).props.onClick());
    assert.deepEqual(api.calls[0], ['update', 'p-1', { status: 'closed' }]);
    assert.equal(refreshes, 1);
    assert.match(JSON.stringify(renderer.toJSON()), /Project P-01 closed/);
    await act(async () => renderer.unmount());
  });

  it('provides admin-only Symbol Set create, edit, archive, default and copy actions', async () => {
    const api = setsApi();
    let refreshes = 0;
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolSetsPanel, { isAdmin: true, api, onContextChanged() { refreshes += 1; } })); });
    for (const label of ['Create Symbol Set', 'Edit Symbol Set SET-01', 'Archive Symbol Set SET-01', 'Set SET-01 as Organization default', 'Copy Symbol Set SET-01']) {
      assert.ok(renderer.root.findByProps({ 'aria-label': label }));
    }
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Set SET-01 as Organization default' }).props.onClick());
    assert.deepEqual(api.calls[0], ['default', 's-1']);
    assert.equal(refreshes, 1);
    await act(async () => renderer.unmount());
  });

  it('renders empty, error, retry and archived refresh states accessibly', async () => {
    let attempt = 0;
    const api = {
      ...projectsApi(),
      listProjects: async ({ includeClosed }) => {
        attempt += 1;
        if (attempt === 1) throw new Error('Projects unavailable.');
        return { items: includeClosed ? [{ id: 'p-closed', code: 'OLD', name: 'Old', shortDescription: null, status: 'closed' }] : [], page: 1, pageSize: 50, total: includeClosed ? 1 : 0 };
      },
    };
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationProjectsPanel, { isAdmin: true, api, onContextChanged() {} })); });
    assert.match(renderer.root.findByProps({ role: 'alert' }).children.join(''), /Projects unavailable/);
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Retry Projects' }).props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()), /No active Projects/);
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Show closed Projects' }).props.onChange({ target: { checked: true } }));
    assert.match(JSON.stringify(renderer.toJSON()), /OLD/);
    await act(async () => renderer.unmount());
  });
});
