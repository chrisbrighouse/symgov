import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

import {
  buildPlanChange,
  buildRenewal,
  describeEventChange,
  describePlanStatus,
  describeSeats,
} from './organizationSubscription.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const PLAN = {
  organizationId: 'org-2', metered: true, seatLimit: 25, seatsInUse: 4, startedOn: '2026-10-04',
  expiresOn: '2027-10-04', status: 'active', version: 1,
  events: [{
    action: 'created', previousSeatLimit: null, newSeatLimit: 25, previousExpiresOn: null,
    newExpiresOn: '2027-10-04', reason: 'Default plan seeded by migration.', actorEmail: null,
    createdAt: '2026-10-04T14:15:00Z',
  }],
};

describe('plan helpers', () => {
  it('describes status, seats and history in operator terms', () => {
    assert.equal(describePlanStatus(PLAN), 'Active until 2027-10-04.');
    assert.match(describePlanStatus({ ...PLAN, status: 'expired' }), /Expired on 2027-10-04.*renewed/);
    assert.match(describePlanStatus({ metered: false, seatsInUse: 1 }), /not limited/);
    assert.equal(describeSeats(PLAN), '4 of 25 in use (21 free)');
    assert.equal(describeSeats({ ...PLAN, seatLimit: 3 }), '4 of 3 in use (1 over the limit)');
    assert.equal(describeSeats({ metered: false, seatsInUse: 1 }), '1 in use (no limit)');
    assert.equal(describeEventChange(PLAN.events[0]), '25 seats, expires 2027-10-04');
    assert.equal(
      describeEventChange({ previousSeatLimit: 25, newSeatLimit: 40, previousExpiresOn: '2027-10-04', newExpiresOn: '2028-10-04' }),
      '25 → 40 seats, expires 2027-10-04 → 2028-10-04',
    );
  });

  it('validates a plan change before any request is made', () => {
    const good = { seatLimit: '30', months: '12', reason: 'Customer upgraded their plan' };
    assert.deepEqual(buildPlanChange(good, PLAN).body, { seatLimit: 30, months: 12, reason: 'Customer upgraded their plan' });
    assert.match(buildPlanChange({ ...good, seatLimit: '0' }, PLAN).error, /Seats/);
    assert.match(buildPlanChange({ ...good, seatLimit: '2.5' }, PLAN).error, /Seats/);
    assert.match(buildPlanChange({ ...good, months: '' }, PLAN).error, /Term/);
    assert.match(buildPlanChange({ ...good, reason: 'short' }, PLAN).error, /reason/);
    assert.match(buildPlanChange({ ...good, seatLimit: '3' }, PLAN).error, /4 seats are in use/);
    assert.equal(buildPlanChange({ ...good, seatLimit: '3' }, { metered: false, seatsInUse: 4 }).body.seatLimit, 3);
  });

  it('validates a renewal', () => {
    assert.deepEqual(buildRenewal({ months: '12', reason: 'Annual renewal agreed' }).body, { months: 12, reason: 'Annual renewal agreed' });
    assert.match(buildRenewal({ months: '0', reason: 'Annual renewal agreed' }).error, /Term/);
    assert.match(buildRenewal({ months: '12', reason: '' }).error, /reason/);
  });
});

function response(status, payload) {
  const body = payload == null ? '' : JSON.stringify(payload);
  return { ok: status >= 200 && status < 300, status, statusText: 'OK', text: async () => body, json: async () => payload };
}

const PLATFORM_USER = {
  id: 'u-1', email: 'admin@example.test', displayName: 'Admin User', roles: [], mustChangePin: false,
  subscription: { tier: 'free', status: 'active' },
  session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
  organization: { id: 'org-1', code: 'symgov', displayName: 'Symgov', baseRole: 'admin', capabilities: [] },
  isPlatformAdmin: true,
  capabilities: { organizationAdminEnabled: true, platformAdminEnabled: true },
  recentStepUpAt: null,
};

async function mount(path) {
  const vite = await createServer({
    configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom',
  });
  let renderer;
  try {
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    await act(async () => {
      renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)));
    });
  } finally {
    await vite.close();
  }
  return renderer;
}

const input = (renderer, id) => renderer.root.find((node) => node.type === 'input' && node.props.id === id);
const formNamed = (renderer, label) => renderer.root.find((node) => node.type === 'form' && node.props['aria-label'] === label);

describe('platform admin Plan tab', () => {
  let originalFetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
    delete globalThis.window;
  });

  // `config.js` reads `window` when the modules load and must not find one, so
  // the confirm stub goes in only after the app has mounted.
  async function mountPlatformAdmin() {
    const renderer = await mount('/platform/admin');
    globalThis.window = { confirm: () => true };
    return renderer;
  }

  function backend({ onRenew }) {
    const requests = [];
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || 'GET';
      requests.push({ url, method, body: options.body });
      if (url.endsWith('/auth/me')) return response(200, { user: PLATFORM_USER });
      if (url.includes('/platform/admins?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.includes('/platform/organizations?')) return response(200, {
        items: [{ id: 'org-2', code: 'ACME', displayName: 'Acme', entitlementStatus: 'active', isActive: true, isProtected: false }],
        page: 1, pageSize: 50, total: 1,
      });
      if (url.includes('/platform/organizations/org-2/members?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.endsWith('/platform/organizations/org-2/subscription') && method === 'GET') return response(200, PLAN);
      if (url.endsWith('/platform/organizations/org-2/subscription/renew')) return onRenew(options);
      if (url.endsWith('/auth/reauthenticate')) return response(200, { recentStepUpAt: '2026-10-04T15:00:00Z' });
      throw new Error(`Unexpected request: ${method} ${url}`);
    };
    return requests;
  }

  it('shows the plan and its history for the selected organization', async () => {
    backend({ onRenew: () => response(500, null) });
    const renderer = await mountPlatformAdmin();
    await act(async () => renderer.root.findByProps({ 'aria-label': 'View plan for Acme' }).props.onClick());
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Plan: Acme/);
    assert.match(text, /4 of 25 in use \(21 free\)/);
    assert.match(text, /Active until 2027-10-04/);
    assert.match(text, /Default plan seeded by migration/);
    assert.match(text, /2026-10-04 14:15 UTC/);
    await act(async () => renderer.unmount());
  });

  it('renews with a reason and retries once after a step-up', async () => {
    let attempts = 0;
    const requests = backend({
      onRenew: () => {
        attempts += 1;
        if (attempts === 1) return response(403, { detail: 'Step-up reauthentication is required.' });
        return response(200, { ...PLAN, expiresOn: '2028-10-04', version: 2 });
      },
    });
    const renderer = await mountPlatformAdmin();
    await act(async () => renderer.root.findByProps({ 'aria-label': 'View plan for Acme' }).props.onClick());
    await act(async () => {
      input(renderer, 'platform-step-up-pin').props.onChange({ target: { value: '1234' } });
      input(renderer, 'plan-renew-reason').props.onChange({ target: { value: 'Annual renewal agreed with customer' } });
    });
    await act(async () => formNamed(renderer, 'Renew plan for Acme').props.onSubmit({ preventDefault() {} }));

    const renewals = requests.filter((r) => r.url.endsWith('/subscription/renew'));
    assert.equal(renewals.length, 2, 'one refused attempt, one retry');
    assert.deepEqual(JSON.parse(renewals[1].body), { months: 12, reason: 'Annual renewal agreed with customer' });
    assert.equal(requests.filter((r) => r.url.endsWith('/auth/reauthenticate')).length, 1);
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Plan renewed/);
    assert.match(text, /Active until 2028-10-04/);
    await act(async () => renderer.unmount());
  });

  it('refuses a renewal without a reason and sends nothing', async () => {
    const requests = backend({ onRenew: () => response(500, null) });
    const renderer = await mountPlatformAdmin();
    await act(async () => renderer.root.findByProps({ 'aria-label': 'View plan for Acme' }).props.onClick());
    await act(async () => formNamed(renderer, 'Renew plan for Acme').props.onSubmit({ preventDefault() {} }));
    assert.match(JSON.stringify(renderer.toJSON()), /Give a reason of at least 10 characters/);
    assert.equal(requests.filter((r) => r.url.endsWith('/subscription/renew')).length, 0);
    await act(async () => renderer.unmount());
  });
});
