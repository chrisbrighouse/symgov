import test from 'node:test';
import assert from 'node:assert/strict';
import { buildRepresentations, downloadChoices, isViewableFormat, normalizeFormat } from './symbolRepresentations.js';
import { discValve, legacyPng, pilotSymbol } from './symbolDetailsFixtures.js';

test('formats are normalised and only raster and SVG are viewable', () => {
  assert.equal(normalizeFormat('.jpeg'), 'JPG');
  ['SVG', 'png', 'JPG', 'gif', 'webp'].forEach((format) => assert.equal(isViewableFormat(format), true, format));
  ['DXF', 'dwg', 'PDF', 'ZIP', ''].forEach((format) => assert.equal(isViewableFormat(format), false, format));
});

test('S-273 has its primary drawing and two states, the primary selected by default', () => {
  const { items, defaultKey } = buildRepresentations(discValve);
  assert.deepEqual(items.map((item) => [item.key, item.label, item.condition]), [
    ['asset-SVG', 'Primary drawing', ''],
    ['state-1', 'State 1', "ValvePosition = 'NC'"],
    ['state-2', 'State 2', "ValvePosition = '1NCAngle'"]
  ]);
  assert.equal(defaultKey, 'asset-SVG');
  assert.ok(items.every((item) => item.viewable && item.sharesPrimaryFrame));
  assert.equal(items[0].url, '/api/v1/published/symbols/S-273/preview?format=SVG');
  assert.equal(items[1].url, '/api/v1/published/symbols/S-273/state-variants/1');
});

test('a DXF is a download-only tile and is never the default', () => {
  const { items, defaultKey } = buildRepresentations(pilotSymbol);
  const dxf = items.find((item) => item.format === 'DXF');
  assert.equal(dxf.viewable, false);
  assert.equal(dxf.url, null);
  assert.equal(defaultKey, 'asset-SVG');
});

test('a DXF-only symbol has tiles but nothing to view', () => {
  const { items, defaultKey } = buildRepresentations({
    id: 'dxf-only',
    previewUrl: null,
    downloadAssets: [{ role: 'primary', format: 'dxf' }, { role: 'primary', format: 'dwg' }],
    stateVariants: []
  });
  assert.deepEqual(items.map((item) => item.viewable), [false, false]);
  assert.equal(defaultKey, '');
});

test('a legacy PNG symbol has one viewable tile that shares no overlay frame it cannot back up', () => {
  const { items, defaultKey } = buildRepresentations(legacyPng);
  assert.equal(items.length, 1);
  assert.equal(items[0].format, 'PNG');
  assert.equal(defaultKey, 'asset-PNG');
});

test('one primary tile per format, and the preview format wins the default', () => {
  const symbol = {
    id: 's',
    previewUrl: '/p',
    previewAsset: { format: 'png' },
    downloadAssets: [
      { role: 'primary', format: 'svg' },
      { role: 'primary', format: 'png' },
      { role: 'primary', format: 'png', filename: 'again.png' }
    ]
  };
  const { items, defaultKey } = buildRepresentations(symbol);
  assert.deepEqual(items.map((item) => item.key), ['asset-SVG', 'asset-PNG']);
  assert.equal(defaultKey, 'asset-PNG');
});

test('image URLs go through the resolver', () => {
  const { items } = buildRepresentations(discValve, { resolveUrl: (url) => `https://symgov.example${url}` });
  assert.equal(items[0].url, 'https://symgov.example/api/v1/published/symbols/S-273/preview?format=SVG');
  assert.equal(items[2].url, 'https://symgov.example/api/v1/published/symbols/S-273/state-variants/2');
});

test('the download picker offers the zip only for an SVG symbol with states', () => {
  assert.deepEqual(downloadChoices(discValve).map((choice) => choice.label), ['SVG', 'SVG + state variants (zip)']);
  const zip = downloadChoices(discValve)[1];
  assert.equal(zip.format, 'SVG');
  assert.equal(zip.includeStateVariants, true);
  assert.deepEqual(downloadChoices(pilotSymbol).map((choice) => choice.label), ['DXF', 'SVG']);
  assert.deepEqual(downloadChoices({ id: 'none' }), []);
});
