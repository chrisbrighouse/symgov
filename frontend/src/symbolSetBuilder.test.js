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

function buildApi({ sets = [baseSet()], items = [], searchResults = [] } = {}) {
  const calls = [];
  let currentItems = items;
  return {
    calls,
    listSymbolSets: async () => ({ items: sets }),
    listItems: async () => ({ items: currentItems, page: 1, pageSize: 1000, total: currentItems.length }),
    search: async (params) => {
      calls.push(['search', params]);
      return { items: searchResults, page: 1, pageSize: 100, total: searchResults.length };
    },
    replaceItems: async (setId, payload) => {
      calls.push(['replace', setId, payload]);
      currentItems = payload.map((entry, index) => ({
        ...baseItem({ ...entry, sortOrder: index }),
        governedSymbolId: entry.governedSymbolId,
      }));
      return { items: currentItems, page: 1, pageSize: 1000, total: currentItems.length };
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
    assert.equal(nameElement.children.join(''), 'Existing Symbol · existing-symbol');
    assert.match(text, /Public/);
    await act(async () => renderer.unmount());
  });

  it('search results distinguish public (addable) from organization (not addable) sources', async () => {
    const api = buildApi({
      searchResults: [
        { governedSymbolId: 'sym-public', source: 'public', canonicalName: 'Public Beacon', category: 'fire', discipline: 'fire-safety', slug: 'public-beacon', organizationWide: null, currentRevisionId: 'rev-2' },
        { governedSymbolId: 'sym-org', source: 'organization', canonicalName: 'Org Symbol', category: 'fire', discipline: 'fire-safety', slug: 'org-symbol', organizationWide: false, currentRevisionId: 'rev-3' },
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
    assert.equal(orgCheckbox.props.disabled, true);
    assert.match(JSON.stringify(renderer.toJSON()), /Toggle organization-wide instead of adding to a set/);
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
});
