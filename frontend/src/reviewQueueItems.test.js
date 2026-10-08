import { test } from 'node:test';
import assert from 'node:assert/strict';
import { isDaisyReportOpenForReview, isTerminalReviewStage } from './reviewQueueItems.js';

test('an open case in a working stage is listed', () => {
  assert.equal(isDaisyReportOpenForReview({ currentStage: 'classification_review', closedAt: null }), true);
});

test('a closed case is not listed even when its stage is not on the terminal list', () => {
  assert.equal(
    isDaisyReportOpenForReview({ currentStage: 'retired_historical_cleanup', closedAt: '2026-10-08T09:30:00Z' }),
    false
  );
  assert.equal(isTerminalReviewStage('retired_historical_cleanup'), false);
});

test('a terminal stage is still not listed without closedAt', () => {
  assert.equal(isDaisyReportOpenForReview({ currentStage: 'Published' }), false);
});

test('a report with no case falls back to its coordination status', () => {
  assert.equal(isDaisyReportOpenForReview({ currentStage: null, coordinationStatus: 'closed' }), false);
  assert.equal(isDaisyReportOpenForReview({ currentStage: null, coordinationStatus: 'proposed' }), true);
});
