import { absoluteHashRoute, routeForCatalogSymbol } from './catalogRoutes.js';

// URL state of the full-page symbol Details view. The view lives on the
// Catalog route, `#/standards?symbol=<catalogId>&view=details&tab=<tab>`, so
// the Catalog stays mounted underneath it and Back finds it as it was.

export const DETAILS_VIEW = 'details';
export const DEFAULT_DETAILS_TAB = 'classification';

export const DETAILS_TABS = [
  { id: 'classification', label: 'Classification & mappings' },
  { id: 'details', label: 'Details' },
  { id: 'connections', label: 'Connection points' },
  { id: 'labels', label: 'Labels & states' },
  { id: 'source', label: 'Source & rights' },
  { id: 'history', label: 'History' },
  { id: 'comments', label: 'Comments' }
];

const TAB_IDS = new Set(DETAILS_TABS.map((tab) => tab.id));

// An unknown tab in a pasted link opens the default one.
export function normalizeDetailsTab(value) {
  return TAB_IDS.has(value) ? value : DEFAULT_DETAILS_TAB;
}

// The tab that is actually shown: the requested one when it exists for this
// symbol, otherwise the default, otherwise the first that does.
export function resolveDetailsTab(requested, availableIds = []) {
  const wanted = normalizeDetailsTab(requested);
  if (availableIds.includes(wanted)) return wanted;
  if (availableIds.includes(DEFAULT_DETAILS_TAB)) return DEFAULT_DETAILS_TAB;
  return availableIds[0] || DEFAULT_DETAILS_TAB;
}

// `open` needs both the view marker and a symbol: `view=details` alone is just
// a Catalog link with a stray parameter.
export function parseDetailsState(searchParams) {
  const symbol = (searchParams.get('symbol') || '').trim();
  const open = searchParams.get('view') === DETAILS_VIEW && symbol !== '';
  return { open, symbol, tab: normalizeDetailsTab(searchParams.get('tab')) };
}

// The Catalog's own tab (`view=set|catalog`) shares the `view` parameter, so
// it is set aside while the Details view is open and put back on return.
export function detailsSearchParams(current, { symbol, tab = DEFAULT_DETAILS_TAB }) {
  const next = new URLSearchParams(current);
  next.set('symbol', symbol);
  next.set('view', DETAILS_VIEW);
  next.set('tab', normalizeDetailsTab(tab));
  return next;
}

export function catalogSearchParams(current, { symbol = '', view = '' } = {}) {
  const next = new URLSearchParams(current);
  next.delete('tab');
  if (view) {
    next.set('view', view);
  } else {
    next.delete('view');
  }
  if (symbol) {
    next.set('symbol', symbol);
  } else {
    next.delete('symbol');
  }
  return next;
}

// The reference a Details link uses: the human-readable catalog ID, falling
// back to the row's own id for organization-private symbols that have none.
export function detailsReference(symbol) {
  return symbol?.catalogSymbolId || symbol?.id || '';
}

export function detailsPath(reference, tab = DEFAULT_DETAILS_TAB) {
  const params = detailsSearchParams(new URLSearchParams(), { symbol: reference, tab });
  return `/standards?${params.toString()}`;
}

// `#/s/<catalogId>` opens the Details view. An ID the route would not accept
// has no target, so the caller falls back to the Catalog.
export function shortLinkTarget(catalogSymbolId) {
  const route = routeForCatalogSymbol(catalogSymbolId);
  if (!route) return null;
  const params = detailsSearchParams(new URLSearchParams(), { symbol: catalogSymbolId });
  params.delete('tab');
  return `/standards?${params.toString()}`;
}

// The shareable address of a symbol's Details view, `<origin>/#/s/<id>`.
export function symgovDetailsUrl(origin, catalogSymbolId) {
  const route = routeForCatalogSymbol(catalogSymbolId);
  return route ? absoluteHashRoute(origin, route) : null;
}

// Where Previous/Next lead from the open symbol, over the Catalog's current
// results. A symbol in several packs is listed once per entry, so repeats are
// dropped; the position is among distinct symbols.
export function resultNeighbours(items, activeId) {
  const seen = new Set();
  const distinct = [];
  (items || []).forEach((item) => {
    if (item && !seen.has(item.id)) {
      seen.add(item.id);
      distinct.push(item);
    }
  });
  const index = distinct.findIndex((item) => item.id === activeId);
  if (index < 0) {
    return { index: -1, position: 0, count: distinct.length, previous: null, next: null };
  }
  return {
    index,
    position: index + 1,
    count: distinct.length,
    previous: index > 0 ? distinct[index - 1] : null,
    next: index < distinct.length - 1 ? distinct[index + 1] : null
  };
}
