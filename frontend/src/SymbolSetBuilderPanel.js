import { createElement, useCallback, useEffect, useMemo, useState } from 'react';

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
  const [items, setItems] = useState([]);

  const [query, setQuery] = useState('');
  const [searchLoading, setSearchLoading] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [searchResults, setSearchResults] = useState([]);
  const [selectedSearchIds, setSelectedSearchIds] = useState({});

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
      setItems([]);
      return;
    }
    setItemsLoading(true);
    setStatus({ mode: '', message: '' });
    try {
      const next = await api.listItems(setId, { page: 1, pageSize: 1000 });
      const loaded = (next?.items || []).map((item) => ({ ...item }));
      setSavedItems(loaded);
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
      const next = await api.search({ q: query.trim(), page: 1, pageSize: 100 });
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

  const presentIds = useMemo(() => new Set(items.map((item) => item.governedSymbolId)), [items]);

  function addSelectedToSet() {
    const toAdd = searchResults.filter((entry) => (
      entry.source === 'public' && selectedSearchIds[entry.governedSymbolId] && !presentIds.has(entry.governedSymbolId)
    ));
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
      await api.replaceItems(selectedSetId, items.map(toInput));
      setStatus({ mode: 'success', message: 'Symbol Set items saved.' });
      await loadItems(selectedSetId);
    } catch (err) {
      setStatus({ mode: 'error', message: err.message || 'Symbol Set items save failed.' });
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

  return createElement(
    'section',
    { className: 'symbol-set-builder-panel', 'aria-labelledby': 'symbol-set-builder-heading' },
    createElement('h2', { id: 'symbol-set-builder-heading' }, 'Symbol Set Builder'),
    loading ? createElement('p', { role: 'status' }, 'Loading Symbol Sets…') : null,
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    !loading && !error && sets.length === 0
      ? createElement('p', { role: 'status' }, 'No active Symbol Sets to build. Create one above first.')
      : null,
    StatusMessage({ status }),
    sets.length > 0
      ? createElement('label', { htmlFor: 'symbol-set-builder-set-select' },
        'Symbol Set',
        createElement('select', {
          id: 'symbol-set-builder-set-select',
          value: selectedSetId,
          onChange: (event) => setSelectedSetId(event.target.value),
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
              onChange: (event) => setQuery(event.target.value),
            }),
            createElement('button', { type: 'submit', disabled: searchLoading }, searchLoading ? 'Searching…' : 'Search'),
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
                disabled: Object.values(selectedSearchIds).every((value) => !value),
                'aria-label': 'Add selected symbols to this Symbol Set',
              }, 'Add selected to set'),
              createElement('ul', { className: 'set-admin-list', 'aria-label': 'Symbol Set Builder search results' },
                searchResults.map((entry) => {
                  const alreadyPresent = presentIds.has(entry.governedSymbolId);
                  const addable = entry.source === 'public' && !alreadyPresent;
                  return createElement('li', { key: entry.governedSymbolId, className: 'set-admin-item' },
                    createElement('label', null,
                      createElement('input', {
                        type: 'checkbox',
                        disabled: !addable,
                        checked: Boolean(selectedSearchIds[entry.governedSymbolId]),
                        onChange: () => toggleSearchSelection(entry.governedSymbolId),
                        'aria-label': `Select ${entry.canonicalName}`,
                      }),
                      createElement('strong', null, ` ${entry.canonicalName} · ${entry.slug}`),
                      createElement(SourceBadge, { source: entry.source, organizationWide: entry.organizationWide }),
                    ),
                    createElement('p', { className: 'set-admin-muted' },
                      `Category: ${entry.category} · Discipline: ${entry.discipline}`
                      + (alreadyPresent ? ' · Already in this set' : '')
                      + (entry.source === 'organization' ? ' · Toggle organization-wide instead of adding to a set' : '')),
                  );
                }),
              ),
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
          createElement('ul', { className: 'set-admin-list symbol-set-builder-item-list', 'aria-label': 'Current Symbol Set items, in order' },
            items.map((item, index) => createElement('li', {
              key: item.governedSymbolId,
              className: `set-admin-item${item.availabilityStatus === 'unavailable' ? ' unavailable' : ''}`,
              draggable: true,
              onDragStart: (event) => handleDragStart(event, item.governedSymbolId),
              onDragOver: (event) => event.preventDefault(),
              onDrop: (event) => handleDrop(event, item.governedSymbolId),
            },
            createElement(
              'div',
              null,
              createElement('strong', null, `${item.canonicalName || item.governedSymbolId} · ${item.slug || ''}`),
              createElement(SourceBadge, { source: 'public' }),
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
                  onChange: (event) => updateField(item.governedSymbolId, 'groupName', event.target.value),
                }),
              ),
              createElement('label', { htmlFor: `builder-format-${item.governedSymbolId}` },
                'Preferred format',
                createElement('input', {
                  id: `builder-format-${item.governedSymbolId}`,
                  value: item.preferredFormat || '',
                  onChange: (event) => updateField(item.governedSymbolId, 'preferredFormat', event.target.value),
                }),
              ),
            ),
            createElement(
              'div',
              { className: 'set-admin-actions', role: 'group', 'aria-label': `Reorder or remove ${item.canonicalName || item.governedSymbolId}` },
              createElement('button', {
                type: 'button',
                disabled: index === 0,
                onClick: () => moveItem(item.governedSymbolId, -1),
                'aria-label': `Move ${item.canonicalName || item.governedSymbolId} up`,
              }, 'Move up'),
              createElement('button', {
                type: 'button',
                disabled: index === items.length - 1,
                onClick: () => moveItem(item.governedSymbolId, 1),
                'aria-label': `Move ${item.canonicalName || item.governedSymbolId} down`,
              }, 'Move down'),
              createElement('button', {
                type: 'button',
                onClick: () => removeItem(item.governedSymbolId),
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
              disabled: !dirty || saving,
              onClick: saveChanges,
              'aria-label': 'Save Symbol Set changes',
            }, saving ? 'Saving…' : 'Save changes'),
            createElement('button', {
              type: 'button',
              disabled: !dirty || saving,
              onClick: discardChanges,
              'aria-label': 'Discard Symbol Set changes',
            }, 'Discard changes'),
          ),
        ),
      )
      : null,
  );
}
