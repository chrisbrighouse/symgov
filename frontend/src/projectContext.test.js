import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { ProjectContextBar } from './ProjectContextBar.js';
import {
  canMountProjectContext,
  codePointCount,
  contextStatusMessage,
  normalizeFacetValues,
  validateProjectShortDescription,
} from './projectContext.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function eligibleAuth(overrides = {}) {
  return {
    user: {
      session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
      organization: { id: 'org-1', baseRole: 'user' },
      capabilities: { symbolSetsEnabled: true },
      ...overrides,
    },
  };
}

describe('Project context eligibility and normalization', () => {
  it('fails closed for every ineligible session shape', () => {
    assert.equal(canMountProjectContext(eligibleAuth()), true);
    for (const auth of [
      null,
      { user: null },
      eligibleAuth({ session: { mode: 'personal', purpose: 'application', activeOrganizationId: null } }),
      eligibleAuth({ session: { mode: 'organization', purpose: 'credential_change', activeOrganizationId: 'org-1' } }),
      eligibleAuth({ organization: null }),
      eligibleAuth({ organization: { id: 'org-2', baseRole: 'user' } }),
      eligibleAuth({ capabilities: { symbolSetsEnabled: false } }),
      eligibleAuth({ capabilities: {} }),
    ]) assert.equal(canMountProjectContext(auth), false);
  });

  it('counts Unicode code points and enforces the 50-character Project description boundary', () => {
    assert.equal(codePointCount('😀'.repeat(50)), 50);
    assert.equal(validateProjectShortDescription('a'.repeat(50)), 'a'.repeat(50));
    assert.equal(validateProjectShortDescription('😀'.repeat(50)), '😀'.repeat(50));
    assert.throws(() => validateProjectShortDescription('a'.repeat(51)), /50 characters/);
    assert.throws(() => validateProjectShortDescription('😀'.repeat(51)), /50 characters/);
  });

  it('normalizes disciplines and use cases and supports an explicit clear', () => {
    assert.deepEqual(normalizeFacetValues(' Electrical, electrical\n Safety '), ['Electrical', 'Safety']);
    assert.deepEqual(normalizeFacetValues('  '), []);
    assert.deepEqual(normalizeFacetValues([' Process ', 'process', 'Utilities']), ['Process', 'Utilities']);
  });

  it('describes exact Set selection and fallback-aware clearing responses', () => {
    assert.equal(contextStatusMessage({ activeSet: { code: 'SET-01' }, reason: 'explicit' }, 'set'), 'Symbol Set SET-01 selected.');
    assert.equal(contextStatusMessage({ activeSet: { code: 'DEFAULT' }, reason: 'project_default' }, 'clear-set'), 'Symbol Set preference cleared. Project default DEFAULT is active.');
    assert.equal(contextStatusMessage({ activeSet: null, reason: 'none' }, 'clear-set'), 'Symbol Set preference cleared. No Symbol Set is active.');
  });
});

describe('ProjectContextBar', () => {
  it('pages Projects, filters active Sets by Project, shows description, and accepts a zero-set Project', async () => {
    const calls = [];
    let currentContext = { selectedProject: null, activeSet: null, reason: 'none' };
    const api = {
      getContext: async () => currentContext,
      listProjects: async ({ page }) => ({
        items: [{ id: 'p-1', code: 'P-01', name: 'Plant upgrade', shortDescription: 'North works', status: 'active' }],
        page, pageSize: 25, total: 1,
      }),
      listSymbolSets: async ({ projectId, status }) => {
        calls.push({ projectId, status });
        return { items: [], page: 1, pageSize: 200, total: 0 };
      },
      selectProject: async (projectId) => {
        currentContext = { selectedProject: { id: projectId, code: 'P-01', name: 'Plant upgrade', shortDescription: 'North works', status: 'active' }, activeSet: null, reason: 'none' };
        return currentContext;
      },
      clearProject: async () => {},
      selectActiveSet: async () => { throw new Error('not used'); },
      clearActiveSet: async () => { throw new Error('not used'); },
    };
    let renderer;
    await act(async () => { renderer = create(createElement(ProjectContextBar, { auth: eligibleAuth(), api })); });
    const projectSelect = renderer.root.findByProps({ 'aria-label': 'Active Project' });
    await act(async () => projectSelect.props.onChange({ target: { value: 'p-1' } }));
    assert.ok(calls.some((entry) => entry.projectId === 'p-1' && entry.status === 'active'));
    assert.match(JSON.stringify(renderer.toJSON()), /North works/);
    assert.match(JSON.stringify(renderer.toJSON()), /No active Symbol Sets are available for this Project/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Active Symbol Set' }));
    await act(async () => renderer.unmount());
  });

  it('renders an alert and keeps stale context recoverable when refresh fails', async () => {
    let attempts = 0;
    const api = {
      getContext: async () => {
        attempts += 1;
        if (attempts === 1) return { selectedProject: { id: 'p-1', code: 'P-01', name: 'Plant', shortDescription: null, status: 'active' }, activeSet: null, reason: 'none' };
        throw new Error('Context refresh failed.');
      },
      listProjects: async () => ({ items: [{ id: 'p-1', code: 'P-01', name: 'Plant', shortDescription: null, status: 'active' }], page: 1, pageSize: 25, total: 1 }),
      listSymbolSets: async () => ({ items: [], page: 1, pageSize: 200, total: 0 }),
      selectProject: async () => { throw new Error('not used'); }, clearProject: async () => {}, selectActiveSet: async () => {}, clearActiveSet: async () => {},
    };
    let renderer;
    await act(async () => { renderer = create(createElement(ProjectContextBar, { auth: eligibleAuth(), api })); });
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Refresh Project and Symbol Set context' }).props.onClick());
    const alerts = renderer.root.findAllByProps({ role: 'alert' });
    assert.ok(alerts.some((node) => /Context refresh failed/.test(node.children.join(''))));
    assert.equal(renderer.root.findByProps({ 'aria-label': 'Active Project' }).props.value, 'p-1');
    await act(async () => renderer.unmount());
  });
});
