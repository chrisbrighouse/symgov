import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { SemanticReviewPage, formatReviewTimestamp, symbolLabel } from './SemanticReviewPage.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const SYMBOL = {
  governedSymbolId: '2f1d6a4e-0000-4000-8000-000000000001',
  catalogSymbolId: 'S-000042',
  canonicalName: 'Gate valve',
  slug: 'gate-valve',
  visibility: 'public',
};

const PRIVATE_SYMBOL = {
  governedSymbolId: '2f1d6a4e-0000-4000-8000-000000000002',
  catalogSymbolId: null,
  canonicalName: 'Draft pump',
  slug: 'draft-pump',
  visibility: 'organization',
};

function capabilities(overrides = {}) {
  return { canVerify: true, canReject: true, canRetire: true, mustRepropose: false, blockedReason: null, ...overrides };
}

const BACKFILL_BLOCKED = capabilities({
  canVerify: false,
  mustRepropose: true,
  blockedReason: 'a legacy_backfill classification cannot be verified (section 12.3); reject it and propose afresh with a real method',
});

function classificationRow(overrides = {}) {
  return {
    assignmentId: 'ca-1',
    symbolRevisionId: 'rev-1',
    symbol: SYMBOL,
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
    capabilities: BACKFILL_BLOCKED,
    ...overrides,
  };
}

function semanticRow(overrides = {}) {
  return {
    assignmentId: 'sa-1',
    symbolRevisionId: 'rev-1',
    symbol: SYMBOL,
    concept: { semanticConceptId: 'sc-1', conceptCode: 'C-0001', preferredName: 'Gate valve', conceptKind: 'physical_equipment', status: 'published' },
    assignmentRole: 'primary',
    status: 'proposed',
    method: 'manual',
    confidence: 0.9,
    evidence: {},
    proposedAt: '2026-09-11T14:32:05+00:00',
    capabilities: capabilities(),
    ...overrides,
  };
}

function rightsRow(overrides = {}) {
  return {
    recordId: 'rr-1',
    subjectKind: 'symbol_revision',
    symbolRevisionId: 'rev-1',
    symbol: SYMBOL,
    sourcePackageId: null,
    standardVersionId: null,
    rightsStatus: 'unknown',
    disposition: 'metadata_only',
    determinationMethod: 'ai_assisted',
    licenceReference: null,
    status: 'proposed',
    evidence: { note: 'Tracy assessment' },
    proposedAt: '2026-09-11T14:32:05+00:00',
    capabilities: capabilities({
      canVerify: false,
      mustRepropose: true,
      blockedReason: 'a ai_assisted rights determination cannot be approved (section 8.4); propose your own record with a reviewed method',
    }),
    ...overrides,
  };
}

function mappingRow(overrides = {}) {
  return {
    referenceId: 'em-1',
    concept: { semanticConceptId: 'sc-1', conceptCode: 'C-0001', preferredName: 'Gate valve', conceptKind: 'physical_equipment', status: 'published' },
    schemeVersionId: 'sv-1',
    schemeCode: 'CFIHOS',
    schemeVersionLabel: '1.5',
    externalIdentifier: 'CFIHOS-100',
    externalLabel: 'Valve, gate',
    mappingType: 'exact',
    status: 'proposed',
    method: 'string_similarity',
    confidence: 0.8,
    evidence: {},
    proposedAt: '2026-09-11T14:32:05+00:00',
    capabilities: capabilities(),
    ...overrides,
  };
}

function conceptClassificationRow(overrides = {}) {
  return {
    assignmentId: 'cc-1',
    concept: { semanticConceptId: 'sc-1', conceptCode: 'C-0001', preferredName: 'Gate valve', conceptKind: 'physical_equipment', status: 'published' },
    classificationSchemeId: 'cs-2',
    schemeCode: 'USE-CASE',
    nodeCode: 'PID',
    nodeLabel: 'P&ID',
    assignmentRole: 'primary',
    status: 'proposed',
    method: 'manual',
    confidence: null,
    evidence: {},
    proposedAt: '2026-09-11T14:32:05+00:00',
    capabilities: capabilities(),
    ...overrides,
  };
}

function revisionState(overrides = {}) {
  return {
    symbolRevisionId: 'rev-1',
    revisionLabel: 'r3',
    lifecycleState: 'published',
    symbol: SYMBOL,
    semanticAssignments: [semanticRow()],
    classificationAssignments: [classificationRow()],
    rightsRecords: [rightsRow()],
    ...overrides,
  };
}

function emptyPage(limit = 50, offset = 0) {
  return { items: [], limit, offset };
}

function stubApi(overrides = {}) {
  const calls = [];
  const record = (name, fn) => async (...args) => {
    calls.push([name, ...args]);
    return fn(...args);
  };
  const api = {
    calls,
    symbolClassifications: record('symbolClassifications', async (params = {}) => ({ items: [classificationRow()], limit: params.limit ?? 50, offset: params.offset ?? 0 })),
    symbolSemanticAssignments: record('symbolSemanticAssignments', async (params = {}) => ({ items: [semanticRow()], limit: params.limit ?? 50, offset: params.offset ?? 0 })),
    conceptClassifications: record('conceptClassifications', async (params = {}) => ({ items: [conceptClassificationRow()], limit: params.limit ?? 50, offset: params.offset ?? 0 })),
    conceptExternalMappings: record('conceptExternalMappings', async (params = {}) => ({ items: [mappingRow()], limit: params.limit ?? 50, offset: params.offset ?? 0 })),
    rightsRecords: record('rightsRecords', async (params = {}) => ({ items: [rightsRow()], limit: params.limit ?? 50, offset: params.offset ?? 0 })),
    symbolRevision: record('symbolRevision', async () => revisionState()),
    decideSemanticAssignment: record('decideSemanticAssignment', async () => revisionState()),
    decideSymbolClassification: record('decideSymbolClassification', async () => revisionState()),
    decideExternalMapping: record('decideExternalMapping', async () => ({ items: [mappingRow({ status: 'verified', capabilities: capabilities({ canVerify: false, canReject: false }) })] })),
    decideRightsRecord: record('decideRightsRecord', async () => rightsRow({ status: 'rejected', capabilities: capabilities({ canVerify: false, canReject: false, canRetire: false, mustRepropose: false, blockedReason: null }) })),
    proposeRightsRecord: record('proposeRightsRecord', async () => rightsRow({ recordId: 'rr-2', determinationMethod: 'manual', status: 'proposed', capabilities: capabilities() })),
  };
  return Object.assign(api, overrides);
}

const AUTH = { user: { id: 'u-1', displayName: 'Rae Reviewer', roles: ['reviewer'], capabilities: { semanticReviewEnabled: true } } };

async function renderPage(api) {
  let renderer;
  await act(async () => {
    renderer = create(createElement(SemanticReviewPage, { auth: AUTH, api }));
  });
  return renderer;
}

function markup(renderer) {
  return JSON.stringify(renderer.toJSON());
}

function byLabel(renderer, label) {
  return renderer.root.findByProps({ 'aria-label': label });
}

function allByLabel(renderer, label) {
  return renderer.root.findAllByProps({ 'aria-label': label });
}

async function click(renderer, label) {
  await act(async () => {
    byLabel(renderer, label).props.onClick({ preventDefault() {} });
  });
}

describe('semantic review identity and timestamps', () => {
  it('labels a symbol by its catalogue ID and never by its UUID', () => {
    assert.equal(symbolLabel(SYMBOL), 'S-000042');
  });

  it('falls back to the canonical name for an unpublished symbol, not the UUID', () => {
    assert.equal(symbolLabel(PRIVATE_SYMBOL), 'Draft pump');
  });

  it('renders a timestamp in a stable operator-readable UTC form', () => {
    assert.equal(formatReviewTimestamp('2026-09-11T14:32:05+00:00'), '2026-09-11 14:32 UTC');
    assert.equal(formatReviewTimestamp(null), 'Unknown');
  });
});

describe('SemanticReviewPage queue', () => {
  it('loads the symbol classification queue on mount and shows the human-readable ID', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    assert.equal(api.calls[0][0], 'symbolClassifications');
    const rendered = markup(renderer);
    assert.match(rendered, /S-000042/);
    assert.doesNotMatch(rendered, /2f1d6a4e-0000-4000-8000-000000000001/);
    assert.match(rendered, /2026-09-11 14:32 UTC/);
    await act(async () => renderer.unmount());
  });

  it('reports how many rows are showing and never a total or page count', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    // The queue queries carry no COUNT, so a total would be invented.
    assert.match(markup(renderer), /Showing 1 row/);
    assert.doesNotMatch(markup(renderer), /of \d+|Page \d+ of/);
    await act(async () => renderer.unmount());
  });

  it('pages by offset, with Previous disabled on the first page', async () => {
    const api = stubApi({
      symbolClassifications: async (params = {}) => ({
        items: Array.from({ length: params.limit ?? 50 }, (_unused, index) => classificationRow({ assignmentId: `ca-${index}` })),
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      }),
    });
    const renderer = await renderPage(api);

    assert.equal(byLabel(renderer, 'Previous page').props.disabled, true);
    await click(renderer, 'Next page');
    const lastQueueCall = renderer.root.findByProps({ 'data-offset': 50 });
    assert.ok(lastQueueCall);
    await act(async () => renderer.unmount());
  });

  it('renders the empty state when a queue has no open rows', async () => {
    const api = stubApi({ symbolClassifications: async () => emptyPage() });
    const renderer = await renderPage(api);

    assert.match(markup(renderer), /No rows in this queue/);
    await act(async () => renderer.unmount());
  });

  it('surfaces a load failure, including the dormant-flag 404', async () => {
    const api = stubApi({
      symbolClassifications: async () => {
        throw new Error('Not found.');
      },
    });
    const renderer = await renderPage(api);

    const alert = renderer.root.findByProps({ role: 'alert' });
    assert.match(JSON.stringify(alert.props.children), /Not found\./);
    await act(async () => renderer.unmount());
  });

  it('sends the status, method and scheme filters the reviewer sets', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await act(async () => {
      byLabel(renderer, 'Status filter').props.onChange({ target: { value: 'verified' } });
    });
    await act(async () => {
      byLabel(renderer, 'Method filter').props.onChange({ target: { value: 'manual' } });
    });
    await act(async () => {
      byLabel(renderer, 'Scheme code filter').props.onChange({ target: { value: 'SYMBOL-CATEGORY-FAMILY' } });
    });

    const last = api.calls.filter(([name]) => name === 'symbolClassifications').at(-1);
    assert.deepEqual(last[1], { status: 'verified', method: 'manual', schemeCode: 'SYMBOL-CATEGORY-FAMILY', limit: 50, offset: 0 });
    await act(async () => renderer.unmount());
  });

  it('moves between queues with the arrow keys, as role=tablist promises', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    const tablist = renderer.root.findByProps({ 'aria-label': 'Semantic review queues' });
    assert.equal(byLabel(renderer, 'Symbol classifications queue').props.tabIndex, 0);
    assert.equal(byLabel(renderer, 'Symbol concepts queue').props.tabIndex, -1);

    await act(async () => {
      tablist.props.onKeyDown({ key: 'ArrowRight', preventDefault() {} });
    });
    assert.equal(byLabel(renderer, 'Symbol concepts queue').props['aria-selected'], true);
    assert.equal(api.calls.filter(([name]) => name === 'symbolSemanticAssignments').length, 1);

    await act(async () => {
      tablist.props.onKeyDown({ key: 'End', preventDefault() {} });
    });
    assert.equal(byLabel(renderer, 'Rights records queue').props['aria-selected'], true);

    await act(async () => {
      tablist.props.onKeyDown({ key: 'ArrowRight', preventDefault() {} });
    });
    assert.equal(byLabel(renderer, 'Symbol classifications queue').props['aria-selected'], true);
    await act(async () => renderer.unmount());
  });

  it('offers the rights queue its own determinationMethod filter and no scheme filter', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await click(renderer, 'Rights records queue');
    assert.equal(allByLabel(renderer, 'Scheme code filter').length, 0);
    await act(async () => {
      byLabel(renderer, 'Determination method filter').props.onChange({ target: { value: 'manual' } });
    });

    const last = api.calls.filter(([name]) => name === 'rightsRecords').at(-1);
    assert.deepEqual(last[1], { status: 'proposed', determinationMethod: 'manual', limit: 50, offset: 0 });
    await act(async () => renderer.unmount());
  });
});

describe('SemanticReviewPage decision controls', () => {
  it('renders controls from the row capabilities, not from its status', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    // The mounted row is a legacy_backfill classification: proposed, but
    // permanently unverifiable (section 12.3).
    assert.equal(allByLabel(renderer, 'Verify classification S-000042 ENGINEERING-DISCIPLINE PIPING').length, 0);
    assert.equal(allByLabel(renderer, 'Reject classification S-000042 ENGINEERING-DISCIPLINE PIPING').length, 1);
    assert.match(markup(renderer), /cannot be verified \(section 12\.3\)/);
    await act(async () => renderer.unmount());
  });

  it('offers verification where the capability allows it', async () => {
    const api = stubApi({
      symbolClassifications: async (params = {}) => ({
        items: [classificationRow({ method: 'manual', capabilities: capabilities() })],
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      }),
      symbolRevision: async () => revisionState({ classificationAssignments: [classificationRow({ method: 'manual', capabilities: capabilities() })] }),
    });
    const renderer = await renderPage(api);

    await click(renderer, 'Verify classification S-000042 ENGINEERING-DISCIPLINE PIPING');
    const decision = api.calls.find(([name]) => name === 'decideSymbolClassification');
    assert.deepEqual(decision.slice(1), ['ca-1', { targetStatus: 'verified' }]);
    await act(async () => renderer.unmount());
  });

  it('re-renders the revision panel from the write response without a second read', async () => {
    const api = stubApi({
      symbolClassifications: async (params = {}) => ({
        items: [classificationRow({ method: 'manual', capabilities: capabilities() })],
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      }),
      symbolRevision: async () => revisionState({ classificationAssignments: [classificationRow({ method: 'manual', capabilities: capabilities() })] }),
      decideSymbolClassification: async () => revisionState({
        classificationAssignments: [classificationRow({ method: 'manual', status: 'verified', capabilities: capabilities({ canVerify: false, canReject: false }) })],
      }),
    });
    const renderer = await renderPage(api);

    const readsBefore = api.calls.filter(([name]) => name === 'symbolRevision').length;
    await click(renderer, 'Verify classification S-000042 ENGINEERING-DISCIPLINE PIPING');
    assert.equal(api.calls.filter(([name]) => name === 'symbolRevision').length, readsBefore);
    assert.equal(allByLabel(renderer, 'Verify classification S-000042 ENGINEERING-DISCIPLINE PIPING').length, 0);
    await act(async () => renderer.unmount());
  });

  it('surfaces a governance refusal against the row the reviewer acted on', async () => {
    const api = stubApi({
      symbolClassifications: async (params = {}) => ({
        items: [classificationRow({ method: 'manual', capabilities: capabilities() })],
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      }),
      symbolRevision: async () => revisionState({ classificationAssignments: [classificationRow({ method: 'manual', capabilities: capabilities() })] }),
      decideSymbolClassification: async () => {
        throw new Error('body: a legacy_backfill classification cannot be verified (section 12.3)');
      },
    });
    const renderer = await renderPage(api);

    await click(renderer, 'Verify classification S-000042 ENGINEERING-DISCIPLINE PIPING');
    assert.match(markup(renderer), /cannot be verified \(section 12\.3\)/);
    await act(async () => renderer.unmount());
  });

  it('decides a semantic assignment against its own endpoint', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await click(renderer, 'Symbol concepts queue');
    await click(renderer, 'Verify concept assignment S-000042 C-0001');
    const decision = api.calls.find(([name]) => name === 'decideSemanticAssignment');
    assert.deepEqual(decision.slice(1), ['sa-1', { targetStatus: 'verified' }]);
    await act(async () => renderer.unmount());
  });

  it('sends the verification basis with an external mapping verification', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await click(renderer, 'External mappings queue');
    await act(async () => {
      byLabel(renderer, 'Verification basis').props.onChange({ target: { value: 'published crosswalk' } });
    });
    await click(renderer, 'Verify mapping C-0001 CFIHOS-100');
    const decision = api.calls.find(([name]) => name === 'decideExternalMapping');
    assert.deepEqual(decision.slice(1), ['em-1', { targetStatus: 'verified', verificationBasis: 'published crosswalk' }]);
    await act(async () => renderer.unmount());
  });

  it('renders concept classifications read-only, because the API exposes no decision for them', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await click(renderer, 'Concept classifications queue');
    assert.equal(allByLabel(renderer, 'Verify concept classification C-0001 USE-CASE PID').length, 0);
    assert.equal(allByLabel(renderer, 'Reject concept classification C-0001 USE-CASE PID').length, 0);
    assert.match(markup(renderer), /No decision control is available/);
    await act(async () => renderer.unmount());
  });

  it('marks a decision-Q6 read-only scheme and creates nothing in it', async () => {
    const api = stubApi({
      symbolClassifications: async (params = {}) => ({
        items: [classificationRow({ schemeCode: 'USE-CASE', nodeCode: 'PID', nodeLabel: 'P&ID', method: 'manual', capabilities: capabilities() })],
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      }),
      symbolRevision: async () => revisionState({
        classificationAssignments: [classificationRow({ schemeCode: 'USE-CASE', nodeCode: 'PID', nodeLabel: 'P&ID', method: 'manual', capabilities: capabilities() })],
      }),
    });
    const renderer = await renderPage(api);

    assert.match(markup(renderer), /Read-only scheme/);
    assert.equal(allByLabel(renderer, 'Propose classification').length, 0);
    await act(async () => renderer.unmount());
  });
});

describe('SemanticReviewPage rights review', () => {
  it('names the rights decision approval, not verification', async () => {
    const api = stubApi({
      rightsRecords: async (params = {}) => ({
        items: [rightsRow({ determinationMethod: 'manual', capabilities: capabilities() })],
        limit: params.limit ?? 50,
        offset: params.offset ?? 0,
      }),
    });
    const renderer = await renderPage(api);

    await click(renderer, 'Rights records queue');
    assert.equal(allByLabel(renderer, 'Approve rights record S-000042').length, 1);
    assert.equal(allByLabel(renderer, 'Verify rights record S-000042').length, 0);
    await click(renderer, 'Approve rights record S-000042');
    const decision = api.calls.find(([name]) => name === 'decideRightsRecord');
    assert.equal(decision[1], 'rr-1');
    assert.equal(decision[2].targetStatus, 'approved');
    await act(async () => renderer.unmount());
  });

  it('offers the reviewer their own record where an ai_assisted determination bars approval', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await click(renderer, 'Rights records queue');
    assert.equal(allByLabel(renderer, 'Approve rights record S-000042').length, 0);
    assert.match(markup(renderer), /cannot be approved \(section 8\.4\)/);

    await act(async () => {
      byLabel(renderer, 'Proposed disposition').props.onChange({ target: { value: 'display' } });
    });
    await act(async () => {
      byLabel(renderer, 'Proposed determination method').props.onChange({ target: { value: 'licence_document' } });
    });
    await act(async () => {
      byLabel(renderer, 'Proposed rights status').props.onChange({ target: { value: 'licensed' } });
    });
    await act(async () => {
      byLabel(renderer, 'Proposed licence reference').props.onChange({ target: { value: 'CC-BY-4.0' } });
    });
    await act(async () => {
      await byLabel(renderer, 'Propose a rights record').props.onSubmit({ preventDefault() {} });
    });

    const proposal = api.calls.find(([name]) => name === 'proposeRightsRecord');
    assert.deepEqual(proposal[1], {
      symbolRevisionId: 'rev-1',
      disposition: 'display',
      determinationMethod: 'licence_document',
      rightsStatus: 'licensed',
      licenceReference: 'CC-BY-4.0',
      decisionReason: '',
    });
    await act(async () => renderer.unmount());
  });

  it('refreshes the queue after a proposal, so the new record appears in it', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await click(renderer, 'Rights records queue');
    const readsBefore = api.calls.filter(([name]) => name === 'rightsRecords').length;
    await act(async () => {
      await byLabel(renderer, 'Propose a rights record').props.onSubmit({ preventDefault() {} });
    });

    assert.equal(api.calls.filter(([name]) => name === 'proposeRightsRecord').length, 1);
    assert.equal(api.calls.filter(([name]) => name === 'rightsRecords').length, readsBefore + 1);
    await act(async () => renderer.unmount());
  });

  it('never offers ai_assisted as a determination method a reviewer can propose', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    await click(renderer, 'Rights records queue');
    const options = byLabel(renderer, 'Proposed determination method').findAllByType('option');
    const values = options.map((option) => option.props.value);
    assert.deepEqual(values, ['manual', 'licence_document']);
    await act(async () => renderer.unmount());
  });
});

describe('SemanticReviewPage revision detail', () => {
  it('opens one revision and shows its assignments, classifications, rights and evidence', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    const read = api.calls.find(([name]) => name === 'symbolRevision');
    assert.equal(read[1], 'rev-1');
    const rendered = markup(renderer);
    assert.match(rendered, /Concept assignments/);
    assert.match(rendered, /Classification assignments/);
    assert.match(rendered, /Rights records/);
    assert.match(rendered, /SM-P0-10 backfill/);
    assert.match(rendered, /r3/);
    await act(async () => renderer.unmount());
  });

  it('does not read a revision for a concept-targeted queue', async () => {
    const api = stubApi();
    const renderer = await renderPage(api);

    const before = api.calls.filter(([name]) => name === 'symbolRevision').length;
    await click(renderer, 'External mappings queue');
    assert.equal(api.calls.filter(([name]) => name === 'symbolRevision').length, before);
    await act(async () => renderer.unmount());
  });
});
