import test from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { OrganizationIcon } from './OrganizationSelectionPage.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

const organization = { displayName: 'Acme', code: 'ACME', logoUrl: '/api/v1/organizations/o-1/logo?v=1' };

async function render(props) {
  let renderer;
  await act(async () => {
    renderer = TestRenderer.create(createElement(OrganizationIcon, props));
  });
  return renderer;
}

test('OrganizationIcon: renders the uploaded logo as a decorative image', async () => {
  const renderer = await render({ organization });
  const image = renderer.root.findByType('img');
  assert.equal(image.props.src, organization.logoUrl);
  assert.equal(image.props.alt, '');
});

test('OrganizationIcon: the picker falls back to the initial when the logo cannot load', async () => {
  const renderer = await render({ organization });
  await act(async () => renderer.root.findByType('img').props.onError());
  assert.equal(renderer.root.findAllByType('img').length, 0);
  assert.deepEqual(renderer.root.findByProps({ className: 'org-selection-fallback' }).children, ['A']);
});

test('OrganizationIcon: the header drops the slot entirely when the logo cannot load', async () => {
  const renderer = await render({ organization, hideOnFailure: true });
  await act(async () => renderer.root.findByType('img').props.onError());
  assert.equal(renderer.toJSON(), null);
});

test('OrganizationIcon: a replaced logo is tried again after an earlier failure', async () => {
  const renderer = await render({ organization });
  await act(async () => renderer.root.findByType('img').props.onError());
  await act(async () => renderer.update(createElement(OrganizationIcon, {
    organization: { ...organization, logoUrl: '/api/v1/organizations/o-1/logo?v=2' },
  })));
  assert.equal(renderer.root.findByType('img').props.src, '/api/v1/organizations/o-1/logo?v=2');
});
