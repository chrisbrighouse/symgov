import test from 'node:test';
import assert from 'node:assert/strict';

import {
  addSymbolsToClipboard,
  applySavedCatalogView,
  buildCatalogFacetValues,
  buildCatalogSearchText,
  buildCatalogViewSnapshot,
  catalogTaxonomyForSymbol,
  normalizeCatalogCategory,
  normalizeCatalogDiscipline,
  removeSymbolFromClipboard,
  serializeCatalogPreferences
} from './catalogWorkbench.js';

const fireAlarmSymbol = {
  id: 'smoke-detector',
  displayName: '007F-2',
  name: 'Smoke Detector',
  category: 'symbol',
  discipline: 'Electrical',
  keywords: ['fire alarm', 'detector'],
  downloads: ['smoke-detector.dxf', 'smoke-detector.png'],
  downloadAssets: [
    { format: 'dxf', filename: 'smoke-detector.dxf' },
    { format: 'png', filename: 'smoke-detector.png' }
  ]
};

const valveSymbol = {
  id: 'gate-valve',
  displayName: '0002-4',
  name: 'Gate Valve',
  category: 'Gate Valves',
  discipline: 'process_instrumentation',
  keywords: ['P&ID', 'valve'],
  downloads: ['gate-valve.svg']
};

test('normalizes raw discipline values into professional Catalog groups', () => {
  assert.deepEqual(normalizeCatalogDiscipline('process_instrumentation'), ['Instrumentation & Controls', 'Piping / P&ID']);
  assert.deepEqual(normalizeCatalogDiscipline('general'), ['General / Annotation']);
  assert.deepEqual(normalizeCatalogDiscipline('Electrical'), ['Electrical']);
});

test('normalizes rough category values using symbol context', () => {
  assert.deepEqual(normalizeCatalogCategory('symbol', fireAlarmSymbol), ['Fire Alarm Devices', 'Sensors / Detectors', 'Drawing Symbols']);
  assert.deepEqual(normalizeCatalogCategory('Gate Valves', valveSymbol), ['Valves']);
  assert.deepEqual(normalizeCatalogCategory('symbol_sheet', {}), ['Drawing Symbols']);
});

test('derives use cases and available formats for a Catalog symbol', () => {
  const taxonomy = catalogTaxonomyForSymbol(fireAlarmSymbol);

  assert.deepEqual(taxonomy.disciplines, ['Electrical', 'Fire & Life Safety']);
  assert.deepEqual(taxonomy.categories, ['Fire Alarm Devices', 'Sensors / Detectors', 'Drawing Symbols']);
  assert.deepEqual(taxonomy.availableFormats, ['DXF', 'PNG']);
  assert.deepEqual(taxonomy.useCases, ['Insert into CAD drawing', 'Mark up / annotate drawing', 'Use in PDF/report']);
});

test('builds de-duplicated Catalog facet values across symbols', () => {
  const facets = buildCatalogFacetValues([fireAlarmSymbol, valveSymbol]);

  assert.ok(facets.disciplines.includes('Fire & Life Safety'));
  assert.ok(facets.disciplines.includes('Piping / P&ID'));
  assert.ok(facets.categories.includes('Valves'));
  assert.ok(facets.formats.includes('DXF'));
  assert.ok(facets.useCases.includes('Insert into CAD drawing'));
});

test('serializes Catalog preferences into a stable local-storage shape', () => {
  const preferences = serializeCatalogPreferences({
    disciplines: ['Electrical', '', 'Electrical'],
    categories: ['Fire Alarm Devices'],
    formats: ['dxf', 'PNG'],
    useCases: ['Insert into CAD drawing']
  });

  assert.deepEqual(preferences, {
    disciplines: ['Electrical'],
    categories: ['Fire Alarm Devices'],
    formats: ['DXF', 'PNG'],
    useCases: ['Insert into CAD drawing']
  });
});

test('captures and reapplies saved Catalog views', () => {
  const snapshot = buildCatalogViewSnapshot({
    name: 'Fire alarm DXF',
    query: 'detector',
    facetFilters: { catalogDiscipline: ['Fire & Life Safety'], format: ['DXF'] },
    preferredFormats: ['DXF']
  });

  assert.equal(snapshot.name, 'Fire alarm DXF');
  assert.equal(snapshot.query, 'detector');
  assert.deepEqual(applySavedCatalogView(snapshot), {
    query: 'detector',
    facetFilters: { catalogDiscipline: ['Fire & Life Safety'], format: ['DXF'] },
    preferredFormats: ['DXF']
  });
});

test('application clipboard adds unique symbols with available format metadata', () => {
  const clipboard = addSymbolsToClipboard([], [fireAlarmSymbol, fireAlarmSymbol, valveSymbol]);

  assert.equal(clipboard.length, 2);
  assert.deepEqual(clipboard[0], {
    id: 'smoke-detector',
    displayName: '007F-2',
    name: 'Smoke Detector',
    availableFormats: ['DXF', 'PNG']
  });
  assert.deepEqual(removeSymbolFromClipboard(clipboard, 'smoke-detector').map((item) => item.id), ['gate-valve']);
});

test('Catalog search text includes normalized taxonomy and format fields', () => {
  const text = buildCatalogSearchText(fireAlarmSymbol).toLowerCase();

  assert.ok(text.includes('fire & life safety'));
  assert.ok(text.includes('fire alarm devices'));
  assert.ok(text.includes('dxf'));
});
