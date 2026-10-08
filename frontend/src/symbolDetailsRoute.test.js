import test from 'node:test';
import assert from 'node:assert/strict';
import {
  catalogSearchParams,
  DEFAULT_DETAILS_TAB,
  DETAILS_TABS,
  detailsPath,
  detailsReference,
  detailsSearchParams,
  normalizeDetailsTab,
  parseDetailsState,
  resolveDetailsTab,
  resultNeighbours,
  shortLinkTarget,
  symgovDetailsUrl
} from './symbolDetailsRoute.js';

const params = (value) => new URLSearchParams(value);

test('the tabs are in the agreed order and Classification & mappings is the default', () => {
  assert.deepEqual(DETAILS_TABS.map((tab) => tab.label), [
    'Classification & mappings',
    'Details',
    'Connection points',
    'Labels & states',
    'Source & rights',
    'History',
    'Comments'
  ]);
  assert.equal(DEFAULT_DETAILS_TAB, 'classification');
});

test('the details URL carries symbol, view and tab', () => {
  assert.equal(detailsPath('S-273', 'connections'), '/standards?symbol=S-273&view=details&tab=connections');
  assert.equal(detailsPath('S-273'), '/standards?symbol=S-273&view=details&tab=classification');
});

test('a direct details link parses to an open view', () => {
  assert.deepEqual(parseDetailsState(params('symbol=S-273&view=details&tab=history')), { open: true, symbol: 'S-273', tab: 'history' });
});

test('a missing or unknown tab opens the default one', () => {
  assert.equal(parseDetailsState(params('symbol=S-273&view=details')).tab, 'classification');
  assert.equal(parseDetailsState(params('symbol=S-273&view=details&tab=nope')).tab, 'classification');
  assert.equal(normalizeDetailsTab('comments'), 'comments');
});

test('view=details without a symbol is not a details view', () => {
  assert.equal(parseDetailsState(params('view=details')).open, false);
  assert.equal(parseDetailsState(params('view=details&symbol=%20')).open, false);
});

test('the Catalog tab values are not the details view', () => {
  assert.equal(parseDetailsState(params('symbol=S-1&view=set')).open, false);
  assert.equal(parseDetailsState(params('symbol=S-1&view=catalog')).open, false);
  assert.equal(parseDetailsState(params('symbol=S-1')).open, false);
});

test('opening the view keeps the other parameters, and closing removes only its own', () => {
  const opened = detailsSearchParams(params('dexpiClass=Pump&symbol=S-1'), { symbol: 'S-273', tab: 'details' });
  assert.equal(opened.get('dexpiClass'), 'Pump');
  assert.equal(opened.get('view'), 'details');
  assert.equal(opened.get('symbol'), 'S-273');
  const closed = catalogSearchParams(opened, { symbol: 'S-273' });
  assert.equal(closed.get('view'), null);
  assert.equal(closed.get('tab'), null);
  assert.equal(closed.get('symbol'), 'S-273');
  assert.equal(closed.get('dexpiClass'), 'Pump');
});

test('closing restores the Catalog tab that was set aside', () => {
  const opened = detailsSearchParams(params('view=set&symbol=S-1'), { symbol: 'S-1' });
  assert.equal(opened.get('view'), 'details');
  assert.equal(catalogSearchParams(opened, { symbol: 'S-1', view: 'set' }).get('view'), 'set');
});

test('#/s/<id> resolves to the details view and rejects malformed ids', () => {
  assert.equal(shortLinkTarget('S-273'), '/standards?symbol=S-273&view=details');
  assert.equal(shortLinkTarget('../etc'), null);
  assert.equal(shortLinkTarget(''), null);
  assert.equal(shortLinkTarget(undefined), null);
});

test('the shareable details URL is the hash short link', () => {
  assert.equal(symgovDetailsUrl('https://symgov.example', 'S-273'), 'https://symgov.example/#/s/S-273');
  assert.equal(symgovDetailsUrl('https://symgov.example', ''), null);
  assert.equal(symgovDetailsUrl('not a url', 'S-273'), null);
});

test('a symbol is referred to by catalog ID, else its own id', () => {
  assert.equal(detailsReference({ catalogSymbolId: 'S-1', id: 'slug' }), 'S-1');
  assert.equal(detailsReference({ catalogSymbolId: null, id: 'uuid-1' }), 'uuid-1');
  assert.equal(detailsReference(null), '');
});

test('the shown tab falls back when the requested one does not exist for the symbol', () => {
  assert.equal(resolveDetailsTab('connections', ['classification', 'comments']), 'classification');
  assert.equal(resolveDetailsTab('comments', ['classification', 'comments']), 'comments');
  assert.equal(resolveDetailsTab('connections', ['details', 'comments']), 'details');
});

test('previous and next follow the results, once per symbol', () => {
  const items = [{ id: 'a' }, { id: 'b' }, { id: 'b' }, { id: 'c' }];
  assert.deepEqual(resultNeighbours(items, 'b'), { index: 1, position: 2, count: 3, previous: { id: 'a' }, next: { id: 'c' } });
  assert.equal(resultNeighbours(items, 'a').previous, null);
  assert.equal(resultNeighbours(items, 'c').next, null);
});

test('a symbol outside the results has no neighbours', () => {
  const result = resultNeighbours([{ id: 'a' }], 'zzz');
  assert.equal(result.index, -1);
  assert.equal(result.previous, null);
  assert.equal(result.next, null);
  assert.equal(resultNeighbours(null, 'a').count, 0);
});
