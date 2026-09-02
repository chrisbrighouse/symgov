import { createElement, useCallback, useEffect, useState } from 'react';

import { canMountEffectivePalette } from './projectContext.js';
import { fetchEffectivePalette, fetchSymbolContext } from './api.js';

const DEFAULT_API = {
  getContext: fetchSymbolContext,
  getPalette: fetchEffectivePalette,
};

const EMPTY_PALETTE = { activeSet: null, reason: 'none', items: [], page: 1, pageSize: 50, total: 0 };

function totalPages(total, pageSize) {
  return Math.max(1, Math.ceil(Number(total || 0) / Math.max(1, Number(pageSize || 1))));
}

function facetCounts(items, field) {
  const counts = {};
  for (const item of items) {
    const key = item[field] || 'Unspecified';
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

function groupItems(items) {
  const groups = new Map();
  for (const item of items) {
    const key = item.groupName || 'Ungrouped';
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(item);
  }
  return Array.from(groups.entries());
}

function SourceBadge({ source }) {
  const label = source === 'organization_wide' ? 'Organization-wide' : 'Set';
  const modifier = source === 'organization_wide' ? 'organization-wide' : 'set-item';
  return createElement('span', { className: `symbol-set-builder-badge ${modifier}` }, label);
}

function reasonLabel(reason) {
  if (reason === 'explicit') return 'explicit Set Code override';
  if (reason === 'user_preference') return 'your saved Symbol Set preference';
  if (reason === 'project_default') return 'the Project default Symbol Set';
  if (reason === 'organization_default') return 'the Organization default Symbol Set';
  return 'no active Symbol Set';
}

export function EffectivePalettePanel({ auth, api = DEFAULT_API }) {
  const canMount = canMountEffectivePalette(auth);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [projectId, setProjectId] = useState('');
  const [page, setPage] = useState(1);
  const [palette, setPalette] = useState(EMPTY_PALETTE);

  const load = useCallback(async (pageToLoad = 1) => {
    if (!canMount) return;
    setBusy(true);
    setError('');
    try {
      const context = await api.getContext();
      const nextProjectId = context?.selectedProject?.id || '';
      setProjectId(nextProjectId);
      if (!nextProjectId) {
        setPalette(EMPTY_PALETTE);
        return;
      }
      const nextPalette = await api.getPalette(nextProjectId, { page: pageToLoad, pageSize: 50 });
      setPalette(nextPalette || EMPTY_PALETTE);
      setPage(pageToLoad);
    } catch (err) {
      setError(err.message || 'Effective palette could not be loaded.');
      setPalette(EMPTY_PALETTE);
    } finally {
      setBusy(false);
    }
  }, [api, canMount]);

  useEffect(() => {
    load(1);
  }, [load]);

  if (!canMount) return null;

  const pages = totalPages(palette.total, palette.pageSize);
  const categoryCounts = facetCounts(palette.items, 'category');
  const disciplineCounts = facetCounts(palette.items, 'discipline');
  const formatCounts = facetCounts(palette.items.filter((item) => item.preferredFormat), 'preferredFormat');

  return createElement(
    'section',
    { className: 'effective-palette-panel', 'aria-labelledby': 'effective-palette-heading' },
    createElement('div', { className: 'project-context-title-row' },
      createElement('h2', { id: 'effective-palette-heading' }, 'Effective palette'),
      createElement('button', {
        type: 'button',
        className: 'project-context-refresh',
        disabled: busy,
        onClick: () => load(page),
        'aria-label': 'Refresh effective palette',
      }, busy ? 'Refreshing…' : 'Refresh'),
    ),
    error ? createElement('p', { role: 'alert', className: 'project-context-alert' }, error) : null,
    !error && !projectId
      ? createElement('p', { role: 'status', className: 'project-context-status' }, 'Select a Project above to see its effective palette.')
      : null,
    !error && projectId
      ? createElement('p', { role: 'status', className: 'project-context-status' }, `Using ${reasonLabel(palette.reason)}${palette.activeSet ? ` (${palette.activeSet.code})` : ''}.`)
      : null,
    !error && projectId && palette.items.length === 0
      ? createElement('p', { role: 'status' }, 'No symbols are currently in the effective palette for this Project.')
      : null,
    palette.items.length > 0
      ? createElement('p', { className: 'set-admin-muted', role: 'status' },
        `${palette.total} symbol(s) · Categories: ${Object.entries(categoryCounts).map(([key, count]) => `${key} (${count})`).join(', ')}`
        + ` · Disciplines: ${Object.entries(disciplineCounts).map(([key, count]) => `${key} (${count})`).join(', ')}`
        + (Object.keys(formatCounts).length > 0
          ? ` · Preferred formats: ${Object.entries(formatCounts).map(([key, count]) => `${key} (${count})`).join(', ')}`
          : ''))
      : null,
    groupItems(palette.items).map(([groupName, groupedItems]) => createElement(
      'div',
      { key: groupName },
      createElement('h3', null, groupName),
      createElement('ul', { className: 'set-admin-list', 'aria-label': `Effective palette items in group ${groupName}` },
        groupedItems.map((item) => createElement('li', { key: item.governedSymbolId, className: 'set-admin-item' },
          createElement('div', null,
            createElement('strong', null, `${item.canonicalName} · ${item.category}`),
            createElement(SourceBadge, { source: item.source }),
            createElement('p', { className: 'set-admin-muted' },
              `Discipline: ${item.discipline}`
              + (item.preferredFormat ? ` · Preferred format: ${item.preferredFormat}` : '')
              + (item.displayLabel ? ` · ${item.displayLabel}` : '')),
          ),
        )),
      ),
    )),
    palette.total > 0
      ? createElement('div', { className: 'project-context-pagination', 'aria-label': 'Effective palette pagination' },
        createElement('button', {
          type: 'button',
          onClick: () => load(Math.max(1, page - 1)),
          disabled: busy || page <= 1,
          'aria-label': 'Previous effective palette page',
        }, 'Previous'),
        createElement('span', null, `Page ${palette.page || page} of ${pages}`),
        createElement('button', {
          type: 'button',
          onClick: () => load(Math.min(pages, page + 1)),
          disabled: busy || page >= pages,
          'aria-label': 'Next effective palette page',
        }, 'Next'),
      )
      : null,
  );
}
