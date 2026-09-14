import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { SymbolSetBuilderPanel } from './SymbolSetBuilderPanel.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function baseSet(overrides = {}) {
  return { id: 'set-1', code: 'SET-01', name: 'Electrical', status: 'active', ...overrides };
}

function baseItem(overrides = {}) {
  return {
    id: 'item-1',
    governedSymbolId: 'sym-existing',
    sortOrder: 0,
    groupName: null,
    displayLabel: null,
    notes: null,
    preferredFormat: null,
    provenance: {},
    currentRevisionId: 'rev-1',
    catalogSymbolId: 'S-000001',
    displayId: 'S-000001',
    availabilityStatus: 'active',
    availabilityReason: null,
    canonicalName: 'Existing Symbol',
    category: 'fire',
    discipline: 'fire-safety',
    slug: 'existing-symbol',
    ...overrides,
  };
}

function buildApi({ sets = [baseSet()], items = [], searchResults = [] } = {}) {
  const calls = [];
  let currentItems = items;
  return {
    calls,
    listSymbolSets: async () => ({ items: sets }),
    listItems: async (_setId, { page = 1, pageSize = 200 } = {}) => {
      const boundedPageSize = Math.min(pageSize, 200);
      const start = (page - 1) * boundedPageSize;
      return {
        items: currentItems.slice(start, start + boundedPageSize),
        page,
        pageSize: boundedPageSize,
        total: currentItems.length,
        etag: 'etag-1',
      };
    },
    search: async (params) => {
      calls.push(['search', params]);
      return { items: searchResults, page: 1, pageSize: 100, total: searchResults.length };
    },
    replaceItems: async (setId, payload, etag) => {
      calls.push(['replace', setId, payload, etag]);
      currentItems = payload.map((entry, index) => ({
        ...baseItem({ ...entry, sortOrder: index }),
        governedSymbolId: entry.governedSymbolId,
      }));
      return { items: currentItems, page: 1, pageSize: 200, total: currentItems.length, etag: 'etag-1' };
    },
  };
}

describe('SymbolSetBuilderPanel', () => {
  it('requires Organization Admin privileges', async () => {
    const api = buildApi();
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: false, api })); });
    assert.match(JSON.stringify(renderer.toJSON()), /Organization Admin privileges are required/);
    await act(async () => renderer.unmount());
  });

  it('lists current Symbol Set items with human-readable identity and a Public badge', async () => {
    const api = buildApi({ items: [baseItem()] });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Existing Symbol/);
    const nameElement = renderer.root.findByType('strong');
    assert.equal(nameElement.children.join(''), 'Existing Symbol · S-000001');
    assert.match(text, /Public/);
    await act(async () => renderer.unmount());
  });

  it('loads every current item page before enabling full-replacement edits', async () => {
    const pageCalls = [];
    const api = buildApi();
    api.listItems = async (_setId, { page, pageSize }) => {
      pageCalls.push({ page, pageSize });
      if (page === 1) {
        return {
          items: [baseItem({ governedSymbolId: 'sym-first', canonicalName: 'First Symbol', slug: 'first-symbol' })],
          page: 1,
          pageSize: 1,
          total: 2,
          etag: 'etag-1',
        };
      }
      return {
        items: [baseItem({ id: 'item-2', governedSymbolId: 'sym-second', canonicalName: 'Second Symbol', slug: 'second-symbol', sortOrder: 1 })],
        page: 2,
        pageSize: 1,
        total: 2,
        etag: 'etag-1',
      };
    };

    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    assert.deepEqual(pageCalls, [
      { page: 1, pageSize: 200 },
      { page: 2, pageSize: 200 },
    ]);
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /First Symbol/);
    assert.match(text, /Second Symbol/);
    await act(async () => renderer.unmount());
  });

  it('discards a stale item response after the selected Symbol Set changes', async () => {
    let resolveFirstLoad;
    const api = buildApi({
      sets: [baseSet(), baseSet({ id: 'set-2', code: 'SET-02', name: 'Mechanical' })],
    });
    api.listItems = async (setId) => {
      if (setId === 'set-1') {
        return new Promise((resolve) => { resolveFirstLoad = resolve; });
      }
      return {
        items: [baseItem({ governedSymbolId: 'sym-current', canonicalName: 'Current Set Symbol', slug: 'current-set-symbol' })],
        page: 1,
        pageSize: 200,
        total: 1,
        etag: 'etag-current',
      };
    };

    let renderer;
    await act(async () => {
      renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api }));
      await Promise.resolve();
    });
    await act(async () => {
      renderer.root.findByProps({ id: 'symbol-set-builder-set-select' }).props.onChange({ target: { value: 'set-2' } });
      await Promise.resolve();
    });
    await act(async () => {
      resolveFirstLoad({
        items: [baseItem({ governedSymbolId: 'sym-stale', canonicalName: 'Stale Set Symbol', slug: 'stale-set-symbol' })],
        page: 1,
        pageSize: 200,
        total: 1,
        etag: 'etag-stale',
      });
      await Promise.resolve();
    });

    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Current Set Symbol/);
    assert.doesNotMatch(text, /Stale Set Symbol/);
    await act(async () => renderer.unmount());
  });

  it('preserves items beyond the first 200 rows in a replacement payload', async () => {
    const items = Array.from({ length: 201 }, (_, index) => baseItem({
      id: `item-${index + 1}`,
      governedSymbolId: `sym-${index + 1}`,
      canonicalName: `Symbol ${index + 1}`,
      slug: `symbol-${index + 1}`,
      sortOrder: index,
    }));
    const api = buildApi({ items });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    assert.match(JSON.stringify(renderer.toJSON()), /Symbol 201/);
    await act(async () => {
      renderer.root.findByProps({ id: 'builder-group-sym-201' }).props.onChange({ target: { value: 'Last group' } });
    });
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick());
    const replaceCall = api.calls.find((call) => call[0] === 'replace');
    assert.equal(replaceCall[2].length, 201);
    assert.equal(replaceCall[2][200].governedSymbolId, 'sym-201');
    await act(async () => renderer.unmount());
  });

  it('fails closed when a later item page cannot be loaded', async () => {
    const api = buildApi();
    api.listItems = async (_setId, { page }) => {
      if (page === 1) {
        return {
          items: [baseItem({ governedSymbolId: 'sym-first', canonicalName: 'First Symbol' })],
          page: 1,
          pageSize: 1,
          total: 2,
          etag: 'etag-1',
        };
      }
      throw new Error('second page unavailable');
    };

    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    assert.match(JSON.stringify(renderer.toJSON()), /second page unavailable/);
    assert.equal(renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.disabled, true);
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick());
    assert.equal(api.calls.some((call) => call[0] === 'replace'), false);
    await act(async () => renderer.unmount());
  });

  it('cannot turn a failed initial load followed by search into a full replacement', async () => {
    const api = buildApi({
      searchResults: [
        { governedSymbolId: 'sym-new', source: 'public', canonicalName: 'New Beacon', category: 'fire', discipline: 'fire-safety', slug: 'new-beacon', organizationWide: null, currentRevisionId: 'rev-2' },
      ],
    });
    api.listItems = async () => { throw new Error('initial item load failed'); };
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    const form = renderer.root.findByProps({ className: 'field search-field' });
    await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });
    const checkbox = renderer.root.findByProps({ 'aria-label': 'Select New Beacon' });
    const addButton = renderer.root.findByProps({ 'aria-label': 'Add selected symbols to this Symbol Set' });
    assert.equal(checkbox.props.disabled, true);
    assert.equal(addButton.props.disabled, true);
    await act(async () => { checkbox.props.onChange(); });
    await act(async () => { addButton.props.onClick(); });
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick());
    assert.equal(api.calls.some((call) => call[0] === 'replace'), false);
    await act(async () => renderer.unmount());
  });

  it('search results mark public and approved organization symbols as addable sources', async () => {
    const api = buildApi({
      searchResults: [
        { governedSymbolId: 'sym-public', catalogSymbolId: 'S-000042', displayId: 'S-000042', source: 'public', canonicalName: 'Public Beacon', category: 'fire', discipline: 'fire-safety', slug: 'public-beacon', organizationWide: null, currentRevisionId: 'rev-2' },
        { governedSymbolId: 'sym-org', catalogSymbolId: null, displayId: 'ABCD-7', source: 'organization', canonicalName: 'Org Symbol', category: 'fire', discipline: 'fire-safety', slug: 'org-symbol', organizationWide: false, currentRevisionId: 'rev-3' },
      ],
    });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Search symbols to add to this Symbol Set' }).props.onChange({ target: { value: 'beacon' } }); });
    await act(async () => { renderer.root.findByProps({ type: 'submit' }).props.onClick?.(); });
    // Submit via the form's onSubmit handler directly since react-test-renderer has no real DOM submit event.
    const form = renderer.root.findByProps({ className: 'field search-field' });
    await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });

    const publicCheckbox = renderer.root.findByProps({ 'aria-label': 'Select Public Beacon' });
    const orgCheckbox = renderer.root.findByProps({ 'aria-label': 'Select Org Symbol' });
    assert.equal(publicCheckbox.props.disabled, false);
    assert.equal(orgCheckbox.props.disabled, false);
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Approved organization symbol/);
    assert.match(text, /Public Beacon · S-000042/);
    assert.match(text, /Org Symbol · ABCD-7/);
    await act(async () => renderer.unmount());
  });

  it('adds a selected public search result to the set and saves it with sequential sortOrder', async () => {
    const api = buildApi({
      items: [baseItem()],
      searchResults: [
        { governedSymbolId: 'sym-new', source: 'public', canonicalName: 'New Beacon', category: 'fire', discipline: 'fire-safety', slug: 'new-beacon', organizationWide: null, currentRevisionId: 'rev-2' },
      ],
    });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
    const form = renderer.root.findByProps({ className: 'field search-field' });
    await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Select New Beacon' }).props.onChange(); });
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Add selected symbols to this Symbol Set' }).props.onClick(); });
    assert.match(JSON.stringify(renderer.toJSON()), /New Beacon/);

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick(); });
    const [, , payload] = api.calls.find((call) => call[0] === 'replace');
    assert.deepEqual(payload.map((entry) => entry.governedSymbolId), ['sym-existing', 'sym-new']);
    assert.deepEqual(payload.map((entry) => entry.sortOrder), [0, 1]);
    assert.equal(api.calls.find((call) => call[0] === 'replace')[3], 'etag-1');
    await act(async () => renderer.unmount());
  });

  it('removes an item locally and discard restores the saved state', async () => {
    const api = buildApi({ items: [baseItem(), baseItem({ id: 'item-2', governedSymbolId: 'sym-second', canonicalName: 'Second Symbol', slug: 'second-symbol', sortOrder: 1 })] });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Remove Existing Symbol from this Symbol Set' }).props.onClick(); });
    assert.match(JSON.stringify(renderer.toJSON()), /Confirm removal/);
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Confirm removal of selected Symbol Set items' }).props.onClick(); });
    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /Existing Symbol/);

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Discard Symbol Set changes' }).props.onClick(); });
    assert.match(JSON.stringify(renderer.toJSON()), /Existing Symbol/);
    await act(async () => renderer.unmount());
  });

  it('requires confirmation before switching away from unsaved Symbol Set changes', async () => {
    const previousWindow = globalThis.window;
    const confirmations = [];
    globalThis.window = {
      ...(previousWindow || {}),
      confirm: (message) => {
        confirmations.push(message);
        return false;
      },
    };
    const api = buildApi({
      sets: [baseSet(), baseSet({ id: 'set-2', code: 'SET-02', name: 'Mechanical' })],
      items: [baseItem()],
    });
    let renderer;
    try {
      await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Remove Existing Symbol from this Symbol Set' }).props.onClick(); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Confirm removal of selected Symbol Set items' }).props.onClick(); });

      const selector = renderer.root.findByProps({ id: 'symbol-set-builder-set-select' });
      await act(async () => { selector.props.onChange({ target: { value: 'set-2' } }); });

      assert.equal(renderer.root.findByProps({ id: 'symbol-set-builder-set-select' }).props.value, 'set-1');
      assert.equal(confirmations.length, 1);
    } finally {
      if (renderer) await act(async () => renderer.unmount());
      if (previousWindow === undefined) delete globalThis.window;
      else globalThis.window = previousWindow;
    }
  });

  it('moves an item up and down, keeping sortOrder sequential on save', async () => {
    const api = buildApi({
      items: [
        baseItem({ governedSymbolId: 'sym-a', canonicalName: 'A Symbol', slug: 'a-symbol', sortOrder: 0 }),
        baseItem({ id: 'item-2', governedSymbolId: 'sym-b', canonicalName: 'B Symbol', slug: 'b-symbol', sortOrder: 1 }),
      ],
    });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Move A Symbol down' }).props.onClick(); });
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick(); });

    const [, , payload] = api.calls.find((call) => call[0] === 'replace');
    assert.deepEqual(payload.map((entry) => entry.governedSymbolId), ['sym-b', 'sym-a']);
    assert.deepEqual(payload.map((entry) => entry.sortOrder), [0, 1]);
    await act(async () => renderer.unmount());
  });
});
