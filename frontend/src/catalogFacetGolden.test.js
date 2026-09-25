import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { expectedCatalogFacets } from './catalogFacetGolden.js';

// The backend stores these values so the Catalog can be searched in the
// database. If this fails, the browser's facet rules changed: rerun
// `node scripts/generate-catalog-facet-golden.mjs`, bump
// CATALOG_FACET_RULES_VERSION in backend/symgov_backend/catalog_facets.py, and
// update the port there until tests/test_catalog_facets.py passes too.
const golden = JSON.parse(
  readFileSync(new URL('../../tests/fixtures/catalog_browser_facets_golden.json', import.meta.url), 'utf8')
);

test('the golden file covers a useful spread of rows', () => {
  assert.ok(golden.cases.length >= 15);
});

for (const entry of golden.cases) {
  test(`browser facet rules still match the golden file: ${entry.name}`, () => {
    assert.deepEqual(expectedCatalogFacets(entry.input), entry.expected);
  });
}
