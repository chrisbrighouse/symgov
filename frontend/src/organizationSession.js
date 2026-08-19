import { requestJson } from './api.js';

export const SESSION_TYPES = {
  PERSONAL: 'personal',
  ORGANIZATION: 'organization',
  LIMITED: 'limited',
  CHALLENGE: 'challenge',
};

/**
 * Normalizes an AuthLoginResponse or AuthChangePinResponse into a state object
 * that the frontend can use to decide which UI to show (login, pin change,
 * org selection, or the main application).
 */
export function normalizeSessionResponse(response) {
  if (!response) return null;

  const { user, selectionChallenge } = response;

  if (selectionChallenge && selectionChallenge.token) {
    return {
      type: SESSION_TYPES.CHALLENGE,
      user: null,
      challenge: selectionChallenge,
    };
  }

  if (user && user.session && user.session.purpose) {
    if (user.session.purpose === 'credential_change') {
      return {
        type: SESSION_TYPES.LIMITED,
        user,
        challenge: null,
      };
    }

    if (user.session.mode === 'organization') {
      return {
        type: SESSION_TYPES.ORGANIZATION,
        user,
        challenge: null,
      };
    }

    if (user.session.mode === 'personal') {
      return {
        type: SESSION_TYPES.PERSONAL,
        user,
        challenge: null,
      };
    }
  }

  return null;
}

export function authStateFromResponse(
  currentState,
  result,
  { preserveRetryableChallenge = false } = {}
) {
  const session = normalizeSessionResponse(result.payload);
  const isRetryableChallengeFailure = (
    preserveRetryableChallenge &&
    !result.ok &&
    result.status !== 401
  );

  if (isRetryableChallengeFailure) {
    return {
      ...currentState,
      loading: false,
      message: result.message
    };
  }

  return {
    loading: false,
    user: session?.user || null,
    challenge: session?.challenge || null,
    type: session?.type || null,
    message: result.ok ? '' : result.message
  };
}

export function authStateAfterLogout(currentState, result) {
  if (!result.ok) {
    return {
      ...currentState,
      loading: false,
      message: result.message || 'Sign out could not be completed.'
    };
  }

  return {
    loading: false,
    user: null,
    challenge: null,
    type: null,
    message: ''
  };
}

/**
 * Calls the /auth/select-organization endpoint.
 *
 * @param {Object} params
 * @param {string} params.token - The challenge token (in-memory only)
 * @param {string} [params.organizationId] - The ID of the organization to select
 * @param {number} [params.page] - Page number for paging choices
 * @param {number} [params.pageSize] - Page size for paging choices
 */
export async function selectOrganization({ token, organizationId, page, pageSize }) {
  const result = await requestJson('/auth/select-organization', {
    method: 'POST',
    body: JSON.stringify({ token, organizationId, page, pageSize })
  });

  return {
    ...result,
    session: result.ok ? normalizeSessionResponse(result.payload) : null
  };
}
