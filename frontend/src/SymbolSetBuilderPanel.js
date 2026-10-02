import { createElement, useCallback, useEffect, useMemo, useState } from 'react';

import {
  listOrganizationSymbolSets,
  listSymbolSetItems,
  loadSymbolSetBuilderClipboard,
  replaceSymbolSetItems,
  searchSymbolSetBuilder,
} from './api.js';
import { symbolIdLabelOrState } from './catalogWorkbench.js';

// The maximum the items route accepts (routes/symbol_sets.py:21, le=200).
export const ITEMS_PAGE_SIZE = 200;

// The most items a set may hold (schemas.py SymbolSetItemsRequest,
// max_length=1000). The server rejects a larger save; the Builder warns
// before an add would go past it.
export const SYMBOL_SET_ITEM_LIMIT = 1000;

const DEFAULT_API = {
  listSymbolSets: listOrganizationSymbolSets,
  listItems: listSymbolSetItems,
  replaceItems: replaceSymbolSetItems,
  search: searchSymbolSetBuilder,
  loadClipboard: loadSymbolSetBuilderClipboard,
};

// "1. Find symbols" has two views: a search, and the session's Catalog clipboard.
const FIND_VIEWS = [
  { key: 'search', label: 'Search' },
  { key: 'clipboard', label: 'Clipboard' },
];
const FIND_PANEL_ID = 'symbol-set-builder-find-panel';

function findTabId(key) {
  return `symbol-set-builder-find-tab-${key}`;
}

const EMPTY_CLIPBOARD = { loading: false, loaded: false, error: '', items: [], unavailable: [], total: 0 };

function StatusMessage({ status }) {
  if (!status?.message) return null;
  return createElement('p', { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode || 'info'}` }, status.message);
}

function SourceBadge({ source, organizationWide }) {
  const label = source === 'public' ? 'Public' : organizationWide ? 'Organization-wide' : 'Organization';
  const modifier = source === 'public' ? 'public' : organizationWide ? 'organization-wide' : 'organization';
  return createElement('span', { className: `symbol-set-builder-badge ${modifier}` }, label);
}

function toInput(item) {
  return {
    governedSymbolId: item.governedSymbolId,
    sortOrder: item.sortOrder,
    groupName: item.groupName || null,
    displayLabel: item.displayLabel || null,
    notes: item.notes || null,
    preferredFormat: item.preferredFormat || null,
    provenance: item.provenance || {},
  };
}

function symbolDisplayId(item) {
  return symbolIdLabelOrState(item);
}

function reindexed(items) {
  return items.map((item, index) => ({ ...item, sortOrder: index }));
}

function facetCounts(items, field) {
  const counts = {};
  for (const item of items) {
    const key = item[field] || 'Unspecified';
    counts[key] = (counts[key] || 0) + 1;
  }
  return counts;
}

export function SymbolSetBuilderPanel({ isAdmin, api = DEFAULT_API }) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [saving, setSaving] = useState(false);

  const [sets, setSets] = useState([]);
  const [selectedSetId, setSelectedSetId] = useState('');
  const [itemsLoading, setItemsLoading] = useState(false);
  const [savedItems, setSavedItems] = useState([]);
  const [savedEtag, setSavedEtag] = useState('');
  const [items, setItems] = useState([]);

  const [query, setQuery] = useState('');
  const [categoryFilter, setCategoryFilter] = useState('');
  const [disciplineFilter, setDisciplineFilter] = useState('');
  const [formatFilter, setFormatFilter] = useState('');
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [selectedSearchIds, setSelectedSearchIds] = useState({});
  const [findView, setFindView] = useState('search');
  const [clipboard, setClipboard] = useState(EMPTY_CLIPBOARD);
  // Kept apart from the search selection, so ticks in the hidden view are
  // never added unseen.
  const [selectedClipboardIds, setSelectedClipboardIds] = useState({});
  const [limitWarning, setLimitWarning] = useState('');

  const refreshSets = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const next = await api.listSymbolSets({ page: 1, pageSize: 200, status: 'active' });
      const activeSets = (next?.items || []).filter((setRow) => setRow.status === 'active');
      setSets(activeSets);
      setSelectedSetId((current) => (current && activeSets.some((setRow) => setRow.id === current)) ? current : (activeSets[0]?.id || ''));
    } catch (err) {
      setError(err.message || 'Symbol Sets unavailable.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => {
    refreshSets();
  }, [refreshSets]);

  const loadItems = useCallback(async (setId) => {
    if (!setId) {
      setSavedItems([]);
      setSavedEtag('');
      setItems([]);
      return;
    }
    setItemsLoading(true);
    setStatus({ mode: '', message: '' });
    try {
      // Page through at the route's own maximum (pageSize is capped at 200 by
      // routes/symbol_sets.py:21 -- asking for more is a 422, which used to
      // leave the panel with no ETag and every save rejected with 428).
      // Every item must be loaded before saving: PUT replaces the whole list,
      // so saving a partial load would delete the items that were never read.
      const loaded = [];
      let etag = '';
      let page = 1;
      for (;;) {
        const next = await api.listItems(setId, { page, pageSize: ITEMS_PAGE_SIZE });
        const batch = (next?.items || []).map((item) => ({ ...item }));
        loaded.push(...batch);
        etag = next?.etag || '';
        const total = Number(next?.total || 0);
        if (batch.length === 0 || loaded.length >= total) break;
        page += 1;
      }
      setSavedItems(loaded);
      setSavedEtag(etag);
      setItems(loaded);
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Symbol Set items unavailable.' });
    } finally {
      setItemsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadItems(selectedSetId);
  }, [selectedSetId, loadItems]);

  const dirty = useMemo(() => JSON.stringify(items) !== JSON.stringify(savedItems), [items, savedItems]);

  async function runSearch(event) {
    event?.preventDefault?.();
    setSearchLoading(true);
    setSearchError('');
    try {
      const next = await api.search({
        q: query.trim(),
        category: categoryFilter.trim(),
        discipline: disciplineFilter.trim(),
        format: formatFilter.trim(),
        page: 1,
        pageSize: 100,
      });
      setSearchResults(next?.items || []);
    } catch (err) {
      setSearchError(err.message || 'Symbol Set Builder search failed.');
    } finally {
      setSearchLoading(false);
    }
  }

  function toggleSearchSelection(governedSymbolId) {
    setSelectedSearchIds((current) => ({ ...current, [governedSymbolId]: !current[governedSymbolId] }));
  }

  function toggleClipboardSelection(governedSymbolId) {
    setSelectedClipboardIds((current) => ({ ...current, [governedSymbolId]: !current[governedSymbolId] }));
  }

  // Read-only: the Catalog page owns the clipboard and saves it whole, so the
  // Builder never changes it.
  async function loadClipboard() {
    setClipboard((current) => ({ ...current, loading: true, error: '' }));
    try {
      const next = await api.loadClipboard();
      setClipboard({
        loading: false,
        loaded: true,
        error: '',
        items: next?.items || [],
        unavailable: next?.unavailable || [],
        total: Number(next?.total || 0),
      });
      setSelectedClipboardIds({});
    } catch (err) {
      setClipboard((current) => ({ ...current, loading: false, error: err.message || 'Your clipboard could not be loaded.' }));
    }
  }

  function selectFindView(key) {
    setFindView(key);
    setLimitWarning('');
    if (key === 'clipboard' && !clipboard.loaded && !clipboard.loading) {
      loadClipboard();
    }
  }

  // Roving tabindex, as `role="tablist"` promises: arrow keys move between
  // the views and Tab leaves the group.
  function onFindTabKeyDown(event) {
    const index = FIND_VIEWS.findIndex((view) => view.key === findView);
    let next = null;
    if (event.key === 'ArrowRight') next = (index + 1) % FIND_VIEWS.length;
    if (event.key === 'ArrowLeft') next = (index - 1 + FIND_VIEWS.length) % FIND_VIEWS.length;
    if (event.key === 'Home') next = 0;
    if (event.key === 'End') next = FIND_VIEWS.length - 1;
    if (next === null) return;
    event.preventDefault();
    const target = FIND_VIEWS[next].key;
    selectFindView(target);
    globalThis.document?.getElementById(findTabId(target))?.focus();
  }

  const presentIds = useMemo(() => new Set(items.map((item) => item.governedSymbolId)), [items]);

  function isAddable(entry) {
    return (entry.source === 'public' || entry.source === 'organization') && !presentIds.has(entry.governedSymbolId);
  }

  // Adds nothing, and says why, when the set would go past its limit.
  function addEntriesToSet(entries) {
    const toAdd = entries.filter(isAddable);
    if (toAdd.length === 0) return false;
    const room = Math.max(0, SYMBOL_SET_ITEM_LIMIT - items.length);
    if (toAdd.length > room) {
      setLimitWarning(
        `A Symbol Set can hold at most ${SYMBOL_SET_ITEM_LIMIT.toLocaleString('en-GB')} symbols. `
        + `This set has ${items.length.toLocaleString('en-GB')}, so ${room.toLocaleString('en-GB')} more can be added, `
        + `and you tried to add ${toAdd.length.toLocaleString('en-GB')}. Nothing was added; choose fewer symbols.`,
      );
      return false;
    }
    setLimitWarning('');
    setItems((current) => reindexed([
      ...current,
      ...toAdd.map((entry) => ({
        governedSymbolId: entry.governedSymbolId,
        sortOrder: 0,
        groupName: null,
        displayLabel: null,
        notes: null,
        preferredFormat: null,
        provenance: {},
        catalogSymbolId: entry.catalogSymbolId || null,
        displayId: entry.displayId || entry.catalogSymbolId || null,
        canonicalName: entry.canonicalName,
        category: entry.category,
        discipline: entry.discipline,
        slug: entry.slug,
        source: entry.source,
        organizationWide: entry.organizationWide,
        availabilityStatus: 'active',
      })),
    ]));
    setStatus({ mode: '', message: '' });
    return true;
  }

  function addSelectedToSet() {
    if (addEntriesToSet(searchResults.filter((entry) => selectedSearchIds[entry.governedSymbolId]))) {
      setSelectedSearchIds({});
    }
  }

  function addSelectedClipboardToSet() {
    if (addEntriesToSet(clipboard.items.filter((entry) => selectedClipboardIds[entry.governedSymbolId]))) {
      setSelectedClipboardIds({});
    }
  }

  function addAllClipboardToSet() {
    if (addEntriesToSet(clipboard.items)) {
      setSelectedClipboardIds({});
    }
  }

  function removeItem(governedSymbolId) {
    setItems((current) => reindexed(current.filter((item) => item.governedSymbolId !== governedSymbolId)));
  }

  function moveItem(governedSymbolId, direction) {
    setItems((current) => {
      const index = current.findIndex((item) => item.governedSymbolId === governedSymbolId);
      const target = index + direction;
      if (index < 0 || target < 0 || target >= current.length) return current;
      const next = current.slice();
      const [moved] = next.splice(index, 1);
      next.splice(target, 0, moved);
      return reindexed(next);
    });
  }

  function handleDragStart(event, governedSymbolId) {
    event.dataTransfer.setData('text/plain', governedSymbolId);
    event.dataTransfer.effectAllowed = 'move';
  }

  function handleDrop(event, targetGovernedSymbolId) {
    event.preventDefault();
    const draggedId = event.dataTransfer.getData('text/plain');
    if (!draggedId || draggedId === targetGovernedSymbolId) return;
    setItems((current) => {
      const from = current.findIndex((item) => item.governedSymbolId === draggedId);
      const to = current.findIndex((item) => item.governedSymbolId === targetGovernedSymbolId);
      if (from < 0 || to < 0) return current;
      const next = current.slice();
      const [moved] = next.splice(from, 1);
      next.splice(to, 0, moved);
      return reindexed(next);
    });
  }

  function updateField(governedSymbolId, field, value) {
    setItems((current) => current.map((item) => (
      item.governedSymbolId === governedSymbolId ? { ...item, [field]: value } : item
    )));
  }

  async function saveChanges() {
    if (!selectedSetId) return;
    setSaving(true);
    setStatus({ mode: '', message: '' });
    try {
      await api.replaceItems(selectedSetId, items.map(toInput), savedEtag);
      setStatus({ mode: 'success', message: 'Symbol Set items saved.' });
      await loadItems(selectedSetId);
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Symbol Set items save failed.' });
      if (err.status === 409 || err.status === 428) {
        await loadItems(selectedSetId);
      }
    } finally {
      setSaving(false);
    }
  }

  function discardChanges() {
    setItems(savedItems);
    setStatus({ mode: '', message: '' });
  }

  if (!isAdmin) {
    return createElement(
      'section',
      { className: 'symbol-set-builder-panel', 'aria-labelledby': 'symbol-set-builder-heading' },
      createElement('h2', { id: 'symbol-set-builder-heading' }, 'Symbol Set Builder'),
      createElement('p', { role: 'status' }, 'Organization Admin privileges are required to build Symbol Sets.'),
    );
  }

  const categoryCounts = facetCounts(items, 'category');
  const disciplineCounts = facetCounts(items, 'discipline');
  const formatCounts = facetCounts(items.filter((item) => item.preferredFormat), 'preferredFormat');
  const selectedCount = Object.values(selectedSearchIds).filter(Boolean).length;
  const countLabel = (counts) => Object.entries(counts).map(([key, count]) => `${key} (${count})`).join(', ');

  const filterField = (id, label, value, setter) => createElement('label', { className: 'field', htmlFor: id },
    createElement('span', null, label),
    createElement('input', { id, value, onChange: (event) => setter(event.target.value) }),
  );

  // One result row, shared by the search and clipboard views.
  const resultRow = (entry, selectedIds, onToggle) => {
    const alreadyPresent = presentIds.has(entry.governedSymbolId);
    const addable = isAddable(entry);
    return createElement('li', { key: entry.governedSymbolId, className: `symbol-set-builder-result${addable ? '' : ' muted'}` },
      createElement('label', { className: 'symbol-set-builder-result-label' },
        createElement('input', {
          type: 'checkbox',
          disabled: !addable,
          checked: addable && Boolean(selectedIds[entry.governedSymbolId]),
          onChange: () => onToggle(entry.governedSymbolId),
          'aria-label': `Select ${entry.canonicalName}`,
        }),
        createElement('span', { className: 'symbol-set-builder-result-text' },
          createElement('span', { className: 'symbol-set-builder-name' },
            createElement('strong', null, `${entry.canonicalName} · ${symbolDisplayId(entry)}`),
            createElement(SourceBadge, { source: entry.source, organizationWide: entry.organizationWide }),
          ),
          createElement('span', { className: 'set-admin-muted' },
            `Category: ${entry.category} · Discipline: ${entry.discipline}`
            + (alreadyPresent ? ' · Already in this set' : '')
            + (entry.source === 'organization' ? ' · Approved organization symbol' : '')),
        ),
      ),
    );
  };

  const limitAlert = limitWarning
    ? createElement('p', { role: 'alert', className: 'set-admin-status error symbol-set-builder-limit' }, limitWarning)
    : null;

  const searchView = createElement(
    'div',
    { className: 'symbol-set-builder-find-view' },
    createElement('p', { className: 'set-admin-muted' }, 'Search the Public Catalog and your approved organization symbols.'),
    createElement('form', { className: 'field search-field', onSubmit: runSearch },
      createElement('input', {
        type: 'search',
        'aria-label': 'Search symbols to add to this Symbol Set',
        placeholder: 'Search by name, category, or discipline',
        value: query,
        onChange: (event) => setQuery(event.target.value),
      }),
      createElement('button', { type: 'submit', className: 'action-button primary', disabled: searchLoading }, searchLoading ? 'Searching…' : 'Search'),
    ),
    createElement('div', { className: 'symbol-set-builder-filters', role: 'group', 'aria-label': 'Symbol Set Builder filters' },
      filterField('symbol-set-builder-category-filter', 'Category', categoryFilter, setCategoryFilter),
      filterField('symbol-set-builder-discipline-filter', 'Discipline', disciplineFilter, setDisciplineFilter),
      filterField('symbol-set-builder-format-filter', 'Format', formatFilter, setFormatFilter),
    ),
    searchError ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, searchError) : null,
    !searchLoading && !searchError && searchResults.length === 0
      ? createElement('p', { role: 'status', className: 'set-admin-muted' }, 'No matching symbols found.')
      : null,
    searchResults.length > 0
      ? createElement(
        'div',
        { className: 'symbol-set-builder-results' },
        createElement('div', { className: 'symbol-set-builder-results-bar' },
          createElement('span', { className: 'set-admin-muted', role: 'status' },
            `${searchResults.length} result(s)${selectedCount > 0 ? ` · ${selectedCount} selected` : ''}`),
          createElement('button', {
            type: 'button',
            className: 'action-button primary compact',
            onClick: addSelectedToSet,
            disabled: selectedCount === 0,
            'aria-label': 'Add selected symbols to this Symbol Set',
          }, 'Add selected to set'),
        ),
        limitAlert,
        createElement('ul', { className: 'set-admin-list symbol-set-builder-result-list', 'aria-label': 'Symbol Set Builder search results' },
          searchResults.map((entry) => resultRow(entry, selectedSearchIds, toggleSearchSelection)),
        ),
      )
      : null,
  );

  const clipboardAddable = clipboard.items.filter(isAddable);
  const clipboardSelected = clipboardAddable.filter((entry) => selectedClipboardIds[entry.governedSymbolId]);
  const allClipboardSelected = clipboardAddable.length > 0 && clipboardSelected.length === clipboardAddable.length;

  function toggleAllClipboard() {
    setSelectedClipboardIds(allClipboardSelected
      ? {}
      : Object.fromEntries(clipboardAddable.map((entry) => [entry.governedSymbolId, true])));
  }

  const clipboardView = createElement(
    'div',
    { className: 'symbol-set-builder-find-view' },
    createElement('div', { className: 'symbol-set-builder-clipboard-head' },
      createElement('p', { className: 'set-admin-muted' }, 'Symbols you collected on your Catalog clipboard. Adding them here leaves your clipboard as it is.'),
      createElement('button', {
        type: 'button',
        className: 'action-button compact',
        onClick: loadClipboard,
        disabled: clipboard.loading,
        'aria-label': 'Refresh clipboard',
      }, clipboard.loading ? 'Refreshing…' : 'Refresh'),
    ),
    clipboard.loading && !clipboard.loaded ? createElement('p', { role: 'status' }, 'Loading your clipboard…') : null,
    clipboard.error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, clipboard.error) : null,
    clipboard.loaded && !clipboard.error && clipboard.total === 0
      ? createElement('p', { role: 'status', className: 'set-admin-muted' },
        'Your clipboard is empty. Add symbols to it from the ',
        createElement('a', { href: '#/standards' }, 'Catalog'),
        ', then come back here.')
      : null,
    clipboard.loaded && clipboard.total > 0
      ? createElement(
        'div',
        { className: 'symbol-set-builder-results' },
        createElement('div', { className: 'symbol-set-builder-results-bar' },
          createElement('label', { className: 'symbol-set-builder-select-all' },
            createElement('input', {
              type: 'checkbox',
              disabled: clipboardAddable.length === 0,
              checked: allClipboardSelected,
              onChange: toggleAllClipboard,
              'aria-label': 'Select all clipboard symbols that can be added',
            }),
            createElement('span', null, `Select all that can be added (${clipboardAddable.length})`),
          ),
          createElement('div', { className: 'set-admin-actions' },
            createElement('button', {
              type: 'button',
              className: 'action-button primary compact',
              onClick: addSelectedClipboardToSet,
              disabled: clipboardSelected.length === 0,
              'aria-label': 'Add selected clipboard symbols to this Symbol Set',
            }, 'Add selected to set'),
            createElement('button', {
              type: 'button',
              className: 'action-button compact',
              onClick: addAllClipboardToSet,
              disabled: clipboardAddable.length === 0,
              'aria-label': 'Add every clipboard symbol that can be added to this Symbol Set',
            }, `Add all (${clipboardAddable.length})`),
          ),
        ),
        createElement('span', { className: 'set-admin-muted', role: 'status' },
          `${clipboard.total} on clipboard · ${clipboardAddable.length} can be added`
          + (clipboardSelected.length > 0 ? ` · ${clipboardSelected.length} selected` : '')),
        limitAlert,
        createElement('ul', { className: 'set-admin-list symbol-set-builder-result-list', 'aria-label': 'Clipboard symbols' },
          clipboard.items.map((entry) => resultRow(entry, selectedClipboardIds, toggleClipboardSelection)),
          clipboard.unavailable.map((entry) => createElement('li', { key: `unavailable-${entry.slug}`, className: 'symbol-set-builder-result muted' },
            createElement('span', { className: 'symbol-set-builder-result-text' },
              createElement('span', { className: 'symbol-set-builder-name' },
                createElement('strong', null, [entry.name, entry.displayId].filter(Boolean).join(' · ') || entry.slug),
                createElement('span', { className: 'symbol-set-builder-badge unavailable' }, 'Unavailable'),
              ),
              createElement('span', { className: 'set-admin-muted' }, 'Can no longer be added to a Symbol Set.'),
            ),
          )),
        ),
      )
      : null,
  );

  const searchCard = createElement(
    'div',
    { className: 'symbol-set-builder-card symbol-set-builder-search' },
    createElement('div', { className: 'symbol-set-builder-card-head' },
      createElement('h3', null, '1. Find symbols'),
    ),
    createElement(
      'div',
      { className: 'symbol-set-builder-find-tabs', role: 'tablist', 'aria-label': 'Ways to find symbols', onKeyDown: onFindTabKeyDown },
      FIND_VIEWS.map((view) => {
        const active = view.key === findView;
        return createElement('button', {
          key: view.key,
          id: findTabId(view.key),
          type: 'button',
          role: 'tab',
          'aria-selected': active,
          'aria-controls': FIND_PANEL_ID,
          tabIndex: active ? 0 : -1,
          className: `symbol-set-builder-find-tab${active ? ' active' : ''}`,
          onClick: () => selectFindView(view.key),
        },
        view.label,
        view.key === 'clipboard' && clipboard.loaded
          ? createElement('span', { className: 'symbol-set-builder-find-tab-count' }, clipboard.total)
          : null);
      }),
    ),
    createElement('div', { id: FIND_PANEL_ID, role: 'tabpanel', 'aria-labelledby': findTabId(findView) },
      findView === 'clipboard' ? clipboardView : searchView,
    ),
  );

  const summaryBlock = items.length > 0
    ? createElement('details', { className: 'symbol-set-builder-summary' },
      createElement('summary', null, `${items.length} item(s) in this set · breakdown`),
      createElement('dl', null,
        createElement('dt', null, 'Categories'),
        createElement('dd', null, countLabel(categoryCounts)),
        createElement('dt', null, 'Disciplines'),
        createElement('dd', null, countLabel(disciplineCounts)),
        Object.keys(formatCounts).length > 0 ? createElement('dt', null, 'Preferred formats') : null,
        Object.keys(formatCounts).length > 0 ? createElement('dd', null, countLabel(formatCounts)) : null,
      ),
    )
    : null;

  const itemsCard = createElement(
    'div',
    { className: 'symbol-set-builder-card symbol-set-builder-items' },
    createElement('div', { className: 'symbol-set-builder-card-head' },
      createElement('h3', null, '2. Symbol Set items'),
      createElement('p', { className: 'set-admin-muted' }, 'Drag rows or use Move up / Move down to set the order. Changes are saved only when you press Save changes.'),
    ),
    createElement(
      'div',
      { className: `symbol-set-builder-savebar${dirty ? ' dirty' : ''}`, role: 'group', 'aria-label': 'Symbol Set changes' },
      createElement('span', { className: 'symbol-set-builder-savebar-state', role: 'status' }, dirty ? 'Unsaved changes' : 'All changes saved'),
      createElement('div', { className: 'set-admin-actions' },
        createElement('button', {
          type: 'button',
          className: 'action-button primary compact',
          disabled: !dirty || saving,
          onClick: saveChanges,
          'aria-label': 'Save Symbol Set changes',
        }, saving ? 'Saving…' : 'Save changes'),
        createElement('button', {
          type: 'button',
          className: 'action-button compact',
          disabled: !dirty || saving,
          onClick: discardChanges,
          'aria-label': 'Discard Symbol Set changes',
        }, 'Discard changes'),
      ),
    ),
    itemsLoading ? createElement('p', { role: 'status' }, 'Loading Symbol Set items…') : null,
    !itemsLoading && items.length === 0
      ? createElement('p', { role: 'status', className: 'set-admin-muted' }, 'This Symbol Set has no items yet. Add symbols from search.')
      : null,
    summaryBlock,
    createElement('ul', { className: 'set-admin-list symbol-set-builder-item-list', 'aria-label': 'Current Symbol Set items, in order' },
      items.map((item, index) => {
        const name = item.canonicalName || item.governedSymbolId;
        return createElement('li', {
          key: item.governedSymbolId,
          className: `symbol-set-builder-item${item.availabilityStatus === 'unavailable' ? ' unavailable' : ''}`,
          draggable: true,
          onDragStart: (event) => handleDragStart(event, item.governedSymbolId),
          onDragOver: (event) => event.preventDefault(),
          onDrop: (event) => handleDrop(event, item.governedSymbolId),
        },
        createElement('span', { className: 'symbol-set-builder-position', 'aria-hidden': 'true' }, index + 1),
        createElement(
          'div',
          { className: 'symbol-set-builder-item-main' },
          createElement('div', { className: 'symbol-set-builder-name' },
            createElement('strong', null, `${name} · ${symbolDisplayId(item)}`),
            createElement(SourceBadge, { source: item.source || 'public', organizationWide: item.organizationWide }),
            item.availabilityStatus === 'unavailable'
              ? createElement('span', { className: 'symbol-set-builder-badge unavailable' }, 'Unavailable')
              : null,
          ),
          createElement('p', { className: 'set-admin-muted' }, `Category: ${item.category || 'unknown'} · Discipline: ${item.discipline || 'unknown'}`),
          item.availabilityStatus === 'unavailable' && item.availabilityReason
            ? createElement('p', { className: 'set-admin-muted' }, item.availabilityReason)
            : null,
          createElement('div', { className: 'symbol-set-builder-item-fields' },
            createElement('label', { className: 'field', htmlFor: `builder-group-${item.governedSymbolId}` },
              createElement('span', null, 'Group'),
              createElement('input', {
                id: `builder-group-${item.governedSymbolId}`,
                value: item.groupName || '',
                onChange: (event) => updateField(item.governedSymbolId, 'groupName', event.target.value),
              }),
            ),
            createElement('label', { className: 'field', htmlFor: `builder-format-${item.governedSymbolId}` },
              createElement('span', null, 'Preferred format'),
              createElement('input', {
                id: `builder-format-${item.governedSymbolId}`,
                value: item.preferredFormat || '',
                onChange: (event) => updateField(item.governedSymbolId, 'preferredFormat', event.target.value),
              }),
            ),
          ),
        ),
        createElement(
          'div',
          { className: 'symbol-set-builder-item-actions', role: 'group', 'aria-label': `Reorder or remove ${name}` },
          createElement('button', {
            type: 'button',
            className: 'action-button compact',
            disabled: index === 0,
            onClick: () => moveItem(item.governedSymbolId, -1),
            'aria-label': `Move ${name} up`,
          }, 'Move up'),
          createElement('button', {
            type: 'button',
            className: 'action-button compact',
            disabled: index === items.length - 1,
            onClick: () => moveItem(item.governedSymbolId, 1),
            'aria-label': `Move ${name} down`,
          }, 'Move down'),
          createElement('button', {
            type: 'button',
            className: 'action-button compact danger',
            onClick: () => removeItem(item.governedSymbolId),
            'aria-label': `Remove ${name} from this Symbol Set`,
          }, 'Remove'),
        ),
        );
      }),
    ),
  );

  return createElement(
    'section',
    { className: 'symbol-set-builder-panel', 'aria-labelledby': 'symbol-set-builder-heading' },
    createElement('div', { className: 'symbol-set-builder-head' },
      createElement('h2', { id: 'symbol-set-builder-heading' }, 'Symbol Set Builder'),
      createElement('p', { className: 'set-admin-muted' }, 'Choose a Symbol Set, find symbols to add, then arrange and save its items.'),
    ),
    loading ? createElement('p', { role: 'status' }, 'Loading Symbol Sets…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    !loading && !error && sets.length === 0
      ? createElement('p', { role: 'status' }, 'No active Symbol Sets to build. Create one above first.')
      : null,
    StatusMessage({ status }),
    sets.length > 0
      ? createElement('label', { className: 'field symbol-set-builder-set-picker', htmlFor: 'symbol-set-builder-set-select' },
        createElement('span', null, 'Symbol Set'),
        createElement('select', {
          id: 'symbol-set-builder-set-select',
          value: selectedSetId,
          onChange: (event) => setSelectedSetId(event.target.value),
        }, sets.map((setRow) => createElement('option', { key: setRow.id, value: setRow.id }, `${setRow.code} · ${setRow.name}`))),
      )
      : null,
    selectedSetId
      ? createElement('div', { className: 'symbol-set-builder-layout' }, searchCard, itemsCard)
      : null,
  );
}
