import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import PanelErrorBoundary from './PanelErrorBoundary.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function Broken() {
  // The shape that blanked the Catalog page: an object rendered as a child.
  return createElement('span', null, { value: 'Process', count: 7 });
}

test('a panel that fails to render is replaced by a notice, and its siblings stay up', () => {
  const originalError = console.error;
  console.error = () => {};
  let renderer;
  try {
    act(() => {
      renderer = TestRenderer.create(createElement('main', null,
        createElement('p', { id: 'sibling' }, 'Catalog results'),
        createElement(PanelErrorBoundary, { label: 'The preferences panel' }, createElement(Broken))
      ));
    });
  } finally {
    console.error = originalError;
  }
  assert.equal(renderer.root.findByProps({ id: 'sibling' }).children[0], 'Catalog results');
  const notice = renderer.root.findByProps({ role: 'alert' });
  assert.match(notice.children[0], /^The preferences panel could not be shown/);
});

test('a panel that renders normally is passed through untouched', () => {
  let renderer;
  act(() => {
    renderer = TestRenderer.create(createElement(PanelErrorBoundary, null, createElement('p', { id: 'ok' }, 'fine')));
  });
  assert.equal(renderer.root.findByProps({ id: 'ok' }).children[0], 'fine');
  assert.equal(renderer.root.findAllByProps({ role: 'alert' }).length, 0);
});
