import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

// SM-P1-01 WP1.5, decision Q9 (2026-09-14).
//
// The panel embedded in the Reviews focus pane forecasts the approval; it
// does not report recorded state, because there is none to report before the
// decision. These tests hold the two things that makes load-bearing: that it
// is labelled as a forecast rather than as a record, and that it forecasts
// the child when a split child is the item under review.

import {
  ReviewClassificationForecast,
  formatForecastField,
  formatGapReason,
  formatMatchBasis,
} from './ReviewClassificationForecast.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const AUTH = {
  user: {
    id: 'u-1',
    roles: ['reviewer'],
    session: { purpose: 'application' },
    capabilities: { semanticReviewEnabled: true },
  },
};

function preview(overrides = {}) {
  return {
    reviewCaseId: 'rc-1',
    splitItemId: null,
    classificationRecordId: 'cr-1',
    disciplineUsed: 'Process',
    categoryUsed: 'Valves',
    method: 'source_mapping',
    mapperVersion: 'symgov-classification-mapping-v1',
    willAssert: [
      {
        field: 'engineeringDiscipline',
        schemeCode: 'ENGINEERING-DISCIPLINE',
        assignmentRole: 'primary',
        rawValue: 'Process',
        classificationNodeId: 'n-process',
        nodeCode: 'PROCESS',
        nodeLabel: 'Process',
        matchBasis: 'exact',
      },
      {
        field: 'category',
        schemeCode: 'SYMBOL-CATEGORY-FAMILY',
        assignmentRole: 'primary',
        rawValue: 'valve',
        classificationNodeId: 'n-valves',
        nodeCode: 'VALVES',
        nodeLabel: 'Valves',
        matchBasis: 'plural_variant',
      },
    ],
    willGap: [
      {
        field: 'industry',
        rawValue: 'process_engineering',
        reason: 'no_scheme',
        detail: 'Industry/Application scheme is not seeded; ICS is the intended source',
      },
      { field: 'aliases', rawValue: 'Gate valve', reason: 'carried_in_payload', detail: null },
    ],
    willLink: [
      {
        field: 'standardsSource',
        rawValue: 'ISA-5.1',
        target: 'symbol_standard_links',
        relationshipType: 'derived_from',
      },
    ],
    ...overrides,
  };
}

function stubApi(overrides = {}) {
  const calls = [];
  return {
    calls,
    fetchPreview: async (reviewCaseId, options = {}) => {
      calls.push([reviewCaseId, options]);
      return preview();
    },
    ...overrides,
  };
}

async function render(props) {
  let renderer;
  await act(async () => {
    renderer = create(createElement(ReviewClassificationForecast, { auth: AUTH, ...props }));
  });
  return renderer;
}

function markup(renderer) {
  return JSON.stringify(renderer.toJSON());
}

describe('forecast vocabularies', () => {
  it('renders every mapping gap reason as a sentence a reviewer can act on', () => {
    // The backend vocabulary, verbatim: `classification_mapping.MAPPING_GAP_REASONS`.
    const reasons = [
      'no_value', 'placeholder_value', 'no_scheme', 'no_node_match',
      'no_concept_target', 'no_relationship_table', 'no_standard_match',
      'ambiguous_standard_version', 'no_source_package', 'carried_in_payload',
      'mapping_failed', 'already_assigned',
    ];
    for (const reason of reasons) {
      const label = formatGapReason(reason);
      assert.ok(label && label !== reason, reason);
    }
  });

  it('never renders a machine value raw when it does not recognise one', () => {
    assert.equal(formatGapReason('some_future_reason'), 'Some future reason');
    assert.equal(formatGapReason(''), 'Not mapped');
  });

  it('says how a value was matched, so a legacy-taxonomy hit is visibly not exact', () => {
    assert.match(formatMatchBasis('exact'), /Exact/);
    assert.match(formatMatchBasis('plural_variant'), /plural/i);
    assert.match(formatMatchBasis('legacy_taxonomy'), /legacy/i);
  });

  it('labels the section 9.3 fields the way the review pane already labels them', () => {
    assert.equal(formatForecastField('engineeringDiscipline'), 'Discipline');
    assert.equal(formatForecastField('symbolFamily'), 'Symbol family');
    assert.equal(formatForecastField('libraryProvenanceClass'), 'Provenance class');
    assert.equal(formatForecastField('whateverComesNext'), 'Whatever comes next');
  });
});

describe('ReviewClassificationForecast', () => {
  it('is absent when the session does not satisfy the API boundary', async () => {
    const api = stubApi();
    let renderer;
    await act(async () => {
      renderer = create(createElement(ReviewClassificationForecast, {
        auth: { user: { roles: ['reviewer'], session: { purpose: 'application' }, capabilities: {} } },
        reviewCaseId: 'rc-1',
        api,
      }));
    });

    assert.equal(renderer.toJSON(), null);
    assert.deepEqual(api.calls, []);
    await act(async () => renderer.unmount());
  });

  it('is absent when no review item is in focus', async () => {
    const api = stubApi();
    const renderer = await render({ reviewCaseId: '', api });

    assert.equal(renderer.toJSON(), null);
    assert.deepEqual(api.calls, []);
    await act(async () => renderer.unmount());
  });

  it('says plainly that it is a forecast of the approval and not recorded state', async () => {
    const renderer = await render({ reviewCaseId: 'rc-1', api: stubApi() });

    const rendered = markup(renderer);
    assert.match(rendered, /will assert/i);
    assert.match(rendered, /not recorded/i);
    await act(async () => renderer.unmount());
  });

  it('names each classification the approval will propose, with its node label', async () => {
    const renderer = await render({ reviewCaseId: 'rc-1', api: stubApi() });

    const rendered = markup(renderer);
    assert.match(rendered, /Process/);
    assert.match(rendered, /Valves/);
    assert.match(rendered, /ENGINEERING-DISCIPLINE/);
    // The node UUID is a transport key, never the compact label.
    assert.doesNotMatch(rendered, /n-process/);
    await act(async () => renderer.unmount());
  });

  it('names each section 9.3 field that will fall into a gap, with its raw value', async () => {
    const renderer = await render({ reviewCaseId: 'rc-1', api: stubApi() });

    const rendered = markup(renderer);
    assert.match(rendered, /Industry/);
    assert.match(rendered, /process_engineering/);
    assert.match(rendered, /Industry\/Application scheme is not seeded/);
    await act(async () => renderer.unmount());
  });

  it('forecasts the split child through its own split item, never the sheet', async () => {
    const api = stubApi();
    const renderer = await render({ reviewCaseId: 'rc-1', splitItemId: 'si-9', api });

    assert.deepEqual(api.calls, [['rc-1', { splitItemId: 'si-9' }]]);
    await act(async () => renderer.unmount());
  });

  it('refetches when the reviewer moves to another review item', async () => {
    const api = stubApi();
    let renderer;
    await act(async () => {
      renderer = create(createElement(ReviewClassificationForecast, { auth: AUTH, reviewCaseId: 'rc-1', api }));
    });
    await act(async () => {
      renderer.update(createElement(ReviewClassificationForecast, { auth: AUTH, reviewCaseId: 'rc-2', api }));
    });

    assert.deepEqual(api.calls.map(([id]) => id), ['rc-1', 'rc-2']);
    await act(async () => renderer.unmount());
  });

  it('announces a failed forecast without claiming the approval will assert nothing', async () => {
    const api = stubApi({
      fetchPreview: async () => {
        throw new Error('Approval forecast load failed.');
      },
    });
    const renderer = await render({ reviewCaseId: 'rc-1', api });

    const alert = renderer.root.findAllByProps({ role: 'alert' });
    assert.ok(alert.length);
    assert.doesNotMatch(markup(renderer), /will assert nothing|no classification/i);
    await act(async () => renderer.unmount());
  });

  it('says so when the approval will assert nothing at all', async () => {
    const api = stubApi({
      fetchPreview: async () => preview({ willAssert: [], disciplineUsed: null, categoryUsed: null }),
    });
    const renderer = await render({ reviewCaseId: 'rc-1', api });

    assert.match(markup(renderer), /no structured classification/i);
    await act(async () => renderer.unmount());
  });
});

// --- Reachability, proved through the real application router -------------
//
// An export or source-shape test cannot establish that the panel is mounted
// in the Reviews focus pane for the roles that lane admits. This is the same
// harness `semanticReviewMountedJourney.test.js` uses.

async function mountReviews(currentUser, requests, { splitItem = false, workspaceFails = false } = {}) {
  const { createServer } = await import('vite');
  const TestRenderer = (await import('react-test-renderer')).default;
  const { MemoryRouter } = await import('react-router-dom');

  const reviewCase = splitItem
    ? {
      id: 'si-9',
      reviewItemType: 'split_item',
      parentReviewCaseId: 'rc-1',
      splitItemId: 'si-9',
      symbolId: 'SG-CHILD-1',
      title: 'Review split symbol SG-CHILD-1',
      status: 'Awaiting decision',
      currentStage: 'awaiting_decision',
      sourceFileName: 'sheet.png',
      childCount: 1,
      children: [],
      engineeringDiscipline: 'Process',
    }
    : {
      id: 'rc-1',
      symbolId: 'SG-0001',
      title: 'Review intake SG-0001',
      status: 'Classification review',
      currentStage: 'classification_review',
      sourceFileName: 'valve.svg',
      childCount: 0,
      children: [],
      engineeringDiscipline: 'Process',
    };

  const json = (status, payload) => ({
    ok: status >= 200 && status < 300,
    status,
    statusText: 'OK',
    text: async () => (payload == null ? '' : JSON.stringify(payload)),
    json: async () => payload,
  });

  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ url, method });
    if (url.includes('/auth/me')) return json(200, { user: currentUser });
    if (url.includes('/semantic-review/review-cases/')) return json(200, preview());
    if (url.includes('/workspace/review-cases')) {
      return workspaceFails ? json(500, { detail: 'Workspace unavailable.' }) : json(200, { items: [reviewCase] });
    }
    if (url.includes('/workspace/daisy')) return json(200, { items: [] });
    if (url.includes('/workspace/review-symbol-property-options')) return json(200, { items: [] });
    return json(200, { items: [] });
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
      renderer = TestRenderer.create(
        createElement(MemoryRouter, { initialEntries: ['/reviews'] }, createElement(App)),
      );
    });
  } finally {
    await vite.close();
  }
  return renderer;
}

function reviewsUser({ roles = ['reviewer'], semanticReviewEnabled = true } = {}) {
  return {
    id: 'u-1',
    email: 'reviewer@example.test',
    displayName: 'Rae Reviewer',
    roles,
    mustChangePin: false,
    subscription: { tier: 'free', status: 'active' },
    session: { mode: 'personal', purpose: 'application', activeOrganizationId: null },
    organization: null,
    isPlatformAdmin: false,
    capabilities: {
      organizationAdminEnabled: false,
      platformAdminEnabled: false,
      symbolSetsEnabled: false,
      semanticReviewEnabled,
    },
    recentStepUpAt: null,
  };
}

describe('mounted Reviews forecast', () => {
  let originalFetch;

  it('is reachable in the Reviews focus pane and reads the v1 forecast route', async () => {
    originalFetch = globalThis.fetch;
    const requests = [];
    const renderer = await mountReviews(reviewsUser(), requests);

    const forecast = requests.find(({ url }) => url.includes('/classification-preview'));
    assert.ok(forecast, `no forecast request in ${requests.map(({ url }) => url).join(', ')}`);
    assert.equal(forecast.method, 'GET');
    assert.match(forecast.url, /\/api\/v1\/semantic-review\/review-cases\/rc-1\/classification-preview$/);
    assert.match(JSON.stringify(renderer.toJSON()), /What approving this will assert/);

    await act(async () => renderer.unmount());
    globalThis.fetch = originalFetch;
  });

  it('forecasts a split child through its own split item', async () => {
    originalFetch = globalThis.fetch;
    const requests = [];
    const renderer = await mountReviews(reviewsUser(), requests, { splitItem: true });

    const forecast = requests.find(({ url }) => url.includes('/classification-preview'));
    assert.ok(forecast);
    assert.match(forecast.url, /review-cases\/rc-1\/classification-preview\?splitItemId=si-9$/);

    await act(async () => renderer.unmount());
    globalThis.fetch = originalFetch;
  });

  it('asks for no forecast over the seeded fallback queue', async () => {
    // With no live Reviews API the page falls back to a seeded queue whose
    // identifiers name no review case. Forecasting one would be a request
    // guaranteed to 404, and a panel of invented values if it did not.
    originalFetch = globalThis.fetch;
    const requests = [];
    const renderer = await mountReviews(reviewsUser(), requests, { workspaceFails: true });

    assert.equal(requests.some(({ url }) => url.includes('/classification-preview')), false);
    assert.doesNotMatch(JSON.stringify(renderer.toJSON()), /What approving this will assert/);

    await act(async () => renderer.unmount());
    globalThis.fetch = originalFetch;
  });

  it('is absent with the flag off, and asks the API nothing', async () => {
    originalFetch = globalThis.fetch;
    const requests = [];
    const renderer = await mountReviews(reviewsUser({ semanticReviewEnabled: false }), requests);

    const markup = JSON.stringify(renderer.toJSON());
    // The Reviews lane itself is unaffected; only the forecast is gone.
    assert.match(markup, /Review Item/);
    assert.doesNotMatch(markup, /What approving this will assert/);
    assert.equal(requests.some(({ url }) => url.includes('/classification-preview')), false);

    await act(async () => renderer.unmount());
    globalThis.fetch = originalFetch;
  });
});
