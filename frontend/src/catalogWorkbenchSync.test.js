import test from 'node:test';
import assert from 'node:assert/strict';

const fetchCalls = [];
let responsePayload = {};
let responseOk = true;

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
    ok: responseOk,
    status: responseOk ? 200 : 500,
    async text() {
      return JSON.stringify(responsePayload);
    }
  };
};

const { fetchCatalogWorkbench, saveCatalogWorkbenchSection } = await import('./api.js');
const {
  LEGACY_CATALOG_STORAGE_KEYS,
  clearLegacyCatalogStorage,
  createCatalogWorkbenchSaver
} = await import('./catalogWorkbenchSync.js');

function deferred() {
  let resolve;
  const promise = new Promise((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

test('the Catalog workbench is loaded for the signed-in account with session credentials', async () => {
  fetchCalls.length = 0;
  responseOk = true;
  responsePayload = { preferences: { formats: ['DXF'] }, savedViews: [], clipboard: [], updatedAt: null };

  const result = await fetchCatalogWorkbench();

  assert.equal(result.ok, true);
  assert.deepEqual(result.state.preferences, { formats: ['DXF'] });
  assert.equal(fetchCalls[0].url, 'https://api.example.test/api/v1/published/workbench');
  assert.equal(fetchCalls[0].options.credentials, 'include');
});

test('a failed workbench load is reported, not treated as an empty workbench', async () => {
  responseOk = false;
  responsePayload = { detail: 'Service unavailable.' };

  const result = await fetchCatalogWorkbench();

  responseOk = true;
  assert.equal(result.ok, false);
  assert.equal(result.state, undefined);
});

test('each workbench section is saved to its own route', async () => {
  fetchCalls.length = 0;
  responseOk = true;
  responsePayload = {};

  await saveCatalogWorkbenchSection('preferences', { formats: ['DXF'] });
  await saveCatalogWorkbenchSection('savedViews', [{ id: 'view-1', name: 'DXF' }]);
  await saveCatalogWorkbenchSection('clipboard', [{ id: '0003-12' }]);

  assert.deepEqual(
    fetchCalls.map(({ url, options }) => ({ url, method: options.method, body: JSON.parse(options.body) })),
    [
      { url: 'https://api.example.test/api/v1/published/workbench/preferences', method: 'PUT', body: { formats: ['DXF'] } },
      { url: 'https://api.example.test/api/v1/published/workbench/saved-views', method: 'PUT', body: { items: [{ id: 'view-1', name: 'DXF' }] } },
      { url: 'https://api.example.test/api/v1/published/workbench/clipboard', method: 'PUT', body: { items: [{ id: '0003-12' }] } }
    ]
  );
  await assert.rejects(() => saveCatalogWorkbenchSection('favourites', []), /Unknown Catalog workbench section/);
});

test('a rejected save throws so the Catalog can tell the user', async () => {
  responseOk = false;
  responsePayload = { detail: 'Request validation failed.' };

  await assert.rejects(() => saveCatalogWorkbenchSection('clipboard', []), /Request validation failed/);
  responseOk = true;
});

test('the legacy browser-wide keys are removed, never read', () => {
  const removed = [];
  const storage = {
    getItem() {
      throw new Error('legacy Catalog storage must not be read');
    },
    removeItem(key) {
      removed.push(key);
    }
  };

  clearLegacyCatalogStorage(storage);
  clearLegacyCatalogStorage(null);

  assert.deepEqual(removed, [
    'symgov.catalog.preferences.v1',
    'symgov.catalog.savedViews.v1',
    'symgov.catalog.clipboard.v1'
  ]);
  assert.deepEqual(LEGACY_CATALOG_STORAGE_KEYS, removed);
});

test('a burst of changes to one section becomes one save of the latest value', async () => {
  const saves = [];
  const saver = createCatalogWorkbenchSaver({
    delayMs: 5,
    save: async (section, value) => {
      saves.push([section, value]);
    }
  });

  saver.schedule('preferences', { formats: ['SVG'] });
  saver.schedule('preferences', { formats: ['DXF'] });
  saver.schedule('clipboard', ['0003-12']);
  await new Promise((resolve) => setTimeout(resolve, 20));

  assert.deepEqual(saves, [
    ['preferences', { formats: ['DXF'] }],
    ['clipboard', ['0003-12']]
  ]);
});

test('a newer save waits for the in-flight one, so it always lands last', async () => {
  const saves = [];
  const firstSave = deferred();
  const saver = createCatalogWorkbenchSaver({
    delayMs: 0,
    save: async (section, value) => {
      saves.push(value);
      if (saves.length === 1) {
        await firstSave.promise;
      }
    }
  });

  saver.schedule('clipboard', ['first']);
  await new Promise((resolve) => setTimeout(resolve, 5));
  saver.schedule('clipboard', ['second']);
  saver.schedule('clipboard', ['third']);
  await new Promise((resolve) => setTimeout(resolve, 5));
  assert.deepEqual(saves, [['first']]);

  firstSave.resolve();
  await saver.flush();

  assert.deepEqual(saves, [['first'], ['third']]);
});

test('flush sends a pending save immediately and reports failures', async () => {
  const errors = [];
  const saver = createCatalogWorkbenchSaver({
    delayMs: 60_000,
    save: async () => {
      throw new Error('offline');
    },
    onError: (section, error) => errors.push([section, error.message])
  });

  saver.schedule('savedViews', []);
  await saver.flush();

  assert.deepEqual(errors, [['savedViews', 'offline']]);
});
