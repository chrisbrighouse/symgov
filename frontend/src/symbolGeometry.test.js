import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { createServer } from 'vite';
import {
  connectionPointSummary,
  connectionPointText,
  hasGeometryDetails,
  labelTemplateList,
  stateVariantCount,
  stateVariantList,
  symbolAttribution
} from './symbolGeometry.js';

// A stand-in for an imported valve: three piping points, one label slot, two
// states (one with no condition stated), and a made-up attribution sentence.
const valve = {
  id: 'S-1',
  stateVariants: [
    { index: 2, condition: "ValvePosition = '1NCAngle'", url: '/api/v1/published/symbols/S-1/state-variants/2' },
    { index: 1, condition: "ValvePosition = 'NC'", url: '/api/v1/published/symbols/S-1/state-variants/1' },
    { index: 3, condition: null, url: '/api/v1/published/symbols/S-1/state-variants/3' }
  ],
  payload: {
    geometry: {
      connection_points: [
        { index: 1, kinds: ['piping'] },
        { index: 2, kinds: ['piping'] },
        { index: 3, kinds: ['piping'] },
        { index: 4, kinds: ['signal'] }
      ],
      label_slots: [{ label_index: 'A', lines: 3, template: '<ObjectDisplayName>\n<NominalDiameter>' }]
    },
    disc: { label_templates: { A: 'register wording' } },
    dexpi: { attribution: '  Stand-in attribution for a test.  ' }
  }
};

test('connection points are counted and grouped by kind in a fixed order', () => {
  const summary = connectionPointSummary(valve);
  assert.equal(summary.count, 4);
  assert.deepEqual(summary.kinds, [{ kind: 'piping', count: 3 }, { kind: 'signal', count: 1 }]);
  assert.equal(connectionPointText(summary), '4 (3 piping, 1 signal)');
});

test('label slots list with the template the slot stores, falling back to the register', () => {
  assert.deepEqual(labelTemplateList(valve), [
    { label: 'A', lines: 3, template: '<ObjectDisplayName>\n<NominalDiameter>' }
  ]);
  const bare = { payload: { geometry: { label_slots: [{ label_index: 'B', lines: 1 }] }, disc: { label_templates: { B: 'from the register' } } } };
  assert.equal(labelTemplateList(bare)[0].template, 'from the register');
});

test('state variants sort by option number and keep a missing condition missing', () => {
  const variants = stateVariantList(valve);
  assert.deepEqual(variants.map((variant) => variant.index), [1, 2, 3]);
  assert.equal(variants[2].condition, '');
  assert.equal(stateVariantCount([valve, { id: 'other' }]), 3);
});

test('a symbol without any of it yields nothing and claims nothing', () => {
  const plain = { id: 'S-2', payload: { name: 'Plain' } };
  assert.equal(connectionPointSummary(plain).count, 0);
  assert.deepEqual(labelTemplateList(plain), []);
  assert.deepEqual(stateVariantList(plain), []);
  assert.equal(symbolAttribution(plain), '');
  assert.equal(hasGeometryDetails(plain), false);
  assert.equal(hasGeometryDetails(valve), true);
  assert.equal(symbolAttribution(valve), 'Stand-in attribution for a test.');
});

test('the detail section shows the counts, templates, state gallery and attribution', async () => {
  const vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  try {
    const { default: Details } = await vite.ssrLoadModule('/frontend/src/SymbolGeometryDetails.jsx');
    const markup = renderToStaticMarkup(createElement(Details, { symbol: valve }));
    assert.match(markup, /Connection points/);
    assert.match(markup, /4 \(3 piping, 1 signal\)/);
    assert.match(markup, /Label slots/);
    assert.match(markup, /Label A/);
    assert.match(markup, /States \(3\)/);
    assert.match(markup, /ValvePosition = &#x27;NC&#x27;/);
    assert.match(markup, /No condition stated by the source/);
    assert.match(markup, /src="\/api\/v1\/published\/symbols\/S-1\/state-variants\/1"/);
    assert.match(markup, /Stand-in attribution for a test\./);
    assert.equal(renderToStaticMarkup(createElement(Details, { symbol: { id: 'S-2', payload: {} } })), '');
  } finally {
    await vite.close();
  }
});
