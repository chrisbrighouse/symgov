import { buildCatalogSearchText, catalogTaxonomyForSymbol } from './catalogWorkbench.js';

// What the backend's facet store must reproduce for one served Catalog row.
// Shared by the golden-file generator and the frontend check of that file.
export function expectedCatalogFacets(symbol) {
  return {
    taxonomy: catalogTaxonomyForSymbol(symbol),
    searchText: buildCatalogSearchText(symbol)
  };
}
