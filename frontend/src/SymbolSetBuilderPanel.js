import { createElement, useCallback, useEffect, useMemo, useRef, useState } from 'react';

import {
  listOrganizationSymbolSets,
  listSymbolSetItems,
  replaceSymbolSetItems,
  searchSymbolSetBuilder,
} from './api.js';

const DEFAULT_API = {
  listSymbolSets: listOrganizationSymbolSets,
  listItems: listSymbolSetItems,
  replaceItems: replaceSymbolSetItems,
  search: searchSymbolSetBuilder,
};

function StatusMessage({ status, onRetry }) {
  if (!status?.message) return null;
  return createElement('div', null,
    createElement('p', { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode || 'info'}` }, status.message),
    status.mode === 'error' && onRetry
      ? createElement('button', { type: 'button', onClick: onRetry, 'aria-label': 'Retry loading Symbol Set items' }, 'Retry')
      : null,
  );
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
  return item.displayId || item.catalogSymbolId || item.slug || '';
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

function totalPages(total, pageSize) {
  return Math.max(1, Math.ceil(Number(total || 0) / Math.max(1, Number(pageSize || 1))));
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
  const [authoritativeLoadComplete, setAuthoritativeLoadComplete] = useState(false);
  const [loadedSetId, setLoadedSetId] = useState('');
  const loadGeneration = useRef(0);

  const [query, setQuery] = useState('');
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [searchPage, setSearchPage] = useState(1);
  const [searchPageSize, setSearchPageSize] = useState(100);
  const [searchTotal, setSearchTotal] = useState(0);
  const [categoryFilter, setCategoryFilter] = useState('');
  const [disciplineFilter, setDisciplineFilter] = useState('');
  const [formatFilter, setFormatFilter] = useState('');
  const [selectedSearchIds, setSelectedSearchIds] = useState({});
  const [selectedItemIds, setSelectedItemIds] = useState({});
  const [pendingRemovalIds, setPendingRemovalIds] = useState([]);
  const searchGeneration = useRef(0);

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
    const generation = loadGeneration.current + 1;
    loadGeneration.current = generation;
    setAuthoritativeLoadComplete(false);
    setLoadedSetId('');
    setSavedItems([]);
    setSavedEtag('');
    setItems([]);
    setSelectedSearchIds({});
    setSelectedItemIds({});
    setPendingRemovalIds([]);
    if (!setId) {
      setItemsLoading(false);
      return;
    }
    setItemsLoading(true);
    setStatus({ mode: '', message: '' });
    try {
      const requestedPageSize = 200;
      const loaded = [];
      const seenIds = new Set();
      let expectedTotal = null;
      let expectedEtag = null;
      let page = 1;
      while (expectedTotal === null || loaded.length < expectedTotal) {
        const next = await api.listItems(setId, { page, pageSize: requestedPageSize });
        if (generation !== loadGeneration.current) return;
        const responsePage = Number(next?.page);
        const responsePageSize = Number(next?.pageSize);
        const responseTotal = Number(next?.total);
        const responseEtag = typeof next?.etag === 'string' ? next.etag : '';
        const pageItems = Array.isArray(next?.items) ? next.items : null;
        if (
          responsePage !== page
          || !Number.isInteger(responsePageSize) || responsePageSize < 1 || responsePageSize > 200
          || !Number.isInteger(responseTotal) || responseTotal < 0
          || !responseEtag
          || pageItems === null || pageItems.length > responsePageSize
          || (expectedTotal !== null && responseTotal !== expectedTotal)
          || (expectedEtag !== null && responseEtag !== expectedEtag)
        ) {
          throw new Error('Symbol Set items load was incomplete. Editing is disabled; retry the load.');
        }
        expectedTotal = responseTotal;
        expectedEtag = responseEtag;
        for (const item of pageItems) {
          const itemId = item.id || item.governedSymbolId;
          if (seenIds.has(itemId)) {
            throw new Error('Symbol Set items load returned duplicate rows. Editing is disabled; retry the load.');
          }
          seenIds.add(itemId);
          loaded.push({ ...item });
        }
        if (loaded.length > expectedTotal || (pageItems.length === 0 && loaded.length < expectedTotal)) {
          throw new Error('Symbol Set items load was incomplete. Editing is disabled; retry the load.');
        }
        if (expectedTotal === loaded.length) break;
        page += 1;
        if (page > expectedTotal + 1) {
          throw new Error('Symbol Set items load was incomplete. Editing is disabled; retry the load.');
        }
      }
      if (generation !== loadGeneration.current || expectedTotal !== loaded.length) return;
      setSavedItems(loaded);
      setSavedEtag(expectedEtag);
      setItems(loaded);
      setLoadedSetId(setId);
      setAuthoritativeLoadComplete(true);
    } catch (err) {
      if (generation === loadGeneration.current) {
        setStatus({ mode: 'error', message: err.message || 'Symbol Set items unavailable.' });
      }
    } finally {
      if (generation === loadGeneration.current) setItemsLoading(false);
    }
  }, [api]);

  useEffect(() => {
    loadItems(selectedSetId);
  }, [selectedSetId, loadItems]);

  const dirty = useMemo(() => JSON.stringify(items) !== JSON.stringify(savedItems), [items, savedItems]);
  const editableReady = authoritativeLoadComplete && loadedSetId === selectedSetId && !itemsLoading;

  function resetSearchResults() {
    searchGeneration.current += 1;
    setSearchResults([]);
    setSelectedSearchIds({});
    setSearchPage(1);
    setSearchTotal(0);
    setSearchError('');
  }

  function changeSelectedSet(nextSetId) {
    if (nextSetId === selectedSetId) return;
    if (dirty) {
      const confirmed = typeof window !== 'undefined'
        && typeof window.confirm === 'function'
        && window.confirm('Discard unsaved Symbol Set changes and switch sets?');
      if (!confirmed) return;
    }
    setAuthoritativeLoadComplete(false);
    setLoadedSetId('');
    setSavedItems([]);
    setSavedEtag('');
    setItems([]);
    setSelectedSearchIds({});
    setSelectedItemIds({});
    setPendingRemovalIds([]);
    resetSearchResults();
    setSelectedSetId(nextSetId);
  }

  async function runSearch(event, pageToLoad = 1) {
    event?.preventDefault?.();
    const generation = searchGeneration.current + 1;
    searchGeneration.current = generation;
    setSearchLoading(true);
    setSearchError('');
    setSelectedSearchIds({});
    try {
      const next = await api.search({
        q: query.trim(),
        category: categoryFilter.trim(),
        discipline: disciplineFilter.trim(),
        format: formatFilter.trim(),
        page: pageToLoad,
        pageSize: 100,
      });
      if (generation !== searchGeneration.current) return;
      setSearchResults(Array.isArray(next?.items) ? next.items : []);
      setSearchPage(Number(next?.page || pageToLoad));
      setSearchPageSize(Number(next?.pageSize || 100));
      setSearchTotal(Number(next?.total || 0));
    } catch (err) {
      if (generation === searchGeneration.current) {
        setSearchError(err.message || 'Symbol Set Builder search failed.');
        setSearchResults([]);
        setSearchTotal(0);
      }
    } finally {
      if (generation === searchGeneration.current) setSearchLoading(false);
    }
  }

  function toggleSearchSelection(governedSymbolId) {
    if (!editableReady) return;
    setSelectedSearchIds((current) => ({ ...current, [governedSymbolId]: !current[governedSymbolId] }));
  }

  const presentIds = useMemo(() => new Set(items.map((item) => item.governedSymbolId)), [items]);

  function addSelectedToSet() {
    if (!editableReady) return;
    const selectedIds = new Set();
    const toAdd = searchResults.filter((entry) => {
      const isAddable = (entry.source === 'public' || entry.source === 'organization')
        && selectedSearchIds[entry.governedSymbolId]
        && !presentIds.has(entry.governedSymbolId)
        && !selectedIds.has(entry.governedSymbolId);
      if (isAddable) selectedIds.add(entry.governedSymbolId);
      return isAddable;
    });
    if (toAdd.length === 0) return;
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
        availabilityStatus: 'active',
      })),
    ]));
    setSelectedSearchIds({});
    setStatus({ mode: '', message: '' });
  }

  function beginRemoval(governedSymbolIds) {
    if (!editableReady) return;
    const ids = [...new Set(governedSymbolIds)].filter((id) => presentIds.has(id));
    if (ids.length > 0) setPendingRemovalIds(ids);
  }

  function toggleItemSelection(governedSymbolId) {
    if (!editableReady) return;
    setSelectedItemIds((current) => ({ ...current, [governedSymbolId]: !current[governedSymbolId] }));
  }

  function confirmRemoval() {
    if (!editableReady || pendingRemovalIds.length === 0) return;
    const ids = new Set(pendingRemovalIds);
    setItems((current) => reindexed(current.filter((item) => !ids.has(item.governedSymbolId))));
    setSelectedItemIds((current) => {
      const next = { ...current };
      for (const id of pendingRemovalIds) delete next[id];
      return next;
    });
    setPendingRemovalIds([]);
    setStatus({ mode: '', message: 'Removal staged locally. Save changes to commit it.' });
  }

  function moveItem(governedSymbolId, direction) {
    if (!editableReady) return;
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
    if (!editableReady) return;
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
    if (!editableReady) return;
    setItems((current) => current.map((item) => (
      item.governedSymbolId === governedSymbolId ? { ...item, [field]: value } : item
    )));
  }

  async function saveChanges() {
    if (!selectedSetId || !editableReady || !savedEtag) return;
    const saveSetId = selectedSetId;
    const saveGeneration = loadGeneration.current;
    setSaving(true);
    setStatus({ mode: '', message: '' });
    try {
      await api.replaceItems(saveSetId, items.map(toInput), savedEtag);
      if (saveGeneration !== loadGeneration.current || loadedSetId !== saveSetId || selectedSetId !== saveSetId) return;
      setStatus({ mode: 'success', message: 'Symbol Set items saved.' });
      await loadItems(saveSetId);
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Symbol Set items save failed.' });
    } finally {
      setSaving(false);
    }
  }

  function discardChanges() {
    if (!editableReady) return;
    setItems(savedItems);
    setSelectedItemIds({});
    setPendingRemovalIds([]);
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
  const searchPageCount = totalPages(searchTotal, searchPageSize);
  const selectedItemCount = Object.values(selectedItemIds).filter(Boolean).length;

  return createElement(
    'section',
    { className: 'symbol-set-builder-panel', 'aria-labelledby': 'symbol-set-builder-heading' },
    createElement('h2', { id: 'symbol-set-builder-heading' }, 'Symbol Set Builder'),
    loading ? createElement('p', { role: 'status' }, 'Loading Symbol Sets…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    !loading && !error && sets.length === 0
      ? createElement('p', { role: 'status' }, 'No active Symbol Sets to build. Create one above first.')
      : null,
    StatusMessage({ status, onRetry: selectedSetId ? () => loadItems(selectedSetId) : null }),
    sets.length > 0
      ? createElement('label', { htmlFor: 'symbol-set-builder-set-select' },
        'Symbol Set',
        createElement('select', {
          id: 'symbol-set-builder-set-select',
          value: selectedSetId,
          onChange: (event) => changeSelectedSet(event.target.value),
        }, sets.map((setRow) => createElement('option', { key: setRow.id, value: setRow.id }, `${setRow.code} · ${setRow.name}`))),
      )
      : null,
    selectedSetId
      ? createElement(
        'div',
        { className: 'symbol-set-builder-layout' },
        createElement(
          'div',
          { className: 'symbol-set-builder-search' },
          createElement('h3', null, 'Search Public Catalog and organization symbols'),
          createElement('form', { className: 'field search-field', onSubmit: runSearch },
            createElement('input', {
              type: 'search',
              'aria-label': 'Search symbols to add to this Symbol Set',
              placeholder: 'Search by name, category, or discipline',
              value: query,
              onChange: (event) => {
                setQuery(event.target.value);
                resetSearchResults();
              },
            }),
            createElement('button', { type: 'submit', disabled: searchLoading }, searchLoading ? 'Searching…' : 'Search'),
          ),
          createElement('div', { className: 'symbol-set-builder-filters', 'aria-label': 'Symbol Set Builder filters' },
            createElement('label', { htmlFor: 'symbol-set-builder-category-filter' },
              'Category',
              createElement('input', {
                id: 'symbol-set-builder-category-filter',
                value: categoryFilter,
                onChange: (event) => { setCategoryFilter(event.target.value); resetSearchResults(); },
              }),
            ),
            createElement('label', { htmlFor: 'symbol-set-builder-discipline-filter' },
              'Discipline',
              createElement('input', {
                id: 'symbol-set-builder-discipline-filter',
                value: disciplineFilter,
                onChange: (event) => { setDisciplineFilter(event.target.value); resetSearchResults(); },
              }),
            ),
            createElement('label', { htmlFor: 'symbol-set-builder-format-filter' },
              'Format',
              createElement('input', {
                id: 'symbol-set-builder-format-filter',
                value: formatFilter,
                onChange: (event) => { setFormatFilter(event.target.value); resetSearchResults(); },
              }),
            ),
            createElement('button', {
              type: 'button',
              onClick: () => {
                setCategoryFilter('');
                setDisciplineFilter('');
                setFormatFilter('');
                resetSearchResults();
              },
              'aria-label': 'Clear Symbol Set Builder filters',
            }, 'Clear filters'),
          ),
          searchError ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, searchError) : null,
          !searchLoading && !searchError && searchResults.length === 0
            ? createElement('p', { role: 'status' }, 'No matching symbols found.')
            : null,
          searchResults.length > 0
            ? createElement(
              'div',
              null,
              createElement('button', {
                type: 'button',
                onClick: addSelectedToSet,
                disabled: !editableReady || Object.values(selectedSearchIds).every((value) => !value),
                'aria-label': 'Add selected symbols to this Symbol Set',
              }, 'Add selected to set'),
              createElement('ul', { className: 'set-admin-list', 'aria-label': 'Symbol Set Builder search results' },
                searchResults.map((entry) => {
                  const alreadyPresent = presentIds.has(entry.governedSymbolId);
                  const addable = (entry.source === 'public' || entry.source === 'organization') && !alreadyPresent;
                  return createElement('li', { key: entry.governedSymbolId, className: 'set-admin-item' },
                    createElement('label', null,
                      createElement('input', {
                        type: 'checkbox',
                        disabled: !editableReady || !addable,
                        checked: Boolean(selectedSearchIds[entry.governedSymbolId]),
                        onChange: () => toggleSearchSelection(entry.governedSymbolId),
                        'aria-label': `Select ${entry.canonicalName}`,
                      }),
                      createElement('strong', null, ` ${entry.canonicalName} · ${symbolDisplayId(entry)}`),
                      createElement(SourceBadge, { source: entry.source, organizationWide: entry.organizationWide }),
                    ),
                    createElement('p', { className: 'set-admin-muted' },
                      `Category: ${entry.category} · Discipline: ${entry.discipline}`
                      + (alreadyPresent ? ' · Already in this set' : '')
                      + (entry.source === 'organization' ? ' · Approved organization symbol' : '')),
                  );
                }),
              ),
              searchTotal > 0
                ? createElement('div', { className: 'project-context-pagination', 'aria-label': 'Symbol Set Builder search pagination' },
                  createElement('button', {
                    type: 'button',
                    onClick: () => runSearch(undefined, Math.max(1, searchPage - 1)),
                    disabled: searchLoading || searchPage <= 1,
                    'aria-label': 'Previous Symbol Set Builder search page',
                  }, 'Previous'),
                  createElement('span', null, `Page ${searchPage} of ${searchPageCount}`),
                  createElement('button', {
                    type: 'button',
                    onClick: () => runSearch(undefined, Math.min(searchPageCount, searchPage + 1)),
                    disabled: searchLoading || searchPage >= searchPageCount,
                    'aria-label': 'Next Symbol Set Builder search page',
                  }, 'Next'),
                )
                : null,
            )
            : null,
        ),
        createElement(
          'div',
          { className: 'symbol-set-builder-items' },
          createElement('h3', null, 'Symbol Set items'),
          itemsLoading ? createElement('p', { role: 'status' }, 'Loading Symbol Set items…') : null,
          !itemsLoading && items.length === 0
            ? createElement('p', { role: 'status' }, 'This Symbol Set has no items yet. Add symbols from search.')
            : null,
          items.length > 0
            ? createElement('p', { className: 'set-admin-muted', role: 'status' },
              `${items.length} item(s) · Categories: ${Object.entries(categoryCounts).map(([key, count]) => `${key} (${count})`).join(', ')}`
              + ` · Disciplines: ${Object.entries(disciplineCounts).map(([key, count]) => `${key} (${count})`).join(', ')}`
              + (Object.keys(formatCounts).length > 0
                ? ` · Preferred formats: ${Object.entries(formatCounts).map(([key, count]) => `${key} (${count})`).join(', ')}`
                : ''))
            : null,
          createElement('div', { className: 'set-admin-actions', role: 'group', 'aria-label': 'Batch Symbol Set item actions' },
            createElement('button', {
              type: 'button',
              disabled: !editableReady || selectedItemCount === 0,
              onClick: () => beginRemoval(Object.entries(selectedItemIds).filter(([, selected]) => selected).map(([id]) => id)),
              'aria-label': 'Remove selected symbols from this Symbol Set',
            }, `Remove selected${selectedItemCount ? ` (${selectedItemCount})` : ''}`),
          ),
          pendingRemovalIds.length > 0
            ? createElement('div', { role: 'alertdialog', 'aria-label': 'Confirm staged Symbol Set item removal' },
              createElement('p', null, `Remove ${pendingRemovalIds.length} selected item(s) from the staged Symbol Set changes? This is local until you save.`),
              createElement('button', {
                type: 'button',
                onClick: confirmRemoval,
                'aria-label': 'Confirm removal of selected Symbol Set items',
              }, 'Confirm removal'),
              createElement('button', {
                type: 'button',
                onClick: () => setPendingRemovalIds([]),
                'aria-label': 'Cancel removal of selected Symbol Set items',
              }, 'Cancel'),
            )
            : null,
          createElement('ul', { className: 'set-admin-list symbol-set-builder-item-list', 'aria-label': 'Current Symbol Set items, in order' },
            items.map((item, index) => createElement('li', {
              key: item.governedSymbolId,
              className: `set-admin-item${item.availabilityStatus === 'unavailable' ? ' unavailable' : ''}`,
              draggable: editableReady,
              onDragStart: (event) => handleDragStart(event, item.governedSymbolId),
              onDragOver: (event) => event.preventDefault(),
              onDrop: (event) => handleDrop(event, item.governedSymbolId),
            },
            createElement(
              'div',
              null,
              createElement('input', {
                type: 'checkbox',
                disabled: !editableReady,
                checked: Boolean(selectedItemIds[item.governedSymbolId]),
                onChange: () => toggleItemSelection(item.governedSymbolId),
                'aria-label': `Select ${item.canonicalName || item.governedSymbolId} for removal`,
              }),
              createElement('strong', null, `${item.canonicalName || item.governedSymbolId} · ${symbolDisplayId(item)}`),
              createElement(SourceBadge, { source: item.source || 'public', organizationWide: item.organizationWide }),
              item.availabilityStatus === 'unavailable'
                ? createElement('span', { className: 'symbol-set-builder-badge unavailable' }, 'Unavailable')
                : null,
              createElement('p', { className: 'set-admin-muted' }, `Category: ${item.category || 'unknown'} · Discipline: ${item.discipline || 'unknown'}`),
              item.availabilityStatus === 'unavailable' && item.availabilityReason
                ? createElement('p', { className: 'set-admin-muted' }, item.availabilityReason)
                : null,
              createElement('label', { htmlFor: `builder-group-${item.governedSymbolId}` },
                'Group',
                createElement('input', {
                  id: `builder-group-${item.governedSymbolId}`,
                  value: item.groupName || '',
                  disabled: !editableReady,
                  onChange: (event) => updateField(item.governedSymbolId, 'groupName', event.target.value),
                }),
              ),
              createElement('label', { htmlFor: `builder-format-${item.governedSymbolId}` },
                'Preferred format',
                createElement('input', {
                  id: `builder-format-${item.governedSymbolId}`,
                  value: item.preferredFormat || '',
                  disabled: !editableReady,
                  onChange: (event) => updateField(item.governedSymbolId, 'preferredFormat', event.target.value),
                }),
              ),
            ),
            createElement(
              'div',
              { className: 'set-admin-actions', role: 'group', 'aria-label': `Reorder or remove ${item.canonicalName || item.governedSymbolId}` },
              createElement('button', {
                type: 'button',
                disabled: !editableReady || index === 0,
                onClick: () => moveItem(item.governedSymbolId, -1),
                'aria-label': `Move ${item.canonicalName || item.governedSymbolId} up`,
              }, 'Move up'),
              createElement('button', {
                type: 'button',
                disabled: !editableReady || index === items.length - 1,
                onClick: () => moveItem(item.governedSymbolId, 1),
                'aria-label': `Move ${item.canonicalName || item.governedSymbolId} down`,
              }, 'Move down'),
              createElement('button', {
                type: 'button',
                disabled: !editableReady,
                onClick: () => beginRemoval([item.governedSymbolId]),
                'aria-label': `Remove ${item.canonicalName || item.governedSymbolId} from this Symbol Set`,
              }, 'Remove'),
            ),
            )),
          ),
          createElement(
            'div',
            { className: 'set-admin-actions' },
            createElement('button', {
              type: 'button',
              disabled: !dirty || saving || !editableReady,
              onClick: saveChanges,
              'aria-label': 'Save Symbol Set changes',
            }, saving ? 'Saving…' : 'Save changes'),
            createElement('button', {
              type: 'button',
              disabled: !dirty || saving || !editableReady,
              onClick: discardChanges,
              'aria-label': 'Discard Symbol Set changes',
            }, 'Discard changes'),
          ),
        ),
      )
      : null,
  );
}
