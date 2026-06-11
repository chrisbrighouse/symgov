import test from 'node:test';
import assert from 'node:assert/strict';

import { formatSymgovBuildStamp, resolveSymgovBuildStamp } from '../vite.config.js';

test('formats frontend build stamp from the UTC build date', () => {
  const date = new Date(Date.UTC(2026, 5, 11, 23, 59, 0));

  assert.equal(formatSymgovBuildStamp(date), '2026-06-11.01');
});

test('allows explicit build stamp override for controlled deployments', () => {
  assert.equal(resolveSymgovBuildStamp({ SYMGOV_BUILD_STAMP: '2026-06-12.02' }), '2026-06-12.02');
});

test('ignores blank build stamp overrides', () => {
  assert.match(resolveSymgovBuildStamp({ SYMGOV_BUILD_STAMP: '   ' }), /^\d{4}-\d{2}-\d{2}\.01$/);
});
