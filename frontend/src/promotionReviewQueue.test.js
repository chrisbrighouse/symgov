import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';

import { PromotionReviewQueuePanel } from './PromotionReviewQueuePanel.js';
import { PromotedSymbolsPanel } from './PromotedSymbolsPanel.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function queueItem(overrides = {}) {
  return {
    id: 'req-1',
    governedSymbolId: 'sym-1',
    status: 'submitted',
    reason: 'Widely useful across the industry.',
    submittedAt: '2026-10-24T09:00:00Z',
    reviewCaseId: null,
    possibleDuplicateOfSlug: null,
    organizationName: 'Acme',
    symbolName: 'Fire hydrant',
    slug: 'fire-hydrant',
    category: 'fire',
    discipline: 'civil',
    revision: { revisionLabel: 'r1', summary: 'A fire hydrant symbol.' },
    submittedByName: 'Ada Admin',
    canDecide: true,
    blockedReason: null,
    ...overrides,
  };
}

function textOf(renderer) {
  return JSON.stringify(renderer.toJSON());
}

function button(renderer, label) {
  return renderer.root.find((node) => node.type === 'button' && node.props['aria-label'] === label);
}

async function mountQueue(api) {
  let renderer;
  await act(async () => {
    renderer = create(createElement(PromotionReviewQueuePanel, { api }));
  });
  return renderer;
}

describe('PromotionReviewQueuePanel', () => {
  it('lists a request with its symbol, organization, submitter and reason', async () => {
    const renderer = await mountQueue({ listQueue: async () => ({ items: [queueItem({ possibleDuplicateOfSlug: 'hydrant' })] }) });
    const text = textOf(renderer);
    assert.match(text, /Fire hydrant/);
    assert.match(text, /From Acme · submitted by Ada Admin/);
    assert.match(text, /Waiting for a reviewer · fire · civil · revision r1/);
    assert.match(text, /A fire hydrant symbol\./);
    assert.match(text, /Widely useful across the industry\./);
    assert.match(text, /Possible duplicate of public symbol hydrant\./);
    assert.ok(button(renderer, 'Open the promotion of Fire hydrant for review'));
    assert.equal(renderer.root.findAll((node) => node.type === 'button' && /^Accept/.test(node.props['aria-label'] || '')).length, 0);
  });

  it('opens a request for review, then accepts it from the queue', async () => {
    let state = queueItem();
    const calls = [];
    const api = {
      listQueue: async () => ({ items: state ? [state] : [] }),
      openReview: async (symbolId, requestId) => {
        calls.push(['open', symbolId, requestId]);
        state = { ...state, status: 'triage', reviewCaseId: 'case-1' };
      },
      decide: async (caseId, payload) => {
        calls.push(['decide', caseId, payload.decisionCode]);
        state = null;
        return { actions: [{ actionStatus: 'completed' }] };
      },
    };
    const renderer = await mountQueue(api);

    await act(async () => button(renderer, 'Open the promotion of Fire hydrant for review').props.onClick());
    assert.match(textOf(renderer), /Fire hydrant is open for your review\./);
    assert.match(textOf(renderer), /Open for review · fire/);

    const originalConfirm = globalThis.window?.confirm;
    globalThis.window = { ...(globalThis.window || {}), confirm: () => true };
    try {
      await act(async () => button(renderer, 'Accept the promotion of Fire hydrant').props.onClick());
    } finally {
      if (originalConfirm) globalThis.window.confirm = originalConfirm;
    }
    assert.deepEqual(calls, [['open', 'sym-1', 'req-1'], ['decide', 'case-1', 'approve']]);
    assert.match(textOf(renderer), /Accepted\. Fire hydrant is now public\./);
    assert.match(textOf(renderer), /No promotion requests are waiting\./);
  });

  it('says so when the publication step refuses an accepted request', async () => {
    const item = queueItem({ status: 'triage', reviewCaseId: 'case-1' });
    const api = {
      listQueue: async () => ({ items: [item] }),
      decide: async () => ({ actions: [{ actionStatus: 'failed' }] }),
    };
    const renderer = await mountQueue(api);
    globalThis.window = { ...(globalThis.window || {}), confirm: () => true };
    await act(async () => button(renderer, 'Accept the promotion of Fire hydrant').props.onClick());
    assert.match(textOf(renderer), /Fire hydrant was not published: the publication step refused it\. The request is still open\./);
  });

  it('offers no decision on a request from the reviewer\'s own organization, and says why', async () => {
    const renderer = await mountQueue({
      listQueue: async () => ({ items: [queueItem({ canDecide: false, blockedReason: 'You are a member of the organization that submitted this promotion request.' })] }),
    });
    assert.match(textOf(renderer), /You are a member of the organization that submitted/);
    assert.equal(renderer.root.findAll((node) => node.type === 'button').length, 0);
  });

  it('shows an empty queue and a load error plainly', async () => {
    let renderer = await mountQueue({ listQueue: async () => ({ items: [] }) });
    assert.match(textOf(renderer), /No promotion requests are waiting\./);
    renderer = await mountQueue({ listQueue: async () => { throw new Error('Forbidden.'); } });
    assert.match(textOf(renderer), /Forbidden\./);
  });
});

async function mountPromoted(api) {
  let renderer;
  await act(async () => {
    renderer = create(createElement(MemoryRouter, null, createElement(PromotedSymbolsPanel, { api })));
  });
  return renderer;
}

describe('PromotedSymbolsPanel', () => {
  it('shows a promoted symbol as Public with its S-number, origin and a Catalog link', async () => {
    const renderer = await mountPromoted({
      listPromoted: async () => ({
        items: [{
          id: 'sym-1', canonicalName: 'Fire hydrant', catalogSymbolId: 'S-1905',
          promotedFromOrganizationName: 'Acme', promotedAt: '2026-10-25T09:30:00Z',
        }],
      }),
    });
    const text = textOf(renderer);
    assert.match(text, /Promoted to public/);
    assert.match(text, /S-1905 · Fire hydrant/);
    assert.match(text, /Public · Promoted from Acme on /);
    const link = renderer.root.find((node) => node.type === 'a');
    assert.equal(link.props.href, '/s/S-1905');
  });

  it('renders nothing when no symbol has been promoted', async () => {
    const renderer = await mountPromoted({ listPromoted: async () => ({ items: [] }) });
    assert.equal(renderer.toJSON(), null);
  });
});
