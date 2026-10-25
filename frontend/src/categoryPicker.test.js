import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement, useState } from 'react';
import { act, create } from 'react-test-renderer';

import { ADD_CATEGORY_OPTION, CategoryPicker } from './CategoryPicker.js';
import { listedCategory } from './catalogWorkbench.js';
import { OrganizationSymbolDraftsPanel } from './OrganizationSymbolDraftsPanel.js';
import { CatalogCategoriesPanel } from './PlatformAdminPage.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const LIST = ['Valves', 'Pumps', 'Doors', 'Miscellaneous / Unclassified'];

// The picker with its value held in state, as the forms hold it.
function Harness({ initial = '', onAdd = null, changes }) {
  const [value, setValue] = useState(initial);
  return createElement(CategoryPicker, {
    id: 'cat',
    value,
    categories: LIST,
    onAdd,
    onChange: (name) => {
      changes.push(name);
      setValue(name);
    },
  });
}

const selectOf = (renderer) => renderer.root.find((node) => node.type === 'select' && node.props.id === 'cat');
const optionValues = (renderer) => selectOf(renderer).findAllByType('option').map((option) => option.props.value);
const buttonLabelled = (renderer, text) => renderer.root.find((node) => node.type === 'button' && node.children.join('') === text);

async function openAdd(renderer, name) {
  await act(async () => selectOf(renderer).props.onChange({ target: { value: ADD_CATEGORY_OPTION } }));
  await act(async () => renderer.root.findByProps({ id: 'cat-new' }).props.onChange({ target: { value: name } }));
}

describe('CategoryPicker', () => {
  it('offers the list, keeps an off-list stored value, and offers "add" only with onAdd', async () => {
    let renderer;
    await act(async () => { renderer = create(createElement(Harness, { initial: 'fire', changes: [] })); });
    assert.deepEqual(optionValues(renderer), ['', 'fire', ...LIST]);
    assert.match(JSON.stringify(renderer.toJSON()), /fire \(not on the list\)/);

    await act(async () => { renderer = create(createElement(Harness, { onAdd: async () => ({}), changes: [] })); });
    assert.deepEqual(optionValues(renderer), ['', ...LIST, ADD_CATEGORY_OPTION]);
  });

  it('adds a new category and selects it', async () => {
    const changes = [];
    const calls = [];
    const onAdd = async (name, options) => {
      calls.push([name, options]);
      return { added: true, name: 'Cable Trays' };
    };
    let renderer;
    await act(async () => { renderer = create(createElement(Harness, { onAdd, changes })); });
    await openAdd(renderer, ' Cable Trays ');
    assert.equal(changes.length, 0, 'choosing "add" is not a value');

    // Enter adds, and must not submit the form around the picker.
    let prevented = false;
    await act(async () => renderer.root.findByProps({ id: 'cat-new' }).props.onKeyDown({ key: 'Enter', preventDefault: () => { prevented = true; } }));
    assert.equal(prevented, true);
    assert.deepEqual(calls, [['Cable Trays', { allowSimilar: false }]]);
    assert.deepEqual(changes, ['Cable Trays']);
    assert.equal(renderer.root.findAllByProps({ id: 'cat-new' }).length, 0);
  });

  it('offers the listed name back when a new one is close to it', async () => {
    const changes = [];
    const calls = [];
    const onAdd = async (name, options) => {
      calls.push(options.allowSimilar);
      return options.allowSimilar ? { added: true, name } : { added: false, similarTo: 'Pumps' };
    };
    let renderer;
    await act(async () => { renderer = create(createElement(Harness, { onAdd, changes })); });
    await openAdd(renderer, 'Pump');
    await act(async () => buttonLabelled(renderer, 'Add').props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()), /is close to/);
    assert.deepEqual(changes, []);

    await act(async () => buttonLabelled(renderer, 'Use Pumps').props.onClick());
    assert.deepEqual(changes, ['Pumps']);

    await openAdd(renderer, 'Pump');
    await act(async () => buttonLabelled(renderer, 'Add').props.onClick());
    await act(async () => buttonLabelled(renderer, 'Add “Pump” anyway').props.onClick());
    assert.deepEqual(calls, [false, false, true]);
    assert.deepEqual(changes, ['Pumps', 'Pump']);
  });

  it('shows why an add failed and keeps the row open', async () => {
    const onAdd = async () => { throw new Error("'Doorz' was retired by a Platform Admin."); };
    let renderer;
    await act(async () => { renderer = create(createElement(Harness, { onAdd, changes: [] })); });
    await openAdd(renderer, 'Doorz');
    await act(async () => buttonLabelled(renderer, 'Add').props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()), /retired by a Platform Admin/);
    assert.equal(renderer.root.findAllByProps({ id: 'cat-new' }).length, 1);
  });
});

describe('listedCategory', () => {
  it('pre-selects the listed name an agent suggestion means, and nothing otherwise', () => {
    assert.equal(listedCategory('valve', LIST), 'Valves');
    assert.equal(listedCategory('DOORS', LIST), 'Doors');
    assert.equal(listedCategory('miscellaneous/unclassified', LIST), 'Miscellaneous / Unclassified');
    assert.equal(listedCategory('Heat exchanger', LIST), '');
    assert.equal(listedCategory('', LIST), '');
  });
});

describe('OrganizationSymbolDraftsPanel categories', () => {
  it('offers the live list, reviewer additions included', async () => {
    const api = {
      listDrafts: async () => ({ items: [] }),
      listCategories: async () => ({ items: [{ name: 'Valves' }, { name: 'Cable Trays' }, { name: 'Miscellaneous / Unclassified' }] }),
    };
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolDraftsPanel, { canCreate: true, api })); });
    const select = renderer.root.find((node) => node.type === 'select' && node.props.id === 'org-symbol-draft-category');
    assert.deepEqual(select.findAllByType('option').map((option) => option.props.value),
      ['', 'Valves', 'Cable Trays', 'Miscellaneous / Unclassified']);
  });
});

describe('CatalogCategoriesPanel', () => {
  const report = {
    additions: [
      { id: 'a1', name: 'Cable Trays', addedByName: 'Rita Reviewer', addedAt: '2026-10-25T10:00:00Z', retiredAt: null, symbolCount: 3 },
    ],
    legacyValues: [{ value: 'Fire', useCount: 7, lastUsedAt: '2026-06-01T09:00:00Z', symbolCount: 2 }],
  };

  it('retires an addition behind step-up and adds an old value to the list', async () => {
    const calls = [];
    const api = {
      load: async () => report,
      retire: async (id) => { calls.push(['retire', id]); return { ...report, additions: [{ ...report.additions[0], retiredAt: '2026-10-25T11:00:00Z' }] }; },
      add: async (value, options) => { calls.push(['add', value, options.allowSimilar]); return { added: true, name: value }; },
    };
    const protect = async (operation) => { calls.push(['step-up']); return operation(); };
    const originalWindow = globalThis.window;
    globalThis.window = { confirm: () => true };
    try {
      let renderer;
      await act(async () => { renderer = create(createElement(CatalogCategoriesPanel, { protect, api })); });
      const text = JSON.stringify(renderer.toJSON());
      assert.match(text, /Cable Trays/);
      assert.match(text, /Rita Reviewer/);
      assert.match(text, /Fire/);

      await act(async () => renderer.root.findByProps({ 'aria-label': 'Retire Cable Trays' }).props.onClick());
      assert.deepEqual(calls.slice(0, 2), [['step-up'], ['retire', 'a1']]);
      assert.match(JSON.stringify(renderer.toJSON()), /Retired/);

      await act(async () => renderer.root.findByProps({ 'aria-label': 'Add Fire to the category list' }).props.onClick());
      assert.deepEqual(calls[2], ['add', 'Fire', false]);
    } finally {
      globalThis.window = originalWindow;
    }
  });
});
