import assert from 'node:assert/strict';
import test from 'node:test';
import { createElement } from 'react';
import TestRenderer, { act } from 'react-test-renderer';

import {
  OrganizationAuditPanel, OrganizationUsagePanel, PlatformUsagePanel, auditFilters, countsLine, csvRangeProblem, describeEvent,
} from './organizationUsage.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function textOf(node) {
  if (node == null) return '';
  if (typeof node === 'string') return node;
  return (node.children || []).map(textOf).join(' ');
}

async function mount(element) {
  let renderer;
  await act(async () => { renderer = TestRenderer.create(element); });
  await act(async () => {});
  return renderer;
}

test('events are named for people and counted in a fixed order', () => {
  assert.equal(describeEvent('symbol_added_to_clipboard'), 'Added to the clipboard');
  assert.equal(describeEvent('something_new'), 'something_new');
  assert.equal(countsLine({ symbol_downloaded: 2, symbol_viewed: 5 }), '5 views · 2 downloads');
  assert.equal(countsLine({}), 'none');
});

test('a CSV export needs both dates, in order, within 31 days', () => {
  assert.match(csvRangeProblem('', '2026-10-16'), /both dates/);
  assert.match(csvRangeProblem('2026-10-16', '2026-10-01'), /before/);
  assert.match(csvRangeProblem('2026-09-01', '2026-10-16'), /at most 31/);
  assert.equal(csvRangeProblem('2026-09-16', '2026-10-16'), '');
  assert.equal(csvRangeProblem('2026-10-16', '2026-10-16'), '');
});

test('audit filters drop what is blank', () => {
  assert.deepEqual(auditFilters({ entityType: '', action: '  ', since: '', until: '2026-10-16' }, 2), {
    entityType: undefined, action: undefined, since: undefined, until: '2026-10-16', page: 2, pageSize: 50,
  });
});

const USAGE = {
  totals: { symbol_viewed: 7, symbol_downloaded: 1 },
  topSymbols: [{ displayId: 'S-1', name: 'Gate valve', counts: { symbol_viewed: 5 } }],
  users: [{ displayName: 'Ada Lovelace', email: 'ada@acme.test', counts: { symbol_viewed: 4, symbol_downloaded: 1 } }],
  zeroResultSearches: [{ query: 'hydrogen vessel', searches: 2 }],
  projects: [{ code: 'P-1', name: 'Plant', events: 3 }], symbolSets: [], trend: [{ date: '2026-10-15', counts: { symbol_viewed: 2 } }],
};

test('the usage page names people and shows what found nothing', async () => {
  const requested = [];
  const api = { orgUsage: async (options) => { requested.push(options); return USAGE; } };
  const renderer = await mount(createElement(OrganizationUsagePanel, { api }));
  const text = textOf(renderer.toJSON());
  for (const expected of ['Ada Lovelace', 'ada@acme.test', 'hydrogen vessel', 'Gate valve', '5 views', '2026-10-15']) assert.match(text, new RegExp(expected));
  assert.deepEqual(requested, [{ days: 30 }]);
  await act(async () => { renderer.root.findByProps({ id: 'organization-usage-window' }).props.onChange({ target: { value: '7' } }); });
  assert.deepEqual(requested.at(-1), { days: 7 });
});

test('the usage page reports a failed load', async () => {
  const renderer = await mount(createElement(OrganizationUsagePanel, { api: { orgUsage: async () => { throw new Error('Usage is off'); } } }));
  assert.match(textOf(renderer.toJSON()), /Usage is off/);
});

test('the platform view shows totals and never asks for people', async () => {
  const api = {
    platformUsage: async () => ({
      organizations: { items: [{ displayName: 'Acme', total: 9, counts: { symbol_viewed: 9 } }, { displayName: 'Personal (no organization)', total: 2, counts: { symbol_viewed: 2 } }] },
      packs: { items: [{ packCode: '0001', total: 11, counts: { symbol_viewed: 11 } }] },
    }),
  };
  const text = textOf((await mount(createElement(PlatformUsagePanel, { api }))).toJSON());
  assert.match(text, /Acme/);
  assert.match(text, /Personal \(no organization\)/);
  assert.match(text, /0001/);
  assert.doesNotMatch(text, /@/);
});

test('the audit panel pages, filters and offers a CSV link only for a valid range', async () => {
  const calls = [];
  const item = (n) => ({ occurredAt: '2026-10-15T10:00:00+00:00', entityType: 'organization_membership', entityId: `abcdef${n}0000`, action: 'membership.deactivated', actorName: 'Admin', details: { reason: 'left' } });
  const api = {
    orgAudit: async (filters) => { calls.push(filters); return { items: [item(1)], page: filters.page, pageSize: 50, total: 120, entityTypes: ['organization', 'project'] }; },
  };
  const renderer = await mount(createElement(OrganizationAuditPanel, { api }));
  assert.match(textOf(renderer.toJSON()), /membership\.deactivated/);
  assert.match(textOf(renderer.toJSON()), /reason: left/);
  assert.match(textOf(renderer.toJSON()), /CSV export: Choose both dates/);
  assert.equal(renderer.root.findAllByType('a').length, 0);

  await act(async () => { renderer.root.findByProps({ id: 'organization-audit-heading-since' }).props.onChange({ target: { value: '2026-10-01' } }); });
  await act(async () => { renderer.root.findByProps({ id: 'organization-audit-heading-until' }).props.onChange({ target: { value: '2026-10-16' } }); });
  await act(async () => { renderer.root.findByProps({ 'aria-label': 'Filter the audit trail' }).props.onSubmit({ preventDefault() {} }); });
  const link = renderer.root.findAllByType('a');
  assert.equal(link.length, 1);
  assert.match(link[0].props.href, /\/org\/me\/audit\.csv\?since=2026-10-01&until=2026-10-16/);
  assert.equal(calls.at(-1).since, '2026-10-01');

  await act(async () => { renderer.root.findByProps({ children: 'Next' }).props.onClick(); });
  assert.equal(calls.at(-1).page, 2);
});
