import { createElement } from 'react';

export async function runWithStepUp({ pin, operation, reauthenticate, clearPin }) {
  try {
    return await operation();
  } catch (error) {
    const isStepUpFailure = error?.status === 401 || (
      error?.status === 403
      && /^Step-up reauthentication (?:is required|has expired)\.$/i.test(error?.message || '')
    );
    if (!isStepUpFailure) throw error;
    if (!pin) {
      error.requiresStepUp = true;
      throw error;
    }
    try {
      await reauthenticate(pin);
    } finally {
      clearPin();
    }
    return operation();
  }
}

function hasActiveOrganizationContext(user) {
  return Boolean(
    user
    && user.session?.mode === 'organization'
    && user.session?.purpose === 'application'
    && user.session?.activeOrganizationId
    && user.organization?.id === user.session.activeOrganizationId
  );
}

export function canAccessOrganizationAdmin(user) {
  return Boolean(
    hasActiveOrganizationContext(user)
    && user.organization?.baseRole === 'admin'
    && user.capabilities?.organizationAdminEnabled === true
  );
}

export function canAccessPlatformAdmin(user) {
  return Boolean(
    hasActiveOrganizationContext(user)
    && user.organization?.code === 'symgov'
    && user.isPlatformAdmin === true
    && user.capabilities?.platformAdminEnabled === true
  );
}

function denied(requiredRole) {
  return createElement('section', { className: 'workspace-empty-state' },
    createElement('p', { className: 'eyebrow' }, 'Access controlled'),
    createElement('h2', null, 'You do not have access to this area.'),
    createElement('p', null, `Required role: ${requiredRole}`));
}

export function OrganizationAdminAccess({ auth, children }) {
  return canAccessOrganizationAdmin(auth?.user) ? children : denied('organization admin');
}

export function PlatformAdminAccess({ auth, children }) {
  return canAccessPlatformAdmin(auth?.user) ? children : denied('platform admin');
}
