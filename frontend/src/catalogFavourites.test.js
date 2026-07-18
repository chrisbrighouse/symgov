import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { renderToStaticMarkup } from 'react-dom/server';

import FavouriteButton from './FavouriteButton.js';
import FavouriteFilter from './FavouriteFilter.js';
import {
  applyFavouriteState,
  applySequencedCatalogLoadState,
  applySequencedFavouriteState,
  buildFavouriteToggle,
  catalogItemsForDisplay,
  favouriteButtonLabel,
  filterCatalogSymbols
} from './catalogFavourites.js';

const symbols = [
  { id: 'symbol-1', displayName: '0001-1', isFavourite: false },
  { id: 'symbol-2', displayName: '0001-2', isFavourite: true }
];

test('builds an optimistic favourite toggle with a rollback snapshot', () => {
  const toggle = buildFavouriteToggle(symbols, 'symbol-1');

  assert.equal(toggle.isFavourite, true);
  assert.deepEqual(toggle.optimisticItems, [
    { id: 'symbol-1', displayName: '0001-1', isFavourite: true },
    symbols[1]
  ]);
  assert.deepEqual(toggle.rollbackItems, symbols);
});

test('applies favourite state consistently to every copy of a symbol', () => {
  const duplicateSymbols = [
    { id: 'symbol-1', isFavourite: false },
    { id: 'symbol-1', isFavourite: false },
    { id: 'symbol-2', isFavourite: false }
  ];

  assert.deepEqual(applyFavouriteState(duplicateSymbols, 'symbol-1', true), [
    { id: 'symbol-1', isFavourite: true },
    { id: 'symbol-1', isFavourite: true },
    duplicateSymbols[2]
  ]);
});

test('successful empty live catalog data remains authoritative instead of falling back to seeds', () => {
  assert.deepEqual(
    catalogItemsForDisplay({ loading: false, mode: 'live', items: [] }, symbols),
    []
  );
  assert.deepEqual(
    catalogItemsForDisplay({ loading: false, mode: 'fallback', items: symbols }, []),
    symbols
  );
});

test('stale favourite completions cannot overwrite newer per-symbol state', () => {
  const newer = applyFavouriteState(symbols, 'symbol-1', false);

  assert.strictEqual(
    applySequencedFavouriteState(newer, 'symbol-1', true, 4, 5),
    newer
  );
  assert.equal(
    applySequencedFavouriteState(newer, 'symbol-1', true, 5, 5)[0].isFavourite,
    true
  );
});

test('stale catalog loads cannot overwrite state changed by a newer mutation', () => {
  const current = { mode: 'live', items: [{ id: 'symbol-1', isFavourite: true }] };
  const staleLoad = { mode: 'live', items: [{ id: 'symbol-1', isFavourite: false }] };

  assert.strictEqual(
    applySequencedCatalogLoadState(current, staleLoad, 2, 2, 7, 8),
    current
  );
  assert.strictEqual(
    applySequencedCatalogLoadState(current, staleLoad, 1, 2, 8, 8),
    current
  );
});

test('uses UK-spelled accessible labels for both favourite states', () => {
  assert.equal(favouriteButtonLabel({ displayName: '0001-1' }, false), 'Add 0001-1 to Favourites');
  assert.equal(favouriteButtonLabel({ name: 'Gate valve' }, true), 'Remove Gate valve from Favourites');
});

test('Show Favourites composes with search, column, category, discipline, format and other facet filters', () => {
  const catalogSymbols = [
    {
      id: 'matching-favourite',
      name: 'Smoke detector',
      pack: 'Life safety',
      facets: {
        catalogCategories: ['Fire Alarm Devices'],
        catalogDisciplines: ['Electrical'],
        availableFormats: ['DXF'],
        symbolFamily: ['Detector']
      },
      isFavourite: true
    },
    {
      id: 'matching-not-favourite',
      name: 'Smoke detector',
      pack: 'Life safety',
      facets: {
        catalogCategories: ['Fire Alarm Devices'],
        catalogDisciplines: ['Electrical'],
        availableFormats: ['DXF'],
        symbolFamily: ['Detector']
      },
      isFavourite: false
    },
    {
      id: 'wrong-format-favourite',
      name: 'Smoke detector',
      pack: 'Life safety',
      facets: {
        catalogCategories: ['Fire Alarm Devices'],
        catalogDisciplines: ['Electrical'],
        availableFormats: ['PNG'],
        symbolFamily: ['Detector']
      },
      isFavourite: true
    }
  ];
  const filterState = {
    query: 'smoke',
    columnFilters: { pack: 'life' },
    facetFilters: {
      catalogCategories: ['Fire Alarm Devices'],
      catalogDisciplines: ['Electrical'],
      availableFormats: ['DXF'],
      symbolFamily: ['Detector']
    },
    showFavourites: true
  };
  const resolvers = {
    buildSearchText: (symbol) => `${symbol.id} ${symbol.name}`,
    getField: (symbol, key) => symbol[key],
    getFacetValues: (symbol, key) => symbol.facets[key] || []
  };

  assert.deepEqual(
    filterCatalogSymbols(catalogSymbols, filterState, resolvers).map((symbol) => symbol.id),
    ['matching-favourite']
  );
});

test('clearing Show Favourites restores the wider filtered set without changing saved favourite state', () => {
  const catalogSymbols = [
    { id: 'favourite', name: 'Gate valve', isFavourite: true },
    { id: 'not-favourite', name: 'Gate valve', isFavourite: false },
    { id: 'different-search', name: 'Pump', isFavourite: true }
  ];
  const snapshot = structuredClone(catalogSymbols);
  const resolvers = {
    buildSearchText: (symbol) => symbol.name,
    getField: (symbol, key) => symbol[key],
    getFacetValues: () => []
  };

  assert.deepEqual(
    filterCatalogSymbols(catalogSymbols, { query: 'gate', showFavourites: true }, resolvers).map((symbol) => symbol.id),
    ['favourite']
  );
  assert.deepEqual(
    filterCatalogSymbols(catalogSymbols, { query: 'gate', showFavourites: false }, resolvers).map((symbol) => symbol.id),
    ['favourite', 'not-favourite']
  );
  assert.deepEqual(catalogSymbols, snapshot);
});

test('renders a native keyboard-operable star button with selected-state semantics', () => {
  const markup = renderToStaticMarkup(FavouriteButton({
    symbol: symbols[1],
    pressed: true,
    pending: false,
    onToggle() {}
  }));

  assert.match(markup, /<button/);
  assert.match(markup, /type="button"/);
  assert.match(markup, /aria-pressed="true"/);
  assert.match(markup, /aria-label="Remove 0001-2 from Favourites"/);
  assert.match(markup, /class="catalog-favourite-button selected"/);
  assert.match(markup, />★<\/span>/);
});

test('favourite button can be disabled while authoritative live data is loading', () => {
  const markup = renderToStaticMarkup(FavouriteButton({
    symbol: symbols[0],
    disabled: true,
    onToggle() {}
  }));

  assert.match(markup, /disabled=""/);
});

test('renders Show Favourites as an accessible first-class Catalog filter', () => {
  const markup = renderToStaticMarkup(FavouriteFilter({ checked: true, onChange() {} }));

  assert.match(markup, /type="checkbox"/);
  assert.match(markup, /checked=""/);
  assert.match(markup, />Show Favourites</);
});

test('favourite star styles include a visible keyboard focus treatment', async () => {
  const styles = await readFile(new URL('./styles.css', import.meta.url), 'utf8');

  assert.match(styles, /\.catalog-favourite-button:focus-visible\s*\{/);
  assert.match(styles, /outline:\s*[^;]+;/);
});

test('Catalog card and table views share the favourite control and persistence helper', async () => {
  const app = await readFile(new URL('./App.jsx', import.meta.url), 'utf8');
  const api = await readFile(new URL('./api.js', import.meta.url), 'utf8');

  assert.match(app, /import FavouriteButton from ['"]\.\/FavouriteButton\.js['"]/);
  assert.match(app, /updateCatalogFavourite/);
  assert.equal((app.match(/<FavouriteButton/g) || []).length, 2);
  assert.match(app, /role="alert"/);
  assert.match(api, /export async function updateCatalogFavourite/);
  assert.match(api, /\/published\/favourites\//);
});

test('Catalog disables favourite mutations until authoritative live loading settles', async () => {
  const app = await readFile(new URL('./App.jsx', import.meta.url), 'utf8');

  assert.match(app, /const standardsSymbols = catalogItemsForDisplay\(standardsState, symbols\)/);
  assert.match(app, /const favouriteMutationsEnabled = !standardsState\.loading && standardsState\.mode === 'live'/);
  assert.equal((app.match(/disabled=\{!favouriteMutationsEnabled/g) || []).length, 2);
  assert.match(app, /applySequencedFavouriteState/);
});

test('Catalog wires Show Favourites into the shared filtered result set used by card and table views', async () => {
  const app = await readFile(new URL('./App.jsx', import.meta.url), 'utf8');

  assert.match(app, /import FavouriteFilter from ['"]\.\/FavouriteFilter\.js['"]/);
  assert.match(app, /filterCatalogSymbols/);
  assert.match(app, /showFavourites/);
  assert.match(app, /<FavouriteFilter/);
  assert.equal((app.match(/visibleSymbols\.map/g) || []).length, 2);
});
