import { useEffect, useMemo, useState, useTransition } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { fetchHealth, fetchWorkspaceDaisyReports, fetchWorkspaceReviewCases, submitExternalSubmission } from './api.js';
import { appConfig } from './config.js';
import { changeQueue, daisyCoordinationReports, submissionPresets, symbols } from './data.js';

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
        <div className="brand-mark">SG</div>
        <div>
          <p className="eyebrow">Symbol governance system</p>
          <h1>symgov</h1>
        </div>
      </div>
      <nav className="top-nav" aria-label="Primary navigation">
        <NavLink to="/workspace" className={({ isActive }) => navClass(isActive)}>
          Workspace
        </NavLink>
        <NavLink to="/standards" className={({ isActive }) => navClass(isActive)}>
          Standards
        </NavLink>
        <NavLink to="/standards/submit" className={({ isActive }) => navClass(isActive)}>
          Submit
        </NavLink>
      </nav>
      <div className="build-chip">v{appConfig.version} · Build {appConfig.build || 'local'}</div>
    </header>
  );
}

function StandardsPage() {
  const [query, setQuery] = useState('');
  const [activeId, setActiveId] = useState(symbols[0]?.id || '');

  const filteredSymbols = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    if (!normalizedQuery) {
      return symbols;
    }

    return symbols.filter((symbol) =>
      [symbol.id, symbol.name, symbol.category, symbol.pack, ...symbol.keywords].some((value) =>
        value.toLowerCase().includes(normalizedQuery)
      )
    );
  }, [query]);

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
                <SymbolGlyph symbolId={symbol.id} />
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
                <SymbolGlyph symbolId={activeSymbol.id} large />
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
                <p>{activeSymbol.metric}</p>
              </div>
              <div className="metric-column">
                <Metric title="Open clarifications" value={String(activeSymbol.clarificationCount)} />
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
  const [workspaceState, setWorkspaceState] = useState({
    loading: true,
    mode: appConfig.apiRoot ? 'loading' : 'seeded',
    message: appConfig.apiRoot ? 'Loading live Workspace review…' : 'No API root configured. Showing seeded queue.',
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
          message: reviewResult.items.length ? reviewResult.message : 'No live Workspace review cases are currently open.',
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

  const filteredQueue = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    if (!normalizedQuery) {
      return workspaceState.items;
    }

    return workspaceState.items.filter((item) =>
      [
        item.id,
        item.symbolId,
        item.title,
        item.owner,
        item.summary,
        item.sourceFileName || '',
        ...(item.children || []).map((child) => child.proposedSymbolName || '')
      ].some((value) => String(value).toLowerCase().includes(normalizedQuery))
    );
  }, [query, workspaceState.items]);

  useEffect(() => {
    if (!filteredQueue.some((item) => item.id === activeId)) {
      setActiveId(filteredQueue[0]?.id || '');
    }
  }, [filteredQueue, activeId]);

  const activeChange = filteredQueue.find((item) => item.id === activeId) || filteredQueue[0];
  const activeChildren = activeChange?.children || [];
  const sourceComment = sourceComments[activeChange?.id] || '';
  const classificationAliases = activeChange?.aliases || [];
  const classificationKeywords = activeChange?.keywords || [];
  const classificationSourceRefs = activeChange?.sourceRefs || [];
  const activeDaisyReports = useMemo(
    () => daisyState.items.filter((item) => item.reviewCaseId === activeChange?.id),
    [activeChange?.id, daisyState.items]
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

  return (
    <section className="experience-shell">
      <div className="hero-panel glass-panel workspace-hero">
        <div>
          <p className="eyebrow">Queue-first Governance Workspace</p>
          <h2>Review split symbols with traceable source context, visual inspection, and draft review notes before deciding what each extracted record should do next.</h2>
        </div>
        <div className="action-stack">
          <label className="field search-field">
            <span>Search queue</span>
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search change id, symbol, owner, summary, or source file"
            />
          </label>
          <div className={`health-chip health-${workspaceState.mode}`}>
            <span>{workspaceState.loading ? 'Loading Workspace…' : workspaceState.message}</span>
            <small>{workspaceState.mode === 'live' ? 'Live review cases' : 'Seeded fallback queue'}</small>
          </div>
        </div>
      </div>

      <div className="workspace-grid">
        <section className="glass-panel pane">
          <SectionHeading title="Queue and Bulk Tools" subtitle={`${filteredQueue.length} active records`} />
          <div className="stack-list">
            {filteredQueue.map((item) => (
              <button
                key={item.id}
                type="button"
                className={`queue-card ${item.id === activeId ? 'active' : ''}`}
                onClick={() => setActiveId(item.id)}
              >
                <div className="queue-card-topline">
                  <strong>{item.id}</strong>
                  <span className={`priority-chip priority-${item.priority.toLowerCase()}`}>{item.priority}</span>
                </div>
                <p>{item.symbolId} · {item.title}</p>
                <small>
                  Owner {item.owner} · {item.childCount || item.children?.length || 0} child symbols · {item.sourceFileName || 'Source file pending'}
                </small>
              </button>
            ))}
          </div>
        </section>

        <section className="glass-panel pane compare-pane">
          <SectionHeading title="Split Review" subtitle="Scrollable visual review for extracted child symbols" />
          {activeChange ? (
            <>
              <div className="detail-heading">
                <div>
                  <h3>{activeChange.title}</h3>
                  <p>{activeChange.summary}</p>
                </div>
                <span className="status-pill">{activeChange.status}</span>
              </div>
              <div className="fact-grid">
                <Fact label="Parent file" value={activeChange.sourceFileName || 'Not recorded'} />
                <Fact label="Intake record" value={activeChange.intakeRecordId || 'Pending'} />
                <Fact label="Open review by" value={activeChange.due} />
                <Fact label="Child symbols" value={String(activeChange.childCount || activeChildren.length)} />
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
                <p>Each extracted child can be reviewed independently while keeping all notes tied back to the source raster file for traceability.</p>
              </div>
              {activeChange.classificationSummary ? (
                <div className="copy-block">
                  <h4>Libby summary</h4>
                  <p>{activeChange.classificationSummary}</p>
                </div>
              ) : null}
              {activeChildren.length ? (
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
              ) : (
                <EmptyState title="No extracted child symbols" body="This queue item does not yet include split children for review." />
              )}
            </>
          ) : (
            <EmptyState title="No queued records" body="The queue is clear." />
          )}
        </section>

        <section className="glass-panel pane">
          <SectionHeading title="Source Review Rail" subtitle="Case-level traceability and notes" />
          {activeChange ? (
            <>
              <div className="metric-column">
                <Metric title="Risk" value={activeChange.risk} />
                <Metric title="Priority" value={activeChange.priority} />
                <Metric title="Status" value={activeChange.status} />
              </div>
              <div className="context-card">
                <p className="context-label">Parent source file</p>
                <strong>{activeChange.sourceFileName || 'Not recorded'}</strong>
                <p>{activeChildren.length} extracted records currently linked to this file.</p>
              </div>
              <div className="fact-grid">
                <Fact label="Discipline" value={activeChange.engineeringDiscipline || 'Pending'} />
                <Fact label="Format" value={activeChange.format || 'Pending'} />
                <Fact label="Industry" value={activeChange.industry || 'Pending'} />
                <Fact label="Symbol family" value={activeChange.symbolFamily || 'Pending'} />
                <Fact label="Process category" value={activeChange.processCategory || 'Pending'} />
                <Fact label="Equipment class" value={activeChange.parentEquipmentClass || 'Pending'} />
                <Fact label="Standards source" value={activeChange.standardsSource || 'Pending'} />
                <Fact label="Provenance class" value={activeChange.libraryProvenanceClass || 'Pending'} />
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
              <label className="field">
                <span>Source file review comment</span>
                <textarea
                  rows="6"
                  value={sourceComment}
                  onChange={(event) => updateSourceComment(activeChange.id, event.target.value)}
                  placeholder="Capture context that applies to the whole source file, such as sheet-level quality issues, missing labels, or review guidance for the extracted set."
                />
              </label>
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
  const statusLabel =
    reviewState.action === 'approved'
      ? 'Approve selected'
      : reviewState.action === 'request_changes'
        ? 'Changes requested'
        : reviewState.action === 'deleted'
          ? 'Delete selected'
          : 'Awaiting decision';

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
          <span className={`review-status review-${reviewState.action}`}>{statusLabel}</span>
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
        <div className="action-stack horizontal split-actions">
          <button
            type="button"
            className={`action-button primary ${reviewState.action === 'approved' ? 'selected' : ''}`}
            onClick={() => onUpdate(child.id, { action: 'approved' })}
          >
            Approve
          </button>
          <button
            type="button"
            className={`action-button secondary ${isRequestChanges ? 'selected' : ''}`}
            onClick={() => onUpdate(child.id, { action: isRequestChanges ? 'pending' : 'request_changes' })}
          >
            Request Changes
          </button>
          <button
            type="button"
            className={`action-button secondary danger ${reviewState.action === 'deleted' ? 'selected' : ''}`}
            onClick={() => onUpdate(child.id, { action: 'deleted' })}
          >
            Delete
          </button>
        </div>
        {isRequestChanges ? (
          <label className="field request-field">
            <span>Requested changes</span>
            <textarea
              rows="4"
              value={reviewState.requestDetails}
              onChange={(event) => onUpdate(child.id, { requestDetails: event.target.value })}
              placeholder="Describe what needs to change before this extracted symbol can be approved."
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
            <Metric title="Accepted files" value="SVG, PNG, JSON" />
            <Metric title="Downstream" value="Scott → Vlad → Tracy" />
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
