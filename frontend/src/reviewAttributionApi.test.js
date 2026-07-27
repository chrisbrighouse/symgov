import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';

const fetchCalls = [];

globalThis.document = {
  querySelector() {
    return null;
  }
};
globalThis.window = {
  location: {
    hostname: 'workspace.example.test',
    origin: 'https://workspace.example.test',
    protocol: 'https:'
  },
  SYMGOV_API_ROOT: 'https://api.example.test/api/v1',
  SYMGOV_CONFIG: {}
};
globalThis.fetch = async (url, options = {}) => {
  fetchCalls.push({ url, options });
  return {
    ok: true,
    status: 200,
    async text() {
      return '{}';
    }
  };
};

const {
  processWorkspaceSplitReviewDecisions,
  submitWorkspaceReviewDecision,
  submitWorkspaceRightsReviewDecision,
  updateWorkspaceReviewSymbolProperties
} = await import('./api.js');

test('review mutation request builders omit every client-controlled attribution field', async () => {
  fetchCalls.length = 0;
  const spoofedAttribution = {
    deciderName: 'Impersonated reviewer',
    deciderRole: 'admin',
    updatedBy: 'Impersonated reviewer',
    reviewerName: 'Impersonated reviewer',
    reviewerRole: 'admin',
    actorId: '11111111-1111-1111-1111-111111111111'
  };

  await submitWorkspaceReviewDecision('review-1', {
    decisionCode: 'approve',
    decisionNote: 'Approved.',
    childDecisions: [],
    caseComment: 'Ready.',
    ...spoofedAttribution
  });
  await submitWorkspaceRightsReviewDecision('review-2', {
    decisionCode: 'clear_rights',
    correctedRightsStatus: 'cleared',
    evidenceNote: 'Checked.',
    ...spoofedAttribution
  });
  await processWorkspaceSplitReviewDecisions('review-3', {
    childDecisions: [{ childId: 'child-1', action: 'approve' }],
    caseComment: 'Ready.',
    ...spoofedAttribution
  });
  await updateWorkspaceReviewSymbolProperties('review-4', {
    splitItemId: 'split-1',
    name: 'Pump',
    description: 'A pump.',
    category: 'Equipment',
    discipline: 'Mechanical',
    format: 'svg',
    ...spoofedAttribution
  });

  assert.equal(fetchCalls.length, 4);
  for (const { options } of fetchCalls) {
    const body = JSON.parse(options.body);
    for (const field of Object.keys(spoofedAttribution)) {
      assert.equal(Object.hasOwn(body, field), false, `${field} must not be transmitted`);
    }
  }
  assert.deepEqual(JSON.parse(fetchCalls[0].options.body), {
    decisionCode: 'approve',
    decisionNote: 'Approved.',
    childDecisions: [],
    caseComment: 'Ready.'
  });
  assert.deepEqual(JSON.parse(fetchCalls[3].options.body), {
    splitItemId: 'split-1',
    name: 'Pump',
    description: 'A pump.',
    category: 'Equipment',
    discipline: 'Mechanical',
    format: 'svg'
  });
});

test('review pages keep no client actor state and display the authenticated reviewer', async () => {
  const app = await readFile(new URL('./App.jsx', import.meta.url), 'utf8');

  assert.doesNotMatch(app, /deciderName|deciderRole|updatedBy/);
  assert.doesNotMatch(app, /Reviewer: Human/);
  assert.match(app, /Reviewer: \{auth\.user\?\.displayName \|\| auth\.user\?\.email\}/);
});
