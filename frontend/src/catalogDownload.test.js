import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildCatalogDownloadOptions,
  catalogDownloadAvailability,
  catalogDownloadResultMessage,
  parseCatalogDownloadFilename,
  requestCatalogDownload
} from './catalogDownload.js';


test('download format chooser contains the union of downloadable selected-symbol formats', () => {
  const symbols = [
    {
      id: 'motor',
      downloadAssets: [
        { format: 'PNG', object_key: 'motor.png' },
        { format: 'SVG', object_key: 'motor.svg' }
      ]
    },
    {
      id: 'valve',
      downloadAssets: [
        { format: 'DXF', object_key: 'valve.dxf' },
        { format: 'png', object_key: 'valve.png' }
      ]
    }
  ];

  assert.deepEqual(buildCatalogDownloadOptions(symbols), ['DXF', 'SVG', 'PNG']);
});


test('download availability uses total selected and rejects more than ten', () => {
  assert.deepEqual(catalogDownloadAvailability(0, 'PNG'), {
    enabled: false,
    label: 'Download (0)',
    reason: 'Select at least one symbol.'
  });
  assert.deepEqual(catalogDownloadAvailability(10, 'PNG'), {
    enabled: true,
    label: 'Download (10)',
    reason: ''
  });
  assert.deepEqual(catalogDownloadAvailability(11, 'PNG'), {
    enabled: false,
    label: 'Download (11)',
    reason: 'Select no more than 10 symbols.'
  });
  assert.equal(catalogDownloadAvailability(3, '').enabled, false);
});


test('download response helpers preserve the server filename and report skipped symbols', () => {
  assert.equal(
    parseCatalogDownloadFilename('attachment; filename="symgov-png-20260720-143012.zip"'),
    'symgov-png-20260720-143012.zip'
  );
  assert.equal(
    catalogDownloadResultMessage({ downloadedCount: 2, selectedCount: 3, skippedSymbols: ['00023-5'], format: 'PNG' }),
    'Downloaded 2 of 3 selected symbols. PNG is not available for: 00023-5.'
  );
});


test('download request calls the batch Catalog API and returns blob metadata', async () => {
  const calls = [];
  const result = await requestCatalogDownload({
    apiRoot: '/api/v1',
    symbolIds: ['motor', 'valve'],
    format: 'PNG',
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return new Response(new Blob(['zip-bytes'], { type: 'application/zip' }), {
        status: 200,
        headers: {
          'Content-Disposition': 'attachment; filename="symgov-png-20260720-143012.zip"',
          'X-Symgov-Selected-Count': '2',
          'X-Symgov-Downloaded-Count': '1',
          'X-Symgov-Skipped-Symbols': '00023-5'
        }
      });
    }
  });

  assert.deepEqual(calls, [{
    url: '/api/v1/catalog/symbols/download',
    options: {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ symbolIds: ['motor', 'valve'], format: 'PNG' })
    }
  }]);
  assert.equal(result.filename, 'symgov-png-20260720-143012.zip');
  assert.equal(result.selectedCount, 2);
  assert.equal(result.downloadedCount, 1);
  assert.deepEqual(result.skippedSymbols, ['00023-5']);
  assert.equal(await result.blob.text(), 'zip-bytes');
});
