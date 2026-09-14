import { createElement, useCallback, useEffect, useMemo, useState } from 'react';

import {
  decideSemanticReviewExternalMapping,
  decideSemanticReviewRightsRecord,
  decideSemanticReviewSemanticAssignment,
  decideSemanticReviewSymbolClassification,
  fetchSemanticReviewSymbolRevision,
  listSemanticReviewConceptClassifications,
  listSemanticReviewConceptExternalMappings,
  listSemanticReviewRightsRecords,
  listSemanticReviewSymbolClassifications,
  listSemanticReviewClassificationSchemes,
  listSemanticReviewSymbolSemanticAssignments,
  proposeSemanticReviewClassification,
  proposeSemanticReviewRightsRecord,
} from './api.js';

// SM-P1-01 WP1.4 -- the standalone semantic review surface (decision Q1).
//
// Three rules govern everything below and none of them is a preference:
//
// 1. **Controls come from `capabilities`, never from `status`.** The services
//    compute `canVerify`/`canReject`/`canRetire`/`mustRepropose` from the same
//    vocabularies the transition functions enforce, so a control rendered from
//    them cannot ask for a decision the database refuses. The two populations
//    that matter in production are both permanently barred while still being
//    `proposed`: every `legacy_backfill` classification (section 12.3) and
//    every `ai_assisted` rights determination (section 8.4). A status-derived
//    approve button would be offered on both and refused on both.
//
// 2. **A queue page has no total.** The WP1.1 queries carry no COUNT, so this
//    surface reports how many rows it is showing and pages by `offset`. A page
//    count would be a number the API cannot compute.
//
// 3. **Symbols are named by `catalogSymbolId`.** `CLAUDE.md`: human-readable
//    IDs stay prominent and a UUID never replaces one. An organisation-private
//    symbol that has never been published has no catalogue ID, and falls back
//    to its canonical name -- not to its UUID.

const DEFAULT_API = {
  symbolClassifications: listSemanticReviewSymbolClassifications,
  symbolSemanticAssignments: listSemanticReviewSymbolSemanticAssignments,
  conceptClassifications: listSemanticReviewConceptClassifications,
  conceptExternalMappings: listSemanticReviewConceptExternalMappings,
  rightsRecords: listSemanticReviewRightsRecords,
  symbolRevision: fetchSemanticReviewSymbolRevision,
  decideSemanticAssignment: decideSemanticReviewSemanticAssignment,
  decideSymbolClassification: decideSemanticReviewSymbolClassification,
  decideExternalMapping: decideSemanticReviewExternalMapping,
  decideRightsRecord: decideSemanticReviewRightsRecord,
  proposeRightsRecord: proposeSemanticReviewRightsRecord,
  classificationSchemes: listSemanticReviewClassificationSchemes,
  proposeClassification: proposeSemanticReviewClassification,
};

const DEFAULT_LIMIT = 50;

const GOVERNED_STATUSES = ['proposed', 'verified', 'rejected', 'retired'];
const RIGHTS_STATUSES_FILTER = ['proposed', 'approved', 'rejected', 'retired'];

const CLASSIFICATION_METHODS = ['manual', 'source_mapping', 'rule', 'ai_assisted', 'legacy_backfill'];
const SEMANTIC_ASSIGNMENT_METHODS = ['manual', 'source_mapping', 'rule', 'ai_assisted'];
const EXTERNAL_MAPPING_METHODS = ['manual', 'imported', 'rule', 'ai_assisted'];
const RIGHTS_DETERMINATION_METHODS = ['manual', 'licence_document', 'ai_assisted'];

// Decision Q6: these three schemes are read-only in v1. Existing assignments
// are displayed and marked; no control creates one, and the API refuses a
// proposal into them with the validation envelope, so the two agree.
const READ_ONLY_SCHEME_CODES = new Set(['USE-CASE', 'DOCUMENT-TYPE', 'REPRESENTATION-TYPE']);

// A reviewer's own rights determination exists to clear section 8.4's bar, so
// the one method that can never approve is not offered as a thing to propose.
// The API still accepts it; this surface simply does not ask for it.
const PROPOSABLE_DETERMINATION_METHODS = RIGHTS_DETERMINATION_METHODS.filter(
  (method) => method !== 'ai_assisted',
);

// Section 12.3's "propose afresh with a real method". A reviewer choosing a
// node in a review surface is making a manual determination; `rule` or
// `source_mapping` would claim a provenance that did not happen, and
// `legacy_backfill` is the very thing being replaced.
const REVIEWER_CLASSIFICATION_METHOD = 'manual';

const SYMBOL_CLASSIFICATION_ROLES = ['primary', 'secondary'];

const RIGHTS_DISPOSITIONS = ['display', 'distribute', 'transform', 'compare_only', 'metadata_only', 'reject'];
const RIGHTS_STATUS_VALUES = ['unknown', 'open', 'licensed', 'restricted', 'prohibited', 'expired'];

export const SEMANTIC_REVIEW_QUEUES = [
  {
    key: 'symbol-classifications',
    label: 'Symbol classifications',
    detail: 'revision',
    source: 'symbolClassifications',
    methods: CLASSIFICATION_METHODS,
    statuses: GOVERNED_STATUSES,
    scheme: true,
    rowId: (row) => row.assignmentId,
  },
  {
    key: 'symbol-semantic-assignments',
    label: 'Symbol concepts',
    detail: 'revision',
    source: 'symbolSemanticAssignments',
    methods: SEMANTIC_ASSIGNMENT_METHODS,
    statuses: GOVERNED_STATUSES,
    scheme: false,
    rowId: (row) => row.assignmentId,
  },
  {
    key: 'concept-classifications',
    label: 'Concept classifications',
    detail: 'concept-classification',
    source: 'conceptClassifications',
    methods: CLASSIFICATION_METHODS,
    statuses: GOVERNED_STATUSES,
    scheme: true,
    rowId: (row) => row.assignmentId,
  },
  {
    key: 'concept-external-mappings',
    label: 'External mappings',
    detail: 'mapping',
    source: 'conceptExternalMappings',
    methods: EXTERNAL_MAPPING_METHODS,
    statuses: GOVERNED_STATUSES,
    scheme: true,
    rowId: (row) => row.referenceId,
  },
  {
    key: 'rights-records',
    label: 'Rights records',
    detail: 'rights',
    source: 'rightsRecords',
    methods: RIGHTS_DETERMINATION_METHODS,
    statuses: RIGHTS_STATUSES_FILTER,
    scheme: false,
    rightsMethodParam: true,
    rowId: (row) => row.recordId,
  },
];

function queueByKey(key) {
  return SEMANTIC_REVIEW_QUEUES.find((queue) => queue.key === key) || SEMANTIC_REVIEW_QUEUES[0];
}

export function symbolLabel(symbol) {
  if (!symbol) return 'Unknown symbol';
  return symbol.catalogSymbolId || symbol.canonicalName || 'Unnamed symbol';
}

export function conceptLabel(concept) {
  if (!concept) return 'Unknown concept';
  return concept.conceptCode || concept.preferredName || 'Unnamed concept';
}

export function formatReviewTimestamp(value) {
  if (!value) return 'Unknown';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return `${parsed.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

function formatConfidence(confidence) {
  if (typeof confidence !== 'number') return 'No confidence recorded';
  return `Confidence ${Math.round(confidence * 100)}%`;
}

function errorMessage(error, fallback) {
  return (error && error.message) || fallback;
}

function statusClassName(status) {
  return `review-status review-${String(status || 'unknown').toLowerCase().replace(/[^a-z0-9_-]+/g, '-')}`;
}

function Eyebrow({ children }) {
  return createElement('p', { className: 'eyebrow' }, children);
}

function EvidenceBlock({ evidence }) {
  const hasEvidence = evidence && typeof evidence === 'object' && Object.keys(evidence).length > 0;
  return createElement(
    'div',
    { className: 'copy-block semantic-review-evidence' },
    createElement('h4', null, 'Evidence'),
    hasEvidence
      ? createElement('pre', null, JSON.stringify(evidence, null, 2))
      : createElement('p', null, 'No evidence was recorded with this assertion.'),
  );
}

function Fact({ label, value }) {
  return createElement(
    'div',
    { className: 'fact-card' },
    createElement('strong', null, value),
    createElement('span', null, label),
  );
}

// One row's available decisions, rendered from its capabilities. `verifyLabel`
// exists because the rights table names its affirmative decision `approved`
// where the other three name theirs `verified`; the capability keeps one name
// so one control renders across every row kind.
function DecisionControls({ identifier, capabilities, verifyLabel = 'Verify', verifyStatus = 'verified', busy, onDecide, extraNote }) {
  const actions = [];
  if (capabilities?.canVerify) actions.push([verifyLabel, verifyStatus]);
  if (capabilities?.canReject) actions.push(['Reject', 'rejected']);
  if (capabilities?.canRetire) actions.push(['Retire', 'retired']);

  return createElement(
    'div',
    { className: 'semantic-review-decisions' },
    actions.length
      ? createElement(
        'div',
        { className: 'action-stack semantic-review-actions', role: 'group', 'aria-label': `Decisions for ${identifier}` },
        actions.map(([label, targetStatus]) => createElement(
          'button',
          {
            key: targetStatus,
            type: 'button',
            className: `action-button ${label === 'Reject' ? 'danger' : label === 'Retire' ? '' : 'primary'}`.trim(),
            disabled: Boolean(busy),
            'aria-label': `${label} ${identifier}`,
            onClick: () => onDecide(targetStatus),
          },
          label,
        )),
      )
      : createElement('p', { className: 'set-admin-muted' }, 'No decision is available for this row.'),
    capabilities?.blockedReason
      ? createElement('p', { role: 'note', className: 'form-message semantic-review-blocked' }, capabilities.blockedReason)
      : null,
    extraNote || null,
  );
}

function SchemeMarker({ schemeCode }) {
  if (!READ_ONLY_SCHEME_CODES.has(schemeCode)) return null;
  return createElement('span', { className: 'status-pill semantic-review-readonly' }, 'Read-only scheme');
}

function classificationIdentifier(row) {
  return `classification ${symbolLabel(row.symbol)} ${row.schemeCode} ${row.nodeCode}`;
}

function semanticIdentifier(row) {
  return `concept assignment ${symbolLabel(row.symbol)} ${conceptLabel(row.concept)}`;
}

function mappingIdentifier(row) {
  return `mapping ${conceptLabel(row.concept)} ${row.externalIdentifier}`;
}

function rightsIdentifier(row) {
  const subject = row.symbol
    ? symbolLabel(row.symbol)
    : row.sourcePackageId
      ? 'source package'
      : 'standard edition';
  return `rights record ${subject}`;
}

function rowSummary(queueKey, row) {
  switch (queueKey) {
    case 'symbol-classifications':
      return {
        title: symbolLabel(row.symbol),
        detail: `${row.schemeCode} · ${row.nodeCode} — ${row.nodeLabel}`,
        status: row.status,
        method: row.method,
        schemeCode: row.schemeCode,
      };
    case 'symbol-semantic-assignments':
      return {
        title: symbolLabel(row.symbol),
        detail: `${conceptLabel(row.concept)} · ${row.assignmentRole}`,
        status: row.status,
        method: row.method,
      };
    case 'concept-classifications':
      return {
        title: conceptLabel(row.concept),
        detail: `${row.schemeCode} · ${row.nodeCode} — ${row.nodeLabel}`,
        status: row.status,
        method: row.method,
        schemeCode: row.schemeCode,
      };
    case 'concept-external-mappings':
      return {
        title: conceptLabel(row.concept),
        detail: `${row.schemeCode} ${row.schemeVersionLabel} · ${row.externalIdentifier} (${row.mappingType})`,
        status: row.status,
        method: row.method,
      };
    default:
      return {
        title: row.symbol ? symbolLabel(row.symbol) : row.subjectKind.replace(/_/g, ' '),
        detail: `${row.disposition} · ${row.rightsStatus}`,
        status: row.status,
        method: row.determinationMethod,
      };
  }
}

function emptyRightsProposal() {
  return {
    disposition: 'metadata_only',
    determinationMethod: 'manual',
    rightsStatus: 'unknown',
    licenceReference: '',
    decisionReason: '',
  };
}

export function SemanticReviewPage({ auth, api = DEFAULT_API }) {
  const [queueKey, setQueueKey] = useState(SEMANTIC_REVIEW_QUEUES[0].key);
  const [filters, setFilters] = useState({ status: 'proposed', method: '', schemeCode: '' });
  const [offset, setOffset] = useState(0);
  const [queueState, setQueueState] = useState({ loading: true, error: '', items: [], limit: DEFAULT_LIMIT, offset: 0 });
  const [selectedId, setSelectedId] = useState('');
  const [revisionState, setRevisionState] = useState({ loading: false, error: '', state: null });
  const [decision, setDecision] = useState({ busy: '', error: '', message: '' });
  const [verificationBasis, setVerificationBasis] = useState('');
  const [proposal, setProposal] = useState(emptyRightsProposal);
  // A successful proposal writes a new `proposed` row that belongs in the
  // queue the reviewer is looking at. Re-cloning `filters` would not refetch:
  // the page effect depends on the filter *values*, not on their container.
  const [refreshToken, setRefreshToken] = useState(0);
  const [schemes, setSchemes] = useState({ loaded: false, error: '', items: [] });
  const [draft, setDraft] = useState({ schemeCode: '', nodeId: '', assignmentRole: 'primary' });

  const queue = queueByKey(queueKey);

  const params = useMemo(() => {
    const next = { status: filters.status, limit: DEFAULT_LIMIT, offset };
    if (queue.rightsMethodParam) {
      next.determinationMethod = filters.method;
    } else {
      next.method = filters.method;
    }
    if (queue.scheme) next.schemeCode = filters.schemeCode;
    return next;
  }, [queue, filters.status, filters.method, filters.schemeCode, offset]);

  useEffect(() => {
    let cancelled = false;
    setQueueState((current) => ({ ...current, loading: true, error: '' }));
    api[queue.source](params)
      .then((page) => {
        if (cancelled) return;
        const items = Array.isArray(page?.items) ? page.items : [];
        setQueueState({
          loading: false,
          error: '',
          items,
          limit: Number(page?.limit ?? DEFAULT_LIMIT),
          offset: Number(page?.offset ?? 0),
        });
        setSelectedId(items.length ? queue.rowId(items[0]) : '');
      })
      .catch((error) => {
        if (cancelled) return;
        setQueueState({ loading: false, error: errorMessage(error, 'Queue load failed.'), items: [], limit: DEFAULT_LIMIT, offset: 0 });
        setSelectedId('');
      });
    return () => {
      cancelled = true;
    };
  }, [api, queue, params, refreshToken]);

  const selectedRow = queueState.items.find((row) => queue.rowId(row) === selectedId) || null;

  // Only a symbol-targeted row has a revision to open. A concept assertion
  // names no symbol revision, so opening one would be a read the row does not
  // justify.
  const revisionId = queue.detail === 'revision' ? selectedRow?.symbolRevisionId || '' : '';

  useEffect(() => {
    if (!revisionId) {
      setRevisionState({ loading: false, error: '', state: null });
      return undefined;
    }
    let cancelled = false;
    setRevisionState({ loading: true, error: '', state: null });
    // A node chosen for one revision must not be carried to the next: the
    // proposal would land on a symbol the reviewer never picked it for.
    setDraft((current) => ({ ...current, nodeId: '' }));
    api.symbolRevision(revisionId)
      .then((state) => {
        if (!cancelled) setRevisionState({ loading: false, error: '', state });
      })
      .catch((error) => {
        if (!cancelled) setRevisionState({ loading: false, error: errorMessage(error, 'Revision load failed.'), state: null });
      });
    return () => {
      cancelled = true;
    };
  }, [api, revisionId]);

  // Once per session, not once per row: the two assignable schemes are
  // seeded reference data holding 31 nodes between them.
  useEffect(() => {
    if (schemes.loaded || queue.detail !== 'revision') return undefined;
    let cancelled = false;
    api.classificationSchemes()
      .then((page) => {
        if (cancelled) return;
        const items = Array.isArray(page?.items) ? page.items : [];
        setSchemes({ loaded: true, error: '', items });
        setDraft((current) => (current.schemeCode ? current : { ...current, schemeCode: items[0]?.schemeCode || '' }));
      })
      .catch((error) => {
        if (!cancelled) setSchemes({ loaded: true, error: errorMessage(error, 'Classification scheme load failed.'), items: [] });
      });
    return () => {
      cancelled = true;
    };
  }, [api, queue.detail, schemes.loaded]);

  const changeQueue = useCallback((nextKey) => {
    setQueueKey(nextKey);
    setFilters({ status: 'proposed', method: '', schemeCode: '' });
    setOffset(0);
    setSelectedId('');
    setDecision({ busy: '', error: '', message: '' });
    setVerificationBasis('');
    setProposal(emptyRightsProposal());
  }, []);

  const changeFilter = useCallback((key, value) => {
    setOffset(0);
    setFilters((current) => ({ ...current, [key]: value }));
  }, []);

  function replaceQueueRow(nextRow, idOf) {
    setQueueState((current) => ({
      ...current,
      items: current.items.map((row) => (idOf(row) === idOf(nextRow) ? nextRow : row)),
    }));
  }

  async function runDecision(key, action, onSuccess) {
    setDecision({ busy: key, error: '', message: '' });
    try {
      const result = await action();
      onSuccess(result);
      setDecision({ busy: '', error: '', message: 'Decision recorded.' });
    } catch (error) {
      setDecision({ busy: '', error: errorMessage(error, 'Decision failed.'), message: '' });
    }
  }

  function decideClassification(row, targetStatus) {
    return runDecision(row.assignmentId, () => api.decideSymbolClassification(row.assignmentId, { targetStatus }), (state) => {
      // A write returns the revision's whole state, so the panel re-renders
      // from the response rather than taking a second read.
      setRevisionState({ loading: false, error: '', state });
    });
  }

  function decideSemanticAssignment(row, targetStatus) {
    return runDecision(row.assignmentId, () => api.decideSemanticAssignment(row.assignmentId, { targetStatus }), (state) => {
      setRevisionState({ loading: false, error: '', state });
    });
  }

  function decideMapping(row, targetStatus) {
    return runDecision(
      row.referenceId,
      () => api.decideExternalMapping(row.referenceId, { targetStatus, verificationBasis }),
      (list) => {
        const items = Array.isArray(list?.items) ? list.items : [];
        const updated = items.find((item) => item.referenceId === row.referenceId);
        if (updated) replaceQueueRow(updated, (candidate) => candidate.referenceId);
        setVerificationBasis('');
      },
    );
  }

  function decideRights(row, targetStatus) {
    return runDecision(
      row.recordId,
      () => api.decideRightsRecord(row.recordId, { targetStatus }),
      (updated) => {
        if (updated?.recordId) replaceQueueRow(updated, (candidate) => candidate.recordId);
      },
    );
  }

  async function submitClassificationProposal(revisionId) {
    if (!draft.nodeId) return;
    await runDecision(
      `${revisionId}-classification-proposal`,
      () => api.proposeClassification(revisionId, {
        classificationNodeId: draft.nodeId,
        assignmentRole: draft.assignmentRole,
        method: REVIEWER_CLASSIFICATION_METHOD,
      }),
      (state) => {
        setRevisionState({ loading: false, error: '', state });
        setDraft((current) => ({ ...current, nodeId: '' }));
      },
    );
  }

  async function submitRightsProposal(row) {
    const subject = row.symbolRevisionId
      ? { symbolRevisionId: row.symbolRevisionId }
      : row.sourcePackageId
        ? { sourcePackageId: row.sourcePackageId }
        : { standardVersionId: row.standardVersionId };
    await runDecision(
      `${row.recordId}-proposal`,
      () => api.proposeRightsRecord({ ...subject, ...proposal }),
      () => {
        setProposal(emptyRightsProposal());
        setRefreshToken((current) => current + 1);
      },
    );
  }

  const showingCount = queueState.items.length;
  const atFirstPage = queueState.offset <= 0;
  const maybeMore = showingCount >= queueState.limit;

  return createElement(
    'section',
    { className: 'experience-shell semantic-review-shell' },
    createElement(
      'div',
      { className: 'hero-panel glass-panel workspace-hero' },
      createElement(
        'div',
        null,
        createElement(Eyebrow, null, 'Platform governance'),
        createElement('h2', null, 'Semantic Review'),
      ),
      createElement(
        'p',
        { className: 'page-status-text' },
        `Reviewer: ${auth?.user?.displayName || auth?.user?.email || 'Signed in'}`,
      ),
    ),

    createElement(
      'nav',
      {
        className: 'semantic-review-tabs',
        role: 'tablist',
        'aria-label': 'Semantic review queues',
        onKeyDown: onTabKeyDown,
      },
      SEMANTIC_REVIEW_QUEUES.map((candidate) => createElement(
        'button',
        {
          key: candidate.key,
          type: 'button',
          role: 'tab',
          id: `semantic-review-tab-${candidate.key}`,
          'aria-selected': candidate.key === queueKey,
          'aria-controls': 'semantic-review-panel',
          'aria-label': `${candidate.label} queue`,
          // Roving tabindex: `role="tablist"` promises that the arrow keys
          // move between tabs and that Tab leaves the group, so the promise is
          // kept rather than left to the browser's default button order.
          tabIndex: candidate.key === queueKey ? 0 : -1,
          className: `semantic-review-tab ${candidate.key === queueKey ? 'active' : ''}`.trim(),
          onClick: () => changeQueue(candidate.key),
        },
        candidate.label,
      )),
    ),

    createElement(
      'div',
      { className: 'semantic-review-filters', role: 'group', 'aria-label': 'Queue filters' },
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Status'),
        createElement(
          'select',
          {
            'aria-label': 'Status filter',
            value: filters.status,
            onChange: (event) => changeFilter('status', event.target.value),
          },
          queue.statuses.map((value) => createElement('option', { key: value, value }, value)),
        ),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, queue.rightsMethodParam ? 'Determination method' : 'Method'),
        createElement(
          'select',
          {
            'aria-label': queue.rightsMethodParam ? 'Determination method filter' : 'Method filter',
            value: filters.method,
            onChange: (event) => changeFilter('method', event.target.value),
          },
          createElement('option', { key: 'any', value: '' }, 'Any method'),
          queue.methods.map((value) => createElement('option', { key: value, value }, value)),
        ),
      ),
      queue.scheme
        ? createElement(
          'label',
          { className: 'field' },
          createElement('span', null, 'Scheme code'),
          createElement('input', {
            type: 'search',
            'aria-label': 'Scheme code filter',
            value: filters.schemeCode,
            placeholder: 'ENGINEERING-DISCIPLINE',
            onChange: (event) => changeFilter('schemeCode', event.target.value),
          }),
        )
        : null,
    ),

    createElement(
      'div',
      {
        className: 'semantic-review-grid',
        id: 'semantic-review-panel',
        role: 'tabpanel',
        'aria-labelledby': `semantic-review-tab-${queueKey}`,
      },
      createElement(
        'section',
        { className: 'glass-panel pane semantic-review-queue-pane' },
        createElement(
          'div',
          { className: 'review-pane-heading' },
          createElement(
            'div',
            { className: 'review-queue-title-row' },
            createElement('h3', null, queue.label),
            createElement(
              'div',
              { className: 'review-navigation compact-icon-navigation', 'aria-label': 'Queue paging' },
              createElement('button', {
                type: 'button',
                className: 'icon-nav-button',
                disabled: atFirstPage || queueState.loading,
                'aria-label': 'Previous page',
                onClick: () => setOffset(Math.max(queueState.offset - queueState.limit, 0)),
              }, '<'),
              createElement('button', {
                type: 'button',
                className: 'icon-nav-button',
                disabled: !maybeMore || queueState.loading,
                'aria-label': 'Next page',
                onClick: () => setOffset(queueState.offset + queueState.limit),
              }, '>'),
            ),
          ),
          // No total and no page count: the queue queries carry no COUNT.
          createElement(
            'p',
            { className: 'set-admin-muted semantic-review-count' },
            `Showing ${showingCount} row${showingCount === 1 ? '' : 's'} from position ${queueState.offset + 1}`,
          ),
        ),
        queueState.loading
          ? createElement('p', { role: 'status' }, 'Loading queue…')
          : null,
        queueState.error
          ? createElement('p', { role: 'alert', className: 'form-message error' }, queueState.error)
          : null,
        !queueState.loading && !queueState.error && showingCount === 0
          ? createElement('p', { className: 'set-admin-muted' }, 'No rows in this queue for the current filters.')
          : null,
        createElement(
          'ul',
          { className: 'stack-list semantic-review-list', 'data-offset': queueState.offset, 'aria-label': `${queue.label} rows` },
          queueState.items.map((row) => {
            const id = queue.rowId(row);
            const summary = rowSummary(queue.key, row);
            return createElement(
              'li',
              { key: id },
              createElement(
                'button',
                {
                  type: 'button',
                  className: `queue-card semantic-review-card ${id === selectedId ? 'active' : ''}`.trim(),
                  'aria-label': `Open ${summary.title} ${summary.detail}`,
                  'aria-current': id === selectedId,
                  onClick: () => setSelectedId(id),
                },
                createElement(
                  'div',
                  { className: 'queue-card-topline' },
                  createElement('strong', null, summary.title),
                  createElement('span', { className: statusClassName(summary.status) }, summary.status),
                ),
                createElement('p', null, summary.detail),
                createElement(
                  'small',
                  null,
                  `${summary.method} · proposed ${formatReviewTimestamp(row.proposedAt)}`,
                ),
                summary.schemeCode ? createElement(SchemeMarker, { schemeCode: summary.schemeCode }) : null,
              ),
            );
          }),
        ),
      ),

      createElement(
        'section',
        { className: 'glass-panel pane semantic-review-focus-pane review-focus-pane' },
        decision.error
          ? createElement('p', { role: 'alert', className: 'form-message error' }, decision.error)
          : null,
        decision.message
          ? createElement('p', { role: 'status', className: 'form-message success' }, decision.message)
          : null,
        renderFocus(),
      ),
    ),
  );

  function onTabKeyDown(event) {
    const keys = { ArrowLeft: -1, ArrowRight: 1, Home: 'first', End: 'last' };
    const move = keys[event.key];
    if (move === undefined) return;
    event.preventDefault?.();
    const index = SEMANTIC_REVIEW_QUEUES.findIndex((candidate) => candidate.key === queueKey);
    const last = SEMANTIC_REVIEW_QUEUES.length - 1;
    const nextIndex = move === 'first'
      ? 0
      : move === 'last'
        ? last
        : (index + move + SEMANTIC_REVIEW_QUEUES.length) % SEMANTIC_REVIEW_QUEUES.length;
    const next = SEMANTIC_REVIEW_QUEUES[nextIndex];
    changeQueue(next.key);
    // Only present on a real DOM event; the tab still changes without it.
    event.currentTarget?.querySelector?.(`#semantic-review-tab-${next.key}`)?.focus?.();
  }

  function renderFocus() {
    if (!selectedRow) {
      return createElement(
        'div',
        { className: 'workspace-empty-state' },
        createElement(Eyebrow, null, 'Nothing selected'),
        createElement('h3', null, 'Select a row to review it.'),
      );
    }
    if (queue.detail === 'revision') return renderRevisionDetail();
    if (queue.detail === 'mapping') return renderMappingDetail(selectedRow);
    if (queue.detail === 'rights') return renderRightsDetail(selectedRow);
    return renderConceptClassificationDetail(selectedRow);
  }

  function renderRevisionDetail() {
    if (revisionState.loading) return createElement('p', { role: 'status' }, 'Loading revision…');
    if (revisionState.error) {
      return createElement('p', { role: 'alert', className: 'form-message error' }, revisionState.error);
    }
    const state = revisionState.state;
    if (!state) return null;

    return createElement(
      'div',
      { className: 'semantic-review-detail' },
      createElement(
        'div',
        { className: 'detail-heading' },
        createElement(
          'div',
          null,
          createElement('h3', null, symbolLabel(state.symbol)),
          createElement('p', null, `${state.symbol?.canonicalName || ''} · revision ${state.revisionLabel}`),
        ),
        createElement('span', { className: 'status-pill' }, state.lifecycleState),
      ),
      createElement(
        'div',
        { className: 'fact-grid' },
        createElement(Fact, { label: 'Visibility', value: state.symbol?.visibility || 'unknown' }),
        createElement(Fact, { label: 'Revision', value: state.revisionLabel }),
        createElement(Fact, { label: 'Lifecycle', value: state.lifecycleState }),
      ),

      createElement(
        'div',
        { className: 'semantic-review-section' },
        createElement('h4', null, 'Concept assignments'),
        (state.semanticAssignments || []).length
          ? (state.semanticAssignments || []).map((row) => createElement(
            'article',
            { key: row.assignmentId, className: 'copy-block semantic-review-row' },
            createElement(
              'div',
              { className: 'queue-card-topline' },
              createElement('strong', null, `${conceptLabel(row.concept)} — ${row.concept?.preferredName || ''}`),
              createElement('span', { className: statusClassName(row.status) }, row.status),
            ),
            createElement('p', null, `${row.assignmentRole} · ${row.method} · ${formatConfidence(row.confidence)}`),
            createElement('small', null, `Proposed ${formatReviewTimestamp(row.proposedAt)}`),
            createElement(EvidenceBlock, { evidence: row.evidence }),
            createElement(DecisionControls, {
              identifier: semanticIdentifier(row),
              capabilities: row.capabilities,
              busy: decision.busy === row.assignmentId,
              onDecide: (targetStatus) => decideSemanticAssignment(row, targetStatus),
            }),
          ))
          : createElement('p', { className: 'set-admin-muted' }, 'No concept assignments are recorded for this revision.'),
      ),

      createElement(
        'div',
        { className: 'semantic-review-section' },
        createElement('h4', null, 'Classification assignments'),
        (state.classificationAssignments || []).length
          ? (state.classificationAssignments || []).map((row) => createElement(
            'article',
            { key: row.assignmentId, className: 'copy-block semantic-review-row' },
            createElement(
              'div',
              { className: 'queue-card-topline' },
              createElement('strong', null, `${row.schemeCode} · ${row.nodeCode} — ${row.nodeLabel}`),
              createElement('span', { className: statusClassName(row.status) }, row.status),
            ),
            createElement(SchemeMarker, { schemeCode: row.schemeCode }),
            createElement('p', null, `${row.assignmentRole} · ${row.method} · ${formatConfidence(row.confidence)}`),
            createElement('small', null, `Proposed ${formatReviewTimestamp(row.proposedAt)}`),
            createElement(EvidenceBlock, { evidence: row.evidence }),
            createElement(DecisionControls, {
              identifier: classificationIdentifier(row),
              capabilities: row.capabilities,
              busy: decision.busy === row.assignmentId,
              onDecide: (targetStatus) => decideClassification(row, targetStatus),
              extraNote: row.capabilities?.mustRepropose
                ? createElement(
                  'p',
                  { className: 'set-admin-muted' },
                  'Reject this row, then propose the correct node below. The order is '
                  + 'forced: one revision may hold only one live assignment per node.',
                )
                : null,
            }),
          ))
          : createElement('p', { className: 'set-admin-muted' }, 'No classification assignments are recorded for this revision.'),
        renderClassificationProposalForm(state),
      ),

      createElement(
        'div',
        { className: 'semantic-review-section' },
        createElement('h4', null, 'Rights records'),
        (state.rightsRecords || []).length
          ? (state.rightsRecords || []).map((row) => createElement(
            'article',
            { key: row.recordId, className: 'copy-block semantic-review-row' },
            createElement(
              'div',
              { className: 'queue-card-topline' },
              createElement('strong', null, `${row.disposition} · ${row.rightsStatus}`),
              createElement('span', { className: statusClassName(row.status) }, row.status),
            ),
            createElement('p', null, `${row.determinationMethod} · ${row.licenceReference || 'no licence reference'}`),
            createElement('small', null, `Proposed ${formatReviewTimestamp(row.proposedAt)}`),
            createElement(EvidenceBlock, { evidence: row.evidence }),
            createElement(DecisionControls, {
              identifier: `${rightsIdentifier(row)} on revision ${state.revisionLabel}`,
              capabilities: row.capabilities,
              verifyLabel: 'Approve',
              verifyStatus: 'approved',
              busy: decision.busy === row.recordId,
              onDecide: (targetStatus) => decideRights(row, targetStatus),
            }),
          ))
          : createElement('p', { className: 'set-admin-muted' }, 'No rights records are recorded for this revision.'),
      ),
    );
  }

  function renderClassificationProposalForm(state) {
    if (schemes.error) {
      return createElement('p', { role: 'alert', className: 'form-message error' }, schemes.error);
    }
    if (!schemes.items.length) return null;

    const scheme = schemes.items.find((candidate) => candidate.schemeCode === draft.schemeCode)
      || schemes.items[0];
    // The partial unique index `uq_symbol_revision_classifications_active_node`
    // permits one live assignment per (revision, node), so a node this
    // revision already holds is shown as taken rather than offered and
    // refused.
    const liveNodeIds = new Set(
      (state.classificationAssignments || [])
        .filter((row) => row.status === 'proposed' || row.status === 'verified')
        .map((row) => row.classificationNodeId),
    );

    return createElement(
      'form',
      {
        className: 'decision-block semantic-review-proposal',
        'aria-label': 'Propose a classification',
        onSubmit: (event) => {
          event.preventDefault();
          return submitClassificationProposal(state.symbolRevisionId);
        },
      },
      createElement('h4', null, 'Propose a classification'),
      createElement(
        'p',
        { className: 'set-admin-muted' },
        `Recorded as a ${REVIEWER_CLASSIFICATION_METHOD} determination, attributed to you, and starting as proposed.`,
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Scheme'),
        createElement(
          'select',
          {
            'aria-label': 'Classification scheme',
            value: scheme.schemeCode,
            onChange: (event) => setDraft((current) => ({ ...current, schemeCode: event.target.value, nodeId: '' })),
          },
          schemes.items.map((candidate) => createElement(
            'option',
            { key: candidate.schemeCode, value: candidate.schemeCode },
            candidate.name || candidate.schemeCode,
          )),
        ),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Node'),
        createElement(
          'select',
          {
            'aria-label': 'Classification node',
            value: draft.nodeId,
            onChange: (event) => setDraft((current) => ({ ...current, nodeId: event.target.value })),
          },
          createElement('option', { key: 'none', value: '', disabled: false }, 'Choose a node…'),
          (scheme.nodes || []).map((node) => createElement(
            'option',
            {
              key: node.nodeId,
              value: node.nodeId,
              disabled: liveNodeIds.has(node.nodeId),
            },
            liveNodeIds.has(node.nodeId)
              ? `${node.nodeLabel} (already assigned)`
              : node.nodeLabel,
          )),
        ),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Role'),
        createElement(
          'select',
          {
            'aria-label': 'Classification role',
            value: draft.assignmentRole,
            onChange: (event) => setDraft((current) => ({ ...current, assignmentRole: event.target.value })),
          },
          SYMBOL_CLASSIFICATION_ROLES.map((role) => createElement('option', { key: role, value: role }, role)),
        ),
      ),
      createElement(
        'button',
        {
          type: 'submit',
          className: 'action-button primary',
          disabled: !draft.nodeId || decision.busy === `${state.symbolRevisionId}-classification-proposal`,
          'aria-label': 'Submit classification proposal',
        },
        'Propose classification',
      ),
    );
  }

  function renderConceptClassificationDetail(row) {
    return createElement(
      'div',
      { className: 'semantic-review-detail' },
      createElement(
        'div',
        { className: 'detail-heading' },
        createElement(
          'div',
          null,
          createElement('h3', null, conceptLabel(row.concept)),
          createElement('p', null, `${row.schemeCode} · ${row.nodeCode} — ${row.nodeLabel}`),
        ),
        createElement('span', { className: statusClassName(row.status) }, row.status),
      ),
      createElement(SchemeMarker, { schemeCode: row.schemeCode }),
      createElement(
        'div',
        { className: 'fact-grid' },
        createElement(Fact, { label: 'Role', value: row.assignmentRole }),
        createElement(Fact, { label: 'Method', value: row.method }),
        createElement(Fact, { label: 'Proposed', value: formatReviewTimestamp(row.proposedAt) }),
      ),
      createElement(EvidenceBlock, { evidence: row.evidence }),
      // WP1.2 exposes no decision route for a concept->node assignment, so
      // this queue is a read surface. Rendering a control here would offer an
      // act the API cannot perform.
      createElement(
        'p',
        { role: 'note', className: 'form-message semantic-review-blocked' },
        'No decision control is available for concept classifications in this release; this queue is read-only.',
      ),
    );
  }

  function renderMappingDetail(row) {
    return createElement(
      'div',
      { className: 'semantic-review-detail' },
      createElement(
        'div',
        { className: 'detail-heading' },
        createElement(
          'div',
          null,
          createElement('h3', null, `${conceptLabel(row.concept)} → ${row.externalIdentifier}`),
          createElement('p', null, `${row.schemeCode} ${row.schemeVersionLabel} · ${row.externalLabel || 'no external label'}`),
        ),
        createElement('span', { className: statusClassName(row.status) }, row.status),
      ),
      createElement(
        'div',
        { className: 'fact-grid' },
        createElement(Fact, { label: 'Mapping type', value: row.mappingType }),
        createElement(Fact, { label: 'Method', value: row.method }),
        createElement(Fact, { label: 'Proposed', value: formatReviewTimestamp(row.proposedAt) }),
      ),
      createElement(EvidenceBlock, { evidence: row.evidence }),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Verification basis'),
        createElement('input', {
          type: 'text',
          'aria-label': 'Verification basis',
          value: verificationBasis,
          placeholder: 'Why this mapping is trusted',
          onChange: (event) => setVerificationBasis(event.target.value),
        }),
      ),
      createElement(DecisionControls, {
        identifier: mappingIdentifier(row),
        capabilities: row.capabilities,
        busy: decision.busy === row.referenceId,
        onDecide: (targetStatus) => decideMapping(row, targetStatus),
      }),
    );
  }

  function renderRightsDetail(row) {
    return createElement(
      'div',
      { className: 'semantic-review-detail' },
      createElement(
        'div',
        { className: 'detail-heading' },
        createElement(
          'div',
          null,
          createElement('h3', null, row.symbol ? symbolLabel(row.symbol) : row.subjectKind.replace(/_/g, ' ')),
          createElement('p', null, `${row.disposition} · ${row.rightsStatus}`),
        ),
        createElement('span', { className: statusClassName(row.status) }, row.status),
      ),
      createElement(
        'div',
        { className: 'fact-grid' },
        createElement(Fact, { label: 'Determination method', value: row.determinationMethod }),
        createElement(Fact, { label: 'Licence reference', value: row.licenceReference || 'None recorded' }),
        createElement(Fact, { label: 'Proposed', value: formatReviewTimestamp(row.proposedAt) }),
      ),
      createElement(EvidenceBlock, { evidence: row.evidence }),
      createElement(DecisionControls, {
        identifier: rightsIdentifier(row),
        capabilities: row.capabilities,
        verifyLabel: 'Approve',
        verifyStatus: 'approved',
        busy: decision.busy === row.recordId,
        onDecide: (targetStatus) => decideRights(row, targetStatus),
      }),
      row.capabilities?.mustRepropose ? renderRightsProposalForm(row) : null,
    );
  }

  function renderRightsProposalForm(row) {
    return createElement(
      'form',
      {
        className: 'decision-block semantic-review-proposal',
        'aria-label': 'Propose a rights record',
        onSubmit: (event) => {
          event.preventDefault();
          return submitRightsProposal(row);
        },
      },
      createElement('h4', null, 'Propose your own rights record'),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Disposition'),
        createElement(
          'select',
          {
            'aria-label': 'Proposed disposition',
            value: proposal.disposition,
            onChange: (event) => setProposal((current) => ({ ...current, disposition: event.target.value })),
          },
          RIGHTS_DISPOSITIONS.map((value) => createElement('option', { key: value, value }, value)),
        ),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Determination method'),
        createElement(
          'select',
          {
            'aria-label': 'Proposed determination method',
            value: proposal.determinationMethod,
            onChange: (event) => setProposal((current) => ({ ...current, determinationMethod: event.target.value })),
          },
          PROPOSABLE_DETERMINATION_METHODS.map((value) => createElement('option', { key: value, value }, value)),
        ),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Rights status'),
        createElement(
          'select',
          {
            'aria-label': 'Proposed rights status',
            value: proposal.rightsStatus,
            onChange: (event) => setProposal((current) => ({ ...current, rightsStatus: event.target.value })),
          },
          RIGHTS_STATUS_VALUES.map((value) => createElement('option', { key: value, value }, value)),
        ),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Licence reference'),
        createElement('input', {
          type: 'text',
          'aria-label': 'Proposed licence reference',
          value: proposal.licenceReference,
          placeholder: 'Required for a licensed or restricted status',
          onChange: (event) => setProposal((current) => ({ ...current, licenceReference: event.target.value })),
        }),
      ),
      createElement(
        'label',
        { className: 'field' },
        createElement('span', null, 'Reason'),
        createElement('textarea', {
          'aria-label': 'Proposed decision reason',
          rows: 3,
          value: proposal.decisionReason,
          onChange: (event) => setProposal((current) => ({ ...current, decisionReason: event.target.value })),
        }),
      ),
      createElement(
        'button',
        {
          type: 'submit',
          className: 'action-button primary',
          disabled: decision.busy === `${row.recordId}-proposal`,
          'aria-label': 'Submit rights record proposal',
        },
        'Propose record',
      ),
    );
  }
}
