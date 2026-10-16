import { createElement, useCallback, useEffect, useState } from 'react';

import {
  auditCsvUrl,
  fetchOrganizationAudit,
  fetchOrganizationSymbolUsage,
  fetchPlatformAudit,
  fetchPlatformSymbolUsage,
} from './api.js';

export const EVENT_LABELS = {
  symbol_viewed: 'Views',
  symbol_downloaded: 'Downloads',
  symbol_added_to_set: 'Added to a Set',
  symbol_added_to_clipboard: 'Added to the clipboard',
  symbol_api_fetched: 'API fetches',
  catalog_searched: 'Searches',
};
const EVENT_ORDER = Object.keys(EVENT_LABELS);
export const MAX_CSV_DAYS = 31;
export const WINDOWS = [7, 30, 60];

const DEFAULT_API = {
  orgUsage: fetchOrganizationSymbolUsage,
  orgAudit: fetchOrganizationAudit,
  platformUsage: fetchPlatformSymbolUsage,
  platformAudit: fetchPlatformAudit,
};

export function describeEvent(eventType) {
  return EVENT_LABELS[eventType] || eventType;
}

export function countsLine(counts = {}) {
  const parts = EVENT_ORDER.filter((type) => counts[type]).map((type) => `${counts[type]} ${describeEvent(type).toLowerCase()}`);
  return parts.length ? parts.join(' · ') : 'none';
}

// Why a CSV cannot be downloaded for these dates, or '' if it can.
export function csvRangeProblem(since, until) {
  if (!since || !until) return 'Choose both dates to export.';
  const from = Date.parse(`${since}T00:00:00Z`);
  const to = Date.parse(`${until}T00:00:00Z`);
  if (Number.isNaN(from) || Number.isNaN(to)) return 'Choose both dates to export.';
  if (to < from) return 'The end date is before the start date.';
  if ((to - from) / 86400000 + 1 > MAX_CSV_DAYS) return `An export covers at most ${MAX_CSV_DAYS} days.`;
  return '';
}

export function auditFilters(form, page) {
  return {
    entityType: form.entityType || undefined,
    action: form.action.trim() || undefined,
    since: form.since || undefined,
    until: form.until || undefined,
    page,
    pageSize: 50,
  };
}

function message(error, fallback) {
  return typeof error?.message === 'string' && error.message ? error.message : fallback;
}

function Alert({ text }) {
  return text ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, text) : null;
}

function Table({ caption, columns, rows, empty }) {
  if (!rows.length) return createElement('p', { className: 'set-admin-muted', role: 'status' }, empty);
  return createElement('div', { className: 'admin-users-table-shell' },
    createElement('table', { className: 'admin-users-table' },
      createElement('caption', { className: 'sr-only' }, caption),
      createElement('thead', null, createElement('tr', null, columns.map((column) => createElement('th', { key: column, scope: 'col' }, column)))),
      createElement('tbody', null, rows.map((row, index) => createElement('tr', { key: index }, row.map((cell, cellIndex) => createElement('td', { key: cellIndex }, cell)))))));
}

function WindowPicker({ days, onChange, idPrefix }) {
  return createElement('label', { htmlFor: `${idPrefix}-window` }, 'Period ',
    createElement('select', { id: `${idPrefix}-window`, value: days, onChange: (event) => onChange(Number(event.target.value)) },
      WINDOWS.map((value) => createElement('option', { key: value, value }, `Last ${value} days`))));
}

function Totals({ totals }) {
  return createElement('ul', { className: 'set-admin-list' },
    EVENT_ORDER.map((type) => createElement('li', { key: type, className: 'set-admin-item' },
      createElement('strong', null, describeEvent(type)), createElement('span', null, String(totals?.[type] || 0)))));
}

export function OrganizationUsagePanel({ api = DEFAULT_API }) {
  const [days, setDays] = useState(30);
  const [state, setState] = useState({ loading: true, error: '', data: null });
  useEffect(() => {
    let current = true;
    setState((value) => ({ ...value, loading: true, error: '' }));
    api.orgUsage({ days }).then(
      (data) => { if (current) setState({ loading: false, error: '', data }); },
      (error) => { if (current) setState({ loading: false, error: message(error, 'Usage could not be loaded.'), data: null }); },
    );
    return () => { current = false; };
  }, [api, days]);
  const { data } = state;
  return createElement('section', { className: 'glass-panel pane organization-admin-section', 'aria-labelledby': 'organization-usage-heading' },
    createElement('h2', { id: 'organization-usage-heading' }, 'Symbol usage'),
    createElement('p', { className: 'title-support' },
      'What your organization does with symbols: its own signed-in sessions and its own organization API keys. People are named. Raw events are kept for 60 days; daily totals are kept after that.'),
    createElement(WindowPicker, { days, onChange: setDays, idPrefix: 'organization-usage' }),
    state.loading ? createElement('p', { role: 'status' }, 'Loading usage…') : null,
    createElement(Alert, { text: state.error }),
    data ? createElement('div', null,
      createElement(Totals, { totals: data.totals }),
      createElement('h3', null, 'Most used symbols'),
      createElement(Table, {
        caption: 'Most used symbols', columns: ['Symbol', 'Name', 'Activity'],
        rows: data.topSymbols.map((item) => [item.displayId || '—', item.name || '—', countsLine(item.counts)]),
        empty: 'No symbol activity in this period.',
      }),
      createElement('h3', null, 'People'),
      createElement(Table, {
        caption: 'Activity by person', columns: ['Person', 'Email', 'Activity'],
        rows: data.users.map((item) => [item.displayName || '—', item.email || '—', countsLine(item.counts)]),
        empty: 'No activity by named people in this period.',
      }),
      createElement('h3', null, 'Searches that found nothing'),
      createElement(Table, {
        caption: 'Searches with no results', columns: ['Search', 'Times'],
        rows: data.zeroResultSearches.map((item) => [item.query, String(item.searches)]),
        empty: 'Every search found something.',
      }),
      createElement('h3', null, 'Projects and Symbol Sets'),
      createElement(Table, {
        caption: 'Activity by Project and Set', columns: ['Kind', 'Code', 'Name', 'Events'],
        rows: [
          ...data.projects.map((item) => ['Project', item.code || '—', item.name || '—', String(item.events)]),
          ...data.symbolSets.map((item) => ['Symbol Set', item.code || '—', item.name || '—', String(item.events)]),
        ],
        empty: 'No activity tied to a Project or Symbol Set.',
      }),
      createElement('h3', null, 'Day by day'),
      createElement(Table, {
        caption: 'Daily totals', columns: ['Day', 'Activity'],
        rows: data.trend.map((item) => [item.date, countsLine(item.counts)]),
        empty: 'No daily totals yet. They appear after the next hourly roll-up.',
      })) : null);
}

function AuditPanel({ scope, api, headingId, title, intro }) {
  const blank = { entityType: '', action: '', since: '', until: '' };
  const [form, setForm] = useState(blank);
  const [applied, setApplied] = useState(blank);
  const [page, setPage] = useState(1);
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const load = scope === 'platform' ? api.platformAudit : api.orgAudit;
  useEffect(() => {
    let current = true;
    setState((value) => ({ ...value, loading: true, error: '' }));
    load(auditFilters(applied, page)).then(
      (data) => { if (current) setState({ loading: false, error: '', data }); },
      (error) => { if (current) setState({ loading: false, error: message(error, 'The audit trail could not be loaded.'), data: null }); },
    );
    return () => { current = false; };
  }, [load, applied, page]);
  const { data } = state;
  const totalPages = data ? Math.max(1, Math.ceil(data.total / data.pageSize)) : 1;
  const csvProblem = csvRangeProblem(applied.since, applied.until);
  const csvHref = csvProblem ? null : auditCsvUrl(scope, {
    since: applied.since, until: applied.until, entityType: applied.entityType || undefined, action: applied.action.trim() || undefined,
  });
  const submit = (event) => { event.preventDefault(); setPage(1); setApplied({ ...form }); };
  return createElement('section', { className: 'glass-panel pane organization-admin-section', 'aria-labelledby': headingId },
    createElement('h2', { id: headingId }, title),
    createElement('p', { className: 'title-support' }, intro),
    createElement('form', { className: 'set-admin-form', onSubmit: submit, 'aria-label': 'Filter the audit trail' },
      createElement('label', { htmlFor: `${headingId}-type` }, 'What it was about',
        createElement('select', { id: `${headingId}-type`, value: form.entityType, onChange: (event) => setForm({ ...form, entityType: event.target.value }) },
          createElement('option', { value: '' }, 'Everything'),
          (data?.entityTypes || []).map((type) => createElement('option', { key: type, value: type }, type.replace(/_/g, ' '))))),
      createElement('label', { htmlFor: `${headingId}-action` }, 'Action',
        createElement('input', { id: `${headingId}-action`, type: 'text', value: form.action, placeholder: 'membership.deactivated', onChange: (event) => setForm({ ...form, action: event.target.value }) })),
      createElement('label', { htmlFor: `${headingId}-since` }, 'From',
        createElement('input', { id: `${headingId}-since`, type: 'date', value: form.since, onChange: (event) => setForm({ ...form, since: event.target.value }) })),
      createElement('label', { htmlFor: `${headingId}-until` }, 'To',
        createElement('input', { id: `${headingId}-until`, type: 'date', value: form.until, onChange: (event) => setForm({ ...form, until: event.target.value }) })),
      createElement('button', { type: 'submit', className: 'action-button' }, 'Apply filters')),
    createElement('p', { className: 'set-admin-muted' },
      csvHref
        ? createElement('a', { href: csvHref, download: true }, 'Download this range as CSV')
        : `CSV export: ${csvProblem}`),
    state.loading ? createElement('p', { role: 'status' }, 'Loading…') : null,
    createElement(Alert, { text: state.error }),
    data ? createElement(Table, {
      caption: 'Audit events', columns: ['When', 'About', 'Action', 'By', 'Details'],
      rows: data.items.map((item) => [
        item.occurredAt ? item.occurredAt.replace('T', ' ').slice(0, 16) + ' UTC' : '—',
        `${item.entityType.replace(/_/g, ' ')} ${item.entityId.slice(0, 8)}`,
        item.action,
        item.actorName || item.actorEmail || 'System',
        Object.entries(item.details || {}).map(([key, value]) => `${key}: ${typeof value === 'object' ? JSON.stringify(value) : value}`).join('; ') || '—',
      ]),
      empty: 'No events match.',
    }) : null,
    data && data.total > data.pageSize ? createElement('div', { className: 'action-stack horizontal' },
      createElement('button', { type: 'button', className: 'ghost-button', disabled: page <= 1, onClick: () => setPage(page - 1) }, 'Previous'),
      createElement('span', { className: 'muted-text' }, `Page ${page} of ${totalPages}`),
      createElement('button', { type: 'button', className: 'ghost-button', disabled: page >= totalPages, onClick: () => setPage(page + 1) }, 'Next')) : null);
}

export function OrganizationAuditPanel({ api = DEFAULT_API }) {
  return createElement(AuditPanel, {
    scope: 'organization', api, headingId: 'organization-audit-heading', title: 'Audit trail',
    intro: 'Changes made to your organization: members, roles, Projects, Symbol Sets, symbols you own and API keys.',
  });
}

export function PlatformAuditPanel({ api = DEFAULT_API }) {
  return createElement(AuditPanel, {
    scope: 'platform', api, headingId: 'platform-audit-heading', title: 'Platform audit trail',
    intro: 'Every recorded change across the platform, including kinds an organization never sees.',
  });
}

export function PlatformUsagePanel({ api = DEFAULT_API }) {
  const [days, setDays] = useState(30);
  const [state, setState] = useState({ loading: true, error: '', data: null });
  const load = useCallback(() => api.platformUsage({ days }), [api, days]);
  useEffect(() => {
    let current = true;
    setState((value) => ({ ...value, loading: true, error: '' }));
    load().then(
      (data) => { if (current) setState({ loading: false, error: '', data }); },
      (error) => { if (current) setState({ loading: false, error: message(error, 'Usage could not be loaded.'), data: null }); },
    );
    return () => { current = false; };
  }, [load]);
  const { data } = state;
  return createElement('section', { className: 'glass-panel pane platform-admin-embedded', 'aria-labelledby': 'platform-usage-heading' },
    createElement('h2', { id: 'platform-usage-heading' }, 'Symbol usage'),
    createElement('p', { className: 'title-support' }, 'Totals only: no people and no search text. Each organization’s own admin sees its named detail.'),
    createElement(WindowPicker, { days, onChange: setDays, idPrefix: 'platform-usage' }),
    state.loading ? createElement('p', { role: 'status' }, 'Loading usage…') : null,
    createElement(Alert, { text: state.error }),
    data ? createElement('div', null,
      createElement('h3', null, 'By organization'),
      createElement(Table, {
        caption: 'Usage by organization', columns: ['Organization', 'Total', 'Activity'],
        rows: data.organizations.items.map((item) => [item.displayName || item.code || '—', String(item.total), countsLine(item.counts)]),
        empty: 'No activity in this period.',
      }),
      createElement('h3', null, 'By pack'),
      createElement(Table, {
        caption: 'Usage by pack', columns: ['Pack', 'Total', 'Activity'],
        rows: data.packs.items.map((item) => [item.packCode, String(item.total), countsLine(item.counts)]),
        empty: 'No pack activity in this period.',
      })) : null);
}
