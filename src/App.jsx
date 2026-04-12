import { useEffect, useMemo, useState, useTransition } from 'react';
import { NavLink, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom';
import { fetchHealth, submitExternalSubmission } from './api.js';
import { appConfig } from './config.js';
import { changeQueue, submissionPresets, symbols } from './data.js';

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
  const [activeId, setActiveId] = useState(changeQueue[0]?.id || '');

  const filteredQueue = useMemo(() => {
    const normalizedQuery = query.trim().toLowerCase();

    if (!normalizedQuery) {
      return changeQueue;
    }

    return changeQueue.filter((item) =>
      [item.id, item.symbolId, item.title, item.owner, item.summary].some((value) =>
        value.toLowerCase().includes(normalizedQuery)
      )
    );
  }, [query]);

  useEffect(() => {
    if (!filteredQueue.some((item) => item.id === activeId)) {
      setActiveId(filteredQueue[0]?.id || '');
    }
  }, [filteredQueue, activeId]);

  const activeChange = filteredQueue.find((item) => item.id === activeId) || filteredQueue[0];

  return (
    <section className="experience-shell">
      <div className="hero-panel glass-panel workspace-hero">
        <div>
          <p className="eyebrow">Queue-first Governance Workspace</p>
          <h2>Review high-impact changes with compare context, pack impact, and Standards-linked clarifications in one surface.</h2>
        </div>
        <label className="field search-field">
          <span>Search queue</span>
          <input
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Search change id, symbol, owner, or summary"
          />
        </label>
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
                  Owner {item.owner} · {item.pages} pages · {item.packs} packs
                </small>
              </button>
            ))}
          </div>
        </section>

        <section className="glass-panel pane compare-pane">
          <SectionHeading title="Active Compare" subtitle="Baseline versus proposed review context" />
          {activeChange ? (
            <>
              <div className="detail-heading">
                <div>
                  <h3>{activeChange.title}</h3>
                  <p>{activeChange.summary}</p>
                </div>
                <span className="status-pill">{activeChange.status}</span>
              </div>
              <div className="compare-stage">
                <div className="symbol-stage comparison">
                  <p className="context-label">Baseline</p>
                  <SymbolGlyph symbolId={activeChange.symbolId} large />
                </div>
                <div className="symbol-stage comparison">
                  <p className="context-label">Proposed</p>
                  <SymbolGlyph symbolId={activeChange.symbolId} large variant />
                </div>
              </div>
              <div className="fact-grid">
                <Fact label="Owner" value={activeChange.owner} />
                <Fact label="Due" value={activeChange.due} />
                <Fact label="Impacted Pages" value={String(activeChange.pages)} />
                <Fact label="Impacted Packs" value={String(activeChange.packs)} />
              </div>
            </>
          ) : (
            <EmptyState title="No queued records" body="The queue is clear." />
          )}
        </section>

        <section className="glass-panel pane">
          <SectionHeading title="Approval Rail" subtitle="Risk, impact, and next actions" />
          {activeChange ? (
            <>
              <div className="metric-column">
                <Metric title="Risk" value={activeChange.risk} />
                <Metric title="Priority" value={activeChange.priority} />
                <Metric title="Status" value={activeChange.status} />
              </div>
              <div className="action-stack">
                <button type="button" className="action-button primary">Approve</button>
                <button type="button" className="action-button secondary">Request changes</button>
                <button type="button" className="action-button secondary">Reassign owner</button>
              </div>
              <div className="copy-block">
                <h4>Linked clarifications</h4>
                <ul className="clean-list">
                  {activeChange.clarifications.map((note) => (
                    <li key={note}>{note}</li>
                  ))}
                </ul>
              </div>
            </>
          ) : (
            <EmptyState title="No active review" body="Select a change record to open the approval rail." />
          )}
        </section>
      </div>
    </section>
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
              accept=".svg,.png,.json"
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
