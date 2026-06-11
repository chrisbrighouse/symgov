import test from 'node:test';
import assert from 'node:assert/strict';

import {
  DEFAULT_AGENT_RUN_DURATION_SECONDS,
  durationSecondsToParts,
  formatCountdown,
  setDurationPart
} from './timerControls.js';

test('uses one minute as the default configurable run duration', () => {
  assert.equal(DEFAULT_AGENT_RUN_DURATION_SECONDS, 60);
});

test('formats countdown values as minutes and seconds', () => {
  assert.equal(formatCountdown(60), '1:00');
  assert.equal(formatCountdown(5), '0:05');
  assert.equal(formatCountdown(125), '2:05');
});

test('splits total seconds into minute and second parts', () => {
  assert.deepEqual(durationSecondsToParts(125), { minutes: 2, seconds: 5 });
  assert.deepEqual(durationSecondsToParts(0), { minutes: 0, seconds: 0 });
});

test('updates the minutes portion without changing seconds', () => {
  assert.equal(setDurationPart(75, 'minutes', '3'), 195);
});

test('updates the seconds portion and clamps it to a valid range', () => {
  assert.equal(setDurationPart(75, 'seconds', '9'), 69);
  assert.equal(setDurationPart(75, 'seconds', '99'), 119);
  assert.equal(setDurationPart(75, 'seconds', '-4'), 60);
  assert.equal(setDurationPart(75, 'seconds', ''), 60);
});
