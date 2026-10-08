import { useEffect, useState } from 'react';

import { fetchPublishedSymbolDetails } from './api.js';
import { normalizeSymbolDetails } from './symbolDetailsModel.js';

const EMPTY = normalizeSymbolDetails(null);

// What the details response is asked for: public symbols, by catalog ID.
// Organization-private symbols have no such response, so none is requested.
export function detailsReferenceFor(symbol) {
  if (!symbol || symbol.source === 'organization_private') return '';
  return symbol.catalogSymbolId || '';
}

// Loads the details response for the symbol the Details view shows. `status`
// is 'loading', 'ready', 'unavailable' (nothing to show for this symbol: a
// private one, or a 404) or 'error'. `details` is always a complete,
// defaulted object, so the view never branches on missing fields. Only the
// reply for the symbol now shown is applied.
export function useSymbolDetails(symbol, { fetchDetails = fetchPublishedSymbolDetails } = {}) {
  const reference = detailsReferenceFor(symbol);
  const [state, setState] = useState({ reference: '', status: 'loading', details: EMPTY });

  useEffect(() => {
    if (!reference) return undefined;
    let cancelled = false;
    fetchDetails(reference)
      .then((payload) => {
        if (!cancelled) setState({ reference, status: 'ready', details: normalizeSymbolDetails(payload) });
      })
      .catch((error) => {
        if (!cancelled) {
          setState({ reference, status: error?.status === 404 ? 'unavailable' : 'error', details: EMPTY });
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reference]);

  if (!reference) return { status: symbol ? 'unavailable' : 'loading', details: EMPTY };
  if (state.reference !== reference) return { status: 'loading', details: EMPTY };
  return { status: state.status, details: state.details };
}
