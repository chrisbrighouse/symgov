function symbolKey(symbol = {}) {
  return String(symbol.id || symbol.symbolId || '').trim();
}

function symbolName(symbol = {}) {
  return String(
    symbol.displayName ||
    symbol.display_name ||
    symbol.symbolDisplayId ||
    symbol.symbol_display_id ||
    symbol.name ||
    symbolKey(symbol) ||
    'symbol'
  ).trim();
}

export function applyFavouriteState(items = [], symbolId, isFavourite) {
  const targetId = String(symbolId || '').trim();
  return (items || []).map((symbol) =>
    symbolKey(symbol) === targetId ? { ...symbol, isFavourite: Boolean(isFavourite) } : symbol
  );
}

export function catalogItemsForDisplay(state = {}, fallbackItems = []) {
  return Array.isArray(state.items) ? state.items : fallbackItems;
}

export function applySequencedFavouriteState(
  items,
  symbolId,
  isFavourite,
  operationSequence,
  latestOperationSequence
) {
  return operationSequence === latestOperationSequence
    ? applyFavouriteState(items, symbolId, isFavourite)
    : items;
}

export function applySequencedCatalogLoadState(
  currentState,
  loadedState,
  loadSequence,
  latestLoadSequence,
  mutationSequenceAtStart,
  latestMutationSequence
) {
  return loadSequence === latestLoadSequence && mutationSequenceAtStart === latestMutationSequence
    ? loadedState
    : currentState;
}

export function buildFavouriteToggle(items = [], symbolId) {
  const targetId = String(symbolId || '').trim();
  const symbol = (items || []).find((item) => symbolKey(item) === targetId);
  const isFavourite = !Boolean(symbol?.isFavourite);

  return {
    isFavourite,
    optimisticItems: applyFavouriteState(items, targetId, isFavourite),
    rollbackItems: items
  };
}

export function favouriteButtonLabel(symbol, isFavourite) {
  return isFavourite
    ? `Remove ${symbolName(symbol)} from Favourites`
    : `Add ${symbolName(symbol)} to Favourites`;
}

export function filterCatalogSymbols(
  items = [],
  { query = '', columnFilters = {}, facetFilters = {}, showFavourites = false } = {},
  { buildSearchText, getField, getFacetValues } = {}
) {
  const normalizedQuery = String(query || '').trim().toLowerCase();

  return (items || []).filter((symbol) => {
    if (showFavourites && !Boolean(symbol?.isFavourite)) {
      return false;
    }

    const searchText = String(buildSearchText?.(symbol) || '').toLowerCase();
    if (normalizedQuery && !searchText.includes(normalizedQuery)) {
      return false;
    }

    const matchesColumns = Object.entries(columnFilters).every(([key, value]) => {
      const normalizedValue = String(value || '').trim().toLowerCase();
      if (!normalizedValue) {
        return true;
      }
      return String(getField?.(symbol, key) || '').toLowerCase().includes(normalizedValue);
    });
    if (!matchesColumns) {
      return false;
    }

    return Object.entries(facetFilters).every(([key, selected]) => {
      if (!selected?.length) {
        return true;
      }
      const values = (getFacetValues?.(symbol, key) || []).map((value) => String(value || '').trim());
      return selected.some((value) => values.includes(value));
    });
  });
}
