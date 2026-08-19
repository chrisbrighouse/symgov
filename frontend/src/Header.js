import { createElement, useState } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { OrganizationIcon } from './OrganizationSelectionPage.js';
import { appConfig } from './config.js';

export function EngineeringSymbolLogo() {
  return createElement(
    'svg',
    {
      viewBox: '0 0 24 24',
      'aria-hidden': 'true',
      fill: 'none',
      stroke: '#17685d',
      strokeLinecap: 'round',
      strokeLinejoin: 'round',
      strokeWidth: '3.5'
    },
    createElement('path', { d: 'M12 2L2 7l10 5 10-5-10-5z' }),
    createElement('path', { d: 'M2 17l10 5 10-5' }),
    createElement('path', { d: 'M2 12l10 5 10-5' })
  );
}

export async function logoutAndNavigate(auth, navigate) {
  const result = await auth.logout();
  if (result?.ok) {
    navigate('/login', { replace: true });
  }
  return result;
}

export function HeaderLogoutError({ message }) {
  if (!message) {
    return null;
  }

  return createElement(
    'p',
    { className: 'form-message error header-logout-error', role: 'alert' },
    message,
    ' Your current session is still active; please try again.'
  );
}

export function Header({ auth }) {
  const navigate = useNavigate();
  const [logoutError, setLogoutError] = useState('');
  const user = auth.user;
  const isOrgSession = auth.type === 'organization';
  const org = user?.organization;

  const handleLogout = async () => {
    setLogoutError('');
    const result = await logoutAndNavigate(auth, navigate);
    if (!result?.ok) {
      setLogoutError(result?.message || 'Sign out could not be completed.');
    }
  };

  return createElement(
    'header',
    { className: 'glass-header' },
    createElement(
      'div',
      { className: 'brand-block' },
      createElement(
        'div',
        { className: 'brand-mark', 'aria-hidden': 'true' },
        createElement(EngineeringSymbolLogo)
      ),
      createElement(
        'div',
        null,
        createElement('p', { className: 'eyebrow' }, 'Symbol governance system'),
        createElement('h1', null, 'symgov')
      )
    ),
    isOrgSession && org && createElement(
      'div',
      { className: 'header-org-context', 'data-testid': 'header-org-context' },
      createElement(OrganizationIcon, { organization: org }),
      createElement('span', { className: 'org-name' }, org.displayName)
    ),
    createElement(
      'div',
      { className: 'header-actions' },
      user && createElement(
        NavLink,
        {
          to: '/profile',
          className: 'build-chip user-identity-chip user-identity-link',
          'aria-label': 'Open your profile'
        },
        user.displayName || user.email,
        user.subscription?.isActive && createElement('span', { className: 'plus-subscription-badge' }, 'Plus')
      ),
      createElement('div', { className: 'build-chip' }, appConfig.build || 'local'),
      isOrgSession && createElement(
        'button',
        {
          type: 'button',
          className: 'ghost-button switch-org-button',
          onClick: handleLogout
        },
        'Switch organization'
      ),
      user
        ? createElement(
            'button',
            { type: 'button', className: 'ghost-button', onClick: handleLogout },
            'Sign out'
          )
        : createElement(
            'button',
            { type: 'button', className: 'ghost-button', onClick: () => navigate('/login') },
            'Sign in'
          ),
      createElement(HeaderLogoutError, { message: logoutError })
    )
  );
}
