import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import * as organizationSession from './organizationSession.js';

const { normalizeSessionResponse, SESSION_TYPES } = organizationSession;

describe('organizationSession', () => {
  describe('normalizeSessionResponse', () => {
    it('returns null for empty response', () => {
      assert.equal(normalizeSessionResponse(null), null);
      assert.equal(normalizeSessionResponse(undefined), null);
    });

    it('identifies an organization selection challenge', () => {
      const response = {
        user: null,
        selectionChallenge: {
          token: 'test-token',
          expiresAt: '2026-08-15T12:00:00Z',
          choices: [
            { organizationId: 'org-1', code: 'ORG1', displayName: 'Org 1' }
          ],
          page: 1,
          pageSize: 5,
          total: 1,
          hasMore: false
        }
      };

      const normalized = normalizeSessionResponse(response);
      assert.equal(normalized.type, SESSION_TYPES.CHALLENGE);
      assert.equal(normalized.challenge.token, 'test-token');
      assert.equal(normalized.user, null);
    });

    it('identifies a limited session (mandatory PIN change)', () => {
      const response = {
        user: {
          id: 'user-1',
          session: { purpose: 'credential_change', mode: 'personal' }
        },
        selectionChallenge: null
      };

      const normalized = normalizeSessionResponse(response);
      assert.equal(normalized.type, SESSION_TYPES.LIMITED);
      assert.equal(normalized.user.id, 'user-1');
    });

    it('identifies an organization full session', () => {
      const response = {
        user: {
          id: 'user-1',
          session: { purpose: 'application', mode: 'organization', activeOrganizationId: 'org-1' }
        },
        selectionChallenge: null
      };

      const normalized = normalizeSessionResponse(response);
      assert.equal(normalized.type, SESSION_TYPES.ORGANIZATION);
      assert.equal(normalized.user.session.activeOrganizationId, 'org-1');
    });

    it('identifies a personal full session', () => {
      const response = {
        user: {
          id: 'user-1',
          session: { purpose: 'application', mode: 'personal' }
        },
        selectionChallenge: null
      };

      const normalized = normalizeSessionResponse(response);
      assert.equal(normalized.type, SESSION_TYPES.PERSONAL);
    });

    it('identifies an organization selection challenge with multiple pages', () => {
      const response = {
        user: null,
        selectionChallenge: {
          token: 'test-token',
          expiresAt: '2026-08-15T12:00:00Z',
          choices: [
            { organizationId: 'org-1', code: 'ORG1', displayName: 'Org 1' },
            { organizationId: 'org-2', code: 'ORG2', displayName: 'Org 2' },
            { organizationId: 'org-3', code: 'ORG3', displayName: 'Org 3' },
            { organizationId: 'org-4', code: 'ORG4', displayName: 'Org 4' },
            { organizationId: 'org-5', code: 'ORG5', displayName: 'Org 5' }
          ],
          page: 1,
          pageSize: 5,
          total: 12,
          hasMore: true
        }
      };

      const normalized = normalizeSessionResponse(response);
      assert.equal(normalized.type, SESSION_TYPES.CHALLENGE);
      assert.equal(normalized.challenge.hasMore, true);
      assert.equal(normalized.challenge.total, 12);
    });

    it('handles unexpected response shapes gracefully', () => {
       assert.equal(normalizeSessionResponse({}), null);
       assert.equal(normalizeSessionResponse({ user: {} }), null);
    });
  });

  describe('organization selection failure state', () => {
    const currentChallengeState = {
      loading: true,
      user: null,
      type: SESSION_TYPES.CHALLENGE,
      challenge: {
        token: 'retryable-token',
        choices: [{ organizationId: 'org-1', code: 'ORG1', displayName: 'Org 1' }]
      },
      message: ''
    };

    it('preserves the challenge and retryable error after a transient failure', () => {
      assert.equal(typeof organizationSession.authStateFromResponse, 'function');

      const nextState = organizationSession.authStateFromResponse(
        currentChallengeState,
        { ok: false, status: 503, message: 'Service temporarily unavailable.', payload: null },
        { preserveRetryableChallenge: true }
      );

      assert.equal(nextState.challenge, currentChallengeState.challenge);
      assert.equal(nextState.type, SESSION_TYPES.CHALLENGE);
      assert.equal(nextState.loading, false);
      assert.equal(nextState.message, 'Service temporarily unavailable.');
    });

    it('clears the challenge after the API terminal 401 challenge response', () => {
      assert.equal(typeof organizationSession.authStateFromResponse, 'function');

      const nextState = organizationSession.authStateFromResponse(
        currentChallengeState,
        {
          ok: false,
          status: 401,
          message: 'Organization selection challenge is invalid or unavailable.',
          payload: { detail: 'Organization selection challenge is invalid or unavailable.' }
        },
        { preserveRetryableChallenge: true }
      );

      assert.equal(nextState.challenge, null);
      assert.equal(nextState.user, null);
      assert.equal(nextState.type, null);
      assert.equal(nextState.loading, false);
    });
  });

  describe('logout state', () => {
    const currentSessionState = {
      loading: false,
      user: { id: 'user-1', organization: { id: 'org-1', displayName: 'Org 1' } },
      type: SESSION_TYPES.ORGANIZATION,
      challenge: null,
      message: ''
    };

    it('keeps the authenticated organization context when server revocation fails', () => {
      assert.equal(typeof organizationSession.authStateAfterLogout, 'function');

      const nextState = organizationSession.authStateAfterLogout(
        currentSessionState,
        { ok: false, message: 'Logout service unavailable.' }
      );

      assert.equal(nextState.user, currentSessionState.user);
      assert.equal(nextState.type, SESSION_TYPES.ORGANIZATION);
      assert.equal(nextState.message, 'Logout service unavailable.');
    });

    it('clears authenticated state after successful server revocation', () => {
      assert.equal(typeof organizationSession.authStateAfterLogout, 'function');

      const nextState = organizationSession.authStateAfterLogout(
        currentSessionState,
        { ok: true, payload: { ok: true, revoked: true } }
      );

      assert.deepEqual(nextState, {
        loading: false,
        user: null,
        challenge: null,
        type: null,
        message: ''
      });
    });
  });
});
