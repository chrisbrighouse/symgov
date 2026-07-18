import test from 'node:test';
import assert from 'node:assert/strict';

const fetchCalls = [];
let responsePayload = { items: [{ id: 'smoke-detector', isFavourite: true }] };

globalThis.document = {
  querySelector() {
    return null;
  }
};
globalThis.window = {
  location: {
    hostname: 'catalog.example.test',
    origin: 'https://catalog.example.test',
    protocol: 'https:'
  },
  SYMGOV_API_ROOT: 'https://api.example.test/api/v1',
  SYMGOV_CONFIG: {}
};
globalThis.fetch = async (url, options = {}) => {
  fetchCalls.push({ url, options });
  return {
    ok: true,
    status: 200,
    async text() {
      return JSON.stringify(responsePayload);
    }
  };
};

const { fetchPublishedSymbols, updateCatalogFavourite } = await import('./api.js');

test('Catalog reload requests account-scoped favourite state with session credentials', async () => {
  fetchCalls.length = 0;
  responsePayload = { items: [{ id: 'smoke-detector', isFavourite: true }] };

  const result = await fetchPublishedSymbols();

  assert.equal(result.ok, true);
  assert.equal(result.items[0].isFavourite, true);
  assert.deepEqual(fetchCalls, [
    {
      url: 'https://api.example.test/api/v1/published/symbols',
      options: {
        credentials: 'include',
        cache: 'no-store',
        headers: {}
      }
    }
  ]);
});

test('Catalog favourite mutations match the authenticated backend contract', async () => {
  fetchCalls.length = 0;
  responsePayload = { symbolId: 'server-symbol-id', isFavourite: true };

  const added = await updateCatalogFavourite('smoke detector/1', true);
  responsePayload = { symbolId: 'server-symbol-id', isFavourite: false };
  const removed = await updateCatalogFavourite('smoke detector/1', false);

  assert.deepEqual(added, { symbolId: 'server-symbol-id', isFavourite: true });
  assert.deepEqual(removed, { symbolId: 'server-symbol-id', isFavourite: false });
  assert.deepEqual(
    fetchCalls.map(({ url, options }) => ({ url, method: options.method, credentials: options.credentials })),
    [
      {
        url: 'https://api.example.test/api/v1/published/favourites/smoke%20detector%2F1',
        method: 'PUT',
        credentials: 'include'
      },
      {
        url: 'https://api.example.test/api/v1/published/favourites/smoke%20detector%2F1',
        method: 'DELETE',
        credentials: 'include'
      }
    ]
  );
});
