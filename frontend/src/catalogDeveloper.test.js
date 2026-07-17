import test from 'node:test';
import assert from 'node:assert/strict';

import {
  buildCatalogCodeExample,
  buildCatalogDeveloperHeaders,
  catalogExampleBodyForEndpoint,
  materializeCatalogEndpoint,
  normalizeCatalogEndpoint,
  resolveDeveloperCitation,
  sandboxOperationForEndpoint
} from './catalogDeveloper.js';

const request = {
  baseUrl: 'https://apps.example.test/api/v1',
  method: 'POST',
  path: '/catalog/search',
  apiKeyPlaceholder: 'YOUR_CATALOG_API_KEY',
  body: { query: 'smoke detector', limit: 5 }
};

test('builds copyable curl example without embedding a real key', () => {
  const output = buildCatalogCodeExample({ ...request, language: 'curl' });
  assert.match(output, /curl/);
  assert.match(output, /Authorization: Bearer YOUR_CATALOG_API_KEY/);
  assert.match(output, /smoke detector/);
  assert.doesNotMatch(output, /symgov_live_/);
});

test('builds TypeScript fetch example with a bearer header', () => {
  const output = buildCatalogCodeExample({ ...request, language: 'typescript' });
  assert.match(output, /fetch\(/);
  assert.match(output, /process\.env\.SYMGOV_CATALOG_API_KEY/);
  assert.match(output, /Content-Type/);
});

test('builds Python requests example with timeout and error handling', () => {
  const output = buildCatalogCodeExample({
    ...request,
    language: 'python',
    body: { query: 'smoke detector', includePreview: true, cursor: null }
  });
  assert.match(output, /requests\.post/);
  assert.match(output, /timeout=/);
  assert.match(output, /raise_for_status/);
  assert.match(output, /SYMGOV_CATALOG_API_KEY/);
  assert.match(output, /True/);
  assert.match(output, /None/);
  assert.doesNotMatch(output, /\btrue\b|\bnull\b/);
});

test('builds C# HttpClient example for drawing review integrations', () => {
  const output = buildCatalogCodeExample({ ...request, language: 'csharp' });
  assert.match(output, /HttpClient/);
  assert.match(output, /AuthenticationHeaderValue/);
  assert.match(output, /JsonSerializer\.Deserialize<JsonElement>/);
  assert.doesNotMatch(output, /JsonContent\.Create\(\{/);
  assert.match(output, /SYMGOV_CATALOG_API_KEY/);
});

for (const path of [
  '/catalog/symbols/0003-12/thumbnail',
  '/catalog/symbols/0003-12/preview'
]) {
  test(`builds binary-aware TypeScript example for ${path}`, () => {
    const output = buildCatalogCodeExample({ ...request, language: 'typescript', method: 'GET', path, body: undefined });
    assert.match(output, /response\.(blob|arrayBuffer)\(\)/);
    assert.doesNotMatch(output, /response\.json\(\)/);
  });

  test(`builds binary-aware Python example for ${path}`, () => {
    const output = buildCatalogCodeExample({ ...request, language: 'python', method: 'GET', path, body: undefined });
    assert.match(output, /response\.content/);
    assert.doesNotMatch(output, /response\.json\(\)/);
  });

  test(`builds binary-aware C# example for ${path}`, () => {
    const output = buildCatalogCodeExample({ ...request, language: 'csharp', method: 'GET', path, body: undefined });
    assert.match(output, /ReadAsByteArrayAsync|ReadAsStreamAsync/);
    assert.doesNotMatch(output, /ReadFromJsonAsync/);
  });
}

test('preserves JSON decoding for non-binary examples', () => {
  assert.match(buildCatalogCodeExample({ ...request, language: 'typescript' }), /response\.json\(\)/);
  assert.match(buildCatalogCodeExample({ ...request, language: 'python' }), /response\.json\(\)/);
  assert.match(buildCatalogCodeExample({ ...request, language: 'csharp' }), /ReadFromJsonAsync/);
});

test('keeps curl binary endpoints as raw-output requests', () => {
  const output = buildCatalogCodeExample({
    ...request,
    language: 'curl',
    method: 'GET',
    path: '/catalog/symbols/0003-12/preview',
    body: undefined
  });
  assert.match(output, /curl --request GET/);
  assert.doesNotMatch(output, /json|JSON/);
});

test('normalizes only allowlisted Catalog endpoints', () => {
  assert.equal(normalizeCatalogEndpoint('/api/v1/catalog/symbols'), '/catalog/symbols');
  assert.equal(normalizeCatalogEndpoint('/catalog/search'), '/catalog/search');
  assert.throws(() => normalizeCatalogEndpoint('/admin/users'), /Catalog endpoint/);
  assert.throws(() => normalizeCatalogEndpoint('https://evil.invalid/'), /Catalog endpoint/);
});

test('maps supported reference endpoints to read-only sandbox operations', () => {
  assert.equal(sandboxOperationForEndpoint('GET', '/catalog/capabilities'), 'capabilities');
  assert.equal(sandboxOperationForEndpoint('POST', '/catalog/search'), 'contextual_search');
  assert.equal(sandboxOperationForEndpoint('POST', '/catalog/symbols/0003-12/feedback'), null);
});

test('developer headers keep the key in the request header only', () => {
  assert.deepEqual(buildCatalogDeveloperHeaders('temporary-key'), {
    Authorization: 'Bearer temporary-key',
    'Content-Type': 'application/json'
  });
});

test('builds endpoint-specific POST examples', () => {
  assert.deepEqual(catalogExampleBodyForEndpoint('POST', '/api/v1/catalog/search'), {
    query: 'smoke detector near stairwell',
    context: {
      application: 'Customer Portal',
      drawingType: 'life_safety_plan',
      preferredFormats: ['PNG']
    },
    limit: 10
  });
  assert.deepEqual(catalogExampleBodyForEndpoint('POST', '/api/v1/catalog/ed/query'), {
    message: 'Find smoke detector symbols for a life safety plan',
    mode: 'auto',
    context: { application: 'Customer Portal', drawingType: 'life_safety_plan' },
    limit: 10
  });
  assert.deepEqual(catalogExampleBodyForEndpoint('POST', '/api/v1/catalog/symbols/{symbol_ref}/feedback'), {
    kind: 'comment',
    message: 'This preview is clear in our drawing review workflow.',
    context: { application: 'Customer Portal' }
  });
  assert.equal(catalogExampleBodyForEndpoint('GET', '/api/v1/catalog/symbols'), undefined);
});

test('materializes either backend or frontend symbol placeholders', () => {
  assert.equal(materializeCatalogEndpoint('/catalog/symbols/{symbol_ref}/preview'), '/catalog/symbols/0003-12/preview');
  assert.equal(materializeCatalogEndpoint('/catalog/symbols/{symbolRef}/preview'), '/catalog/symbols/0003-12/preview');
});

test('maps only allowlisted developer citations to page sections or support', () => {
  assert.deepEqual(resolveDeveloperCitation('developer://guides/search'), { href: '#reference', label: 'Search guide' });
  assert.deepEqual(resolveDeveloperCitation({ href: 'developer://guides/sandbox', title: 'Sandbox details' }), { href: '#sandbox', label: 'Sandbox details' });
  assert.deepEqual(resolveDeveloperCitation('developer://support'), { href: '/support', label: 'Support' });
  for (const unsafe of ['javascript:alert(1)', 'https://evil.invalid', '/admin', 'developer://guides/not-real']) {
    assert.equal(resolveDeveloperCitation(unsafe), null);
  }
});
