import { createElement, useCallback, useEffect, useState } from 'react';

import {
  decideHannahPhoto,
  listHannahConceptPhotos,
  listHannahConcepts,
  searchHannahConceptNow,
  startHannahCurationSearch,
  updateHannahConcept,
} from './api.js';

// WP-H4 -- the steward's side of Hannah's concept photos, in the Workspace
// Curation tab. Hannah proposes up to two real-world photos per semantic
// concept; a steward approves, rejects or removes them, and corrects whether a
// concept can be photographed or what Hannah should search for.
//
// Concepts are named by their concept code (`SGC-00000123`) and the symbols on
// them by catalogue ID, never by UUID. Every write returns the concept as the
// list shows it, so one card is replaced in place.

const DEFAULT_API = {
  list: listHannahConcepts,
  photos: listHannahConceptPhotos,
  update: updateHannahConcept,
  searchNow: searchHannahConceptNow,
  decide: decideHannahPhoto,
  startSearch: startHannahCurationSearch,
};

export const TARGET_STATUS_LABELS = {
  has_proposals: 'Proposals to review',
  has_photos: 'Has photos',
  pending: 'Waiting to be searched',
  searched: 'Searched',
  no_candidates: 'No photos found',
  excluded: 'Not photographable',
};

const BASIS_LABELS = {
  concept_kind: 'set by concept type',
  llm: 'decided by the model',
  steward: 'set by a steward',
};

const PHOTO_STATUS_LABELS = {
  proposed: 'Proposed',
  approved: 'Approved',
  candidate: 'Candidate',
  rejected: 'Rejected',
  removed: 'Removed',
  filtered: 'Filtered out',
};

const DECISION_LABELS = { approve: 'Approve', reject: 'Reject', remove: 'Remove' };

export function formatTimestamp(value) {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return '';
  }
  return `${date.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

export function photoDecisions(photo) {
  if (photo.status === 'approved') {
    return ['remove'];
  }
  if (photo.status === 'proposed' || photo.status === 'candidate') {
    return ['approve', 'reject'];
  }
  if (photo.status === 'rejected' || photo.status === 'removed') {
    return ['approve'];
  }
  return [];
}

export function parseSearchTerms(value) {
  return String(value || '')
    .split(',')
    .map((term) => term.trim())
    .filter(Boolean);
}

function PhotoCard({ photo, concept, busy, onDecide }) {
  const label = photo.title || photo.depicts || 'Candidate photo';
  const credit = [photo.author, photo.licenseLabel].filter(Boolean).join(' · ');
  return createElement(
    'li',
    { className: `hannah-photo hannah-photo-${photo.status}` },
    createElement('img', {
      src: photo.thumbUrl,
      alt: photo.depicts ? `${label}: ${photo.depicts}` : label,
      loading: 'lazy',
      referrerPolicy: 'no-referrer',
    }),
    createElement(
      'div',
      { className: 'hannah-photo-body' },
      createElement(
        'p',
        { className: 'hannah-photo-status' },
        createElement('span', { className: 'status-pill' }, PHOTO_STATUS_LABELS[photo.status] || photo.status),
        photo.score !== null && photo.score !== undefined
          ? createElement('span', { className: 'hannah-photo-score' }, `Score ${photo.score.toFixed(2)}`)
          : null,
      ),
      createElement('p', { className: 'hannah-photo-title' }, label),
      photo.reason ? createElement('p', { className: 'hannah-photo-reason' }, photo.reason) : null,
      photo.filterReasons?.length
        ? createElement('p', { className: 'hannah-photo-reason' }, photo.filterReasons.join(', '))
        : null,
      createElement(
        'p',
        { className: 'hannah-photo-credit' },
        credit ? `${credit} · ` : '',
        createElement('a', { href: photo.sourceUrl, target: '_blank', rel: 'noreferrer' }, 'Source'),
      ),
      createElement(
        'div',
        { className: 'hannah-photo-actions' },
        photoDecisions(photo).map((decision) =>
          createElement(
            'button',
            {
              key: decision,
              type: 'button',
              className: `action-button compact ${decision === 'approve' ? 'primary' : decision === 'remove' ? 'danger' : 'ghost'}`,
              disabled: busy,
              'aria-label': `${DECISION_LABELS[decision]} ${label} for ${concept.conceptCode}`,
              onClick: () => onDecide(photo, decision),
            },
            DECISION_LABELS[decision],
          ),
        ),
      ),
    ),
  );
}

function ConceptCard({ concept, api, onReplace }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [terms, setTerms] = useState(concept.searchTerms.join(', '));
  const [allPhotos, setAllPhotos] = useState(null);
  const headingId = `hannah-concept-${concept.targetId}`;

  useEffect(() => {
    setTerms(concept.searchTerms.join(', '));
  }, [concept.searchTerms.join('|')]);

  const run = useCallback(async (action, successNotice) => {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      const result = await action();
      if (successNotice) {
        setNotice(typeof successNotice === 'function' ? successNotice(result) : successNotice);
      }
      return result;
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'The request failed.');
      return null;
    } finally {
      setBusy(false);
    }
  }, []);

  const refreshAll = useCallback(async () => {
    if (allPhotos !== null) {
      setAllPhotos(await api.photos(concept.targetId));
    }
  }, [allPhotos, api, concept.targetId]);

  async function decide(photo, decision) {
    const updated = await run(() => api.decide(photo.id, decision));
    if (updated) {
      onReplace(updated);
      await refreshAll();
    }
  }

  async function changePhotographable(value) {
    const updated = await run(() => api.update(concept.targetId, { photographable: value }));
    if (updated) {
      onReplace(updated);
    }
  }

  async function saveTerms(event) {
    event.preventDefault();
    const updated = await run(() => api.update(concept.targetId, { searchTerms: parseSearchTerms(terms) }), 'Search terms saved.');
    if (updated) {
      onReplace(updated);
    }
  }

  async function searchNow() {
    await run(() => api.searchNow(concept.targetId), (result) => result?.message || 'Searching now.');
  }

  async function toggleAll() {
    if (allPhotos !== null) {
      setAllPhotos(null);
      return;
    }
    const photos = await run(() => api.photos(concept.targetId));
    if (photos) {
      setAllPhotos(photos);
    }
  }

  const checked = Object.values(concept.photoCounts || {}).reduce((total, count) => total + count, 0);
  const searchable = concept.photographable === 'yes' || concept.photographable === 'unsure';
  const shownPhotos = allPhotos ?? concept.photos;

  return createElement(
    'article',
    { className: 'hannah-concept glass-panel', 'aria-labelledby': headingId },
    createElement(
      'header',
      { className: 'hannah-concept-header' },
      createElement(
        'h3',
        { id: headingId },
        createElement('span', { className: 'hannah-concept-code' }, concept.conceptCode),
        ' ',
        concept.name,
      ),
      createElement(
        'p',
        { className: 'hannah-concept-meta' },
        createElement('span', { className: 'status-pill' }, TARGET_STATUS_LABELS[concept.status] || concept.status),
        // `other` is the import's catch-all and tells an operator nothing.
        concept.conceptKind !== 'other' ? createElement('span', null, concept.conceptKind.replace(/_/g, ' ')) : null,
        createElement(
          'span',
          null,
          `${concept.symbolCount} ${concept.symbolCount === 1 ? 'symbol' : 'symbols'}`,
          concept.exampleSymbolIds.length ? `: ${concept.exampleSymbolIds.join(', ')}` : '',
        ),
        concept.lastAttemptAt ? createElement('span', null, `Last searched ${formatTimestamp(concept.lastAttemptAt)}`) : null,
        concept.wikidataQid
          ? createElement(
            'a',
            { href: `https://www.wikidata.org/wiki/${concept.wikidataQid}`, target: '_blank', rel: 'noreferrer' },
            `Wikidata ${concept.wikidataQid}`,
          )
          : null,
        concept.commonsCategory
          ? createElement(
            'a',
            {
              href: `https://commons.wikimedia.org/wiki/Category:${encodeURIComponent(concept.commonsCategory.replace(/ /g, '_'))}`,
              target: '_blank',
              rel: 'noreferrer',
            },
            `Commons: ${concept.commonsCategory}`,
          )
          : null,
      ),
    ),
    createElement(
      'div',
      { className: 'hannah-concept-controls' },
      createElement(
        'label',
        { className: 'field' },
        createElement(
          'span',
          null,
          'Photographable',
          concept.photographableBasis ? ` (${BASIS_LABELS[concept.photographableBasis] || concept.photographableBasis})` : '',
        ),
        createElement(
          'select',
          {
            value: concept.photographable || '',
            disabled: busy,
            'aria-label': `Can ${concept.conceptCode} be photographed`,
            onChange: (event) => changePhotographable(event.target.value),
          },
          concept.photographable ? null : createElement('option', { value: '' }, 'Undecided'),
          createElement('option', { value: 'yes' }, 'Yes'),
          createElement('option', { value: 'unsure' }, 'Unsure'),
          createElement('option', { value: 'no' }, 'No'),
        ),
      ),
      createElement(
        'form',
        { className: 'hannah-concept-terms', onSubmit: saveTerms },
        createElement(
          'label',
          { className: 'field' },
          createElement('span', null, 'Search terms (comma separated, up to three)'),
          createElement('input', {
            type: 'text',
            value: terms,
            disabled: busy,
            'aria-label': `Search terms for ${concept.conceptCode}`,
            onChange: (event) => setTerms(event.target.value),
          }),
        ),
        createElement(
          'button',
          { type: 'submit', className: 'action-button compact', disabled: busy || terms === concept.searchTerms.join(', ') },
          'Save terms',
        ),
      ),
      createElement(
        'button',
        {
          type: 'button',
          className: 'action-button compact',
          disabled: busy || !searchable,
          'aria-label': `Search for photos of ${concept.conceptCode} now`,
          onClick: searchNow,
        },
        'Search now',
      ),
    ),
    error ? createElement('p', { className: 'inline-status error', role: 'alert' }, error) : null,
    notice ? createElement('p', { className: 'inline-status success', role: 'status' }, notice) : null,
    shownPhotos.length
      ? createElement(
        'ul',
        { className: 'hannah-photo-grid', 'aria-label': `Photos for ${concept.conceptCode}` },
        shownPhotos.map((photo) =>
          createElement(PhotoCard, { key: photo.id, photo, concept, busy, onDecide: decide }),
        ),
      )
      : createElement('p', { className: 'hannah-concept-empty' }, allPhotos !== null ? 'Hannah has not looked at any images yet.' : 'No proposed or approved photos.'),
    checked
      ? createElement(
        'button',
        { type: 'button', className: 'action-button ghost compact', disabled: busy, onClick: toggleAll, 'aria-expanded': allPhotos !== null },
        allPhotos !== null ? 'Show proposals only' : `Show all ${checked} images Hannah checked`,
      )
      : null,
  );
}

export function HannahConceptReview({ api = DEFAULT_API, pageSize = 25 }) {
  const [filters, setFilters] = useState({ status: 'has_proposals', q: '' });
  const [state, setState] = useState({ loading: true, error: '', items: [], total: 0, summary: {}, offset: 0 });
  const [startState, setStartState] = useState({ pending: false, message: '', error: '' });

  const load = useCallback(async (offset = 0) => {
    setState((current) => ({ ...current, loading: true, error: '' }));
    try {
      const page = await api.list({ status: filters.status, q: filters.q, offset, limit: pageSize });
      setState((current) => ({
        loading: false,
        error: '',
        items: offset ? [...current.items, ...page.items] : page.items,
        total: page.total,
        summary: page.summary || {},
        offset,
      }));
    } catch (caught) {
      setState((current) => ({ ...current, loading: false, error: caught instanceof Error ? caught.message : 'Hannah concepts could not be loaded.' }));
    }
  }, [api, filters.status, filters.q, pageSize]);

  useEffect(() => {
    // Typing in the search box waits for a pause rather than loading per keystroke.
    const timer = setTimeout(() => load(0), filters.q ? 300 : 0);
    return () => clearTimeout(timer);
  }, [load, filters.q]);

  function replace(updated) {
    setState((current) => ({
      ...current,
      items: current.items.map((item) => (item.targetId === updated.targetId ? updated : item)),
    }));
  }

  async function startSearch() {
    setStartState({ pending: true, message: '', error: '' });
    try {
      const result = await api.startSearch({ durationSeconds: 120 });
      setStartState({ pending: false, message: result?.message || 'Search started.', error: '' });
    } catch (caught) {
      setStartState({ pending: false, message: '', error: caught instanceof Error ? caught.message : 'The search could not be started.' });
    }
  }

  return createElement(
    'div',
    { className: 'hannah-review' },
    createElement(
      'div',
      { className: 'workspace-content-titlebar glass-panel' },
      createElement(
        'div',
        null,
        createElement('p', { className: 'eyebrow' }, 'Hannah curation'),
        createElement('h2', null, 'Real-world photos'),
      ),
      createElement(
        'div',
        { className: 'workspace-content-tools' },
        startState.message ? createElement('span', { className: 'search-inline-status', role: 'status' }, startState.message) : null,
        createElement(
          'button',
          { type: 'button', className: 'action-button primary compact', disabled: startState.pending, onClick: startSearch },
          startState.pending ? 'Starting...' : 'Search due concepts',
        ),
      ),
    ),
    startState.error ? createElement('p', { className: 'inline-status error', role: 'alert' }, startState.error) : null,
    createElement(
      'div',
      { className: 'hannah-review-filters', role: 'group', 'aria-label': 'Concept filters' },
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Status'),
        createElement(
          'select',
          {
            'aria-label': 'Concept status filter',
            value: filters.status,
            onChange: (event) => setFilters((current) => ({ ...current, status: event.target.value })),
          },
          createElement('option', { value: '' }, `All (${Object.values(state.summary).reduce((total, count) => total + count, 0)})`),
          Object.entries(TARGET_STATUS_LABELS).map(([value, label]) =>
            createElement('option', { key: value, value }, `${label} (${state.summary[value] ?? 0})`),
          ),
        ),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Concept code or name'),
        createElement('input', {
          type: 'search',
          'aria-label': 'Search concepts',
          value: filters.q,
          onChange: (event) => setFilters((current) => ({ ...current, q: event.target.value })),
        }),
      ),
    ),
    state.error ? createElement('p', { className: 'inline-status error', role: 'alert' }, state.error) : null,
    createElement(
      'p',
      { className: 'hannah-review-count', 'aria-live': 'polite' },
      state.loading && !state.items.length ? 'Loading concepts…' : `Showing ${state.items.length} of ${state.total} concepts.`,
    ),
    state.items.map((concept) => createElement(ConceptCard, { key: concept.targetId, concept, api, onReplace: replace })),
    !state.loading && state.items.length < state.total
      ? createElement(
        'button',
        { type: 'button', className: 'action-button compact', onClick: () => load(state.offset + pageSize) },
        'Load more concepts',
      )
      : null,
  );
}
