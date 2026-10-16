import assert from 'node:assert/strict';
import test from 'node:test';

import {
  buildLegendPrintHtml,
  legendPageCount,
  legendSheetPath,
  openLegendForPrint,
  requestLegendSheet,
} from './legendSheet.js';

const SHEET = '<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="http://www.w3.org/2000/svg" width="420mm" height="594mm" viewBox="0 0 420 594"><g class="legend-page" data-page="1"/><g class="legend-page" data-page="2"/></svg>';

test('legend paths are per Symbol Set or Project and encode the id', () => {
  assert.equal(legendSheetPath('set', 'a b'), '/org/me/symbol-sets/a%20b/legend.svg');
  assert.equal(legendSheetPath('project', 'p1'), '/org/me/projects/p1/legend.svg');
});

test('pages are counted from the sheet markup', () => {
  assert.equal(legendPageCount(SHEET), 2);
  assert.equal(legendPageCount('<svg/>'), 0);
  assert.equal(legendPageCount(null), 0);
});

test('print html defines the sheet once and shows one A3 page per viewBox slice', () => {
  const html = buildLegendPrintHtml(SHEET, 'PLANT · <Main>');
  assert.equal((html.match(/id="legend-root"/g) || []).length, 1);
  assert.ok(html.includes('viewBox="0 0 420 297"'));
  assert.ok(html.includes('viewBox="0 297 420 297"'));
  assert.ok(html.includes('size:A3 landscape'));
  assert.ok(html.includes('PLANT · &lt;Main&gt;'));
  assert.ok(!html.includes('<Main>'));
  assert.ok(!html.includes('<?xml'));
  assert.equal(buildLegendPrintHtml('<svg/>'), '');
});

test('requestLegendSheet reports unavailable, failed and empty legends', async () => {
  const reply = (status, body = '') => async () => ({ ok: status < 300, status, text: async () => body });
  assert.deepEqual(await requestLegendSheet('/x', { apiRoot: '/api', fetchImpl: reply(200, SHEET) }), { ok: true, svg: SHEET });
  assert.match((await requestLegendSheet('/x', { apiRoot: '/api', fetchImpl: reply(404) })).message, /not available/);
  assert.match((await requestLegendSheet('/x', { apiRoot: '/api', fetchImpl: reply(500) })).message, /could not be built/);
  assert.match((await requestLegendSheet('/x', { apiRoot: '/api', fetchImpl: reply(200, '<svg/>') })).message, /empty/);
  assert.equal((await requestLegendSheet('/x', { apiRoot: '', fetchImpl: reply(200) })).ok, false);
  const offline = async () => { throw new Error('offline'); };
  assert.equal((await requestLegendSheet('/x', { apiRoot: '/api', fetchImpl: offline })).message, 'offline');
});

test('the print window is opened first and closed when the legend fails', async () => {
  const calls = [];
  const target = { closed: false, close() { this.closed = true; }, focus() {}, document: { open() {}, write: (html) => calls.push(html), close() {} } };
  const fail = await openLegendForPrint('/x', 'T', { openWindow: () => target, apiRoot: '/api', fetchImpl: async () => ({ ok: false, status: 404, text: async () => '' }) });
  assert.equal(fail.ok, false);
  assert.equal(target.closed, true);
  assert.equal(calls.length, 0);

  const win = { close() {}, focus() {}, document: { open() {}, write: (html) => calls.push(html), close() {} } };
  const ok = await openLegendForPrint('/x', 'T', { openWindow: () => win, apiRoot: '/api', fetchImpl: async () => ({ ok: true, status: 200, text: async () => SHEET }) });
  assert.equal(ok.ok, true);
  assert.equal(calls.length, 1);

  const blocked = await openLegendForPrint('/x', 'T', { openWindow: () => null });
  assert.match(blocked.message, /pop-ups/);
});
