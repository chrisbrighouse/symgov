import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { HannahConceptReview, formatTimestamp, parseSearchTerms, photoDecisions } from './HannahConceptReview.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function photo(overrides = {}) {
  return {
    id: 'p-1',
    status: 'proposed',
    rank: 1,
    score: 0.72,
    source: 'commons_category',
    sourceUrl: 'https://commons.wikimedia.org/wiki/File:Gate_valve.jpg',
    thumbUrl: 'https://upload.wikimedia.org/thumb/Gate_valve.jpg',
    title: 'Gate valve.jpg',
    author: 'Ann',
    licenseLabel: 'CC BY-SA 4.0',
    licenseUrl: null,
    depicts: 'a flanged gate valve',
    reason: 'A real photograph of a gate valve.',
    filterReasons: [],
    ...overrides,
  };
}

function concept(overrides = {}) {
  return {
    targetId: 't-1',
    conceptId: 'c-1',
    conceptCode: 'SGC-00000042',
    name: 'Gate valve',
    conceptKind: 'physical_equipment',
    photographable: 'yes',
    photographableBasis: 'concept_kind',
    searchTerms: ['gate valve'],
    status: 'has_proposals',
    attemptCount: 1,
    lastAttemptAt: '2026-10-20T09:05:00Z',
    nextEligibleAt: null,
    wikidataQid: 'Q1',
    commonsCategory: 'Gate valves',
    wikipediaUrl: null,
    symbolCount: 2,
    exampleSymbolIds: ['S-000002', 'S-000010'],
    photoCounts: { proposed: 2, filtered: 3 },
    photos: [photo(), photo({ id: 'p-2', title: 'Valve 2.jpg', score: 0.6 })],
    ...overrides,
  };
}

function fakeApi(overrides = {}) {
  const calls = [];
  const api = {
    list: async (params) => {
      calls.push(['list', params]);
      return { items: [concept()], total: 1, offset: 0, limit: 25, summary: { has_proposals: 1, excluded: 4 } };
    },
    photos: async (targetId) => {
      calls.push(['photos', targetId]);
      return [photo(), photo({ id: 'p-9', status: 'filtered', title: 'Valve symbol.png', score: null, filterReasons: ['title_names_a_drawing'] })];
    },
    update: async (targetId, body) => {
      calls.push(['update', targetId, body]);
      return concept({ ...body, searchTerms: body.searchTerms || ['gate valve'], photographableBasis: 'steward' });
    },
    searchNow: async (targetId) => {
      calls.push(['searchNow', targetId]);
      return { queueItemId: 'q-1', status: 'searching', message: 'Searching SGC-00000042 now.' };
    },
    decide: async (photoId, decision) => {
      calls.push(['decide', photoId, decision]);
      return concept({ status: 'has_proposals', photos: [photo({ status: 'approved' }), photo({ id: 'p-2', title: 'Valve 2.jpg' })] });
    },
    startSearch: async () => {
      calls.push(['startSearch']);
      return { message: 'Searching 2 concepts: SGC-00000042, SGC-00000043.' };
    },
    ...overrides,
  };
  return { api, calls };
}

async function mount(api) {
  let renderer;
  await act(async () => {
    renderer = create(createElement(HannahConceptReview, { api }));
  });
  // The list load is scheduled on a timer of 0 ms when the search box is empty.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 5));
  });
  return renderer;
}

function textOf(node) {
  if (typeof node === 'string') return node;
  return (node.children || []).map(textOf).join('');
}

function button(renderer, label) {
  return renderer.root.find((node) => node.type === 'button' && (node.props['aria-label'] === label || textOf(node) === label));
}

describe('HannahConceptReview', () => {
  it('opens on the concepts waiting for a steward and names them by code', async () => {
    const { api, calls } = fakeApi();
    const renderer = await mount(api);

    assert.deepEqual(calls[0], ['list', { status: 'has_proposals', q: '', offset: 0, limit: 25 }]);
    const heading = renderer.root.find((node) => node.type === 'h3');
    assert.equal(textOf(heading), 'SGC-00000042 Gate valve');
    const meta = textOf(renderer.root.find((node) => node.props.className === 'hannah-concept-meta'));
    assert.match(meta, /2 symbols: S-000002, S-000010/);
    assert.match(meta, /Last searched 2026-10-20 09:05 UTC/);
    assert.match(meta, /Wikidata Q1/);
  });

  it('approves a photo and replaces the card with the answer', async () => {
    const { api, calls } = fakeApi();
    const renderer = await mount(api);

    await act(async () => {
      button(renderer, 'Approve Gate valve.jpg for SGC-00000042').props.onClick();
    });

    assert.deepEqual(calls.at(-1), ['decide', 'p-1', 'approve']);
    assert.ok(button(renderer, 'Remove Gate valve.jpg for SGC-00000042'));
  });

  it('saves search terms and the photographable answer as a steward', async () => {
    const { api, calls } = fakeApi();
    const renderer = await mount(api);
    const input = renderer.root.find((node) => node.props['aria-label'] === 'Search terms for SGC-00000042');
    await act(async () => {
      input.props.onChange({ target: { value: 'gate valve, sluice valve' } });
    });
    await act(async () => {
      renderer.root.find((node) => node.type === 'form').props.onSubmit({ preventDefault() {} });
    });
    assert.deepEqual(calls.at(-1), ['update', 't-1', { searchTerms: ['gate valve', 'sluice valve'] }]);

    const select = renderer.root.find((node) => node.props['aria-label'] === 'Can SGC-00000042 be photographed');
    await act(async () => {
      select.props.onChange({ target: { value: 'no' } });
    });
    assert.deepEqual(calls.at(-1), ['update', 't-1', { photographable: 'no' }]);
  });

  it('shows every image Hannah checked on request, with her reasons', async () => {
    const { api, calls } = fakeApi();
    const renderer = await mount(api);

    await act(async () => {
      button(renderer, 'Show all 5 images Hannah checked').props.onClick();
    });

    assert.deepEqual(calls.at(-1), ['photos', 't-1']);
    const reasons = renderer.root.findAll((node) => node.props.className === 'hannah-photo-reason').map(textOf);
    assert.ok(reasons.includes('title_names_a_drawing'));
  });

  it('starts a search of due concepts and of one concept, and reports what Hannah said', async () => {
    const { api, calls } = fakeApi();
    const renderer = await mount(api);

    await act(async () => {
      button(renderer, 'Search due concepts').props.onClick();
    });
    assert.match(textOf(renderer.root.find((node) => node.props.className === 'search-inline-status')), /Searching 2 concepts/);

    await act(async () => {
      button(renderer, 'Search for photos of SGC-00000042 now').props.onClick();
    });
    assert.deepEqual(calls.at(-1), ['searchNow', 't-1']);
    assert.equal(textOf(renderer.root.find((node) => node.props.role === 'status' && node.props.className === 'inline-status success')), 'Searching SGC-00000042 now.');
  });

  it('shows a refused decision as an alert', async () => {
    const { api } = fakeApi({
      decide: async () => {
        throw new Error('This concept already has 2 approved photos; remove one first.');
      },
    });
    const renderer = await mount(api);

    await act(async () => {
      button(renderer, 'Approve Gate valve.jpg for SGC-00000042').props.onClick();
    });

    assert.match(textOf(renderer.root.find((node) => node.props.role === 'alert')), /remove one first/);
  });
});

describe('Hannah review helpers', () => {
  it('offers only the decisions the server accepts', () => {
    assert.deepEqual(photoDecisions({ status: 'approved' }), ['remove']);
    assert.deepEqual(photoDecisions({ status: 'proposed' }), ['approve', 'reject']);
    assert.deepEqual(photoDecisions({ status: 'rejected' }), ['approve']);
    assert.deepEqual(photoDecisions({ status: 'filtered' }), []);
  });

  it('splits terms and formats timestamps for operators', () => {
    assert.deepEqual(parseSearchTerms(' gate valve, ,sluice valve '), ['gate valve', 'sluice valve']);
    assert.equal(formatTimestamp('2026-10-20T09:05:41Z'), '2026-10-20 09:05 UTC');
    assert.equal(formatTimestamp(null), '');
  });
});
