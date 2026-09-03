import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import * as headerModule from './Header.js';

const { Header } = headerModule;

const mockPersonalAuth = {
  type: 'personal',
  user: {
    id: 'user-1',
    email: 'chris@test.com',
    displayName: 'Chris Personal',
    session: { mode: 'personal' }
  },
  logout: async () => {}
};

const mockOrgAuth = {
  type: 'organization',
  user: {
    id: 'user-1',
    email: 'chris@test.com',
    displayName: 'Chris Org',
    session: { mode: 'organization', activeOrganizationId: 'org-1' },
    organization: {
      id: 'org-1',
      code: 'TESTORG',
      displayName: 'Test Organization',
      logoUrl: null
    }
  },
  logout: async () => {}
};

function renderHeader(auth) {
  return renderToStaticMarkup(
    createElement(MemoryRouter, null,
      createElement(Header, { auth: auth })
    )
  );
}

test('Header: shows organization context only for organization sessions', () => {
  const orgMarkup = renderHeader(mockOrgAuth);
  assert.match(orgMarkup, /header-org-context/);
  assert.match(orgMarkup, /Test Organization/);
  assert.match(orgMarkup, />T<\/span>/); // Fallback for 'Test Organization'
  assert.match(orgMarkup, /Switch organization/);

  const personalMarkup = renderHeader(mockPersonalAuth);
  assert.doesNotMatch(personalMarkup, /header-org-context/);
  assert.doesNotMatch(personalMarkup, /Test Organization/);
  assert.doesNotMatch(personalMarkup, /Switch organization/);
});

test('Header: personal mode remains unchanged', () => {
  const personalMarkup = renderHeader(mockPersonalAuth);
  assert.match(personalMarkup, /Chris Personal/);
  assert.match(personalMarkup, /Sign out/);
});

test('Header: organization mode shows both switch and sign out', () => {
  const orgMarkup = renderHeader(mockOrgAuth);
  assert.match(orgMarkup, /Switch organization/);
  assert.match(orgMarkup, /Sign out/);
});

test('Header: renders the organization logo image when logoUrl is set', () => {
  const authWithLogo = {
    ...mockOrgAuth,
    user: {
      ...mockOrgAuth.user,
      organization: { ...mockOrgAuth.user.organization, logoUrl: 'https://logo.test/acme' }
    }
  };
  const markup = renderHeader(authWithLogo);
  assert.match(markup, /src="https:\/\/logo\.test\/acme"/);
  assert.doesNotMatch(markup, /org-selection-fallback/);
});

test('Header: renders no organization context when signed out', () => {
  const markup = renderHeader({ type: 'personal', user: null, logout: async () => {} });
  assert.doesNotMatch(markup, /header-org-context/);
  assert.match(markup, /Sign in/);
});

test('Header switch handler keeps the current session and route when revocation fails', async () => {
  assert.equal(typeof headerModule.logoutAndNavigate, 'function');
  const navigations = [];
  const auth = {
    ...mockOrgAuth,
    logout: async () => ({ ok: false, message: 'Logout service unavailable.' })
  };

  const result = await headerModule.logoutAndNavigate(auth, (...args) => navigations.push(args));

  assert.equal(result.ok, false);
  assert.deepEqual(navigations, []);
});

test('Header switch handler advances to sign-in only after successful revocation', async () => {
  assert.equal(typeof headerModule.logoutAndNavigate, 'function');
  const navigations = [];
  const auth = {
    ...mockOrgAuth,
    logout: async () => ({ ok: true, payload: { ok: true, revoked: true } })
  };

  const result = await headerModule.logoutAndNavigate(auth, (...args) => navigations.push(args));

  assert.equal(result.ok, true);
  assert.deepEqual(navigations, [['/login', { replace: true }]]);
});

test('Header logout failure is exposed as an accessible retryable error', () => {
  assert.equal(typeof headerModule.HeaderLogoutError, 'function');
  const markup = renderToStaticMarkup(
    createElement(headerModule.HeaderLogoutError, { message: 'Logout service unavailable.' })
  );

  assert.match(markup, /role="alert"/);
  assert.match(markup, /Logout service unavailable\./);
  assert.match(markup, /try again/i);
});
