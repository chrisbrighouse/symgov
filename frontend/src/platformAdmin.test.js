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
import { PlatformOrganizationUsageDashboardSection } from './UsageDashboardSection.js';

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

// --- WP7.8: demotion console and promotion-request review panel ---

const platformAdminUser = {
  session: { purpose: 'application', mode: 'organization', activeOrganizationId: 'org-symgov' },
  organization: { id: 'org-symgov', code: 'symgov', baseRole: 'admin', capabilities: [] },
  capabilities: { organizationsEnabled: true, organizationSymbolsEnabled: true, platformAdminEnabled: true },
};

async function mountPlatformAdminWithUser(fetchImpl, user, createNodeMock) {
  globalThis.fetch = fetchImpl;
  let renderer;
  await act(async () => {
    renderer = create(createElement(PlatformAdminPage, {
      auth: {
        user,
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

function baselineFetch(overrides = {}) {
  return async (url, options = {}) => {
    const path = new URL(url, 'http://test').pathname;
    if (path === '/api/v1/platform/admins' && !options.method) return jsonResponse(mockAdminsResponse);
    if (path === '/api/v1/platform/organizations' && !options.method) return jsonResponse(mockOrganizationsResponse);
    if (path === '/api/v1/platform/organizations/symgov/members' && !options.method) return jsonResponse(mockSymgovMembersResponse);
    const override = overrides[path];
    if (override) return override(options);
    return jsonResponse({ detail: `Unexpected request: ${path} ${options.method || 'GET'}` }, 404);
  };
}

describe('demotion console (WP7.8)', () => {
  it('is not rendered when the organizationSymbolsEnabled/platformAdminEnabled capabilities are absent', async () => {
    const renderer = await mountPlatformAdminWithUser(baselineFetch(), null);
    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /Demote a public symbol/);
    await act(async () => renderer.unmount());
  });

  it('previews impact, then demotes only after a reason is given, without leaking the PIN', async () => {
    const preview = {
      governedSymbolId: 'sym-1', eligible: true, reasons: [], blockingOrganizationIds: [], favouritesCount: 3,
    };
    const demoteResult = {
      governedSymbolId: 'sym-1', visibility: 'organization_private',
      symbolRevisionIds: ['rev-1'], publishedPageIds: ['page-1'], packEntryIds: ['entry-1'], retiredPackIds: ['pack-1'],
    };
    const requests = [];
    const renderer = await mountPlatformAdminWithUser(baselineFetch({
      '/api/v1/platform/governed-symbols/sym-1/demotion-impact-preview': () => jsonResponse(preview),
      '/api/v1/platform/governed-symbols/sym-1/demote': (options) => {
        requests.push(JSON.parse(options.body));
        return jsonResponse(demoteResult);
      },
    }), platformAdminUser);

    await act(async () => {
      renderer.root.findByProps({ id: 'demotion-symbol-id' }).props.onChange({ target: { value: 'sym-1' } });
    });
    await act(async () => {
      await renderer.root.findByProps({ id: 'demotion-symbol-id' }).parent.parent.props.onSubmit({ preventDefault() {} });
    });
    assert.match(JSON.stringify(renderer.toJSON()), /Eligible for demotion/);

    const demoteButton = renderer.root.findByProps({ 'aria-label': 'Demote governed symbol sym-1' });
    assert.equal(demoteButton.props.disabled, true, 'must stay disabled until a reason is entered');

    await act(async () => {
      renderer.root.findByProps({ id: 'demotion-reason' }).props.onChange({ target: { value: 'Superseded by a newer symbol.' } });
    });
    globalThis.window = globalThis.window || {};
    const originalConfirm = globalThis.window.confirm;
    globalThis.window.confirm = () => true;
    await act(async () => {
      await renderer.root.findByProps({ 'aria-label': 'Demote governed symbol sym-1' }).props.onClick();
    });
    globalThis.window.confirm = originalConfirm;

    assert.deepEqual(requests, [{ reason: 'Superseded by a newer symbol.' }]);
    assert.match(JSON.stringify(renderer.toJSON()), /Demoted\. Visibility is now \\"organization_private\\"/);
    await act(async () => renderer.unmount());
  });

  it('does not offer a demote action when the symbol is ineligible', async () => {
    const preview = {
      governedSymbolId: 'sym-2', eligible: false, reasons: ['Referenced by another organization’s Symbol Set.'],
      blockingOrganizationIds: ['org-acme'], favouritesCount: 0,
    };
    const renderer = await mountPlatformAdminWithUser(baselineFetch({
      '/api/v1/platform/governed-symbols/sym-2/demotion-impact-preview': () => jsonResponse(preview),
    }), platformAdminUser);

    await act(async () => {
      renderer.root.findByProps({ id: 'demotion-symbol-id' }).props.onChange({ target: { value: 'sym-2' } });
    });
    await act(async () => {
      await renderer.root.findByProps({ id: 'demotion-symbol-id' }).parent.parent.props.onSubmit({ preventDefault() {} });
    });

    assert.match(JSON.stringify(renderer.toJSON()), /Not eligible for demotion/);
    assert.throws(() => renderer.root.findByProps({ id: 'demotion-reason' }));
    await act(async () => renderer.unmount());
  });
});

describe('promotion-request review panel (WP7.8)', () => {
  it('opens a promotion request for review, then accepts it with an accept-only decision', async () => {
    const opened = {
      id: 'req-1', governedSymbolId: 'sym-1', organizationId: 'org-acme', symbolRevisionId: 'rev-1',
      status: 'triage', proposedMetadata: {}, reason: 'Widely used.', sharingAcknowledgment: true,
      submittedByUserId: 'user-9', submittedAt: '2026-09-03T00:00:00Z', closedAt: null, traceId: null,
      reviewCaseId: 'case-1',
    };
    const decisionRequests = [];
    const renderer = await mountPlatformAdminWithUser(baselineFetch({
      '/api/v1/organization-symbols/sym-1/promotion-requests/req-1/open-review': () => jsonResponse(opened),
      '/api/v1/workspace/review-cases/case-1/decisions': (options) => {
        decisionRequests.push(JSON.parse(options.body));
        return jsonResponse({ id: 'case-1', status: 'closed' });
      },
    }), platformAdminUser);

    await act(async () => {
      renderer.root.findByProps({ id: 'promotion-review-symbol-id' }).props.onChange({ target: { value: 'sym-1' } });
    });
    await act(async () => {
      renderer.root.findByProps({ id: 'promotion-review-request-id' }).props.onChange({ target: { value: 'req-1' } });
    });
    await act(async () => {
      await renderer.root.findByProps({ id: 'promotion-review-symbol-id' }).parent.parent.props.onSubmit({ preventDefault() {} });
    });
    assert.match(JSON.stringify(renderer.toJSON()), /Status: triage/);

    globalThis.window = globalThis.window || {};
    const originalConfirm = globalThis.window.confirm;
    globalThis.window.confirm = () => true;
    await act(async () => {
      await renderer.root.findByProps({ 'aria-label': 'Accept promotion request req-1' }).props.onClick();
    });
    globalThis.window.confirm = originalConfirm;

    assert.deepEqual(decisionRequests, [{ decisionCode: 'approve' }]);
    assert.match(JSON.stringify(renderer.toJSON()), /Accepted\. The symbol has been published\./);
    await act(async () => renderer.unmount());
  });
});

describe('PlatformOrganizationUsageDashboardSection (WP9.7)', () => {
  async function mountDashboard(fetchSummary) {
    let renderer;
    await act(async () => {
      renderer = create(createElement(PlatformOrganizationUsageDashboardSection, {
        organizationId: 'org-9',
        organizationLabel: 'ACME Corp',
        fetchSummary,
      }));
    });
    return renderer;
  }

  it('calls the injected fetchSummary with the given organizationId', async () => {
    const seenOrganizationIds = [];
    const renderer = await mountDashboard(async (organizationId) => {
      seenOrganizationIds.push(organizationId);
      return { organizationId, since: '2026-08-05', until: '2026-09-04', eventTypes: [] };
    });
    assert.deepEqual(seenOrganizationIds, ['org-9']);
    assert.match(JSON.stringify(renderer.toJSON()), /ACME Corp/);
    await act(async () => renderer.unmount());
  });

  it('renders stat tiles with totals and visibly surfaces suppressedDayCount', async () => {
    const renderer = await mountDashboard(async (organizationId) => ({
      organizationId,
      since: '2026-08-05',
      until: '2026-09-04',
      eventTypes: [
        {
          eventType: 'symbol_downloaded',
          days: [{ date: '2026-09-02', eventCount: 7, distinctUserCount: 5 }],
          suppressedDayCount: 2,
          totalEventCount: 7,
        },
      ],
    }));
    const markup = JSON.stringify(renderer.toJSON());
    assert.match(markup, /Symbol downloads/);
    assert.match(markup, /2026-09-02/);
    assert.match(markup, /Days suppressed/);
    await act(async () => renderer.unmount());
  });

  it('renders an explicit empty state when there is no activity in the window', async () => {
    const renderer = await mountDashboard(async (organizationId) => ({
      organizationId, since: '2026-08-05', until: '2026-09-04', eventTypes: [],
    }));
    assert.match(JSON.stringify(renderer.toJSON()), /No usage recorded/);
    await act(async () => renderer.unmount());
  });

  it('renders a friendly message (not a crash) when the endpoint 404s (flag disabled)', async () => {
    const renderer = await mountDashboard(async () => {
      const error = new Error('Not found');
      error.status = 404;
      throw error;
    });
    assert.match(renderer.root.findByProps({ role: 'alert' }).children.join(''), /not available/i);
    await act(async () => renderer.unmount());
  });
});

describe('PlatformAdminPage organization row usage dashboard trigger', () => {
  it('does not fetch a usage summary until "View usage" is clicked, then renders it for the selected organization', async () => {
    const usageRequests = [];
    const renderer = await mountPlatformAdmin(async (url, options = {}) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/admins') return jsonResponse(mockAdminsResponse);
      if (path === '/api/v1/platform/organizations') return jsonResponse(mockOrganizationsResponse);
      if (path === '/api/v1/platform/organizations/symgov/members') return jsonResponse(mockSymgovMembersResponse);
      if (path === '/api/v1/platform/organizations/org-2/usage-summary') {
        usageRequests.push(path);
        return jsonResponse({
          organizationId: 'org-2',
          since: '2026-08-05',
          until: '2026-09-04',
          eventTypes: [{
            eventType: 'symbol_downloaded',
            days: [{ date: '2026-09-01', eventCount: 4, distinctUserCount: 3 }],
            suppressedDayCount: 1,
            totalEventCount: 4,
          }],
        });
      }
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    });
    const root = renderer.root;

    assert.equal(usageRequests.length, 0, 'usage summary must not be fetched before the admin asks for it');

    await act(async () => {
      await root.findByProps({ 'aria-label': 'View usage dashboard for Acme Inc' }).props.onClick();
    });

    assert.deepEqual(usageRequests, ['/api/v1/platform/organizations/org-2/usage-summary']);
    const markup = JSON.stringify(renderer.toJSON());
    assert.match(markup, /Symbol downloads/);
    assert.match(markup, /Days suppressed/);
    await act(async () => renderer.unmount());
  });
});
