import test from 'node:test';
import assert from 'node:assert/strict';
import {
  safeInternalDestination,
  internalDestinationFromLocation,
  destinationFromRouterState,
  routeForCatalogSymbol,
  absoluteHashRoute
} from './catalogRoutes.js';

test('safeInternalDestination preserves a safe pathname, query and fragment byte-for-byte', () => {
  const destination = '/standards?symbol=S-%30%30%30%30%30%31#detail';
  assert.equal(safeInternalDestination(destination), destination);
});

test('internalDestinationFromLocation reads only pathname, search and hash', () => {
  assert.equal(
    internalDestinationFromLocation({ pathname: '/standards', search: '?symbol=S-000001', hash: '#detail', user: 'ignored' }),
    '/standards?symbol=S-000001#detail'
  );
});

test('destinationFromRouterState accepts only one validated semantic string', () => {
  assert.equal(destinationFromRouterState({ from: '/standards?symbol=S-000001#detail' }), '/standards?symbol=S-000001#detail');
  assert.equal(destinationFromRouterState({ from: { pathname: '/standards', search: '?symbol=S-000001', hash: '#detail' } }), '/standards');
});

test('safeInternalDestination falls back for external, malformed and authentication routes', () => {
  const values = [
    null,
    [],
    {},
    42,
    '',
    'https://evil.example/x',
    '//evil.example/x',
    '/\\evil.example',
    '/bad%2',
    '/%2F%2Fevil.example/x',
    '/%5C%5Cevil.example/x',
    '/safe/../login',
    '/safe/../select-organization',
    '/safe/%2e%2e/change-pin',
    '/safe%3f/../login',
    '/safe%23/../login',
    '/login?from=/standards',
    '/select-organization#x',
    '/change-pin?next=/standards'
  ];
  for (const value of values) assert.equal(safeInternalDestination(value), '/standards', String(value));
});

test('safeInternalDestination preserves safe encoded values without decoding them', () => {
  const destination = '/standards?query=fire%20alarm#detail%2Fone';
  assert.equal(safeInternalDestination(destination), destination);
});

test('routeForCatalogSymbol only accepts canonical Catalog IDs', () => {
  assert.equal(routeForCatalogSymbol('S-000001'), '/s/S-000001');
  assert.equal(routeForCatalogSymbol('S-1000000'), '/s/S-1000000');
  assert.equal(routeForCatalogSymbol('0003-12'), '/s/0003-12');
  assert.equal(routeForCatalogSymbol('AB'), '/s/AB');
  assert.equal(routeForCatalogSymbol('smoke-detector'), null);
  assert.equal(routeForCatalogSymbol('A'), null);
  assert.equal(routeForCatalogSymbol('S-1'), '/s/S-1');
  assert.equal(routeForCatalogSymbol('S-000001/evil'), null);
});

test('absoluteHashRoute validates origin and semantic route', () => {
  assert.equal(absoluteHashRoute('https://example.test', '/s/S-000001'), 'https://example.test/#/s/S-000001');
  assert.equal(absoluteHashRoute('https://example.test/', '/s/S-000001'), 'https://example.test/#/s/S-000001');
  assert.equal(absoluteHashRoute('javascript:alert(1)', '/s/S-000001'), null);
  assert.equal(absoluteHashRoute('https://example.test', 'https://evil.example/x'), null);
});
