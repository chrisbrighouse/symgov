import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import { useSymbolViewBeacon, viewReferenceFor } from './useSymbolViewBeacon.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Probe({ symbol, record }) {
  useSymbolViewBeacon(symbol, { record });
  return null;
}

test('a view is recorded under the symbol id, public or private', () => {
  assert.equal(viewReferenceFor({ symbolId: 'uuid-1', id: 'valve' }), 'uuid-1');
  assert.equal(viewReferenceFor({ id: 'valve' }), 'valve');
  assert.equal(viewReferenceFor(null), '');
});

test('opening Details records once per symbol, closing it records nothing', async () => {
  const seen = [];
  const record = (reference) => seen.push(reference);
  let renderer;
  await act(async () => { renderer = TestRenderer.create(createElement(Probe, { symbol: null, record })); });
  assert.deepEqual(seen, []);
  await act(async () => { renderer.update(createElement(Probe, { symbol: { symbolId: 'a' }, record })); });
  await act(async () => { renderer.update(createElement(Probe, { symbol: { symbolId: 'a', name: 'edited' }, record })); });
  assert.deepEqual(seen, ['a']);
  await act(async () => { renderer.update(createElement(Probe, { symbol: { symbolId: 'b' }, record })); });
  assert.deepEqual(seen, ['a', 'b']);
  await act(async () => { renderer.update(createElement(Probe, { symbol: null, record })); });
  assert.deepEqual(seen, ['a', 'b']);
});
