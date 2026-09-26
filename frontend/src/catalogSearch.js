// Helpers for the Catalog page's two tabs, which search in the database
// (`GET /published/symbols/search`). Kept free of React so they can be tested
// directly.

import { filterCatalogSymbols } from './catalogFavourites.js';
import {
  buildCatalogSearchText,
  catalogTaxonomyForSymbol,
  sortSymbolsByPreferredFormats
} from './catalogWorkbench.js';

export const CATALOG_VIEW = 'catalog';
export const SET_VIEW = 'set';
export const SET_ORDER_SORT = 'setOrder';
export const CATALOG_PAGE_SIZE = 60;

// Filters the server knows for both tabs, and the two only the Set tab has.
export const SHARED_FACET_KEYS = [
  'catalogDisciplines',
  'catalogCategories',
  'useCases',
  'availableFormats',
  'pack',
  'symbolFamily'
];
export const SET_FACET_KEYS = ['paletteSource', 'setGroup'];

export const FACET_LABELS = {
  paletteSource: 'Source',
  setGroup: 'Set group',
  catalogDisciplines: 'Discipline',
  catalogCategories: 'Category',
  useCases: 'Use case',
  availableFormats: 'Format',
  pack: 'Pack',
  symbolFamily: 'Symbol family'
};

const PALETTE_SOURCE_LABELS = {
  set: 'In this set',
  organization_wide: 'Organization-wide'
};

export function facetValueLabel(key, value) {
  if (key === 'paletteSource') {
    return PALETTE_SOURCE_LABELS[value] || value;
  }
  return value;
}

export function facetKeysForView(view) {
  return view === SET_VIEW ? [...SET_FACET_KEYS, ...SHARED_FACET_KEYS] : SHARED_FACET_KEYS;
}

export function defaultSortForView(view) {
  return view === SET_VIEW ? { key: SET_ORDER_SORT, direction: 'asc' } : { key: 'id', direction: 'asc' };
}

// Which tab opens when the URL does not say: the Set tab when a Project is
// selected and a set resolves for it, otherwise the Catalog.
export function defaultCatalogView(context) {
  return context?.selectedProject?.id && context?.activeSet ? SET_VIEW : CATALOG_VIEW;
}

export function normalizeCatalogView(value) {
  return value === SET_VIEW || value === CATALOG_VIEW ? value : '';
}

// The request one tab sends, as a URLSearchParams. Filters that the tab does
// not have are left out (the server rejects Set filters on the Catalog tab),
// so filters can be shared between the tabs without breaking either.
export function buildCatalogSearchQuery({
  view = CATALOG_VIEW,
  projectId = '',
  query = '',
  facetFilters = {},
  columnFilters = {},
  showFavourites = false,
  sort = defaultSortForView(view),
  preferredFormats = [],
  page = 1,
  pageSize = CATALOG_PAGE_SIZE
} = {}) {
  const params = new URLSearchParams();
  params.set('scope', view === SET_VIEW ? 'set' : 'catalog');
  if (view === SET_VIEW && projectId) {
    params.set('projectId', projectId);
  }
  const trimmed = String(query || '').trim();
  if (trimmed) {
    params.set('q', trimmed);
  }
  for (const key of facetKeysForView(view)) {
    for (const value of facetFilters[key] || []) {
      const cleaned = String(value || '').trim();
      if (cleaned) {
        params.append(key, cleaned);
      }
    }
  }
  for (const key of Object.keys(columnFilters).sort()) {
    const value = String(columnFilters[key] || '').trim();
    if (value) {
      params.set(`column.${key}`, value);
    }
  }
  if (showFavourites) {
    params.set('favourites', 'true');
  }
  const sortKey = view !== SET_VIEW && sort?.key === SET_ORDER_SORT ? 'id' : sort?.key || 'id';
  params.set('sort', sortKey);
  params.set('direction', sort?.direction === 'desc' ? 'desc' : 'asc');
  for (const format of preferredFormats || []) {
    const cleaned = String(format || '').trim().toUpperCase();
    if (cleaned) {
      params.append('preferredFormats', cleaned);
    }
  }
  params.set('page', String(page));
  params.set('pageSize', String(pageSize));
  return params;
}

// The facet list the filter panel shows: every value the server counted, in
// the server's order, plus any ticked value the current results no longer
// contain, at zero, so it can still be unticked.
export function facetOptionsForView(view, serverFacets = {}, facetFilters = {}) {
  return facetKeysForView(view).map((key) => {
    const counted = Array.isArray(serverFacets[key]) ? serverFacets[key] : [];
    const seen = new Set(counted.map((entry) => entry.value));
    const values = [...counted];
    for (const ticked of facetFilters[key] || []) {
      if (!seen.has(ticked)) {
        values.push({ value: ticked, count: 0 });
      }
    }
    return { key, label: FACET_LABELS[key] || key, values };
  });
}

export function hasActiveFilters({ query = '', facetFilters = {}, columnFilters = {}, showFavourites = false } = {}) {
  return Boolean(
    String(query || '').trim()
    || showFavourites
    || Object.values(facetFilters).some((values) => (values || []).length)
    || Object.values(columnFilters).some((value) => String(value || '').trim())
  );
}

// Consecutive items that share a set group, in the order the server sent
// them. Only meaningful in set order; a column sort would scatter the groups.
export function groupBySetGroup(items = []) {
  const groups = [];
  for (const item of items) {
    const name = item?.paletteEntry?.groupName || 'Ungrouped';
    const last = groups[groups.length - 1];
    if (last && last.name === name) {
      last.items.push(item);
    } else {
      groups.push({ name, items: [item] });
    }
  }
  return groups;
}

// The line under the Set tab's pickers: why this set is active, and what the
// palette holds.
export function setSourceSummary(response, facets = {}) {
  const reasonLabels = {
    explicit: 'Your choice',
    user_preference: 'Your choice',
    project_default: 'Project default',
    organization_default: 'Organization default'
  };
  const counts = Object.fromEntries((facets.paletteSource || []).map((entry) => [entry.value, entry.count]));
  const fromSet = counts.set || 0;
  const organizationWide = counts.organization_wide || 0;
  const parts = [];
  if (response?.activeSet) {
    parts.push(`${fromSet} from ${response.activeSet.code}`);
  }
  if (organizationWide) {
    parts.push(`${organizationWide} organization-wide`);
  }
  return {
    reason: response?.activeSet ? reasonLabels[response.reason] || null : null,
    counts: parts.join(', ')
  };
}

function seededFacetValues(symbol, key) {
  const taxonomy = catalogTaxonomyForSymbol(symbol);
  if (key === 'catalogDisciplines') return taxonomy.disciplines;
  if (key === 'catalogCategories') return taxonomy.categories;
  if (key === 'useCases') return taxonomy.useCases;
  if (key === 'availableFormats') return taxonomy.availableFormats;
  if (key === 'pack') return symbol.pack ? [String(symbol.pack).trim()] : [];
  if (key === 'symbolFamily') {
    const family = String(symbol.symbolFamily || symbol.family || symbol.category || '').trim();
    return family ? [family] : [];
  }
  return [];
}

// The same answer shape as the server, over a list held in the browser. Used
// only when no API is configured (local development against seeded data).
export function searchSeededCatalog(symbols = [], params = new URLSearchParams(), { getField = (symbol, key) => String(symbol?.[key] || '') } = {}) {
  const facetFilters = {};
  for (const key of SHARED_FACET_KEYS) {
    const values = params.getAll(key);
    if (values.length) facetFilters[key] = values;
  }
  const columnFilters = {};
  for (const [name, value] of params.entries()) {
    if (name.startsWith('column.')) columnFilters[name.slice('column.'.length)] = value;
  }
  const request = {
    query: params.get('q') || '',
    facetFilters,
    columnFilters,
    showFavourites: params.get('favourites') === 'true'
  };
  const helpers = { buildSearchText: buildCatalogSearchText, getField, getFacetValues: seededFacetValues };
  const matching = filterCatalogSymbols(symbols, request, helpers);
  const sortKey = params.get('sort') || 'id';
  const direction = params.get('direction') === 'desc' ? -1 : 1;
  const sorted = [...matching].sort((left, right) => direction * getField(left, sortKey).localeCompare(getField(right, sortKey), undefined, { numeric: true, sensitivity: 'base' }));
  const ordered = sortSymbolsByPreferredFormats(sorted, params.getAll('preferredFormats'));

  const facets = {};
  for (const key of SHARED_FACET_KEYS) {
    const others = { ...request, facetFilters: { ...facetFilters, [key]: [] } };
    const counts = new Map();
    for (const symbol of symbols) {
      for (const value of seededFacetValues(symbol, key)) {
        if (!counts.has(value)) counts.set(value, 0);
      }
    }
    for (const symbol of filterCatalogSymbols(symbols, others, helpers)) {
      for (const value of seededFacetValues(symbol, key)) {
        counts.set(value, (counts.get(value) || 0) + 1);
      }
    }
    facets[key] = [...counts.entries()]
      .map(([value, count]) => ({ value, count }))
      .sort((left, right) => left.value.localeCompare(right.value));
  }

  const page = Math.max(1, Number(params.get('page') || 1));
  const pageSize = Math.max(1, Number(params.get('pageSize') || CATALOG_PAGE_SIZE));
  return {
    scope: 'catalog',
    items: ordered.slice((page - 1) * pageSize, page * pageSize),
    total: ordered.length,
    page,
    pageSize,
    facets,
    project: null,
    activeSet: null,
    reason: null
  };
}

