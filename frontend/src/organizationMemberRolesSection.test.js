import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

import {
  PLAN_ROLE_INFO,
  describeRole,
  describeRolesEffect,
  rolesByMembership,
} from './organizationSubscription.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

describe('plan role wording', () => {
  it('describes each role by what it lets a member do, in product terms', () => {
    assert.deepEqual(Object.keys(PLAN_ROLE_INFO).sort(), ['integrator', 'reviewer', 'submitter']);
    assert.match(describeRole('submitter').summary, /Submit symbols and files/);
    assert.match(describeRole('reviewer').summary, /review and rights-review cases/);
    assert.match(describeRole('integrator').summary, /API key/);
    // The internal names never reach the screen as labels.
    for (const role of Object.keys(PLAN_ROLE_INFO)) assert.notEqual(describeRole(role).label.toLowerCase(), role);
    assert.deepEqual(describeRole('mystery'), { label: 'mystery', summary: '' });
  });

  it('groups assignments by membership', () => {
    const grouped = rolesByMembership([
      { membershipId: 'm-1', role: 'submitter' }, { membershipId: 'm-1', role: 'reviewer' }, { membershipId: 'm-2', role: 'integrator' },
    ]);
    assert.deepEqual([...grouped['m-1']].sort(), ['reviewer', 'submitter']);
    assert.deepEqual(rolesByMembership(null), {});
  });

  it('says why roles are not changing access right now', () => {
    assert.match(describeRolesEffect({ status: 'active' }, false), /Not switched on yet/);
    assert.match(describeRolesEffect({ status: 'expired' }, true), /plan has expired/);
    assert.equal(describeRolesEffect({ status: 'active' }, true), '');
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

const PLAN = {
  organizationId: 'org-2', metered: true, seatLimit: 25, seatsInUse: 2, startedOn: '2026-10-04',
  expiresOn: '2027-10-04', status: 'active', version: 1, events: [],
};

const MEMBERS = {
  items: [
    { membershipId: 'm-1', userId: 'u-a', email: 'ann@acme.test', displayName: 'Ann', userIsActive: true, status: 'active', baseRole: 'user', capabilities: [] },
    { membershipId: 'm-2', userId: 'u-b', email: 'gone@acme.test', displayName: 'Gone', userIsActive: true, status: 'inactive', baseRole: 'user', capabilities: [] },
  ],
  page: 1, pageSize: 200, total: 2,
};

function listing(items, rolesEnabled) {
  return { organizationId: 'org-2', assignableRoles: ['integrator', 'reviewer', 'submitter'], rolesEnabled, items };
}

async function mount() {
  const vite = await createServer({
    configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom',
  });
  let renderer;
  try {
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    await act(async () => {
      renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: ['/platform/admin'] }, createElement(App)));
    });
  } finally {
    await vite.close();
  }
  globalThis.window = { confirm: () => true }; // after the app's modules have loaded
  return renderer;
}

const input = (renderer, id) => renderer.root.find((node) => node.type === 'input' && node.props.id === id);
const text = (renderer) => JSON.stringify(renderer.toJSON());

describe('platform admin Plan roles', () => {
  let originalFetch;
  beforeEach(() => { originalFetch = globalThis.fetch; });
  afterEach(() => { globalThis.fetch = originalFetch; delete globalThis.window; });

  function backend({ rolesEnabled = true, onGrant } = {}) {
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
      if (url.includes('/platform/organizations/org-2/members?pageSize=200')) return response(200, MEMBERS);
      if (url.includes('/platform/organizations/org-2/members?')) return response(200, MEMBERS);
      if (url.endsWith('/platform/organizations/org-2/subscription')) return response(200, PLAN);
      if (url.endsWith('/platform/organizations/org-2/member-roles')) return response(200, listing([], rolesEnabled));
      if (url.endsWith('/members/m-1/roles/reviewer') && method === 'PUT') return onGrant(options);
      if (url.endsWith('/auth/reauthenticate')) return response(200, { recentStepUpAt: '2026-10-04T15:00:00Z' });
      throw new Error(`Unexpected request: ${method} ${url}`);
    };
    return requests;
  }

  async function openPlan(renderer) {
    await act(async () => renderer.root.findByProps({ 'aria-label': 'View plan for Acme' }).props.onClick());
  }

  it('explains each role, separates them from the org Contributor/Reviewer, and lists only active members', async () => {
    backend({ rolesEnabled: false, onGrant: () => response(500, null) });
    const renderer = await mount();
    await openPlan(renderer);
    const page = text(renderer);
    assert.match(page, /Plan roles/);
    for (const label of ['Submissions', 'Governance review', 'Catalog developer']) assert.ok(page.includes(label), label);
    assert.match(page, /Submit symbols and files to Symgov for governance review/);
    assert.match(page, /only while they work as this organization and only while its plan is active/);
    assert.match(page, /separate from the Contributor and Reviewer settings/);
    assert.match(page, /Not switched on yet/);
    assert.ok(page.includes('ann@acme.test'));
    assert.ok(!page.includes('gone@acme.test'), 'inactive members are not offered roles');
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Governance review for ann@acme.test' }).length, 1);
    await act(async () => renderer.unmount());
  });

  it('will not change a role without a recorded reason', async () => {
    const requests = backend({ onGrant: () => response(500, null) });
    const renderer = await mount();
    await openPlan(renderer);
    const box = renderer.root.findByProps({ 'aria-label': 'Governance review for ann@acme.test' });
    await act(async () => box.props.onChange({ target: { checked: true } }));
    assert.match(text(renderer), /Give a reason of at least 10 characters first/);
    assert.equal(requests.filter((r) => r.method === 'PUT').length, 0);
    await act(async () => renderer.unmount());
  });

  it('gives a role with the reason after one step-up retry and shows it ticked', async () => {
    let attempts = 0;
    const requests = backend({
      onGrant: () => {
        attempts += 1;
        if (attempts === 1) return response(403, { detail: 'Step-up reauthentication is required.' });
        return response(200, listing([{ membershipId: 'm-1', email: 'ann@acme.test', displayName: 'Ann', role: 'reviewer', assignedAt: '2026-10-04T15:00:00Z' }], true));
      },
    });
    const renderer = await mount();
    await openPlan(renderer);
    await act(async () => {
      input(renderer, 'platform-step-up-pin').props.onChange({ target: { value: '1234' } });
      input(renderer, 'plan-roles-reason').props.onChange({ target: { value: 'Contracted review seat for Ann' } });
    });
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Governance review for ann@acme.test' }).props.onChange({ target: { checked: true } }));

    const puts = requests.filter((r) => r.method === 'PUT');
    assert.equal(puts.length, 2, 'one refused attempt and one retry');
    assert.deepEqual(JSON.parse(puts[1].body), { reason: 'Contracted review seat for Ann' });
    assert.equal(requests.filter((r) => r.url.endsWith('/auth/reauthenticate')).length, 1);
    assert.match(text(renderer), /Governance review given to ann@acme.test/);
    assert.equal(renderer.root.findByProps({ 'aria-label': 'Governance review for ann@acme.test' }).props.checked, true);
    await act(async () => renderer.unmount());
  });
});
