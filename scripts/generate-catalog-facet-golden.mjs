// Regenerates tests/fixtures/catalog_browser_facets_golden.json from the
// browser's own Catalog facet rules, for the cases listed in its "inputs".
//
//   node scripts/generate-catalog-facet-golden.mjs
//
// The backend's port (backend/symgov_backend/catalog_facets.py) is checked
// against this file by tests/test_catalog_facets.py, and the browser rules by
// frontend/src/catalogFacetGolden.test.js. After changing either copy of the
// rules, rerun this script, bump CATALOG_FACET_RULES_VERSION in the backend,
// and make both suites pass.
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { expectedCatalogFacets } from '../frontend/src/catalogFacetGolden.js';

const goldenPath = resolve(dirname(fileURLToPath(import.meta.url)), '../tests/fixtures/catalog_browser_facets_golden.json');
const golden = JSON.parse(readFileSync(goldenPath, 'utf8'));
golden.cases = golden.cases.map((entry) => ({ name: entry.name, input: entry.input, expected: expectedCatalogFacets(entry.input) }));
writeFileSync(goldenPath, `${JSON.stringify(golden, null, 2)}\n`);
console.log(`Wrote ${golden.cases.length} cases to ${goldenPath}`);
