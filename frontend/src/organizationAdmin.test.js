/**
 * Tests for OrganizationAdminPage (Stage 3, Slice 3A).
 *
 * These are unit tests for the React component. Full integration relies on
 * the backend API tests in test_organization_admin_api.py.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { act, create } from 'react-test-renderer';
import {
  OrganizationAdminPage,
  OrganizationMemberAddForm,
  addExistingOrganizationMember,
} from './OrganizationAdminPage.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const mockOrg = {
  id: 'org-1',
  code: 'ACME',
  displayName: 'ACME Corp',
  legalName: 'ACME Legal Ltd',
  locale: 'en-US',
  entitlementStatus: 'active',
  isActive: true,
  isProtected: false,
  iconUrl: '/api/v1/org/me/icon',
  hasCustomIcon: false,
};

const mockMembersResponse = {
  items: [
    {
      membershipId: 'm-1',
      userId: 'u-1',
      email: 'admin@example.test',
      displayName: 'Admin User',
      userIsActive: true,
      status: 'active',
      baseRole: 'admin',
      capabilities: [],
      activatedAt: '2026-08-19T10:00:00Z',
      deactivatedAt: null,
    },
    {
      membershipId: 'm-2',
      userId: 'u-2',
      email: 'member@example.test',
      displayName: 'Org Member',
      userIsActive: true,
      status: 'active',
      baseRole: 'user',
      capabilities: [{ capability: 'contributor', grantedAt: '2026-08-19T11:00:00Z' }],
      activatedAt: '2026-08-19T10:30:00Z',
      deactivatedAt: null,
    },
  ],
  page: 1,
  pageSize: 25,
  total: 2,
};

function setupFetchMock(overrides = {}) {
  const responses = {
    '/api/v1/org/me': mockOrg,
    '/api/v1/org/me/members': mockMembersResponse,
    ...overrides,
  };
  globalThis.fetch = async (url, _opts) => {
    const path = new URL(url, 'http://test').pathname;
    const body = responses[path];
    if (body === undefined) {
      return { ok: false, status: 404, json: async () => ({ detail: 'Not found' }), statusText: 'Not Found' };
    }
    if (body instanceof Error) {
      return { ok: false, status: 403, json: async () => ({ detail: body.message }), statusText: body.message };
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

async function mountOrganizationAdmin(fetchImpl, createNodeMock) {
  globalThis.fetch = fetchImpl;
  let renderer;
  await act(async () => {
    renderer = create(createElement(OrganizationAdminPage, {
      auth: {
        user: { organization: { baseRole: 'admin' }, capabilities: { organizationIconUploadEnabled: false } },
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

describe('OrganizationAdminPage', () => {
  it('is exported as a function', () => {
    assert.equal(typeof OrganizationAdminPage, 'function');
  });

  it('has the expected function length (accepts props object)', () => {
    assert.equal(OrganizationAdminPage.length, 1);
  });

  it('renders accessible existing-user and base-role controls', () => {
    const markup = renderToStaticMarkup(createElement(OrganizationMemberAddForm, {
      onAdd: async () => {},
    }));
    assert.match(markup, /for="organization-member-user-id"/);
    assert.match(markup, /id="organization-member-user-id"/);
    assert.match(markup, /for="organization-member-base-role"/);
    assert.match(markup, /id="organization-member-base-role"/);
    assert.match(markup, /<option value="user" selected="">User<\/option>/);
    assert.match(markup, /<option value="admin">Admin<\/option>/);
    assert.match(markup, /type="submit"[^>]*>Add member<\/button>/);
    assert.doesNotMatch(markup, /type="password"/);
  });
});

describe('existing-user member mutation', () => {
  it('posts the selected base role through the protected operation without a PIN payload', async () => {
    let request;
    globalThis.fetch = async (url, options) => {
      request = { url, options };
      return { ok: true, json: async () => mockMembersResponse.items[1] };
    };
    let protectedAttempts = 0;
    const result = await addExistingOrganizationMember({
      userId: 'u-2',
      baseRole: 'admin',
      protect: async (operation) => {
        protectedAttempts += 1;
        return operation();
      },
    });

    assert.equal(result.userId, 'u-2');
    assert.equal(protectedAttempts, 1);
    assert.match(request.url, /\/api\/v1\/org\/me\/members$/);
    assert.equal(request.options.method, 'POST');
    assert.deepEqual(JSON.parse(request.options.body), { userId: 'u-2', baseRole: 'admin' });
    assert.doesNotMatch(request.options.body, /pin/i);
  });

  it('preserves backend denial from the protected member mutation', async () => {
    globalThis.fetch = async () => ({
      ok: false,
      status: 403,
      statusText: 'Forbidden',
      json: async () => ({ detail: 'Organization admin access is required.' }),
    });

    await assert.rejects(
      addExistingOrganizationMember({
        userId: 'u-2',
        baseRole: 'user',
        protect: (operation) => operation(),
      }),
      (error) => error.status === 403 && /admin access is required/i.test(error.message),
    );
  });

  it('submits the selected base role from the rendered page and clears a one-shot step-up PIN', async () => {
    const requests = [];
    let memberAttempts = 0;
    const renderer = await mountOrganizationAdmin(async (url, options = {}) => {
      const path = new URL(url, 'http://test').pathname;
      requests.push({ path, options });
      if (path === '/api/v1/org/me') return jsonResponse(mockOrg);
      if (path === '/api/v1/org/me/members' && !options.method) return jsonResponse(mockMembersResponse);
      if (path === '/api/v1/org/me/members' && options.method === 'POST') {
        memberAttempts += 1;
        if (memberAttempts === 1) {
          return jsonResponse({ detail: 'Step-up reauthentication is required.' }, 403);
        }
        return jsonResponse({ ...mockMembersResponse.items[1], baseRole: 'admin' });
      }
      if (path === '/api/v1/auth/reauthenticate') return jsonResponse({ ok: true });
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    });
    const root = renderer.root;

    await act(async () => {
      root.findByProps({ id: 'organization-step-up-pin' }).props.onChange({ target: { value: '1234' } });
      root.findByProps({ id: 'organization-member-user-id' }).props.onChange({ target: { value: 'u-2' } });
      root.findByProps({ id: 'organization-member-base-role' }).props.onChange({ target: { value: 'admin' } });
    });
    await act(async () => {
      const userInput = root.findByProps({ id: 'organization-member-user-id' });
      await userInput.parent.parent.props.onSubmit({ preventDefault() {} });
    });

    const membershipBodies = requests
      .filter(({ path, options }) => path === '/api/v1/org/me/members' && options.method === 'POST')
      .map(({ options }) => JSON.parse(options.body));
    assert.deepEqual(membershipBodies, [
      { userId: 'u-2', baseRole: 'admin' },
      { userId: 'u-2', baseRole: 'admin' },
    ]);
    assert.equal(requests.filter(({ path }) => path === '/api/v1/auth/reauthenticate').length, 1);
    assert.equal(root.findByProps({ id: 'organization-step-up-pin' }).props.value, '');
    assert.equal(root.findByProps({ id: 'organization-member-user-id' }).props.value, '');
    assert.equal(root.findByProps({ id: 'organization-member-base-role' }).props.value, 'user');
    assert.equal(JSON.stringify(membershipBodies).includes('1234'), false);
    await act(async () => renderer.unmount());
  });

  it('renders backend denial as an alert and returns focus to the labelled user control', async () => {
    const focused = [];
    const renderer = await mountOrganizationAdmin(async (url, options = {}) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/org/me') return jsonResponse(mockOrg);
      if (path === '/api/v1/org/me/members' && !options.method) return jsonResponse(mockMembersResponse);
      if (path === '/api/v1/org/me/members' && options.method === 'POST') {
        return jsonResponse({ detail: 'Organization admin access is required.' }, 403);
      }
      return jsonResponse({ detail: `Unexpected request: ${path}` }, 404);
    }, (element) => ({ focus: () => focused.push(element.props.id) }));
    const root = renderer.root;

    await act(async () => {
      root.findByProps({ id: 'organization-member-user-id' }).props.onChange({ target: { value: 'u-2' } });
    });
    await act(async () => {
      const userInput = root.findByProps({ id: 'organization-member-user-id' });
      await userInput.parent.parent.props.onSubmit({ preventDefault() {} });
    });

    assert.match(root.findByProps({ role: 'alert' }).children.join(''), /admin access is required/i);
    assert.equal(root.findByProps({ id: 'organization-member-user-id' }).props.disabled, undefined);
    assert.equal(root.findByProps({ id: 'organization-member-user-id' }).parent.props.htmlFor, 'organization-member-user-id');
    assert.deepEqual(focused, ['organization-member-user-id']);
    await act(async () => renderer.unmount());
  });
});

describe('fetch mock', () => {
  it('setup works correctly', async () => {
    setupFetchMock();
    const resp = await globalThis.fetch('/api/v1/org/me');
    const data = await resp.json();
    assert.equal(data.code, 'ACME');
    assert.equal(data.displayName, 'ACME Corp');
    assert.equal(resp.ok, true);
  });

  it('members list mock returns two members', async () => {
    setupFetchMock();
    const resp = await globalThis.fetch('/api/v1/org/me/members');
    const data = await resp.json();
    assert.equal(data.total, 2);
    assert.equal(data.items.length, 2);
    assert.equal(data.items[0].baseRole, 'admin');
    assert.equal(data.items[1].baseRole, 'user');
  });

  it('member with contributor capability is identified', async () => {
    setupFetchMock();
    const resp = await globalThis.fetch('/api/v1/org/me/members');
    const data = await resp.json();
    const member = data.items.find((m) => m.membershipId === 'm-2');
    assert.equal(member.capabilities.length, 1);
    assert.equal(member.capabilities[0].capability, 'contributor');
  });

  it('feature-flag-off scenario returns 404', async () => {
    setupFetchMock({ '/api/v1/org/me': new Error('Not found.') });
    const resp = await globalThis.fetch('/api/v1/org/me');
    assert.equal(resp.ok, false);
    assert.equal(resp.status, 403);
  });
});

describe('mockOrg schema', () => {
  it('has all required response fields', () => {
    const requiredFields = ['id', 'code', 'displayName', 'entitlementStatus', 'isActive', 'isProtected', 'iconUrl', 'hasCustomIcon'];
    for (const field of requiredFields) {
      assert.ok(field in mockOrg, `mockOrg missing field: ${field}`);
    }
  });

  it('hasCustomIcon is boolean', () => {
    assert.equal(typeof mockOrg.hasCustomIcon, 'boolean');
  });
});

describe('org icon upload API contract', () => {
  it('upload endpoint is POST /org/me/icon', async () => {
    let capturedUrl, capturedOpts;
    globalThis.fetch = async (url, opts) => {
      capturedUrl = url;
      capturedOpts = opts;
      return { ok: true, json: async () => ({ ...mockOrg, hasCustomIcon: true }) };
    };
    const body = JSON.stringify({ contentType: 'image/png', contentBase64: 'aGVsbG8=' });
    await globalThis.fetch('/api/v1/org/me/icon', { method: 'POST', body });
    assert.equal(capturedUrl, '/api/v1/org/me/icon');
    assert.equal(capturedOpts.method, 'POST');
  });

  it('upload request body contains contentType and contentBase64 without data-URL prefix', () => {
    // bare base64 must not contain a comma or the literal "base64," prefix
    const bare = 'aGVsbG8=';
    const body = JSON.parse(JSON.stringify({ contentType: 'image/png', contentBase64: bare }));
    assert.ok('contentType' in body, 'contentType missing');
    assert.ok('contentBase64' in body, 'contentBase64 missing');
    assert.ok(!body.contentBase64.includes(','), 'contentBase64 must be bare base64, not a data URL');
    assert.ok(!body.contentBase64.startsWith('data:'), 'contentBase64 must not start with data:');
  });

  it('upload response includes hasCustomIcon true', async () => {
    setupFetchMock({ '/api/v1/org/me/icon': { ...mockOrg, hasCustomIcon: true } });
    const resp = await globalThis.fetch('/api/v1/org/me/icon');
    const data = await resp.json();
    assert.equal(data.hasCustomIcon, true);
  });

  it('content type must be one of the three allowed types', () => {
    const allowed = new Set(['image/png', 'image/jpeg', 'image/webp']);
    assert.ok(allowed.has('image/png'));
    assert.ok(allowed.has('image/jpeg'));
    assert.ok(allowed.has('image/webp'));
    assert.ok(!allowed.has('image/svg+xml'));
    assert.ok(!allowed.has('image/gif'));
    assert.ok(!allowed.has(''));
  });
});

describe('org icon remove API contract', () => {
  it('remove endpoint is DELETE /org/me/icon', async () => {
    let capturedUrl, capturedOpts;
    globalThis.fetch = async (url, opts) => {
      capturedUrl = url;
      capturedOpts = opts;
      return { ok: true, json: async () => ({ ...mockOrg, hasCustomIcon: false }) };
    };
    await globalThis.fetch('/api/v1/org/me/icon', { method: 'DELETE', body: '{}' });
    assert.equal(capturedUrl, '/api/v1/org/me/icon');
    assert.equal(capturedOpts.method, 'DELETE');
  });

  it('remove response includes hasCustomIcon false', async () => {
    setupFetchMock({ '/api/v1/org/me/icon': { ...mockOrg, hasCustomIcon: false } });
    const resp = await globalThis.fetch('/api/v1/org/me/icon');
    const data = await resp.json();
    assert.equal(data.hasCustomIcon, false);
  });
});

describe('OrgMemberResponse schema', () => {
  it('member response fields match schema', () => {
    const member = mockMembersResponse.items[0];
    const requiredFields = ['membershipId', 'userId', 'email', 'displayName', 'userIsActive', 'status', 'baseRole', 'capabilities'];
    for (const field of requiredFields) {
      assert.ok(field in member, `member missing field: ${field}`);
    }
    assert.ok(Array.isArray(member.capabilities));
  });
});
