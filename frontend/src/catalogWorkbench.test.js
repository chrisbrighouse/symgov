import test from 'node:test';
import assert from 'node:assert/strict';

import {
  addSymbolsToClipboard,
  applySavedCatalogView,
  buildCatalogCardSummary,
  buildCatalogPreviewOptions,
  buildCatalogFacetValues,
  buildCatalogSearchText,
  buildCatalogViewSnapshot,
  catalogTaxonomyForSymbol,
  normalizeCatalogCategory,
  normalizeCatalogDiscipline,
  interpretEdCatalogPrompt,
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

test('builds compact card summaries for symbol browsing', () => {
  const summary = buildCatalogCardSummary(fireAlarmSymbol);

  assert.deepEqual(summary, {
    id: 'smoke-detector',
    displayId: '007F-2',
    name: 'Smoke Detector',
    categories: ['Fire Alarm Devices', 'Sensors / Detectors', 'Drawing Symbols'],
    disciplines: ['Electrical', 'Fire & Life Safety'],
    formats: ['DXF', 'PNG'],
    useCases: ['Insert into CAD drawing', 'Mark up / annotate drawing', 'Use in PDF/report'],
    hasPhotos: false,
    commentCount: 0
  });
});

test('builds format badges that identify the active preview and only enable browser-previewable formats', () => {
  const options = buildCatalogPreviewOptions({
    ...fireAlarmSymbol,
    availableFormats: ['DXF', 'PNG', 'SVG'],
    previewAsset: { format: 'png' },
    previewAssets: [{ format: 'png' }, { format: 'svg' }]
  }, 'SVG');

  assert.deepEqual(options, [
    { format: 'DXF', active: false, previewable: false },
    { format: 'SVG', active: true, previewable: true },
    { format: 'PNG', active: false, previewable: true }
  ]);
});

test('Ed guided search maps natural language to non-mutating Catalog filters', () => {
  const interpretation = interpretEdCatalogPrompt('Find fire alarm detector symbols I can insert into CAD as DXF');

  assert.deepEqual(interpretation.facetFilters, {
    catalogDisciplines: ['Fire & Life Safety'],
    catalogCategories: ['Fire Alarm Devices', 'Sensors / Detectors'],
    useCases: ['Insert into CAD drawing'],
    availableFormats: ['DXF']
  });
  assert.deepEqual(interpretation.preferredFormats, ['DXF']);
  assert.match(interpretation.query, /fire alarm detector/i);
  assert.equal(interpretation.mutatesRecords, false);
});

test('Ed guided search maps electrical switchgear and lighting categories', () => {
  const interpretation = interpretEdCatalogPrompt('Electrical switchgear or lighting symbols');

  assert.deepEqual(interpretation.facetFilters, {
    catalogDisciplines: ['Electrical'],
    catalogCategories: ['Switchgear / Distribution', 'Lighting']
  });
  assert.match(interpretation.searchQuery, /electrical/i);
  assert.equal(interpretation.mutatesRecords, false);
});

test('Ed guided search converts natural-language electrical requests into searchable query terms', () => {
  const interpretation = interpretEdCatalogPrompt('I need electrical symbols');

  assert.deepEqual(interpretation.facetFilters, {
    catalogDisciplines: ['Electrical']
  });
  assert.equal(interpretation.searchQuery, 'Electrical');
  assert.equal(interpretation.mutatesRecords, false);
});

test('Ed guided search maps motor requests to motors and drives filters', () => {
  const interpretation = interpretEdCatalogPrompt('I need motor symbols');

  assert.deepEqual(interpretation.facetFilters, {
    catalogDisciplines: ['Electrical'],
    catalogCategories: ['Motors / Drives']
  });
  assert.match(interpretation.searchQuery, /motors\s*\/\s*drives/i);
  assert.equal(interpretation.mutatesRecords, false);
});

test('Ed guided search maps mechanical pump report prompts to documentation-ready formats', () => {
  const interpretation = interpretEdCatalogPrompt('Mechanical pump symbols for reports');

  assert.deepEqual(interpretation.facetFilters, {
    catalogDisciplines: ['Mechanical'],
    catalogCategories: ['Pumps'],
    useCases: ['Use in PDF/report'],
    availableFormats: ['SVG', 'PNG', 'PDF']
  });
  assert.deepEqual(interpretation.preferredFormats, ['SVG', 'PNG', 'PDF']);
  assert.equal(interpretation.mutatesRecords, false);
});

test('Ed guided search keeps explicit report formats without adding implicit extras', () => {
  const interpretation = interpretEdCatalogPrompt('I need PNG or PDF symbols for marking up a fire alarm drawing');

  assert.deepEqual(interpretation.facetFilters, {
    catalogDisciplines: ['Fire & Life Safety'],
    catalogCategories: ['Fire Alarm Devices'],
    useCases: ['Mark up / annotate drawing', 'Use in PDF/report'],
    availableFormats: ['PNG', 'PDF']
  });
  assert.deepEqual(interpretation.preferredFormats, ['PNG', 'PDF']);
  assert.equal(interpretation.mutatesRecords, false);
});

test('Ed guided search treats drawing review prompts as non-mutating markup intent', () => {
  const interpretation = interpretEdCatalogPrompt('symbols for a drawing review');

  assert.deepEqual(interpretation.facetFilters, {
    useCases: ['Mark up / annotate drawing']
  });
  assert.equal(interpretation.mutatesRecords, false);
  assert.match(interpretation.explanation, /No records were changed/i);
});

test('Ed guided search never creates mutation commands for mutation-like wording', () => {
  const interpretation = interpretEdCatalogPrompt('rename all fire alarm symbols and send them for review');

  assert.equal(interpretation.mutatesRecords, false);
  assert.equal(Object.hasOwn(interpretation, 'command'), false);
  assert.equal(Object.hasOwn(interpretation, 'handoffPayload'), false);
  assert.match(interpretation.explanation, /No records were changed/i);
});
