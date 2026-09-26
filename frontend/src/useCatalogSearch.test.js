import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { useCatalogSearch } from './useCatalogSearch.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((done, fail) => { resolve = done; reject = fail; });
  return { promise, resolve, reject };
}

function Probe({ query, options, seen }) {
  seen.current = useCatalogSearch(query, options);
  return null;
}

function page(ids, total, facets = {}) {
  return { items: ids.map((id) => ({ id })), total, facets };
}

function params(q) {
  return new URLSearchParams({ scope: 'catalog', q, pageSize: '2' });
}

async function flush() {
  await act(async () => { await new Promise((done) => setTimeout(done, 0)); });
}

describe('useCatalogSearch', () => {
  it('loads the first page, then appends further pages', async () => {
    const requests = [];
    const search = async (query) => {
      requests.push(query.get('page'));
      return query.get('page') === '1' ? page(['a', 'b'], 3) : page(['c'], 3);
    };
    const seen = { current: null };
    let renderer;
    await act(async () => { renderer = create(createElement(Probe, { query: params('valve'), options: { search, delayMs: 0 }, seen })); });
    await flush();
    assert.deepEqual(seen.current.items.map((item) => item.id), ['a', 'b']);
    assert.equal(seen.current.hasMore, true);
    await act(async () => seen.current.loadMore());
    assert.deepEqual(seen.current.items.map((item) => item.id), ['a', 'b', 'c']);
    assert.equal(seen.current.hasMore, false);
    assert.deepEqual(requests, ['1', '2']);
    await act(async () => renderer.unmount());
  });

  it('keeps the old results while a new search loads, and drops a slower earlier reply', async () => {
    const replies = { first: deferred(), slow: deferred(), fast: deferred() };
    const search = async (query) => replies[query.get('q')].promise;
    const seen = { current: null };
    let renderer;
    await act(async () => { renderer = create(createElement(Probe, { query: params('first'), options: { search, delayMs: 0 }, seen })); });
    await act(async () => { replies.first.resolve(page(['old'], 1)); });
    await flush();
    assert.deepEqual(seen.current.items.map((item) => item.id), ['old']);

    await act(async () => { renderer.update(createElement(Probe, { query: params('slow'), options: { search, delayMs: 0 }, seen })); });
    await flush();
    assert.equal(seen.current.loading, true);
    // The previous results stay in place while the new search runs.
    assert.deepEqual(seen.current.items.map((item) => item.id), ['old']);

    await act(async () => { renderer.update(createElement(Probe, { query: params('fast'), options: { search, delayMs: 0 }, seen })); });
    await flush();
    await act(async () => { replies.fast.resolve(page(['fast'], 1)); });
    await flush();
    assert.deepEqual(seen.current.items.map((item) => item.id), ['fast']);
    assert.equal(seen.current.loading, false);

    await act(async () => { replies.slow.resolve(page(['slow'], 1)); });
    await flush();
    assert.deepEqual(seen.current.items.map((item) => item.id), ['fast']);
    await act(async () => renderer.unmount());
  });

  it('keeps the last good results when a search fails, and retries on request', async () => {
    let fail = false;
    const search = async () => {
      if (fail) throw new Error('Catalog search failed.');
      return page(['a'], 1);
    };
    const seen = { current: null };
    let renderer;
    await act(async () => { renderer = create(createElement(Probe, { query: params('a'), options: { search, delayMs: 0 }, seen })); });
    await flush();
    fail = true;
    await act(async () => { renderer.update(createElement(Probe, { query: params('b'), options: { search, delayMs: 0 }, seen })); });
    await flush();
    assert.equal(seen.current.error, 'Catalog search failed.');
    assert.deepEqual(seen.current.items.map((item) => item.id), ['a']);
    fail = false;
    await act(async () => seen.current.retry());
    await flush();
    assert.equal(seen.current.error, '');
    await act(async () => renderer.unmount());
  });

  it('searches again when the reload key changes, as a new Symbol Set selection does', async () => {
    let calls = 0;
    const search = async () => { calls += 1; return page([`r${calls}`], 1); };
    const seen = { current: null };
    let renderer;
    const query = params('same');
    await act(async () => { renderer = create(createElement(Probe, { query, options: { search, delayMs: 0, reloadKey: 1 }, seen })); });
    await flush();
    await act(async () => { renderer.update(createElement(Probe, { query, options: { search, delayMs: 0, reloadKey: 2 }, seen })); });
    await flush();
    assert.equal(calls, 2);
    assert.deepEqual(seen.current.items.map((item) => item.id), ['r2']);
    await act(async () => renderer.unmount());
  });

  it('waits for typing to settle before searching again', async () => {
    const queries = [];
    const search = async (query) => { queries.push(query.get('q')); return page([], 0); };
    const seen = { current: null };
    let renderer;
    const options = { search, delayMs: 30 };
    await act(async () => { renderer = create(createElement(Probe, { query: params('v'), options, seen })); });
    await flush();
    for (const q of ['va', 'val', 'valv', 'valve']) {
      await act(async () => { renderer.update(createElement(Probe, { query: params(q), options, seen })); });
    }
    await act(async () => { await new Promise((done) => setTimeout(done, 60)); });
    assert.deepEqual(queries, ['v', 'valve']);
    await act(async () => renderer.unmount());
  });

  it('sends nothing while disabled, as the Set tab does before a Project is chosen', async () => {
    let calls = 0;
    const search = async () => { calls += 1; return page(['a'], 1); };
    const seen = { current: null };
    let renderer;
    await act(async () => { renderer = create(createElement(Probe, { query: params('x'), options: { search, delayMs: 0, enabled: false }, seen })); });
    await flush();
    assert.equal(calls, 0);
    assert.deepEqual(seen.current.items, []);
    assert.equal(seen.current.loaded, false);
    await act(async () => renderer.unmount());
  });

  it('applies optimistic changes to loaded rows', async () => {
    const search = async () => page(['a', 'b'], 2);
    const seen = { current: null };
    let renderer;
    await act(async () => { renderer = create(createElement(Probe, { query: params('x'), options: { search, delayMs: 0 }, seen })); });
    await flush();
    await act(async () => seen.current.updateItems((items) => items.map((item) => (item.id === 'a' ? { ...item, isFavourite: true } : item))));
    assert.equal(seen.current.items[0].isFavourite, true);
    await act(async () => renderer.unmount());
  });
});
