import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

// SM-P1-01 WP1.4. Reachability, proved through the real application router
// rather than by importing the component. An export or source-shape test
// cannot establish that a journey is mounted for a role.

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function response(status, payload) {
  const body = payload == null ? '' : JSON.stringify(payload);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 403 ? 'Forbidden' : 'OK',
    text: async () => body,
    json: async () => payload,
  };
}

function user({ roles = ['reviewer'], semanticReviewEnabled = true, mode = 'organization' } = {}) {
  return {
    id: 'u-1',
    email: 'reviewer@example.test',
    displayName: 'Rae Reviewer',
    roles,
    mustChangePin: false,
    subscription: { tier: 'free', status: 'active' },
    session: { mode, purpose: 'application', activeOrganizationId: mode === 'organization' ? 'org-1' : null },
    organization: mode === 'organization'
      ? { id: 'org-1', code: 'acme', displayName: 'Acme', baseRole: 'user', capabilities: [] }
      : null,
    isPlatformAdmin: false,
    capabilities: { organizationAdminEnabled: false, platformAdminEnabled: false, symbolSetsEnabled: false, semanticReviewEnabled },
    recentStepUpAt: null,
  };
}

function queuePage() {
  return {
    items: [
      {
        assignmentId: 'ca-1',
        symbolRevisionId: '7a1d6a4e-0000-4000-8000-0000000000aa',
        symbol: {
          governedSymbolId: '2f1d6a4e-0000-4000-8000-000000000001',
          catalogSymbolId: 'S-000042',
          canonicalName: 'Gate valve',
          slug: 'gate-valve',
          visibility: 'public',
        },
        classificationSchemeId: 'cs-1',
        schemeCode: 'ENGINEERING-DISCIPLINE',
        nodeCode: 'PIPING',
        nodeLabel: 'Piping',
        assignmentRole: 'primary',
        status: 'proposed',
        method: 'legacy_backfill',
        confidence: null,
        evidence: { source: 'SM-P0-10 backfill' },
        proposedAt: '2026-09-11T14:32:05+00:00',
        capabilities: {
          canVerify: false,
          canReject: true,
          canRetire: true,
          mustRepropose: true,
          blockedReason: 'a legacy_backfill classification cannot be verified (section 12.3); reject it and propose afresh with a real method',
        },
      },
    ],
    limit: 50,
    offset: 0,
  };
}

function revisionState() {
  return {
    symbolRevisionId: '7a1d6a4e-0000-4000-8000-0000000000aa',
    revisionLabel: 'r3',
    lifecycleState: 'published',
    symbol: queuePage().items[0].symbol,
    semanticAssignments: [],
    classificationAssignments: queuePage().items,
    rightsRecords: [],
  };
}

async function mount(path, currentUser, requests) {
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ url, method, body: options.body });
    if (url.includes('/auth/me')) return response(200, { user: currentUser });
    if (url.includes('/semantic-review/queues/symbol-classifications')) return response(200, queuePage());
    if (url.includes('/semantic-review/symbol-revisions/')) return response(200, revisionState());
    throw new Error(`Unexpected request: ${method} ${url}`);
  };

  const vite = await createServer({
    configFile: false,
    root: process.cwd(),
    server: { middlewareMode: true, hmr: false },
    appType: 'custom',
  });
  let renderer;
  try {
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    await act(async () => {
      renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)));
    });
  } finally {
    await vite.close();
  }
  return renderer;
}

describe('mounted semantic review journey', () => {
  let originalFetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('mounts the queue for a reviewer and reads the v1 semantic review API', async () => {
    const requests = [];
    const renderer = await mount('/semantic-review', user({ roles: ['reviewer'] }), requests);

    const markup = JSON.stringify(renderer.toJSON());
    assert.match(markup, /Semantic Review/);
    assert.match(markup, /S-000042/);
    assert.match(markup, /cannot be verified \(section 12\.3\)/);

    const queueRequest = requests.find(({ url }) => url.includes('/semantic-review/queues/symbol-classifications'));
    assert.ok(queueRequest, 'the mounted route reads the queue');
    assert.match(queueRequest.url, /\/api\/v1\/semantic-review\/queues\/symbol-classifications\?/);
    assert.match(queueRequest.url, /status=proposed/);
    assert.equal(queueRequest.method, 'GET');

    // Decision Q7: v1 only. There is no legacy `/api` twin to fall back to.
    assert.equal(requests.some(({ url }) => /\/api\/semantic-review/.test(url)), false);
    await act(async () => renderer.unmount());
  });

  it('mounts the same route for an admin and shows the rail entry', async () => {
    const requests = [];
    const renderer = await mount('/semantic-review', user({ roles: ['admin'] }), requests);

    assert.match(JSON.stringify(renderer.toJSON()), /Semantic Review/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Semantics' }));
    await act(async () => renderer.unmount());
  });

  it('mounts for a personal-mode reviewer, which the API scopes to public symbols', async () => {
    const requests = [];
    const renderer = await mount('/semantic-review', user({ mode: 'personal' }), requests);

    assert.match(JSON.stringify(renderer.toJSON()), /Semantic Review/);
    await act(async () => renderer.unmount());
  });

  it('shows the access-controlled state and no rail entry when the flag is off', async () => {
    const requests = [];
    const renderer = await mount('/semantic-review', user({ semanticReviewEnabled: false }), requests);

    const markup = JSON.stringify(renderer.toJSON());
    assert.doesNotMatch(markup, /Semantic Review<|Platform governance/);
    assert.match(markup, /Access controlled/);
    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Semantics' }).length, 0);
    assert.equal(requests.some(({ url }) => url.includes('/semantic-review/queues')), false);
    await act(async () => renderer.unmount());
  });

  it('denies a role outside the admin/reviewer boundary the API enforces', async () => {
    const requests = [];
    const renderer = await mount('/semantic-review', user({ roles: ['submitter'] }), requests);

    const markup = JSON.stringify(renderer.toJSON());
    assert.match(markup, /You do not have access to this area/);
    assert.equal(requests.some(({ url }) => url.includes('/semantic-review/queues')), false);
    await act(async () => renderer.unmount());
  });

  it('leaves the legacy intake Rights lane alone', async () => {
    const requests = [];
    const renderer = await mount('/semantic-review', user({ roles: ['reviewer'] }), requests);

    // `/rights` is `provenance_rights_review`, a different, intake-scoped
    // domain from `rights_records`. WP1.4 adds a route; it repoints nothing.
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Rights' }));
    assert.equal(requests.some(({ url }) => url.includes('/workspace/rights')), false);
    await act(async () => renderer.unmount());
  });
});
