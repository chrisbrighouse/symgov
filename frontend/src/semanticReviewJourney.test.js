import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';

import { canAccessSemanticReview, SemanticReviewAccess } from './semanticReviewJourney.js';

function user(overrides = {}) {
  return {
    id: 'u-1',
    roles: ['reviewer'],
    session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
    organization: { id: 'org-1', code: 'acme', baseRole: 'user' },
    capabilities: { semanticReviewEnabled: true },
    ...overrides,
  };
}

describe('semantic review capability gate', () => {
  it('admits a reviewer whose session carries the capability', () => {
    assert.equal(canAccessSemanticReview(user()), true);
  });

  it('admits an admin', () => {
    assert.equal(canAccessSemanticReview(user({ roles: ['admin'] })), true);
  });

  it('admits a personal-mode session, which sees public symbols only', () => {
    // Decision Q3/section 14.2: semantic review is platform governance, so
    // unlike `symbolSetsEnabled` it is not gated on an organization-bound
    // session. The router's own scope predicate is the authority on what a
    // personal-mode caller can see.
    assert.equal(
      canAccessSemanticReview(user({
        session: { mode: 'personal', purpose: 'application', activeOrganizationId: null },
        organization: null,
      })),
      true,
    );
  });

  it('refuses a session without the capability, which is the default-off flag', () => {
    assert.equal(canAccessSemanticReview(user({ capabilities: { semanticReviewEnabled: false } })), false);
    assert.equal(canAccessSemanticReview(user({ capabilities: {} })), false);
  });

  it('refuses a role outside the admin/reviewer boundary the API enforces', () => {
    assert.equal(canAccessSemanticReview(user({ roles: ['submitter'] })), false);
    assert.equal(canAccessSemanticReview(user({ roles: [] })), false);
  });

  it('refuses a credential-change session and an absent user', () => {
    assert.equal(
      canAccessSemanticReview(user({ session: { mode: 'personal', purpose: 'credential_change' } })),
      false,
    );
    assert.equal(canAccessSemanticReview(null), false);
    assert.equal(canAccessSemanticReview(undefined), false);
  });
});

describe('SemanticReviewAccess', () => {
  it('renders its children for an admitted session', () => {
    const markup = renderToStaticMarkup(createElement(
      SemanticReviewAccess,
      { auth: { user: user() } },
      createElement('p', null, 'queue'),
    ));
    assert.match(markup, /queue/);
  });

  it('renders the shared access-controlled empty state otherwise', () => {
    const markup = renderToStaticMarkup(createElement(
      SemanticReviewAccess,
      { auth: { user: user({ capabilities: {} }) } },
      createElement('p', null, 'queue'),
    ));
    assert.doesNotMatch(markup, /queue/);
    assert.match(markup, /workspace-empty-state/);
    assert.match(markup, /Access controlled/);
    assert.match(markup, /admin or reviewer/);
  });
});
