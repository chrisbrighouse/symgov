import { useEffect } from 'react';

import { recordSymbolView } from './api.js';

// The reference a view is recorded under: the symbol's own id, which every
// symbol has, public or private.
export function viewReferenceFor(symbol) {
  if (!symbol) return '';
  return String(symbol.symbolId || symbol.id || '');
}

// Tells the server once each time the Details view opens on a symbol (the
// server drops repeats within five minutes). Passing null while the view is
// closed records nothing.
export function useSymbolViewBeacon(symbol, { record = recordSymbolView } = {}) {
  const reference = viewReferenceFor(symbol);
  useEffect(() => {
    if (reference) record(reference);
  }, [reference]);
}
