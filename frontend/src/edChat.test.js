import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';

import { askEd } from './api.js';
import {
  ED_PROMPT_LIMIT,
  canUseEd,
  describeEdError,
  formatEdTimestamp,
  normalizeEdResponse,
  shortVersion,
} from './edChat.js';

function user({ edEnabled = true, purpose = 'application' } = {}) {
  return { session: { purpose }, capabilities: { edEnabled } };
}

describe('Ed access', () => {
  it('follows the capability the API pilot gate reports', () => {
    assert.equal(canUseEd(user()), true);
    assert.equal(canUseEd(user({ edEnabled: false })), false);
    assert.equal(canUseEd(user({ purpose: 'credential_change' })), false);
    assert.equal(canUseEd({ session: { purpose: 'application' } }), false);
    assert.equal(canUseEd(null), false);
  });
});

describe('askEd', () => {
  let originalFetch;
  beforeEach(() => { originalFetch = globalThis.fetch; });
  afterEach(() => { globalThis.fetch = originalFetch; });

  it('posts the trimmed prompt to the v1 session endpoint, uncached', async () => {
    const calls = [];
    globalThis.fetch = async (url, options) => {
      calls.push({ url, options });
      return { ok: true, status: 200, text: async () => '{"answer":"Hi"}' };
    };

    const result = await askEd('  What is Symgov?  ');

    assert.equal(result.ok, true);
    assert.match(calls[0].url, /\/api\/v1\/ed\/chat$/);
    assert.equal(calls[0].options.method, 'POST');
    assert.equal(calls[0].options.credentials, 'include');
    assert.equal(calls[0].options.cache, 'no-store');
    assert.deepEqual(JSON.parse(calls[0].options.body), { prompt: 'What is Symgov?' });
    assert.equal(calls[0].options.headers.Authorization, undefined);
  });
});

describe('Ed response handling', () => {
  it('keeps only well-formed parts of a response', () => {
    const response = normalizeEdResponse({
      answer: 'Symgov governs symbols.',
      status: 'answered',
      mode: 'mixed',
      citations: [{ sourceType: 'approved_knowledge', title: 'What Symgov is for', reference: 'knowledge:abc:claim:x:v1', asOf: null }, null],
      warnings: ['A warning', 7],
      attributions: [{ source: 'iso_ics, edition 7', attribution: 'A', licenseCode: 'ODC-By 1.0', licenseUrl: 'javascript:alert(1)', clarification: 'C' }],
      knowledgeVersion: 'sha256:' + 'f'.repeat(64),
    });

    assert.equal(response.mode, 'mixed');
    assert.equal(response.citations.length, 1);
    assert.deepEqual(response.warnings, ['A warning']);
    assert.equal(response.attributions[0].licenseUrl, null, 'only https licence links are rendered');
    assert.equal(shortVersion(response.knowledgeVersion), 'ffffffffffff');
  });

  it('treats an unknown shape as no answer rather than trusting it', () => {
    const response = normalizeEdResponse({ status: 'deleted', mode: 'mutation' });

    assert.equal(response.status, 'unavailable');
    assert.equal(response.mode, 'cannot_answer');
    assert.equal(response.answer, 'Ed could not answer that.');
  });

  it('explains each failure without echoing server detail', () => {
    assert.match(describeEdError({ status: 404 }), /not available for your organization/);
    assert.match(describeEdError({ status: 429 }), /Wait a minute/);
    assert.match(describeEdError({ status: 401 }), /Sign in again/);
    assert.match(describeEdError({ status: 422 }), new RegExp(ED_PROMPT_LIMIT.toLocaleString('en-GB')));
    assert.match(describeEdError({ status: 0 }), /could not be reached/);
    assert.match(describeEdError({ status: 504 }), /took too long/);
    assert.match(describeEdError({ status: 500, message: 'Traceback at /srv/app.py' }), /^Ed is unavailable right now/);
  });

  it('shows timestamps in UTC to the minute', () => {
    assert.equal(formatEdTimestamp('2026-09-29T12:34:56+01:00'), '2026-09-29 11:34 UTC');
    assert.equal(formatEdTimestamp('not a date'), null);
  });
});
