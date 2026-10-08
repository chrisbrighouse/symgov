import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { createServer } from 'vite';
import { discDetails, discValve, legacyPng, pilotSymbol } from './symbolDetailsFixtures.js';

// The Details view through the real application router: a direct link, the
// Details button, Back, and Previous/Next over filtered results.

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function response(status, payload) {
  const body = payload == null ? '' : JSON.stringify(payload);
  return { ok: status >= 200 && status < 300, status, text: async () => body, json: async () => payload, headers: new Map() };
}

const USER = {
  id: 'u-1',
  email: 'member@example.test',
  displayName: 'Mo Member',
  roles: [],
  mustChangePin: false,
  subscription: { tier: 'free', status: 'active' },
  session: { mode: 'personal', purpose: 'application', activeOrganizationId: null },
  organization: null,
  isPlatformAdmin: false,
  capabilities: { organizationAdminEnabled: false, platformAdminEnabled: false, symbolSetsEnabled: false, semanticReviewEnabled: false, edEnabled: false },
  recentStepUpAt: null
};

const second = { ...pilotSymbol };
const third = { ...legacyPng };
const BY_REFERENCE = new Map([discValve, second, third].flatMap((symbol) => [[symbol.catalogSymbolId, symbol], [symbol.id, symbol]]));

// The server: three symbols, of which "valve" matches the first two.
function server(requests) {
  return async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ url, method, body: options.body });
    if (url.includes('/auth/me')) return response(200, { user: USER });
    const search = url.match(/\/published\/symbols\/search\?(.*)$/);
    if (search) {
      const params = new URLSearchParams(search[1].split('&refresh=')[0]);
      const q = (params.get('q') || '').toLowerCase();
      const items = [discValve, second, third].filter((symbol) => !q || `${symbol.name} ${symbol.summary}`.toLowerCase().includes(q));
      return response(200, { scope: 'catalog', items, total: items.length, page: 1, pageSize: 60, facets: {} });
    }
    const details = url.match(/\/published\/symbols\/([^/?]+)\/details/);
    if (details) return response(200, { ...discDetails, catalogSymbolId: decodeURIComponent(details[1]) });
    if (url.match(/\/published\/symbols\/[^/?]+\/comments/)) return response(200, { commentCount: 0, items: [] });
    const one = url.match(/\/published\/symbols\/([^/?]+)\?/);
    if (one) {
      const symbol = BY_REFERENCE.get(decodeURIComponent(one[1]));
      return symbol ? response(200, { item: symbol, resolvedBy: 'canonical' }) : response(404, { detail: 'catalog_symbol_not_found' });
    }
    return response(200, {});
  };
}

let current;
function Probe() {
  current = useLocation();
  return null;
}

async function mount(path, requests, { keydownHandlers = [] } = {}) {
  globalThis.fetch = server(requests);
  const vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  let renderer;
  try {
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    // Browser globals the Catalog page touches; set after the modules load so
    // the API root is still resolved as it is under Node.
    globalThis.document = {
      getElementById: () => null,
      addEventListener: (type, handler) => { if (type === 'keydown') keydownHandlers.push(handler); },
      removeEventListener() {},
      activeElement: null
    };
    globalThis.window = { scrollTo() {}, scrollY: 0, location: { origin: 'https://symgov.test' } };
    await act(async () => {
      renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: [path] }, createElement(Probe), createElement(App)));
    });
    await act(async () => {});
  } finally {
    await vite.close();
  }
  return renderer;
}

const flat = (renderer) => JSON.stringify(renderer.toJSON());
const textOf = (node) => (typeof node === 'string' ? node : (node.children || []).map(textOf).join(''));
const button = (renderer, label) => renderer.root.findAll((node) => node.type === 'button' && textOf(node).trim() === label)[0];
const search = () => new URLSearchParams(current.search);

describe('the Details view in the application', () => {
  let originalFetch;
  beforeEach(() => { originalFetch = globalThis.fetch; });
  afterEach(() => {
    globalThis.fetch = originalFetch;
    delete globalThis.document;
    delete globalThis.window;
  });

  it('opens from a direct #/s/<id> link, with the same view as the long URL, and no result context', async () => {
    const requests = [];
    const renderer = await mount('/s/S-273', requests);
    assert.equal(current.pathname, '/standards');
    assert.equal(search().get('symbol'), 'S-273');
    assert.equal(search().get('view'), 'details');
    const markup = flat(renderer);
    assert.match(markup, /S-273 · Modular Valve Double Isolation and Bleed/);
    assert.match(markup, /Piping › CustomOperatedValve › Double Block And Bleed Valve/);
    // A link has no results to step through.
    assert.equal(button(renderer, '‹ Previous result').props.disabled, true);
    assert.equal(button(renderer, 'Next result ›').props.disabled, true);
    assert.ok(requests.some(({ url }) => /\/published\/symbols\/S-273\/details/.test(url)), 'details requested for S-273');
    await act(async () => renderer.unmount());
  });

  it('opens from the long URL on a chosen tab', async () => {
    const renderer = await mount('/standards?symbol=S-273&view=details&tab=connections', []);
    assert.match(flat(renderer), /Connection points \(3\)/);
    assert.equal(renderer.root.findAllByType('tr').length, 4);
    await act(async () => renderer.unmount());
  });

  it('puts the selected tab in the URL', async () => {
    const renderer = await mount('/standards?symbol=S-273&view=details', []);
    await act(async () => button(renderer, 'History').props.onClick());
    assert.equal(search().get('tab'), 'history');
    assert.equal(search().get('view'), 'details');
    assert.equal(search().get('symbol'), 'S-273');
    await act(async () => renderer.unmount());
  });

  it('says so when the symbol does not exist, and Back leaves for the catalog', async () => {
    const renderer = await mount('/standards?symbol=S-9999&view=details', []);
    await act(async () => {});
    assert.match(flat(renderer), /Symbol not found/);
    await act(async () => button(renderer, 'Back to catalog').props.onClick());
    assert.equal(search().get('view'), null);
    assert.equal(search().get('tab'), null);
    await act(async () => renderer.unmount());
  });

  it('is reached from the panel button, hides the catalog, and Back restores it with its filter and selection', async () => {
    const renderer = await mount('/standards?symbol=S-273', []);
    const searchBox = renderer.root.findAll((node) => node.type === 'input' && node.props.type === 'search')[0];
    await act(async () => searchBox.props.onChange({ target: { value: 'valve' } }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 320)); });
    assert.match(flat(renderer), /Published symbol details/);

    await act(async () => button(renderer, 'Details').props.onClick());
    assert.equal(search().get('view'), 'details');
    assert.equal(search().get('symbol'), 'S-273');
    const shell = renderer.root.findAll((node) => node.props['data-symbol-details-open'] === 'true');
    assert.equal(shell.length, 1, 'the catalog is hidden, not unmounted');
    assert.match(flat(renderer), /Approved symbol/);

    await act(async () => button(renderer, 'Back to catalog').props.onClick());
    assert.equal(search().get('view'), null);
    assert.equal(search().get('symbol'), 'S-273');
    assert.equal(renderer.root.findAll((node) => node.props['data-symbol-details-open'] === 'true').length, 0);
    const searchAgain = renderer.root.findAll((node) => node.type === 'input' && node.props.type === 'search')[0];
    assert.equal(searchAgain.props.value, 'valve', 'the search text is kept');
    assert.match(flat(renderer), /Published symbol details/, 'the symbol panel is open again');
    await act(async () => renderer.unmount());
  });

  it('steps through the filtered results with Previous and Next, and stops at the ends', async () => {
    const renderer = await mount('/standards?symbol=S-273', []);
    const searchBox = renderer.root.findAll((node) => node.type === 'input' && node.props.type === 'search')[0];
    await act(async () => searchBox.props.onChange({ target: { value: 'valve' } }));
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 320)); });
    await act(async () => button(renderer, 'Details').props.onClick());

    // "valve" matches two of the three symbols: S-273 then S-103.
    assert.equal(button(renderer, '‹ Previous result').props.disabled, true);
    assert.equal(button(renderer, 'Next result ›').props.disabled, false);
    assert.match(textOf(renderer.toJSON()), /1 of 2/);

    await act(async () => button(renderer, 'Next result ›').props.onClick());
    assert.equal(search().get('symbol'), 'S-103');
    assert.equal(search().get('view'), 'details');
    assert.match(flat(renderer), /S-103 · Gate valve/);
    assert.equal(button(renderer, 'Next result ›').props.disabled, true);
    assert.equal(button(renderer, '‹ Previous result').props.disabled, false);

    await act(async () => button(renderer, '‹ Previous result').props.onClick());
    assert.equal(search().get('symbol'), 'S-273');
    await act(async () => renderer.unmount());
  });

  it('keeps the tab while stepping, and one Back still returns to the catalog', async () => {
    const renderer = await mount('/standards?symbol=S-273', []);
    await act(async () => button(renderer, 'Details').props.onClick());
    await act(async () => button(renderer, 'History').props.onClick());
    await act(async () => button(renderer, 'Next result ›').props.onClick());
    assert.equal(search().get('tab'), 'history');
    await act(async () => button(renderer, 'Back to catalog').props.onClick());
    assert.equal(search().get('view'), null);
    assert.equal(search().get('tab'), null);
    await act(async () => renderer.unmount());
  });

  it('"See all N in the catalog" opens the catalog filtered to that class and nothing else', async () => {
    const requests = [];
    const renderer = await mount('/standards?symbol=S-273&view=details', requests);
    await act(async () => {});
    const seeAll = renderer.root.findAll((node) => node.type === 'button' && textOf(node).startsWith('See all'))[0];
    await act(async () => seeAll.props.onClick());
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 320)); });
    assert.equal(search().get('dexpiClass'), 'CustomOperatedValve');
    assert.equal(search().get('view'), null);
    assert.equal(search().get('symbol'), null);
    const classSearch = requests.filter(({ url }) => /\/published\/symbols\/search\?/.test(url) && /dexpiClass=CustomOperatedValve/.test(url));
    assert.ok(classSearch.length >= 1, 'the search carries the class');
    assert.match(flat(renderer), /DEXPI class: /);
    await act(async () => renderer.unmount());
  });

  it('ignores the Catalog\'s arrow-key shortcuts while Details is open', async () => {
    const handlers = [];
    const renderer = await mount('/standards?symbol=S-273&view=details', [], { keydownHandlers: handlers });
    assert.ok(handlers.length >= 1, 'the page listens for keys');
    for (const key of ['ArrowDown', 'j', 'End']) {
      await act(async () => handlers.forEach((handler) => handler({ key, defaultPrevented: false, target: {}, preventDefault() {} })));
    }
    assert.equal(search().get('symbol'), 'S-273');
    assert.equal(search().get('view'), 'details');
    await act(async () => renderer.unmount());
  });
});
