import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { renderToStaticMarkup } from 'react-dom/server';

import LlmConsumptionReport, {
  LlmConsumptionControls,
  consumptionAnnouncement,
  humanizeConsumptionStatus
} from './LlmConsumptionView.js';

const baseUsage = {
  startUtc: '2026-08-01T00:00:00Z',
  endUtcExclusive: '2026-08-02T00:00:00Z',
  ledger: {
    status: 'available',
    totals: { attempts: 2, successful: 1, failed: 1, inputTokens: 10, outputTokens: 4, effectiveCostUsd: 0.5, unknownCostAttempts: 0 },
    breakdowns: {
      byProviderModel: [{ provider: 'openai', model: 'gpt-test', attempts: 2, input_tokens: 10, output_tokens: 4, effective_cost_usd: 0.5 }],
      byUseCase: [{ label: 'catalog', attempts: 2 }],
      byAgent: [{ label: 'reggie', attempts: 2 }]
    }
  },
  langfuse: {
    status: 'available', message: 'Langfuse metrics are available.',
    totals: { observations: 2, inputTokens: 9, outputTokens: 4, totalTokens: 13, totalCostUsd: 0.45 },
    byModel: [{ model: 'gpt-test', observations: 2, inputTokens: 9, outputTokens: 4, totalTokens: 13, totalCostUsd: 0.45 }]
  },
  reconciliation: { status: 'different', tokenDifference: 1, costDifferenceUsd: 0.05 },
  warnings: []
};

function render(props) {
  return renderToStaticMarkup(LlmConsumptionReport({ state: { loading: false, error: '', usage: null }, ...props }));
}

test('loading report remains a persistent busy live region and announces the update', () => {
  const markup = render({ state: { loading: true, error: '', usage: null }, announcement: 'Updating consumption report…' });
  assert.match(markup, /aria-busy="true"/);
  assert.match(markup, /role="status"/);
  assert.match(markup, /aria-live="polite"/);
  assert.match(markup, />Updating consumption report…</);
});

test('request errors are announced as alerts without removing the persistent status region', () => {
  const markup = render({ state: { loading: false, error: 'Request timed out.', usage: null }, announcement: 'Consumption report could not be updated.' });
  assert.match(markup, /role="status"/);
  assert.match(markup, /role="alert"/);
  assert.match(markup, /Consumption data could not be loaded\. Request timed out\./);
});

test('available response renders authoritative and Langfuse totals, model rows, and reconciliation differences', () => {
  const markup = render({ state: { loading: false, error: '', usage: baseUsage }, announcement: 'Consumption report updated.' });
  assert.match(markup, /Symgov ledger totals/);
  assert.match(markup, /Langfuse totals/);
  assert.match(markup, /Langfuse model breakdown/);
  assert.match(markup, /gpt-test/);
  assert.match(markup, /Token difference/);
  assert.match(markup, />1</);
  assert.match(markup, /Cost difference/);
  assert.match(markup, /\$0\.05/);
  assert.match(markup, /The Symgov ledger remains authoritative/);
});

test('tables have named empty rows inside keyboard-accessible horizontal scroll regions', () => {
  const usage = structuredClone(baseUsage);
  usage.ledger.totals.attempts = 0;
  usage.ledger.breakdowns = { byProviderModel: [], byUseCase: [], byAgent: [] };
  usage.langfuse.byModel = [];
  const markup = render({ state: { loading: false, error: '', usage }, announcement: 'Consumption report updated. No ledger attempts were recorded.' });
  assert.equal((markup.match(/tabindex="0"/g) || []).length, 4);
  assert.match(markup, /aria-label="Scrollable Provider and model breakdown table"/);
  assert.match(markup, /No provider or model usage recorded/);
  assert.match(markup, /No use case usage recorded/);
  assert.match(markup, /No agent usage recorded/);
  assert.match(markup, /No Langfuse model usage returned/);
});

test('source statuses are semantic, humanized, classed distinctly, and include safe messages', () => {
  const usage = structuredClone(baseUsage);
  usage.langfuse = { status: 'disabled', message: 'Comparison is disabled by an administrator.', totals: null, byModel: [] };
  usage.reconciliation = { status: 'notComparable' };
  const markup = render({ state: { loading: false, error: '', usage }, announcement: 'Consumption report updated with limited comparison data.' });
  assert.match(markup, /<dl[^>]*aria-label="Consumption source status"/);
  assert.match(markup, /source-status--available/);
  assert.match(markup, /source-status--disabled/);
  assert.match(markup, /Comparison is disabled by an administrator\./);
  assert.match(markup, /Not comparable/);
  assert.doesNotMatch(markup, />notComparable</);
  assert.equal(humanizeConsumptionStatus('temporarily_unavailable'), 'Temporarily unavailable');
});

test('unavailable comparison renders as degraded data with its safe source message', () => {
  const usage = structuredClone(baseUsage);
  usage.langfuse = { status: 'unavailable', message: 'Langfuse metrics are temporarily unavailable.', totals: null, byModel: null };
  usage.reconciliation = { status: 'unavailable' };
  const markup = render({ state: { loading: false, error: '', usage } });
  assert.match(markup, /source-status--unavailable/);
  assert.match(markup, /Langfuse metrics are temporarily unavailable\./);
  assert.match(markup, /Consumption report updated with limited comparison data\./);
  assert.match(markup, /Comparable differences are unavailable\./);
});

test('horizontal table regions have responsive overflow and visible keyboard focus styling', async () => {
  const css = await readFile(new URL('./styles.css', import.meta.url), 'utf8');
  assert.match(css, /\.llm-table-scroll\s*\{[^}]*overflow-x:\s*auto/s);
  assert.match(css, /\.llm-table-scroll:focus-visible\s*\{[^}]*outline:/s);
  assert.match(css, /\.llm-breakdown-table\s*\{[^}]*width:\s*100%[^}]*min-width:/s);
  assert.match(css, /\.source-status--available/);
  assert.match(css, /\.source-status--disabled/);
  assert.match(css, /\.source-status--unavailable/);
});

test('controls explain UTC anchor semantics, automatic selection updates, and manual refresh', () => {
  const markup = renderToStaticMarkup(LlmConsumptionControls({
    period: 'week', anchor: '2026-08-01', onPeriodChange() {}, onAnchorChange() {}, onRefresh() {}
  }));
  assert.match(markup, /The report updates automatically when the period or anchor changes/);
  assert.match(markup, /selected period containing the anchor date in UTC/);
  assert.match(markup, /Refresh requests the latest data again/);
  assert.match(markup, /value="week" selected=""/);
  assert.match(markup, /type="date" value="2026-08-01"/);
});

test('announcements distinguish completion, partial data, empty data, and errors', () => {
  assert.equal(consumptionAnnouncement({ loading: true }), 'Updating consumption report…');
  assert.equal(consumptionAnnouncement({ error: 'bad' }), 'Consumption report could not be updated.');
  assert.match(consumptionAnnouncement({ usage: baseUsage }), /limited comparison data/);
  const matched = structuredClone(baseUsage);
  matched.reconciliation.status = 'matched';
  assert.equal(consumptionAnnouncement({ usage: matched }), 'Consumption report updated.');
  const empty = structuredClone(matched);
  empty.ledger.totals.attempts = 0;
  assert.equal(consumptionAnnouncement({ usage: empty }), 'Consumption report updated. No ledger attempts were recorded.');
});
