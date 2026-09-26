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
      renderer.root.findByProps({ id: 'org-symbol-draft-discipline' }).props.onChange({ target: { value: 'Mechanical' } });
      renderer.root.findByProps({ id: 'org-symbol-draft-summary' }).props.onChange({ target: { value: 'A valve symbol.' } });
    });
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
    assert.deepEqual(api.calls[0], ['create', {
      name: 'Valve', category: 'process', discipline: 'Mechanical', summary: 'A valve symbol.',
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

// --- SM-P1-01 WP1.5, decision Q10 (2026-09-14) ---------------------------
//
// The governed-state panel on the organization symbol review page renders
// only for a session that satisfies the semantic review API's own boundary
// and its default-off flag, and is absent otherwise -- no failed request, no
// empty frame, no promise the API will not keep.
//
// The two gates are on different axes and that is the whole point. This page
// is gated on the *organization* capability `symbol_reviewer` (or
// organization `baseRole === 'admin'`); the router requires the *platform*
// role `admin` or `reviewer`. An organization reviewer holding only the
// former would meet a 403. Widening the router to accept the organization
// capability was considered and rejected: it would re-open decision Q2.

function pendingDraft(overrides = {}) {
  return baseDraft({
    canonicalName: 'Valve',
    currentRevision: {
      id: 'rev-1',
      lifecycleState: 'review',
      summary: 'A valve symbol.',
      description: null,
      assets: [],
      pendingSubmissionId: 'sub-1',
      pendingSubmissionRationale: 'Ready for review.',
      pendingSubmissionSubmittedAt: '2026-09-01T12:00:00Z',
    },
    ...overrides,
  });
}

function revisionState(overrides = {}) {
  return {
    symbolRevisionId: 'rev-1',
    revisionLabel: 'r2',
    lifecycleState: 'review',
    symbol: {
      governedSymbolId: '2f1d6a4e-0000-4000-8000-000000000002',
      catalogSymbolId: null,
      canonicalName: 'Valve',
      slug: 'org-draft-sym-1',
      visibility: 'organization',
    },
    semanticAssignments: [],
    classificationAssignments: [
      {
        assignmentId: 'ca-1',
        symbolRevisionId: 'rev-1',
        symbol: { canonicalName: 'Valve', catalogSymbolId: null, slug: 'org-draft-sym-1', visibility: 'organization' },
        classificationSchemeId: 'cs-1',
        classificationNodeId: 'n-piping',
        schemeCode: 'ENGINEERING-DISCIPLINE',
        nodeCode: 'PIPING',
        nodeLabel: 'Piping',
        assignmentRole: 'primary',
        status: 'proposed',
        method: 'source_mapping',
        confidence: null,
        evidence: {},
        proposedAt: '2026-09-11T14:32:05+00:00',
        capabilities: { canVerify: true, canReject: true, canRetire: true, mustRepropose: false, blockedReason: null },
      },
    ],
    rightsRecords: [],
    ...overrides,
  };
}

function reviewApi({ drafts = [pendingDraft()], revision = revisionState(), fail = null } = {}) {
  const calls = [];
  return {
    calls,
    listDrafts: async () => ({ items: drafts }),
    decide: async () => {},
    setOrganizationWide: async () => {},
    fetchSymbolRevision: async (revisionId) => {
      calls.push(revisionId);
      if (fail) throw fail;
      return revision;
    },
  };
}

function platformReviewer(overrides = {}) {
  return {
    user: {
      id: 'u-1',
      roles: ['reviewer'],
      session: { purpose: 'application' },
      capabilities: { semanticReviewEnabled: true },
      ...overrides,
    },
  };
}

describe('OrganizationSymbolReviewQueuePanel governed state (Q10)', () => {
  it('shows the governed semantic state for a session the API would admit', async () => {
    const api = reviewApi();
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { auth: platformReviewer(), api }));
    });

    assert.deepEqual(api.calls, ['rev-1']);
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Governed semantic state/);
    assert.match(text, /Piping/);
    await act(async () => renderer.unmount());
  });

  it('is absent, and asks the API nothing, for an organization reviewer without the platform role', async () => {
    // Exactly the session this page is built for: `symbol_reviewer` on the
    // organization, and no platform `admin`/`reviewer`. The router would
    // answer 403, so the panel is not rendered at all.
    const api = reviewApi();
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolReviewQueuePanel, {
        auth: { user: { id: 'u-2', roles: [], session: { purpose: 'application' }, capabilities: { semanticReviewEnabled: true } } },
        api,
      }));
    });

    const text = JSON.stringify(renderer.toJSON());
    assert.doesNotMatch(text, /Governed semantic state/);
    assert.deepEqual(api.calls, []);
    // The page itself is untouched.
    assert.match(text, /Valve/);
    await act(async () => renderer.unmount());
  });

  it('is absent while the feature flag is off', async () => {
    const api = reviewApi();
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolReviewQueuePanel, {
        auth: platformReviewer({ capabilities: { semanticReviewEnabled: false } }),
        api,
      }));
    });

    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /Governed semantic state/);
    assert.deepEqual(api.calls, []);
    await act(async () => renderer.unmount());
  });

  it('is absent when no auth is supplied at all', async () => {
    const api = reviewApi();
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { api }));
    });

    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /Governed semantic state/);
    assert.deepEqual(api.calls, []);
    await act(async () => renderer.unmount());
  });

  it('says plainly when a draft revision carries no governed assertions yet', async () => {
    // Which is every organization draft today: no path writes assignments to
    // an organization draft revision before promotion.
    const api = reviewApi({ revision: revisionState({ classificationAssignments: [] }) });
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { auth: platformReviewer(), api }));
    });

    assert.match(JSON.stringify(renderer.toJSON()), /No governed semantic assertions/);
    await act(async () => renderer.unmount());
  });

  it('reports a failed read without hiding the review controls', async () => {
    const api = reviewApi({ fail: new Error('Symbol revision semantic state load failed.') });
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { auth: platformReviewer(), api }));
    });

    assert.match(JSON.stringify(renderer.toJSON()), /Symbol revision semantic state load failed/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Approve Valve' }));
    await act(async () => renderer.unmount());
  });

  it('adds no decision control: the audit path stays on the semantic review surface', async () => {
    const api = reviewApi();
    let renderer;
    await act(async () => {
      renderer = create(createElement(OrganizationSymbolReviewQueuePanel, { auth: platformReviewer(), api }));
    });

    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Verify ca-1' }).length, 0);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Reject ca-1' }).length, 0);
    await act(async () => renderer.unmount());
  });
});
