import { useCallback, useEffect, useRef, useState } from 'react';

import { searchPublishedSymbols } from './api.js';

const EMPTY_RESULT = { items: [], total: 0, facets: {}, project: null, activeSet: null, reason: null };

// Loads one Catalog tab from the database search, a page at a time.
//
// `query` is the tab's URLSearchParams without `page`; whenever its string
// changes, the results start again from page 1, after `delayMs` so typing
// does not send a request per keystroke. Until the new page arrives the old
// results stay in place with `loading` set, so the view can fade them rather
// than flash empty. Only the reply to the latest request is applied: a slow
// reply to an older one is dropped. A failed request keeps the last good
// results and sets `error`.
//
// `reloadKey` forces a fresh search without changing the query, for example
// when the Project or Symbol Set selection changes. `enabled: false` stops
// all requests and clears the results.
export function useCatalogSearch(query, {
  enabled = true,
  reloadKey = 0,
  delayMs = 250,
  search = searchPublishedSymbols
} = {}) {
  const queryString = query ? query.toString() : '';
  const [result, setResult] = useState(EMPTY_RESULT);
  const [loading, setLoading] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState('');
  const [loaded, setLoaded] = useState(false);
  const [retryToken, setRetryToken] = useState(0);
  const sequenceRef = useRef(0);
  const pageRef = useRef(1);
  const searchRef = useRef(search);
  searchRef.current = search;
  const firstRequestRef = useRef(true);

  const withPage = useCallback((page) => {
    const params = new URLSearchParams(queryString);
    params.set('page', String(page));
    return params;
  }, [queryString]);

  useEffect(() => {
    if (!enabled) {
      sequenceRef.current += 1;
      setResult(EMPTY_RESULT);
      setLoading(false);
      setLoadingMore(false);
      setError('');
      setLoaded(false);
      firstRequestRef.current = true;
      return undefined;
    }
    const sequence = sequenceRef.current + 1;
    sequenceRef.current = sequence;
    setLoading(true);
    const run = async () => {
      try {
        const next = await searchRef.current(withPage(1));
        if (sequence !== sequenceRef.current) return;
        pageRef.current = 1;
        setResult({ ...EMPTY_RESULT, ...next, items: next.items || [] });
        setError('');
        setLoaded(true);
      } catch (err) {
        if (sequence !== sequenceRef.current) return;
        setError(err?.message || 'Catalog search failed.');
      } finally {
        if (sequence === sequenceRef.current) {
          setLoading(false);
          setLoadingMore(false);
        }
      }
    };
    // The first search runs at once; later ones wait for typing to settle.
    const wait = firstRequestRef.current ? 0 : delayMs;
    firstRequestRef.current = false;
    const timer = setTimeout(run, wait);
    return () => clearTimeout(timer);
  }, [enabled, queryString, reloadKey, retryToken, delayMs, withPage]);

  const hasMore = result.items.length < result.total;

  const loadMore = useCallback(async () => {
    if (!enabled || loading || loadingMore || !hasMore) return;
    const sequence = sequenceRef.current;
    const nextPage = pageRef.current + 1;
    setLoadingMore(true);
    try {
      const next = await searchRef.current(withPage(nextPage));
      if (sequence !== sequenceRef.current) return;
      pageRef.current = nextPage;
      setResult((current) => ({
        ...current,
        total: next.total,
        facets: next.facets || current.facets,
        items: [...current.items, ...(next.items || [])]
      }));
      setError('');
    } catch (err) {
      if (sequence !== sequenceRef.current) return;
      setError(err?.message || 'More results could not be loaded.');
    } finally {
      if (sequence === sequenceRef.current) setLoadingMore(false);
    }
  }, [enabled, hasMore, loading, loadingMore, withPage]);

  const retry = useCallback(() => setRetryToken((current) => current + 1), []);

  // For optimistic changes to loaded rows, such as a favourite toggled on a
  // card, without a new search.
  const updateItems = useCallback((updater) => {
    setResult((current) => ({ ...current, items: updater(current.items) }));
  }, []);

  return {
    ...result,
    loading,
    loadingMore,
    loaded,
    error,
    hasMore,
    loadMore,
    retry,
    updateItems
  };
}
