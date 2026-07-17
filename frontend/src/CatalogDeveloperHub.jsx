import { useEffect, useMemo, useRef, useState } from 'react';
import { Link } from 'react-router-dom';

import {
  askCatalogIntegrationEd,
  createCatalogSelfServiceApiKey,
  fetchCatalogDeveloperManifest,
  fetchCatalogDeveloperOpenApi,
  fetchCatalogSelfServiceApiKey,
  revokeCatalogSelfServiceApiKey,
  runCatalogDeveloperSandbox
} from './api.js';
import {
  buildCatalogCodeExample,
  catalogExampleBodyForEndpoint,
  materializeCatalogEndpoint,
  resolveDeveloperCitation,
  sandboxOperationForEndpoint
} from './catalogDeveloper.js';

const LANGUAGES = [
  ['curl', 'curl'],
  ['typescript', 'TypeScript'],
  ['python', 'Python'],
  ['csharp', 'C#']
];

const CATALOG_API_SCOPES = [
  ['catalog.read', 'Read Catalog symbols and taxonomy'],
  ['catalog.preview', 'Retrieve symbol previews'],
  ['catalog.ed.query', 'Ask Catalog Ed'],
  ['catalog.feedback.write', 'Submit Catalog feedback'],
  ['catalog.usage.read', 'Read integration usage']
];

const DEFAULT_KEY_FORM = {
  customerName: '',
  integrationName: '',
  scopes: ['catalog.read', 'catalog.preview'],
  expiresAt: ''
};

const FALLBACK_ENDPOINTS = [
  { method: 'GET', path: '/api/v1/catalog/capabilities', scope: 'catalog.read', summary: 'Discover current integration capabilities.' },
  { method: 'GET', path: '/api/v1/catalog/taxonomy', scope: 'catalog.read', summary: 'Load canonical Catalog facets.' },
  { method: 'GET', path: '/api/v1/catalog/symbols', scope: 'catalog.read', summary: 'Filter and paginate published symbols.' },
  { method: 'GET', path: '/api/v1/catalog/symbols/{symbolRef}', scope: 'catalog.read', summary: 'Read one published symbol.' },
  { method: 'GET', path: '/api/v1/catalog/symbols/{symbolRef}/thumbnail', scope: 'catalog.read', summary: 'Retrieve a symbol thumbnail.' },
  { method: 'GET', path: '/api/v1/catalog/symbols/{symbolRef}/preview', scope: 'catalog.read', summary: 'Retrieve a symbol preview.' },
  { method: 'POST', path: '/api/v1/catalog/search', scope: 'catalog.read', summary: 'Search using application and drawing context.' },
  { method: 'POST', path: '/api/v1/catalog/ed/query', scope: 'catalog.ed.query', summary: 'Ask Catalog Ed for symbol guidance.' },
  { method: 'POST', path: '/api/v1/catalog/symbols/{symbolRef}/feedback', scope: 'catalog.feedback.write', summary: 'Submit feedback or an explicit review request.' }
];

const QUICKSTART_BODY = {
  query: 'smoke detector near stairwell',
  context: {
    application: 'Customer Portal',
    drawingType: 'life_safety_plan',
    preferredFormats: ['PNG']
  },
  limit: 10
};

function endpointRows(manifest, openApi) {
  if (Array.isArray(manifest?.endpoints) && manifest.endpoints.length) {
    return manifest.endpoints;
  }
  const paths = openApi?.paths;
  if (!paths || typeof paths !== 'object') return FALLBACK_ENDPOINTS;
  return Object.entries(paths).flatMap(([path, methods]) =>
    Object.entries(methods || {}).map(([method, operation]) => ({
      method: method.toUpperCase(),
      path,
      scope: operation?.['x-symgov-scope'] || operation?.security?.[0]?.CatalogApiKey?.[0] || 'catalog.read',
      summary: operation?.summary || 'Catalog integration operation.'
    }))
  );
}

export default function CatalogDeveloperHub() {
  const [apiKey, setApiKey] = useState('');
  const [toolApiKey, setToolApiKey] = useState('');
  const [unlocked, setUnlocked] = useState(false);
  const [manifest, setManifest] = useState(null);
  const [openApi, setOpenApi] = useState(null);
  const [accessState, setAccessState] = useState({ busy: false, message: '' });
  const [keyPanelOpen, setKeyPanelOpen] = useState(false);
  const [keyForm, setKeyForm] = useState(DEFAULT_KEY_FORM);
  const [keyState, setKeyState] = useState({ busy: false, activeKey: null, rawKey: '', message: '' });
  const [language, setLanguage] = useState('typescript');
  const [selectedEndpoint, setSelectedEndpoint] = useState(FALLBACK_ENDPOINTS[6]);
  const [sandboxState, setSandboxState] = useState({ busy: false, output: null, message: '' });
  const [edQuestion, setEdQuestion] = useState('How should an internal portal paginate Catalog search results?');
  const [edState, setEdState] = useState({ busy: false, output: null, message: '' });
  const mountedRef = useRef(true);
  const requestControllersRef = useRef(new Set());

  function abortInFlightRequests() {
    requestControllersRef.current.forEach((controller) => controller.abort());
    requestControllersRef.current.clear();
  }

  function beginRequest() {
    const controller = new AbortController();
    requestControllersRef.current.add(controller);
    return controller;
  }

  function requestIsActive(controller) {
    return mountedRef.current && !controller.signal.aborted;
  }

  useEffect(() => {
    mountedRef.current = true;
    const clearForUnload = () => {
      abortInFlightRequests();
      setApiKey('');
      setToolApiKey('');
      setKeyState((current) => ({ ...current, rawKey: '' }));
      setUnlocked(false);
    };
    window.addEventListener('pagehide', clearForUnload);
    window.addEventListener('beforeunload', clearForUnload);
    return () => {
      mountedRef.current = false;
      abortInFlightRequests();
      window.removeEventListener('pagehide', clearForUnload);
      window.removeEventListener('beforeunload', clearForUnload);
    };
  }, []);

  const endpoints = useMemo(() => endpointRows(manifest, openApi), [manifest, openApi]);
  const codeExample = useMemo(() => {
    const path = materializeCatalogEndpoint(selectedEndpoint.path.replace('/api/v1', ''));
    return buildCatalogCodeExample({
      language,
      baseUrl: `${window.location.origin}/api/v1`,
      method: selectedEndpoint.method,
      path,
      body: catalogExampleBodyForEndpoint(selectedEndpoint.method, selectedEndpoint.path)
    });
  }, [language, selectedEndpoint]);

  async function unlockDeveloperHub(event) {
    event.preventDefault();
    const candidate = apiKey.trim();
    const controller = beginRequest();
    setToolApiKey('');
    setAccessState({ busy: true, message: 'Opening Integrator documentation…' });
    try {
      const manifestResult = await fetchCatalogDeveloperManifest(controller.signal);
      if (!requestIsActive(controller)) return;
      if (!manifestResult.ok) {
        setToolApiKey('');
        setUnlocked(false);
        setAccessState({ busy: false, message: manifestResult.message || 'Integrator access could not be validated.' });
        return;
      }
      const openApiResult = await fetchCatalogDeveloperOpenApi(controller.signal);
      if (!requestIsActive(controller)) return;
      if (!openApiResult.ok) {
        setToolApiKey('');
        setUnlocked(false);
        setAccessState({ busy: false, message: openApiResult.message || 'Integrator access could not be validated.' });
        return;
      }
      setManifest(manifestResult.payload);
      setOpenApi(openApiResult.payload);
      setToolApiKey(candidate);
      setUnlocked(true);
      setKeyState((current) => ({ ...current, busy: true, rawKey: '', message: 'Checking API key status…' }));
      const keyStatusResult = await fetchCatalogSelfServiceApiKey(controller.signal);
      if (!requestIsActive(controller)) return;
      setKeyState({
        busy: false,
        activeKey: keyStatusResult.ok ? keyStatusResult.payload?.activeKey || null : null,
        rawKey: '',
        message: keyStatusResult.ok ? '' : (keyStatusResult.message || 'API key status could not be loaded.')
      });
      setAccessState({
        busy: false,
        message: candidate
          ? 'Documentation opened. The API key is ready for sandbox tools.'
          : 'Documentation opened. Add an API key later if you need sandbox tools.'
      });
    } finally {
      requestControllersRef.current.delete(controller);
    }
  }

  function lockDeveloperHub() {
    abortInFlightRequests();
    setApiKey('');
    setToolApiKey('');
    setUnlocked(false);
    setManifest(null);
    setOpenApi(null);
    setKeyPanelOpen(false);
    setKeyForm(DEFAULT_KEY_FORM);
    setKeyState({ busy: false, activeKey: null, rawKey: '', message: '' });
    setSandboxState({ busy: false, output: null, message: '' });
    setEdState({ busy: false, output: null, message: '' });
    setAccessState({ busy: false, message: 'API key cleared from page memory.' });
  }

  function openKeyPanel() {
    setKeyPanelOpen(true);
    window.requestAnimationFrame(() => document.getElementById('api-key')?.scrollIntoView({ behavior: 'smooth', block: 'start' }));
  }

  function toggleKeyScope(scope) {
    setKeyForm((current) => ({
      ...current,
      scopes: current.scopes.includes(scope)
        ? current.scopes.filter((item) => item !== scope)
        : [...current.scopes, scope]
    }));
  }

  async function generateSelfServiceKey(event) {
    event.preventDefault();
    if (!keyForm.customerName.trim() || !keyForm.integrationName.trim() || keyForm.scopes.length === 0) {
      setKeyState((current) => ({ ...current, message: 'Customer, integration, and at least one scope are required.' }));
      return;
    }
    const controller = beginRequest();
    setKeyState((current) => ({ ...current, busy: true, rawKey: '', message: 'Generating API key…' }));
    try {
      const result = await createCatalogSelfServiceApiKey({
        customerName: keyForm.customerName.trim(),
        integrationName: keyForm.integrationName.trim(),
        scopes: keyForm.scopes,
        expiresAt: keyForm.expiresAt ? new Date(keyForm.expiresAt).toISOString() : null
      }, controller.signal);
      if (!requestIsActive(controller)) return;
      if (!result.ok) {
        setKeyState((current) => ({ ...current, busy: false, rawKey: '', message: result.message || 'API key could not be generated.' }));
        return;
      }
      setKeyState({
        busy: false,
        activeKey: result.payload.activeKey,
        rawKey: result.payload.rawKey,
        message: result.payload.warning
      });
      setApiKey(result.payload.rawKey);
      setToolApiKey(result.payload.rawKey);
    } finally {
      requestControllersRef.current.delete(controller);
    }
  }

  async function clearSelfServiceKey() {
    const activeKey = keyState.activeKey;
    if (!activeKey || !window.confirm('Clear and permanently revoke this API key? This cannot be undone.')) return;
    const controller = beginRequest();
    setKeyState((current) => ({ ...current, busy: true, message: 'Revoking API key…' }));
    try {
      const result = await revokeCatalogSelfServiceApiKey({
        keyId: activeKey.keyId,
        keyPrefix: activeKey.keyPrefix
      }, controller.signal);
      if (!requestIsActive(controller)) return;
      if (!result.ok) {
        setKeyState((current) => ({ ...current, busy: false, message: result.message || 'API key could not be revoked.' }));
        return;
      }
      setApiKey('');
      setToolApiKey('');
      setKeyForm(DEFAULT_KEY_FORM);
      setKeyState({ busy: false, activeKey: null, rawKey: '', message: 'API key cleared and permanently revoked.' });
    } finally {
      requestControllersRef.current.delete(controller);
    }
  }

  async function runSandbox() {
    const path = materializeCatalogEndpoint(selectedEndpoint.path.replace('/api/v1', ''));
    const operation = sandboxOperationForEndpoint(selectedEndpoint.method, path);
    if (!operation) {
      setSandboxState({ busy: false, output: null, message: 'This operation is not available in the read-only sandbox.' });
      return;
    }
    const controller = beginRequest();
    setSandboxState({ busy: true, output: null, message: 'Running synthetic request…' });
    try {
      const result = await runCatalogDeveloperSandbox(toolApiKey, {
        operation,
        query: operation.includes('search') ? 'smoke detector' : undefined,
        symbolRef: operation === 'symbol_detail' ? 'SANDBOX-FA-001' : undefined,
        context: operation === 'contextual_search' ? QUICKSTART_BODY.context : undefined,
        message: operation === 'ed_query' ? 'Find smoke detector symbols' : undefined,
        limit: 5
      }, controller.signal);
      if (!requestIsActive(controller)) return;
      setSandboxState({
        busy: false,
        output: result.ok ? result.payload : null,
        message: result.ok ? 'Synthetic sandbox response received. No Catalog records were changed.' : result.message
      });
    } finally {
      requestControllersRef.current.delete(controller);
    }
  }

  async function askEd(event) {
    event.preventDefault();
    if (!edQuestion.trim()) return;
    const controller = beginRequest();
    setEdState({ busy: true, output: null, message: 'Ed is checking the integration documentation…' });
    try {
      const result = await askCatalogIntegrationEd(toolApiKey, { message: edQuestion.trim() }, controller.signal);
      if (!requestIsActive(controller)) return;
      setEdState({
        busy: false,
        output: result.ok ? result.payload : null,
        message: result.ok ? 'Documentation-grounded answer received.' : result.message
      });
    } finally {
      requestControllersRef.current.delete(controller);
    }
  }

  if (!unlocked) {
    return (
      <section className="catalog-developer-hub catalog-developer-gate" aria-labelledby="developer-title">
        <div className="catalog-developer-hero">
          <h2 id="developer-title">Catalog Integrator Hub</h2>
          <p>Build internal portals, automation, and drawing-review integrations against the current Catalog API.</p>
        </div>
        <form className="catalog-developer-access-card" onSubmit={unlockDeveloperHub}>
          <h3>Open Integrator documentation</h3>
          <p>An API key is optional for documentation. Add one only if you want to run the sandbox or ask Ed.</p>
          <label>
            Catalog API key <span className="catalog-optional-label">Optional</span>
            <input
              type="password"
              autoComplete="off"
              value={apiKey}
              onChange={(event) => setApiKey(event.target.value)}
              placeholder="Paste your key for this page only"
              disabled={accessState.busy}
            />
          </label>
          <p className="catalog-key-safety">The key is kept in memory only and is never saved to browser storage.</p>
          <button type="submit" className="action-button primary" disabled={accessState.busy}>
            {accessState.busy ? 'Checking access…' : 'Open integrator documentation'}
          </button>
          {accessState.message ? <p role="status" className="catalog-developer-status">{accessState.message}</p> : null}
        </form>
      </section>
    );
  }

  return (
    <section className="catalog-developer-hub" aria-labelledby="developer-title">
      <header className="catalog-developer-hero">
        <div>
          <p className="eyebrow">Authenticated integration workspace</p>
          <h2 id="developer-title">Catalog Integrator Hub</h2>
          <p>Current v1 guidance, executable synthetic examples, and documentation-grounded help.</p>
        </div>
        <div className="catalog-developer-hero-actions">
          {keyState.activeKey ? <span className="catalog-key-badge"><span aria-hidden="true">●</span> Active key</span> : null}
          <button type="button" className="action-button primary" onClick={openKeyPanel}>
            {keyState.activeKey ? 'Manage API key' : 'Generate API key'}
          </button>
          <button type="button" className="ghost-button" onClick={lockDeveloperHub}>Change entered API key</button>
        </div>
      </header>

      <nav className="catalog-developer-local-nav" aria-label="Integrator documentation">
        <a href="#api-key" onClick={openKeyPanel}>API key</a>
        <a href="#quickstart">Quickstart</a>
        <a href="#reference">API reference</a>
        <a href="#sandbox">Sandbox</a>
        <a href="#ask-ed">Ask Ed</a>
        <a href="#changelog">Changelog</a>
      </nav>

      {keyPanelOpen ? (
        <section id="api-key" className="catalog-developer-panel catalog-key-manager" aria-labelledby="api-key-title">
          <div className="catalog-developer-section-heading">
            <div>
              <p className="eyebrow">Self-service access</p>
              <h3 id="api-key-title">Catalog API key</h3>
              <p>One active key is available per Integrator account. Key generation is free during the initial release; subscription access will be added later.</p>
            </div>
            <button type="button" className="ghost-button" onClick={() => setKeyPanelOpen(false)}>Close</button>
          </div>

          {keyState.activeKey ? (
            <article className="catalog-active-key" aria-label="Active API key">
              <div>
                <p className="catalog-key-status"><span aria-hidden="true">●</span> Active API key</p>
                <dl>
                  <div><dt>Prefix</dt><dd><code>{keyState.activeKey.keyPrefix}</code></dd></div>
                  <div><dt>Customer</dt><dd>{keyState.activeKey.customerName}</dd></div>
                  <div><dt>Integration</dt><dd>{keyState.activeKey.integrationName}</dd></div>
                  <div><dt>Scopes</dt><dd>{keyState.activeKey.scopes.join(', ')}</dd></div>
                  <div><dt>Expires</dt><dd>{keyState.activeKey.expiresAt ? new Date(keyState.activeKey.expiresAt).toLocaleString() : 'No expiry'}</dd></div>
                </dl>
              </div>
              <button type="button" className="action-button catalog-danger-button" onClick={clearSelfServiceKey} disabled={keyState.busy}>
                Clear and revoke key
              </button>
            </article>
          ) : (
            <form className="catalog-key-generator-form" onSubmit={generateSelfServiceKey}>
              <div className="catalog-key-form-grid">
                <label>
                  Customer name
                  <input
                    name="customerName"
                    value={keyForm.customerName}
                    onChange={(event) => setKeyForm((current) => ({ ...current, customerName: event.target.value }))}
                    maxLength="200"
                    required
                  />
                </label>
                <label>
                  Integration name
                  <input
                    name="integrationName"
                    value={keyForm.integrationName}
                    onChange={(event) => setKeyForm((current) => ({ ...current, integrationName: event.target.value }))}
                    maxLength="200"
                    required
                  />
                </label>
                <label>
                  Expiry <span className="catalog-optional-label">Optional</span>
                  <input
                    name="expiresAt"
                    type="datetime-local"
                    value={keyForm.expiresAt}
                    onChange={(event) => setKeyForm((current) => ({ ...current, expiresAt: event.target.value }))}
                  />
                </label>
              </div>
              <fieldset>
                <legend>API scopes</legend>
                <div className="catalog-key-scope-grid">
                  {CATALOG_API_SCOPES.map(([scope, label]) => (
                    <label key={scope}>
                      <input
                        type="checkbox"
                        checked={keyForm.scopes.includes(scope)}
                        onChange={() => toggleKeyScope(scope)}
                      />
                      <span><code>{scope}</code><small>{label}</small></span>
                    </label>
                  ))}
                </div>
              </fieldset>
              <button type="submit" className="action-button primary" disabled={keyState.busy || keyForm.scopes.length === 0}>
                {keyState.busy ? 'Generating…' : 'Generate API key'}
              </button>
            </form>
          )}

          {keyState.rawKey ? (
            <aside className="catalog-one-time-key" role="alert">
              <h4>Save this key now</h4>
              <p>This generated key must be saved securely. It won't be accessible again after you leave or clear this page.</p>
              <div>
                <code>{keyState.rawKey}</code>
                <button type="button" className="ghost-button" onClick={() => navigator.clipboard?.writeText(keyState.rawKey)}>Copy key</button>
              </div>
            </aside>
          ) : null}
          {keyState.message ? <p aria-live="polite" className="catalog-developer-status">{keyState.message}</p> : null}
        </section>
      ) : null}

      <section id="quickstart" className="catalog-developer-panel">
        <p className="eyebrow">Start here</p>
        <h3>Five-minute quickstart</h3>
        <ol>
          <li>Call <code>/catalog/capabilities</code> to discover current behavior.</li>
          <li>Load <code>/catalog/taxonomy</code> for filter labels.</li>
          <li>Search symbols and show the human-readable ID, such as <strong>0003-12</strong>.</li>
          <li>Retrieve symbol details and an authenticated preview.</li>
          <li>Use contextual search or Catalog Ed when a person describes a drawing need.</li>
        </ol>
        <p className="catalog-boundary-note">Downloads are not available. CORS is deployment-dependent; browser apps should use a backend-for-frontend.</p>
      </section>

      <section id="reference" className="catalog-developer-panel">
        <div className="catalog-developer-section-heading">
          <div>
            <p className="eyebrow">Current v1 contract</p>
            <h3>API reference</h3>
          </div>
          <label>
            Code language
            <select value={language} onChange={(event) => setLanguage(event.target.value)}>
              {LANGUAGES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
            </select>
          </label>
        </div>
        <div className="catalog-endpoint-grid">
          <div className="catalog-endpoint-list" aria-label="Catalog endpoints">
            {endpoints.map((endpoint) => (
              <button
                type="button"
                key={`${endpoint.method}-${endpoint.path}`}
                className={selectedEndpoint.path === endpoint.path && selectedEndpoint.method === endpoint.method ? 'active' : ''}
                onClick={() => setSelectedEndpoint(endpoint)}
              >
                <span className={`catalog-method method-${endpoint.method.toLowerCase()}`}>{endpoint.method}</span>
                <code>{endpoint.path}</code>
                <small>{endpoint.scope}</small>
              </button>
            ))}
          </div>
          <article className="catalog-code-card">
            <h4>{selectedEndpoint.summary}</h4>
            <pre><code>{codeExample}</code></pre>
          </article>
        </div>
      </section>

      <section id="sandbox" className="catalog-developer-panel">
        <p className="eyebrow">Synthetic and read-only</p>
        <h3>Try it in the sandbox</h3>
        <p>The sandbox runs on the current Symgov host with deterministic synthetic data. It is not a production clone and performs no Catalog mutation.</p>
        <button type="button" className="action-button" onClick={runSandbox} disabled={sandboxState.busy || !toolApiKey}>
          {sandboxState.busy ? 'Running…' : `Run ${selectedEndpoint.method} example`}
        </button>
        {!toolApiKey ? <p className="catalog-boundary-note">Add a Catalog API key to run sandbox examples.</p> : null}
        {sandboxState.message ? <p aria-live="polite" className="catalog-developer-status">{sandboxState.message}</p> : null}
        {sandboxState.output ? <pre className="catalog-response"><code>{JSON.stringify(sandboxState.output, null, 2)}</code></pre> : null}
      </section>

      <section id="ask-ed" className="catalog-developer-panel">
        <p className="eyebrow">Stateless documentation help</p>
        <h3>Ask Ed about integration</h3>
        <p>Ed answers from the current Catalog API documentation, cites relevant sections, and does not retain conversation history. Never include credentials.</p>
        <form onSubmit={askEd} className="catalog-ed-integration-form">
          <label>
            Integration question
            <textarea rows="4" value={edQuestion} onChange={(event) => setEdQuestion(event.target.value)} maxLength="2000" />
          </label>
          <button type="submit" className="action-button primary" disabled={edState.busy || !edQuestion.trim() || !toolApiKey}>
            {edState.busy ? 'Checking documentation…' : 'Ask Ed'}
          </button>
        </form>
        {!toolApiKey ? <p className="catalog-boundary-note">Add a Catalog API key to ask Ed.</p> : null}
        {edState.message ? <p aria-live="polite" className="catalog-developer-status">{edState.message}</p> : null}
        {edState.output ? (
          <article className="catalog-ed-answer">
            <p>{edState.output.answer}</p>
            {Array.isArray(edState.output.citations) && edState.output.citations.length ? (
              <ul>{edState.output.citations.map(resolveDeveloperCitation).filter(Boolean).map((citation) => (
                <li key={citation.href}>
                  {citation.href === '/support'
                    ? <Link to="/support">{citation.label}</Link>
                    : <a href={citation.href}>{citation.label}</a>}
                </li>
              ))}</ul>
            ) : null}
            {edState.output.code ? <pre><code>{edState.output.code}</code></pre> : null}
          </article>
        ) : null}
      </section>

      <section id="changelog" className="catalog-developer-panel catalog-developer-footer-grid">
        <div>
          <p className="eyebrow">Release notes</p>
          <h3>Changelog</h3>
          <p>Milestone 1 documents the current API, adds a Catalog-only reference, synthetic sandbox, generated examples, and stateless Ed integration help.</p>
        </div>
        <div>
          <h3>Need more help?</h3>
          <p>Use the existing Symgov support mechanism when Ed cannot resolve an integration question.</p>
          <Link className="action-button" to="/support">Open support</Link>
        </div>
      </section>
    </section>
  );
}
