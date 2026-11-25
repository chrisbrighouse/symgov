import test from 'node:test';
import assert from 'node:assert/strict';

import {
  BAND_COPY,
  VISUAL_MAX_BYTES,
  bandCopy,
  buildVisualMatchBody,
  bytesToBase64,
  firstImageFile,
  groupHeading,
  resultNotices,
  validateImageFile,
  visualMatchErrorMessage
} from './visualMatch.js';

test('only non-empty PNG and JPEG files up to 8 MB are accepted', () => {
  assert.equal(validateImageFile({ type: 'image/png', size: 10 }).ok, true);
  assert.equal(validateImageFile({ type: 'image/jpeg', size: VISUAL_MAX_BYTES }).ok, true);
  assert.match(validateImageFile({ type: 'image/gif', size: 10 }).message, /PNG and JPEG/);
  assert.match(validateImageFile({ type: 'image/png', size: VISUAL_MAX_BYTES + 1 }).message, /8 MB/);
  assert.equal(validateImageFile({ type: 'image/png', size: 0 }).ok, false);
  assert.equal(validateImageFile(null).ok, false);
});

test('the first pasted or dropped image is found, whichever way the browser exposes it', () => {
  const png = { type: 'image/png' };
  assert.equal(firstImageFile({ items: [{ kind: 'string', type: 'text/plain' }, { kind: 'file', type: 'image/png', getAsFile: () => png }] }), png);
  assert.equal(firstImageFile({ files: [{ type: 'text/plain' }, png] }), png);
  assert.equal(firstImageFile({ items: [], files: [] }), null);
  assert.equal(firstImageFile(null), null);
});

test('bytes become base64 in chunks without a stack overflow', () => {
  assert.equal(bytesToBase64(new Uint8Array([72, 105])), 'SGk=');
  const big = new Uint8Array(200_000).fill(65);
  assert.equal(Buffer.from(bytesToBase64(big), 'base64').length, 200_000);
});

test('the request carries only the filters the server knows, and only ticked ones', () => {
  const body = buildVisualMatchBody({
    imageBase64: 'AAAA',
    filters: { catalogDisciplines: ['Piping', ' '], pack: ['x'], useCases: [], availableFormats: ['SVG'] },
    includeWeak: 1
  });
  assert.deepEqual(body, { imageBase64: 'AAAA', filters: { catalogDisciplines: ['Piping'], availableFormats: ['SVG'] }, includeWeak: true, limit: 10 });
});

test('bands are described in plain language and never as a probability', () => {
  for (const band of ['likely', 'possible', 'weak']) {
    assert.ok(BAND_COPY[band].label && !/%|probab/i.test(BAND_COPY[band].text));
  }
  assert.equal(bandCopy('mystery').label, 'Match');
  assert.equal(groupHeading({ band: 'likely', symbols: [{}, {}] }, 0), '1. Likely match · 2 interchangeable symbols');
  assert.equal(groupHeading({ band: 'possible', symbols: [{}] }, 2), '3. Possible match');
});

test('errors say something specific and a missing result is never "does not exist"', () => {
  assert.match(visualMatchErrorMessage(413), /too large/);
  assert.match(visualMatchErrorMessage(415), /PNG and JPEG/);
  assert.match(visualMatchErrorMessage(503), /busy or not ready/);
  assert.match(visualMatchErrorMessage(0), /could not be reached/);
  assert.equal(visualMatchErrorMessage(500, 'Custom'), 'Custom');
});

test('the result notices cover degraded mode, unindexed symbols and a stronger match outside the filters', () => {
  assert.deepEqual(resultNotices(null), []);
  assert.equal(resultNotices({ degraded: true, warnings: [] }).length, 1);
  assert.match(resultNotices({ degraded: true })[0], /none is marked as a likely match/);
  assert.equal(resultNotices({ warnings: ['some_symbols_not_embedded'] }).length, 1);
  assert.match(resultNotices({ strongerMatchOutsideFilters: true })[0], /outside your filters/);
  assert.deepEqual(resultNotices({ degraded: false, warnings: [], strongerMatchOutsideFilters: false }), []);
  assert.match(resultNotices({ privateSymbolsPending: 1 })[0], /^1 of your organization's private symbols is still being indexed/);
  assert.match(resultNotices({ privateSymbolsPending: 3 })[0], /^3 of your organization's private symbols are still being indexed/);
  assert.deepEqual(resultNotices({ privateSymbolsPending: 0 }), []);
});
