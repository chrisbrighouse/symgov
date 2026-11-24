import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

// The Catalog's Visual match tab, through the real application router: it exists only when the server says the
// feature is on, it replaces the results (not the page), and it leaves the Catalog search running for its filters.

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
// The Catalog page and the matcher use `document` for focus and paste; Node has none.
globalThis.document = { getElementById: () => null, querySelector: () => null, addEventListener() {}, removeEventListener() {} };

function response(status, payload) {
  const body = payload == null ? '' : JSON.stringify(payload);
  return { ok: status >= 200 && status < 300, status, text: async () => body, json: async () => payload };
}

function user({ visualMatchEnabled }) {
  return {
    id: 'u-1', email: 'member@example.test', displayName: 'Mo Member', roles: [], mustChangePin: false,
    subscription: { tier: 'free', status: 'active' },
    session: { mode: 'personal', purpose: 'application', activeOrganizationId: null },
    organization: null, isPlatformAdmin: false,
    capabilities: { organizationAdminEnabled: false, platformAdminEnabled: false, symbolSetsEnabled: false, semanticReviewEnabled: false, edEnabled: false, visualMatchEnabled },
    recentStepUpAt: null,
  };
}

async function mount(path, currentUser, requests) {
  globalThis.fetch = async (url, options = {}) => {
    requests.push({ url, method: options.method || 'GET' });
    if (url.includes('/auth/me')) return response(200, { user: currentUser });
    if (url.includes('/published/symbols/search')) {
      return response(200, { scope: 'catalog', items: [], total: 0, facets: { catalogDisciplines: [{ value: 'Piping', count: 2 }] } });
    }
    return response(200, {});
  };
  const vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  let renderer;
  try {
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    await act(async () => {
      renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)));
    });
  } finally {
    await vite.close();
  }
  return renderer;
}

const tabLabels = (renderer) =>
  renderer.root.findAll((n) => n.type === 'button' && n.props.role === 'tab').map((n) => n.children.filter((c) => typeof c === 'string').join(''));

describe('mounted Visual match tab', () => {
  let originalFetch;
  beforeEach(() => { originalFetch = globalThis.fetch; });
  afterEach(() => { globalThis.fetch = originalFetch; });

  it('shows the tab and the matcher, with the disclosure, when the feature is on', async () => {
    const requests = [];
    const renderer = await mount('/standards?view=visual', user({ visualMatchEnabled: true }), requests);
    assert.deepEqual(tabLabels(renderer), ['Catalog', 'Visual match']);
    const markup = JSON.stringify(renderer.toJSON());
    assert.match(markup, /Find Catalog symbols that look like one in your drawing/);
    assert.match(markup, /zero data retention/);
    assert.equal(renderer.root.findAllByProps({ className: 'visual-match-panel' }).length, 1);
    assert.equal(renderer.root.findAll((n) => typeof n.props?.className === 'string' && n.props.className.includes('standards-browser-grid')).length, 0);
    assert.ok(requests.some(({ url }) => url.includes('/published/symbols/search')), 'the Catalog search still runs for the filter options');
    assert.equal(requests.some(({ url }) => url.includes('/visual-match')), false, 'nothing is sent until the person asks');
    await act(async () => renderer.unmount());
  });

  it('has no tab and no panel when the feature is off, even if the address asks for it', async () => {
    const renderer = await mount('/standards?view=visual', user({ visualMatchEnabled: false }), []);
    assert.equal(tabLabels(renderer).includes('Visual match'), false);
    assert.equal(renderer.root.findAllByProps({ className: 'visual-match-panel' }).length, 0);
    await act(async () => renderer.unmount());
  });

  it('switching to the Catalog tab brings back the results grid', async () => {
    const renderer = await mount('/standards?view=visual', user({ visualMatchEnabled: true }), []);
    const catalogTab = renderer.root.findAll((n) => n.type === 'button' && n.props.role === 'tab' && n.props.id === 'catalog-view-tab-catalog')[0];
    await act(async () => catalogTab.props.onClick());
    assert.equal(renderer.root.findAllByProps({ className: 'visual-match-panel' }).length, 0);
    assert.ok(renderer.root.findAll((n) => typeof n.props?.className === 'string' && n.props.className.includes('standards-browser-grid')).length > 0);
    await act(async () => renderer.unmount());
  });
});
