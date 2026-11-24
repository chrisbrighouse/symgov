import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import VisualMatchPanel from './VisualMatchPanel.js';
import { VISUAL_MATCH_DISCLOSURE } from './visualMatch.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;
const listeners = {};
globalThis.document = {
  addEventListener: (name, fn) => { listeners[name] = fn; },
  removeEventListener: (name) => { delete listeners[name]; }
};
globalThis.URL.createObjectURL = () => 'blob:test';
globalThis.URL.revokeObjectURL = () => {};

const png = { type: 'image/png', size: 4, arrayBuffer: async () => new Uint8Array([1, 2, 3, 4]).buffer };
const options = [{ key: 'catalogDisciplines', label: 'Discipline', values: [{ value: 'Piping', count: 3 }] }];

function text(node) {
  return JSON.stringify(node.toJSON());
}

function render(props) {
  let renderer;
  act(() => { renderer = TestRenderer.create(createElement(VisualMatchPanel, props)); });
  return renderer;
}

const pasteImage = () => act(() => {
  listeners.paste({ target: { tagName: 'DIV' }, clipboardData: { items: [{ kind: 'file', type: 'image/png', getAsFile: () => png }] }, preventDefault() {} });
});

test('the disclosure is shown before anything is sent, and Find matches waits for an image', () => {
  const renderer = render({ filterOptions: options, match: async () => { throw new Error('must not be called'); } });
  assert.ok(text(renderer).includes(VISUAL_MATCH_DISCLOSURE.slice(0, 40)));
  const find = renderer.root.findAll((n) => n.type === 'button' && n.children.includes('Find matches'))[0];
  assert.equal(find.props.disabled, true);
});

test('pasting an image, then Find matches, sends the base64 body with the ticked filters and shows bands', async () => {
  const sent = [];
  const response = {
    groups: [
      { rank: 1, band: 'likely', symbols: [{ symbolId: 's1', displayId: '0003-12', name: 'Gate valve' }, { symbolId: 's2', displayId: '0003-13', name: 'Gate valve B' }] },
      { rank: 2, band: 'possible', symbols: [{ symbolId: 's3', displayId: '0004-01', name: 'Globe valve' }] }
    ],
    degraded: false, warnings: [], strongerMatchOutsideFilters: true, symbolsSearched: 120, notice: ''
  };
  const opened = [];
  const renderer = render({ filterOptions: options, match: async (body) => { sent.push(body); return response; }, onOpenSymbol: (s) => opened.push(s.displayId) });
  pasteImage();
  const box = renderer.root.findAll((n) => n.type === 'input' && n.props.type === 'checkbox')[0];
  act(() => box.props.onChange());
  const find = renderer.root.findAll((n) => n.type === 'button' && n.children.includes('Find matches'))[0];
  assert.equal(find.props.disabled, false);
  await act(async () => { await find.props.onClick(); });

  assert.deepEqual(sent[0], { imageBase64: 'AQIDBA==', filters: { catalogDisciplines: ['Piping'] }, includeWeak: false, limit: 10 });
  const out = text(renderer);
  assert.ok(out.includes('1. Likely match · 2 interchangeable symbols') && out.includes('2. Possible match'));
  assert.ok(out.includes('0003-12') && out.includes('closer match outside your filters') && out.includes('Searched 120 symbols'));
  renderer.root.findAll((n) => n.type === 'button' && n.props['aria-label']?.startsWith('Open details for 0004-01'))[0].props.onClick();
  assert.deepEqual(opened, ['0004-01']);
});

test('an empty result says no close match was found, not that the symbol is missing', async () => {
  const renderer = render({ match: async () => ({ groups: [], degraded: false, warnings: [], strongerMatchOutsideFilters: false, symbolsSearched: 5, notice: 'No close match found among the symbols you can search.' }) });
  pasteImage();
  await act(async () => { await renderer.root.findAll((n) => n.type === 'button' && n.children.includes('Find matches'))[0].props.onClick(); });
  assert.ok(text(renderer).includes('No close match found among the symbols you can search.'));
});

test('a failed request shows a specific message and keeps the image so the person can retry', async () => {
  const failure = Object.assign(new Error('x'), { status: 503 });
  const renderer = render({ match: async () => { throw failure; } });
  pasteImage();
  const find = () => renderer.root.findAll((n) => n.type === 'button' && n.children.includes('Find matches'))[0];
  await act(async () => { await find().props.onClick(); });
  assert.ok(text(renderer).includes('busy or not ready'));
  assert.equal(find().props.disabled, false);
});

test('a wrong file type is refused with a message and nothing is sent', () => {
  const renderer = render({ match: async () => { throw new Error('must not be called'); } });
  act(() => {
    listeners.paste({ target: { tagName: 'DIV' }, clipboardData: { items: [{ kind: 'file', type: 'image/gif', getAsFile: () => ({ type: 'image/gif', size: 5 }) }] }, preventDefault() {} });
  });
  assert.ok(text(renderer).includes('Only PNG and JPEG images can be matched.'));
});

test('pasting while typing in a field is left alone', () => {
  const renderer = render({ match: async () => ({}) });
  let prevented = false;
  act(() => { listeners.paste({ target: { tagName: 'INPUT' }, clipboardData: { items: [{ kind: 'file', type: 'image/png', getAsFile: () => png }] }, preventDefault() { prevented = true; } }); });
  assert.equal(prevented, false);
  assert.ok(!text(renderer).includes('The image you are matching'));
});
