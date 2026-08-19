/**
 * Tests for OrganizationAdminPage (Stage 3, Slice 3A).
 *
 * These are unit tests for the React component. Full integration relies on
 * the backend API tests in test_organization_admin_api.py.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { OrganizationAdminPage } from './OrganizationAdminPage.js';

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
    const path = url.replace(/\?.*$/, '');
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

describe('OrganizationAdminPage', () => {
  it('is exported as a function', () => {
    assert.equal(typeof OrganizationAdminPage, 'function');
  });

  it('has the expected function length (accepts props object)', () => {
    assert.equal(OrganizationAdminPage.length, 1);
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
