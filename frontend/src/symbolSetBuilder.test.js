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
    availabilityStatus: 'active',
    availabilityReason: null,
    canonicalName: 'Existing Symbol',
    category: 'fire',
    discipline: 'fire-safety',
    slug: 'existing-symbol',
    ...overrides,
  };
}

function clipboardEntry(overrides = {}) {
  return {
    governedSymbolId: 'sym-clip', catalogSymbolId: 'S-000207', displayId: 'S-000207', source: 'public',
    canonicalName: 'Clip Valve', category: 'valve', discipline: 'piping', slug: 'clip-valve',
    organizationWide: null, currentRevisionId: 'rev-clip', ...overrides,
  };
}

function buildApi({ sets = [baseSet()], items = [], searchResults = [], clipboard = { items: [], unavailable: [], total: 0 } } = {}) {
  const calls = [];
  let currentItems = items;
  return {
    calls,
    loadClipboard: async () => {
      calls.push(['clipboard']);
      return clipboard;
    },
    listSymbolSets: async () => ({ items: sets }),
    listItems: async () => ({ items: currentItems, page: 1, pageSize: 200, total: currentItems.length }),
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
      return { items: currentItems, page: 1, pageSize: 200, total: currentItems.length };
    },
  };
}

describe('SymbolSetBuilderPanel', () => {
  it('pages through every item at the route maximum and keeps the last ETag', async () => {
    // The items route caps pageSize at 200 (routes/symbol_sets.py:21). This
    // mock enforces that cap the way the server does, so asking for more
    // fails here exactly as it did in production.
    const all = Array.from({ length: 250 }, (_, index) => baseItem({
      id: `item-${index}`, governedSymbolId: `sym-${index}`, sortOrder: index,
    }));
    const requested = [];
    const api = buildApi();
    api.listItems = async (setId, { page = 1, pageSize = 200 } = {}) => {
      if (pageSize > 200) throw new Error('Symbol Set items load failed.');
      requested.push([page, pageSize]);
      const start = (page - 1) * pageSize;
      return {
        items: all.slice(start, start + pageSize),
        page, pageSize, total: all.length,
        etag: `etag-page-${page}`,
      };
    };
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    assert.deepEqual(requested, [[1, 200], [2, 200]]);

    // Saving must send the ETag from the final page; without one the server
    // rejects the write with 428 and nothing persists.
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick());
    const replace = api.calls.find((entry) => entry[0] === 'replace');
    assert.ok(replace, 'expected a save call');
    assert.equal(replace[3], 'etag-page-2');
    assert.equal(replace[2].length, 250, 'every loaded item must be saved back');
    await act(async () => renderer.unmount());
  });

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
    assert.equal(nameElement.children.join(''), 'Existing Symbol · Draft');
    assert.match(text, /Public/);
    await act(async () => renderer.unmount());
  });

  it('search results allow adding both public and approved organization symbols directly', async () => {
    const api = buildApi({
      searchResults: [
        { governedSymbolId: 'sym-public', source: 'public', canonicalName: 'Public Beacon', category: 'fire', discipline: 'fire-safety', slug: 'public-beacon', organizationWide: null, currentRevisionId: 'rev-2' },
        { governedSymbolId: 'sym-org', source: 'organization', canonicalName: 'Org Symbol', category: 'fire', discipline: 'fire-safety', slug: 'org-symbol', organizationWide: false, currentRevisionId: 'rev-3' },
      ],
    });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Search symbols to add to this Symbol Set' }).props.onChange({ target: { value: 'beacon' } }); });
    // Submit via the form's onSubmit handler directly since react-test-renderer has no real DOM submit event.
    const form = renderer.root.findByProps({ className: 'field search-field' });
    await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });

    const publicCheckbox = renderer.root.findByProps({ 'aria-label': 'Select Public Beacon' });
    const orgCheckbox = renderer.root.findByProps({ 'aria-label': 'Select Org Symbol' });
    assert.equal(publicCheckbox.props.disabled, false);
    assert.equal(orgCheckbox.props.disabled, false);
    assert.match(JSON.stringify(renderer.toJSON()), /Approved organization symbol/);
    await act(async () => renderer.unmount());
  });

  it('adds a selected approved organization search result to the set directly (Stage 11 loosening)', async () => {
    const api = buildApi({
      items: [baseItem()],
      searchResults: [
        { governedSymbolId: 'sym-org', source: 'organization', canonicalName: 'Org Symbol', category: 'fire', discipline: 'fire-safety', slug: 'org-symbol', displayId: 'ABCD-9', organizationWide: false, currentRevisionId: 'rev-3' },
      ],
    });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
    const form = renderer.root.findByProps({ className: 'field search-field' });
    await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Select Org Symbol' }).props.onChange(); });
    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Add selected symbols to this Symbol Set' }).props.onClick(); });
    assert.match(JSON.stringify(renderer.toJSON()), /Org Symbol/);
    assert.match(JSON.stringify(renderer.toJSON()), /ABCD-9/);

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick(); });
    const [, , payload] = api.calls.find((call) => call[0] === 'replace');
    assert.deepEqual(payload.map((entry) => entry.governedSymbolId), ['sym-existing', 'sym-org']);
    await act(async () => renderer.unmount());
  });

  it('search sends the category/discipline/format filters entered by the user', async () => {
    const api = buildApi({ searchResults: [] });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
    await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-category-filter' }).props.onChange({ target: { value: 'fire detection' } }); });
    await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-discipline-filter' }).props.onChange({ target: { value: 'life safety' } }); });
    await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-format-filter' }).props.onChange({ target: { value: 'svg' } }); });
    const form = renderer.root.findByProps({ className: 'field search-field' });
    await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });

    const [, params] = api.calls.find((call) => call[0] === 'search');
    assert.equal(params.category, 'fire detection');
    assert.equal(params.discipline, 'life safety');
    assert.equal(params.format, 'svg');
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
    await act(async () => renderer.unmount());
  });

  it('removes an item locally and discard restores the saved state', async () => {
    const api = buildApi({ items: [baseItem(), baseItem({ id: 'item-2', governedSymbolId: 'sym-second', canonicalName: 'Second Symbol', slug: 'second-symbol', sortOrder: 1 })] });
    let renderer;
    await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Remove Existing Symbol from this Symbol Set' }).props.onClick(); });
    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /Existing Symbol/);

    await act(async () => { renderer.root.findByProps({ 'aria-label': 'Discard Symbol Set changes' }).props.onClick(); });
    assert.match(JSON.stringify(renderer.toJSON()), /Existing Symbol/);
    await act(async () => renderer.unmount());
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

  describe('clipboard view', () => {
    async function openClipboard(api) {
      let renderer;
      await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
      await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-clipboard' }).props.onClick(); });
      return renderer;
    }

    function savedIds(api) {
      const [, , payload] = api.calls.find((call) => call[0] === 'replace');
      return payload.map((entry) => entry.governedSymbolId);
    }

    it('loads the clipboard only when its tab is first opened, and shows what cannot be added', async () => {
      const api = buildApi({
        items: [baseItem({ governedSymbolId: 'sym-existing', canonicalName: 'Existing Symbol' })],
        clipboard: {
          items: [
            clipboardEntry(),
            clipboardEntry({ governedSymbolId: 'sym-existing', canonicalName: 'Existing Symbol', slug: 'existing-symbol' }),
          ],
          unavailable: [{ slug: 'gone', displayId: '0003-12', name: 'Withdrawn Symbol' }],
          total: 3,
        },
      });
      let renderer;
      await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
      assert.equal(api.calls.filter((call) => call[0] === 'clipboard').length, 0);

      await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-clipboard' }).props.onClick(); });
      assert.equal(api.calls.filter((call) => call[0] === 'clipboard').length, 1);
      const tab = renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-clipboard' });
      assert.equal(tab.props['aria-selected'], true);

      const text = JSON.stringify(renderer.toJSON());
      assert.match(text, /Clip Valve · S-000207/);
      assert.match(text, /3 on clipboard · 1 can be added/);
      assert.match(text, /Withdrawn Symbol · 0003-12/);
      assert.match(text, /Can no longer be added/);
      // The search filters belong to the search view only.
      assert.equal(renderer.root.findAllByProps({ id: 'symbol-set-builder-category-filter' }).length, 0);
      const existing = renderer.root.findAllByProps({ 'aria-label': 'Select Existing Symbol' });
      assert.equal(existing[0].props.disabled, true);

      // Going back and forth does not reload; Refresh does.
      await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-search' }).props.onClick(); });
      await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-clipboard' }).props.onClick(); });
      assert.equal(api.calls.filter((call) => call[0] === 'clipboard').length, 1);
      await act(async () => { await renderer.root.findByProps({ 'aria-label': 'Refresh clipboard' }).props.onClick(); });
      assert.equal(api.calls.filter((call) => call[0] === 'clipboard').length, 2);
      await act(async () => renderer.unmount());
    });

    it('adds every addable clipboard symbol, in clipboard order, and only on save', async () => {
      const api = buildApi({
        items: [baseItem()],
        clipboard: {
          items: [
            clipboardEntry({ governedSymbolId: 'sym-b', canonicalName: 'B Valve', slug: 'b-valve' }),
            clipboardEntry({ governedSymbolId: 'sym-existing', canonicalName: 'Existing Symbol', slug: 'existing-symbol' }),
            clipboardEntry({ governedSymbolId: 'sym-a', canonicalName: 'A Beacon', slug: 'a-beacon', source: 'organization', organizationWide: true, displayId: 'ACME-3' }),
          ],
          unavailable: [],
          total: 3,
        },
      });
      const renderer = await openClipboard(api);
      const addAll = renderer.root.findByProps({ 'aria-label': 'Add every clipboard symbol that can be added to this Symbol Set' });
      assert.equal(addAll.children.join(''), 'Add all (2)');
      await act(async () => { addAll.props.onClick(); });
      assert.equal(api.calls.some((call) => call[0] === 'replace'), false);

      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick(); });
      assert.deepEqual(savedIds(api), ['sym-existing', 'sym-b', 'sym-a']);
      await act(async () => renderer.unmount());
    });

    it('selects all, or some, and keeps its selection apart from the search selection', async () => {
      const api = buildApi({
        searchResults: [clipboardEntry({ governedSymbolId: 'sym-search', canonicalName: 'Search Beacon', slug: 'search-beacon' })],
        clipboard: {
          items: [
            clipboardEntry({ governedSymbolId: 'sym-1', canonicalName: 'One', slug: 'one' }),
            clipboardEntry({ governedSymbolId: 'sym-2', canonicalName: 'Two', slug: 'two' }),
          ],
          unavailable: [],
          total: 2,
        },
      });
      let renderer;
      await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
      const form = renderer.root.findByProps({ className: 'field search-field' });
      await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Select Search Beacon' }).props.onChange(); });

      await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-clipboard' }).props.onClick(); });
      const selectAll = () => renderer.root.findByProps({ 'aria-label': 'Select all clipboard symbols that can be added' });
      await act(async () => { selectAll().props.onChange(); });
      assert.equal(selectAll().props.checked, true);
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Select Two' }).props.onChange(); });
      assert.equal(selectAll().props.checked, false);

      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Add selected clipboard symbols to this Symbol Set' }).props.onClick(); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick(); });
      // The ticked search result was not added from the clipboard view.
      assert.deepEqual(savedIds(api), ['sym-1']);
      await act(async () => renderer.unmount());
    });

    it('says where to collect symbols when the clipboard is empty', async () => {
      const api = buildApi();
      const renderer = await openClipboard(api);
      assert.match(JSON.stringify(renderer.toJSON()), /Your clipboard is empty/);
      assert.equal(renderer.root.findByType('a').props.href, '#/standards');
      await act(async () => renderer.unmount());
    });

    it('shows a load failure without breaking the search view', async () => {
      const api = buildApi();
      api.loadClipboard = async () => { throw new Error('Your clipboard could not be loaded.'); };
      const renderer = await openClipboard(api);
      assert.match(JSON.stringify(renderer.toJSON()), /Your clipboard could not be loaded/);
      await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-search' }).props.onClick(); });
      assert.ok(renderer.root.findByProps({ 'aria-label': 'Search symbols to add to this Symbol Set' }));
      await act(async () => renderer.unmount());
    });
  });

  describe('the 1,000-item limit', () => {
    const nearlyFull = () => Array.from({ length: 999 }, (_, index) => baseItem({
      id: `item-${index}`, governedSymbolId: `sym-${index}`, sortOrder: index, canonicalName: `Symbol ${index}`,
    }));

    it('warns and adds nothing when the clipboard would take the set past 1,000', async () => {
      const api = buildApi({
        items: nearlyFull(),
        clipboard: {
          items: [
            clipboardEntry({ governedSymbolId: 'sym-x', canonicalName: 'X', slug: 'x' }),
            clipboardEntry({ governedSymbolId: 'sym-y', canonicalName: 'Y', slug: 'y' }),
          ],
          unavailable: [],
          total: 2,
        },
      });
      let renderer;
      await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
      await act(async () => { renderer.root.findByProps({ id: 'symbol-set-builder-find-tab-clipboard' }).props.onClick(); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Add every clipboard symbol that can be added to this Symbol Set' }).props.onClick(); });

      const alert = renderer.root.findByProps({ className: 'set-admin-status error symbol-set-builder-limit' });
      assert.equal(alert.props.role, 'alert');
      assert.match(alert.children.join(''), /at most 1,000 symbols\. This set has 999, so 1 more can be added, and you tried to add 2\. Nothing was added/);
      assert.equal(renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.disabled, true);

      // Choosing fewer clears the warning and adds them.
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Select X' }).props.onChange(); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Add selected clipboard symbols to this Symbol Set' }).props.onClick(); });
      assert.equal(renderer.root.findAllByProps({ className: 'set-admin-status error symbol-set-builder-limit' }).length, 0);
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Save Symbol Set changes' }).props.onClick(); });
      const [, , payload] = api.calls.find((call) => call[0] === 'replace');
      assert.equal(payload.length, 1000);
      await act(async () => renderer.unmount());
    });

    it('applies the same limit to search results', async () => {
      const api = buildApi({
        items: nearlyFull(),
        searchResults: [
          clipboardEntry({ governedSymbolId: 'sym-x', canonicalName: 'X', slug: 'x' }),
          clipboardEntry({ governedSymbolId: 'sym-y', canonicalName: 'Y', slug: 'y' }),
        ],
      });
      let renderer;
      await act(async () => { renderer = create(createElement(SymbolSetBuilderPanel, { isAdmin: true, api })); });
      const form = renderer.root.findByProps({ className: 'field search-field' });
      await act(async () => { await form.props.onSubmit({ preventDefault: () => {} }); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Select X' }).props.onChange(); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Select Y' }).props.onChange(); });
      await act(async () => { renderer.root.findByProps({ 'aria-label': 'Add selected symbols to this Symbol Set' }).props.onClick(); });
      assert.match(JSON.stringify(renderer.toJSON()), /Nothing was added; choose fewer symbols/);
      await act(async () => renderer.unmount());
    });
  });
});
