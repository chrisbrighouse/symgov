import { useEffect, useMemo, useState, useTransition } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate, useSearchParams } from 'react-router-dom';
import {
  fetchHealth,
  fetchPublishedSymbols,
  fetchWorkspaceDaisyReports,
  fetchWorkspaceQueueItems,
  fetchWorkspaceReviewCases,
  processWorkspaceSplitReviewDecisions,
  submitWorkspaceReviewDecision,
  submitExternalSubmission,
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
    subtitle: 'Validation',
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
  }
];

const WORKSPACE_REFRESH_INTERVAL_MS = 5000;
const DEFAULT_HIDDEN_WORKSPACE_STATUSES = new Set(['completed']);

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
        <NavLink to="/standards" className={({ isActive }) => navClass(isActive)}>
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

function CogIcon() {
  return (
    <svg viewBox="0 0 24 24" aria-hidden="true">
      <path d="M12 15.2a3.2 3.2 0 1 0 0-6.4 3.2 3.2 0 0 0 0 6.4Z" />
      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06a2.06 2.06 0 0 1-2.91 2.91l-.06-.06a1.7 1.7 0 0 0-1.88-.34 1.7 1.7 0 0 0-1.03 1.56V21a2.06 2.06 0 0 1-4.12 0v-.09a1.7 1.7 0 0 0-1.03-1.56 1.7 1.7 0 0 0-1.88.34l-.06.06a2.06 2.06 0 0 1-2.91-2.91l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.56-1.03H3a2.06 2.06 0 0 1 0-4.12h.09A1.7 1.7 0 0 0 4.6 8.8a1.7 1.7 0 0 0-.34-1.88l-.06-.06a2.06 2.06 0 0 1 2.91-2.91l.06.06a1.7 1.7 0 0 0 1.88.34 1.7 1.7 0 0 0 1.03-1.56V3a2.06 2.06 0 0 1 4.12 0v.09a1.7 1.7 0 0 0 1.03 1.56 1.7 1.7 0 0 0 1.88-.34l.06-.06a2.06 2.06 0 0 1 2.91 2.91l-.06.06a1.7 1.7 0 0 0-.34 1.88 1.7 1.7 0 0 0 1.56 1.03H21a2.06 2.06 0 0 1 0 4.12h-.09A1.7 1.7 0 0 0 19.4 15Z" />
    </svg>
  );
}

function StandardsPage() {
  const [query, setQuery] = useState('');
  const [sortState, setSortState] = useState({ key: 'id', direction: 'asc' });
  const [columnFilters, setColumnFilters] = useState({});
  const [facetFilters, setFacetFilters] = useState({});
  const [activeId, setActiveId] = useState('');
  const [displayCount, setDisplayCount] = useState(60);
  const [standardsState, setStandardsState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading live published records…' : 'No API root configured. Showing seeded published records.',
    items: appConfig.apiRoot ? [] : symbols
  });
  const standardsSymbols = standardsState.items.length ? standardsState.items : symbols;
  const standardsColumns = [
    ['id', 'ID'],
    ['name', 'Name'],
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
    ['status', 'Status'],
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
    setDisplayCount(60);
  }, [query, columnFilters, facetFilters, sortState]);

  const activeSymbol = filteredSymbols.find((symbol) => symbol.id === activeId) || filteredSymbols[0];

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

      <div className="standards-browser-grid">
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
          <div className="approved-symbol-grid" onScroll={handleGridScroll}>
            <table>
              <thead>
                <tr>
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
                  <tr key={symbol.id} className={symbol.id === activeId ? 'active' : ''} onClick={() => setActiveId(symbol.id)}>
                    <td><PublishedSymbolPreview symbol={symbol} /></td>
                    {standardsColumns.map(([key]) => (
                      <td key={`${symbol.id}-${key}`}>{getSymbolField(symbol, key) || 'Pending'}</td>
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
      </div>

      {activeId && activeSymbol ? (
        <div className="standards-detail-overlay" role="dialog" aria-modal="false" aria-label="Published symbol details">
          <section className="glass-panel pane standards-detail-drawer">
            <div className="detail-heading">
              <div>
                <p className="eyebrow">Approved symbol</p>
                <h3>
                  {displaySymbolId(activeSymbol)} · {displaySymbolName(activeSymbol)}
                </h3>
                <p>{activeSymbol.summary}</p>
              </div>
              <button type="button" className="action-button secondary compact" onClick={() => setActiveId('')}>
                Close
              </button>
            </div>
            <div className="symbol-stage">
              <PublishedSymbolPreview symbol={activeSymbol} large />
            </div>
            <div className="fact-grid detail-list">
              <Fact label="Status" value={activeSymbol.status || 'Published'} />
              <Fact label="Revision" value={activeSymbol.revision} />
              <Fact label="Effective" value={activeSymbol.effectiveDate} />
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
  if (key === 'symbolFamily') {
    return symbol.symbolFamily || symbol.family || symbol.category || '';
  }
  const value = symbol[key];
  if (Array.isArray(value)) {
    return value.join(', ');
  }
  return String(value || '');
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
  const [lastWorkspaceRefresh, setLastWorkspaceRefresh] = useState(null);

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

      Promise.all([fetchWorkspaceQueueItems(), fetchWorkspaceReviewCases(), fetchWorkspaceDaisyReports()]).then(([queueResult, reviewResult, daisyResult]) => {
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
      return `${refreshLabel} · Auto-refresh 5s · ${queueState.message} · ${reviewState.message}`;
    }

    return `${refreshLabel} · ${queueState.message} · ${reviewState.message}`;
  }, [
    lastWorkspaceRefresh,
    queueState.loading,
    queueState.message,
    queueState.mode,
    reviewState.loading,
    reviewState.message,
    reviewState.mode
  ]);

  const monitorItems = useMemo(
    () => buildWorkspaceMonitorItems(queueState.items, queueState.mode, reviewState.items, daisyState.items),
    [queueState.items, queueState.mode, reviewState.items, daisyState.items]
  );
  const statusOptionsByColumn = useMemo(() => buildWorkspaceStatusOptions(monitorItems), [monitorItems]);
  const filteredColumns = useMemo(
    () => filterWorkspaceMonitorColumns(monitorItems, query, columnStatusFilters),
    [monitorItems, query, columnStatusFilters]
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
  return (
    <section className="experience-shell queue-monitor-shell">
      <div className="workspace-titlebar glass-panel">
        <div>
          <p className="eyebrow">ADMIN WORKSPACE</p>
          <h2>Activity Monitors</h2>
        </div>
        <div className="workspace-titlebar-tools">
          <label className="field monitor-search-field" aria-label="Search workspace activity">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search for a Batch, Status, Case"
            />
          </label>
        </div>
      </div>

      <div className={`workspace-monitor-status-row health-chip health-${reviewState.mode}`} aria-live="polite">
        <span>{refreshSummary}</span>
      </div>

      <div className="queue-monitor-board">
        {filteredColumns.map((column) => (
          <WorkspaceQueueColumn
            key={column.id}
            column={column}
            statusOptions={statusOptionsByColumn[column.id] || []}
            statusFilter={columnStatusFilters[column.id] || {}}
            onStatusToggle={handleColumnStatusToggle}
            onReviewOpen={openReviewFromWorkspace}
          />
        ))}
      </div>
    </section>
  );
}

function buildWorkspaceMonitorItems(queueItems, queueMode, reviewItems, daisyItems) {
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

      column.items.push({
        id: queueItem.id,
        label: resolveQueueItemLabel(queueItem),
        title: compactTitle(queueItem.displayName || resolveQueueItemDisplayTitle(queueItem)),
        meta: queueItem.payload?.stage || queueItem.sourceType || queueItem.queueFamily,
        status: queueItem.status,
        priority: queueItem.priority,
        searchText: [
          queueItem.id,
          queueItem.agentId,
          queueItem.agentName,
          queueItem.queueFamily,
          queueItem.sourceType,
          queueItem.sourceId,
          queueItem.status,
          queueItem.priority,
          queueItem.escalationReason,
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

  return columns;
}

function resolveQueueItemTitle(queueItem) {
  const payload = queueItem.payload || {};

  return (
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
      const matchesQuery = !normalizedQuery || item.searchText.toLowerCase().includes(normalizedQuery);
      const matchesStatus = isWorkspaceStatusVisible(getWorkspaceStatusKey(item.status), columnStatusFilters[column.id], column.id);

      return matchesQuery && matchesStatus;
    })
  }));
}

function getWorkspaceStatusKey(status) {
  return String(status || 'pending').trim().toLowerCase().replaceAll('_', ' ');
}

function formatWorkspaceStatusLabel(status) {
  return getWorkspaceStatusKey(status).replaceAll(' ', '_').toUpperCase();
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

function WorkspaceQueueColumn({ column, statusOptions, statusFilter, onStatusToggle, onReviewOpen }) {
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
              onOpen={column.id === 'human_review' ? onReviewOpen : null}
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
  const isClickable = typeof onOpen === 'function' && Boolean(item.reviewCaseId);
  const CardElement = isClickable ? 'button' : 'article';

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
      <b className="monitor-card-status">{String(item.status || 'pending').replaceAll('_', ' ')}</b>
    </CardElement>
  );
}

function resolveWorkspaceAssetUrl(assetUrl) {
  if (!assetUrl) {
    return null;
  }
  return new URL(assetUrl, appConfig.apiRoot || window.location.origin).toString();
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

function ReviewSourceVisual({ activeChange, activeChildren, reviewedChildCount, onSaveProperties, workspaceMode }) {
  const primaryChild = activeChildren[0];
  const resolvedPreviewUrl = resolveWorkspaceAssetUrl(activeChange?.sourcePreviewUrl || primaryChild?.previewUrl);
  const [imageUnavailable, setImageUnavailable] = useState(!resolvedPreviewUrl);
  const [propertyDraft, setPropertyDraft] = useState({
    name: '',
    description: '',
    category: '',
    discipline: ''
  });
  const [propertyState, setPropertyState] = useState({ pending: false, message: '', error: '' });
  const itemName = displaySymbolId(activeChange) || 'Review item';
  const originalFilename = displayReviewOriginalFilename(activeChange) || 'Not recorded';
  const symbolProperties = activeChange?.symbolProperties || {};
  const propertyNamePattern = '^[A-Za-z0-9 \\\\-/$]*$';

  useEffect(() => {
    setImageUnavailable(!resolvedPreviewUrl);
  }, [resolvedPreviewUrl]);

  useEffect(() => {
    setPropertyDraft({
      name: symbolProperties.name || activeChange?.title || itemName,
      description: symbolProperties.description || activeChange?.summary || '',
      category: symbolProperties.category || activeChange?.processCategory || activeChange?.symbolFamily || '',
      discipline: symbolProperties.discipline || activeChange?.engineeringDiscipline || ''
    });
    setPropertyState({ pending: false, message: '', error: '' });
  }, [
    activeChange?.id,
    activeChange?.title,
    activeChange?.summary,
    activeChange?.processCategory,
    activeChange?.symbolFamily,
    activeChange?.engineeringDiscipline,
    itemName,
    symbolProperties.name,
    symbolProperties.description,
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
        <label className="field">
          <span>Category</span>
          <input
            type="text"
            maxLength="80"
            value={propertyDraft.category}
            onChange={(event) => updatePropertyDraft('category', event.target.value)}
          />
        </label>
        <label className="field">
          <span>Discipline</span>
          <input
            type="text"
            maxLength="80"
            value={propertyDraft.discipline}
            onChange={(event) => updatePropertyDraft('discipline', event.target.value)}
          />
        </label>
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
      {activeChildren.length ? (
        <div className="review-thumbnail-strip" aria-label="Extracted child symbol previews">
          {activeChildren.slice(0, 8).map((child, index) => (
            <div key={child.id} className="review-thumbnail-card">
              <SplitSymbolPreview child={child} variant={index % 2 === 1} />
              <span>{displaySymbolId(child)}</span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
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
    await refreshReviewData();
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
                    {item.splitChildStatus === 'returned_for_review' ? 'Returned' : 'New'}
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
                    {activeSingleChildReview?.action === 'request_changes' ? (
                      <label className="field request-field">
                        <span>Requested changes</span>
                        <textarea
                          rows="4"
                          value={activeSingleChildReview.requestDetails}
                          onChange={(event) => updateChildReview(activeSingleChild.id, { requestDetails: event.target.value })}
                          placeholder="Describe the changes needed before this symbol can be approved."
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
    ? new URL(child.previewUrl, appConfig.apiRoot || window.location.origin).toString()
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
