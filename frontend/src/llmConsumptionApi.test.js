import test from 'node:test';
import assert from 'node:assert/strict';

const fetchCalls = [];
let responsePayload = { ledger: { status: 'available' } };

globalThis.document = { querySelector() { return null; } };
globalThis.window = {
  location: { hostname: 'admin.example.test', origin: 'https://admin.example.test', protocol: 'https:' },
  SYMGOV_API_ROOT: 'https://api.example.test/api/v1',
  SYMGOV_CONFIG: {}
};
globalThis.fetch = async (url, options = {}) => {
  fetchCalls.push({ url, options });
  return { ok: true, status: 200, async text() { return JSON.stringify(responsePayload); } };
};

const { fetchAdminLlmUsage } = await import('./api.js');

test('consumption requests send each selected period and UTC anchor with no-store credentials', async () => {
  fetchCalls.length = 0;
  await fetchAdminLlmUsage('week', '2026-08-01');
  await fetchAdminLlmUsage('mtd', '2026-08-15');

  assert.deepEqual(fetchCalls, [
    {
      url: 'https://api.example.test/api/v1/admin/llm/usage?period=week&anchor=2026-08-01',
      options: { credentials: 'include', cache: 'no-store', headers: {} }
    },
    {
      url: 'https://api.example.test/api/v1/admin/llm/usage?period=mtd&anchor=2026-08-15',
      options: { credentials: 'include', cache: 'no-store', headers: {} }
    }
  ]);
});

test('consumption response is exposed to the report renderer', async () => {
  responsePayload = { startUtc: '2026-08-01T00:00:00Z', ledger: { status: 'available' } };
  const result = await fetchAdminLlmUsage('day', '2026-08-01');
  assert.equal(result.ok, true);
  assert.deepEqual(result.usage, responsePayload);
});
