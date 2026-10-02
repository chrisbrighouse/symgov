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

const PLATFORM_ORGANIZATION_CODE = 'symgov';

// The global `admin` role is not scoped to an organization. In a session bound
// to a customer organization it does not unlock platform administration: the
// holder acts for that organization, and Organization Admin is the authority.
export function isCustomerOrganizationSession(user) {
  return Boolean(
    user
    && user.session?.mode === 'organization'
    && user.session?.activeOrganizationId
    && String(user.organization?.code || '').trim().toLowerCase() !== PLATFORM_ORGANIZATION_CODE
  );
}

export function canAccessPlatformWorkspace(user) {
  return Boolean(
    Array.isArray(user?.roles)
    && user.roles.includes('admin')
    && !isCustomerOrganizationSession(user)
  );
}

// Manage users narrowed to the active organization's members.
export function canManageOrganizationUsers(user) {
  return isCustomerOrganizationSession(user) && canAccessOrganizationAdmin(user);
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
