import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter } from 'react-router-dom';
import { createServer } from 'vite';
import { readFile } from 'node:fs/promises';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

test('Support renders official attribution and mounts it through the authenticated App route', async () => {
  const source = JSON.parse(await readFile(new URL('../../backend/symgov_backend/data/ics-source.json', import.meta.url), 'utf8'));
  const vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  const oldFetch = globalThis.fetch;
  let renderer;
  try {
    const { default: Sources } = await vite.ssrLoadModule('/frontend/src/SupportDataSources.jsx');
    const markup = renderToStaticMarkup(createElement(Sources));
    assert.ok(markup.includes(source.attribution));
    assert.ok(markup.includes(source.clarification));
    for (const key of ['browse_url', 'page_url', 'license_url']) assert.ok(markup.includes(`href="${source[key]}"`));
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ user: { id: 'support-user', displayName: 'Support test', roles: [], mustChangePin: false, subscription: { tier: 'free', status: 'active' }, session: { mode: 'personal', purpose: 'application' }, capabilities: {} } }) });
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    await act(async () => { renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: ['/support'] }, createElement(App))); });
    assert.equal(renderer.root.findByProps({ 'aria-labelledby': 'classification-data-sources' }).type, 'section');
    assert.ok(renderer.root.findAllByType('a').some(a => a.props.href === source.license_url));
    const textarea = renderer.root.findByType('textarea');
    await act(async () => textarea.props.onChange({ target: { value: 'Help with classification' } }));
    await act(async () => renderer.root.findByType('form').props.onSubmit({ preventDefault() {} }));
    assert.ok(JSON.stringify(renderer.toJSON()).includes('Support request captured locally'));
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    globalThis.fetch = oldFetch;
    await vite.close();
  }
});
