import { useEffect, useMemo, useState, useTransition } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import {
  fetchHealth,
  fetchPublishedSymbols,
  fetchWorkspaceDaisyReports,
  fetchWorkspaceQueueItems,
  fetchWorkspaceReviewCases,
  processWorkspaceSplitReviewDecisions,
  submitWorkspaceReviewDecision,
  submitExternalSubmission
} from './api.js';
import { appConfig } from './config.js';
import { changeQueue, daisyCoordinationReports, processingActivity, submissionPresets, symbols } from './data.js';

const REVIEW_DECISION_OPTIONS = [
  ['child_actions_submitted', 'Record Child Actions'],
  ['approve', 'Approve'],
  ['reject', 'Reject'],
  ['request_changes', 'Request Changes'],
  ['more_evidence', 'Request More Evidence'],
  ['rename_classify', 'Rename/Classify'],
  ['duplicate', 'Mark Duplicate'],
  ['deleted', 'Delete Proposed Child'],
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
  const [standardsState, setStandardsState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading live published records…' : 'No API root configured. Showing seeded published records.',
    items: appConfig.apiRoot ? [] : symbols
  });
  const [activeId, setActiveId] = useState(symbols[0]?.id || '');
  const standardsSymbols = standardsState.items.length ? standardsState.items : symbols;

  const filteredSymbols = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    if (!normalizedQuery) {
      return standardsSymbols;
    }

    return standardsSymbols.filter((symbol) =>
      [
        symbol.id,
        symbol.name,
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
      )
    );
  }, [standardsSymbols, query]);

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
      setActiveId(filteredSymbols[0]?.id || '');
    }
  }, [filteredSymbols, activeId]);

  const activeSymbol = filteredSymbols.find((symbol) => symbol.id === activeId) || filteredSymbols[0];

  return (
    <section className="experience-shell">
      <div className="hero-panel glass-panel standards-hero">
        <div>
          <p className="eyebrow">Published-only Standards View</p>
          <h2>Browse approved symbols, confirm current guidance, and keep clarifications bound to the active page.</h2>
        </div>
        <label className="field search-field">
          <span>Search published symbols</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search by symbol, pack, page, or guidance topic"
          />
        </label>
      </div>

      <div className="three-pane-grid">
        <section className="glass-panel pane">
          <SectionHeading
            title="Approved Browse"
            subtitle={`${filteredSymbols.length} published records`}
          />
          <div className="stack-list">
            {filteredSymbols.map((symbol) => (
              <button
                key={symbol.id}
                type="button"
                className={`symbol-card ${symbol.id === activeId ? 'active' : ''}`}
                onClick={() => setActiveId(symbol.id)}
              >
                <PublishedSymbolPreview symbol={symbol} />
                <div>
                  <strong>
                    {symbol.id} · {symbol.name}
                  </strong>
                  <p>{symbol.pack}</p>
                  <small>
                    {symbol.revision} · {symbol.pageCode}
                  </small>
                </div>
              </button>
            ))}
          </div>
        </section>

        <section className="glass-panel pane detail-pane">
          <SectionHeading title="Published Detail" subtitle="Latest approved revision only" />
          {activeSymbol ? (
            <>
              <div className="detail-heading">
                <div>
                  <h3>
                    {activeSymbol.id} · {activeSymbol.name}
                  </h3>
                  <p>{activeSymbol.summary}</p>
                </div>
                <span className="status-pill">{activeSymbol.status}</span>
              </div>
              <div className="symbol-stage">
                <PublishedSymbolPreview symbol={activeSymbol} large />
              </div>
              <div className="fact-grid">
                <Fact label="Revision" value={activeSymbol.revision} />
                <Fact label="Effective" value={activeSymbol.effectiveDate} />
                <Fact label="Published Page" value={activeSymbol.pageCode} />
                <Fact label="Pack" value={activeSymbol.pack} />
              </div>
              <div className="copy-block">
                <h4>Governance rationale</h4>
                <p>{activeSymbol.rationale}</p>
              </div>
              <div className="tag-row">
                {activeSymbol.downloads.map((download) => (
                  <span key={download} className="tag-chip">
                    {download}
                  </span>
                ))}
              </div>
            </>
          ) : (
            <EmptyState title="No published records" body="Adjust the search or seed more published symbols." />
          )}
        </section>

        <section className="glass-panel pane">
          <SectionHeading title="Clarification Context" subtitle="Bound to the selected symbol" />
          {activeSymbol ? (
            <>
              <div className="context-card">
                <p className="context-label">Active reference</p>
                <strong>
                  {activeSymbol.id} / {activeSymbol.pageCode}
                </strong>
                <p>{activeSymbol.metric || activeSymbol.packCode || activeSymbol.discipline}</p>
              </div>
              <div className="metric-column">
                <Metric title="Open clarifications" value={String(activeSymbol.clarificationCount || 0)} />
                <Metric title="Publish state" value="Latest approved" />
              </div>
              <div className="copy-block">
                <h4>Suggested next actions</h4>
                <p>Route clarification into Workspace review with the page and symbol context pre-attached.</p>
              </div>
              <NavLink to="/standards/submit" className="action-button primary">
                Open submission and clarification intake
              </NavLink>
            </>
          ) : (
            <EmptyState title="No active symbol" body="Pick a published symbol to anchor clarifications." />
          )}
        </section>
      </div>
    </section>
  );
}

function WorkspacePage() {
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
        [statusKey]: !isWorkspaceStatusVisible(statusKey, current[columnId])
      };

      return {
        ...current,
        [columnId]: nextColumn
      };
    });
  };
  const totalVisibleItems = filteredColumns.reduce((total, column) => total + column.items.length, 0);
  const activeCount = filteredColumns.reduce(
    (total, column) =>
      total +
      column.items.filter((item) =>
        ['queued', 'running', 'waiting', 'escalated', 'review', 'in review', 'request_changes'].includes(
          String(item.status || '').toLowerCase()
        )
      ).length,
    0
  );

  return (
    <section className="experience-shell queue-monitor-shell">
      <div className="workspace-titlebar glass-panel">
        <div>
          <p className="eyebrow">ADMIN WORKSPACE</p>
          <h2>Activity Monitors</h2>
        </div>
        <div className="workspace-titlebar-tools">
          <label className="field monitor-search-field">
            <span>Search activity</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Batch, file, agent, status, case"
            />
          </label>
          <div className={`health-chip health-${reviewState.mode}`} aria-live="polite">
            <span>{refreshSummary}</span>
          </div>
        </div>
      </div>

      <div className="monitor-summary-row" aria-label="Workspace monitor summary">
        <Metric title="Visible items" value={String(totalVisibleItems)} />
        <Metric title="Needs attention" value={String(activeCount)} />
        <Metric title="Review cases" value={String(reviewState.items.length)} />
        <Metric title="Daisy reports" value={String(daisyState.items.length)} />
      </div>

      <div className="queue-monitor-board">
        {filteredColumns.map((column) => (
          <WorkspaceQueueColumn
            key={column.id}
            column={column}
            statusOptions={statusOptionsByColumn[column.id] || []}
            statusFilter={columnStatusFilters[column.id] || {}}
            onStatusToggle={handleColumnStatusToggle}
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
        title: compactTitle(resolveQueueItemDisplayTitle(queueItem)),
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
          label: isExternalSubmissionId(activity.batchId)
            ? formatWorkspaceDisplayDate(activity.batchId, { createdAt: activity.submittedAt })
            : activity.batchId || activity.id,
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
      label: formatWorkspaceDisplayDate(review.openedAt || review.createdAt, { id: reviewIdentifier }),
      title: compactTitle(review.title || review.summary || reviewIdentifier),
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
      label: report.reviewCaseId || report.queueItemId || report.id,
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

  if (usesCreatedAtQueueLabel(queueItem)) {
    return formatWorkspaceDisplayDate(null, queueItem);
  }

  return submissionBatchId || queueItem.sourceId || queueItem.id;
}

function resolveQueueItemDisplayTitle(queueItem) {
  const title = resolveQueueItemTitle(queueItem);

  if (isExternalSubmissionId(title)) {
    return formatWorkspaceDisplayDate(title, queueItem);
  }

  return title;
}

function usesCreatedAtQueueLabel(queueItem) {
  return (
    queueItem?.agentId === 'libby' ||
    queueItem?.queueFamily === 'classification' ||
    queueItem?.agentId === 'daisy' ||
    queueItem?.queueFamily === 'review' ||
    queueItem?.agentId === 'rupert' ||
    queueItem?.queueFamily === 'publication'
  );
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
  const date = extractWorkspaceDisplayDate(value) || extractWorkspaceDisplayDate(queueItem?.createdAt);

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
          const firstHidden = DEFAULT_HIDDEN_WORKSPACE_STATUSES.has(first.key);
          const secondHidden = DEFAULT_HIDDEN_WORKSPACE_STATUSES.has(second.key);

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
      const matchesStatus = isWorkspaceStatusVisible(getWorkspaceStatusKey(item.status), columnStatusFilters[column.id]);

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

function isWorkspaceStatusVisible(statusKey, columnFilter = {}) {
  if (Object.prototype.hasOwnProperty.call(columnFilter, statusKey)) {
    return Boolean(columnFilter[statusKey]);
  }

  return !DEFAULT_HIDDEN_WORKSPACE_STATUSES.has(statusKey);
}

function compactTitle(value) {
  const text = String(value || 'Untitled').trim();

  if (text.length <= 42) {
    return text;
  }

  return `${text.slice(0, 39)}...`;
}

function WorkspaceQueueColumn({ column, statusOptions, statusFilter, onStatusToggle }) {
  const attentionCount = column.items.filter((item) =>
    ['queued', 'running', 'waiting', 'escalated', 'review', 'request_changes'].includes(String(item.status).toLowerCase())
  ).length;

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
                      checked={isWorkspaceStatusVisible(option.key, statusFilter)}
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
          column.items.map((item) => <WorkspaceMonitorCard key={item.id} item={item} />)
        ) : (
          <div className="monitor-empty">Clear</div>
        )}
      </div>
      <footer className="monitor-column-footer">
        <span>{attentionCount} active</span>
      </footer>
    </section>
  );
}

function WorkspaceMonitorCard({ item }) {
  const priority = String(item.priority || 'Normal').toLowerCase();
  const status = String(item.status || 'pending').toLowerCase().replaceAll('_', '-');

  return (
    <article className={`monitor-card monitor-status-${status}`} title={item.detail || item.title}>
      <div className="monitor-card-line">
        <strong>{item.label}</strong>
        <span className={`monitor-dot priority-${priority}`} aria-label={`${item.priority || 'Normal'} priority`} />
      </div>
      <p>{item.title}</p>
      <div className="monitor-card-meta">
        <span>{item.meta}</span>
        <b>{String(item.status || 'pending').replaceAll('_', ' ')}</b>
      </div>
    </article>
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

function ReviewSourceVisual({ activeChange, activeChildren }) {
  const primaryChild = activeChildren[0];
  const resolvedPreviewUrl = resolveWorkspaceAssetUrl(activeChange?.sourcePreviewUrl || primaryChild?.previewUrl);
  const [imageUnavailable, setImageUnavailable] = useState(!resolvedPreviewUrl);

  useEffect(() => {
    setImageUnavailable(!resolvedPreviewUrl);
  }, [resolvedPreviewUrl]);

  return (
    <section className="review-visual-panel">
      <div className="review-visual-header">
        <div>
          <p className="context-label">Visual evidence</p>
          <h3>{activeChange?.sourceFileName || activeChange?.symbolId || 'Review item'}</h3>
        </div>
        <span className="status-pill">{activeChildren.length ? `${activeChildren.length} proposed children` : activeChange?.status}</span>
      </div>
      <div className="review-visual-frame">
        {!imageUnavailable && resolvedPreviewUrl ? (
          <img
            className="review-source-image"
            src={resolvedPreviewUrl}
            alt={`Visual preview for ${activeChange?.sourceFileName || activeChange?.symbolId || 'review item'}`}
            onError={() => setImageUnavailable(true)}
          />
        ) : (
          <div className="review-source-fallback">
            <SymbolGlyph symbolId={activeChange?.symbolId || 'SYMBOL'} large />
            <small>Preview unavailable</small>
          </div>
        )}
      </div>
      {activeChildren.length ? (
        <div className="review-thumbnail-strip" aria-label="Extracted child symbol previews">
          {activeChildren.slice(0, 8).map((child, index) => (
            <div key={child.id} className="review-thumbnail-card">
              <SplitSymbolPreview child={child} variant={index % 2 === 1} />
              <span>{child.proposedSymbolId}</span>
            </div>
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ReviewsPage() {
  const [query, setQuery] = useState('');
  const [stageFilter, setStageFilter] = useState('all');
  const [reviewerFilter, setReviewerFilter] = useState('all');
  const [priorityFilter, setPriorityFilter] = useState('all');
  const [actionFilter, setActionFilter] = useState('all');
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

  const reviewStages = useMemo(
    () => ['all', ...Array.from(new Set(reviewQueue.map((item) => item.currentStage || item.status).filter(Boolean))).sort()],
    [reviewQueue]
  );
  const reviewers = useMemo(
    () => ['all', ...Array.from(new Set(reviewQueue.map((item) => item.owner).filter(Boolean))).sort()],
    [reviewQueue]
  );
  const priorities = useMemo(
    () => ['all', ...Array.from(new Set(reviewQueue.map((item) => item.priority).filter(Boolean))).sort()],
    [reviewQueue]
  );

  const filteredQueue = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    return reviewQueue.filter((item) => {
      if (stageFilter !== 'all' && (item.currentStage || item.status) !== stageFilter) {
        return false;
      }
      if (reviewerFilter !== 'all' && item.owner !== reviewerFilter) {
        return false;
      }
      if (priorityFilter !== 'all' && item.priority !== priorityFilter) {
        return false;
      }
      if (actionFilter !== 'all') {
        const latestCode = item.latestDecision?.decisionCode || 'none';
        const reviewCaseId = item.parentReviewCaseId || item.id;
        const hasPendingDaisy = daisyState.items.some((report) => report.reviewCaseId === reviewCaseId);
        if (actionFilter === 'needs_decision' && latestCode !== 'none') {
          return false;
        }
        if (actionFilter === 'daisy_coordinated' && !hasPendingDaisy) {
          return false;
        }
        if (!['needs_decision', 'daisy_coordinated'].includes(actionFilter) && latestCode !== actionFilter) {
          return false;
        }
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
  }, [query, reviewQueue, stageFilter, reviewerFilter, priorityFilter, actionFilter, daisyState.items]);

  useEffect(() => {
    if (!filteredQueue.some((item) => item.id === activeId)) {
      setActiveId(filteredQueue[0]?.id || '');
    }
  }, [filteredQueue, activeId]);

  const activeChange = filteredQueue.find((item) => item.id === activeId) || filteredQueue[0];
  const activeChildren = activeChange?.children || [];
  const activeReviewCaseId = activeChange?.parentReviewCaseId || activeChange?.id;
  const activeIndex = activeChange ? filteredQueue.findIndex((item) => item.id === activeChange.id) : -1;
  const reviewedChildCount = activeChildren.filter((child) => getChildReview(child.id).action !== 'pending').length;
  const isSplitReview = (activeChange?.reviewItemType === 'split_item' || activeChange?.currentStage === 'raster_split_review') && activeChildren.length > 0;
  const pendingChildCount = Math.max(activeChildren.length - reviewedChildCount, 0);
  const splitDecisionCounts = activeChildren.reduce((counts, child) => {
    const action = getChildReview(child.id).action;
    if (action && action !== 'pending') {
      counts[action] = (counts[action] || 0) + 1;
    }
    return counts;
  }, {});
  const sourceComment = sourceComments[activeChange?.id] || '';
  const classificationAliases = activeChange?.aliases || [];
  const classificationKeywords = activeChange?.keywords || [];
  const classificationSourceRefs = activeChange?.sourceRefs || [];
  const activeDaisyReports = useMemo(
    () => daisyState.items.filter((item) => item.reviewCaseId === activeReviewCaseId),
    [activeReviewCaseId, daisyState.items]
  );

  function updateSourceComment(changeId, value) {
    setSourceComments((current) => ({ ...current, [changeId]: value }));
  }

  function updateChildReview(childId, updates) {
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
        decisionCode: current[changeId]?.decisionCode || 'child_actions_submitted',
        deciderName: current[changeId]?.deciderName || 'SME reviewer',
        deciderRole: current[changeId]?.deciderRole || 'sme_reviewer',
        decisionNote: current[changeId]?.decisionNote || '',
        ...updates
      }
    }));
  }

  function getCaseReview(changeId) {
    return (
      caseReviewState[changeId] || {
        decisionCode: 'child_actions_submitted',
        deciderName: 'SME reviewer',
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
    setActiveId(filteredQueue[nextIndex].id);
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
          <h2>Daisy-coordinated Reviews</h2>
        </div>
        <div className="action-stack">
          <label className="field search-field">
            <span>Search reviews</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search change id, symbol, owner, summary, or source file"
            />
          </label>
          <div className={`health-chip health-${workspaceState.mode}`}>
            <span>{workspaceState.loading ? 'Loading Reviews…' : workspaceState.message}</span>
            <small>{workspaceState.mode === 'live' ? 'Live review cases' : 'Seeded fallback reviews'}</small>
          </div>
        </div>
      </div>

      <div className="review-filter-grid glass-panel">
        <label className="field">
          <span>Stage</span>
          <select value={stageFilter} onChange={(event) => setStageFilter(event.target.value)}>
            {reviewStages.map((value) => (
              <option key={value} value={value}>
                {value === 'all' ? 'All stages' : value.replaceAll('_', ' ')}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Reviewer</span>
          <select value={reviewerFilter} onChange={(event) => setReviewerFilter(event.target.value)}>
            {reviewers.map((value) => (
              <option key={value} value={value}>
                {value === 'all' ? 'All reviewers' : value}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Priority</span>
          <select value={priorityFilter} onChange={(event) => setPriorityFilter(event.target.value)}>
            {priorities.map((value) => (
              <option key={value} value={value}>
                {value === 'all' ? 'All priorities' : value}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Action</span>
          <select value={actionFilter} onChange={(event) => setActionFilter(event.target.value)}>
            <option value="all">All actions</option>
            <option value="needs_decision">Needs decision</option>
            <option value="daisy_coordinated">Daisy coordinated</option>
            {REVIEW_DECISION_OPTIONS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="review-workbench-grid">
        <section className="glass-panel pane review-queue-pane">
          <div className="review-pane-heading">
            <SectionHeading title="Review Queue" subtitle={`${filteredQueue.length} Daisy-visible records`} />
            <div className="review-navigation">
              <button type="button" className="action-button secondary compact" disabled={activeIndex <= 0} onClick={() => selectAdjacentReview(-1)}>
                Previous
              </button>
              <button
                type="button"
                className="action-button secondary compact"
                disabled={activeIndex < 0 || activeIndex >= filteredQueue.length - 1}
                onClick={() => selectAdjacentReview(1)}
              >
                Next
              </button>
            </div>
          </div>
          <div className="stack-list">
            {filteredQueue.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`queue-card review-list-card ${item.id === activeId ? 'active' : ''}`}
                onClick={() => setActiveId(item.id)}
              >
                <div className="queue-card-topline">
                  <strong>{item.symbolId}</strong>
                  <span className={`priority-chip priority-${item.priority.toLowerCase()}`}>{item.priority}</span>
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
                  <h3>{activeChange.title}</h3>
                  <p>{activeChange.summary}</p>
                </div>
                <span className="status-pill">{activeChange.status}</span>
              </div>
              <ReviewSourceVisual activeChange={activeChange} activeChildren={activeChildren} />
              <div className="fact-grid">
                <Fact label="Parent file" value={activeChange.sourceFileName || 'Not recorded'} />
                <Fact label="Intake record" value={activeChange.intakeRecordId || 'Pending'} />
                <Fact label="Review item" value={activeChange.reviewItemType === 'split_item' ? 'Split child' : 'Review case'} />
                <Fact label="Open review by" value={activeChange.due} />
                <Fact label="Child decisions" value={`${reviewedChildCount} / ${activeChildren.length || activeChange.childCount || 0}`} />
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
              {activeChildren.length ? (
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
              ) : (
                <EmptyState title="No extracted child symbols" body="This queue item does not yet include split children for review." />
              )}
            </>
          ) : (
            <EmptyState title="No review cases" body="There are no Daisy-coordinated reviews to show." />
          )}
        </section>

        <section className="glass-panel pane review-decision-pane">
          <SectionHeading
            title={isSplitReview ? 'Process Symbols' : 'Record Decision'}
            subtitle={isSplitReview ? 'Move selected child symbols to the next queue' : 'Case action, reviewer notes, and Daisy context'}
          />
          {activeChange ? (
            <>
              <div className="review-summary-strip">
                <Metric title="Risk" value={activeChange.risk} />
                <Metric title="Priority" value={activeChange.priority} />
                <Metric title="Status" value={activeChange.status} />
              </div>
              <div className={`copy-block decision-block ${isSplitReview ? 'split-processing-block' : ''}`}>
                <h4>{isSplitReview ? 'Selected symbol criteria' : 'Case decision'}</h4>
                {activeChange.latestDecision && !isSplitReview ? (
                  <div className="context-card">
                    <p className="context-label">Latest recorded decision</p>
                    <strong>{activeChange.latestDecision.decisionCode.replaceAll('_', ' ')}</strong>
                    <p>
                      {activeChange.latestDecision.deciderName} · {activeChange.latestDecision.createdAt}
                    </p>
                  </div>
                ) : null}
                {isSplitReview ? (
                  <>
                    <div className="split-process-summary-grid">
                      <Metric title="Ready" value={reviewedChildCount} />
                      <Metric title="Waiting" value={pendingChildCount} />
                      <Metric title="Total" value={activeChildren.length} />
                    </div>
                    {reviewedChildCount ? (
                      <div className="split-process-chip-list" aria-label="Selected split decision counts">
                        {Object.entries(splitDecisionCounts).map(([action, count]) => (
                          <span key={action} className={`split-process-chip review-${action}`}>
                            {action.replaceAll('_', ' ')} · {count}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="daisy-empty-text">Choose review criteria on one or more child symbols, then process them together.</p>
                    )}
                  </>
                ) : (
                  <div className="review-decision-actions" role="group" aria-label="Case decision options">
                    {REVIEW_DECISION_OPTIONS.map(([value, label]) => (
                      <button
                        key={value}
                        type="button"
                        className={`action-button case-decision-button case-decision-${value} ${getCaseReview(activeChange.id).decisionCode === value ? 'selected' : ''}`}
                        onClick={() => updateCaseReview(activeChange.id, { decisionCode: value })}
                      >
                        {label}
                      </button>
                    ))}
                  </div>
                )}
                {!isSplitReview ? (
                  <>
                    <label className="field">
                      <span>Reviewer name</span>
                      <input
                        value={getCaseReview(activeChange.id).deciderName}
                        onChange={(event) => updateCaseReview(activeChange.id, { deciderName: event.target.value })}
                        placeholder="SME reviewer"
                      />
                    </label>
                    <label className="field">
                      <span>Case review comment</span>
                      <textarea
                        rows="4"
                        value={sourceComment}
                        onChange={(event) => updateSourceComment(activeChange.id, event.target.value)}
                        placeholder="Capture context that applies to the whole source file."
                      />
                    </label>
                    <label className="field">
                      <span>Decision note</span>
                      <textarea
                        rows="4"
                        value={getCaseReview(activeChange.id).decisionNote}
                        onChange={(event) => updateCaseReview(activeChange.id, { decisionNote: event.target.value })}
                        placeholder="Summarize the SME decision and any follow-up instructions."
                      />
                    </label>
                  </>
                ) : null}
                <button
                  type="button"
                  className={`action-button record-review-button ${submitState.pending ? 'recording' : ''} ${submitState.message ? 'recorded' : ''} ${submitState.error ? 'failed' : ''}`}
                  disabled={submitState.pending || workspaceState.mode !== 'live' || (isSplitReview && reviewedChildCount === 0)}
                  onClick={isSplitReview ? processSplitReviewDecisions : submitReviewDecision}
                >
                  {submitState.pending
                    ? isSplitReview ? 'Processing symbols...' : 'Recording decision...'
                    : submitState.message
                      ? isSplitReview ? 'Symbols Processed' : 'Decision Recorded'
                      : isSplitReview ? 'Process Selected Symbols' : 'Record Review Decision'}
                </button>
                {submitState.message ? <p className="success-text">{submitState.message}</p> : null}
                {submitState.error ? <p className="error-text">{submitState.error}</p> : null}
                {workspaceState.mode !== 'live' ? (
                  <p className="daisy-empty-text">Decision persistence requires the live Symgov API.</p>
                ) : null}
              </div>
              {classificationAliases.length ? (
                <div className="copy-block">
                  <h4>Aliases</h4>
                  <div className="tag-row">
                    {classificationAliases.map((value) => (
                      <span key={`alias-${value}`} className="tag-chip">
                        {value}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              {classificationKeywords.length ? (
                <div className="copy-block">
                  <h4>Keywords</h4>
                  <div className="tag-row">
                    {classificationKeywords.map((value) => (
                      <span key={`keyword-${value}`} className="tag-chip">
                        {value}
                      </span>
                    ))}
                  </div>
                </div>
              ) : null}
              {classificationSourceRefs.length ? (
                <div className="copy-block">
                  <h4>Source references</h4>
                  <ul className="clean-list">
                    {classificationSourceRefs.map((value) => (
                      <li key={value}>{value}</li>
                    ))}
                  </ul>
                </div>
              ) : null}
              <div className="copy-block">
                <h4>{workspaceState.mode === 'live' ? 'Review notes' : 'Linked clarifications'}</h4>
                <ul className="clean-list">
                  {activeChange.clarifications.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
              <div className="copy-block daisy-block">
                <div className="daisy-heading">
                  <div>
                    <h4>Daisy coordination</h4>
                    <p>{daisyState.loading ? 'Loading coordination state…' : daisyState.message}</p>
                  </div>
                  <span className={`review-status review-${daisyState.mode === 'live' ? 'approved' : 'pending'}`}>
                    {daisyState.mode === 'live' ? 'Live feed' : 'Seeded feed'}
                  </span>
                </div>
                {activeDaisyReports.length ? (
                  <div className="daisy-report-list">
                    {activeDaisyReports.map((report) => (
                      <DaisyReportCard key={report.id} report={report} />
                    ))}
                  </div>
                ) : (
                  <EmptyState
                    title="No Daisy coordination yet"
                    body="This review case does not yet have a coordination report. Daisy will appear here once an escalated review flow produces one."
                  />
                )}
              </div>
            </>
          ) : (
            <EmptyState title="No active review" body="Select a change record to open the source review rail." />
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
          <Fact label="Proposed id" value={child.proposedSymbolId} />
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

function SubmissionPage() {
  const navigate = useNavigate();
  const [healthState, setHealthState] = useState({ loading: true, mode: 'loading', message: 'Checking API…' });
  const [isPending, startTransition] = useTransition();
  const [result, setResult] = useState(null);
  const [error, setError] = useState('');
  const [formState, setFormState] = useState({
    submitterName: '',
    submitterEmail: '',
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
      <div className="hero-panel glass-panel standards-hero">
        <div>
          <p className="eyebrow">External intake</p>
          <h2>Send SVG, PNG, or JSON into the live Symgov intake path without exposing draft Workspace state to public users.</h2>
        </div>
        <div className={`health-chip health-${healthState.mode}`}>
          <span>{healthState.loading ? 'Checking API…' : healthState.message}</span>
          <small>{appConfig.apiRoot || 'No API root configured'}</small>
        </div>
      </div>

      <div className="submission-grid">
        <form className="glass-panel pane form-panel" onSubmit={handleSubmit}>
          <SectionHeading title="Submission Form" subtitle="Public intake to Scott, then Vlad and Tracy" />
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
          <div className="action-stack horizontal">
            <button type="submit" className="action-button primary" disabled={isDisabled}>
              {isPending ? 'Submitting…' : 'Submit to live intake'}
            </button>
            <button type="button" className="action-button secondary" onClick={() => navigate('/standards')}>
              Back to Standards
            </button>
          </div>
          {error ? <p className="error-text">{error}</p> : null}
        </form>

        <section className="glass-panel pane">
          <SectionHeading title="Processing Model" subtitle="Current live path" />
          <div className="metric-column">
            <Metric title="Public route" value="POST /api/v1/public/external-submissions" />
            <Metric title="Accepted files" value="SVG, PNG, JPG, JSON" />
            <Metric title="Downstream" value="Scott → Vlad → Tracy → Libby → Daisy" />
          </div>
          <div className="copy-block">
            <h4>Why this app shell is dynamic</h4>
            <p>
              Submission status is already backend-driven. Standards and Workspace views are structured to swap from seeded data to versioned API reads as those endpoints arrive.
            </p>
          </div>
          {result ? (
            <div className="result-card">
              <p className="context-label">Submission accepted</p>
              <pre>{JSON.stringify(result, null, 2)}</pre>
            </div>
          ) : null}
        </section>
      </div>
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
