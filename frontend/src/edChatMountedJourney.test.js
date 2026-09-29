import { afterEach, beforeEach, describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';

// Ed Stage 5. Proved through the real application router, as for semantic
// review: reachability for the capability, the question round trip, and
// what the page shows for each kind of answer.

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function response(status, payload) {
  const body = payload == null ? '' : JSON.stringify(payload);
  return { ok: status >= 200 && status < 300, status, text: async () => body, json: async () => payload };
}

function user({ edEnabled = true } = {}) {
  return {
    id: 'u-1',
    email: 'member@example.test',
    displayName: 'Mo Member',
    roles: [],
    mustChangePin: false,
    subscription: { tier: 'free', status: 'active' },
    session: { mode: 'organization', purpose: 'application', activeOrganizationId: 'org-1' },
    organization: { id: 'org-1', code: 'acme', displayName: 'Acme Engineering', baseRole: 'user', capabilities: [] },
    isPlatformAdmin: false,
    capabilities: { organizationAdminEnabled: false, platformAdminEnabled: false, symbolSetsEnabled: true, semanticReviewEnabled: false, edEnabled },
    recentStepUpAt: null,
  };
}

const ANSWER = {
  answer: 'A project belongs to one organization.\nIt decides which symbol sets are available.',
  status: 'answered',
  mode: 'mixed',
  citations: [
    { sourceType: 'live_record', title: 'Live project context', reference: 'live:project_context:7c8c3128-4022-58e5-8d2f-13ab86fd8f6b', asOf: '2026-09-29T12:34:56+00:00' },
    { sourceType: 'approved_knowledge', title: 'Projects live inside your organization', reference: 'knowledge:198fca6e359e:claim:project.organization-visibility:v1', asOf: null },
  ],
  context: { organization: 'Acme Engineering', project: 'P-01', scope: 'organization' },
  warnings: [],
  suggestedFollowups: ['What happens when a project is closed?'],
  readOnly: true,
  knowledgeVersion: 'sha256:ffbc5470a002703468e03600a1895fd19999943dca3f3ac00a56cf5214b496d9',
  attributions: [],
};

async function mount(path, currentUser, requests, edReply) {
  globalThis.fetch = async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ url, method, body: options.body, signal: options.signal });
    if (url.includes('/auth/me')) return response(200, { user: currentUser });
    if (url.includes('/ed/chat')) return edReply(options);
    if (url.includes('/projects/context') || url.includes('/symbol-context')) return response(200, {});
    return response(200, {});
  };
  const vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  let renderer;
  try {
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    await act(async () => {
      renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: [path] }, createElement(App)));
    });
  } finally {
    await vite.close();
  }
  return renderer;
}

async function ask(renderer, text) {
  const textarea = renderer.root.findByProps({ id: 'ed-prompt' });
  await act(async () => textarea.props.onChange({ target: { value: text } }));
  const form = renderer.root.findByProps({ className: 'glass-panel pane ed-composer' });
  await act(async () => form.props.onSubmit({ preventDefault() {} }));
}

function text(renderer) {
  return JSON.stringify(renderer.toJSON());
}

describe('mounted Ed journey', () => {
  let originalFetch;
  beforeEach(() => { originalFetch = globalThis.fetch; });
  afterEach(() => { globalThis.fetch = originalFetch; });

  it('shows the Ed rail entry and page for a pilot session, read-only and with suggestions', async () => {
    const renderer = await mount('/ed', user(), [], () => response(200, ANSWER));

    assert.ok(renderer.root.findByProps({ 'aria-label': 'Ed' }), 'rail entry');
    const markup = text(renderer);
    assert.match(markup, /Ask Ed about Symgov/);
    assert.match(markup, /Read-only/);
    assert.match(markup, /Answering for Acme Engineering/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Suggested questions' }));
    assert.equal(renderer.root.findByProps({ role: 'log' }).props['aria-live'], 'polite');
    await act(async () => renderer.unmount());
  });

  it('hides the entry and the page outside the pilot', async () => {
    const requests = [];
    const renderer = await mount('/ed', user({ edEnabled: false }), requests, () => response(404, { detail: 'Not found.' }));

    assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Ed' }).length, 0);
    assert.match(text(renderer), /Ed is not available in this session yet/);
    assert.equal(requests.some(({ url }) => url.includes('/ed/chat')), false);
    await act(async () => renderer.unmount());
  });

  it('asks the v1 endpoint and shows the answer with its sources and context', async () => {
    const requests = [];
    const renderer = await mount('/ed', user(), requests, () => response(200, ANSWER));

    await ask(renderer, 'What does a project control?');

    const chat = requests.find(({ url }) => url.includes('/ed/chat'));
    assert.match(chat.url, /\/api\/v1\/ed\/chat$/);
    assert.equal(chat.method, 'POST');
    assert.deepEqual(JSON.parse(chat.body), { prompt: 'What does a project control?' });
    const markup = text(renderer);
    assert.match(markup, /What does a project control\?/);
    assert.match(markup, /A project belongs to one organization/);
    assert.match(markup, /From approved knowledge and your live records/);
    assert.match(markup, /Sources \(2\)/);
    assert.match(markup, /2026-09-29 12:34 UTC/);
    assert.match(markup, /ffbc5470a002/);
    assert.match(markup, /Answering for Acme Engineering · P-01/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Suggested follow-up questions' }));
    assert.equal(renderer.root.findByProps({ id: 'ed-prompt' }).props.value, '', 'composer cleared');
    await act(async () => renderer.unmount());
  });

  it('shows the ISO attribution wherever an answer carries one', async () => {
    const reply = {
      ...ANSWER,
      mode: 'live_data',
      attributions: [{
        source: 'iso_ics, edition 7',
        attribution: 'Taxonomy classification is based on the International Classification for Standards (ICS), 7th edition (2015), © ISO.',
        licenseCode: 'ODC-By 1.0',
        licenseUrl: 'https://opendatacommons.org/licenses/by/1-0/',
        clarification: 'ICS codes are used here only as a classification taxonomy.',
      }],
    };
    const renderer = await mount('/ed', user(), [], () => response(200, reply));

    await ask(renderer, 'What does ICS 29 mean?');

    const aside = renderer.root.findByProps({ 'aria-label': 'Data attribution' });
    const markup = text(renderer);
    assert.match(markup, /© ISO/);
    assert.match(markup, /only as a classification taxonomy/);
    assert.equal(aside.findByType('a').props.href, 'https://opendatacommons.org/licenses/by/1-0/');
    await act(async () => renderer.unmount());
  });

  it('shows a refusal and each failure plainly, and keeps the conversation going', async () => {
    const replies = [
      response(200, { ...ANSWER, status: 'refused', mode: 'blocked', answer: 'Ed is read-only and cannot change Symgov data.', citations: [], suggestedFollowups: [] }),
      response(429, { detail: 'Too many Ed requests. Please try again later.' }),
      response(404, { detail: 'Not found.' }),
    ];
    const renderer = await mount('/ed', user(), [], () => replies.shift());

    await ask(renderer, 'Delete this symbol set.');
    await ask(renderer, 'And again?');
    await ask(renderer, 'Once more?');

    const markup = text(renderer);
    assert.match(markup, /Declined/);
    assert.match(markup, /Ed is read-only and cannot change Symgov data/);
    assert.match(markup, /Wait a minute, then try again/);
    assert.match(markup, /not available for your organization yet/);
    assert.equal(renderer.root.findAllByProps({ className: 'ed-turn ed-turn-user' }).length, 3);
    await act(async () => renderer.unmount());
  });

  it('clearing the conversation also clears the context it reported', async () => {
    const renderer = await mount('/ed', user(), [], () => response(200, ANSWER));

    await ask(renderer, 'What does a project control?');
    assert.match(text(renderer), /Answering for Acme Engineering · P-01/);
    const clear = renderer.root.findAllByType('button').find((button) => button.props.children === 'Clear conversation');
    await act(async () => clear.props.onClick());

    const markup = text(renderer);
    assert.doesNotMatch(markup, /P-01/);
    assert.match(markup, /Answering for Acme Engineering/);
    assert.ok(renderer.root.findByProps({ 'aria-label': 'Suggested questions' }));
    await act(async () => renderer.unmount());
  });

  it('can cancel a question in flight', async () => {
    let release;
    const renderer = await mount('/ed', user(), [], (options) => new Promise((resolve, reject) => {
      release = () => resolve(response(200, ANSWER));
      options.signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })));
    }));

    const textarea = renderer.root.findByProps({ id: 'ed-prompt' });
    await act(async () => textarea.props.onChange({ target: { value: 'A slow question' } }));
    const form = renderer.root.findByProps({ className: 'glass-panel pane ed-composer' });
    let submitted;
    await act(async () => { submitted = form.props.onSubmit({ preventDefault() {} }); });
    assert.match(text(renderer), /Ed is working on an answer/);
    const cancel = renderer.root.findAllByType('button').find((button) => button.props.children === 'Cancel');
    await act(async () => { cancel.props.onClick(); await submitted; });

    assert.match(text(renderer), /Question cancelled/);
    assert.equal(typeof release, 'function');
    await act(async () => renderer.unmount());
  });
});
