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
    const dexpi = JSON.parse(await readFile(new URL('../../backend/symgov_backend/data/dexpi-source.json', import.meta.url), 'utf8'));
    const escaped = text => text.replaceAll('&', '&amp;').replaceAll("'", '&#x27;');
    for (const key of ['attribution', 'modifications', 'clarification', 'limitation']) assert.ok(markup.includes(escaped(dexpi[key])), key);
    for (const key of ['source_url', 'page_url', 'license_url']) assert.ok(markup.includes(`href="${dexpi[key]}"`), key);
    globalThis.fetch = async () => ({ ok: true, status: 200, text: async () => JSON.stringify({ user: { id: 'support-user', displayName: 'Support test', roles: [], mustChangePin: false, subscription: { tier: 'free', status: 'active' }, session: { mode: 'personal', purpose: 'application' }, capabilities: {} } }) });
    const { default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx');
    await act(async () => { renderer = TestRenderer.create(createElement(MemoryRouter, { initialEntries: ['/support'] }, createElement(App))); });
    assert.equal(renderer.root.findByProps({ 'aria-labelledby': 'classification-data-sources' }).type, 'section');
    assert.ok(renderer.root.findAllByType('a').some(a => a.props.href === source.license_url));
    assert.equal(renderer.root.findByProps({ 'aria-labelledby': 'symbol-library-data-sources' }).type, 'section');
    assert.ok(renderer.root.findAllByType('a').some(a => a.props.href === dexpi.license_url));
    assert.equal(renderer.root.findAllByType('textarea').length, 0);
    assert.equal(renderer.root.findAllByType('form').length, 0);
    assert.equal(renderer.root.findByProps({ 'aria-labelledby': 'support-ask-ed' }).type, 'section');
  } finally {
    if (renderer) await act(async () => renderer.unmount());
    globalThis.fetch = oldFetch;
    await vite.close();
  }
});


test('an imported library section is built from its record, attribution included', async () => {
  const vite = await createServer({ configFile: false, root: process.cwd(), server: { middlewareMode: true, hmr: false }, appType: 'custom' });
  try {
    const { ImportedLibrarySources } = await vite.ssrLoadModule('/frontend/src/SupportDataSources.jsx');
    const record = {
      packageCode: 'EXAMPLE-LIB-1.0',
      title: 'Example symbol library (Profile 1.0)',
      releaseVersion: 'Profile 1.0 @ abc123',
      attributionText: 'Stand-in attribution text for a test.',
      attributionIsPlaceholder: true,
      licensor: 'A. Licensor',
      creator: 'The Example project',
      publishedSymbols: 12,
      sourceUri: 'https://example.test/library',
      organisationUrl: 'https://example.test'
    };
    const markup = renderToStaticMarkup(createElement(ImportedLibrarySources, { sources: [record] }));
    assert.match(markup, /aria-labelledby="library-source-example-lib-1-0"/);
    assert.match(markup, /Example symbol library \(Profile 1\.0\)/);
    assert.ok(markup.includes(record.attributionText));
    assert.match(markup, /provisional/);
    assert.match(markup, /Licensed by A\. Licensor/);
    assert.match(markup, /12 published symbols/);
    assert.match(markup, /href="https:\/\/example\.test\/library"/);
    assert.equal(renderToStaticMarkup(createElement(ImportedLibrarySources, { sources: [] })), '');
    const final = renderToStaticMarkup(createElement(ImportedLibrarySources, { sources: [{ ...record, attributionIsPlaceholder: false }] }));
    assert.doesNotMatch(final, /provisional/);
  } finally {
    await vite.close();
  }
});
