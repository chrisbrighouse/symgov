import test from 'node:test';
import assert from 'node:assert/strict';

import {
  CATALOG_VIEW,
  SET_VIEW,
  buildCatalogSearchQuery,
  defaultCatalogView,
  defaultSortForView,
  facetOptionsForView,
  facetValueLabel,
  groupBySetGroup,
  hasActiveFilters,
  normalizeCatalogView,
  preferenceOptionsFor,
  searchSeededCatalog,
  setSourceSummary
} from './catalogSearch.js';

test('the Catalog tab query leaves out Set filters, so shared filters never break it', () => {
  const params = buildCatalogSearchQuery({
    view: CATALOG_VIEW,
    projectId: 'p-1',
    query: '  valve ',
    facetFilters: { catalogCategories: ['Valves', 'Pumps'], setGroup: ['Valves'], paletteSource: ['set'] },
    columnFilters: { name: 'gate', pack: '  ' },
    showFavourites: true,
    sort: { key: 'setOrder', direction: 'asc' },
    preferredFormats: ['svg', ''],
    page: 2
  });
  assert.equal(params.get('scope'), 'catalog');
  // On the Catalog tab the Project marks rows in its active set.
  assert.equal(params.get('projectId'), 'p-1');
  assert.equal(params.get('q'), 'valve');
  assert.deepEqual(params.getAll('catalogCategories'), ['Valves', 'Pumps']);
  assert.deepEqual(params.getAll('setGroup'), []);
  assert.deepEqual(params.getAll('paletteSource'), []);
  assert.equal(params.get('column.name'), 'gate');
  assert.equal(params.get('column.pack'), null);
  assert.equal(params.get('favourites'), 'true');
  // Set order means nothing on the Catalog tab.
  assert.equal(params.get('sort'), 'id');
  assert.deepEqual(params.getAll('preferredFormats'), ['SVG']);
  assert.equal(params.get('page'), '2');
  assert.equal(params.get('pageSize'), '60');
});

test('the Set tab query carries the Project, the Set filters and set order', () => {
  const params = buildCatalogSearchQuery({
    view: SET_VIEW,
    projectId: 'p-1',
    facetFilters: { setGroup: ['Valves'], paletteSource: ['organization_wide'] },
    sort: defaultSortForView(SET_VIEW)
  });
  assert.equal(params.get('scope'), 'set');
  assert.equal(params.get('projectId'), 'p-1');
  assert.deepEqual(params.getAll('setGroup'), ['Valves']);
  assert.deepEqual(params.getAll('paletteSource'), ['organization_wide']);
  assert.equal(params.get('sort'), 'setOrder');
  assert.equal(params.get('q'), null);
});

test('the same inputs always build the same query string', () => {
  const inputs = { view: CATALOG_VIEW, columnFilters: { pack: 'a', name: 'b' } };
  assert.equal(buildCatalogSearchQuery(inputs).toString(), buildCatalogSearchQuery(inputs).toString());
});

test('the Set tab opens first only when a Project and a set are active', () => {
  assert.equal(defaultCatalogView({ selectedProject: { id: 'p-1' }, activeSet: { code: 'SET' } }), SET_VIEW);
  assert.equal(defaultCatalogView({ selectedProject: { id: 'p-1' }, activeSet: null }), CATALOG_VIEW);
  assert.equal(defaultCatalogView({ selectedProject: null, activeSet: null }), CATALOG_VIEW);
  assert.equal(normalizeCatalogView('set'), SET_VIEW);
  assert.equal(normalizeCatalogView('bogus'), '');
});

test('facet options keep ticked values the results no longer contain, at zero', () => {
  const options = facetOptionsForView(
    CATALOG_VIEW,
    { catalogCategories: [{ value: 'Valves', count: 2 }] },
    { catalogCategories: ['Valves', 'Pumps'], setGroup: ['Valves'] }
  );
  const categories = options.find((option) => option.key === 'catalogCategories');
  assert.deepEqual(categories.values, [{ value: 'Valves', count: 2 }, { value: 'Pumps', count: 0 }]);
  assert.equal(categories.label, 'Category');
  // Set-only filters are not offered on the Catalog tab.
  assert.equal(options.some((option) => option.key === 'setGroup'), false);
  const setOptions = facetOptionsForView(SET_VIEW, {}, {});
  assert.deepEqual(setOptions.slice(0, 2).map((option) => option.label), ['Source', 'Set group']);
  assert.equal(facetValueLabel('paletteSource', 'set'), 'In this set');
  assert.equal(facetValueLabel('paletteSource', 'organization_wide'), 'Organization-wide');
});

test('active filters are recognised, so an empty set is told apart from filtered-out results', () => {
  assert.equal(hasActiveFilters({}), false);
  assert.equal(hasActiveFilters({ query: '  ' }), false);
  assert.equal(hasActiveFilters({ facetFilters: { pack: [] } }), false);
  assert.equal(hasActiveFilters({ facetFilters: { pack: ['A'] } }), true);
  assert.equal(hasActiveFilters({ columnFilters: { name: 'x' } }), true);
  assert.equal(hasActiveFilters({ showFavourites: true }), true);
});

test('set groups follow the order the server sent', () => {
  const item = (id, groupName) => ({ id, paletteEntry: { groupName } });
  const groups = groupBySetGroup([item('a', 'Valves'), item('b', 'Valves'), item('c', 'Pumps'), item('d', null)]);
  assert.deepEqual(groups.map((group) => [group.name, group.items.map((entry) => entry.id)]), [
    ['Valves', ['a', 'b']],
    ['Pumps', ['c']],
    ['Ungrouped', ['d']]
  ]);
});

test('the Set tab summary names the reason and splits set items from organization-wide ones', () => {
  const facets = { paletteSource: [{ value: 'set', count: 20 }, { value: 'organization_wide', count: 4 }] };
  assert.deepEqual(
    setSourceSummary({ activeSet: { code: 'SET-PID' }, reason: 'project_default' }, facets),
    { reason: 'Project default', counts: '20 from SET-PID, 4 organization-wide' }
  );
  assert.deepEqual(
    setSourceSummary({ activeSet: null, reason: 'none' }, { paletteSource: [{ value: 'organization_wide', count: 3 }] }),
    { reason: null, counts: '3 organization-wide' }
  );
});

test('the seeded search answers in the server shape, with counts that ignore their own filter', () => {
  const symbols = [
    { id: 'a', name: 'Gate valve', category: 'valve', discipline: 'piping', downloads: ['a.dxf'] },
    { id: 'b', name: 'Globe valve', category: 'valve', discipline: 'piping', downloads: ['b.svg'] },
    { id: 'c', name: 'Pump', category: 'pump', discipline: 'mechanical', downloads: ['c.dxf'] }
  ];
  const getField = (symbol, key) => String(symbol[key] || '');
  const params = buildCatalogSearchQuery({
    view: CATALOG_VIEW,
    facetFilters: { catalogCategories: ['Valves'] },
    sort: { key: 'name', direction: 'desc' },
    pageSize: 1
  });
  const result = searchSeededCatalog(symbols, params, { getField });
  assert.equal(result.total, 2);
  assert.deepEqual(result.items.map((symbol) => symbol.id), ['b']);
  const categories = Object.fromEntries(result.facets.catalogCategories.map((entry) => [entry.value, entry.count]));
  assert.equal(categories.Valves, 2);
  assert.equal(categories.Pumps, 1);
});

test('preference options read counted facet values, and plain-string fallbacks, as { value, count }', () => {
  const options = facetOptionsForView(CATALOG_VIEW, {
    catalogDisciplines: [{ value: 'Process', count: 7 }, { value: 'Electrical', count: 3 }]
  }, {});
  assert.deepEqual(preferenceOptionsFor(options, 'catalogDisciplines', { limit: 1 }), [{ value: 'Process', count: 7 }]);
  // No format values yet: the plain-string fallback is offered without counts.
  assert.deepEqual(
    preferenceOptionsFor(options, 'availableFormats', { fallback: ['DXF', 'SVG'] }),
    [{ value: 'DXF', count: null }, { value: 'SVG', count: null }]
  );
  assert.deepEqual(preferenceOptionsFor(options, 'catalogCategories'), []);
});
