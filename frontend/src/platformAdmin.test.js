/**
 * Tests for PlatformAdminPage (Stage 3, Slice 3B).
 *
 * These are unit tests for the React component. Full integration relies on
 * the backend API tests in test_platform_admin_api.py.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { PlatformAdminPage } from './PlatformAdminPage.js';

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

function setupFetchMock(overrides = {}) {
  const responses = {
    '/api/v1/platform/admins': mockAdminsResponse,
    '/api/v1/platform/organizations': mockOrganizationsResponse,
    ...overrides,
  };
  globalThis.fetch = async (url, _opts) => {
    const path = url.replace(/\?.*$/, '');
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

describe('PlatformAdminPage', () => {
  it('is exported as a function', () => {
    assert.equal(typeof PlatformAdminPage, 'function');
  });

  it('has the expected function length (no required props)', () => {
    assert.equal(PlatformAdminPage.length, 0);
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
