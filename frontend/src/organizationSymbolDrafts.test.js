import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { OrganizationSymbolDraftsPanel } from './OrganizationSymbolDraftsPanel.js';
import { OrganizationSymbolReviewQueuePanel } from './OrganizationSymbolReviewQueuePanel.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function draftsApi(initialDrafts) {
  let drafts = initialDrafts;
  const calls = [];
  return {
    calls,
    listDrafts: async () => ({ items: drafts }),
    createDraft: async (payload) => {
      calls.push(['create', payload]);
      const created = {
        id: 'sym-2',
        slug: 'org-draft-sym-2',
        canonicalName: payload.name,
        category: payload.category,
        discipline: payload.discipline,
        currentRevisionId: 'rev-2',
        updatedAt: '2026-09-01T00:00:00Z',
        currentRevision: {
          lifecycleState: 'draft',
          assets: [],
          pendingSubmissionId: null,
        },
      };
      drafts = [...drafts, created];
      return created;
    },
    attachAsset: async (symbolId, revisionId, payload) => {
      calls.push(['attach', symbolId, revisionId, payload]);
      return { id: 'asset-1', ...payload };
    },
    submitForReview: async (symbolId, revisionId, payload) => {
      calls.push(['submit', symbolId, revisionId, payload]);
      drafts = drafts.map((draft) => (
        draft.id === symbolId
          ? { ...draft, currentRevision: { ...draft.currentRevision, lifecycleState: 'review', pendingSubmissionId: 'sub-1' } }
          : draft
      ));
      return { id: 'sub-1' };
    },
  };
}

function baseDraft(overrides = {}) {
  return {
    id: 'sym-1',
    slug: 'org-draft-sym-1',
    canonicalName: 'Fire hydrant',
    category: 'fire',
    discipline: 'civil',
    currentRevisionId: 'rev-1',
    updatedAt: '2026-09-01T00:00:00Z',
    currentRevision: {
      lifecycleState: 'draft',
      summary: 'A fire hydrant symbol.',
      description: null,
      assets: [],
      pendingSubmissionId: null,
      pendingSubmissionRationale: null,
      pendingSubmissionSubmittedAt: null,
    },
    ...overrides,
  };
}

describe('OrganizationSymbolDraftsPanel', () => {
  it('lists drafts and hides the create form from non-contributor users', async () => {
    const api = draftsApi([baseDraft()]);
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolDraftsPanel, { canCreate: false, api })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Fire hydrant/);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Create organization symbol draft' }).length, 0);
    await act(async () => renderer.unmount());
  });

  it('creates a draft and submits an existing draft for review', async () => {
    const api = draftsApi([baseDraft()]);
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolDraftsPanel, { canCreate: true, api })); });

    await act(async () => {
      renderer.root.findByProps({ id: 'org-symbol-draft-name' }).props.onChange({ target: { value: 'Valve' } });
      renderer.root.findByProps({ id: 'org-symbol-draft-category' }).props.onChange({ target: { value: 'process' } });
      renderer.root.findByProps({ id: 'org-symbol-draft-discipline' }).props.onChange({ target: { value: 'mechanical' } });
      renderer.root.findByProps({ id: 'org-symbol-draft-summary' }).props.onChange({ target: { value: 'A valve symbol.' } });
    });
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
    assert.deepEqual(api.calls[0], ['create', {
      name: 'Valve', category: 'process', discipline: 'mechanical', summary: 'A valve symbol.',
      description: undefined, aliases: [], keywords: [],
    }]);
    assert.match(JSON.stringify(renderer.toJSON()), /Draft created\./);

    await act(async () => renderer.root.findByProps({ 'aria-label': 'Submit Fire hydrant for organization review' }).props.onClick());
    assert.deepEqual(api.calls[1], ['submit', 'sym-1', 'rev-1', {}]);
    assert.match(JSON.stringify(renderer.toJSON()), /submitted for organization review/);

    await act(async () => renderer.unmount());
  });
});

describe('OrganizationSymbolReviewQueuePanel', () => {
  it('shows only submissions awaiting review and hides everything else', async () => {
    const api = {
      listDrafts: async () => ({
        items: [
          baseDraft(),
          baseDraft({
            id: 'sym-2',
            canonicalName: 'Valve',
            currentRevision: {
              lifecycleState: 'review',
              summary: 'A valve symbol.',
              description: null,
              assets: [],
              pendingSubmissionId: 'sub-1',
              pendingSubmissionRationale: 'Ready for review.',
              pendingSubmissionSubmittedAt: '2026-09-01T12:00:00Z',
            },
          }),
        ],
      }),
      decide: async () => {},
    };
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { api })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Valve/);
    assert.doesNotMatch(text, /Fire hydrant/);
    await act(async () => renderer.unmount());
  });

  it('decides a submission and refreshes the queue', async () => {
    const calls = [];
    let closed = false;
    const api = {
      listDrafts: async () => ({
        items: closed ? [] : [
          baseDraft({
            canonicalName: 'Valve',
            currentRevision: {
              lifecycleState: 'review',
              summary: 'A valve symbol.',
              description: null,
              assets: [],
              pendingSubmissionId: 'sub-1',
              pendingSubmissionRationale: null,
              pendingSubmissionSubmittedAt: '2026-09-01T12:00:00Z',
            },
          }),
        ],
      }),
      decide: async (symbolId, submissionId, payload) => {
        calls.push([symbolId, submissionId, payload]);
        closed = true;
      },
    };
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { api })); });
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Approve Valve' }).props.onClick());
    assert.deepEqual(calls[0], ['sym-1', 'sub-1', { decision: 'approved', rationale: undefined }]);
    assert.match(JSON.stringify(renderer.toJSON()), /No organization symbol submissions awaiting review\./);
    await act(async () => renderer.unmount());
  });

  it('lists approved organization symbols with an organization-wide toggle', async () => {
    const approvedDraft = baseDraft({
      canonicalName: 'Sprinkler head',
      organizationWide: false,
      currentRevision: {
        lifecycleState: 'approved',
        summary: 'A sprinkler head symbol.',
        description: null,
        assets: [],
        pendingSubmissionId: null,
        pendingSubmissionRationale: null,
        pendingSubmissionSubmittedAt: null,
      },
    });
    const api = {
      listDrafts: async () => ({ items: [approvedDraft] }),
      decide: async () => {},
      setOrganizationWide: async () => {},
    };
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { api })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Sprinkler head/);
    assert.match(text, /Set-only/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Enable organization-wide scope for Sprinkler head' }));
    await act(async () => renderer.unmount());
  });

  it('toggles organization-wide scope and refreshes', async () => {
    const calls = [];
    let organizationWide = false;
    const api = {
      listDrafts: async () => ({
        items: [
          baseDraft({
            canonicalName: 'Sprinkler head',
            organizationWide,
            currentRevision: {
              lifecycleState: 'approved',
              summary: 'A sprinkler head symbol.',
              description: null,
              assets: [],
              pendingSubmissionId: null,
              pendingSubmissionRationale: null,
              pendingSubmissionSubmittedAt: null,
            },
          }),
        ],
      }),
      decide: async () => {},
      setOrganizationWide: async (symbolId, enabled) => {
        calls.push([symbolId, enabled]);
        organizationWide = enabled;
      },
    };
    let renderer;
    await act(async () => { renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { api })); });
    await act(async () => renderer.root.findByProps({ 'aria-label': 'Enable organization-wide scope for Sprinkler head' }).props.onClick());
    assert.deepEqual(calls[0], ['sym-1', true]);
    assert.match(JSON.stringify(renderer.toJSON()), /Organization-wide/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Disable organization-wide scope for Sprinkler head' }));
    await act(async () => renderer.unmount());
  });
});
