import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import OrganizationSelectionPage, { OrganizationSelectionScreen } from './OrganizationSelectionPage.js';

const challenge = {
  token: 'test-token',
  expiresAt: '2026-08-15T13:00:00Z',
  choices: [
    { organizationId: 'org-1', code: 'ORG1', displayName: 'Organization One', logoUrl: null },
    { organizationId: 'org-2', code: 'ORG2', displayName: 'Organization Two', logoUrl: 'https://logo.test/2' }
  ],
  total: 2
};

test('OrganizationSelectionScreen: renders a list of organization choices', () => {
  const markup = renderToStaticMarkup(createElement(OrganizationSelectionScreen, {
    challenge,
    onSelect: () => {}
  }));

  assert.match(markup, /Organization One/);
  assert.match(markup, /Organization Two/);
  assert.match(markup, /ORG1/);
  assert.match(markup, /ORG2/);
});

test('OrganizationSelectionScreen: renders logo or deterministic fallback icon', () => {
  const markup = renderToStaticMarkup(createElement(OrganizationSelectionScreen, {
    challenge,
    onSelect: () => {}
  }));

  // Logo for org-2
  assert.match(markup, /src="https:\/\/logo\.test\/2"/);
  // Fallback for org-1 (deterministic: e.g. first letter of name or code)
  assert.match(markup, /org-selection-fallback/);
  assert.match(markup, />O<\/span>/);
});

test('OrganizationSelectionScreen: renders keyboard-operable buttons without default selection', () => {
  const markup = renderToStaticMarkup(createElement(OrganizationSelectionScreen, {
    challenge,
    onSelect: () => {}
  }));

  const buttonCount = (markup.match(/<button/g) || []).length;
  // Two org buttons + one cancel button
  assert.equal(buttonCount, 3);
  assert.match(markup, /type="button"/);
});

test('OrganizationSelectionScreen: renders loading and error states', () => {
  const loadingMarkup = renderToStaticMarkup(createElement(OrganizationSelectionScreen, {
    challenge,
    isSubmitting: true,
    onSelect: () => {}
  }));
  assert.match(loadingMarkup, /disabled=""/);

  const errorMarkup = renderToStaticMarkup(createElement(OrganizationSelectionScreen, {
    challenge,
    message: 'Selection expired',
    onSelect: () => {}
  }));
  assert.match(errorMarkup, /Selection expired/);
});

test('OrganizationSelectionScreen: handles > 5 choices and reports paging context', () => {
  const largeChallenge = {
    token: 'test-token',
    expiresAt: '2026-08-15T13:00:00Z',
    choices: Array.from({ length: 6 }, (_, i) => ({
      organizationId: `org-${i}`,
      code: `ORG${i}`,
      displayName: `Org ${i}`,
      logoUrl: null
    })),
    total: 12,
    page: 1,
    pageSize: 6,
    hasMore: true
  };

  const markup = renderToStaticMarkup(createElement(OrganizationSelectionScreen, {
    challenge: largeChallenge,
    onSelect: () => {}
  }));

  const buttonCount = (markup.match(/org-selection-button/g) || []).length;
  assert.equal(buttonCount, 6);
  assert.match(markup, /Showing 6 of 12 organizations/);
});

test('OrganizationSelectionPage: terminal challenge loss provides a deterministic return to sign-in', () => {
  const markup = renderToStaticMarkup(
    createElement(
      MemoryRouter,
      null,
      createElement(OrganizationSelectionPage, {
        auth: { challenge: null, message: 'Organization selection challenge is invalid or unavailable.' }
      })
    )
  );

  assert.match(markup, /Organization selection challenge is invalid or unavailable\./);
  assert.match(markup, /href="\/login"/);
  assert.match(markup, /Return to sign-in/);
});
