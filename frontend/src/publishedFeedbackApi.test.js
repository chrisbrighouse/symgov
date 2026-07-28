import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
  buildPublishedFeedbackRequest,
  createPublishedFeedbackAttempt,
  publishedFeedbackLifecycleNotice
} from './publishedFeedbackLifecycle.js';

test('send for review modal gives the required pre-submit publication assurance', () => {
  const appSource = readFileSync(new URL('./App.jsx', import.meta.url), 'utf8');

  assert.match(
    appSource,
    /\{commandDialog === 'send_for_review' \? \([\s\S]*?Requesting review opens review work\. The current published revision remains available unless an authorized human later withdraws it\.[\s\S]*?<textarea[\s\S]*?onClick=\{handleSubmitPublishedCommand\}/
  );
});

test('published feedback request wraps only command, UUID targets, comment and request ID', () => {
  const request = buildPublishedFeedbackRequest({
    command: 'comment',
    symbolIds: ['11111111-1111-4111-8111-111111111111'],
    comment: 'Check this.',
    requestId: '22222222-2222-4222-8222-222222222222',
    actorId: 'spoof',
    requester: { id: 'spoof' }
  });

  assert.deepEqual(request, {
    payload: {
      command: 'comment',
      symbolIds: ['11111111-1111-4111-8111-111111111111'],
      comment: 'Check this.',
      requestId: '22222222-2222-4222-8222-222222222222'
    }
  });
});

test('same attempt retains its request ID and deliberate new submission regenerates it', () => {
  const generated = ['11111111-1111-4111-8111-111111111111', '22222222-2222-4222-8222-222222222222'];
  const randomUUID = () => generated.shift();
  const input = { command: 'send_for_review', symbolIds: ['b', 'a'], comment: ' Check it. ' };

  const first = createPublishedFeedbackAttempt(null, input, randomUUID);
  const retry = createPublishedFeedbackAttempt(first, input, randomUUID);
  const deliberateNew = createPublishedFeedbackAttempt(null, input, randomUUID);

  assert.equal(retry.requestId, first.requestId);
  assert.notEqual(deliberateNew.requestId, first.requestId);
});

test('structured lifecycle response produces bounded success and pending notices', () => {
  const item = {
    symbolId: '11111111-1111-4111-8111-111111111111',
    commentId: '22222222-2222-4222-8222-222222222222',
    reviewCaseId: '33333333-3333-4333-8333-333333333333',
    edQueueItemId: '44444444-4444-4444-8444-444444444444',
    remainsPublished: true,
    requestReplayed: false,
    workflowDeliveryState: 'materialized'
  };
  const completed = publishedFeedbackLifecycleNotice('send_for_review', {
    status: 'completed', command: 'send_for_review', publishedAvailabilityChanged: false, items: [item]
  });
  const pending = publishedFeedbackLifecycleNotice('send_for_review', {
    status: 'accepted_pending_delivery', command: 'send_for_review', publishedAvailabilityChanged: false,
    items: [{ ...item, workflowDeliveryState: 'pending' }]
  });

  assert.deepEqual(completed, { mode: 'success', message: 'Review requested; the published symbol remains available.' });
  assert.deepEqual(pending, { mode: 'info', message: 'Review recorded; Ed delivery is pending. The published symbol remains available.' });
});

test('terminal replay is accepted as historical completed work', () => {
  const item = {
    symbolId: '11111111-1111-4111-8111-111111111111',
    commentId: '22222222-2222-4222-8222-222222222222',
    reviewCaseId: '33333333-3333-4333-8333-333333333333',
    edQueueItemId: '44444444-4444-4444-8444-444444444444',
    remainsPublished: true,
    requestReplayed: true,
    workflowDeliveryState: 'historical'
  };

  assert.deepEqual(
    publishedFeedbackLifecycleNotice('send_for_review', {
      status: 'completed', command: 'send_for_review', publishedAvailabilityChanged: false, items: [item]
    }),
    { mode: 'success', message: 'Review requested; the published symbol remains available.' }
  );
});

test('active and operator-waiting replay is accepted only as pending non-terminal work', () => {
  const item = {
    symbolId: '11111111-1111-4111-8111-111111111111',
    commentId: '22222222-2222-4222-8222-222222222222',
    reviewCaseId: '33333333-3333-4333-8333-333333333333',
    edQueueItemId: '44444444-4444-4444-8444-444444444444',
    remainsPublished: true,
    requestReplayed: true,
    workflowDeliveryState: 'pending'
  };

  assert.deepEqual(
    publishedFeedbackLifecycleNotice('send_for_review', {
      status: 'accepted_pending_delivery',
      command: 'send_for_review',
      publishedAvailabilityChanged: false,
      items: [item]
    }),
    { mode: 'info', message: 'Review recorded; Ed delivery is pending. The published symbol remains available.' }
  );
  assert.deepEqual(
    publishedFeedbackLifecycleNotice('send_for_review', {
      status: 'completed',
      command: 'send_for_review',
      publishedAvailabilityChanged: false,
      items: [item]
    }),
    { mode: 'error', message: 'The review response could not be verified. Please refresh before trying again.' }
  );
});

test('missing or contradictory lifecycle fields fail closed and ignore arbitrary message', () => {
  const invalid = [
    null,
    { status: 'completed', command: 'send_for_review', publishedAvailabilityChanged: true, items: [] },
    { status: 'completed', command: 'send_for_review', publishedAvailabilityChanged: false, items: [{ remainsPublished: false }] },
    { status: 'completed', command: 'comment', publishedAvailabilityChanged: false, items: [{}] },
    { status: 'completed', command: 'send_for_review', publishedAvailabilityChanged: false, items: [], message: 'Everything is safe.' }
  ];

  for (const result of invalid) {
    assert.deepEqual(
      publishedFeedbackLifecycleNotice('send_for_review', result),
      { mode: 'error', message: 'The review response could not be verified. Please refresh before trying again.' }
    );
  }
});