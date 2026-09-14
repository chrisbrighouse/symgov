import test from 'node:test';
import assert from 'node:assert/strict';

const fetchCalls = [];
let responsePayload = {};

globalThis.document = {
  querySelector() {
    return null;
  },
};
globalThis.window = {
  location: {
    hostname: 'catalog.example.test',
    origin: 'https://catalog.example.test',
    protocol: 'https:',
  },
  SYMGOV_API_ROOT: 'https://api.example.test/api/v1',
  SYMGOV_CONFIG: {},
};
globalThis.fetch = async (url, options = {}) => {
  fetchCalls.push({ url, options });
  return {
    ok: true,
    status: 200,
    async text() {
      return JSON.stringify(responsePayload);
    },
  };
};

const { replaceSymbolSetItems, searchSymbolSetBuilder } = await import('./api.js');

test('Builder search forwards every supported filter and pagination value', async () => {
  fetchCalls.length = 0;
  responsePayload = { items: [], page: 3, pageSize: 100, total: 0 };

  await searchSymbolSetBuilder({
    q: 'alarm',
    category: 'fire detection',
    discipline: 'life safety',
    format: 'svg',
    page: 3,
    pageSize: 100,
  });

  assert.equal(
    fetchCalls[0].url,
    'https://api.example.test/api/v1/org/me/symbol-sets/builder-search?q=alarm&category=fire+detection&discipline=life+safety&format=svg&page=3&pageSize=100',
  );
});

test('full replacement sends the authoritative ETag in the header and body', async () => {
  fetchCalls.length = 0;
  responsePayload = { items: [], page: 1, pageSize: 200, total: 0, etag: 'etag-next' };

  await replaceSymbolSetItems('set/1', [], 'etag-current');

  assert.equal(fetchCalls[0].url, 'https://api.example.test/api/v1/org/me/symbol-sets/set%2F1/items');
  assert.equal(fetchCalls[0].options.headers['If-Match'], 'etag-current');
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), { items: [], etag: 'etag-current' });
});
