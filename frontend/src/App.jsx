import { useEffect, useMemo, useRef, useState, useTransition } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchReggieQueueControls,
  fetchScottSourceSites,
  fetchHannahPhotoCandidates,
  fetchWhitneyDemandSignals,
  fetchHealth,
  fetchPublishedSymbols,
  fetchWorkspaceDaisyReports,
  fetchWorkspaceQueueItems,
  fetchWorkspaceReviewCases,
  fetchWorkspaceReviewSymbolPropertyOptions,
  processWorkspaceSplitReviewDecisions,
  startHannahCurationSearch,
  startScottSourceSearch,
  startWhitneyDemandScan,
  stopHannahCurationSearch,
  stopScottSourceSearch,
  stopWhitneyDemandScan,
  submitWorkspaceReviewDecision,
  submitExternalSubmission,
  submitPublishedSymbolCommand,
  updateScottSourceSiteIncludeNextRun,
  updateScottSourceSitePrompt,
  updateScottSourceSiteStatus,
  updateWorkspaceReviewSymbolProperties
} from './api.js';
import { appConfig } from './config.js';
import { changeQueue, daisyCoordinationReports, processingActivity, submissionPresets, symbols } from './data.js';

const REVIEW_ITEM_ACTION_OPTIONS = [
  ['approve', 'Approve'],
  ['request_changes', 'Request Changes'],
  ['reject', 'Reject'],
  ['more_evidence', 'More Evidence'],
  ['rename_classify', 'Rename/Classify'],
  ['duplicate', 'Duplicate'],
  ['deleted', 'Delete'],
  ['defer', 'Defer']
];

const REVIEW_CHILD_ACTION_OPTIONS = [
  ['approved', 'Approve'],
  ['request_changes', 'Request Changes'],
  ['rejected', 'Reject'],
  ['more_evidence', 'More Evidence'],
  ['rename_classify', 'Rename/Classify'],
  ['duplicate', 'Duplicate'],
  ['deleted', 'Delete'],
  ['defer', 'Defer']
];

const WORKSPACE_QUEUE_COLUMNS = [
  {
    id: 'intake',
    title: 'Scott',
    subtitle: 'Intake',
    agentId: 'scott',
    tone: 'intake'
  },
  {
    id: 'validation',
    title: 'Vlad',
    subtitle: 'Image Processing',
    agentId: 'vlad',
    tone: 'validation'
  },
  {
    id: 'provenance',
    title: 'Tracy',
    subtitle: 'Provenance',
    agentId: 'tracy',
    tone: 'provenance'
  },
  {
    id: 'classification',
    title: 'Libby',
    subtitle: 'Classification',
    agentId: 'libby',
    tone: 'classification'
  },
  {
    id: 'review_coordination',
    title: 'Daisy',
    subtitle: 'Coordination',
    agentId: 'daisy',
    tone: 'coordination'
  },
  {
    id: 'human_review',
    title: 'Review',
    subtitle: 'Human',
    tone: 'human'
  },
  {
    id: 'publication',
    title: 'Rupert',
    subtitle: 'Publication',
    agentId: 'rupert',
    tone: 'publication'
  },
  {
    id: 'ux_feedback',
    title: 'Ed',
    subtitle: 'UX feedback',
    agentId: 'ed',
    tone: 'feedback'
  },
  {
    id: 'curation',
    title: 'Hannah',
    subtitle: 'Curation',
    agentId: 'hannah',
    tone: 'curation'
  },
  {
    id: 'control_audit',
    title: 'Reggie',
    subtitle: 'Audit / Control',
    agentId: 'reggie',
    tone: 'control'
  },
  {
    id: 'market_intelligence',
    title: 'Whitney',
    subtitle: 'Demand',
    agentId: 'whitney',
    tone: 'intelligence'
  }
];

const WORKSPACE_REFRESH_INTERVAL_MS = 5000;
const PUBLISHED_SYMBOL_SELECTION_LIMIT = 5;
const DEFAULT_HIDDEN_WORKSPACE_STATUSES = new Set(['completed']);
const SCOTT_SOURCE_SITE_PAGE_SIZE = 50;
const SCOTT_SOURCE_SEARCH_DURATION_SECONDS = 120;
const HANNAH_CANDIDATE_PAGE_SIZE = 50;
const HANNAH_CURATION_SEARCH_DURATION_SECONDS = 120;
const WHITNEY_SIGNAL_PAGE_SIZE = 50;
const WHITNEY_DEMAND_SCAN_DURATION_SECONDS = 120;
const SCOTT_SOURCE_COLUMNS = [
  ['url', 'URL'],
  ['status', 'Status'],
  ['includeNextRun', 'Next run'],
  ['sourcePrompt', 'Scott prompt'],
  ['relevanceScore', 'Score'],
  ['title', 'Title'],
  ['domain', 'Domain'],
  ['description', 'Description'],
  ['industry', 'Industry'],
  ['process', 'Process'],
  ['organizationType', 'Organization'],
  ['symbolFormats', 'Formats'],
  ['evidence', 'Evidence'],
  ['firstSeenAt', 'First seen'],
  ['lastSeenAt', 'Last seen'],
  ['lastSessionQueueItemId', 'Last queue item']
];
const SCOTT_SOURCE_STATUS_OPTIONS = [
  ['candidate', 'Candidate'],
  ['low_signal', 'Low_signal'],
  ['ignored', 'Ignore']
];
const HANNAH_CANDIDATE_COLUMNS = [
  ['symbolName', 'Symbol'],
  ['status', 'Status'],
  ['relevanceScore', 'Score'],
  ['rightsStatus', 'Rights'],
  ['licenseLabel', 'License'],
  ['sourceDomain', 'Domain'],
  ['title', 'Title'],
  ['description', 'Feedback'],
  ['sourceUrl', 'Source URL'],
  ['imageUrl', 'Image URL'],
  ['category', 'Category'],
  ['discipline', 'Discipline'],
  ['lastSeenAt', 'Last seen'],
  ['lastSessionQueueItemId', 'Last queue item']
];
const WHITNEY_SIGNAL_COLUMNS = [
  ['signalType', 'Signal'],
  ['marketSegment', 'Segment'],
  ['title', 'Title'],
  ['demandScore', 'Demand'],
  ['confidence', 'Confidence'],
  ['recommendedAction', 'Recommended action'],
  ['status', 'Status'],
  ['discipline', 'Discipline'],
  ['category', 'Category'],
  ['sourceType', 'Source'],
  ['lastSeenAt', 'Last seen'],
  ['lastSessionQueueItemId', 'Last queue item']
];
const WORKSPACE_MONITOR_SCREENS = {
  pipeline: ['intake', 'validation', 'provenance', 'classification', 'review_coordination', 'human_review', 'publication'],
  intelligence: ['curation', 'control_audit', 'market_intelligence', 'ux_feedback']
};
const WORKSPACE_MONITOR_SCREEN_SEQUENCE = ['pipeline', 'intelligence'];

function App() {
  const location = useLocation();
  const isStandardsRoute = location.pathname.startsWith('/standards');

  return (
    <div className={`app-shell ${isStandardsRoute ? 'mode-standards' : 'mode-workspace'}`}>
      <AmbientBackdrop />
      <Header />
      <main className="page-frame">
        <Routes>
          <Route path="/" element={<Navigate to="/workspace" replace />} />
          <Route path="/workspace" element={<WorkspacePage />} />
          <Route path="/reviews" element={<ReviewsPage />} />
          <Route path="/standards" element={<StandardsPage />} />
          <Route path="/standards/submit" element={<SubmissionPage />} />
          <Route path="/support" element={<SupportPage />} />
          <Route path="*" element={<Navigate to="/workspace" replace />} />
        </Routes>
      </main>
    </div>
  );
}

function Header() {
  return (
    <header className="glass-header">
      <div className="brand-block">
        <div className="brand-mark" aria-hidden="true">
          <EngineeringSymbolLogo />
        </div>
        <div>
          <p className="eyebrow">Symbol governance system</p>
          <h1>symgov</h1>
        </div>
      </div>
      <nav className="top-nav" aria-label="Primary navigation">
        <NavLink to="/standards/submit" className={({ isActive }) => navClass(isActive)}>
          Submissions
        </NavLink>
        <NavLink to="/reviews" className={({ isActive }) => navClass(isActive)}>
          Reviews
        </NavLink>
        <NavLink to="/standards" end className={({ isActive }) => navClass(isActive)}>
          Standards
        </NavLink>
        <NavLink to="/support" className={({ isActive }) => navClass(isActive)}>
          Support
        </NavLink>
      </nav>
      <div className="header-actions">
        <div className="build-chip">v{appConfig.version} · {appConfig.build || 'local'}</div>
        <NavLink to="/workspace" className="icon-button" aria-label="Open workspace view">
          <CogIcon />
        </NavLink>
      </div>
    </header>
  );
}

function EngineeringSymbolLogo() {
  return (
    <svg viewBox="0 0 64 64" role="img" aria-label="Engineering valve symbol">
      <title>Engineering valve symbol</title>
      <line x1="8" y1="32" x2="56" y2="32" />
      <polygon points="20,18 32,32 20,46" />
      <polygon points="44,18 32,32 44,46" />
      <circle cx="32" cy="14" r="7" />
      <line x1="32" y1="21" x2="32" y2="32" />
    </svg>
  );
}

function CameraIconMini() {
  return (
    <svg viewBox="0 0 24 24" width="12" height="12" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
      <circle cx="12" cy="13" r="4" />
    </svg>
  );
}

function CogIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2.06 2.06 0 0 1-2.91 2.91l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21a2.06 2.06 0 0 1-4.12 0v-.09a1.7 1.7 0 0 0-1.03-1.56 1.7 1.7 0 0 0-1.88.34l-.06.06a2.06 2.06 0 0 1-2.91-2.91l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2.06 2.06 0 0 1 0-4.12h.09A1.7 1.7 0 0 0 4.6 8.8a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2.06 2.06 0 0 1 2.91-2.91l.06.06a1.7 1.7 0 0 0 1.88.34 1.7 1.7 0 0 0 1.03-1.56V3a2.06 2.06 0 0 1 4.12 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06a2.06 2.06 0 0 1 2.91 2.91l-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.56 1.03H21a2.06 2.06 0 0 1 0 4.12h-.09A1.7 1.7 0 0 0 19.4 15Z" />
    </svg>
  );
}

function StandardsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const [sortState, setSortState] = useState({ key: 'id', direction: 'asc' });
  const [columnFilters, setColumnFilters] = useState({});
  const [facetFilters, setFacetFilters] = useState({});
  const [activeId, setActiveId] = useState('');
  const [selectedSymbolIds, setSelectedSymbolIds] = useState([]);
  const [commandDialog, setCommandDialog] = useState(null);
  const [commandComment, setCommandComment] = useState('');
  const [commandStatus, setCommandStatus] = useState({ mode: '', message: '' });
  const [submittingCommand, setSubmittingCommand] = useState(false);
  const [displayCount, setDisplayCount] = useState(60);
  const [standardsState, setStandardsState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading live published records…' : 'No API root configured. Showing seeded published records.',
    items: appConfig.apiRoot ? [] : symbols
  });
  const standardsGridRef = useRef(null);
  const standardsSymbols = standardsState.items.length ? standardsState.items : symbols;
  const requestedSymbolId = searchParams.get('symbol') || '';
  const standardsColumns = [
    ['id', 'ID'],
    ['name', 'Name'],
    ['lastUpdatedAt', 'Last update'],
    ['photoStatus', 'Photos'],
    ['commentStatus', 'Comments'],
    ['category', 'Category'],
    ['discipline', 'Discipline'],
    ['pack', 'Pack'],
    ['revision', 'Revision'],
    ['effectiveDate', 'Effective']
  ];
  const facetDefinitions = [
    ['category', 'Category'],
    ['discipline', 'Discipline'],
    ['pack', 'Pack'],
    ['format', 'Format'],
    ['symbolFamily', 'Symbol family']
  ];

  const facetOptions = useMemo(() => {
    return facetDefinitions.map(([key, label]) => {
      const values = Array.from(
        new Set(
          standardsSymbols
            .flatMap((symbol) => getSymbolFacetValues(symbol, key))
            .map((value) => String(value || '').trim())
            .filter(Boolean)
        )
      ).sort((a, b) => a.localeCompare(b));
      return { key, label, values };
    });
  }, [standardsSymbols]);

  const filteredSymbols = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    const filtered = standardsSymbols.filter((symbol) => {
      const matchesSearch = !normalizedQuery || [
        displaySymbolId(symbol),
        symbol.id,
        symbol.symbolId,
        displaySymbolName(symbol),
        symbol.category,
        symbol.discipline,
        symbol.pack,
        symbol.packCode,
        symbol.pageCode,
        ...(symbol.keywords || [])
      ].some((value) =>
        String(value || '')
          .toLowerCase()
          .includes(normalizedQuery)
      );

      if (!matchesSearch) {
        return false;
      }

      const matchesColumns = Object.entries(columnFilters).every(([key, value]) => {
        const normalizedValue = String(value || '').trim().toLowerCase();
        if (!normalizedValue) {
          return true;
        }
        return getSymbolField(symbol, key).toLowerCase().includes(normalizedValue);
      });

      if (!matchesColumns) {
        return false;
      }

      return Object.entries(facetFilters).every(([key, selected]) => {
        if (!selected?.length) {
          return true;
        }
        const values = getSymbolFacetValues(symbol, key).map((value) => String(value || '').trim());
        return selected.some((value) => values.includes(value));
      });
    });

    return filtered.sort((left, right) => {
      const leftValue = getSymbolField(left, sortState.key);
      const rightValue = getSymbolField(right, sortState.key);
      const result = leftValue.localeCompare(rightValue, undefined, { numeric: true, sensitivity: 'base' });
      return sortState.direction === 'asc' ? result : -result;
    });
  }, [standardsSymbols, query, columnFilters, facetFilters, sortState]);

  const visibleSymbols = filteredSymbols.slice(0, displayCount);
  const selectedSymbols = selectedSymbolIds
    .map((symbolId) => standardsSymbols.find((symbol) => symbol.id === symbolId || symbol.symbolId === symbolId))
    .filter(Boolean);
  const selectionLimitReached = selectedSymbolIds.length >= PUBLISHED_SYMBOL_SELECTION_LIMIT;

  useEffect(() => {
    let cancelled = false;

    fetchPublishedSymbols().then((result) => {
      if (cancelled) {
        return;
      }

      if (result.ok) {
        setStandardsState({
          loading: false,
          mode: 'live',
          message: result.items.length ? result.message : 'No live published records are currently available.',
          items: result.items
        });
        return;
      }

      setStandardsState({
        loading: false,
        mode: result.mode,
        message: result.message,
        items: symbols
      });
    });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!filteredSymbols.some((symbol) => symbol.id === activeId)) {
      setActiveId('');
    }
  }, [filteredSymbols, activeId]);

  useEffect(() => {
    const available = new Set(standardsSymbols.map((symbol) => symbol.id));
    setSelectedSymbolIds((current) => current.filter((symbolId) => available.has(symbolId)));
  }, [standardsSymbols]);

  useEffect(() => {
    if (!requestedSymbolId || !standardsSymbols.length) {
      return;
    }
    const normalizedRequested = requestedSymbolId.trim().toLowerCase();
    const requestedSymbol = standardsSymbols.find((symbol) =>
      [symbol.id, symbol.slug, symbol.symbolId, symbol.pageCode]
        .map((value) => String(value || '').trim().toLowerCase())
        .includes(normalizedRequested)
    );
    if (requestedSymbol) {
      setActiveId(requestedSymbol.id);
      setColumnFilters({});
      setFacetFilters({});
      setQuery('');
    }
  }, [requestedSymbolId, standardsSymbols]);

  useEffect(() => {
    setDisplayCount(60);
  }, [query, columnFilters, facetFilters, sortState]);

  const activeSymbol = filteredSymbols.find((symbol) => symbol.id === activeId) || null;

  function selectSymbol(symbolId) {
    setActiveId(symbolId);
    setSearchParams((current) => {
      const next = new URLSearchParams(current);
      if (symbolId) {
        next.set('symbol', symbolId);
      } else {
        next.delete('symbol');
      }
      return next;
    }, { replace: true });
  }

  function toggleSymbolSelection(symbolId) {
    setCommandStatus({ mode: '', message: '' });
    setSelectedSymbolIds((current) => {
      if (current.includes(symbolId)) {
        return current.filter((value) => value !== symbolId);
      }
      if (current.length >= PUBLISHED_SYMBOL_SELECTION_LIMIT) {
        setCommandStatus({ mode: 'error', message: `Select no more than ${PUBLISHED_SYMBOL_SELECTION_LIMIT} symbols at a time.` });
        return current;
      }
      return [...current, symbolId];
    });
  }

  function openPublishedCommandDialog(command) {
    if (!selectedSymbols.length) {
      return;
    }
    setCommandDialog(command);
    setCommandComment('');
    setCommandStatus({ mode: '', message: '' });
  }

  function closePublishedCommandDialog() {
    if (submittingCommand) {
      return;
    }
    setCommandDialog(null);
    setCommandComment('');
  }

  async function handleSubmitPublishedCommand() {
    if (!commandDialog || submittingCommand) {
      return;
    }
    const comment = commandComment.trim();
    if (!comment) {
      setCommandStatus({ mode: 'error', message: 'Add a comment before submitting.' });
      return;
    }
    setSubmittingCommand(true);
    setCommandStatus({ mode: 'info', message: commandDialog === 'comment' ? 'Posting comment…' : 'Sending selected symbol(s) for review…' });
    const submittedCommand = commandDialog;
    const submittedSymbolIds = selectedSymbols.map((symbol) => symbol.id);
    setCommandDialog(null);
    setCommandComment('');
    try {
      const result = await submitPublishedSymbolCommand({
        command: submittedCommand,
        symbolIds: submittedSymbolIds,
        comment
      });
      setStandardsState((current) => ({
        ...current,
        items: current.items.map((symbol) =>
          submittedSymbolIds.includes(symbol.id)
            ? { ...symbol, hasComments: true, commentCount: Number(symbol.commentCount || 0) + 1 }
            : symbol
        )
      }));
      setCommandStatus({ mode: 'success', message: result?.message || 'Published symbol command submitted.' });
      setSelectedSymbolIds([]);
    } catch (error) {
      setCommandStatus({ mode: 'error', message: error instanceof Error ? error.message : 'Published symbol command failed.' });
    } finally {
      setSubmittingCommand(false);
    }
  }

  function handleStandardsGridKeyDown(event) {
    if (event.defaultPrevented) {
      return;
    }

    if (!filteredSymbols.length) {
      return;
    }

    if (!['ArrowDown', 'ArrowUp', 'j', 'k', 'Home', 'End'].includes(event.key)) {
      return;
    }

    const activeElement = document.activeElement;
    const activeTag = activeElement?.tagName;
    const isEditableElement = ['INPUT', 'TEXTAREA', 'SELECT'].includes(activeTag) || activeElement?.isContentEditable;
    if (isEditableElement) {
      return;
    }

    // If nothing is selected yet, treat the visually-displayed fallback (first row)
    // as the current row so the very first arrow press moves to row 2 — matches
    // what a user sees in the detail panel after page load.
    const effectiveActiveId = activeId || filteredSymbols[0]?.id || '';
    if (!effectiveActiveId) {
      return;
    }

    const currentIndex = filteredSymbols.findIndex((symbol) => symbol.id === effectiveActiveId);
    if (currentIndex < 0) {
      return;
    }

    let nextIndex = currentIndex;
    if (event.key === 'ArrowDown' || event.key === 'j') {
      // From the "no selection yet" state, ArrowDown should select the first row
      // (not skip past it to row 2).
      nextIndex = activeId ? Math.min(currentIndex + 1, filteredSymbols.length - 1) : currentIndex;
    } else if (event.key === 'ArrowUp' || event.key === 'k') {
      nextIndex = Math.max(currentIndex - 1, 0);
    } else if (event.key === 'Home') {
      nextIndex = 0;
    } else if (event.key === 'End') {
      nextIndex = filteredSymbols.length - 1;
    }

    // If we materialized the implicit selection on ArrowDown, we still want to commit it.
    const shouldCommit = !activeId || nextIndex !== currentIndex;
    if (!shouldCommit) {
      return;
    }

    event.preventDefault();
    const nextSymbol = filteredSymbols[nextIndex];
    if (nextIndex >= visibleSymbols.length) {
      setDisplayCount((current) => Math.min(Math.max(current + 40, nextIndex + 1), filteredSymbols.length));
    }
    selectSymbol(nextSymbol.id);
  }

  // Also listen at the document level so arrow keys work regardless of which
  // element inside the Standards page currently has focus (the per-row tabindex
  // means real browsers — particularly Edge/Firefox — leave focus on the <tr>
  // after a click, and key events on the row may not always bubble through
  // React's grid handler reliably).
  const standardsKeyHandlerRef = useRef(handleStandardsGridKeyDown);
  standardsKeyHandlerRef.current = handleStandardsGridKeyDown;
  useEffect(() => {
    function onDocumentKeyDown(event) {
      standardsKeyHandlerRef.current?.(event);
    }
    document.addEventListener('keydown', onDocumentKeyDown);
    return () => {
      document.removeEventListener('keydown', onDocumentKeyDown);
    };
  }, []);

  useEffect(() => {
    if (!activeId) {
      return;
    }
    const row = standardsGridRef.current?.querySelector(`tr[data-symbol-id="${activeId}"]`);
    if (row) {
      row.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    }
  }, [activeId, visibleSymbols]);

  function updateColumnFilter(key, value) {
    setColumnFilters((current) => ({ ...current, [key]: value }));
  }

  function toggleFacetValue(key, value) {
    setFacetFilters((current) => {
      const selected = new Set(current[key] || []);
      if (selected.has(value)) {
        selected.delete(value);
      } else {
        selected.add(value);
      }
      return { ...current, [key]: Array.from(selected) };
    });
  }

  function toggleSort(key) {
    setSortState((current) => ({
      key,
      direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc'
    }));
  }

  function handleGridScroll(event) {
    const element = event.currentTarget;
    if (element.scrollTop + element.clientHeight >= element.scrollHeight - 160) {
      setDisplayCount((current) => Math.min(current + 40, filteredSymbols.length));
    }
  }

  return (
    <section className="experience-shell">
      <div className="hero-panel glass-panel standards-hero page-title-row">
        <div>
          <p className="eyebrow">Published-only Standards View</p>
          <h2>Browse approved symbols</h2>
        </div>
        <label className="field search-field" aria-label="Search published symbols">
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by symbol, pack, page, or guidance topic"
          />
        </label>
      </div>
      <p className={`page-status-text status-${standardsState.mode}`}>
        {standardsState.loading ? 'Loading published symbols...' : `${standardsState.message} Showing ${filteredSymbols.length} records.`}
      </p>

      <div className={`standards-browser-grid ${activeId && activeSymbol ? 'has-detail' : ''}`}>
        <section className="glass-panel pane facets-panel">
          <SectionHeading title="Filter symbols" subtitle="Narrow by properties" />
          {facetOptions.map((facet) => (
            <div key={facet.key} className="facet-group">
              <h4>{facet.label}</h4>
              {facet.values.length ? (
                facet.values.map((value) => (
                  <label key={`${facet.key}-${value}`} className="checkbox-row">
                    <input
                      type="checkbox"
                      checked={(facetFilters[facet.key] || []).includes(value)}
                      onChange={() => toggleFacetValue(facet.key, value)}
                    />
                    <span>{value}</span>
                  </label>
                ))
              ) : (
                <p className="muted-text">No values yet</p>
              )}
            </div>
          ))}
        </section>

        <section className="glass-panel pane standards-grid-panel">
          <div className="standards-result-meta">
            <strong>{filteredSymbols.length} approved symbols</strong>
            <span>{visibleSymbols.length < filteredSymbols.length ? `Showing ${visibleSymbols.length}; scroll for more` : 'All visible'}</span>
          </div>
          <div className="published-command-bar">
            <div>
              <strong>{selectedSymbols.length} selected</strong>
              <span>Select up to {PUBLISHED_SYMBOL_SELECTION_LIMIT} published symbols.</span>
            </div>
            <div className="command-button-row">
              <button
                type="button"
                className="action-button secondary compact"
                disabled={!selectedSymbols.length}
                onClick={() => openPublishedCommandDialog('comment')}
              >
                Comment
              </button>
              <button
                type="button"
                className="action-button compact"
                disabled={!selectedSymbols.length}
                onClick={() => openPublishedCommandDialog('send_for_review')}
              >
                Send for Review
              </button>
            </div>
          </div>
          {commandStatus.message ? (
            <p className={`inline-status ${commandStatus.mode || 'info'}`}>{commandStatus.message}</p>
          ) : null}
          <div
            className="approved-symbol-grid"
            ref={standardsGridRef}
            onScroll={handleGridScroll}
            onKeyDown={handleStandardsGridKeyDown}
            tabIndex={0}
            role="region"
            aria-label="Published symbols table"
          >
            <table>
              <thead>
                <tr>
                  <th aria-label="Select symbols" className="select-column">Select</th>
                  <th>Preview</th>
                  {standardsColumns.map(([key, label]) => (
                    <th key={key}>
                      <button type="button" className="column-sort-button" onClick={() => toggleSort(key)}>
                        {label}
                        {sortState.key === key ? <span>{sortState.direction === 'asc' ? '↑' : '↓'}</span> : null}
                      </button>
                      <input
                        aria-label={`Filter ${label}`}
                        value={columnFilters[key] || ''}
                        onChange={(event) => updateColumnFilter(key, event.target.value)}
                        placeholder="Filter"
                      />
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {visibleSymbols.map((symbol) => (
                  <tr
                    key={symbol.id}
                    data-symbol-id={symbol.id}
                    className={symbol.id === activeId ? 'active' : ''}
                    tabIndex={0}
                    aria-selected={symbol.id === activeId}
                    onClick={() => {
                      selectSymbol(symbol.id);
                    }}
                    onKeyDown={(event) => {
                      if (event.key === 'Enter' || event.key === ' ') {
                        event.preventDefault();
                        selectSymbol(symbol.id);
                      }
                    }}
                  >
                    <td className="select-column" onClick={(event) => event.stopPropagation()}>
                      <input
                        type="checkbox"
                        aria-label={`Select ${displaySymbolId(symbol)}`}
                        checked={selectedSymbolIds.includes(symbol.id)}
                        disabled={!selectedSymbolIds.includes(symbol.id) && selectionLimitReached}
                        onChange={() => toggleSymbolSelection(symbol.id)}
                      />
                    </td>
                    <td className="preview-cell">
                      <div className="preview-indicator-wrapper">
                        <PublishedSymbolPreview symbol={symbol} />
                        {symbol.supplementalPhotos?.length > 0 && (
                          <div className="photo-indicator-dot" title="Has equipment photos">
                            <CameraIconMini />
                          </div>
                        )}
                      </div>
                    </td>
                    {standardsColumns.map(([key]) => (
                      <td key={`${symbol.id}-${key}`}>
                        {key === 'photoStatus' ? (
                          <span
                            className={`table-icon-indicator ${Array.isArray(symbol.supplementalPhotos) && symbol.supplementalPhotos.length ? 'positive' : 'muted'}`}
                            title={Array.isArray(symbol.supplementalPhotos) && symbol.supplementalPhotos.length ? `${symbol.supplementalPhotos.length} photo(s)` : 'No photos yet'}
                            aria-label={Array.isArray(symbol.supplementalPhotos) && symbol.supplementalPhotos.length ? `${symbol.supplementalPhotos.length} photo(s)` : 'No photos yet'}
                          >
                            📷
                          </span>
                        ) : key === 'commentStatus' ? (
                          <span
                            className={`table-icon-indicator ${symbol.hasComments || Number(symbol.commentCount || 0) > 0 ? 'positive' : 'muted'}`}
                            title={symbol.hasComments || Number(symbol.commentCount || 0) > 0 ? `${symbol.commentCount || 1} comment(s)` : 'No comments yet'}
                            aria-label={symbol.hasComments || Number(symbol.commentCount || 0) > 0 ? `${symbol.commentCount || 1} comment(s)` : 'No comments yet'}
                          >
                            💬
                          </span>
                        ) : (
                          getSymbolField(symbol, key) || 'Pending'
                        )}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
            {!visibleSymbols.length ? (
              <EmptyState title="No published records" body="Adjust the search or filters to find approved symbols." />
            ) : null}
          </div>
        </section>

        {activeId && activeSymbol ? (
          <aside className="standards-detail-panel" role="dialog" aria-modal="false" aria-label="Published symbol details">
            <section className="glass-panel pane standards-detail-drawer">
            <div className="detail-heading">
              <div>
                <p className="eyebrow">Approved symbol</p>
                <h3>
                  {displaySymbolId(activeSymbol)} · {displaySymbolName(activeSymbol)}
                </h3>
                <p>{activeSymbol.summary}</p>
              </div>
              <button type="button" className="action-button secondary compact" onClick={() => selectSymbol('')}>
                Close
              </button>
            </div>
            <div className="symbol-stage">
              <PublishedSymbolPreview symbol={activeSymbol} large />
            </div>
            <SupplementalPhotoStrip photos={activeSymbol.supplementalPhotos || []} />
            <div className="fact-grid detail-list">
              <Fact label="Status" value={activeSymbol.status || 'Published'} />
              <Fact label="Revision" value={activeSymbol.revision} />
              <Fact label="Last update" value={formatPublishedDate(activeSymbol.lastUpdatedAt || activeSymbol.revisionCreatedAt)} />
              <Fact label="Effective" value={formatPublishedDate(activeSymbol.effectiveDate)} />
              <Fact label="Published Page" value={activeSymbol.pageCode} />
              <Fact label="Pack" value={activeSymbol.pack} />
              <Fact label="Category" value={activeSymbol.category} />
              <Fact label="Discipline" value={activeSymbol.discipline} />
              <Fact label="Format" value={activeSymbol.format || activeSymbol.contentType || 'Pending'} />
            </div>
            <div className="copy-block">
              <h4>Governance rationale</h4>
              <p>{activeSymbol.rationale || 'No rationale has been published for this symbol yet.'}</p>
            </div>
            <div className="tag-row">
              {(activeSymbol.downloads || ['Download options coming later']).map((download) => (
                <span key={download} className="tag-chip">
                  {download}
                </span>
              ))}
            </div>
            </section>
          </aside>
        ) : null}
      </div>
      {commandDialog ? (
        <div className="modal-backdrop" role="presentation" onClick={closePublishedCommandDialog}>
          <div className="modal-card published-command-dialog" role="dialog" aria-modal="true" aria-labelledby="published-command-title" onClick={(event) => event.stopPropagation()}>
            <div className="detail-heading">
              <div>
                <p className="eyebrow">Published symbol command</p>
                <h3 id="published-command-title">{commandDialog === 'comment' ? 'Comment' : 'Send for Review'}</h3>
              </div>
              <button type="button" className="action-button secondary compact" onClick={closePublishedCommandDialog} disabled={submittingCommand}>
                Cancel
              </button>
            </div>
            <ul className="selected-symbol-list">
              {selectedSymbols.map((symbol) => (
                <li key={symbol.id}>
                  <strong>{displaySymbolId(symbol)}</strong>
                  <span>{displaySymbolName(symbol)}</span>
                </li>
              ))}
            </ul>
            <label className="field comment-field">
              <span>Comment</span>
              <textarea
                value={commandComment}
                onChange={(event) => setCommandComment(event.target.value)}
                placeholder="Describe what needs addressing. Ed will manage follow-up."
                rows={5}
              />
            </label>
            <div className="dialog-actions">
              <button type="button" className="action-button secondary" onClick={closePublishedCommandDialog} disabled={submittingCommand}>
                Cancel
              </button>
              <button type="button" className="action-button" onClick={handleSubmitPublishedCommand} disabled={submittingCommand || !commandComment.trim()}>
                {submittingCommand ? 'Sending…' : commandDialog === 'comment' ? 'Post' : 'Send'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}

function getSymbolField(symbol, key) {
  if (!symbol) {
    return '';
  }
  if (key === 'id') {
    return displaySymbolId(symbol);
  }
  if (key === 'name') {
    return displaySymbolName(symbol);
  }
  if (key === 'format') {
    return symbol.format || symbol.contentType || (symbol.downloads || []).join(', ');
  }
  if (key === 'photoStatus') {
    const count = Array.isArray(symbol.supplementalPhotos) ? symbol.supplementalPhotos.length : 0;
    return count ? `${count} added` : '';
  }
  if (key === 'commentStatus') {
    return Number(symbol.commentCount || 0) > 0 || symbol.hasComments ? `${symbol.commentCount || 1} comment${Number(symbol.commentCount || 1) === 1 ? '' : 's'}` : '';
  }
  if (key === 'lastUpdatedAt' || key === 'effectiveDate' || key === 'revisionCreatedAt') {
    return formatPublishedDate(symbol[key]);
  }
  if (key === 'symbolFamily') {
    return symbol.symbolFamily || symbol.family || symbol.category || '';
  }
  const value = symbol[key];
  if (Array.isArray(value)) {
    return value.join(', ');
  }
  return String(value || '');
}

const PUBLISHED_DATE_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/London',
  day: '2-digit',
  month: 'short',
  year: 'numeric'
});

function formatPublishedDate(value) {
  if (!value) {
    return '';
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return String(value || '');
  }
  return PUBLISHED_DATE_FORMATTER.format(date).replace(',', '');
}

function displaySymbolName(record) {
  if (!record) {
    return '';
  }
  return (
    record.name ||
    record.payload?.name ||
    record.payload?.canonical_name ||
    record.symbolProperties?.name ||
    record.proposedSymbolName ||
    record.title ||
    ''
  );
}

function displaySymbolId(record) {
  if (!record) {
    return '';
  }
  const packageId = record.packageDisplayId || record.package_display_id;
  const sequence = record.packageSymbolSequence ?? record.package_symbol_sequence;
  return (
    record.displayName ||
    record.display_name ||
    record.workspaceDisplayName ||
    record.workspace_display_name ||
    (packageId && sequence != null ? `${packageId}-${sequence}` : '') ||
    packageId ||
    record.symbolDisplayId ||
    record.symbol_display_id ||
    record.symbolId ||
    record.proposedSymbolId ||
    record.id ||
    ''
  );
}

function displayReviewOriginalFilename(record) {
  if (!record) {
    return '';
  }
  return (
    record.originalFilename ||
    record.original_filename ||
    record.sourceFileName ||
    record.source_file_name ||
    record.parentFileName ||
    record.parent_file_name ||
    record.fileName ||
    record.file_name ||
    record.children?.[0]?.parentFileName ||
    record.children?.[0]?.fileName ||
    ''
  );
}

function getSymbolFacetValues(symbol, key) {
  if (key === 'format') {
    const formats = [];
    if (symbol.format) {
      formats.push(symbol.format);
    }
    if (symbol.contentType) {
      formats.push(symbol.contentType);
    }
    (symbol.downloads || []).forEach((download) => formats.push(download));
    return formats;
  }
  const value = getSymbolField(symbol, key);
  return value ? [value] : [];
}

function WorkspacePage() {
  const navigate = useNavigate();
  const [activeWorkspaceTab, setActiveWorkspaceTab] = useState('agents');
  const [activeMonitorScreen, setActiveMonitorScreen] = useState('pipeline');
  const [query, setQuery] = useState('');
  const [columnStatusFilters, setColumnStatusFilters] = useState({});
  const [reviewState, setReviewState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading live review cases...' : 'No API root configured. Showing seeded review cases.',
    items: appConfig.apiRoot ? [] : changeQueue
  });
  const [daisyState, setDaisyState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading Daisy coordination...' : 'No API root configured. Showing seeded coordination.',
    items: appConfig.apiRoot ? [] : daisyCoordinationReports
  });
  const [queueState, setQueueState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading live queue items...' : 'No API root configured. Showing seeded queue activity.',
    items: appConfig.apiRoot ? [] : processingActivity
  });
  const [reggieState, setReggieState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading Reggie controls...' : 'No API root configured. Reggie controls unavailable.',
    items: []
  });
  const [stoppedSourceSearches, setStoppedSourceSearches] = useState({});
  const [lastWorkspaceRefresh, setLastWorkspaceRefresh] = useState(null);
  const {
    searchState,
    sourcesSort,
    sourceFilters,
    sourcesState,
    handleStartScottSearch,
    handleStopScottSearch,
    handleSourceSort,
    updateSourceFilter,
    updateSourceSitePrompt,
    updateSourceSiteIncludeNextRun,
    updateSourceSiteStatus,
    handleScottSourcesScroll
  } = useScottSourceDiscoveryControls({
    enabled: Boolean(appConfig.apiRoot),
    sourcesActive: activeWorkspaceTab === 'sources',
    onSearchStarted: (queueItemId) => {
      setStoppedSourceSearches((current) => {
        if (!queueItemId || !current[queueItemId]) {
          return current;
        }
        const next = { ...current };
        delete next[queueItemId];
        return next;
      });
    },
    onSearchStopped: ({ queueItemId, remainingSeconds }) => {
      if (!queueItemId || queueItemId === 'starting') {
        return;
      }
      setStoppedSourceSearches((current) => ({
        ...current,
        [queueItemId]: {
          remainingSeconds,
          label: `STOPPED ${formatCountdown(remainingSeconds)}`
        }
      }));
    }
  });
  const searchIsRunning = Boolean(searchState.result && searchState.remainingSeconds > 0 && searchState.result.status === 'searching');
  const searchDisabled = searchState.pending || searchIsRunning || searchState.stopping || !appConfig.apiRoot;
  const searchStatusText = scottSearchStatusText(searchState);
  const {
    curationState,
    candidatesSort,
    candidateFilters,
    candidatesState,
    handleStartHannahCuration,
    handleStopHannahCuration,
    handleCandidateSort,
    updateCandidateFilter,
    handleHannahCandidatesScroll
  } = useHannahCurationControls({
    enabled: Boolean(appConfig.apiRoot),
    curationActive: activeWorkspaceTab === 'curation'
  });
  const curationIsRunning = Boolean(curationState.result && curationState.remainingSeconds > 0 && curationState.result.status === 'searching');
  const curationDisabled = curationState.pending || curationIsRunning || curationState.stopping || !appConfig.apiRoot;
  const curationStatusText = hannahCurationStatusText(curationState);
  const {
    scanState,
    signalsSort,
    signalFilters,
    signalsState,
    handleStartWhitneyDemandScan,
    handleStopWhitneyDemandScan,
    handleSignalSort,
    updateSignalFilter,
    handleWhitneySignalsScroll
  } = useWhitneyDemandSensingControls({
    enabled: Boolean(appConfig.apiRoot),
    intelligenceActive: activeWorkspaceTab === 'intelligence'
  });
  const scanIsRunning = Boolean(scanState.result && scanState.remainingSeconds > 0 && scanState.result.status === 'sensing');
  const scanDisabled = scanState.pending || scanIsRunning || scanState.stopping || !appConfig.apiRoot;
  const scanStatusText = whitneyDemandScanStatusText(scanState);

  useEffect(() => {
    let cancelled = false;
    let refreshTimer = null;

    const stopRefreshTimer = () => {
      if (refreshTimer !== null) {
        window.clearInterval(refreshTimer);
        refreshTimer = null;
      }
    };

    const refreshWorkspaceMonitor = () => {
      if (document.hidden) {
        return;
      }

      Promise.all([fetchWorkspaceQueueItems(), fetchWorkspaceReviewCases(), fetchWorkspaceDaisyReports(), fetchReggieQueueControls()]).then(([queueResult, reviewResult, daisyResult, reggieResult]) => {
        if (cancelled) {
          return;
        }

        setQueueState({
          loading: false,
          mode: queueResult.ok ? 'live' : 'seeded',
          message: queueResult.ok
            ? queueResult.items.length
              ? queueResult.message
              : 'No live agent queue items are currently open.'
            : `${queueResult.message} Showing seeded queue activity.`,
          items: queueResult.ok ? queueResult.items : processingActivity
        });

        setReviewState({
          loading: false,
          mode: reviewResult.ok ? 'live' : 'seeded',
          message: reviewResult.ok
            ? reviewResult.items.length
              ? reviewResult.message
              : 'No live review cases are currently open.'
            : `${reviewResult.message} Showing seeded processing activity.`,
          items: reviewResult.ok ? reviewResult.items : changeQueue
        });

        setDaisyState({
          loading: false,
          mode: daisyResult.ok ? 'live' : 'seeded',
          message: daisyResult.ok
            ? daisyResult.items.length
              ? daisyResult.message
              : 'No live Daisy coordination reports are available yet.'
            : `${daisyResult.message} Showing seeded coordination.`,
          items: daisyResult.ok ? daisyResult.items : daisyCoordinationReports
        });

        setReggieState({
          loading: false,
          mode: reggieResult.ok ? 'live' : 'seeded',
          message: reggieResult.ok ? reggieResult.message : `${reggieResult.message} Reggie controls unavailable.`,
          items: reggieResult.ok ? reggieResult.items : [],
          summary: reggieResult.summary || null
        });

        setLastWorkspaceRefresh(new Date());
      });
    };

    const startRefreshTimer = () => {
      if (!appConfig.apiRoot || document.hidden || refreshTimer !== null) {
        return;
      }

      refreshTimer = window.setInterval(refreshWorkspaceMonitor, WORKSPACE_REFRESH_INTERVAL_MS);
    };

    const handleVisibilityChange = () => {
      if (document.hidden) {
        stopRefreshTimer();
        return;
      }

      refreshWorkspaceMonitor();
      startRefreshTimer();
    };

    refreshWorkspaceMonitor();

    if (!appConfig.apiRoot) {
      return () => {
        cancelled = true;
      };
    }

    startRefreshTimer();
    document.addEventListener('visibilitychange', handleVisibilityChange);

    return () => {
      cancelled = true;
      stopRefreshTimer();
      document.removeEventListener('visibilitychange', handleVisibilityChange);
    };
  }, []);

  useEffect(() => {
    const closeOpenStatusFilters = (event) => {
      if (event.target instanceof Element && event.target.closest('.monitor-status-filter')) {
        return;
      }

      document.querySelectorAll('.monitor-status-filter[open]').forEach((filter) => {
        filter.removeAttribute('open');
      });
    };

    document.addEventListener('pointerdown', closeOpenStatusFilters);

    return () => {
      document.removeEventListener('pointerdown', closeOpenStatusFilters);
    };
  }, []);

  const refreshSummary = useMemo(() => {
    if (queueState.loading || reviewState.loading) {
      return 'Loading...';
    }

    const refreshLabel = lastWorkspaceRefresh
      ? `Updated ${lastWorkspaceRefresh.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`
      : 'Awaiting first refresh';

    if (queueState.mode === 'live' || reviewState.mode === 'live') {
      return `${refreshLabel} · Auto-refresh 5s · ${queueState.message} · ${reviewState.message} · ${reggieState.message}`;
    }

    return `${refreshLabel} · ${queueState.message} · ${reviewState.message} · ${reggieState.message}`;
  }, [
    lastWorkspaceRefresh,
    queueState.loading,
    queueState.message,
    queueState.mode,
    reggieState.message,
    reviewState.loading,
    reviewState.message,
    reviewState.mode
  ]);

  const monitorItems = useMemo(
    () => buildWorkspaceMonitorItems(queueState.items, queueState.mode, reviewState.items, daisyState.items, stoppedSourceSearches, reggieState.items),
    [queueState.items, queueState.mode, reviewState.items, daisyState.items, stoppedSourceSearches, reggieState.items]
  );
  const monitorScreenColumns = useMemo(() => {
    const columnById = new Map(monitorItems.map((column) => [column.id, column]));
    return (WORKSPACE_MONITOR_SCREENS[activeMonitorScreen] || WORKSPACE_MONITOR_SCREENS.pipeline)
      .map((columnId) => columnById.get(columnId))
      .filter(Boolean);
  }, [activeMonitorScreen, monitorItems]);
  const statusOptionsByColumn = useMemo(() => buildWorkspaceStatusOptions(monitorItems), [monitorItems]);
  const filteredColumns = useMemo(
    () => filterWorkspaceMonitorColumns(monitorScreenColumns, query, columnStatusFilters),
    [monitorScreenColumns, query, columnStatusFilters]
  );
  const handleColumnStatusToggle = (columnId, statusKey) => {
    setColumnStatusFilters((current) => {
      const nextColumn = {
        ...(current[columnId] || {}),
        [statusKey]: !isWorkspaceStatusVisible(statusKey, current[columnId], columnId)
      };

      return {
        ...current,
        [columnId]: nextColumn
      };
    });
  };
  const openReviewFromWorkspace = (item) => {
    if (!item.reviewCaseId) {
      return;
    }

    navigate(`/reviews?review=${encodeURIComponent(item.reviewCaseId)}`);
  };
  const openPublishedFromWorkspace = (item) => {
    const standardsPath = item.publishedStandardsPath || (item.publishedSymbolId ? `/standards?symbol=${encodeURIComponent(item.publishedSymbolId)}` : '');
    if (!standardsPath) {
      return;
    }

    navigate(standardsPath);
  };
  const goToNextMonitorScreen = () => {
    const currentIndex = WORKSPACE_MONITOR_SCREEN_SEQUENCE.indexOf(activeMonitorScreen);
    const nextIndex = currentIndex < WORKSPACE_MONITOR_SCREEN_SEQUENCE.length - 1 ? currentIndex + 1 : 0;
    setActiveMonitorScreen(WORKSPACE_MONITOR_SCREEN_SEQUENCE[nextIndex]);
  };
  const goToPreviousMonitorScreen = () => {
    const currentIndex = WORKSPACE_MONITOR_SCREEN_SEQUENCE.indexOf(activeMonitorScreen);
    const previousIndex = currentIndex > 0 ? currentIndex - 1 : WORKSPACE_MONITOR_SCREEN_SEQUENCE.length - 1;
    setActiveMonitorScreen(WORKSPACE_MONITOR_SCREEN_SEQUENCE[previousIndex]);
  };
  const onFirstMonitorScreen = activeMonitorScreen === WORKSPACE_MONITOR_SCREEN_SEQUENCE[0];
  return (
    <section className="experience-shell queue-monitor-shell">
      <div className="workspace-titlebar glass-panel">
        <div>
          <p className="eyebrow">ADMIN WORKSPACE</p>
          <h2>Admin Workspace</h2>
        </div>
        <div className="workspace-titlebar-tools">
          <div className="workspace-tab-list" role="tablist" aria-label="Workspace view">
            <button
              type="button"
              role="tab"
              aria-selected={activeWorkspaceTab === 'agents'}
              className={`workspace-tab ${activeWorkspaceTab === 'agents' ? 'active' : ''}`}
              onClick={() => setActiveWorkspaceTab('agents')}
            >
              Agents
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeWorkspaceTab === 'sources'}
              className={`workspace-tab ${activeWorkspaceTab === 'sources' ? 'active' : ''}`}
              onClick={() => setActiveWorkspaceTab('sources')}
            >
              Sources
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeWorkspaceTab === 'curation'}
              className={`workspace-tab ${activeWorkspaceTab === 'curation' ? 'active' : ''}`}
              onClick={() => setActiveWorkspaceTab('curation')}
            >
              Curation
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={activeWorkspaceTab === 'intelligence'}
              className={`workspace-tab ${activeWorkspaceTab === 'intelligence' ? 'active' : ''}`}
              onClick={() => setActiveWorkspaceTab('intelligence')}
            >
              Intelligence
            </button>
          </div>
        </div>
      </div>

      {activeWorkspaceTab === 'agents' ? (
        <>
          <div className="workspace-content-titlebar glass-panel">
            <div>
              <p className="eyebrow">Agents</p>
              <div className="workspace-content-heading-row">
                {!onFirstMonitorScreen ? (
                  <button
                    type="button"
                    className="monitor-screen-chevron"
                    aria-label="Show previous queue screen"
                    onClick={goToPreviousMonitorScreen}
                  >
                    {'<'}
                  </button>
                ) : null}
                <h2>Activity Monitors</h2>
                {onFirstMonitorScreen ? (
                  <button
                    type="button"
                    className="monitor-screen-chevron"
                    aria-label="Show next queue screen"
                    onClick={goToNextMonitorScreen}
                  >
                    {'>'}
                  </button>
                ) : null}
              </div>
            </div>
            <div className="workspace-content-tools">
              <label className="field monitor-search-field" aria-label="Search workspace activity">
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Search for a Batch, Status, Case"
                />
              </label>
              <div className={`workspace-monitor-status-row health-chip health-${reviewState.mode}`} aria-live="polite">
                <span>{refreshSummary}</span>
              </div>
            </div>
          </div>

          <div className="queue-monitor-board" role="tabpanel" aria-label="Agent queues">
            {filteredColumns.map((column) => (
              <WorkspaceQueueColumn
                key={column.id}
                column={column}
                statusOptions={statusOptionsByColumn[column.id] || []}
                statusFilter={columnStatusFilters[column.id] || {}}
                onStatusToggle={handleColumnStatusToggle}
                onReviewOpen={openReviewFromWorkspace}
                onPublishedOpen={openPublishedFromWorkspace}
              />
            ))}
          </div>
        </>
      ) : activeWorkspaceTab === 'sources' ? (
        <div className="workspace-sources-tab" role="tabpanel" aria-label="Scott sources">
          <div className="workspace-content-titlebar glass-panel">
            <div>
              <p className="eyebrow">Scott memory</p>
              <h2>Sources</h2>
            </div>
            <div className="workspace-content-tools">
              {searchStatusText ? <span className="search-inline-status">{searchStatusText}</span> : null}
              <button type="button" className="action-button primary compact" disabled={searchDisabled} onClick={handleStartScottSearch}>
                {searchState.pending ? 'Searching...' : 'Start search'}
              </button>
              {searchIsRunning ? (
                <button
                  type="button"
                  className="action-button danger solid compact"
                  disabled={searchState.stopping}
                  onClick={handleStopScottSearch}
                >
                  {searchState.stopping ? 'Stopping...' : 'Stop'}
                </button>
              ) : null}
              <span className="search-countdown">
                {formatScottSearchTimer(searchState)}
              </span>
            </div>
          </div>
          {searchState.error ? <p className="error-text">{searchState.error}</p> : null}

          <ScottSourcesPanel
            state={sourcesState}
            sort={sourcesSort}
            filters={sourceFilters}
            onSort={handleSourceSort}
            onFilterChange={updateSourceFilter}
            onPromptSaved={updateSourceSitePrompt}
            onIncludeNextRunSaved={updateSourceSiteIncludeNextRun}
            onStatusSaved={updateSourceSiteStatus}
            onScroll={handleScottSourcesScroll}
          />
        </div>
      ) : activeWorkspaceTab === 'curation' ? (
        <div className="workspace-sources-tab" role="tabpanel" aria-label="Hannah curation">
          <div className="workspace-content-titlebar glass-panel">
            <div>
              <p className="eyebrow">Hannah curation</p>
              <h2>Curation</h2>
            </div>
            <div className="workspace-content-tools">
              {curationStatusText ? <span className="search-inline-status">{curationStatusText}</span> : null}
              <button type="button" className="action-button primary compact" disabled={curationDisabled} onClick={handleStartHannahCuration}>
                {curationState.pending ? 'Searching...' : 'Start search'}
              </button>
              {curationIsRunning ? (
                <button
                  type="button"
                  className="action-button danger solid compact"
                  disabled={curationState.stopping}
                  onClick={handleStopHannahCuration}
                >
                  {curationState.stopping ? 'Stopping...' : 'Stop'}
                </button>
              ) : null}
              <span className="search-countdown">
                {formatHannahCurationTimer(curationState)}
              </span>
            </div>
          </div>
          {curationState.error ? <p className="error-text">{curationState.error}</p> : null}

          <HannahCurationPanel
            state={candidatesState}
            sort={candidatesSort}
            filters={candidateFilters}
            onSort={handleCandidateSort}
            onFilterChange={updateCandidateFilter}
            onScroll={handleHannahCandidatesScroll}
          />
        </div>
      ) : (
        <div className="workspace-sources-tab" role="tabpanel" aria-label="Whitney market intelligence">
          <div className="workspace-content-titlebar glass-panel">
            <div>
              <p className="eyebrow">Whitney intelligence</p>
              <h2>Demand Sensing</h2>
            </div>
            <div className="workspace-content-tools">
              {scanStatusText ? <span className="search-inline-status">{scanStatusText}</span> : null}
              <button type="button" className="action-button primary compact" disabled={scanDisabled} onClick={handleStartWhitneyDemandScan}>
                {scanState.pending ? 'Sensing...' : 'Start scan'}
              </button>
              {scanIsRunning ? (
                <button
                  type="button"
                  className="action-button danger solid compact"
                  disabled={scanState.stopping}
                  onClick={handleStopWhitneyDemandScan}
                >
                  {scanState.stopping ? 'Stopping...' : 'Stop'}
                </button>
              ) : null}
              <span className="search-countdown">
                {formatWhitneyDemandScanTimer(scanState)}
              </span>
            </div>
          </div>
          {scanState.error ? <p className="error-text">{scanState.error}</p> : null}

          <WhitneyDemandSignalsPanel
            state={signalsState}
            sort={signalsSort}
            filters={signalFilters}
            onSort={handleSignalSort}
            onFilterChange={updateSignalFilter}
            onScroll={handleWhitneySignalsScroll}
          />
        </div>
      )}
    </section>
  );
}

function buildWorkspaceMonitorItems(queueItems, queueMode, reviewItems, daisyItems, stoppedSourceSearches = {}, reggieSuggestions = []) {
  const columns = WORKSPACE_QUEUE_COLUMNS.map((column) => ({ ...column, items: [] }));
  const byId = Object.fromEntries(columns.map((column) => [column.id, column]));

  if (queueMode === 'live') {
    (queueItems || []).forEach((queueItem) => {
      const column =
        columns.find((candidate) => candidate.agentId === queueItem.agentId) ||
        columns.find((candidate) => candidate.id === queueItem.queueFamily);

      if (!column) {
        return;
      }

      const stoppedSearch = stoppedSourceSearches[queueItem.id];
      column.items.push({
        id: queueItem.id,
        label: resolveQueueItemLabel(queueItem),
        title: compactTitle(queueItem.displayName || resolveQueueItemDisplayTitle(queueItem)),
        meta: queueItem.payload?.stage || queueItem.sourceType || queueItem.queueFamily,
        status: stoppedSearch ? 'stopped' : queueItem.status,
        statusLabel: stoppedSearch?.label,
        priority: queueItem.priority,
        publishedSymbolId: queueItem.publishedSymbolId,
        publishedPageCode: queueItem.publishedPageCode,
        publishedPackCode: queueItem.publishedPackCode,
        publishedStandardsPath: queueItem.publishedStandardsPath,
        toolSummary: Array.isArray(queueItem.toolSummary) ? queueItem.toolSummary : [],
        searchText: [
          queueItem.id,
          queueItem.agentId,
          queueItem.agentName,
          queueItem.queueFamily,
          queueItem.sourceType,
          queueItem.sourceId,
          queueItem.status,
          stoppedSearch?.label,
          queueItem.priority,
          queueItem.publishedSymbolId,
          queueItem.publishedPageCode,
          queueItem.publishedPackCode,
          queueItem.publishedStandardsPath,
          queueItem.escalationReason,
          (queueItem.toolSummary || []).join(' '),
          JSON.stringify(queueItem.payload || {})
        ].join(' '),
        detail: queueItem.escalationReason || resolveQueueItemTitle(queueItem)
      });
    });
  } else {
    (queueItems || []).forEach((activity) => {
      (activity.agents || []).forEach((agent) => {
        const column = columns.find((candidate) => candidate.agentId === agent.id);

        if (!column) {
          return;
        }

        column.items.push({
          id: `${activity.id}-${agent.id}`,
          label: formatWorkspaceDisplayDate(activity.batchId, { createdAt: activity.submittedAt, submittedAt: activity.submittedAt, id: activity.id }),
          title: compactTitle(activity.sourceFileName || activity.title),
          meta: agent.stage,
          status: agent.status,
          priority: activity.priority,
          searchText: [
            activity.id,
            activity.batchId,
            activity.title,
            activity.sourceFileName,
            activity.operatorStatus,
            activity.owner,
            activity.reviewCaseId,
            agent.name,
            agent.stage,
            agent.status,
            agent.summary
          ].join(' '),
          detail: agent.summary
        });
      });
    });
  }

  (reviewItems || []).forEach((review) => {
    const reviewIdentifier = review.symbolId || review.id;
    const isSplitItem = review.reviewItemType === 'split_item';

    byId.human_review.items.push({
      id: `review-${review.id}`,
      reviewCaseId: review.id,
      label: formatWorkspaceDisplayDate(review.openedAt || review.createdAt, { id: reviewIdentifier }),
      title: compactTitle(review.displayName || review.title || review.summary || reviewIdentifier),
      meta: isSplitItem ? 'Split item review' : review.currentStage || review.status || review.owner || 'Review case',
      status: review.latestDecision?.decisionCode || review.status || 'review',
      priority: review.priority || review.escalationLevel || 'Medium',
      searchText: [
        review.id,
        review.parentReviewCaseId,
        review.splitChildKey,
        review.reviewItemType,
        review.symbolId,
        review.openedAt,
        review.createdAt,
        review.title,
        review.summary,
        review.currentStage,
        review.status,
        review.owner,
        review.priority,
        review.escalationLevel,
        review.latestDecision?.decisionCode
      ].join(' '),
      detail: review.summary || review.status || 'Open human review'
    });
  });

  (daisyItems || []).forEach((report) => {
    byId.review_coordination.items.push({
      id: `daisy-report-${report.id}`,
      label: formatWorkspaceDisplayDate(report.createdAt || report.created_at, report),
      title: compactTitle(report.coordinationSummary || 'Coordination report'),
      meta: report.currentStage || 'Daisy report',
      status: report.coordinationStatus || report.decision || 'report',
      priority: report.escalationLevel || 'Medium',
      searchText: [
        report.id,
        report.queueItemId,
        report.reviewCaseId,
        report.coordinationStatus,
        report.coordinationSummary,
        report.currentStage,
        report.escalationLevel,
        report.decision,
        report.escalationTarget
      ].join(' '),
      detail: report.coordinationSummary
    });
  });

  (reggieSuggestions || []).forEach((suggestion) => {
    const evidence = suggestion.evidence || {};
    byId.control_audit.items.push({
      id: suggestion.id,
      label: suggestion.severity || 'info',
      title: compactTitle(suggestion.detail || suggestion.ruleCode || 'Queue control suggestion'),
      meta: suggestion.ruleCode || suggestion.sourceType || 'Reggie control',
      status: suggestion.status || 'open',
      priority: suggestion.severity || 'info',
      searchText: [
        suggestion.id,
        suggestion.ruleCode,
        suggestion.severity,
        suggestion.status,
        suggestion.detail,
        suggestion.suggestedRemediation,
        suggestion.sourceType,
        suggestion.sourceId,
        evidence.agent,
        evidence.db_status,
        evidence.runtime_status,
        evidence.runtime_path,
        JSON.stringify(evidence)
      ].join(' '),
      detail: suggestion.suggestedRemediation || 'Observational only; inspect before applying any remediation.'
    });
  });

  return columns;
}

function resolveQueueItemTitle(queueItem) {
  const payload = queueItem.payload || {};
  const packageId = queueItem.packageDisplayId || payload.package_display_id || payload.packageDisplayId;
  const packageSequence =
    queueItem.packageSymbolSequence ?? payload.package_symbol_sequence ?? payload.packageSymbolSequence;
  const readableSymbolId =
    queueItem.publishedSymbolId ||
    payload.published_display_id ||
    payload.symbol_display_id ||
    payload.symbol_slug ||
    (packageId && packageSequence != null ? `${packageId}-${packageSequence}` : '') ||
    packageId;

  return (
    readableSymbolId ||
    queueItem.displayName ||
    payload.display_name ||
    payload.workspace_display_name ||
    (Array.isArray(payload.symbol_display_ids) && payload.symbol_display_ids.length === 1 ? payload.symbol_display_ids[0] : '') ||
    payload.candidate_title ||
    payload.original_filename ||
    payload.file_name ||
    payload.source_file_name ||
    payload.candidate_symbol_id ||
    payload.review_case_id ||
    payload.submission_batch_id ||
    queueItem.escalationReason ||
    `${queueItem.agentName || queueItem.agentId} ${queueItem.sourceType}`
  );
}

function resolveQueueItemLabel(queueItem) {
  const payload = queueItem.payload || {};
  const submissionBatchId = payload.submission_batch_id;

  if (isExternalSubmissionId(submissionBatchId)) {
    return formatWorkspaceDisplayDate(submissionBatchId, queueItem);
  }

  return formatWorkspaceDisplayDate(null, queueItem);
}

function resolveQueueItemDisplayTitle(queueItem) {
  const title = resolveQueueItemTitle(queueItem);

  if (isExternalSubmissionId(title)) {
    return formatWorkspaceDisplayDate(title, queueItem);
  }

  return title;
}

function isExternalSubmissionId(value) {
  return /^subext-\d{8}T\d{6}Z$/i.test(String(value || '').trim());
}

const WORKSPACE_DISPLAY_DATE_FORMATTER = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Europe/London',
  hour: '2-digit',
  minute: '2-digit',
  day: '2-digit',
  month: 'short',
  year: '2-digit',
  hourCycle: 'h23'
});

function formatWorkspaceDisplayDate(value, queueItem) {
  const date = extractWorkspaceDisplayDate(value) || extractWorkspaceItemDate(queueItem);

  if (!date) {
    return String(value || queueItem?.id || 'Pending').trim();
  }

  const parts = Object.fromEntries(
    WORKSPACE_DISPLAY_DATE_FORMATTER.formatToParts(date)
      .filter((part) => part.type !== 'literal')
      .map((part) => [part.type, part.value])
  );
  const month = String(parts.month || '').replace('.', '').toUpperCase();

  return `${parts.hour}:${parts.minute} ${parts.day}${month}${parts.year}`;
}

function extractWorkspaceItemDate(item) {
  if (!item) {
    return null;
  }

  const payload = item.payload || item.payload_json || {};
  const candidates = [
    item.createdAt,
    item.created_at,
    item.submittedAt,
    item.submitted_at,
    item.openedAt,
    item.opened_at,
    item.startedAt,
    item.started_at,
    item.completedAt,
    item.completed_at,
    payload.createdAt,
    payload.created_at,
    payload.submittedAt,
    payload.submitted_at,
    payload.openedAt,
    payload.opened_at,
    payload.timestamp,
    payload.created,
    payload.submission_batch_id,
    item.id,
    item.sourceId
  ];

  for (const candidate of candidates) {
    const date = extractWorkspaceDisplayDate(candidate);
    if (date) {
      return date;
    }
  }

  return null;
}

function extractWorkspaceDisplayDate(value) {
  if (!value) {
    return null;
  }

  const candidate = String(value).trim();
  const externalSubmissionMatch = candidate.match(/^subext-(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/i);

  if (externalSubmissionMatch) {
    const [, year, month, day, hours, minutes, seconds] = externalSubmissionMatch;
    return new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), Number(hours), Number(minutes), Number(seconds)));
  }

  const parsed = new Date(candidate);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
}

function buildWorkspaceStatusOptions(columns) {
  return Object.fromEntries(
    columns.map((column) => {
      const optionMap = new Map();

      column.items.forEach((item) => {
        const key = getWorkspaceStatusKey(item.status);
        const existing = optionMap.get(key);

        if (existing) {
          existing.count += 1;
          return;
        }

        optionMap.set(key, {
          key,
          label: formatWorkspaceStatusLabel(item.status),
          count: 1
        });
      });

      return [
        column.id,
        Array.from(optionMap.values()).sort((first, second) => {
          const firstHidden = isDefaultHiddenWorkspaceStatus(first.key, column.id);
          const secondHidden = isDefaultHiddenWorkspaceStatus(second.key, column.id);

          if (firstHidden !== secondHidden) {
            return firstHidden ? 1 : -1;
          }

          return first.label.localeCompare(second.label);
        })
      ];
    })
  );
}

function filterWorkspaceMonitorColumns(columns, query, columnStatusFilters) {
  const normalizedQuery = query.trim().toLowerCase();

  return columns.map((column) => ({
    ...column,
    items: column.items.filter((item) => {
      const searchableText = [
        item.searchText,
        item.label,
        item.title,
        item.meta,
        item.detail,
        item.id,
        item.reviewCaseId,
        item.publishedSymbolId,
        item.publishedPageCode,
        item.publishedPackCode
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      const matchesQuery = !normalizedQuery || searchableText.includes(normalizedQuery);
      if (!matchesQuery) {
        return false;
      }

      const statusKey = getWorkspaceStatusKey(item.status);
      const columnFilter = columnStatusFilters[column.id] || {};

      // When searching for a specific symbol/card ID, include all matching statuses
      // by default so completed upstream stages stay visible across columns.
      if (normalizedQuery && !Object.prototype.hasOwnProperty.call(columnFilter, statusKey)) {
        return true;
      }

      return isWorkspaceStatusVisible(statusKey, columnFilter, column.id);
    })
  }));
}

function getWorkspaceStatusKey(status) {
  return String(status || 'pending').trim().toLowerCase().replaceAll('_', ' ');
}

function formatWorkspaceStatusLabel(status) {
  const key = getWorkspaceStatusKey(status);
  if (key === 'deleted') {
    return 'DELETE';
  }
  return key.replaceAll(' ', '_').toUpperCase();
}

function isDefaultHiddenWorkspaceStatus(statusKey, columnId) {
  if (columnId === 'intake' && statusKey === 'completed') {
    return false;
  }
  return DEFAULT_HIDDEN_WORKSPACE_STATUSES.has(statusKey);
}

function isWorkspaceStatusVisible(statusKey, columnFilter = {}, columnId = '') {
  if (Object.prototype.hasOwnProperty.call(columnFilter, statusKey)) {
    return Boolean(columnFilter[statusKey]);
  }

  return !isDefaultHiddenWorkspaceStatus(statusKey, columnId);
}

function compactTitle(value) {
  const text = String(value || 'Untitled').trim();

  if (text.length <= 42) {
    return text;
  }

  return `${text.slice(0, 39)}...`;
}

function WorkspaceQueueColumn({ column, statusOptions, statusFilter, onStatusToggle, onReviewOpen, onPublishedOpen }) {
  return (
    <section className={`monitor-column monitor-${column.tone}`}>
      <header className="monitor-column-header">
        <div>
          <h3>{column.title}</h3>
          <p>{column.subtitle}</p>
        </div>
        <div className="monitor-column-tools">
          {statusOptions.length ? (
            <details className="monitor-status-filter">
              <summary>Status</summary>
              <div className="monitor-status-filter-menu">
                {statusOptions.map((option) => (
                  <label key={option.key}>
                    <input
                      type="checkbox"
                      checked={isWorkspaceStatusVisible(option.key, statusFilter, column.id)}
                      onChange={() => onStatusToggle(column.id, option.key)}
                    />
                    <span>{option.label}</span>
                    <b>{option.count}</b>
                  </label>
                ))}
              </div>
            </details>
          ) : null}
          <span>{column.items.length}</span>
        </div>
      </header>
      <div className="monitor-column-body">
        {column.items.length ? (
          column.items.map((item) => (
            <WorkspaceMonitorCard
              key={item.id}
              item={item}
              onOpen={
                column.id === 'human_review'
                  ? onReviewOpen
                  : column.id === 'publication'
                    ? onPublishedOpen
                    : null
              }
            />
          ))
        ) : (
          <div className="monitor-empty">Clear</div>
        )}
      </div>
    </section>
  );
}

function WorkspaceMonitorCard({ item, onOpen }) {
  const priority = String(item.priority || 'Normal').toLowerCase();
  const status = String(item.status || 'pending').toLowerCase().replaceAll('_', '-');
  const isClickable = typeof onOpen === 'function' && (Boolean(item.reviewCaseId) || Boolean(item.publishedSymbolId) || Boolean(item.publishedStandardsPath));
  const CardElement = isClickable ? 'button' : 'article';
  const statusLabel = item.statusLabel || (getWorkspaceStatusKey(item.status) === 'published' ? 'PUBLISHED' : String(item.status || 'pending').replaceAll('_', ' '));

  return (
    <CardElement
      className={`monitor-card monitor-status-${status} ${isClickable ? 'monitor-card-clickable' : ''}`}
      title={item.detail || item.title}
      type={isClickable ? 'button' : undefined}
      onClick={isClickable ? () => onOpen(item) : undefined}
    >
      <div className="monitor-card-line">
        <strong>{item.label}</strong>
        <span className={`monitor-dot priority-${priority}`} aria-label={`${item.priority || 'Normal'} priority`} />
      </div>
      <p>{item.title}</p>
      <div className="monitor-card-meta">
        <span>{item.meta}</span>
      </div>
      {item.toolSummary?.length ? (
        <div className="monitor-card-tools">
          <span>Process</span>
          <b>{item.toolSummary.join(' · ')}</b>
        </div>
      ) : null}
      {item.publishedStandardsPath || item.publishedSymbolId ? (
        <div className="monitor-card-tools">
          <span>Standards</span>
          <b>{item.publishedPageCode || item.publishedSymbolId}</b>
        </div>
      ) : null}
      <b className="monitor-card-status">{statusLabel}</b>
    </CardElement>
  );
}

function resolveAbsoluteBase() {
  // appConfig.apiRoot may be relative (e.g. "/api/v1"), absolute (e.g. "https://host/api/v1"),
  // or empty. `new URL(x, base)` requires an absolute base, so coerce here.
  const root = appConfig.apiRoot;
  if (!root) {
    return window.location.origin;
  }
  try {
    // Will throw if `root` is relative; in that case fall through to absolutize it.
    return new URL(root).toString();
  } catch {
    try {
      return new URL(root, window.location.origin).toString();
    } catch {
      return window.location.origin;
    }
  }
}

function resolveWorkspaceAssetUrl(assetUrl) {
  if (!assetUrl) {
    return null;
  }
  return new URL(assetUrl, resolveAbsoluteBase()).toString();
}

function isTerminalReviewStage(stage) {
  return ['approved', 'closed', 'published', 'rejected', 'superseded_by_raster_split'].includes(
    String(stage || '').trim().toLowerCase()
  );
}

function buildPublishedPreviewCandidates(symbol) {
  const candidates = [];
  const symbolReference = symbol?.slug || symbol?.id || symbol?.symbolId;

  if (symbol?.previewUrl) {
    candidates.push(resolveWorkspaceAssetUrl(symbol.previewUrl));
  }
  if (symbolReference) {
    candidates.push(resolveWorkspaceAssetUrl(`/api/v1/published/symbols/${encodeURIComponent(symbolReference)}/preview`));
  }

  const reviewCaseId = symbol?.payload?.review_case_id || symbol?.payload?.lineage?.parent_sheet_review_case_id;
  const objectKey = symbol?.payload?.source_object_key;
  if (reviewCaseId && objectKey) {
    candidates.push(
      resolveWorkspaceAssetUrl(
        `/api/v1/workspace/review-cases/${encodeURIComponent(reviewCaseId)}/children/preview?object_key=${encodeURIComponent(objectKey)}`
      )
    );
  }

  return Array.from(new Set(candidates.filter(Boolean)));
}

function PublishedSymbolPreview({ symbol, large = false }) {
  const previewCandidates = useMemo(() => buildPublishedPreviewCandidates(symbol), [symbol]);
  const [previewIndex, setPreviewIndex] = useState(0);
  const previewUrl = previewCandidates[previewIndex];

  useEffect(() => {
    setPreviewIndex(0);
  }, [previewCandidates.join('|')]);

  if (previewUrl) {
    return (
      <img
        className={`published-symbol-preview ${large ? 'large' : ''}`.trim()}
        src={previewUrl}
        alt={`Published preview of ${symbol?.name || symbol?.id || 'symbol'}`}
        onError={() => setPreviewIndex((current) => current + 1)}
      />
    );
  }

  return <SymbolGlyph symbolId={symbol?.id || symbol?.symbolId || 'SYMBOL'} large={large} />;
}

function SupplementalPhotoStrip({ photos }) {
  if (!Array.isArray(photos) || photos.length === 0) {
    return null;
  }

  return (
    <section className="supplemental-photo-strip" aria-label="Real equipment photos">
      <div className="supplemental-photo-grid">
        {photos.slice(0, 2).map((photo) => (
          <a
            key={photo.id}
            className="supplemental-photo-card"
            href={photo.sourceUrl || photo.previewUrl}
            target="_blank"
            rel="noreferrer"
          >
            <img src={resolveWorkspaceAssetUrl(photo.previewUrl)} alt={photo.title || 'Supplemental equipment reference'} />
            <span>{photo.title || photo.sourceDomain || 'Equipment photo'}</span>
          </a>
        ))}
      </div>
    </section>
  );
}

function ReviewSourceVisual({ activeChange, activeChildren, reviewedChildCount, onSaveProperties, propertyOptions, workspaceMode }) {
  const primaryChild = activeChildren[0];
  const resolvedPreviewUrl = resolveWorkspaceAssetUrl(activeChange?.sourcePreviewUrl || primaryChild?.previewUrl);
  const [imageUnavailable, setImageUnavailable] = useState(!resolvedPreviewUrl);
  const [propertyDraft, setPropertyDraft] = useState({
    name: '',
    description: '',
    format: '',
    category: '',
    discipline: ''
  });
  const [propertyState, setPropertyState] = useState({ pending: false, message: '', error: '' });
  const itemName = displaySymbolId(activeChange) || 'Review item';
  const originalFilename = displayReviewOriginalFilename(activeChange) || 'Not recorded';
  const symbolProperties = activeChange?.symbolProperties || {};
  const propertyNamePattern = '^[A-Za-z0-9 \\\\-/$]*$';
  const categoryOptions = mergePropertyOptions(propertyOptions?.category, propertyDraft.category);
  const disciplineOptions = mergePropertyOptions(propertyOptions?.discipline, propertyDraft.discipline);

  useEffect(() => {
    setImageUnavailable(!resolvedPreviewUrl);
  }, [resolvedPreviewUrl]);

  useEffect(() => {
    const suggestedName = primaryChild?.proposedSymbolName || activeChange?.proposedSymbolName || activeChange?.displayName || itemName;
    const suggestedDescription = activeChange?.classificationSummary || activeChange?.summary || '';
    const suggestedCategory = activeChange?.category || activeChange?.processCategory || activeChange?.symbolFamily || activeChange?.parentEquipmentClass || '';
    const suggestedDiscipline = activeChange?.discipline || activeChange?.engineeringDiscipline || '';
    const suggestedFormat = activeChange?.format || primaryChild?.format || '';

    setPropertyDraft({
      name: symbolProperties.name || suggestedName,
      description: symbolProperties.description || suggestedDescription,
      format: symbolProperties.format || suggestedFormat,
      category: symbolProperties.category || suggestedCategory,
      discipline: symbolProperties.discipline || suggestedDiscipline
    });
    setPropertyState({ pending: false, message: '', error: '' });
  }, [
    activeChange?.id,
    activeChange?.displayName,
    activeChange?.proposedSymbolName,
    activeChange?.category,
    activeChange?.summary,
    activeChange?.classificationSummary,
    activeChange?.format,
    activeChange?.processCategory,
    activeChange?.symbolFamily,
    activeChange?.parentEquipmentClass,
    activeChange?.discipline,
    activeChange?.engineeringDiscipline,
    itemName,
    primaryChild?.proposedSymbolName,
    primaryChild?.format,
    symbolProperties.name,
    symbolProperties.description,
    symbolProperties.format,
    symbolProperties.category,
    symbolProperties.discipline
  ]);

  function updatePropertyDraft(field, value) {
    setPropertyState((current) => {
      if (current.pending || (!current.message && !current.error)) {
        return current;
      }
      return { pending: false, message: '', error: '' };
    });
    setPropertyDraft((current) => ({ ...current, [field]: value }));
  }

  async function saveProperties(event) {
    event.preventDefault();
    setPropertyState({ pending: true, message: '', error: '' });
    try {
      await onSaveProperties({
        name: propertyDraft.name,
        description: propertyDraft.description,
        format: propertyDraft.format,
        category: propertyDraft.category,
        discipline: propertyDraft.discipline
      });
      setPropertyState({ pending: false, message: 'Saved.', error: '' });
    } catch (error) {
      setPropertyState({
        pending: false,
        message: '',
        error: error instanceof Error ? error.message : 'Symbol properties update failed.'
      });
    }
  }

  return (
    <section className="review-visual-panel">
      <div className="review-visual-header">
        <div>
          <p className="context-label">Visual evidence</p>
          <h3>{itemName}</h3>
        </div>
        <span className="status-pill">{activeChildren.length ? `${activeChildren.length} proposed children` : activeChange?.status}</span>
      </div>
      <div className="review-visual-primary-row">
        <div className="review-visual-frame">
          {!imageUnavailable && resolvedPreviewUrl ? (
            <img
              className="review-source-image"
              src={resolvedPreviewUrl}
              alt={`Visual preview for ${originalFilename || itemName}`}
              onError={() => setImageUnavailable(true)}
            />
          ) : (
            <div className="review-source-fallback">
              <SymbolGlyph symbolId={activeChange?.symbolId || 'SYMBOL'} large />
              <small>Preview unavailable</small>
            </div>
          )}
        </div>
        <div className="review-primary-properties">
          <Fact label="ID" value={itemName} />
          <Fact label="Original file" value={originalFilename} />
          <Fact label="Review item" value={activeChange?.reviewItemType === 'split_item' ? 'Split child' : 'Review case'} />
          <Fact label="Status" value={activeChange?.splitChildStatus || activeChange?.status || 'Pending'} />
          <Fact label="Child decisions" value={`${reviewedChildCount} / ${activeChildren.length || activeChange?.childCount || 0}`} />
          <Fact label="Intake record" value={activeChange?.intakeRecordId || 'Pending'} />
        </div>
      </div>
      <form className="symbol-property-editor" onSubmit={saveProperties}>
        <div className="symbol-property-editor-left">
          <label className="field">
            <span>Name</span>
            <input
              type="text"
              maxLength="50"
              pattern={propertyNamePattern}
              value={propertyDraft.name}
              onChange={(event) => updatePropertyDraft('name', event.target.value)}
              title="Use letters, numbers, spaces, hyphens, slashes, and dollar signs only."
              required
            />
          </label>
          <label className="field symbol-description-field">
            <span>Description</span>
            <textarea
              rows="2"
              maxLength="256"
              value={propertyDraft.description}
              onChange={(event) => updatePropertyDraft('description', event.target.value)}
            />
          </label>
          <FormatIndicator format={propertyDraft.format} />
        </div>
        <div className="symbol-property-editor-right">
          <label className="field">
            <span>Category</span>
            <div className="property-combo-field">
              <input
                type="text"
                maxLength="80"
                placeholder="Type category"
                autoComplete="off"
                value={propertyDraft.category}
                onChange={(event) => updatePropertyDraft('category', event.target.value)}
              />
              <select
                aria-label="Saved category values"
                value=""
                onChange={(event) => updatePropertyDraft('category', event.target.value)}
              >
                <option value="">Saved</option>
                {categoryOptions.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            </div>
          </label>
          <label className="field">
            <span>Discipline</span>
            <div className="property-combo-field">
              <input
                type="text"
                maxLength="80"
                placeholder="Type discipline"
                autoComplete="off"
                value={propertyDraft.discipline}
                onChange={(event) => updatePropertyDraft('discipline', event.target.value)}
              />
              <select
                aria-label="Saved discipline values"
                value=""
                onChange={(event) => updatePropertyDraft('discipline', event.target.value)}
              >
                <option value="">Saved</option>
                {disciplineOptions.map((option) => (
                  <option key={option} value={option}>{option}</option>
                ))}
              </select>
            </div>
          </label>
        </div>
        <div className="symbol-property-actions">
          <small>{propertyDraft.name.length}/50 · {propertyDraft.description.length}/256</small>
          <button
            type="submit"
            className="action-button secondary compact-save-button"
            disabled={propertyState.pending || workspaceMode !== 'live'}
          >
            {propertyState.pending ? 'Saving...' : 'Save'}
          </button>
        </div>
        {propertyState.message ? <p className="success-text">{propertyState.message}</p> : null}
        {propertyState.error ? <p className="error-text">{propertyState.error}</p> : null}
      </form>
    </section>
  );
}

function FormatIndicator({ format }) {
  const extension = String(format || '').trim().replace(/^\./, '').toUpperCase() || 'Pending';
  const isPending = extension === 'Pending';

  return (
    <div className={`property-format-indicator ${isPending ? 'pending' : ''}`} aria-label={`Format ${extension}`}>
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M7 3h7l4 4v14H7z" />
        <path d="M14 3v5h5" />
      </svg>
      <span>Format</span>
      <b>{extension}</b>
    </div>
  );
}

function mergePropertyOptions(options = [], currentValue = '') {
  const values = [...options];
  const current = String(currentValue || '').trim();
  if (current && !values.some((option) => option.toLowerCase() === current.toLowerCase())) {
    values.unshift(current);
  }
  return values;
}

function ReviewsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState('');
  const [queueFilter, setQueueFilter] = useState('new');
  const [workspaceState, setWorkspaceState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading live Reviews…' : 'No API root configured. Showing seeded review queue.',
    items: appConfig.apiRoot ? [] : changeQueue
  });
  const [daisyState, setDaisyState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading Daisy coordination…' : 'No API root configured. Showing seeded coordination.',
    items: appConfig.apiRoot ? [] : daisyCoordinationReports
  });
  const [activeId, setActiveId] = useState(changeQueue[0]?.id || '');
  const [sourceComments, setSourceComments] = useState({});
  const [childReviewState, setChildReviewState] = useState({});
  const [caseReviewState, setCaseReviewState] = useState({});
  const [submitState, setSubmitState] = useState({ pending: false, message: '', error: '' });
  const [propertyOptions, setPropertyOptions] = useState({ category: [], discipline: [] });
  const requestedReviewId = searchParams.get('review') || '';
  const reviewQueue = useMemo(() => {
    const items = [...workspaceState.items];
    const knownIds = new Set(items.map((item) => item.id));

    daisyState.items.forEach((report) => {
      if (!report.reviewCaseId || knownIds.has(report.reviewCaseId)) {
        return;
      }
      if (isTerminalReviewStage(report.currentStage || report.coordinationStatus)) {
        return;
      }

      knownIds.add(report.reviewCaseId);
      items.push({
        id: report.reviewCaseId,
        symbolId: report.sourceId || report.reviewCaseId,
        title: report.coordinationSummary || 'Daisy-coordinated review case',
        owner: report.assignmentProposals?.[0]?.reviewer || 'Unassigned',
        due: 'Pending',
        priority: report.escalationLevel === 'high' ? 'High' : report.escalationLevel === 'low' ? 'Low' : 'Medium',
        risk: report.escalationLevel || 'Pending',
        status: report.currentStage || report.coordinationStatus || 'Daisy coordinated',
        summary: report.coordinationSummary || 'Daisy created coordination output for this review case.',
        sourceFileName: 'Pending source context',
        childCount: 0,
        children: [],
        clarifications: ['Daisy coordination exists for this case.'],
        classificationStatus: 'Pending',
        classificationConfidence: null,
        libbyApproved: false,
        sourceClassification: 'Pending'
      });
    });

    return items;
  }, [workspaceState.items, daisyState.items]);

  useEffect(() => {
    let cancelled = false;

    Promise.all([fetchWorkspaceReviewCases(), fetchWorkspaceDaisyReports()]).then(([reviewResult, daisyResult]) => {
      if (cancelled) {
        return;
      }

      if (reviewResult.ok) {
        setWorkspaceState({
          loading: false,
          mode: 'live',
          message: reviewResult.items.length ? reviewResult.message : 'No live review cases are currently open.',
          items: reviewResult.items
        });
      } else {
        setWorkspaceState({
          loading: false,
          mode: 'seeded',
          message: `${reviewResult.message} Showing seeded queue instead.`,
          items: changeQueue
        });
      }

      if (daisyResult.ok) {
        setDaisyState({
          loading: false,
          mode: 'live',
          message: daisyResult.items.length ? daisyResult.message : 'No live Daisy coordination reports are available yet.',
          items: daisyResult.items
        });
      } else {
        setDaisyState({
          loading: false,
          mode: 'seeded',
          message: `${daisyResult.message} Showing seeded coordination instead.`,
          items: daisyCoordinationReports
        });
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  async function refreshPropertyOptions() {
    const result = await fetchWorkspaceReviewSymbolPropertyOptions();
    if (!result.ok) {
      return;
    }

    const nextOptions = { category: [], discipline: [] };
    result.items.forEach((item) => {
      const fieldName = item.fieldName || item.field_name;
      if (!nextOptions[fieldName] || !item.value) {
        return;
      }
      if (!nextOptions[fieldName].includes(item.value)) {
        nextOptions[fieldName].push(item.value);
      }
    });
    Object.keys(nextOptions).forEach((fieldName) => {
      nextOptions[fieldName].sort((left, right) => left.localeCompare(right));
    });
    setPropertyOptions(nextOptions);
  }

  useEffect(() => {
    refreshPropertyOptions();
  }, []);

  const reviewQueueCounts = useMemo(() => {
    return reviewQueue.reduce(
      (counts, item) => {
        const isReturned = item.splitChildStatus === 'returned_for_review' || String(item.status || '').toLowerCase().includes('returned');
        if (isReturned) {
          counts.returned += 1;
        } else {
          counts.new += 1;
        }
        return counts;
      },
      { new: 0, returned: 0 }
    );
  }, [reviewQueue]);

  const filteredQueue = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    return reviewQueue.filter((item) => {
      const isReturned = item.splitChildStatus === 'returned_for_review' || String(item.status || '').toLowerCase().includes('returned');
      if (queueFilter === 'returned' && !isReturned) {
        return false;
      }
      if (queueFilter === 'new' && isReturned) {
        return false;
      }
      if (!normalizedQuery) {
        return true;
      }
      return [
        item.id,
        item.parentReviewCaseId,
        item.splitChildKey,
        item.reviewItemType,
        item.symbolId,
        item.title,
        item.owner,
        item.summary,
        item.sourceFileName || '',
        ...(item.children || []).map((child) => child.proposedSymbolName || '')
      ].some((value) => String(value).toLowerCase().includes(normalizedQuery));
    });
  }, [query, reviewQueue, queueFilter]);

  useEffect(() => {
    if (!filteredQueue.some((item) => item.id === activeId)) {
      setActiveId(filteredQueue[0]?.id || '');
    }
  }, [filteredQueue, activeId]);

  useEffect(() => {
    if (!requestedReviewId) {
      return;
    }

    const requestedReview = filteredQueue.find(
      (item) => item.id === requestedReviewId || item.parentReviewCaseId === requestedReviewId
    );

    if (requestedReview) {
      setActiveId(requestedReview.id);
    }
  }, [filteredQueue, requestedReviewId]);

  const activeChange = filteredQueue.find((item) => item.id === activeId) || filteredQueue[0];
  const activeChildren = activeChange?.children || [];
  const activeSingleChild = activeChildren.length === 1 ? activeChildren[0] : null;
  const activeReviewCaseId = activeChange?.parentReviewCaseId || activeChange?.id;
  const activeIndex = activeChange ? filteredQueue.findIndex((item) => item.id === activeChange.id) : -1;
  const reviewedChildCount = activeChildren.filter((child) => getChildReview(child.id).action !== 'pending').length;
  const isSplitReview = (activeChange?.reviewItemType === 'split_item' || activeChange?.currentStage === 'raster_split_review') && activeChildren.length > 0;
  const selectedCaseDecision = activeChange ? getCaseReview(activeChange.id).decisionCode : '';
  const activeSingleChildReview = activeSingleChild ? getChildReview(activeSingleChild.id) : null;
  const pendingChildCount = Math.max(activeChildren.length - reviewedChildCount, 0);
  const splitDecisionCounts = activeChildren.reduce((counts, child) => {
    const action = getChildReview(child.id).action;
    if (action && action !== 'pending') {
      counts[action] = (counts[action] || 0) + 1;
    }
    return counts;
  }, {});
  const sourceComment = sourceComments[activeChange?.id] || '';
  const activeDaisyReports = useMemo(
    () => daisyState.items.filter((item) => item.reviewCaseId === activeReviewCaseId),
    [activeReviewCaseId, daisyState.items]
  );

  useEffect(() => {
    setSubmitState((current) => {
      if (current.pending || (!current.message && !current.error)) {
        return current;
      }
      return { pending: false, message: '', error: '' };
    });
  }, [activeChange?.id]);

  function updateSourceComment(changeId, value) {
    setSourceComments((current) => ({ ...current, [changeId]: value }));
  }

  function updateChildReview(childId, updates) {
    setSubmitState((current) => {
      if (current.pending || (!current.message && !current.error)) {
        return current;
      }
      return { pending: false, message: '', error: '' };
    });
    setChildReviewState((current) => ({
      ...current,
      [childId]: {
        action: current[childId]?.action || 'pending',
        note: current[childId]?.note || '',
        requestDetails: current[childId]?.requestDetails || '',
        ...updates
      }
    }));
  }

  function getChildReview(childId) {
    return childReviewState[childId] || { action: 'pending', note: '', requestDetails: '' };
  }

  function updateCaseReview(changeId, updates) {
    setCaseReviewState((current) => ({
      ...current,
      [changeId]: {
        decisionCode: current[changeId]?.decisionCode || '',
        deciderName: current[changeId]?.deciderName || 'Human',
        deciderRole: current[changeId]?.deciderRole || 'sme_reviewer',
        decisionNote: current[changeId]?.decisionNote || '',
        ...updates
      }
    }));
  }

  function getCaseReview(changeId) {
    return (
      caseReviewState[changeId] || {
        decisionCode: '',
        deciderName: 'Human',
        deciderRole: 'sme_reviewer',
        decisionNote: ''
      }
    );
  }

  function selectAdjacentReview(direction) {
    if (!filteredQueue.length || activeIndex < 0) {
      return;
    }
    const nextIndex = Math.min(Math.max(activeIndex + direction, 0), filteredQueue.length - 1);
    selectReview(filteredQueue[nextIndex].id);
  }

  function selectReview(reviewId) {
    setActiveId(reviewId);

    if (!searchParams.has('review')) {
      return;
    }

    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete('review');
    setSearchParams(nextParams, { replace: true });
  }

  async function refreshReviewData() {
    const [reviewResult, daisyResult] = await Promise.all([fetchWorkspaceReviewCases(), fetchWorkspaceDaisyReports()]);
    if (reviewResult.ok) {
      setWorkspaceState({
        loading: false,
        mode: 'live',
        message: reviewResult.items.length ? reviewResult.message : 'No live review cases are currently open.',
        items: reviewResult.items
      });
    }
    if (daisyResult.ok) {
      setDaisyState({
        loading: false,
        mode: 'live',
        message: daisyResult.items.length ? daisyResult.message : 'No live Daisy coordination reports are available yet.',
        items: daisyResult.items
      });
    }
  }

  async function saveSymbolProperties(properties) {
    if (!activeChange) {
      return;
    }

    await updateWorkspaceReviewSymbolProperties(activeReviewCaseId, {
      splitItemId: activeChange.splitItemId || null,
      ...properties,
      updatedBy: 'Human'
    });
    await Promise.all([refreshReviewData(), refreshPropertyOptions()]);
  }

  async function submitReviewDecision() {
    if (!activeChange) {
      return;
    }

    const caseReview = getCaseReview(activeChange.id);
    const childDecisions = activeChildren
      .map((child) => {
        const review = getChildReview(child.id);
        return {
          childId: child.id,
          action: review.action,
          note: review.note,
          details: review.requestDetails,
          proposedSymbolName: child.proposedSymbolName,
          proposedSymbolId: child.proposedSymbolId
        };
      })
      .filter((item) => item.action && item.action !== 'pending');

    setSubmitState({ pending: true, message: '', error: '' });
    try {
      const result = await submitWorkspaceReviewDecision(activeChange.id, {
        decisionCode: caseReview.decisionCode,
        decisionNote: caseReview.decisionNote,
        deciderName: caseReview.deciderName,
        deciderRole: caseReview.deciderRole,
        childDecisions,
        caseComment: sourceComments[activeChange.id] || ''
      });
      setSubmitState({
        pending: false,
        message: `Decision recorded: ${result.decision.decisionCode.replaceAll('_', ' ')}.`,
        error: ''
      });
      await refreshReviewData();
    } catch (error) {
      setSubmitState({
        pending: false,
        message: '',
        error: error instanceof Error ? error.message : 'Review decision failed.'
      });
    }
  }

  async function processSplitReviewDecisions() {
    if (!activeChange) {
      return;
    }

    const childDecisions = activeChildren
      .map((child) => {
        const review = getChildReview(child.id);
        return {
          childId: child.id,
          action: review.action,
          note: review.note,
          details: review.requestDetails,
          proposedSymbolName: child.proposedSymbolName,
          proposedSymbolId: child.proposedSymbolId
        };
      })
      .filter((item) => item.action && item.action !== 'pending');

    if (!childDecisions.length) {
      setSubmitState({ pending: false, message: '', error: 'Choose at least one child-symbol decision to process.' });
      return;
    }

    setSubmitState({ pending: true, message: '', error: '' });
    try {
      const result = await processWorkspaceSplitReviewDecisions(activeReviewCaseId, {
        childDecisions
      });
      setSubmitState({
        pending: false,
        message: `Processed ${result.processedCount} child decision${result.processedCount === 1 ? '' : 's'}. ${result.remainingOpenCount} remain.`,
        error: ''
      });
      setChildReviewState((current) => {
        const next = { ...current };
        childDecisions.forEach((item) => {
          delete next[item.childId];
        });
        return next;
      });
      await refreshReviewData();
    } catch (error) {
      setSubmitState({
        pending: false,
        message: '',
        error: error instanceof Error ? error.message : 'Split review processing failed.'
      });
    }
  }

  return (
    <section className="experience-shell">
      <div className="hero-panel glass-panel workspace-hero">
        <div>
          <p className="eyebrow">SME Reviews</p>
          <h2>Coordinated Reviews</h2>
        </div>
        <div className="action-stack">
          <label className="field search-field" aria-label="Search reviews">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search change id, symbol, owner, summary, or source file"
            />
          </label>
        </div>
      </div>
      <p className={`page-status-text status-${workspaceState.mode}`}>
        {workspaceState.loading ? 'Loading reviews...' : workspaceState.message} · Reviewer: Human
      </p>

      <div className="review-workbench-grid">
        <section className="glass-panel pane review-queue-pane">
          <div className="review-pane-heading">
            <div className="review-queue-title-row">
              <h3>Review Queue</h3>
              <div className="review-navigation compact-icon-navigation" aria-label="Review item navigation">
                <button type="button" className="icon-nav-button" disabled={activeIndex <= 0} onClick={() => selectAdjacentReview(-1)} aria-label="Previous review">
                  &lt;
                </button>
                <button
                  type="button"
                  className="icon-nav-button"
                  disabled={activeIndex < 0 || activeIndex >= filteredQueue.length - 1}
                  onClick={() => selectAdjacentReview(1)}
                  aria-label="Next review"
                >
                  &gt;
                </button>
              </div>
            </div>
            <div className="segmented-control" aria-label="Review queue filter">
              <button type="button" className={queueFilter === 'new' ? 'active' : ''} onClick={() => setQueueFilter('new')}>
                New <span>{reviewQueueCounts.new}</span>
              </button>
              <button type="button" className={queueFilter === 'returned' ? 'active' : ''} onClick={() => setQueueFilter('returned')}>
                Returned <span>{reviewQueueCounts.returned}</span>
              </button>
            </div>
          </div>
          <div className="stack-list">
            {filteredQueue.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`queue-card review-list-card ${item.id === activeId ? 'active' : ''}`}
                onClick={() => selectReview(item.id)}
              >
                <div className="queue-card-topline">
                  <strong>{displaySymbolId(item)}</strong>
                  <span className={`review-status review-${item.splitChildStatus || item.status || 'pending'}`}>
                    {item.splitChildStatus === 'returned_for_review'
                      ? 'Returned'
                      : item.splitChildStatus === 'duplicate_exception'
                        ? 'Duplicate exception'
                        : 'New'}
                  </span>
                </div>
                <p>{item.title}</p>
                <small>
                  {item.reviewItemType === 'split_item' ? 'Split item' : item.currentStage || item.status} · {item.childCount || item.children?.length || 0} child symbols
                </small>
                <small>{item.sourceFileName || 'Source file pending'}</small>
              </button>
            ))}
          </div>
        </section>

        <section className="glass-panel pane compare-pane review-focus-pane">
          <SectionHeading title="Review Item" subtitle={activeChange ? `${activeIndex + 1} of ${filteredQueue.length}` : 'No active item'} />
          {activeChange ? (
            <>
              <div className="detail-heading">
                <div>
                  <h3>{displaySymbolId(activeChange)}</h3>
                  <p>
                    {displayReviewOriginalFilename(activeChange) || 'Original file not recorded'} · {activeChange.title}
                  </p>
                </div>
                <span className="status-pill">{activeChange.status}</span>
              </div>
              <ReviewSourceVisual
                activeChange={activeChange}
                activeChildren={activeChildren}
                reviewedChildCount={reviewedChildCount}
                onSaveProperties={saveSymbolProperties}
                propertyOptions={propertyOptions}
                workspaceMode={workspaceState.mode}
              />
              <div className="copy-block decision-block review-submit-block">
                <div className="review-submit-heading">
                  <h4>Decision and submit</h4>
                  {isSplitReview ? (
                    <div className="decision-count-strip" aria-label="Split review decision counts">
                      <span>Ready {reviewedChildCount}</span>
                      <span>Waiting {pendingChildCount}</span>
                      <span>Total {activeChildren.length}</span>
                    </div>
                  ) : null}
                </div>
                {activeSingleChild ? (
                  <>
                    <div className="review-decision-actions simple-review-actions" role="group" aria-label="Review item decision options">
                      {REVIEW_CHILD_ACTION_OPTIONS.map(([value, label]) => (
                        <button
                          key={value}
                          type="button"
                          className={`action-button case-decision-button case-decision-${value} ${activeSingleChildReview?.action === value ? 'selected' : ''}`}
                          onClick={() => updateChildReview(activeSingleChild.id, { action: activeSingleChildReview?.action === value ? 'pending' : value })}
                        >
                          {label}
                        </button>
                      ))}
                    </div>
                    {['request_changes', 'duplicate', 'rename_classify', 'more_evidence', 'defer'].includes(activeSingleChildReview?.action) ? (
                      <label className="field request-field">
                        <span>{activeSingleChildReview?.action === 'duplicate' ? 'Duplicate decision detail' : 'Requested changes'}</span>
                        <textarea
                          rows="4"
                          value={activeSingleChildReview.requestDetails}
                          onChange={(event) => updateChildReview(activeSingleChild.id, { requestDetails: event.target.value })}
                          placeholder={
                            activeSingleChildReview?.action === 'duplicate'
                              ? 'Confirm why this should not be published as a separate symbol.'
                              : 'Describe the changes, evidence, or classification detail needed before this symbol can be approved.'
                          }
                        />
                      </label>
                    ) : null}
                  </>
                ) : !activeChildren.length ? (
                  <div className="review-decision-actions simple-review-actions" role="group" aria-label="Review item decision options">
                    {REVIEW_ITEM_ACTION_OPTIONS.map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        className={`action-button case-decision-button case-decision-${value} ${selectedCaseDecision === value ? 'selected' : ''}`}
                        onClick={() => updateCaseReview(activeChange.id, { decisionCode: selectedCaseDecision === value ? '' : value, deciderName: 'Human' })}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                ) : null}
                {isSplitReview && !reviewedChildCount ? (
                  <p className="daisy-empty-text">Choose a decision on the review item before submitting.</p>
                ) : null}
                <label className="field">
                  <span>Comment</span>
                  <textarea
                    rows="3"
                    value={sourceComment}
                    onChange={(event) => updateSourceComment(activeChange.id, event.target.value)}
                    placeholder="Add comments or correction instructions for this review item."
                  />
                </label>
                <button
                  type="button"
                  className={`action-button record-review-button ${submitState.pending ? 'recording' : ''} ${submitState.message ? 'recorded' : ''} ${submitState.error ? 'failed' : ''}`}
                  disabled={submitState.pending || workspaceState.mode !== 'live' || (isSplitReview ? reviewedChildCount === 0 : !selectedCaseDecision)}
                  onClick={isSplitReview ? processSplitReviewDecisions : submitReviewDecision}
                >
                  {submitState.pending ? 'Submitting...' : submitState.message ? 'Submitted' : 'Submit'}
                </button>
                {submitState.message ? <p className="success-text">{submitState.message}</p> : null}
                {submitState.error ? <p className="error-text">{submitState.error}</p> : null}
                {workspaceState.mode !== 'live' ? (
                  <p className="daisy-empty-text">Decision persistence requires the live Symgov API.</p>
                ) : null}
              </div>
              <div className="fact-grid">
                <Fact label="Classification status" value={activeChange.classificationStatus || 'Not classified'} />
                <Fact
                  label="Libby confidence"
                  value={typeof activeChange.classificationConfidence === 'number' ? `${Math.round(activeChange.classificationConfidence * 100)}%` : 'Pending'}
                />
                <Fact label="Libby approved" value={activeChange.libbyApproved ? 'Yes' : 'No'} />
                <Fact label="Source classification" value={activeChange.sourceClassification || 'Unknown'} />
              </div>
              <div className="copy-block">
                <h4>Review intent</h4>
                <p>
                  {isSplitReview
                    ? activeChange.reviewItemType === 'split_item'
                      ? 'This split symbol has its own review lifecycle. Processing the decision moves only this symbol to Rupert or Libby.'
                      : 'Process decided child symbols when ready. Processed children leave this sheet review and continue independently through Rupert or Libby.'
                    : 'Each extracted child can be reviewed independently while keeping notes tied back to the source file for traceability.'}
                </p>
              </div>
              {activeChange.classificationSummary ? (
                <div className="copy-block">
                  <h4>Libby summary</h4>
                  <p>{activeChange.classificationSummary}</p>
                </div>
              ) : null}
              <div className="review-support-facts">
                <Fact label="Discipline" value={activeChange.engineeringDiscipline || 'Pending'} />
                <Fact label="Format" value={activeChange.format || 'Pending'} />
                <Fact label="Industry" value={activeChange.industry || 'Pending'} />
                <Fact label="Symbol family" value={activeChange.symbolFamily || 'Pending'} />
                <Fact label="Process category" value={activeChange.processCategory || 'Pending'} />
                <Fact label="Equipment class" value={activeChange.parentEquipmentClass || 'Pending'} />
                <Fact label="Standards source" value={activeChange.standardsSource || 'Pending'} />
                <Fact label="Provenance class" value={activeChange.libraryProvenanceClass || 'Pending'} />
              </div>
              {activeChildren.length > 1 ? (
                <>
                <SectionHeading title="Child Symbol Decisions" subtitle="Per-symbol actions and notes" />
                <div className="split-review-list" role="list" aria-label="Extracted symbols awaiting review">
                  {activeChildren.map((child, index) => {
                    const reviewState = getChildReview(child.id);
                    return (
                      <SplitReviewCard
                        key={child.id}
                        child={child}
                        index={index}
                        reviewState={reviewState}
                        onUpdate={updateChildReview}
                      />
                    );
                  })}
                </div>
                </>
              ) : null}
              {activeDaisyReports.length ? (
                <div className="copy-block daisy-block">
                  <div className="daisy-heading">
                    <div>
                      <h4>Daisy coordination</h4>
                      <p>{daisyState.loading ? 'Loading coordination state…' : daisyState.message}</p>
                    </div>
                    <span className="review-status review-approved">Live feed</span>
                  </div>
                  <div className="daisy-report-list">
                    {activeDaisyReports.map((report) => (
                      <DaisyReportCard key={report.id} report={report} />
                    ))}
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <EmptyState title="No review cases" body="There are no Daisy-coordinated reviews to show." />
          )}
        </section>

      </div>
    </section>
  );
}

function DaisyReportCard({ report }) {
  const confidenceLabel = typeof report.confidence === 'number' ? `${Math.round(report.confidence * 100)}%` : 'Pending';

  return (
    <article className="daisy-report-card">
      <div className="split-card-header">
        <div>
          <p className="context-label">Coordination report</p>
          <h4>{report.coordinationSummary}</h4>
        </div>
        <span className={`review-status review-${report.coordinationStatus === 'completed' ? 'approved' : 'request_changes'}`}>
          {report.coordinationStatus.replaceAll('_', ' ')}
        </span>
      </div>
      <div className="fact-grid daisy-fact-grid">
        <Fact label="Stage" value={report.currentStage || 'Not recorded'} />
        <Fact label="Escalation" value={report.escalationLevel || 'Not recorded'} />
        <Fact label="Decision" value={report.decision || 'Pending'} />
        <Fact label="Confidence" value={confidenceLabel} />
      </div>
      <div className="daisy-lane-grid">
        <DaisyLane
          title="Assignment proposals"
          items={report.assignmentProposals}
          renderItem={(item) => (
            <>
              <strong>
                #{item.proposalRank} · {item.reviewer}
              </strong>
              <p>{item.role}</p>
              <small>{item.reason}</small>
            </>
          )}
          emptyText="No assignment proposals."
        />
        <DaisyLane
          title="Stage movement"
          items={report.stageTransitionProposals}
          renderItem={(item) => (
            <>
              <strong>
                {item.fromStage} → {item.toStage}
              </strong>
              <p>{item.action}</p>
              <small>{item.reason}</small>
            </>
          )}
          emptyText="No stage movement proposals."
        />
      </div>
      <DaisyLane
        title="Evidence requests"
        items={report.contributorEvidenceRequests}
        renderItem={(item) => (
          <>
            <strong>{item.requestType}</strong>
            <small>{item.detail}</small>
          </>
        )}
        emptyText="No contributor evidence requests."
      />
    </article>
  );
}

function DaisyLane({ title, items, renderItem, emptyText }) {
  return (
    <div className="daisy-lane">
      <p className="context-label">{title}</p>
      {items.length ? (
        <div className="daisy-lane-list">
          {items.map((item, index) => (
            <div key={`${title}-${index}`} className="daisy-lane-item">
              {renderItem(item)}
            </div>
          ))}
        </div>
      ) : (
        <p className="daisy-empty-text">{emptyText}</p>
      )}
    </div>
  );
}

function SplitReviewCard({ child, index, reviewState, onUpdate }) {
  const isRequestChanges = reviewState.action === 'request_changes';
  const needsDetail = ['request_changes', 'more_evidence', 'rename_classify', 'duplicate', 'defer'].includes(
    reviewState.action
  );
  const statusLabels = {
    approved: 'Approve selected',
    rejected: 'Reject selected',
    request_changes: 'Changes requested',
    more_evidence: 'Evidence requested',
    rename_classify: 'Rename/classify',
    duplicate: 'Duplicate selected',
    deleted: 'Delete selected',
    defer: 'Deferred',
    pending: 'Awaiting decision'
  };
  const actionOptions = [
    ['approved', 'Approve', 'primary'],
    ['rejected', 'Reject', 'secondary danger'],
    ['request_changes', 'Request Changes', 'secondary'],
    ['more_evidence', 'More Evidence', 'secondary'],
    ['rename_classify', 'Rename/Classify', 'secondary'],
    ['duplicate', 'Duplicate', 'secondary'],
    ['deleted', 'Delete', 'secondary danger'],
    ['defer', 'Defer', 'secondary']
  ];

  return (
    <article className={`split-review-card action-${reviewState.action}`} role="listitem">
      <div className="split-card-preview">
        <SplitSymbolPreview child={child} variant={index % 2 === 1} />
      </div>
      <div className="split-card-body">
        <div className="split-card-header">
          <div>
            <p className="context-label">Proposed child record</p>
            <h4>{child.proposedSymbolName}</h4>
          </div>
        </div>
        <div className="split-meta-grid">
          <Fact label="ID" value={displaySymbolId(child)} />
          <Fact label="Child file" value={child.fileName} />
          <Fact label="Parent file" value={child.parentFileName || 'Not recorded'} />
          <Fact label="Name source" value={child.nameSource || 'Not recorded'} />
        </div>
        <label className="field">
          <span>Review note for this symbol</span>
          <textarea
            rows="3"
            value={reviewState.note}
            onChange={(event) => onUpdate(child.id, { note: event.target.value })}
            placeholder="Add a note to this extracted symbol record for traceability or downstream review."
          />
        </label>
        <div className="split-decision-row">
          <div className="action-stack horizontal split-actions">
            {actionOptions.map(([action, label]) => (
              <button
                key={action}
                type="button"
                className={`action-button split-action-button split-action-${action} ${reviewState.action === action ? 'selected' : ''}`}
                onClick={() => onUpdate(child.id, { action: reviewState.action === action ? 'pending' : action })}
              >
                {label}
              </button>
            ))}
          </div>
          <span className={`review-status split-decision-status review-${reviewState.action}`}>
            {statusLabels[reviewState.action] || statusLabels.pending}
          </span>
        </div>
        {needsDetail ? (
          <label className="field request-field">
            <span>{isRequestChanges ? 'Requested changes' : 'Reviewer instruction'}</span>
            <textarea
              rows="4"
              value={reviewState.requestDetails}
              onChange={(event) => onUpdate(child.id, { requestDetails: event.target.value })}
              placeholder="Describe the evidence, naming, classification, duplicate, deletion, or deferral detail needed for this decision."
            />
          </label>
        ) : null}
      </div>
    </article>
  );
}

function SplitSymbolPreview({ child, variant = false }) {
  const resolvedPreviewUrl = child.previewUrl
    ? new URL(child.previewUrl, resolveAbsoluteBase()).toString()
    : null;
  const [imageUnavailable, setImageUnavailable] = useState(!resolvedPreviewUrl);

  useEffect(() => {
    setImageUnavailable(!resolvedPreviewUrl);
  }, [resolvedPreviewUrl]);

  if (!imageUnavailable && resolvedPreviewUrl) {
    return (
      <img
        className="split-symbol-image"
        src={resolvedPreviewUrl}
        alt={`Preview of ${child.proposedSymbolName}`}
        onError={() => setImageUnavailable(true)}
      />
    );
  }

  return (
    <div className="split-symbol-fallback" aria-label={`Preview unavailable for ${child.proposedSymbolName}`}>
      <SymbolGlyph symbolId={child.proposedSymbolId} large variant={variant} />
      <small>Preview unavailable</small>
    </div>
  );
}

function readSubmissionDetailsCookie() {
  const cookie = document.cookie
    .split('; ')
    .find((item) => item.startsWith('symgov_submitter_details='));

  if (!cookie) {
    return {};
  }

  try {
    return JSON.parse(decodeURIComponent(cookie.split('=').slice(1).join('=')));
  } catch {
    return {};
  }
}

function writeSubmissionDetailsCookie(details) {
  const encoded = encodeURIComponent(JSON.stringify(details));
  document.cookie = `symgov_submitter_details=${encoded}; max-age=31536000; path=/; SameSite=Lax`;
}

function clearSubmissionDetailsCookie() {
  document.cookie = 'symgov_submitter_details=; max-age=0; path=/; SameSite=Lax';
}

function formatFileSize(bytes) {
  if (!Number.isFinite(bytes)) {
    return 'Unknown size';
  }
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1024 * 1024) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function fileFormatLabel(file) {
  const extension = file.name.includes('.') ? file.name.split('.').pop().toUpperCase() : '';
  return extension || file.type || 'Unknown';
}

function formatCountdown(seconds) {
  const safeSeconds = Math.max(0, Math.ceil(seconds));
  const minutes = Math.floor(safeSeconds / 60);
  const remainder = safeSeconds % 60;
  return `${minutes}:${String(remainder).padStart(2, '0')}`;
}

function formatScottSearchTimer(searchState) {
  if (!searchState.result) {
    return formatCountdown(SCOTT_SOURCE_SEARCH_DURATION_SECONDS);
  }
  if (searchState.remainingSeconds > 0) {
    return formatCountdown(searchState.remainingSeconds);
  }
  return 'Progress Saved';
}

function scottSearchStatusText(searchState) {
  if (!searchState.result) {
    return '';
  }
  if (searchState.result.queueItemId === 'starting') {
    return searchState.stopping ? 'Stopping Scott source search.' : 'Starting Scott source search.';
  }

  const statusLabel =
    searchState.result.status === 'cancelled'
      ? 'Stopped'
      : searchState.result.status === 'stop_requested'
        ? 'Stop Requested'
      : searchState.remainingSeconds > 0
        ? 'Searching'
        : 'Progress Saved';
  return `Scott queue item ${searchState.result.queueItemId} is ${statusLabel}.`;
}

function formatHannahCurationTimer(curationState) {
  if (!curationState.result) {
    return formatCountdown(HANNAH_CURATION_SEARCH_DURATION_SECONDS);
  }
  if (curationState.remainingSeconds > 0) {
    return formatCountdown(curationState.remainingSeconds);
  }
  return 'Progress Saved';
}

function hannahCurationStatusText(curationState) {
  if (!curationState.result) {
    return '';
  }
  if (curationState.result.queueItemId === 'starting') {
    return 'Starting Hannah curation search.';
  }
  if (curationState.result.message) {
    return curationState.result.message;
  }
  const statusLabel = curationState.remainingSeconds > 0 ? 'Searching' : 'Progress Saved';
  return `Hannah queue item ${curationState.result.queueItemId} is ${statusLabel}.`;
}

function formatWhitneyDemandScanTimer(scanState) {
  if (!scanState.result) {
    return formatCountdown(WHITNEY_DEMAND_SCAN_DURATION_SECONDS);
  }
  if (scanState.remainingSeconds > 0) {
    return formatCountdown(scanState.remainingSeconds);
  }
  return 'Signals Saved';
}

function whitneyDemandScanStatusText(scanState) {
  if (!scanState.result) {
    return '';
  }
  if (scanState.result.queueItemId === 'starting') {
    return 'Starting Whitney demand scan.';
  }
  const statusLabel = scanState.remainingSeconds > 0 ? 'Sensing' : 'Signals Saved';
  return `Whitney queue item ${scanState.result.queueItemId} is ${statusLabel}.`;
}

function useWhitneyDemandSensingControls({ enabled = true, intelligenceActive = false } = {}) {
  const stopRequestedRef = useRef(false);
  const [scanState, setScanState] = useState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });
  const [signalsSort, setSignalsSort] = useState({ key: 'lastSeenAt', direction: 'desc' });
  const [signalFilters, setSignalFilters] = useState({});
  const [signalsState, setSignalsState] = useState({
    loading: false,
    loadingMore: false,
    error: '',
    items: [],
    total: 0,
    hasMore: false
  });

  useEffect(() => {
    if (!scanState.result?.expectedCompletedAt) {
      return undefined;
    }

    const updateRemaining = () => {
      const target = new Date(scanState.result.expectedCompletedAt).getTime();
      const remainingSeconds = Number.isFinite(target) ? Math.max(0, Math.ceil((target - Date.now()) / 1000)) : 0;
      setScanState((current) => ({
        ...current,
        pending: remainingSeconds > 0 && current.result?.status === 'sensing' && !current.stopping,
        remainingSeconds
      }));
    };

    updateRemaining();
    const timer = window.setInterval(updateRemaining, 1000);
    return () => window.clearInterval(timer);
  }, [scanState.result]);

  useEffect(() => {
    if (!enabled || !intelligenceActive) {
      return undefined;
    }

    let cancelled = false;
    setSignalsState((current) => ({ ...current, loading: true, loadingMore: false, error: '' }));

    fetchWhitneyDemandSignals({
      offset: 0,
      limit: WHITNEY_SIGNAL_PAGE_SIZE,
      sort: signalsSort.key,
      direction: signalsSort.direction,
      filters: signalFilters
    }).then((payload) => {
      if (cancelled) {
        return;
      }

      setSignalsState({
        loading: false,
        loadingMore: false,
        error: payload.ok ? '' : payload.message,
        items: payload.ok ? payload.items : [],
        total: payload.ok ? payload.total : 0,
        hasMore: payload.ok ? payload.hasMore : false
      });
    });

    return () => {
      cancelled = true;
    };
  }, [enabled, intelligenceActive, signalsSort.key, signalsSort.direction, signalFilters]);

  function updateSignalFilter(key, value) {
    setSignalFilters((current) => ({ ...current, [key]: value }));
  }

  function handleSignalSort(key) {
    setSignalsSort((current) => ({
      key,
      direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc'
    }));
  }

  function loadMoreWhitneySignals() {
    if (!enabled || !intelligenceActive || signalsState.loading || signalsState.loadingMore || !signalsState.hasMore) {
      return;
    }

    const offset = signalsState.items.length;
    setSignalsState((current) => ({ ...current, loadingMore: true, error: '' }));

    fetchWhitneyDemandSignals({
      offset,
      limit: WHITNEY_SIGNAL_PAGE_SIZE,
      sort: signalsSort.key,
      direction: signalsSort.direction,
      filters: signalFilters
    }).then((payload) => {
      setSignalsState((current) => ({
        loading: false,
        loadingMore: false,
        error: payload.ok ? '' : payload.message,
        items: payload.ok ? [...current.items, ...payload.items] : current.items,
        total: payload.ok ? payload.total : current.total,
        hasMore: payload.ok ? payload.hasMore : current.hasMore
      }));
    });
  }

  function handleWhitneySignalsScroll(event) {
    const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
    if (scrollHeight - scrollTop - clientHeight < 120) {
      loadMoreWhitneySignals();
    }
  }

  function handleStartWhitneyDemandScan() {
    if (!enabled) {
      return;
    }

    const durationSeconds = WHITNEY_DEMAND_SCAN_DURATION_SECONDS;
    stopRequestedRef.current = false;
    const startedAt = new Date();
    const expectedCompletedAt = new Date(startedAt.getTime() + durationSeconds * 1000).toISOString();
    setScanState({
      pending: true,
      stopping: false,
      result: {
        queueItemId: 'starting',
        status: 'sensing',
        durationSeconds,
        startedAt: startedAt.toISOString(),
        expectedCompletedAt
      },
      error: '',
      remainingSeconds: durationSeconds
    });

    startWhitneyDemandScan({ durationSeconds }).then((payload) => {
      if (stopRequestedRef.current) {
        setScanState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });
        return stopWhitneyDemandScan(payload.queueItemId).catch(() => {});
      }

      setScanState({
        pending: payload.status === 'sensing',
        stopping: false,
        result: payload,
        error: '',
        remainingSeconds: payload.durationSeconds || WHITNEY_DEMAND_SCAN_DURATION_SECONDS
      });
    }).catch((scanError) => {
      setScanState({
        pending: false,
        stopping: false,
        result: null,
        error: scanError instanceof Error ? scanError.message : 'Whitney demand scan could not be started.',
        remainingSeconds: 0
      });
    });
  }

  function handleStopWhitneyDemandScan() {
    const queueItemId = scanState.result?.queueItemId;
    if (!queueItemId) {
      return;
    }

    stopRequestedRef.current = true;
    setScanState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });

    if (queueItemId === 'starting') {
      return;
    }

    stopWhitneyDemandScan(queueItemId).catch(() => {});
  }

  return {
    scanState,
    signalsSort,
    signalFilters,
    signalsState,
    handleStartWhitneyDemandScan,
    handleStopWhitneyDemandScan,
    handleSignalSort,
    updateSignalFilter,
    handleWhitneySignalsScroll
  };
}

function useHannahCurationControls({ enabled = true, curationActive = false } = {}) {
  const stopRequestedRef = useRef(false);
  const [curationState, setCurationState] = useState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });
  const [candidatesSort, setCandidatesSort] = useState({ key: 'lastSeenAt', direction: 'desc' });
  const [candidateFilters, setCandidateFilters] = useState({});
  const [candidatesState, setCandidatesState] = useState({
    loading: false,
    loadingMore: false,
    error: '',
    items: [],
    total: 0,
    hasMore: false
  });

  useEffect(() => {
    if (!curationState.result?.expectedCompletedAt) {
      return undefined;
    }

    const updateRemaining = () => {
      const target = new Date(curationState.result.expectedCompletedAt).getTime();
      const remainingSeconds = Number.isFinite(target) ? Math.max(0, Math.ceil((target - Date.now()) / 1000)) : 0;
      setCurationState((current) => ({
        ...current,
        pending: remainingSeconds > 0 && current.result?.status === 'searching' && !current.stopping,
        remainingSeconds
      }));
    };

    updateRemaining();
    const timer = window.setInterval(updateRemaining, 1000);
    return () => window.clearInterval(timer);
  }, [curationState.result]);

  useEffect(() => {
    if (!enabled || !curationActive) {
      return undefined;
    }

    let cancelled = false;
    setCandidatesState((current) => ({ ...current, loading: true, loadingMore: false, error: '' }));

    fetchHannahPhotoCandidates({
      offset: 0,
      limit: HANNAH_CANDIDATE_PAGE_SIZE,
      sort: candidatesSort.key,
      direction: candidatesSort.direction,
      filters: candidateFilters
    }).then((payload) => {
      if (cancelled) {
        return;
      }

      setCandidatesState({
        loading: false,
        loadingMore: false,
        error: payload.ok ? '' : payload.message,
        items: payload.ok ? payload.items : [],
        total: payload.ok ? payload.total : 0,
        hasMore: payload.ok ? payload.hasMore : false
      });
    });

    return () => {
      cancelled = true;
    };
  }, [enabled, curationActive, candidatesSort.key, candidatesSort.direction, candidateFilters]);

  function updateCandidateFilter(key, value) {
    setCandidateFilters((current) => ({ ...current, [key]: value }));
  }

  function handleCandidateSort(key) {
    setCandidatesSort((current) => ({
      key,
      direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc'
    }));
  }

  function loadMoreHannahCandidates() {
    if (!enabled || !curationActive || candidatesState.loading || candidatesState.loadingMore || !candidatesState.hasMore) {
      return;
    }

    const offset = candidatesState.items.length;
    setCandidatesState((current) => ({ ...current, loadingMore: true, error: '' }));

    fetchHannahPhotoCandidates({
      offset,
      limit: HANNAH_CANDIDATE_PAGE_SIZE,
      sort: candidatesSort.key,
      direction: candidatesSort.direction,
      filters: candidateFilters
    }).then((payload) => {
      setCandidatesState((current) => ({
        loading: false,
        loadingMore: false,
        error: payload.ok ? '' : payload.message,
        items: payload.ok ? [...current.items, ...payload.items] : current.items,
        total: payload.ok ? payload.total : current.total,
        hasMore: payload.ok ? payload.hasMore : current.hasMore
      }));
    });
  }

  function handleHannahCandidatesScroll(event) {
    const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
    if (scrollHeight - scrollTop - clientHeight < 120) {
      loadMoreHannahCandidates();
    }
  }

  function handleStartHannahCuration() {
    if (!enabled) {
      return;
    }

    const durationSeconds = HANNAH_CURATION_SEARCH_DURATION_SECONDS;
    stopRequestedRef.current = false;
    const startedAt = new Date();
    const expectedCompletedAt = new Date(startedAt.getTime() + durationSeconds * 1000).toISOString();
    setCurationState({
      pending: true,
      stopping: false,
      result: {
        queueItemId: 'starting',
        status: 'searching',
        durationSeconds,
        startedAt: startedAt.toISOString(),
        expectedCompletedAt
      },
      error: '',
      remainingSeconds: durationSeconds
    });

    startHannahCurationSearch({ durationSeconds }).then((payload) => {
      if (stopRequestedRef.current) {
        setCurationState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });
        return stopHannahCurationSearch(payload.queueItemId).catch(() => {});
      }

      setCurationState({
        pending: payload.status === 'searching',
        stopping: false,
        result: payload,
        error: '',
        remainingSeconds: payload.durationSeconds || HANNAH_CURATION_SEARCH_DURATION_SECONDS
      });
    }).catch((curationError) => {
      setCurationState({
        pending: false,
        stopping: false,
        result: null,
        error: curationError instanceof Error ? curationError.message : 'Hannah curation search could not be started.',
        remainingSeconds: 0
      });
    });
  }

  function handleStopHannahCuration() {
    const queueItemId = curationState.result?.queueItemId;
    if (!queueItemId) {
      return;
    }

    stopRequestedRef.current = true;
    setCurationState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });

    if (queueItemId === 'starting') {
      return;
    }

    stopHannahCurationSearch(queueItemId).catch(() => {});
  }

  return {
    curationState,
    candidatesSort,
    candidateFilters,
    candidatesState,
    handleStartHannahCuration,
    handleStopHannahCuration,
    handleCandidateSort,
    updateCandidateFilter,
    handleHannahCandidatesScroll
  };
}

function useScottSourceDiscoveryControls({ enabled = true, sourcesActive = false, onSearchStarted, onSearchStopped } = {}) {
  const stopRequestedRef = useRef(false);
  const [searchState, setSearchState] = useState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });
  const [sourcesSort, setSourcesSort] = useState({ key: 'lastSeenAt', direction: 'desc' });
  const [sourceFilters, setSourceFilters] = useState({});
  const [sourcesState, setSourcesState] = useState({
    loading: false,
    loadingMore: false,
    error: '',
    items: [],
    total: 0,
    hasMore: false
  });

  useEffect(() => {
    if (!searchState.result?.expectedCompletedAt) {
      return undefined;
    }

    const updateRemaining = () => {
      const target = new Date(searchState.result.expectedCompletedAt).getTime();
      const remainingSeconds = Number.isFinite(target) ? Math.max(0, Math.ceil((target - Date.now()) / 1000)) : 0;
      setSearchState((current) => ({
        ...current,
        pending: remainingSeconds > 0 && current.result?.status === 'searching' && !current.stopping,
        remainingSeconds
      }));
    };

    updateRemaining();
    const timer = window.setInterval(updateRemaining, 1000);
    return () => window.clearInterval(timer);
  }, [searchState.result]);

  useEffect(() => {
    if (!enabled || !sourcesActive) {
      return undefined;
    }

    let cancelled = false;
    setSourcesState((current) => ({ ...current, loading: true, loadingMore: false, error: '' }));

    fetchScottSourceSites({
      offset: 0,
      limit: SCOTT_SOURCE_SITE_PAGE_SIZE,
      sort: sourcesSort.key,
      direction: sourcesSort.direction,
      filters: sourceFilters
    }).then((payload) => {
      if (cancelled) {
        return;
      }

      setSourcesState({
        loading: false,
        loadingMore: false,
        error: payload.ok ? '' : payload.message,
        items: payload.ok ? payload.items : [],
        total: payload.ok ? payload.total : 0,
        hasMore: payload.ok ? payload.hasMore : false
      });
    });

    return () => {
      cancelled = true;
    };
  }, [enabled, sourcesActive, sourcesSort.key, sourcesSort.direction, sourceFilters]);

  function updateSourceFilter(key, value) {
    setSourceFilters((current) => ({ ...current, [key]: value }));
  }

  function handleSourceSort(key) {
    setSourcesSort((current) => ({
      key,
      direction: current.key === key && current.direction === 'asc' ? 'desc' : 'asc'
    }));
  }

  function loadMoreScottSources() {
    if (!enabled || !sourcesActive || sourcesState.loading || sourcesState.loadingMore || !sourcesState.hasMore) {
      return;
    }

    const offset = sourcesState.items.length;
    setSourcesState((current) => ({ ...current, loadingMore: true, error: '' }));

    fetchScottSourceSites({
      offset,
      limit: SCOTT_SOURCE_SITE_PAGE_SIZE,
      sort: sourcesSort.key,
      direction: sourcesSort.direction,
      filters: sourceFilters
    }).then((payload) => {
      setSourcesState((current) => ({
        loading: false,
        loadingMore: false,
        error: payload.ok ? '' : payload.message,
        items: payload.ok ? [...current.items, ...payload.items] : current.items,
        total: payload.ok ? payload.total : current.total,
        hasMore: payload.ok ? payload.hasMore : current.hasMore
      }));
    });
  }

  function handleScottSourcesScroll(event) {
    const { scrollTop, scrollHeight, clientHeight } = event.currentTarget;
    if (scrollHeight - scrollTop - clientHeight < 120) {
      loadMoreScottSources();
    }
  }

  function updateSourceSitePrompt(sourceSiteId, updatedSite) {
    setSourcesState((current) => ({
      ...current,
      items: current.items.map((site) => (site.id === sourceSiteId ? { ...site, ...updatedSite } : site))
    }));
  }

  function updateSourceSiteIncludeNextRun(sourceSiteId, updatedSite) {
    setSourcesState((current) => ({
      ...current,
      items: current.items.map((site) => (site.id === sourceSiteId ? { ...site, ...updatedSite } : site))
    }));
  }

  function updateSourceSiteStatus(sourceSiteId, updatedSite) {
    setSourcesState((current) => ({
      ...current,
      items: current.items.map((site) => (site.id === sourceSiteId ? { ...site, ...updatedSite } : site))
    }));
  }

  function handleStartScottSearch() {
    if (!enabled) {
      return;
    }

    stopRequestedRef.current = false;
    const durationSeconds = SCOTT_SOURCE_SEARCH_DURATION_SECONDS;
    const startedAt = new Date();
    const expectedCompletedAt = new Date(startedAt.getTime() + durationSeconds * 1000).toISOString();
    setSearchState({
      pending: true,
      stopping: false,
      result: {
        queueItemId: 'starting',
        status: 'searching',
        durationSeconds,
        startedAt: startedAt.toISOString(),
        expectedCompletedAt
      },
      error: '',
      remainingSeconds: durationSeconds
    });

    startScottSourceSearch({
      durationSeconds,
      seedQuery: 'commons.wikimedia.org P&ID symbols'
    }).then((payload) => {
      if (stopRequestedRef.current) {
        onSearchStopped?.({
          queueItemId: payload.queueItemId,
          remainingSeconds: SCOTT_SOURCE_SEARCH_DURATION_SECONDS
        });
        setSearchState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });
        return stopScottSourceSearch(payload.queueItemId).catch(() => {});
      }

      onSearchStarted?.(payload.queueItemId);
      setSearchState({
        pending: payload.status === 'searching',
        stopping: false,
        result: payload,
        error: '',
        remainingSeconds: payload.durationSeconds || SCOTT_SOURCE_SEARCH_DURATION_SECONDS
      });
    }).catch((searchError) => {
      setSearchState({
        pending: false,
        stopping: false,
        result: null,
        error: searchError instanceof Error ? searchError.message : 'Scott search could not be started.',
        remainingSeconds: 0
      });
    });
  }

  function handleStopScottSearch() {
    const queueItemId = searchState.result?.queueItemId;
    if (!queueItemId) {
      return;
    }

    stopRequestedRef.current = true;
    const stoppedRemainingSeconds = searchState.remainingSeconds || SCOTT_SOURCE_SEARCH_DURATION_SECONDS;
    onSearchStopped?.({ queueItemId, remainingSeconds: stoppedRemainingSeconds });
    setSearchState({ pending: false, stopping: false, result: null, error: '', remainingSeconds: 0 });

    if (queueItemId === 'starting') {
      return;
    }

    stopScottSourceSearch(queueItemId).catch(() => {});
  }

  return {
    searchState,
    sourcesSort,
    sourceFilters,
    sourcesState,
    handleStartScottSearch,
    handleStopScottSearch,
    handleSourceSort,
    updateSourceFilter,
    updateSourceSitePrompt,
    updateSourceSiteIncludeNextRun,
    updateSourceSiteStatus,
    handleScottSourcesScroll
  };
}

function SubmissionPage() {
  const rememberedDetails = useMemo(readSubmissionDetailsCookie, []);
  const [healthState, setHealthState] = useState({ loading: true, mode: 'loading', message: 'Checking API…' });
  const [isPending, startTransition] = useTransition();
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [rememberDetails, setRememberDetails] = useState(true);
  const [formState, setFormState] = useState({
    submitterName: rememberedDetails.submitterName || '',
    submitterEmail: rememberedDetails.submitterEmail || '',
    pin: '',
    description: submissionPresets[0],
    files: []
  });

  useEffect(() => {
    let cancelled = false;

    fetchHealth().then((health) => {
      if (!cancelled) {
        setHealthState({ loading: false, ...health });
      }
    });

    return () => {
      cancelled = true;
    };
  }, []);

  const isDisabled = isPending || healthState.mode !== 'live';

  function updateField(field, value) {
    setFormState((current) => ({ ...current, [field]: value }));
  }

  useEffect(() => {
    if (rememberDetails) {
      writeSubmissionDetailsCookie({
        submitterName: formState.submitterName,
        submitterEmail: formState.submitterEmail
      });
      return;
    }

    clearSubmissionDetailsCookie();
  }, [rememberDetails, formState.submitterName, formState.submitterEmail]);

  function handleSubmit(event) {
    event.preventDefault();
    setError('');
    setResult(null);

    startTransition(async () => {
      try {
        const payload = await submitExternalSubmission(formState);
        setResult(payload);
      } catch (submissionError) {
        setError(submissionError instanceof Error ? submissionError.message : 'Submission failed.');
      }
    });
  }

  return (
    <section className="experience-shell">
      <div className="hero-panel glass-panel standards-hero page-title-row">
        <div>
          <p className="eyebrow">External intake</p>
          <h2>Upload symbol files for processing</h2>
          <p className="title-support">Accepted: SVG, PNG, JPG, JSON</p>
        </div>
      </div>
      <p className={`page-status-text status-${healthState.mode}`}>
        {healthState.loading ? 'Checking API...' : `${healthState.message}${appConfig.apiRoot ? ` · ${appConfig.apiRoot}` : ''}`}
      </p>

      <form className="submission-grid" onSubmit={handleSubmit}>
        <section className="glass-panel pane form-panel">
          <SectionHeading title="Your details" subtitle="Used by Symgov if we need to follow up" />
          <label className="field">
            <span>Submitter name</span>
            <input
              required
              value={formState.submitterName}
              onChange={(event) => updateField('submitterName', event.target.value)}
            />
          </label>
          <label className="field">
            <span>Submitter email</span>
            <input
              required
              type="email"
              value={formState.submitterEmail}
              onChange={(event) => updateField('submitterEmail', event.target.value)}
            />
          </label>
          <label className="field">
            <span>Submission PIN</span>
            <input
              required
              type="password"
              inputMode="numeric"
              pattern="[0-9]{4}"
              maxLength="4"
              value={formState.pin}
              onChange={(event) => updateField('pin', event.target.value)}
            />
          </label>
          <label className="checkbox-row remember-row">
            <input
              type="checkbox"
              checked={rememberDetails}
              onChange={(event) => setRememberDetails(event.target.checked)}
            />
            <span>Remember these details</span>
          </label>
          <p className="muted-text">Name and email can be remembered on this device. The PIN is never saved.</p>
        </section>

        <section className="glass-panel pane form-panel">
          <SectionHeading title="Files and summary" subtitle="Describe what these symbols are for" />
          <label className="field">
            <span>Submission summary</span>
            <textarea
              rows="5"
              value={formState.description}
              onChange={(event) => updateField('description', event.target.value)}
            />
          </label>
          <label className="field">
            <span>Files</span>
            <input
              required
              type="file"
              accept=".svg,.png,.jpg,.jpeg,.json"
              multiple
              onChange={(event) => updateField('files', Array.from(event.target.files || []))}
            />
          </label>
          {formState.files.length ? (
            <div className="selected-file-list">
              {formState.files.map((file) => (
                <div key={`${file.name}-${file.size}`} className="selected-file-row">
                  <strong>{file.name}</strong>
                  <span>{fileFormatLabel(file)} · {formatFileSize(file.size)}</span>
                </div>
              ))}
            </div>
          ) : (
            <p className="muted-text">Select one or more files to send into the live intake path.</p>
          )}
          <div className="action-stack horizontal">
            <button type="submit" className="action-button primary" disabled={isDisabled}>
              {isPending ? 'Submitting…' : 'Submit'}
            </button>
          </div>
          {error ? <p className="error-text">{error}</p> : null}
          {result ? (
            <div className="result-card">
              <p className="success-text">Submission accepted</p>
            </div>
          ) : null}
        </section>
      </form>
    </section>
  );
}

function ScottSourcesPanel({ state, sort, filters, onSort, onFilterChange, onPromptSaved, onIncludeNextRunSaved, onStatusSaved, onScroll }) {
  const [promptDrafts, setPromptDrafts] = useState({});
  const [promptSaves, setPromptSaves] = useState({});
  const [includeNextRunSaves, setIncludeNextRunSaves] = useState({});
  const [statusSaves, setStatusSaves] = useState({});

  function handlePromptChange(siteId, value) {
    setPromptDrafts((current) => ({ ...current, [siteId]: value }));
  }

  function handlePromptSave(site) {
    const sourcePrompt = promptDrafts[site.id] ?? site.sourcePrompt ?? '';
    setPromptSaves((current) => ({ ...current, [site.id]: { pending: true, error: '' } }));
    updateScottSourceSitePrompt(site.id, sourcePrompt).then((updatedSite) => {
      onPromptSaved?.(site.id, updatedSite);
      setPromptDrafts((current) => {
        const next = { ...current };
        delete next[site.id];
        return next;
      });
      setPromptSaves((current) => ({ ...current, [site.id]: { pending: false, error: '' } }));
    }).catch((error) => {
      setPromptSaves((current) => ({
        ...current,
        [site.id]: {
          pending: false,
          error: error instanceof Error ? error.message : 'Prompt could not be saved.'
        }
      }));
    });
  }

  function handleIncludeNextRunChange(site, includeNextRun) {
    setIncludeNextRunSaves((current) => ({ ...current, [site.id]: { pending: true, error: '' } }));
    updateScottSourceSiteIncludeNextRun(site.id, includeNextRun).then((updatedSite) => {
      onIncludeNextRunSaved?.(site.id, updatedSite);
      setIncludeNextRunSaves((current) => ({ ...current, [site.id]: { pending: false, error: '' } }));
    }).catch((error) => {
      setIncludeNextRunSaves((current) => ({
        ...current,
        [site.id]: {
          pending: false,
          error: error instanceof Error ? error.message : 'Next-run flag could not be saved.'
        }
      }));
    });
  }

  function handleStatusChange(site, status) {
    setStatusSaves((current) => ({ ...current, [site.id]: { pending: true, error: '' } }));
    updateScottSourceSiteStatus(site.id, status).then((updatedSite) => {
      onStatusSaved?.(site.id, updatedSite);
      setStatusSaves((current) => ({ ...current, [site.id]: { pending: false, error: '' } }));
    }).catch((error) => {
      setStatusSaves((current) => ({
        ...current,
        [site.id]: {
          pending: false,
          error: error instanceof Error ? error.message : 'Status could not be saved.'
        }
      }));
    });
  }

  return (
    <section className="glass-panel pane scott-sources-panel">
      <div className="scott-sources-toolbar">
        <div>
          <p className="eyebrow">Scott memory</p>
          <h3>Sources</h3>
        </div>
        <div className="scott-sources-summary">
          <span>{state.loading ? 'Loading...' : `${state.items.length} of ${state.total} shown`}</span>
        </div>
      </div>

      {state.error ? <p className="error-text">{state.error}</p> : null}

      <div className="scott-sources-grid-shell" onScroll={onScroll}>
        <table className="scott-sources-grid">
          <thead>
            <tr>
              {SCOTT_SOURCE_COLUMNS.map(([key, label]) => (
                <th key={key} scope="col">
                  <button type="button" className="source-column-sort" onClick={() => onSort(key)}>
                    <span>{label}</span>
                    <span className="source-sort-indicator">{sort.key === key ? (sort.direction === 'asc' ? 'Up' : 'Down') : ''}</span>
                  </button>
                  {key === 'status' ? (
                    <ScottSourceStatusFilter
                      value={filters[key] || ''}
                      onChange={(value) => onFilterChange(key, value)}
                    />
                  ) : key === 'includeNextRun' ? (
                    <select
                      value={filters[key] || ''}
                      onChange={(event) => onFilterChange(key, event.target.value)}
                      aria-label={`Filter ${label}`}
                    >
                      <option value="">All</option>
                      <option value="true">Checked</option>
                      <option value="false">Unchecked</option>
                    </select>
                  ) : (
                    <input
                      type="search"
                      value={filters[key] || ''}
                      onChange={(event) => onFilterChange(key, event.target.value)}
                      aria-label={`Filter ${label}`}
                    />
                  )}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {state.items.map((site) => (
              <tr key={site.id}>
                {SCOTT_SOURCE_COLUMNS.map(([key]) => (
                  <td key={key} className={`source-cell source-cell-${key}`}>
                    {key === 'status' ? (
                      <ScottSourceStatusCell
                        site={site}
                        saveState={statusSaves[site.id]}
                        onChange={handleStatusChange}
                      />
                    ) : key === 'includeNextRun' ? (
                      <ScottSourceIncludeNextRunCell
                        site={site}
                        saveState={includeNextRunSaves[site.id]}
                        onChange={handleIncludeNextRunChange}
                      />
                    ) : key === 'sourcePrompt' ? (
                      <ScottSourcePromptCell
                        site={site}
                        draftValue={promptDrafts[site.id]}
                        saveState={promptSaves[site.id]}
                        onChange={handlePromptChange}
                        onSave={handlePromptSave}
                      />
                    ) : (
                      formatScottSourceValue(site, key)
                    )}
                  </td>
                ))}
              </tr>
            ))}
            {!state.loading && !state.items.length ? (
              <tr>
                <td colSpan={SCOTT_SOURCE_COLUMNS.length} className="source-empty-cell">
                  No source records match the current filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {state.loadingMore ? <p className="source-loading-row">Loading more sources...</p> : null}
      </div>
    </section>
  );
}

function ScottSourceStatusFilter({ value, onChange }) {
  const selected = new Set(
    String(value || '')
      .split(',')
      .map((status) => status.trim())
      .filter(Boolean)
  );

  function handleToggle(status, checked) {
    const next = new Set(selected);
    if (checked) {
      next.add(status);
    } else {
      next.delete(status);
    }
    onChange(Array.from(next).join(','));
  }

  return (
    <details className="source-status-filter">
      <summary>{selected.size ? `${selected.size} selected` : 'All'}</summary>
      <div className="source-status-filter-menu">
        {SCOTT_SOURCE_STATUS_OPTIONS.map(([status, label]) => (
          <label key={status}>
            <input
              type="checkbox"
              checked={selected.has(status)}
              onChange={(event) => handleToggle(status, event.target.checked)}
            />
            <span>{label}</span>
          </label>
        ))}
      </div>
    </details>
  );
}

function ScottSourceStatusCell({ site, saveState, onChange }) {
  const statusValue = normalizeScottSourceStatus(site?.status);
  return (
    <div className="source-status-editor">
      <select
        value={statusValue}
        disabled={Boolean(saveState?.pending)}
        onChange={(event) => onChange(site, event.target.value)}
        aria-label={`Status for ${site.domain || site.url}`}
      >
        {SCOTT_SOURCE_STATUS_OPTIONS.map(([status, label]) => (
          <option key={status} value={status}>{label}</option>
        ))}
      </select>
      {saveState?.pending ? <span className="source-status-saving">Saving</span> : null}
      {saveState?.error ? <span className="source-prompt-error">{saveState.error}</span> : null}
    </div>
  );
}

function ScottSourceIncludeNextRunCell({ site, saveState, onChange }) {
  return (
    <label className="source-next-run-toggle">
      <input
        type="checkbox"
        checked={Boolean(site?.includeNextRun)}
        disabled={Boolean(saveState?.pending)}
        onChange={(event) => onChange(site, event.target.checked)}
        aria-label={`Include ${site.domain || site.url} in Scott's next run`}
      />
      <span>{saveState?.pending ? 'Saving' : site?.includeNextRun ? 'Checked' : 'Off'}</span>
      {saveState?.error ? <span className="source-prompt-error">{saveState.error}</span> : null}
    </label>
  );
}

function ScottSourcePromptCell({ site, draftValue, saveState, onChange, onSave }) {
  const isCandidate = String(site?.status || '').toLowerCase() === 'candidate';
  if (!isCandidate) {
    return site?.sourcePrompt ? <span>{site.sourcePrompt}</span> : <span className="source-prompt-disabled">Candidate only</span>;
  }

  const value = draftValue ?? site.sourcePrompt ?? '';
  const pending = Boolean(saveState?.pending);
  const isDirty = value !== (site.sourcePrompt ?? '');

  return (
    <div className="source-prompt-editor">
      <textarea
        value={value}
        rows="4"
        maxLength="4000"
        onChange={(event) => onChange(site.id, event.target.value)}
        placeholder="Access notes, login steps, or download instructions for Scott"
        aria-label={`Scott prompt for ${site.domain || site.url}`}
      />
      <div className="source-prompt-actions">
        <button
          type="button"
          className="mini-action-button"
          disabled={pending || !isDirty}
          onClick={() => onSave(site)}
        >
          {pending ? 'Saving...' : 'Save'}
        </button>
        {saveState?.error ? <span className="source-prompt-error">{saveState.error}</span> : null}
      </div>
    </div>
  );
}

function formatScottSourceValue(site, key) {
  const value = site?.[key];
  if (key === 'symbolFormats') {
    return Array.isArray(value) && value.length ? value.join(', ') : '';
  }
  if (key === 'evidence') {
    return value && Object.keys(value).length ? JSON.stringify(value) : '';
  }
  if (key === 'relevanceScore') {
    return value == null ? '' : Number(value).toFixed(4);
  }
  if (key === 'firstSeenAt' || key === 'lastSeenAt') {
    return value ? new Date(value).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '';
  }
  return String(value || '');
}

function normalizeScottSourceStatus(status) {
  const normalized = String(status || 'candidate').trim().toLowerCase().replaceAll('-', '_').replaceAll(' ', '_');
  if (normalized === 'ignore') {
    return 'ignored';
  }
  return SCOTT_SOURCE_STATUS_OPTIONS.some(([value]) => value === normalized) ? normalized : 'candidate';
}

function HannahCurationPanel({ state, sort, filters, onSort, onFilterChange, onScroll }) {
  return (
    <section className="glass-panel pane scott-sources-panel">
      <div className="scott-sources-toolbar">
        <div>
          <p className="eyebrow">Hannah results</p>
          <h3>Photo candidates</h3>
        </div>
        <div className="scott-sources-summary">
          <span>{state.loading ? 'Loading...' : `${state.items.length} of ${state.total} shown`}</span>
        </div>
      </div>

      {state.error ? <p className="error-text">{state.error}</p> : null}

      <div className="scott-sources-grid-shell hannah-candidates-grid-shell" onScroll={onScroll}>
        <table className="scott-sources-grid hannah-candidates-grid">
          <thead>
            <tr>
              {HANNAH_CANDIDATE_COLUMNS.map(([key, label]) => (
                <th key={key} scope="col">
                  <button type="button" className="source-column-sort" onClick={() => onSort(key)}>
                    <span>{label}</span>
                    <span className="source-sort-indicator">{sort.key === key ? (sort.direction === 'asc' ? 'Up' : 'Down') : ''}</span>
                  </button>
                  <input
                    type="search"
                    value={filters[key] || ''}
                    onChange={(event) => onFilterChange(key, event.target.value)}
                    aria-label={`Filter ${label}`}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {state.items.map((candidate) => (
              <tr key={candidate.id}>
                {HANNAH_CANDIDATE_COLUMNS.map(([key]) => (
                  <td key={key} className={`source-cell source-cell-${key}`}>
                    {formatHannahCandidateValue(candidate, key)}
                  </td>
                ))}
              </tr>
            ))}
            {!state.loading && !state.items.length ? (
              <tr>
                <td colSpan={HANNAH_CANDIDATE_COLUMNS.length} className="source-empty-cell">
                  No curation records match the current filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {state.loadingMore ? <p className="source-loading-row">Loading more candidates...</p> : null}
      </div>
    </section>
  );
}

function formatHannahCandidateValue(candidate, key) {
  const value = candidate?.[key];
  if (key === 'relevanceScore') {
    return value == null ? '' : Number(value).toFixed(4);
  }
  if (key === 'lastSeenAt') {
    return value ? new Date(value).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '';
  }
  if (key === 'sourceUrl' || key === 'imageUrl') {
    return value ? (
      <a href={value} target="_blank" rel="noreferrer" onClick={(event) => event.stopPropagation()}>
        {value}
      </a>
    ) : '';
  }
  return String(value || '');
}

function WhitneyDemandSignalsPanel({ state, sort, filters, onSort, onFilterChange, onScroll }) {
  return (
    <section className="glass-panel pane scott-sources-panel">
      <div className="scott-sources-toolbar">
        <div>
          <p className="eyebrow">Whitney signals</p>
          <h3>Demand signals</h3>
        </div>
        <div className="scott-sources-summary">
          <span>{state.loading ? 'Loading...' : `${state.items.length} of ${state.total} shown`}</span>
        </div>
      </div>

      {state.error ? <p className="error-text">{state.error}</p> : null}

      <div className="scott-sources-grid-shell whitney-signals-grid-shell" onScroll={onScroll}>
        <table className="scott-sources-grid whitney-signals-grid">
          <thead>
            <tr>
              {WHITNEY_SIGNAL_COLUMNS.map(([key, label]) => (
                <th key={key} scope="col">
                  <button type="button" className="source-column-sort" onClick={() => onSort(key)}>
                    <span>{label}</span>
                    <span className="source-sort-indicator">{sort.key === key ? (sort.direction === 'asc' ? 'Up' : 'Down') : ''}</span>
                  </button>
                  <input
                    type="search"
                    value={filters[key] || ''}
                    onChange={(event) => onFilterChange(key, event.target.value)}
                    aria-label={`Filter ${label}`}
                  />
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {state.items.map((signal) => (
              <tr key={signal.id}>
                {WHITNEY_SIGNAL_COLUMNS.map(([key]) => (
                  <td key={key} className={`source-cell source-cell-${key}`}>
                    {formatWhitneySignalValue(signal, key)}
                  </td>
                ))}
              </tr>
            ))}
            {!state.loading && !state.items.length ? (
              <tr>
                <td colSpan={WHITNEY_SIGNAL_COLUMNS.length} className="source-empty-cell">
                  No demand signals match the current filters.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {state.loadingMore ? <p className="source-loading-row">Loading more signals...</p> : null}
      </div>
    </section>
  );
}

function formatWhitneySignalValue(signal, key) {
  const value = signal?.[key];
  if (key === 'demandScore' || key === 'confidence') {
    return value == null ? '' : Number(value).toFixed(4);
  }
  if (key === 'lastSeenAt') {
    return value ? new Date(value).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' }) : '';
  }
  return String(value || '');
}

function SupportPage() {
  const [requestText, setRequestText] = useState('');
  const [submitted, setSubmitted] = useState(false);

  function handleSupportSubmit(event) {
    event.preventDefault();
    setSubmitted(true);
  }

  return (
    <section className="experience-shell">
      <div className="hero-panel glass-panel standards-hero page-title-row">
        <div>
          <p className="eyebrow">Support</p>
          <h2>Ask for help or request an improvement</h2>
          <p className="title-support">Requests can cover new symbols, corrections, usability issues, or service improvements.</p>
        </div>
      </div>
      <form className="glass-panel pane support-panel" onSubmit={handleSupportSubmit}>
        <SectionHeading title="Request" subtitle="Ed will manage this workflow in a future release" />
        <label className="field">
          <span>What do you need?</span>
          <textarea
            rows="7"
            value={requestText}
            onChange={(event) => {
              setRequestText(event.target.value);
              setSubmitted(false);
            }}
            placeholder="Describe the symbol, correction, question, or improvement you want Symgov to consider."
          />
        </label>
        <button type="submit" className="action-button primary" disabled={!requestText.trim()}>
          Submit request
        </button>
        {submitted ? <p className="success-text">Support request captured locally. Ed workflow integration will be added later.</p> : null}
      </form>
    </section>
  );
}

function SectionHeading({ title, subtitle }) {
  return (
    <div className="section-heading">
      <h3>{title}</h3>
      <p>{subtitle}</p>
    </div>
  );
}

function Metric({ title, value }) {
  return (
    <div className="metric-card">
      <span>{title}</span>
      <strong>{value}</strong>
    </div>
  );
}

function Fact({ label, value }) {
  return (
    <div className="fact-card">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function EmptyState({ title, body }) {
  return (
    <div className="empty-state">
      <h4>{title}</h4>
      <p>{body}</p>
    </div>
  );
}

function AmbientBackdrop() {
  return (
    <div className="ambient-backdrop" aria-hidden="true">
      <div className="orb orb-one" />
      <div className="orb orb-two" />
      <div className="orb orb-three" />
      <div className="grid-haze" />
    </div>
  );
}

function SymbolGlyph({ symbolId, large = false, variant = false }) {
  const className = `symbol-glyph ${large ? 'large' : ''} ${variant ? 'variant' : ''}`.trim();

  if (symbolId.startsWith('FV')) {
    return (
      <svg className={className} viewBox="0 0 220 120" role="img" aria-label={`${symbolId} glyph`}>
        <title>{symbolId} glyph</title>
        <desc>Stylized control valve symbol.</desc>
        <line x1="20" y1="60" x2="200" y2="60" />
        <polygon points="70,40 110,60 70,80" />
        <polygon points="150,40 110,60 150,80" />
        <circle cx="110" cy="26" r="14" />
        <line x1="110" y1="40" x2="110" y2="60" />
      </svg>
    );
  }

  if (symbolId.startsWith('PI') || symbolId.startsWith('TT')) {
    return (
      <svg className={className} viewBox="0 0 220 120" role="img" aria-label={`${symbolId} glyph`}>
        <title>{symbolId} glyph</title>
        <desc>Stylized instrument bubble symbol.</desc>
        <line x1="20" y1="60" x2="82" y2="60" />
        <line x1="138" y1="60" x2="200" y2="60" />
        <circle cx="110" cy="60" r="28" />
        <text x="110" y="68" textAnchor="middle">
          {symbolId.slice(0, 2)}
        </text>
      </svg>
    );
  }

  return (
    <svg className={className} viewBox="0 0 220 120" role="img" aria-label={`${symbolId} glyph`}>
      <title>{symbolId} glyph</title>
      <desc>Stylized manual valve symbol.</desc>
      <line x1="20" y1="60" x2="200" y2="60" />
      <polygon points="78,42 110,60 78,78" />
      <polygon points="142,42 110,60 142,78" />
    </svg>
  );
}

function navClass(isActive) {
  return `nav-pill ${isActive ? 'active' : ''}`.trim();
}

export default App;
