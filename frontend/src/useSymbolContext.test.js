import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { useSymbolContext } from './useSymbolContext.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function eligibleAuth() {
  return {
    user: {
      session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
      organization: { id: 'org-1', baseRole: 'user' },
      capabilities: { symbolSetsEnabled: true },
    },
  };
}

const PROJECT = { id: 'p-1', code: 'NORTH', name: 'North Terminal', shortDescription: null, status: 'active' };
const SETS = [
  { id: 's-1', code: 'SET-PID', name: 'P&ID core' },
  { id: 's-2', code: 'SET-ELEC', name: 'Electrical' },
];

function deferred() {
  let resolve;
  const promise = new Promise((done) => { resolve = done; });
  return { promise, resolve };
}

// A consumer that records every state the hook hands it, the way the
// Catalog's Set tab will read it.
function Probe({ auth, api, refreshToken = 0, onContextChanged, seen }) {
  const state = useSymbolContext({ auth, api, refreshToken, onContextChanged });
  seen.current = state;
  return null;
}

function fakeApi(overrides = {}) {
  let context = { selectedProject: PROJECT, activeSet: SETS[0], reason: 'project_default' };
  const calls = { getContext: 0, listProjects: [] };
  const api = {
    calls,
    setContext: (next) => { context = next; },
    getContext: async () => { calls.getContext += 1; return context; },
    listProjects: async ({ page }) => {
      calls.listProjects.push(page);
      return { items: [PROJECT], page, pageSize: 25, total: 60 };
    },
    listSymbolSets: async () => ({ items: SETS, page: 1, pageSize: 200, total: SETS.length }),
    selectProject: async () => context,
    clearProject: async () => {},
    selectActiveSet: async (code) => {
      context = { ...context, activeSet: SETS.find((row) => row.code === code), reason: 'explicit' };
      return context;
    },
    clearActiveSet: async () => context,
    ...overrides,
  };
  return api;
}

describe('useSymbolContext', () => {
  it('bumps the context version and tells the consumer when the Symbol Set changes', async () => {
    const api = fakeApi();
    const seen = { current: null };
    const reported = [];
    let renderer;
    await act(async () => {
      renderer = create(createElement(Probe, { auth: eligibleAuth(), api, seen, onContextChanged: (next) => reported.push(next) }));
    });
    const afterLoad = seen.current.contextVersion;
    assert.ok(afterLoad >= 1);
    assert.equal(seen.current.activeSetCode, 'SET-PID');

    await act(async () => seen.current.selectSet('SET-ELEC'));
    assert.equal(seen.current.activeSetCode, 'SET-ELEC');
    assert.ok(seen.current.contextVersion > afterLoad);
    assert.equal(reported.at(-1).activeSet.code, 'SET-ELEC');
    assert.equal(seen.current.status, 'Symbol Set SET-ELEC selected.');
    await act(async () => renderer.unmount());
  });

  it('never lets a slow earlier refresh overwrite a newer one', async () => {
    const slow = deferred();
    let call = 0;
    const api = fakeApi();
    const newer = { selectedProject: PROJECT, activeSet: SETS[1], reason: 'explicit' };
    api.getContext = async () => {
      call += 1;
      if (call === 1) {
        await slow.promise;
        return { selectedProject: PROJECT, activeSet: SETS[0], reason: 'project_default' };
      }
      return newer;
    };
    const seen = { current: null };
    let renderer;
    await act(async () => {
      renderer = create(createElement(Probe, { auth: eligibleAuth(), api, seen }));
    });
    // The first (mount) refresh is still waiting; a second one finishes first.
    await act(async () => seen.current.refresh());
    assert.equal(seen.current.activeSetCode, 'SET-ELEC');
    const versionAfterNewer = seen.current.contextVersion;

    await act(async () => { slow.resolve(); await slow.promise; });
    assert.equal(seen.current.activeSetCode, 'SET-ELEC');
    assert.equal(seen.current.contextVersion, versionAfterNewer);
    assert.equal(seen.current.busy, false);
    await act(async () => renderer.unmount());
  });

  it('pages Projects without counting it as a context change', async () => {
    const api = fakeApi();
    const seen = { current: null };
    let renderer;
    await act(async () => {
      renderer = create(createElement(Probe, { auth: eligibleAuth(), api, seen }));
    });
    const version = seen.current.contextVersion;
    const contextCalls = api.calls.getContext;
    assert.equal(seen.current.projectPages, 3);

    await act(async () => seen.current.setProjectsPage(2));
    assert.equal(seen.current.projectsPage, 2);
    assert.equal(api.calls.listProjects.at(-1), 2);
    assert.equal(seen.current.contextVersion, version);
    assert.equal(api.calls.getContext, contextCalls);
    await act(async () => renderer.unmount());
  });

  it('refreshes again when the refresh token changes', async () => {
    const api = fakeApi();
    const seen = { current: null };
    let renderer;
    await act(async () => {
      renderer = create(createElement(Probe, { auth: eligibleAuth(), api, seen, refreshToken: 0 }));
    });
    const version = seen.current.contextVersion;
    api.setContext({ selectedProject: PROJECT, activeSet: null, reason: 'none' });
    await act(async () => {
      renderer.update(createElement(Probe, { auth: eligibleAuth(), api, seen, refreshToken: 1 }));
    });
    assert.ok(seen.current.contextVersion > version);
    assert.equal(seen.current.activeSetCode, '');
    await act(async () => renderer.unmount());
  });

  it('does nothing for a session that cannot use Symbol Sets', async () => {
    const api = fakeApi();
    const seen = { current: null };
    let renderer;
    await act(async () => {
      renderer = create(createElement(Probe, { auth: { user: null }, api, seen }));
    });
    assert.equal(seen.current.canMount, false);
    assert.equal(api.calls.getContext, 0);
    assert.equal(seen.current.contextVersion, 0);
    await act(async () => renderer.unmount());
  });
});
