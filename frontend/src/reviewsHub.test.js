import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';

import { PrivateSupportView, ReviewsHub, reviewsAccess } from './reviewsHub.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function user({ roles = [], orgCode = 'acme', baseRole = 'user', capabilities = [], v2 = true, platformAdmin = false } = {}) {
  return {
    user: {
      id: 'user-1',
      roles,
      isPlatformAdmin: platformAdmin,
      session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
      organization: { id: 'org-1', code: orgCode, displayName: orgCode === 'symgov' ? 'Symgov' : 'Acme', baseRole, capabilities },
      capabilities: {
        organizationSymbolsEnabled: true,
        organizationSymbolWorkflowV2Enabled: v2,
        platformAdminEnabled: true,
      },
    },
  };
}

describe('reviewsAccess', () => {
  it('opens Private to an Org Reviewer and Public to a Public Reviewer, in the same organization session', () => {
    assert.deepEqual(reviewsAccess(user({ capabilities: ['symbol_reviewer'] })), { canPublic: false, canPrivate: true, canSupport: false, any: true });
    assert.deepEqual(reviewsAccess(user({ roles: ['reviewer'] })), { canPublic: true, canPrivate: false, canSupport: false, any: true });
    assert.equal(reviewsAccess(user({ roles: ['reviewer'], capabilities: ['symbol_reviewer'] })).canPrivate, true);
  });

  it('gives an Org Admin the private queue but no public review through the global admin role', () => {
    assert.deepEqual(reviewsAccess(user({ roles: ['admin'], baseRole: 'admin' })), { canPublic: false, canPrivate: true, canSupport: false, any: true });
  });

  it('gives a Platform Admin the read-only support view in the Symgov session', () => {
    const access = reviewsAccess(user({ roles: ['admin'], orgCode: 'symgov', platformAdmin: true }));
    assert.deepEqual(access, { canPublic: true, canPrivate: false, canSupport: true, any: true });
  });

  it('opens nothing private while the workflow flag is off', () => {
    const access = reviewsAccess(user({ capabilities: ['symbol_reviewer'], v2: false }));
    assert.equal(access.any, false);
  });
});

async function mountHub(auth, path = '/reviews') {
  let renderer;
  await act(async () => {
    renderer = create(createElement(MemoryRouter, { initialEntries: [path] },
      createElement(ReviewsHub, {
        auth,
        renderPublic: () => createElement('p', null, 'PUBLIC QUEUE'),
        renderDenied: () => createElement('p', null, 'DENIED'),
      })));
  });
  return renderer;
}

describe('ReviewsHub', () => {
  it('shows both tabs to a user holding both roles and defaults to Private', async () => {
    globalThis.fetch = async () => ({ ok: true, json: async () => ({ items: [], allowSelfApproval: true }) });
    const renderer = await mountHub(user({ roles: ['reviewer'], capabilities: ['symbol_reviewer'] }));
    const tabs = renderer.root.findAll((node) => node.props.role === 'tab' && node.type === 'button');
    assert.deepEqual(tabs.map((tab) => tab.props.children), ['Public', 'Private · Acme']);
    assert.match(JSON.stringify(renderer.toJSON()), /Organization Symbols/);
    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /PUBLIC QUEUE/);

    await act(async () => tabs[0].props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()), /PUBLIC QUEUE/);
    await act(async () => renderer.unmount());
  });

  it('honours ?scope=public and shows no tab bar when only one queue is open', async () => {
    const renderer = await mountHub(user({ roles: ['reviewer'] }), '/reviews?scope=private');
    assert.equal(renderer.root.findAll((node) => node.props.role === 'tab' && node.type === 'button').length, 0);
    assert.match(JSON.stringify(renderer.toJSON()), /PUBLIC QUEUE/);
    await act(async () => renderer.unmount());
  });

  it('denies a member with neither role', async () => {
    const renderer = await mountHub(user({ capabilities: ['contributor'] }));
    assert.match(JSON.stringify(renderer.toJSON()), /DENIED/);
    await act(async () => renderer.unmount());
  });
});

describe('PrivateSupportView', () => {
  it('lists one organization\'s private symbols read-only and loads their history on request', async () => {
    const calls = [];
    const api = {
      listOrganizations: async () => ({ items: [
        { id: 'org-sym', code: 'symgov', displayName: 'Symgov', isProtected: true },
        { id: 'org-1', code: 'ACME', displayName: 'Acme', isProtected: false },
      ] }),
      listSymbols: async (organizationId) => {
        calls.push(['list', organizationId]);
        return { items: [{
          id: 'sym-1', slug: 'org-draft-sym-1', canonicalName: 'Acme pump', organizationWide: true, updatedAt: '2026-10-24T10:00:00Z',
          currentRevisionId: 'rev-1', currentRevision: { id: 'rev-1', lifecycleState: 'approved' },
        }] };
      },
      history: async (organizationId, symbolId) => {
        calls.push(['history', organizationId, symbolId]);
        return { items: [{ submissionId: 'sub-1', outcome: 'approved', submittedByName: 'Ann', submittedAt: '2026-10-24T09:00:00Z', decidedByName: 'Rex', closedAt: '2026-10-24T09:30:00Z' }] };
      },
    };
    let renderer;
    await act(async () => { renderer = create(createElement(PrivateSupportView, { api })); });
    const select = renderer.root.findByProps({ id: 'private-support-organization' });
    const options = renderer.root.findAllByType('option').map((option) => option.props.value);
    assert.deepEqual(options, ['', 'org-1']);

    await act(async () => select.props.onChange({ target: { value: 'org-1' } }));
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Acme pump/);
    assert.match(text, /Published to the organization library/);
    assert.doesNotMatch(text, /Approve|Reject|Request changes/);

    await act(async () => renderer.root.findByProps({ 'aria-label': 'Show review history of Acme pump' }).props.onClick());
    assert.deepEqual(calls, [['list', 'org-1'], ['history', 'org-1', 'sym-1']]);
    assert.match(JSON.stringify(renderer.toJSON()), /decided by Rex/);
    await act(async () => renderer.unmount());
  });
});
