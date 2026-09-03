import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { PromotionSubmissionPanel } from './PromotionSubmissionPanel.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function baseDraft(overrides = {}) {
  return {
    id: 'sym-1',
    slug: 'wall-mounted-alarm',
    canonicalName: 'Wall Mounted Alarm',
    category: 'fire',
    discipline: 'fire-safety',
    visibility: 'organization_private',
    organizationWide: true,
    organizationId: 'org-1',
    ownerId: 'user-1',
    currentRevisionId: 'rev-1',
    currentRevision: { id: 'rev-1', lifecycleState: 'approved' },
    createdAt: '2026-09-01T00:00:00Z',
    updatedAt: '2026-09-01T00:00:00Z',
    ...overrides,
  };
}

function basePromotionRequest(overrides = {}) {
  return {
    id: 'req-1',
    governedSymbolId: 'sym-1',
    organizationId: 'org-1',
    symbolRevisionId: 'rev-1',
    status: 'submitted',
    proposedMetadata: {},
    reason: 'Widely used across the region.',
    sharingAcknowledgment: true,
    submittedByUserId: 'user-1',
    submittedAt: '2026-09-03T00:00:00Z',
    closedAt: null,
    traceId: null,
    reviewCaseId: null,
    ...overrides,
  };
}

function buildApi({ drafts = [baseDraft()], requestsBySymbolId = {} } = {}) {
  const calls = [];
  return {
    calls,
    listDrafts: async () => ({ items: drafts }),
    listPromotionRequests: async (symbolId) => {
      calls.push(['list', symbolId]);
      return { items: requestsBySymbolId[symbolId] || [] };
    },
    submitPromotionRequest: async (symbolId, payload) => {
      calls.push(['submit', symbolId, payload]);
      return basePromotionRequest({ governedSymbolId: symbolId, reason: payload.reason });
    },
    withdrawPromotionRequest: async (symbolId, requestId, payload) => {
      calls.push(['withdraw', symbolId, requestId, payload]);
      return basePromotionRequest({ governedSymbolId: symbolId, id: requestId, status: 'withdrawn' });
    },
  };
}

describe('PromotionSubmissionPanel', () => {
  it('requires Organization Admin privileges', async () => {
    const api = buildApi();
    let renderer;
    await act(async () => { renderer = create(createElement(PromotionSubmissionPanel, { isAdmin: false, api })); });
    assert.match(JSON.stringify(renderer.toJSON()), /Organization Admin privileges are required/);
    await act(async () => renderer.unmount());
  });

  it('lists only approved or withdrawn (re-promotion-eligible) drafts', async () => {
    const api = buildApi({
      drafts: [
        baseDraft(),
        baseDraft({ id: 'sym-2', canonicalName: 'Draft Only Symbol', currentRevision: { id: 'rev-2', lifecycleState: 'draft' } }),
        baseDraft({ id: 'sym-3', canonicalName: 'Withdrawn Symbol', currentRevision: { id: 'rev-3', lifecycleState: 'withdrawn' } }),
      ],
    });
    let renderer;
    await act(async () => { renderer = create(createElement(PromotionSubmissionPanel, { isAdmin: true, api })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Wall Mounted Alarm/);
    assert.match(text, /Withdrawn Symbol/);
    assert.doesNotMatch(text, /Draft Only Symbol/);
    await act(async () => renderer.unmount());
  });

  it('submits a promotion request only once reason and sharing acknowledgment are provided', async () => {
    const api = buildApi();
    let renderer;
    await act(async () => { renderer = create(createElement(PromotionSubmissionPanel, { isAdmin: true, api })); });

    const submitButton = renderer.root.findByProps({ 'aria-label': 'Submit Wall Mounted Alarm for public promotion' });
    assert.equal(submitButton.props.disabled, true);

    await act(async () => {
      renderer.root.findByProps({ id: 'promotion-reason-sym-1' }).props.onChange({ target: { value: 'Widely used across the region.' } });
    });
    await act(async () => {
      renderer.root.findByProps({ id: 'promotion-ack-sym-1' }).props.onChange({ target: { checked: true } });
    });
    assert.equal(renderer.root.findByProps({ 'aria-label': 'Submit Wall Mounted Alarm for public promotion' }).props.disabled, false);

    await act(async () => {
      await renderer.root.findByProps({ 'aria-label': 'Submit Wall Mounted Alarm for public promotion' }).props.onClick();
    });
    const [, , payload] = api.calls.find((call) => call[0] === 'submit');
    assert.equal(payload.reason, 'Widely used across the region.');
    assert.equal(payload.sharingAcknowledgment, true);
    await act(async () => renderer.unmount());
  });

  it('shows a pending promotion request instead of the submission form, with a withdraw action', async () => {
    const api = buildApi({ requestsBySymbolId: { 'sym-1': [basePromotionRequest()] } });
    let renderer;
    await act(async () => { renderer = create(createElement(PromotionSubmissionPanel, { isAdmin: true, api })); });
    const text = JSON.stringify(renderer.toJSON());
    assert.match(text, /Promotion request pending: submitted/);
    assert.doesNotMatch(text, /Submit Wall Mounted Alarm for public promotion/);

    const originalConfirm = globalThis.window?.confirm;
    globalThis.window = globalThis.window || {};
    globalThis.window.confirm = () => true;
    await act(async () => {
      await renderer.root.findByProps({ 'aria-label': 'Withdraw promotion request for Wall Mounted Alarm' }).props.onClick();
    });
    globalThis.window.confirm = originalConfirm;

    const [, symbolId, requestId] = api.calls.find((call) => call[0] === 'withdraw');
    assert.equal(symbolId, 'sym-1');
    assert.equal(requestId, 'req-1');
    await act(async () => renderer.unmount());
  });
});
