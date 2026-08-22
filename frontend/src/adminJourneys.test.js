import { describe, it } from 'node:test';
import assert from 'node:assert/strict';

import {
  canAccessOrganizationAdmin,
  canAccessPlatformAdmin,
  runWithStepUp,
} from './adminJourneys.js';

function stepUpRequired(status = 403) {
  const error = new Error('Step-up reauthentication is required.');
  error.status = status;
  return error;
}

describe('admin step-up journey', () => {
  it('reauthenticates without retaining the PIN and retries exactly once', async () => {
    let attempts = 0;
    let cleared = 0;
    const result = await runWithStepUp({
      pin: '1234',
      operation: async () => {
        attempts += 1;
        if (attempts === 1) throw stepUpRequired();
        return 'done';
      },
      reauthenticate: async (pin) => assert.equal(pin, '1234'),
      clearPin: () => { cleared += 1; },
    });
    assert.equal(result, 'done');
    assert.equal(attempts, 2);
    assert.equal(cleared, 1);
  });

  it('does not retry a denied mutation without a PIN', async () => {
    let attempts = 0;
    await assert.rejects(
      runWithStepUp({
        pin: '',
        operation: async () => { attempts += 1; throw stepUpRequired(); },
        reauthenticate: async () => assert.fail('must not reauthenticate'),
        clearPin: () => {},
      }),
      (error) => error.requiresStepUp === true,
    );
    assert.equal(attempts, 1);
  });

  it('keeps the legacy 401 step-up retry contract', async () => {
    let attempts = 0;
    const result = await runWithStepUp({
      pin: '1234',
      operation: async () => {
        attempts += 1;
        if (attempts === 1) throw stepUpRequired(401);
        return 'done';
      },
      reauthenticate: async () => {},
      clearPin: () => {},
    });
    assert.equal(result, 'done');
    assert.equal(attempts, 2);
  });

  it('retries the backend expired-step-up 403 contract', async () => {
    let attempts = 0;
    let cleared = 0;
    const result = await runWithStepUp({
      pin: '1234',
      operation: async () => {
        attempts += 1;
        if (attempts === 1) {
          const expired = new Error('Step-up reauthentication has expired.');
          expired.status = 403;
          throw expired;
        }
        return 'done';
      },
      reauthenticate: async () => {},
      clearPin: () => { cleared += 1; },
    });
    assert.equal(result, 'done');
    assert.equal(attempts, 2);
    assert.equal(cleared, 1);
  });

  it('does not reinterpret an ordinary authorization 403 as step-up', async () => {
    const denied = new Error('Platform admin access is required.');
    denied.status = 403;
    await assert.rejects(
      runWithStepUp({
        pin: '1234',
        operation: async () => { throw denied; },
        reauthenticate: async () => assert.fail('must not reauthenticate'),
        clearPin: () => assert.fail('must not clear an unused PIN'),
      }),
      denied,
    );
  });
});

function authorizedUser(overrides = {}) {
  return {
    session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
    organization: { id: 'org-1', code: 'symgov', baseRole: 'admin' },
    isPlatformAdmin: true,
    capabilities: { organizationAdminEnabled: true, platformAdminEnabled: true },
    ...overrides,
  };
}

describe('backend-authoritative admin access', () => {
  it('requires an active matching organization binding, admin role, and enabled feature', () => {
    assert.equal(canAccessOrganizationAdmin(authorizedUser()), true);
    assert.equal(canAccessOrganizationAdmin(authorizedUser({ capabilities: { organizationAdminEnabled: false } })), false);
    assert.equal(canAccessOrganizationAdmin(authorizedUser({ session: { mode: 'personal', purpose: 'application', activeOrganizationId: null } })), false);
    assert.equal(canAccessOrganizationAdmin(authorizedUser({ organization: { id: 'org-2', code: 'other', baseRole: 'admin' } })), false);
    assert.equal(canAccessOrganizationAdmin(authorizedUser({ organization: { id: 'org-1', code: 'symgov', baseRole: 'user' } })), false);
  });

  it('requires effective platform authority in the protected Symgov application context and enabled feature', () => {
    assert.equal(canAccessPlatformAdmin(authorizedUser()), true);
    assert.equal(canAccessPlatformAdmin(authorizedUser({ capabilities: { platformAdminEnabled: false } })), false);
    assert.equal(canAccessPlatformAdmin(authorizedUser({ isPlatformAdmin: false })), false);
    assert.equal(canAccessPlatformAdmin(authorizedUser({ session: { mode: 'personal', purpose: 'application', activeOrganizationId: null } })), false);
    assert.equal(canAccessPlatformAdmin(authorizedUser({ organization: { id: 'org-1', code: 'other', baseRole: 'admin' } })), false);
    assert.equal(canAccessPlatformAdmin(authorizedUser({ session: { mode: 'organization', purpose: 'credential_change', activeOrganizationId: 'org-1' } })), false);
  });

  it('does not retry when reauthentication is invalid, throttled, expired, or session-lost', async () => {
    for (const [status, message] of [
      [401, 'The PIN is invalid.'],
      [429, 'Too many attempts. Try again later.'],
      [403, 'Step-up reauthentication has expired.'],
      [401, 'Authentication required.'],
    ]) {
      let attempts = 0;
      let cleared = 0;
      const failure = new Error(message);
      failure.status = status;
      await assert.rejects(
        runWithStepUp({
          pin: '1234',
          operation: async () => { attempts += 1; throw stepUpRequired(); },
          reauthenticate: async () => { throw failure; },
          clearPin: () => { cleared += 1; },
        }),
        failure,
      );
      assert.equal(attempts, 1);
      assert.equal(cleared, 1);
    }
  });
});
