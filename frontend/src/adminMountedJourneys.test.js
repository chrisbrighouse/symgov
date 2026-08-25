import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function response(status, payload) {
  const body = payload == null ? '' : JSON.stringify(payload);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 403 ? 'Forbidden' : 'OK',
    text: async () => body,
    json: async () => payload,
  };
}

function user({ organizationRole = 'user', platform = false, organizationAdminEnabled = true, platformAdminEnabled = true, organizationIconUploadEnabled = false, symbolSetsEnabled = false } = {}) {
  return {
    id: 'u-1',
    email: 'admin@example.test',
    displayName: 'Admin User',
    roles: [],
    mustChangePin: false,
    subscription: { tier: 'free', status: 'active' },
    session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
    organization: { id: 'org-1', code: platform ? 'symgov' : 'acme', displayName: platform ? 'Symgov' : 'Acme', baseRole: organizationRole, capabilities: [] },
    isPlatformAdmin: platform,
    capabilities: { organizationAdminEnabled, platformAdminEnabled, organizationIconUploadEnabled, symbolSetsEnabled },
    recentStepUpAt: null,
  };
}

function organization() {
  return {
    id: 'org-1', code: 'acme', displayName: 'Acme', legalName: null,
    entitlementStatus: 'active', isActive: true, isProtected: false,
    hasCustomIcon: false, customIconEnabled: false, iconUrl: null,
  };
}

function memberList() {
  return { items: [], page: 1, pageSize: 50, total: 0 };
}

async function mount(path) {
  const vite = await createServer({
    configFile: false,
    root: process.cwd(),
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
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

function input(renderer, id) {
  return renderer.root.find((node) => node.type === 'input' && node.props.id === id);
}

function formContaining(renderer, id) {
  const field = input(renderer, id);
  let node = field.parent;
  while (node && node.type !== 'form') node = node.parent;
  assert.ok(node, `form containing ${id}`);
  return node;
}

describe('mounted admin App journeys', () => {
  let originalFetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('uses App navigation and clears the PIN after a 403 step-up member-add retry', async () => {
    const requests = [];
    let addAttempts = 0;
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || 'GET';
      requests.push({ url, method, body: options.body });
      if (url.endsWith('/auth/me')) return response(200, { user: user({ organizationRole: 'admin' }) });
      if (url.endsWith('/org/me') && method === 'GET') return response(200, organization());
      if (url.includes('/org/me/members?')) return response(200, memberList());
      if (url.endsWith('/auth/reauthenticate')) return response(200, { recentStepUpAt: '2026-08-21T15:00:00Z' });
      if (url.endsWith('/org/me/members') && method === 'POST') {
        addAttempts += 1;
        if (addAttempts === 1) return response(403, { detail: 'Step-up reauthentication is required.' });
        return response(201, { membershipId: 'm-2', userId: 'u-2', baseRole: 'admin' });
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    const renderer = await mount('/organization/admin');
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Organization' }));
    assert.equal(input(renderer, 'organization-step-up-pin').props.type, 'password');
    assert.equal(input(renderer, 'organization-step-up-pin').props.autoComplete, 'off');

    await act(async () => {
      input(renderer, 'organization-step-up-pin').props.onChange({ target: { value: '1234' } });
      input(renderer, 'organization-member-user-id').props.onChange({ target: { value: 'u-2' } });
      renderer.root.findByProps({ id: 'organization-member-base-role' }).props.onChange({ target: { value: 'admin' } });
    });
    await act(async () => {
      await formContaining(renderer, 'organization-member-user-id').props.onSubmit({ preventDefault() {} });
    });

    assert.equal(addAttempts, 2);
    assert.equal(input(renderer, 'organization-step-up-pin').props.value, '');
    const reauth = requests.find((request) => request.url.endsWith('/auth/reauthenticate'));
    assert.deepEqual(JSON.parse(reauth.body), { pin: '1234' });
    const memberPosts = requests.filter((request) => request.url.endsWith('/org/me/members') && request.method === 'POST');
    assert.deepEqual(JSON.parse(memberPosts[0].body), { userId: 'u-2', baseRole: 'admin' });
    assert.doesNotMatch(memberPosts[0].body, /pin/i);
    await act(async () => renderer.unmount());
  });

  it('mounts Project and Symbol Set selectors only for symbol-set-enabled organization sessions', async () => {
    const requests = [];
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || 'GET';
      requests.push({ url, method });
      if (url.endsWith('/auth/me')) return response(200, { user: user({ organizationRole: 'admin', symbolSetsEnabled: true }) });
      if (url.endsWith('/org/me') && method === 'GET') return response(200, organization());
      if (url.includes('/org/me/members?')) return response(200, memberList());
      if (url.includes('/org/me/projects?')) {
        return response(200, { items: [{ id: 'p-1', code: 'P-01', name: 'Plant', shortDescription: 'North works', status: 'active', externalReference: null, metadata: {}, createdAt: '2026-08-20T10:00:00Z', updatedAt: '2026-08-20T10:00:00Z', closedAt: null }], page: 1, pageSize: 25, total: 1 });
      }
      if (url.includes('/org/me/symbol-context') && method === 'GET') {
        return response(200, { selectedProject: null, activeSet: null, reason: 'none' });
      }
      if (url.includes('/org/me/symbol-sets?')) {
        return response(200, { items: [], page: 1, pageSize: 200, total: 0 });
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    const renderer = await mount('/organization/admin');
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Active Project' }));
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Active Symbol Set' }));
    assert.match(JSON.stringify(renderer.toJSON()), /Select a Project first/);
    assert.equal(requests.some((request) => request.url.includes('/organization/context')), false);
    await act(async () => renderer.unmount());
  });

  it('mounts Platform Admin through App navigation and preserves backend denial', async () => {
    const requests = [];
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || 'GET';
      requests.push({ url, method, body: options.body });
      if (url.endsWith('/auth/me')) return response(200, { user: user({ platform: true }) });
      if (url.includes('/platform/admins?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.includes('/platform/organizations?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.includes('/platform/organizations/symgov/members?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.endsWith('/platform/admins') && method === 'POST') return response(403, { detail: 'Platform admin access is required.' });
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    const renderer = await mount('/platform/admin');
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Platform' }));
    assert.equal(input(renderer, 'platform-admin-user-id').props.required, true);
    await act(async () => {
      input(renderer, 'platform-admin-user-id').props.onChange({ target: { value: 'u-2' } });
    });
    await act(async () => {
      await formContaining(renderer, 'platform-admin-user-id').props.onSubmit({ preventDefault() {} });
    });

    const alert = renderer.root.findByProps({ role: 'alert' });
    assert.match(alert.children.join(''), /Platform admin access is required/);
    assert.equal(requests.some((request) => request.url.endsWith('/auth/reauthenticate')), false);
    await act(async () => renderer.unmount());
  });

  it('executes protected Symgov membership workflows through the mounted Platform Admin route', async () => {
    const requests = [];
    let addAttempts = 0;
    let protectedMember = {
      membershipId: 'm-1', userId: 'u-3', email: 'member@example.test',
      displayName: 'Protected Member', userIsActive: true, status: 'active',
      baseRole: 'user', capabilities: [], activatedAt: '2026-08-19T12:00:00Z', deactivatedAt: null,
    };
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || 'GET';
      requests.push({ url, method, body: options.body });
      if (url.endsWith('/auth/me')) return response(200, { user: user({ platform: true }) });
      if (url.includes('/platform/admins?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.includes('/platform/organizations?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.includes('/platform/organizations/symgov/members?')) {
        return response(200, { items: [protectedMember], page: 1, pageSize: 50, total: 1 });
      }
      if (url.endsWith('/platform/organizations/symgov/members') && method === 'POST') {
        addAttempts += 1;
        if (addAttempts === 1) return response(403, { detail: 'Step-up reauthentication is required.' });
        return response(201, protectedMember);
      }
      if (url.endsWith('/auth/reauthenticate')) return response(200, { recentStepUpAt: '2026-08-22T14:00:00Z' });
      if (url.endsWith('/platform/organizations/symgov/members/m-1') && method === 'PATCH') {
        const body = JSON.parse(options.body);
        protectedMember = { ...protectedMember, baseRole: body.baseRole };
        return response(200, protectedMember);
      }
      if (url.endsWith('/platform/organizations/symgov/members/m-1/deactivate') && method === 'POST') {
        protectedMember = { ...protectedMember, status: 'inactive', deactivatedAt: '2026-08-22T14:01:00Z' };
        return response(204, null);
      }
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    const renderer = await mount('/platform/admin');
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Platform' }));
    assert.equal(input(renderer, 'protected-member-reason').props.required, true);
    assert.equal(renderer.root.findByProps({ 'aria-label': 'Promote Protected Member' }).props.disabled, true);

    await act(async () => {
      input(renderer, 'platform-step-up-pin').props.onChange({ target: { value: '1234' } });
      input(renderer, 'protected-member-user-id').props.onChange({ target: { value: 'u-4' } });
      input(renderer, 'protected-member-reason').props.onChange({ target: { value: 'Approved onboarding request' } });
    });
    await act(async () => formContaining(renderer, 'protected-member-user-id').props.onSubmit({ preventDefault() {} }));
    assert.equal(addAttempts, 2);
    assert.equal(input(renderer, 'platform-step-up-pin').props.value, '');

    await act(async () => input(renderer, 'protected-member-mutation-reason-m-1').props.onChange({ target: { value: 'Approved role promotion' } }));
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Promote Protected Member' }).props.onClick());
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Demote Protected Member' }));

    await act(async () => input(renderer, 'protected-member-mutation-reason-m-1').props.onChange({ target: { value: 'Approved role demotion' } }));
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Demote Protected Member' }).props.onClick());
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Promote Protected Member' }));

    await act(async () => input(renderer, 'protected-member-mutation-reason-m-1').props.onChange({ target: { value: 'Membership no longer required' } }));
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Deactivate Protected Member' }).props.onClick());

    const protectedMutations = requests.filter(({ url, method }) =>
      url.includes('/platform/organizations/symgov/members') && (method === 'POST' || method === 'PATCH'));
    assert.deepEqual(protectedMutations.map(({ body }) => JSON.parse(body)), [
      { userId: 'u-4', baseRole: 'user', reason: 'Approved onboarding request' },
      { userId: 'u-4', baseRole: 'user', reason: 'Approved onboarding request' },
      { baseRole: 'admin', reason: 'Approved role promotion' },
      { baseRole: 'user', reason: 'Approved role demotion' },
      { reason: 'Membership no longer required' },
    ]);
    assert.equal(JSON.stringify(protectedMutations).includes('1234'), false);
    assert.equal(requests.filter(({ url }) => url.endsWith('/auth/reauthenticate')).length, 1);
    await act(async () => renderer.unmount());
  });

  it('hides privileged navigation and denies direct routes without backend authority', async () => {
    globalThis.fetch = async (url) => {
      if (url.endsWith('/auth/me')) return response(200, { user: user() });
      throw new Error(`Unexpected request: ${url}`);
    };
    const renderer = await mount('/organization/admin');
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Organization' }).length, 0);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Platform' }).length, 0);
    const text = renderer.toJSON();
    assert.match(JSON.stringify(text), /You do not have access to this area/);
    await act(async () => renderer.unmount());
  });

  it('fails closed for feature-off and wrong-context direct admin URLs', async () => {
    for (const currentUser of [
      user({ organizationRole: 'admin', organizationAdminEnabled: false }),
      { ...user({ organizationRole: 'admin' }), session: { mode: 'personal', purpose: 'application', activeOrganizationId: null }, organization: null },
      user({ platform: true, platformAdminEnabled: false }),
      { ...user({ platform: true }), organization: { id: 'org-1', code: 'other', displayName: 'Other', baseRole: 'admin', capabilities: [] } },
    ]) {
      globalThis.fetch = async (url) => {
        if (url.endsWith('/auth/me')) return response(200, { user: currentUser });
        throw new Error(`Privileged API must not be called: ${url}`);
      };
      const path = currentUser.isPlatformAdmin ? '/platform/admin' : '/organization/admin';
      const renderer = await mount(path);
      assert.match(JSON.stringify(renderer.toJSON()), /You do not have access to this area/);
      assert.equal(renderer.root.findAllByProps({ id: 'protected-member-user-id' }).length, 0);
      await act(async () => renderer.unmount());
    }
  });

  it('uses one main landmark and renders the authenticated fallback icon as an image', async () => {
    globalThis.fetch = async (url, options = {}) => {
      if (url.endsWith('/auth/me')) return response(200, { user: user({ organizationRole: 'admin' }) });
      if (url.endsWith('/org/me')) return response(200, { ...organization(), iconUrl: '/api/v1/org/me/icon' });
      if (url.includes('/org/me/members?')) return response(200, memberList());
      throw new Error(`Unexpected request: ${options.method || 'GET'} ${url}`);
    };
    const renderer = await mount('/organization/admin');
    assert.equal(renderer.root.findAllByType('main').length, 1);
    assert.equal(renderer.root.findByProps({ alt: 'Acme icon' }).props.src, '/api/v1/org/me/icon');
    assert.equal(renderer.root.findAllByProps({ id: 'org-icon-file' }).length, 0);
    await act(async () => renderer.unmount());
  });

  it('loads member diagnostics and reactivates an inactive membership with a reason and one step-up retry', async () => {
    const requests = [];
    let reactivationAttempts = 0;
    globalThis.fetch = async (url, options = {}) => {
      const method = options.method || 'GET';
      requests.push({ url, method, body: options.body });
      if (url.endsWith('/auth/me')) return response(200, { user: user({ platform: true }) });
      if (url.includes('/platform/admins?')) return response(200, { items: [], page: 1, pageSize: 50, total: 0 });
      if (url.includes('/platform/organizations?')) return response(200, {
        items: [{ id: 'org-2', code: 'ACME', displayName: 'Acme', entitlementStatus: 'active', isActive: true, isProtected: false }],
        page: 1, pageSize: 50, total: 1,
      });
      if (url.includes('/platform/organizations/org-2/members?')) return response(200, {
        items: [{ membershipId: 'm-2', userId: 'u-2', email: 'member@example.test', displayName: 'Member', userIsActive: true, status: 'inactive', baseRole: 'user', capabilities: [], activatedAt: null, deactivatedAt: '2026-08-20T10:00:00Z' }],
        page: 1, pageSize: 50, total: 1,
      });
      if (url.endsWith('/platform/memberships/m-2/reactivate')) {
        reactivationAttempts += 1;
        if (reactivationAttempts === 1) return response(403, { detail: 'Step-up reauthentication is required.' });
        return response(200, { membershipId: 'm-2', status: 'active' });
      }
      if (url.endsWith('/auth/reauthenticate')) return response(200, { recentStepUpAt: '2026-08-22T10:00:00Z' });
      throw new Error(`Unexpected request: ${method} ${url}`);
    };

    const renderer = await mount('/platform/admin');
    await act(async () => renderer.root.findByProps({ 'aria-label': 'View members for Acme' }).props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()), /member@example.test/);
    await act(async () => {
      input(renderer, 'platform-step-up-pin').props.onChange({ target: { value: '1234' } });
      input(renderer, 'reactivation-reason-m-2').props.onChange({ target: { value: 'Restoring verified active access' } });
    });
    await act(async () => formContaining(renderer, 'reactivation-reason-m-2').props.onSubmit({ preventDefault() {} }));

    assert.equal(reactivationAttempts, 2);
    const reactivationBodies = requests
      .filter(({ url }) => url.endsWith('/platform/memberships/m-2/reactivate'))
      .map(({ body }) => JSON.parse(body));
    assert.deepEqual(reactivationBodies, [
      { reason: 'Restoring verified active access' },
      { reason: 'Restoring verified active access' },
    ]);
    assert.equal(input(renderer, 'platform-step-up-pin').props.value, '');
    await act(async () => renderer.unmount());
  });

  it('revokes a selected icon preview object URL on unmount', async () => {
    const revoked = [];
    const originalCreateObjectURL = URL.createObjectURL;
    const originalRevokeObjectURL = URL.revokeObjectURL;
    URL.createObjectURL = () => 'blob:preview';
    URL.revokeObjectURL = (value) => revoked.push(value);
    globalThis.fetch = async (url) => {
      if (url.endsWith('/auth/me')) return response(200, { user: user({ organizationRole: 'admin', organizationIconUploadEnabled: true }) });
      if (url.endsWith('/org/me')) return response(200, organization());
      if (url.includes('/org/me/members?')) return response(200, memberList());
      throw new Error(`Unexpected request: ${url}`);
    };
    try {
      const renderer = await mount('/organization/admin');
      await act(async () => renderer.root.findByProps({ id: 'org-icon-file' }).props.onChange({
        target: { files: [{ type: 'image/png', size: 100 }] },
      }));
      await act(async () => renderer.unmount());
      assert.deepEqual(revoked, ['blob:preview']);
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
      URL.revokeObjectURL = originalRevokeObjectURL;
    }
  });
});
