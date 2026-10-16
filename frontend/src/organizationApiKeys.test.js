import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import { emptyKeyForm, issueKeyRequest, MyApiKeysCard, OrganizationApiKeysPanel } from './organizationApiKeys.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function textOf(node) {
  if (node == null) return '';
  if (typeof node === 'string') return node;
  return (node.children || []).map(textOf).join(' ');
}

async function mount(element) {
  let renderer;
  await act(async () => { renderer = TestRenderer.create(element); });
  await act(async () => {});
  return renderer;
}

const MEMBERS = [
  { userId: 'u-1', displayName: 'Ada', email: 'ada@acme.test', status: 'active', userIsActive: true },
  { userId: 'u-2', displayName: 'Gone', email: 'gone@acme.test', status: 'inactive', userIsActive: true },
];

function key(overrides = {}) {
  return {
    keyId: 'k-1', integrationName: 'Revit add-in', state: 'pending_claim', keyPrefix: null, holderDisplayName: 'Ada',
    holderEmail: 'ada@acme.test', rateLimitPerMinute: null, allowedOrigins: [], lastUsedAt: null, ...overrides,
  };
}

test('the issue request needs a holder, a name and a scope, and keeps the limit in range', () => {
  const form = { ...emptyKeyForm(), holderUserId: 'u-1', integrationName: '  Revit  ' };
  assert.deepEqual(issueKeyRequest(form).request, {
    holderUserId: 'u-1', integrationName: 'Revit', scopes: ['catalog.read', 'catalog.preview'],
  });
  assert.match(issueKeyRequest({ ...form, holderUserId: '' }).error, /who will hold/);
  assert.match(issueKeyRequest({ ...form, integrationName: ' ' }).error, /what the key is for/);
  assert.match(issueKeyRequest({ ...form, scopes: [] }).error, /at least one/);
  assert.match(issueKeyRequest({ ...form, rateLimitPerMinute: '601' }).error, /1 to 600/);
  assert.match(issueKeyRequest({ ...form, rateLimitPerMinute: '2.5' }).error, /whole number/);
  assert.equal(issueKeyRequest({ ...form, rateLimitPerMinute: '250' }).request.rateLimitPerMinute, 250);
  assert.deepEqual(
    issueKeyRequest({ ...form, allowedOrigins: 'https://a.example\nhttps://b.example, https://a.example' }).request.allowedOrigins,
    ['https://a.example', 'https://b.example', 'https://a.example'],
  );
});

test('the admin panel lists keys, offers only active members, and issues through the api', async () => {
  const issued = [];
  const api = {
    listKeys: async () => ({ items: [key()], defaults: { rateLimitPerMinute: 120, maxRateLimitPerMinute: 600 } }),
    listMembers: async () => MEMBERS,
    issueKey: async (request) => { issued.push(request); return {}; },
    revokeKey: async () => ({}),
  };
  const renderer = await mount(createElement(OrganizationApiKeysPanel, { isAdmin: true, api }));
  const text = textOf(renderer.toJSON());
  assert.match(text, /Revit add-in/);
  assert.match(text, /Held by Ada \(ada@acme.test\)/);
  assert.match(text, /Waiting for the holder to collect it/);
  assert.match(text, /Ada \(ada@acme.test\)/);
  assert.doesNotMatch(text, /Gone \(gone@acme.test\)/);

  const select = renderer.root.findByProps({ id: 'api-key-holder' });
  const name = renderer.root.findByProps({ id: 'api-key-name' });
  await act(async () => { select.props.onChange({ target: { value: 'u-1' } }); });
  await act(async () => { name.props.onChange({ target: { value: 'Plant add-in' } }); });
  const form = renderer.root.findByProps({ 'aria-label': 'Issue an API key' });
  await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
  assert.deepEqual(issued, [{ holderUserId: 'u-1', integrationName: 'Plant add-in', scopes: ['catalog.read', 'catalog.preview'] }]);
  assert.match(textOf(renderer.toJSON()), /Key issued/);
});

test('the admin panel says what is wrong instead of sending an incomplete request', async () => {
  let calls = 0;
  const api = {
    listKeys: async () => ({ items: [], defaults: {} }), listMembers: async () => MEMBERS,
    issueKey: async () => { calls += 1; }, revokeKey: async () => {},
  };
  const renderer = await mount(createElement(OrganizationApiKeysPanel, { isAdmin: true, api }));
  const form = renderer.root.findByProps({ 'aria-label': 'Issue an API key' });
  await act(async () => { form.props.onSubmit({ preventDefault() {} }); });
  assert.equal(calls, 0);
  assert.match(textOf(renderer.toJSON()), /Choose who will hold the key/);
});

test('issuing and revoking run under the admin page step-up', async () => {
  const protectedCalls = [];
  const api = {
    listKeys: async () => ({ items: [key({ state: 'active' })], defaults: {} }), listMembers: async () => MEMBERS,
    issueKey: async () => ({}), revokeKey: async () => ({}),
  };
  const protect = async (operation) => { protectedCalls.push('protected'); return operation(); };
  globalThis.window = { confirm: () => true };
  const renderer = await mount(createElement(OrganizationApiKeysPanel, { isAdmin: true, api, protect }));
  await act(async () => { renderer.root.findByProps({ 'aria-label': 'Revoke API key Revit add-in held by Ada' }).props.onClick(); });
  assert.deepEqual(protectedCalls, ['protected']);
  delete globalThis.window;
});

test('a revoked key offers no revoke button, and a failed load is reported', async () => {
  const api = {
    listKeys: async () => ({ items: [key({ state: 'revoked' })], defaults: {} }), listMembers: async () => MEMBERS,
    issueKey: async () => {}, revokeKey: async () => {},
  };
  const renderer = await mount(createElement(OrganizationApiKeysPanel, { isAdmin: true, api }));
  assert.equal(renderer.root.findAll((node) => /^Revoke API key/.test(node.props['aria-label'] || '')).length, 0);

  const broken = { ...api, listKeys: async () => { throw new Error('Server unavailable'); } };
  const failed = await mount(createElement(OrganizationApiKeysPanel, { isAdmin: true, api: broken }));
  assert.match(textOf(failed.toJSON()), /Server unavailable/);
});

test('the profile card is absent without keys, collects a key once and then forgets the secret', async () => {
  const empty = await mount(createElement(MyApiKeysCard, { api: { listMyKeys: async () => ({ items: [] }) } }));
  assert.equal(empty.toJSON(), null);

  let claimed = false;
  const api = {
    listMyKeys: async () => ({ items: [claimed
      ? key({ state: 'active', keyPrefix: 'symgov_live_abcd1234', organizationName: 'Acme' })
      : key({ organizationName: 'Acme' })] }),
    claimKey: async () => { claimed = true; return { rawKey: 'symgov_live_SECRET' }; },
  };
  const renderer = await mount(createElement(MyApiKeysCard, { api }));
  assert.match(textOf(renderer.toJSON()), /Acme · Waiting for the holder to collect it/);
  await act(async () => { renderer.root.findByProps({ 'aria-label': 'Collect API key Revit add-in' }).props.onClick(); });
  await act(async () => {});
  const shown = textOf(renderer.toJSON());
  assert.match(shown, /symgov_live_SECRET/);
  assert.match(shown, /shown once/);
  assert.match(shown, /symgov_live_abcd1234…/);
  assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Collect API key Revit add-in' }).length, 0);

  await act(async () => { renderer.root.findByProps({ children: 'I have saved it' }).props.onClick(); });
  assert.doesNotMatch(textOf(renderer.toJSON()), /SECRET/);
});
