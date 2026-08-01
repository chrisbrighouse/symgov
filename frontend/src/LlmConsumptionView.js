import React from 'react';

import { formatConsumptionCost, formatConsumptionNumber } from './llmConsumption.js';

const h = React.createElement;

export function humanizeConsumptionStatus(value) {
  const normalized = String(value || 'unavailable')
    .replace(/([a-z])([A-Z])/g, '$1 $2')
    .replace(/[_-]+/g, ' ')
    .trim()
    .toLowerCase();
  return normalized ? normalized.charAt(0).toUpperCase() + normalized.slice(1) : 'Unavailable';
}

export function consumptionAnnouncement(state = {}) {
  if (state.loading) return 'Updating consumption report…';
  if (state.error) return 'Consumption report could not be updated.';
  if (!state.usage) return 'Consumption report is ready to update.';
  if (Number(state.usage.ledger?.totals?.attempts) === 0) {
    return 'Consumption report updated. No ledger attempts were recorded.';
  }
  const externalStatus = state.usage.langfuse?.status;
  const reconciliationStatus = state.usage.reconciliation?.status;
  if (externalStatus !== 'available' || ['different', 'notComparable', 'unavailable'].includes(reconciliationStatus)) {
    return 'Consumption report updated with limited comparison data.';
  }
  return 'Consumption report updated.';
}

function emptyRow(message, columns) {
  return h('tr', { className: 'llm-empty-row' }, h('td', { colSpan: columns }, message));
}

function scrollTable(caption, headers, rows, emptyMessage) {
  return h('div', {
    className: 'table-scroll llm-table-scroll',
    tabIndex: 0,
    role: 'region',
    'aria-label': `Scrollable ${caption} table`
  }, h('table', { className: 'admin-users-table llm-breakdown-table' },
    h('caption', null, caption),
    h('thead', null, h('tr', null, ...headers.map((header) => h('th', { scope: 'col', key: header }, header)))),
    h('tbody', null, ...(rows.length ? rows : [emptyRow(emptyMessage, headers.length)]))
  ));
}

function sourceStatus(label, source, authoritative = false) {
  const status = source?.status || 'unavailable';
  return h('div', { className: `llm-source-item source-status--${status}`, key: label },
    h('dt', null, label),
    h('dd', null,
      h('strong', null, humanizeConsumptionStatus(status)),
      authoritative ? h('span', null, 'Authoritative source') : null,
      source?.message ? h('span', { className: 'llm-source-message' }, source.message) : null
    )
  );
}

function summary(title, values) {
  return h('section', { className: 'llm-summary-section', 'aria-label': title },
    h('h4', null, title),
    h('dl', { className: 'llm-summary-grid' }, ...values.map(([label, value]) =>
      h('div', { className: 'llm-summary-card', key: label }, h('dt', null, label), h('dd', null, value))
    ))
  );
}

function ledgerContent(ledger) {
  if (ledger?.status !== 'available') {
    return h('p', { className: 'inline-status error', role: 'alert' }, 'Authoritative ledger data is unavailable. Langfuse values, if shown, are not a substitute.');
  }
  const totals = ledger.totals || {};
  const breakdowns = ledger.breakdowns || {};
  const providerRows = (breakdowns.byProviderModel || []).map((row) => h('tr', { key: `${row.provider}-${row.model}` },
    h('td', null, row.provider), h('td', null, row.model), h('td', null, formatConsumptionNumber(row.attempts)),
    h('td', null, formatConsumptionNumber(row.input_tokens)), h('td', null, formatConsumptionNumber(row.output_tokens)),
    h('td', null, formatConsumptionCost(row.effective_cost_usd))
  ));
  const simpleTable = (caption, key, emptyMessage) => scrollTable(caption, ['Name', 'Attempts'], (breakdowns[key] || []).map((row) =>
    h('tr', { key: row.label }, h('td', null, row.label), h('td', null, formatConsumptionNumber(row.attempts)))
  ), emptyMessage);

  return h(React.Fragment, null,
    summary('Symgov ledger totals', [
      ['Known spend', formatConsumptionCost(totals.effectiveCostUsd)], ['Attempts', formatConsumptionNumber(totals.attempts)],
      ['Successful', formatConsumptionNumber(totals.successful)], ['Failures', formatConsumptionNumber(totals.failed)],
      ['Input tokens', formatConsumptionNumber(totals.inputTokens)], ['Output tokens', formatConsumptionNumber(totals.outputTokens)],
      ['Cost unknown', formatConsumptionNumber(totals.unknownCostAttempts)]
    ]),
    totals.unknownCostAttempts > 0 ? h('p', { className: 'inline-status' }, 'Known spend excludes attempts whose cost was not reported or calculable. Unknown cost is not treated as $0.') : null,
    totals.attempts === 0 ? h('p', { className: 'inline-status llm-empty-state', role: 'status' }, 'No LLM attempts were recorded in this UTC period.') : null,
    h('div', { className: 'llm-breakdown-grid' },
      scrollTable('Provider and model breakdown', ['Provider', 'Model', 'Attempts', 'Input tokens', 'Output tokens', 'Cost'], providerRows, 'No provider or model usage recorded.'),
      simpleTable('Use case breakdown', 'byUseCase', 'No use case usage recorded.'),
      simpleTable('Agent breakdown', 'byAgent', 'No agent usage recorded.')
    )
  );
}

function langfuseContent(langfuse) {
  const totals = langfuse?.totals || {};
  const rows = (langfuse?.byModel || []).map((row) => h('tr', { key: row.model },
    h('td', null, row.model), h('td', null, formatConsumptionNumber(row.observations)),
    h('td', null, formatConsumptionNumber(row.inputTokens)), h('td', null, formatConsumptionNumber(row.outputTokens)),
    h('td', null, formatConsumptionNumber(row.totalTokens)), h('td', null, formatConsumptionCost(row.totalCostUsd))
  ));
  return h('section', { className: 'llm-langfuse-report', 'aria-labelledby': 'llm-langfuse-heading' },
    h('h4', { id: 'llm-langfuse-heading' }, 'Langfuse comparison'),
    langfuse?.status === 'available' ? summary('Langfuse totals', [
      ['Observations', formatConsumptionNumber(totals.observations)], ['Input tokens', formatConsumptionNumber(totals.inputTokens)],
      ['Output tokens', formatConsumptionNumber(totals.outputTokens)], ['Total tokens', formatConsumptionNumber(totals.totalTokens)],
      ['Total cost', formatConsumptionCost(totals.totalCostUsd)]
    ]) : h('p', { className: 'inline-status llm-empty-state' }, 'Langfuse comparison totals are not available.'),
    scrollTable('Langfuse model breakdown', ['Model', 'Observations', 'Input tokens', 'Output tokens', 'Total tokens', 'Cost'], rows, 'No Langfuse model usage returned.')
  );
}

function reconciliationContent(reconciliation = {}) {
  const values = [];
  if (reconciliation.tokenDifference !== null && reconciliation.tokenDifference !== undefined) {
    values.push(['Token difference', formatConsumptionNumber(reconciliation.tokenDifference)]);
  }
  if (reconciliation.costDifferenceUsd !== null && reconciliation.costDifferenceUsd !== undefined) {
    values.push(['Cost difference', formatConsumptionCost(reconciliation.costDifferenceUsd)]);
  }
  return h('aside', { className: 'llm-reconciliation-callout', 'aria-label': 'Source reconciliation' },
    h('strong', null, `Reconciliation: ${humanizeConsumptionStatus(reconciliation.status)}`),
    values.length ? h('dl', { className: 'llm-reconciliation-values' }, ...values.map(([label, value]) =>
      h('div', { key: label }, h('dt', null, label), h('dd', null, value))
    )) : h('p', null, 'Comparable differences are unavailable.'),
    h('p', null, 'The Symgov ledger remains authoritative. Langfuse export is asynchronous, so temporary differences are informational.')
  );
}

export function LlmConsumptionControls({ period, anchor, onPeriodChange, onAnchorChange, onRefresh }) {
  return h('div', { className: 'llm-consumption-control-group' },
    h('div', { className: 'llm-consumption-controls' },
      h('label', { className: 'field compact-field' }, h('span', null, 'Period'),
        h('select', { 'aria-label': 'Consumption period', value: period, onChange: (event) => onPeriodChange(event.target.value) },
          h('option', { value: 'day' }, 'Today'), h('option', { value: 'week' }, 'This week'),
          h('option', { value: 'month' }, 'This month'), h('option', { value: 'mtd' }, 'Month to date')
        )
      ),
      h('label', { className: 'field compact-field' }, h('span', null, 'Anchor date (UTC)'),
        h('input', { type: 'date', value: anchor, onChange: (event) => onAnchorChange(event.target.value) })
      ),
      h('button', { type: 'button', className: 'action-button secondary compact', onClick: onRefresh }, 'Refresh')
    ),
    h('p', { className: 'muted-text llm-consumption-controls-help' },
      'The report covers the selected period containing the anchor date in UTC. The report updates automatically when the period or anchor changes. Refresh requests the latest data again without changing the selection.'
    )
  );
}

export default function LlmConsumptionReport({ state = {}, announcement }) {
  const message = announcement || consumptionAnnouncement(state);
  const usage = state.usage;
  return h('div', { className: 'llm-consumption-results', 'aria-busy': state.loading ? 'true' : 'false' },
    h('p', { className: 'inline-status llm-consumption-live', role: 'status', 'aria-live': 'polite', 'aria-atomic': 'true' }, message),
    state.error ? h('p', { className: 'inline-status error', role: 'alert' }, `Consumption data could not be loaded. ${state.error}`) : null,
    !state.loading && usage ? h(React.Fragment, null,
      h('p', { className: 'llm-consumption-range' }, 'UTC range containing the selected anchor: ', h('time', null, usage.startUtc), ' until ', h('time', null, usage.endUtcExclusive), ' (exclusive).'),
      h('dl', { className: 'llm-source-status', 'aria-label': 'Consumption source status' },
        sourceStatus('Symgov ledger', usage.ledger, true), sourceStatus('Langfuse export/query', usage.langfuse)
      ),
      reconciliationContent(usage.reconciliation), ledgerContent(usage.ledger), langfuseContent(usage.langfuse),
      (usage.warnings || []).length ? h('section', { className: 'llm-warning-group', 'aria-labelledby': 'llm-warnings-heading' },
        h('h4', { id: 'llm-warnings-heading' }, 'Report warnings'),
        h('ul', { className: 'llm-consumption-warnings' }, ...(usage.warnings || []).map((warning) => h('li', { key: warning }, warning)))
      ) : null
    ) : null
  );
}
