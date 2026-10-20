import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import SupplementalPhotoStrip, { photoCreditParts } from './SupplementalPhotoStrip.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function photo(overrides = {}) {
  return {
    id: 'p-1',
    title: 'Gate valve.jpg',
    sourceUrl: 'https://commons.wikimedia.org/wiki/File:Gate_valve.jpg',
    sourceDomain: 'commons.wikimedia.org',
    author: 'Ann Example',
    licenseLabel: 'CC BY-SA 4.0',
    licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0',
    previewUrl: '/api/v1/published/concept-photos/p-1/preview',
    ...overrides,
  };
}

function textOf(node) {
  return typeof node === 'string' ? node : node.children.map(textOf).join('');
}

function render(photos) {
  let renderer;
  act(() => {
    renderer = create(createElement(SupplementalPhotoStrip, { photos, resolveUrl: (url) => `https://symgov.test${url}` }));
  });
  return renderer;
}

describe('photoCreditParts', () => {
  it('names the author, links the licence and the source', () => {
    assert.deepEqual(photoCreditParts(photo()), [
      { key: 'author', text: 'Ann Example' },
      { key: 'license', text: 'CC BY-SA 4.0', href: 'https://creativecommons.org/licenses/by-sa/4.0' },
      { key: 'source', text: 'Wikimedia Commons', href: 'https://commons.wikimedia.org/wiki/File:Gate_valve.jpg' },
    ]);
  });

  it('leaves out whatever is unknown', () => {
    assert.deepEqual(photoCreditParts(photo({ author: null, licenseUrl: null })), [
      { key: 'license', text: 'CC BY-SA 4.0', href: null },
      { key: 'source', text: 'Wikimedia Commons', href: 'https://commons.wikimedia.org/wiki/File:Gate_valve.jpg' },
    ]);
    assert.deepEqual(photoCreditParts(photo({ author: null, licenseLabel: null, sourceUrl: null })), []);
  });
});

describe('SupplementalPhotoStrip', () => {
  it('renders nothing without photos', () => {
    assert.equal(render([]).toJSON(), null);
    assert.equal(render(undefined).toJSON(), null);
  });

  it('shows at most two photos, each with its credit line', () => {
    const renderer = render([photo(), photo({ id: 'p-2', author: null }), photo({ id: 'p-3' })]);
    const figures = renderer.root.findAllByType('figure');
    assert.equal(figures.length, 2);

    const image = figures[0].findByType('img');
    assert.equal(image.props.src, 'https://symgov.test/api/v1/published/concept-photos/p-1/preview');
    assert.equal(image.props.alt, 'Gate valve.jpg');

    const credit = figures[0].findByType('figcaption');
    const links = credit.findAllByType('a').map((link) => [link.children.join(''), link.props.href]);
    assert.deepEqual(links, [
      ['CC BY-SA 4.0', 'https://creativecommons.org/licenses/by-sa/4.0'],
      ['Wikimedia Commons', 'https://commons.wikimedia.org/wiki/File:Gate_valve.jpg'],
    ]);
    assert.equal(textOf(credit), 'Ann Example · CC BY-SA 4.0 · Wikimedia Commons');
    assert.equal(textOf(figures[1].findByType('figcaption')), 'CC BY-SA 4.0 · Wikimedia Commons');
  });

  it('labels the strip as illustrative', () => {
    const section = render([photo()]).root.findByType('section');
    assert.equal(section.props['aria-label'], 'Illustrative equipment photos');
  });
});
