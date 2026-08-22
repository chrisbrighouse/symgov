/**
 * Tests for PlatformAdminPage (Stage 3, Slice 3B).
 *
 * These are unit tests for the React component. Full integration relies on
 * the backend API tests in test_platform_admin_api.py.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import {
  CreateOrganizationForm,
  GrantAdminForm,
  PlatformAdminPage,
  grantExistingPlatformAdmin,
} from './PlatformAdminPage.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mockAdminsResponse = {
  items: [
    {
      userId: 'u-1',
      email: 'platform-admin@example.test',
      displayName: 'Platform Admin',
      userIsActive: true,
      grantedAt: '2026-08-19T10:00:00Z',
    },
    {
      userId: 'u-2',
      email: 'candidate@example.test',
      displayName: 'Candidate Admin',
      userIsActive: true,
      grantedAt: '2026-08-19T11:00:00Z',
    },
  ],
  page: 1,
  pageSize: 50,
  total: 2,
};

const mockOrganizationsResponse = {
  items: [
    {
      id: 'org-1',
      code: 'symgov',
      displayName: 'Symgov',
      legalName: null,
      entitlementStatus: 'active',
      isActive: true,
      isProtected: true,
    },
    {
      id: 'org-2',
      code: 'ACME',
      displayName: 'Acme Inc',
      legalName: 'Acme Incorporated',
      entitlementStatus: 'suspended',
      isActive: true,
      isProtected: false,
    },
  ],
  page: 1,
  pageSize: 50,
  total: 2,
};

const mockSymgovMembersResponse = {
  items: [{
    membershipId: 'm-1', userId: 'u-3', email: 'member@example.test',
    displayName: 'Protected Member', userIsActive: true, status: 'active',
    baseRole: 'user', capabilities: [], activatedAt: '2026-08-19T12:00:00Z', deactivatedAt: null,
  }],
  page: 1, pageSize: 50, total: 1,
};

function setupFetchMock(overrides = {}) {
  const responses = {
    '/api/v1/platform/admins': mockAdminsResponse,
    '/api/v1/platform/organizations': mockOrganizationsResponse,
    ...overrides,
  };
  globalThis.fetch = async (url, _opts) => {
    const path = new URL(url, 'http://test').pathname;
    const body = responses[path];
    if (body === undefined) {
      return { ok: false, status: 404, json: async () => ({ detail: 'Not found' }), statusText: 'Not Found' };
    }
    if (body instanceof Error) {
      const status = body.status || 403;
      return { ok: false, status, json: async () => ({ detail: body.message }), statusText: body.message };
    }
    return { ok: true, json: async () => body };
  };
}

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Forbidden',
    json: async () => body,
  };
}

async function mountPlatformAdmin(fetchImpl, createNodeMock) {
  globalThis.fetch = fetchImpl;
  let renderer;
  await act(async () => {
    renderer = create(createElement(PlatformAdminPage, {
      auth: {
        reauthenticate: async ({ pin }) => {
          const response = await globalThis.fetch('/api/v1/auth/reauthenticate', {
            method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ pin }),
          });
          if (!response.ok) {
            const error = new Error((await response.json()).detail || response.statusText);
            error.status = response.status;
            throw error;
          }
        },
      },
    }), { createNodeMock });
  });
  return renderer;
}

describe('PlatformAdminPage', () => {
  it('is exported as a function', () => {
    assert.equal(typeof PlatformAdminPage, 'function');
  });

  it('accepts the authenticated session API', () => {
    assert.equal(PlatformAdminPage.length, 1);
  });

  it('renders labelled platform-admin and organization mutation controls', () => {
    const adminMarkup = renderToStaticMarkup(createElement(GrantAdminForm, { onGrant: async () => {} }));
    const organizationMarkup = renderToStaticMarkup(createElement(CreateOrganizationForm, { onCreate: async () => {} }));
    assert.match(adminMarkup, /for="platform-admin-user-id"/);
    assert.match(adminMarkup, /type="submit"[^>]*>Grant platform admin<\/button>/);
    assert.match(organizationMarkup, /for="new-org-code"/);
    assert.match(organizationMarkup, /for="new-org-display-name"/);
    assert.match(organizationMarkup, /for="new-org-initial-admin"/);
    assert.doesNotMatch(adminMarkup + organizationMarkup, /type="password"/);
  });
});

describe('protected platform mutation', () => {
  it('grants an existing user through protection without putting the PIN in the request', async () => {
    let request;
    globalThis.fetch = async (url, options) => {
      request = { url, options };
      return { ok: true, json: async () => mockAdminsResponse.items[1] };
    };
    const result = await grantExistingPlatformAdmin({
      userId: 'u-2',
      protect: (operation) => operation(),
    });
    assert.equal(result.userId, 'u-2');
    assert.match(request.url, /\/api\/v1\/platform\/admins$/);
    assert.deepEqual(JSON.parse(request.options.body), { userId: 'u-2' });
    assert.doesNotMatch(request.options.body, /pin/i);
  });

  it('preserves backend denial', async () => {
    globalThis.fetch = async () => ({
      ok: false, status: 403, statusText: 'Forbidden',
      json: async () => ({ detail: 'Platform admin access is required.' }),
    });
    await assert.rejects(
      grantExistingPlatformAdmin({ userId: 'u-2', protect: (operation) => operation() }),
      (error) => error.status === 403 && /platform admin access/i.test(error.message),
    );
  });

  it('submits a rendered grant through one-shot step-up and clears the PIN', async () => {
    const requests = [];
    let grantAttempts = 0;
    const renderer = await mountPlatformAdmin(async (url, options = {}) => {
      const path = new URL(url, 'http://test').pathname;
      requests.push({ path, options });
      if (path === '/api/v1/platform/admins' && !options.method) return jsonResponse(mockAdminsResponse);
      if (path === '/api/v1/platform/organizations') return jsonResponse(mockOrganizationsResponse);
      if (path === '/api/v1/platform/admins' && options.method === 'POST') {
        grantAttempts += 1;
        if (grantAttempts === 1) {
          return jsonResponse({ detail: 'Step-up reauthentication has expired.' }, 403);
        }
        return jsonResponse(mockAdminsResponse.items[1]);
      }
      if (path === '/api/v1/auth/reauthenticate') return jsonResponse({ ok: true });
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    });
    const root = renderer.root;

    await act(async () => {
      root.findByProps({ id: 'platform-step-up-pin' }).props.onChange({ target: { value: '1234' } });
      root.findByProps({ id: 'platform-admin-user-id' }).props.onChange({ target: { value: 'u-2' } });
    });
    await act(async () => {
      const userInput = root.findByProps({ id: 'platform-admin-user-id' });
      await userInput.parent.parent.props.onSubmit({ preventDefault() {} });
    });

    const grantBodies = requests
      .filter(({ path, options }) => path === '/api/v1/platform/admins' && options.method === 'POST')
      .map(({ options }) => JSON.parse(options.body));
    assert.deepEqual(grantBodies, [{ userId: 'u-2' }, { userId: 'u-2' }]);
    assert.equal(requests.filter(({ path }) => path === '/api/v1/auth/reauthenticate').length, 1);
    assert.equal(root.findByProps({ id: 'platform-step-up-pin' }).props.value, '');
    assert.equal(root.findByProps({ id: 'platform-admin-user-id' }).props.value, '');
    assert.equal(JSON.stringify(grantBodies).includes('1234'), false);
    await act(async () => renderer.unmount());
  });

  it('keeps the rendered grant usable and focused after backend denial', async () => {
    const focused = [];
    const renderer = await mountPlatformAdmin(async (url, options = {}) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/admins' && !options.method) return jsonResponse(mockAdminsResponse);
      if (path === '/api/v1/platform/organizations') return jsonResponse(mockOrganizationsResponse);
      if (path === '/api/v1/platform/organizations/symgov/members') return jsonResponse(mockSymgovMembersResponse);
      if (path === '/api/v1/platform/admins' && options.method === 'POST') {
        return jsonResponse({ detail: 'Platform admin access is required.' }, 403);
      }
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    }, (element) => ({ focus: () => focused.push(element.props.id) }));
    const root = renderer.root;

    await act(async () => {
      root.findByProps({ id: 'platform-admin-user-id' }).props.onChange({ target: { value: 'u-2' } });
    });
    await act(async () => {
      const userInput = root.findByProps({ id: 'platform-admin-user-id' });
      await userInput.parent.parent.props.onSubmit({ preventDefault() {} });
    });

    assert.match(root.findByProps({ role: 'alert' }).children.join(''), /platform admin access is required/i);
    assert.equal(root.findByProps({ id: 'platform-admin-user-id' }).props.disabled, undefined);
    assert.equal(root.findByProps({ id: 'platform-admin-user-id' }).parent.props.htmlFor, 'platform-admin-user-id');
    assert.deepEqual(focused, ['platform-admin-user-id']);
    await act(async () => renderer.unmount());
  });

  it('mounts protected Symgov member administration with bounded, labelled controls', async () => {
    const renderer = await mountPlatformAdmin(async (url, options = {}) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/admins') return jsonResponse(mockAdminsResponse);
      if (path === '/api/v1/platform/organizations') return jsonResponse(mockOrganizationsResponse);
      if (path === '/api/v1/platform/organizations/symgov/members' && !options.method) return jsonResponse(mockSymgovMembersResponse);
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    });
    const root = renderer.root;
    assert.equal(root.findByProps({ id: 'protected-symgov-members-heading' }).children.join(''), 'Protected Symgov members (1)');
    assert.equal(root.findByProps({ id: 'protected-member-reason' }).props.minLength, 10);
    assert.equal(root.findByProps({ id: 'protected-member-reason' }).props.maxLength, 1000);
    assert.equal(root.findByProps({ id: 'protected-member-mutation-reason-m-1' }).props.minLength, 10);
    assert.equal(root.findByProps({ 'aria-label': 'Promote Protected Member' }).children.join(''), 'Promote');
    assert.equal(root.findByProps({ 'aria-label': 'Deactivate Protected Member' }).children.join(''), 'Deactivate');
    await act(async () => renderer.unmount());
  });

  it('executes protected add, promote, and deactivate with one safe retry and no retained PIN', async () => {
    const requests = [];
    let addAttempts = 0;
    const renderer = await mountPlatformAdmin(async (url, options = {}) => {
      const path = new URL(url, 'http://test').pathname;
      requests.push({ path, options });
      if (path === '/api/v1/platform/admins') return jsonResponse(mockAdminsResponse);
      if (path === '/api/v1/platform/organizations') return jsonResponse(mockOrganizationsResponse);
      if (path === '/api/v1/platform/organizations/symgov/members' && !options.method) return jsonResponse(mockSymgovMembersResponse);
      if (path === '/api/v1/platform/organizations/symgov/members' && options.method === 'POST') {
        addAttempts += 1;
        if (addAttempts === 1) return jsonResponse({ detail: 'Step-up reauthentication has expired.' }, 403);
        return jsonResponse(mockSymgovMembersResponse.items[0], 201);
      }
      if (path === '/api/v1/auth/reauthenticate') return jsonResponse({ ok: true });
      if (path.endsWith('/m-1') && options.method === 'PATCH') return jsonResponse({ ...mockSymgovMembersResponse.items[0], baseRole: 'admin' });
      if (path.endsWith('/m-1/deactivate') && options.method === 'POST') return jsonResponse(null, 204);
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    });
    const root = renderer.root;
    await act(async () => {
      root.findByProps({ id: 'platform-step-up-pin' }).props.onChange({ target: { value: '1234' } });
      root.findByProps({ id: 'protected-member-user-id' }).props.onChange({ target: { value: 'u-4' } });
      root.findByProps({ id: 'protected-member-reason' }).props.onChange({ target: { value: 'Approved onboarding request' } });
    });
    await act(async () => root.findByProps({ id: 'protected-member-user-id' }).parent.parent.props.onSubmit({ preventDefault() {} }));
    assert.equal(addAttempts, 2);
    assert.equal(requests.filter(({ path }) => path === '/api/v1/auth/reauthenticate').length, 1);
    assert.equal(root.findByProps({ id: 'platform-step-up-pin' }).props.value, '');
    await act(async () => root.findByProps({ id: 'protected-member-mutation-reason-m-1' }).props.onChange({ target: { value: 'Approved role correction' } }));
    await act(async () => root.findByProps({ 'aria-label': 'Promote Protected Member' }).props.onClick());
    await act(async () => root.findByProps({ id: 'protected-member-mutation-reason-m-1' }).props.onChange({ target: { value: 'Membership no longer required' } }));
    await act(async () => root.findByProps({ 'aria-label': 'Deactivate Protected Member' }).props.onClick());
    const mutationBodies = requests
      .filter(({ path, options }) => path.startsWith('/api/v1/platform/organizations/symgov/members') && (options.method === 'POST' || options.method === 'PATCH'))
      .map(({ options }) => options.body && JSON.parse(options.body));
    assert.ok(mutationBodies.some((body) => body?.baseRole === 'admin' && body?.reason === 'Approved role correction'));
    assert.ok(mutationBodies.some((body) => body?.reason === 'Membership no longer required'));
    assert.equal(JSON.stringify(mutationBodies).includes('1234'), false);
    await act(async () => renderer.unmount());
  });

  it('keeps protected controls absent when the server denies effective authority', async () => {
    const renderer = await mountPlatformAdmin(async (url) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/admins') return jsonResponse(mockAdminsResponse);
      if (path === '/api/v1/platform/organizations') return jsonResponse(mockOrganizationsResponse);
      if (path === '/api/v1/platform/organizations/symgov/members') return jsonResponse({ detail: 'Platform admin access is required.' }, 403);
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    });
    const root = renderer.root;
    assert.equal(root.findAllByProps({ id: 'protected-member-user-id' }).length, 0);
    assert.match(root.findByProps({ role: 'alert' }).children.join(''), /platform admin access is required/i);
    await act(async () => renderer.unmount());
  });
});

describe('fetch mock', () => {
  it('setup works correctly', async () => {
    setupFetchMock();
    const resp = await globalThis.fetch('/api/v1/platform/admins');
    const data = await resp.json();
    assert.equal(data.total, 2);
    assert.equal(resp.ok, true);
  });

  it('admin list mock returns two admins', async () => {
    setupFetchMock();
    const resp = await globalThis.fetch('/api/v1/platform/admins');
    const data = await resp.json();
    assert.equal(data.items.length, 2);
    assert.equal(data.items[0].email, 'platform-admin@example.test');
    assert.equal(data.items[1].email, 'candidate@example.test');
  });

  it('feature-flag-off scenario returns 404', async () => {
    const notFound = new Error('Not found.');
    notFound.status = 404;
    setupFetchMock({ '/api/v1/platform/admins': notFound });
    const resp = await globalThis.fetch('/api/v1/platform/admins');
    assert.equal(resp.ok, false);
    assert.equal(resp.status, 404);
  });
});

describe('PlatformAdminItem schema', () => {
  it('admin item fields match schema', () => {
    const admin = mockAdminsResponse.items[0];
    const requiredFields = ['userId', 'email', 'displayName', 'userIsActive', 'grantedAt'];
    for (const field of requiredFields) {
      assert.ok(field in admin, `admin missing field: ${field}`);
    }
  });
});

describe('PlatformAdminListResponse schema', () => {
  it('has all required response fields', () => {
    const requiredFields = ['items', 'page', 'pageSize', 'total'];
    for (const field of requiredFields) {
      assert.ok(field in mockAdminsResponse, `mockAdminsResponse missing field: ${field}`);
    }
    assert.ok(Array.isArray(mockAdminsResponse.items));
  });
});

describe('organization directory fetch mock', () => {
  it('organization list mock returns two organizations', async () => {
    setupFetchMock();
    const resp = await globalThis.fetch('/api/v1/platform/organizations');
    const data = await resp.json();
    assert.equal(data.total, 2);
    assert.equal(data.items[0].code, 'symgov');
    assert.equal(data.items[1].entitlementStatus, 'suspended');
  });

  it('feature-flag-off scenario returns 404', async () => {
    const notFound = new Error('Not found.');
    notFound.status = 404;
    setupFetchMock({ '/api/v1/platform/organizations': notFound });
    const resp = await globalThis.fetch('/api/v1/platform/organizations');
    assert.equal(resp.ok, false);
    assert.equal(resp.status, 404);
  });
});

describe('PlatformOrganizationItem schema', () => {
  it('organization item fields match schema', () => {
    const organization = mockOrganizationsResponse.items[0];
    const requiredFields = ['id', 'code', 'displayName', 'legalName', 'entitlementStatus', 'isActive', 'isProtected'];
    for (const field of requiredFields) {
      assert.ok(field in organization, `organization missing field: ${field}`);
    }
  });
});

describe('PlatformOrganizationListResponse schema', () => {
  it('has all required response fields', () => {
    const requiredFields = ['items', 'page', 'pageSize', 'total'];
    for (const field of requiredFields) {
      assert.ok(field in mockOrganizationsResponse, `mockOrganizationsResponse missing field: ${field}`);
    }
    assert.ok(Array.isArray(mockOrganizationsResponse.items));
  });
});
