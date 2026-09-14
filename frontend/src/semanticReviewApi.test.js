import test from 'node:test';
import assert from 'node:assert/strict';

// SM-P1-01 WP1.4. The wire contract of the semantic review helpers, asserted
// against the real helpers rather than a mock: method, URL, query string and
// body. A mocked component call proves none of those.

const fetchCalls = [];
let nextResponse = { ok: true, status: 200, payload: {} };

globalThis.document = {
  querySelector() {
    return null;
  },
};
globalThis.window = {
  location: {
    hostname: 'catalog.example.test',
    origin: 'https://catalog.example.test',
    protocol: 'https:',
  },
  SYMGOV_API_ROOT: 'https://api.example.test/api/v1',
  SYMGOV_CONFIG: {},
};
globalThis.fetch = async (url, options = {}) => {
  fetchCalls.push({ url, options });
  const { ok, status, payload } = nextResponse;
  return {
    ok,
    status,
    async text() {
      return payload == null ? '' : JSON.stringify(payload);
    },
  };
};

const api = await import('./api.js');

function reset(payload = {}, { ok = true, status = 200 } = {}) {
  fetchCalls.length = 0;
  nextResponse = { ok, status, payload };
}

const ROOT = 'https://api.example.test/api/v1';

test('the symbol classification queue sends status, method, scheme and page bounds', async () => {
  reset({ items: [], limit: 25, offset: 50 });

  const page = await api.listSemanticReviewSymbolClassifications({
    status: 'proposed',
    method: 'legacy_backfill',
    schemeCode: 'ENGINEERING-DISCIPLINE',
    limit: 25,
    offset: 50,
  });

  assert.equal(
    fetchCalls[0].url,
    `${ROOT}/semantic-review/queues/symbol-classifications`
    + '?status=proposed&method=legacy_backfill&schemeCode=ENGINEERING-DISCIPLINE&limit=25&offset=50',
  );
  assert.equal(fetchCalls[0].options.method ?? 'GET', 'GET');
  assert.deepEqual(page, { items: [], limit: 25, offset: 50 });
});

test('an omitted filter is left off the query string rather than sent empty', async () => {
  reset({ items: [], limit: 50, offset: 0 });

  await api.listSemanticReviewSymbolClassifications();

  assert.equal(
    fetchCalls[0].url,
    `${ROOT}/semantic-review/queues/symbol-classifications?status=proposed&limit=50&offset=0`,
  );
});

test('the symbol semantic assignment queue carries no schemeCode parameter', async () => {
  reset({ items: [], limit: 50, offset: 0 });

  await api.listSemanticReviewSymbolSemanticAssignments({ method: 'ai_assisted', schemeCode: 'IGNORED' });

  assert.equal(
    fetchCalls[0].url,
    `${ROOT}/semantic-review/queues/symbol-semantic-assignments?status=proposed&method=ai_assisted&limit=50&offset=0`,
  );
});

test('the concept classification queue keeps its scheme filter', async () => {
  reset({ items: [], limit: 50, offset: 0 });

  await api.listSemanticReviewConceptClassifications({ schemeCode: 'USE-CASE' });

  assert.equal(
    fetchCalls[0].url,
    `${ROOT}/semantic-review/queues/concept-classifications?status=proposed&schemeCode=USE-CASE&limit=50&offset=0`,
  );
});

test('the external mapping queue keeps its scheme filter', async () => {
  reset({ items: [], limit: 50, offset: 0 });

  await api.listSemanticReviewConceptExternalMappings({ status: 'verified', schemeCode: 'CFIHOS' });

  assert.equal(
    fetchCalls[0].url,
    `${ROOT}/semantic-review/queues/concept-external-mappings?status=verified&schemeCode=CFIHOS&limit=50&offset=0`,
  );
});

test('the rights queue names its method filter determinationMethod', async () => {
  reset({ items: [], limit: 50, offset: 0 });

  await api.listSemanticReviewRightsRecords({ determinationMethod: 'ai_assisted' });

  assert.equal(
    fetchCalls[0].url,
    `${ROOT}/semantic-review/queues/rights-records?status=proposed&determinationMethod=ai_assisted&limit=50&offset=0`,
  );
});

test('one revision state is read by id, uncached, and returns its three row lists', async () => {
  reset({
    symbolRevisionId: 'rev-1',
    revisionLabel: 'r2',
    lifecycleState: 'published',
    symbol: { governedSymbolId: 'gs-1', catalogSymbolId: 'S-000001', canonicalName: 'Ball valve', slug: 'ball-valve', visibility: 'public' },
    semanticAssignments: [],
    classificationAssignments: [],
    rightsRecords: [],
  });

  const state = await api.fetchSemanticReviewSymbolRevision('rev/1');

  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/symbol-revisions/rev%2F1`);
  assert.equal(fetchCalls[0].options.cache, 'no-store');
  assert.equal(state.symbol.catalogSymbolId, 'S-000001');
  assert.deepEqual(state.classificationAssignments, []);
});

test('a semantic assignment decision posts only the target status', async () => {
  reset({ symbolRevisionId: 'rev-1', semanticAssignments: [], classificationAssignments: [], rightsRecords: [] });

  await api.decideSemanticReviewSemanticAssignment('a-1', { targetStatus: 'verified' });

  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/semantic-assignments/a-1/decision`);
  assert.equal(fetchCalls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), { targetStatus: 'verified' });
});

test('a symbol classification decision posts to its own path', async () => {
  reset({ symbolRevisionId: 'rev-1', semanticAssignments: [], classificationAssignments: [], rightsRecords: [] });

  await api.decideSemanticReviewSymbolClassification('c-1', { targetStatus: 'rejected' });

  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/symbol-classifications/c-1/decision`);
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), { targetStatus: 'rejected' });
});

test('an external mapping verification carries its basis and drops an empty one', async () => {
  reset({ items: [] });
  await api.decideSemanticReviewExternalMapping('m-1', { targetStatus: 'verified', verificationBasis: 'published crosswalk' });
  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/external-mappings/m-1/decision`);
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), { targetStatus: 'verified', verificationBasis: 'published crosswalk' });

  reset({ items: [] });
  await api.decideSemanticReviewExternalMapping('m-1', { targetStatus: 'rejected', verificationBasis: '' });
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), { targetStatus: 'rejected' });
});

test('a rights decision sends only the fields the reviewer supplied', async () => {
  reset({ recordId: 'r-1', capabilities: {} });

  await api.decideSemanticReviewRightsRecord('r-1', {
    targetStatus: 'approved',
    decisionReason: 'Licence confirmed.',
    rightsStatus: 'licensed',
    licenceReference: 'CC-BY-4.0',
  });

  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/rights-records/r-1/decision`);
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), {
    targetStatus: 'approved',
    decisionReason: 'Licence confirmed.',
    rightsStatus: 'licensed',
    licenceReference: 'CC-BY-4.0',
  });

  reset({ recordId: 'r-1', capabilities: {} });
  await api.decideSemanticReviewRightsRecord('r-1', { targetStatus: 'retired' });
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), { targetStatus: 'retired' });
});

test('a reviewer rights proposal names exactly one subject and omits blank optional fields', async () => {
  reset({ recordId: 'r-2', capabilities: {} });

  await api.proposeSemanticReviewRightsRecord({
    symbolRevisionId: 'rev-1',
    disposition: 'redraw_required',
    determinationMethod: 'manual',
    rightsStatus: 'unknown',
    licenceReference: '',
    decisionReason: '',
  });

  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/rights-records`);
  assert.equal(fetchCalls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), {
    symbolRevisionId: 'rev-1',
    disposition: 'redraw_required',
    determinationMethod: 'manual',
    rightsStatus: 'unknown',
  });
});

test('the assignable classification schemes are read uncached', async () => {
  reset({ items: [{ schemeId: 'cs-1', schemeCode: 'ENGINEERING-DISCIPLINE', name: 'Engineering Discipline', nodes: [] }] });

  const schemes = await api.listSemanticReviewClassificationSchemes();

  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/classification-schemes`);
  assert.equal(fetchCalls[0].options.cache, 'no-store');
  assert.equal(schemes.items[0].schemeCode, 'ENGINEERING-DISCIPLINE');
});

test('a reviewer classification proposal posts the node id and omits blank optionals', async () => {
  reset({ symbolRevisionId: 'rev-1', semanticAssignments: [], classificationAssignments: [], rightsRecords: [] });

  await api.proposeSemanticReviewClassification('rev-1', {
    classificationNodeId: 'node-9',
    assignmentRole: 'primary',
    method: 'manual',
    evidence: { reviewedBecause: 'backfill rejected' },
  });

  assert.equal(fetchCalls[0].url, `${ROOT}/semantic-review/symbol-revisions/rev-1/classifications`);
  assert.equal(fetchCalls[0].options.method, 'POST');
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), {
    classificationNodeId: 'node-9',
    assignmentRole: 'primary',
    method: 'manual',
    evidence: { reviewedBecause: 'backfill rejected' },
  });
});

test('a governance refusal surfaces the issue message, not the generic 422 detail', async () => {
  // The 422 envelope puts the human-readable refusal in `issues[].msg`;
  // `detail` is the handler's constant "Request validation failed."
  reset(
    {
      error: 'validation_error',
      detail: 'Request validation failed.',
      issues: [{ loc: ['body'], msg: 'a legacy_backfill classification cannot be verified (section 12.3)', type: 'value_error' }],
    },
    { ok: false, status: 422 },
  );

  await assert.rejects(
    () => api.decideSemanticReviewSymbolClassification('c-1', { targetStatus: 'verified' }),
    (error) => {
      assert.match(error.message, /legacy_backfill classification cannot be verified/);
      assert.equal(error.status, 422);
      return true;
    },
  );
});

test('a dormant feature reports its 404 detail rather than a validation message', async () => {
  reset({ error: 'not_found', detail: 'Not found.' }, { ok: false, status: 404 });

  await assert.rejects(
    () => api.listSemanticReviewRightsRecords(),
    (error) => {
      assert.equal(error.status, 404);
      assert.match(error.message, /Not found\./);
      return true;
    },
  );
});
