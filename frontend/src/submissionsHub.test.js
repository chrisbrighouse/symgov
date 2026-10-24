import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';

import { OrganizationSymbolDraftsPanel } from './OrganizationSymbolDraftsPanel.js';
import { SubmissionsHub, submissionsAccess } from './submissionsHub.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function user({ roles = [], baseRole = 'user', capabilities = [], v2 = true } = {}) {
  return {
    user: {
      id: 'user-1',
      roles,
      session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
      organization: { id: 'org-1', code: 'acme', displayName: 'Acme', baseRole, capabilities },
      capabilities: { organizationSymbolsEnabled: true, organizationSymbolWorkflowV2Enabled: v2 },
    },
  };
}

describe('submissionsAccess', () => {
  it('opens Private to an Org Submitter and Public to a Public Submitter, in the same organization session', () => {
    assert.deepEqual(submissionsAccess(user({ capabilities: ['contributor'] })), { canPublic: false, canPrivate: true, any: true });
    assert.deepEqual(submissionsAccess(user({ roles: ['submitter'] })), { canPublic: true, canPrivate: false, any: true });
  });

  it('gives an Org Admin the private library', () => {
    assert.equal(submissionsAccess(user({ baseRole: 'admin' })).canPrivate, true);
  });

  it('opens nothing private while the workflow flag is off, and nothing to an Org Reviewer alone', () => {
    assert.equal(submissionsAccess(user({ capabilities: ['contributor'], v2: false })).any, false);
    assert.equal(submissionsAccess(user({ capabilities: ['symbol_reviewer'] })).any, false);
  });
});

async function mountHub(auth, path = '/standards/submit') {
  globalThis.fetch = async () => ({ ok: true, status: 200, json: async () => ({ items: [], allowSelfApproval: true }) });
  let renderer;
  await act(async () => {
    renderer = create(createElement(MemoryRouter, { initialEntries: [path] },
      createElement(SubmissionsHub, {
        auth,
        renderPublic: () => createElement('p', null, 'PUBLIC INTAKE'),
        renderDenied: () => createElement('p', null, 'DENIED'),
      })));
  });
  return renderer;
}

function tabs(renderer) {
  return renderer.root.findAll((node) => node.props.role === 'tab' && node.type === 'button');
}

describe('SubmissionsHub', () => {
  it('shows both targets to a user holding both roles and defaults to Private', async () => {
    const renderer = await mountHub(user({ roles: ['submitter'], capabilities: ['contributor'] }));
    assert.deepEqual(tabs(renderer).map((tab) => tab.props.children), ['Private library · Acme', 'Public']);
    assert.match(JSON.stringify(renderer.toJSON()), /My submissions/);
    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /PUBLIC INTAKE/);

    await act(async () => tabs(renderer)[1].props.onClick());
    assert.match(JSON.stringify(renderer.toJSON()), /PUBLIC INTAKE/);
    await act(async () => renderer.unmount());
  });

  it('honours ?target=public, and shows no tab bar when only one target is open', async () => {
    const both = await mountHub(user({ roles: ['submitter'], capabilities: ['contributor'] }), '/standards/submit?target=public');
    assert.match(JSON.stringify(both.toJSON()), /PUBLIC INTAKE/);
    await act(async () => both.unmount());

    const publicOnly = await mountHub(user({ roles: ['submitter'] }), '/standards/submit?target=private');
    assert.equal(tabs(publicOnly).length, 0);
    assert.match(JSON.stringify(publicOnly.toJSON()), /PUBLIC INTAKE/);
    await act(async () => publicOnly.unmount());
  });

  it('denies a member with neither role', async () => {
    const renderer = await mountHub(user({ capabilities: ['symbol_reviewer'] }));
    assert.match(JSON.stringify(renderer.toJSON()), /DENIED/);
    await act(async () => renderer.unmount());
  });
});

describe('OrganizationSymbolDraftsPanel on Submissions', () => {
  const draft = (id, ownerId, name, ownerName) => ({
    id, ownerId, ownerName, canonicalName: name, category: 'pumps', discipline: 'Mechanical', updatedAt: '2026-10-24T10:00:00Z',
    visibility: 'organization_private', currentRevisionId: `${id}-r`, currentRevision: { id: `${id}-r`, lifecycleState: 'draft', assets: [] },
  });
  const api = {
    listDrafts: async () => ({ items: [draft('a', 'user-1', 'My pump', 'Me'), draft('b', 'user-2', 'Their valve', 'Rex Ward')] }),
    fetchReviewPolicy: async () => ({ allowSelfApproval: true }),
  };

  async function mountPanel(props) {
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolDraftsPanel, {
        canCreate: true, workflowV2: true, submissionsView: true, currentUserId: 'user-1', api, ...props,
      }));
    });
    return renderer;
  }

  it('puts the create form first and lists only the caller\'s own symbols', async () => {
    const renderer = await mountPanel({});
    const section = renderer.toJSON();
    assert.equal(section.children[0].type, 'form');
    const text = JSON.stringify(section);
    assert.match(text, /New private symbol/);
    assert.match(text, /My pump/);
    assert.doesNotMatch(text, /Their valve/);
    assert.equal(renderer.root.findAll((node) => node.props['aria-pressed'] !== undefined && node.type === 'button').length, 0);
    await act(async () => renderer.unmount());
  });

  it('lets an Org Admin or Org Reviewer switch to every member\'s symbols, with their owners named', async () => {
    const renderer = await mountPanel({ canViewAll: true });
    const allButton = renderer.root.find((node) => node.type === 'button' && node.props.children === 'All in organization');
    await act(async () => allButton.props.onClick());
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Their valve/);
    assert.match(text, /Owner: Rex Ward/);
    assert.match(text, /Owner: you/);
    assert.equal(allButton.props['aria-pressed'], true);
    await act(async () => renderer.unmount());
  });
});
