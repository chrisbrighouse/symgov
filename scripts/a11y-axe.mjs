// Automated accessibility pass: runs axe-core (WCAG 2.0/2.1 A and AA) over the
// Symgov surfaces in headless Chromium and writes one JSON result and one
// screenshot per route.
//
//   SYMGOV_A11Y_EMAIL=... SYMGOV_A11Y_PIN=... \
//     node scripts/a11y-axe.mjs --base-url http://localhost:8030 --out-dir /tmp/a11y-out
//
// Add `--serve dist --api http://127.0.0.1:8031` to have this script serve the
// built frontend at --base-url itself and proxy /api to a local API, the way
// production serves both from one origin. See docs/ops/a11y-axe.md.
//
// Local only: it refuses any base URL or API origin that is not localhost or
// 127.0.0.1. Never point it at production; it signs in and loads every
// surface as the configured accounts.
//
// Accounts (env): SYMGOV_A11Y_EMAIL/_PIN signs in for the Catalog, Details and
// Profile routes; SYMGOV_A11Y_STAFF_EMAIL/_PIN (global admin, reviewer and
// submitter) for Submissions, Workspace and Reviews; SYMGOV_A11Y_ORG_ADMIN_EMAIL
// /_PIN (an organization's admin) for the organization admin page. A missing
// staff or org admin account falls back to the default one. If sign-in asks
// for an organization, SYMGOV_A11Y_ORG_CODE picks it, else the first choice.
// SYMGOV_A11Y_SYMBOL is the catalog ID the Details routes open (default S-1).
// SYMGOV_A11Y_CHROME overrides the Chromium binary.
//
// Exits 1 when any route has a serious or critical violation, 2 on a setup
// failure (no sign-in, a route that never settles, Chromium not starting).
import { spawn } from 'node:child_process';
import { createReadStream, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { createServer, request as httpRequest } from 'node:http';
import { dirname, extname, join, normalize, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const AXE_PATH = join(REPO_ROOT, 'node_modules/axe-core/axe.min.js');
const DEFAULT_CHROME = '/root/.cache/ms-playwright/chromium-1223/chrome-linux64/chrome';
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];
const IMPACTS = ['critical', 'serious', 'moderate', 'minor'];
const LOCAL_HOSTS = new Set(['localhost', '127.0.0.1', '[::1]']);
const SETTLE_QUIET_MS = 750;
const SETTLE_TIMEOUT_MS = 30000;
// Mirrors DETAILS_TABS in frontend/src/symbolDetailsRoute.js.
const DETAILS_TABS = ['classification', 'details', 'connections', 'labels', 'source', 'history', 'comments'];

function parseArgs(argv) {
  const options = {};
  for (let index = 0; index < argv.length; index += 1) {
    const flag = argv[index];
    if (!flag.startsWith('--')) throw new Error(`Unexpected argument: ${flag}`);
    options[flag.slice(2)] = argv[index + 1];
    index += 1;
  }
  return {
    baseUrl: (options['base-url'] || process.env.SYMGOV_A11Y_BASE_URL || '').replace(/\/$/, ''),
    outDir: resolve(options['out-dir'] || process.env.SYMGOV_A11Y_OUT_DIR || 'a11y-axe-results'),
    serve: options.serve ? resolve(options.serve) : '',
    api: (options.api || '').replace(/\/$/, ''),
    only: options.only ? options.only.split(',') : null
  };
}

function requireLocal(label, value) {
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new Error(`${label} must be a full URL, got ${JSON.stringify(value)}`);
  }
  if (!LOCAL_HOSTS.has(url.hostname)) {
    throw new Error(`${label} ${value} is not local. This script runs only against a local stack, never production.`);
  }
  return url;
}

function accounts() {
  const user = { name: 'user', email: process.env.SYMGOV_A11Y_EMAIL, pin: process.env.SYMGOV_A11Y_PIN };
  if (!user.email || !user.pin) throw new Error('SYMGOV_A11Y_EMAIL and SYMGOV_A11Y_PIN are required');
  const pick = (name, prefix) => (process.env[`${prefix}_EMAIL`] && process.env[`${prefix}_PIN`]
    ? { name, email: process.env[`${prefix}_EMAIL`], pin: process.env[`${prefix}_PIN`] }
    : { ...user, fallbackFor: name });
  return { user, staff: pick('staff', 'SYMGOV_A11Y_STAFF'), orgAdmin: pick('orgAdmin', 'SYMGOV_A11Y_ORG_ADMIN') };
}

function targetRoutes(symbol) {
  const details = DETAILS_TABS.map((tab) => ({
    id: `details-${tab}`,
    label: `Details: ${tab}`,
    hash: `/standards?symbol=${encodeURIComponent(symbol)}&view=details&tab=${tab}`,
    account: 'user'
  }));
  return [
    { id: 'submissions', label: 'Submissions', hash: '/standards/submit', account: 'staff' },
    { id: 'workspace', label: 'Workspace', hash: '/workspace', account: 'staff' },
    { id: 'reviews', label: 'Reviews', hash: '/reviews', account: 'staff' },
    { id: 'catalog', label: 'Catalog (Standards)', hash: '/standards', account: 'user' },
    ...details,
    { id: 'organization-admin', label: 'Organization admin', hash: '/organization/admin', account: 'orgAdmin' },
    { id: 'profile', label: 'Profile', hash: '/profile', account: 'user' }
  ];
}

// --- Optional local static server with an /api reverse proxy ---------------

const CONTENT_TYPES = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml',
  '.png': 'image/png', '.json': 'application/json', '.ico': 'image/x-icon', '.woff2': 'font/woff2'
};

function startStaticServer(root, apiOrigin, port) {
  const api = new URL(apiOrigin);
  const server = createServer((req, res) => {
    if (req.url.startsWith('/api/')) {
      const upstream = httpRequest({
        hostname: api.hostname, port: api.port, path: req.url, method: req.method,
        // The Host header is kept, as a same-origin reverse proxy does, so the
        // API's CSRF host check sees the browser's origin.
        headers: { ...req.headers, 'x-forwarded-proto': 'http' }
      }, (upstreamRes) => {
        res.writeHead(upstreamRes.statusCode, upstreamRes.headers);
        upstreamRes.pipe(res);
      });
      upstream.on('error', () => {
        res.writeHead(502, { 'content-type': 'text/plain' });
        res.end('Local API unavailable');
      });
      req.pipe(upstream);
      return;
    }
    const pathname = decodeURIComponent(new URL(req.url, 'http://local').pathname);
    let file = normalize(join(root, pathname === '/' ? 'index.html' : pathname));
    if (!file.startsWith(root) || !existsSync(file) || statSync(file).isDirectory()) file = join(root, 'index.html');
    res.writeHead(200, { 'content-type': CONTENT_TYPES[extname(file)] || 'application/octet-stream' });
    createReadStream(file).pipe(res);
  });
  return new Promise((resolveServer, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', () => resolveServer(server));
  });
}

// --- Chromium over the DevTools protocol -----------------------------------

const sleep = (ms) => new Promise((done) => setTimeout(done, ms));

async function launchChrome(profileDir) {
  const binary = process.env.SYMGOV_A11Y_CHROME || DEFAULT_CHROME;
  if (!existsSync(binary)) throw new Error(`Chromium not found at ${binary}`);
  rmSync(profileDir, { recursive: true, force: true });
  mkdirSync(profileDir, { recursive: true });
  const child = spawn(binary, [
    '--headless=new', '--no-sandbox', '--disable-gpu', '--remote-debugging-port=0',
    `--user-data-dir=${profileDir}`, '--window-size=1440,900', '--no-first-run', '--no-default-browser-check',
    'about:blank'
  ], { stdio: 'ignore' });
  const portFile = join(profileDir, 'DevToolsActivePort');
  for (let attempt = 0; attempt < 100 && !existsSync(portFile); attempt += 1) await sleep(100);
  if (!existsSync(portFile)) {
    child.kill('SIGKILL');
    throw new Error('Chromium did not open a DevTools port');
  }
  const port = readFileSync(portFile, 'utf8').split('\n')[0].trim();
  const targets = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
  const page = targets.find((target) => target.type === 'page');
  return { child, wsUrl: page.webSocketDebuggerUrl };
}

class Cdp {
  constructor(wsUrl) {
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = [];
    this.socket = new WebSocket(wsUrl);
    this.socket.addEventListener('message', (event) => {
      const message = JSON.parse(event.data);
      if (message.id && this.pending.has(message.id)) {
        const { resolveCall, rejectCall } = this.pending.get(message.id);
        this.pending.delete(message.id);
        if (message.error) rejectCall(new Error(`${message.error.message} ${message.error.data || ''}`.trim()));
        else resolveCall(message.result);
      } else if (message.method) {
        for (const listener of this.listeners) listener(message);
      }
    });
  }

  open() {
    return new Promise((done, fail) => {
      this.socket.addEventListener('open', done, { once: true });
      this.socket.addEventListener('error', fail, { once: true });
    });
  }

  send(method, params = {}) {
    const id = this.nextId;
    this.nextId += 1;
    this.socket.send(JSON.stringify({ id, method, params }));
    return new Promise((resolveCall, rejectCall) => this.pending.set(id, { resolveCall, rejectCall }));
  }

  on(listener) {
    this.listeners.push(listener);
  }

  async evaluate(expression) {
    const result = await this.send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description || result.exceptionDetails.text);
    }
    return result.result.value;
  }
}

// Counts in-flight requests to the app's own origin; third-party fonts are
// not waited for.
function trackNetwork(cdp, origin) {
  const inFlight = new Set();
  let lastChange = Date.now();
  cdp.on((message) => {
    if (message.method === 'Network.requestWillBeSent' && message.params.request.url.startsWith(origin)) {
      inFlight.add(message.params.requestId);
      lastChange = Date.now();
    } else if (['Network.loadingFinished', 'Network.loadingFailed'].includes(message.method)) {
      if (inFlight.delete(message.params.requestId)) lastChange = Date.now();
    }
  });
  return { idleFor: () => (inFlight.size === 0 ? Date.now() - lastChange : 0), reset: () => inFlight.clear() };
}

async function waitForSettled(cdp, network) {
  const deadline = Date.now() + SETTLE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const state = await cdp.evaluate(`(() => ({
      ready: document.readyState === 'complete',
      mounted: Boolean(document.querySelector('#root')?.children.length),
      checking: /Checking access|Opening your Symgov session/.test(document.body?.innerText || ''),
      busy: Boolean(document.querySelector('[aria-busy="true"]'))
    }))()`);
    if (state.ready && state.mounted && !state.checking && !state.busy && network.idleFor() >= SETTLE_QUIET_MS) return true;
    await sleep(200);
  }
  return false;
}

async function navigate(cdp, network, url) {
  // A fresh document every time: a hash-only change would keep the old auth state.
  await cdp.send('Page.navigate', { url: 'about:blank' });
  await sleep(100);
  network.reset();
  await cdp.send('Page.navigate', { url });
  return waitForSettled(cdp, network);
}

async function signIn(cdp, network, baseUrl, account) {
  await cdp.send('Network.clearBrowserCookies');
  await navigate(cdp, network, `${baseUrl}/#/login`);
  const outcome = await cdp.evaluate(`(async () => {
    const post = (path, body) => fetch('/api/v1/auth/' + path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'content-type': 'application/json' }, body: JSON.stringify(body)
    }).then(async (response) => ({ status: response.status, body: await response.json().catch(() => ({})) }));
    let result = await post('login', ${JSON.stringify({ email: account.email, pin: account.pin })});
    const challenge = result.body.selectionChallenge;
    if (result.status === 200 && challenge) {
      const wanted = ${JSON.stringify((process.env.SYMGOV_A11Y_ORG_CODE || '').toLowerCase())};
      const choice = challenge.choices.find((item) => item.code.toLowerCase() === wanted) || challenge.choices[0];
      result = await post('select-organization', { token: challenge.token, organizationId: choice.organizationId });
    }
    return { status: result.status, user: result.body.user ? result.body.user.email : null,
      organization: result.body.user?.organization?.code || null };
  })()`);
  if (outcome.status !== 200 || !outcome.user) {
    throw new Error(`Sign-in failed for the ${account.name} account (HTTP ${outcome.status})`);
  }
  return outcome;
}

async function runAxe(cdp, axeSource) {
  const loaded = await cdp.evaluate('typeof window.axe');
  if (loaded !== 'object') await cdp.evaluate(`${axeSource};'loaded'`);
  return cdp.evaluate(`axe.run(document, {
    runOnly: { type: 'tag', values: ${JSON.stringify(AXE_TAGS)} },
    resultTypes: ['violations', 'incomplete']
  }).then((result) => ({
    testEngine: result.testEngine.version,
    url: result.url,
    violations: result.violations.map((rule) => ({
      id: rule.id, impact: rule.impact, help: rule.help, helpUrl: rule.helpUrl, tags: rule.tags,
      nodes: rule.nodes.map((node) => ({
        target: node.target, html: node.html.slice(0, 400), failureSummary: node.failureSummary,
        any: node.any.map((check) => ({ id: check.id, data: check.data, message: check.message }))
      }))
    })),
    // Needs manual review: axe could not decide, e.g. text over an image or gradient.
    incomplete: result.incomplete.map((rule) => ({
      id: rule.id, impact: rule.impact, help: rule.help,
      nodes: rule.nodes.map((node) => ({ target: node.target, html: node.html.slice(0, 200) }))
    })),
    passes: result.passes.length
  }))`);
}

async function pageState(cdp) {
  return cdp.evaluate(`(() => ({
    hash: location.hash,
    title: document.title,
    heading: document.querySelector('main h1, main h2')?.textContent?.trim().slice(0, 80) || '',
    accessDenied: /You do not have access to this area/.test(document.body.innerText),
    selectedTab: document.querySelector('[role="tab"][aria-selected="true"]')?.textContent?.trim() || ''
  }))()`);
}

function impactCounts(violations) {
  const counts = Object.fromEntries(IMPACTS.map((impact) => [impact, 0]));
  for (const rule of violations) counts[rule.impact || 'minor'] += rule.nodes.length;
  return counts;
}

function printSummary(rows) {
  const header = ['route', 'account', 'landed on', ...IMPACTS, 'violations (nodes)'];
  const lines = rows.map((row) => [
    row.id, row.account, row.note || row.landedOn, ...IMPACTS.map((impact) => String(row.counts[impact])),
    row.rules.join(' ')
  ]);
  const widths = header.map((title, column) => Math.max(title.length, ...lines.map((line) => Math.min(line[column].length, 60))));
  const format = (cells) => cells.map((cell, column) => (column === cells.length - 1 ? cell : cell.slice(0, 60).padEnd(widths[column]))).join('  ');
  console.log(format(header));
  console.log(widths.map((width) => '-'.repeat(width)).join('  '));
  for (const line of lines) console.log(format(line));
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  if (!options.baseUrl) throw new Error('--base-url (or SYMGOV_A11Y_BASE_URL) is required');
  const base = requireLocal('Base URL', options.baseUrl);
  if (options.serve && !options.api) throw new Error('--serve needs --api <local API origin>');
  if (options.api) requireLocal('API origin', options.api);
  if (!existsSync(AXE_PATH)) throw new Error(`axe-core not found at ${AXE_PATH}; run npm install`);
  const axeSource = readFileSync(AXE_PATH, 'utf8');
  const users = accounts();
  const symbol = process.env.SYMGOV_A11Y_SYMBOL || 'S-1';
  const routes = targetRoutes(symbol).filter((route) => !options.only || options.only.includes(route.id));
  mkdirSync(options.outDir, { recursive: true });

  const server = options.serve ? await startStaticServer(options.serve, options.api, Number(base.port || 80)) : null;
  const profileDir = join(options.outDir, '.chrome-profile');
  const chrome = await launchChrome(profileDir);
  const cdp = new Cdp(chrome.wsUrl);
  const rows = [];
  let setupFailure = false;
  try {
    await cdp.open();
    await cdp.send('Page.enable');
    await cdp.send('Network.enable');
    await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
    const network = trackNetwork(cdp, base.origin);
    let signedInAs = '';
    for (const route of routes) {
      const account = users[route.account];
      if (signedInAs !== account.email) {
        const session = await signIn(cdp, network, options.baseUrl, account);
        signedInAs = account.email;
        console.error(`Signed in as ${route.account}${account.fallbackFor ? ' (fallback: default account)' : ''}${session.organization ? ` in ${session.organization}` : ''}`);
      }
      const settled = await navigate(cdp, network, `${options.baseUrl}/#${route.hash}`);
      const state = await pageState(cdp);
      const result = await runAxe(cdp, axeSource);
      const shot = await cdp.send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
      writeFileSync(join(options.outDir, `${route.id}.png`), Buffer.from(shot.data, 'base64'));
      const record = { route, account: route.account, settled, state, ...result };
      writeFileSync(join(options.outDir, `${route.id}.json`), `${JSON.stringify(record, null, 2)}\n`);
      if (!settled) setupFailure = true;
      rows.push({
        id: route.id,
        account: route.account,
        landedOn: state.hash.slice(0, 60),
        note: !settled ? 'did not settle' : state.accessDenied ? 'ACCESS DENIED' : '',
        counts: impactCounts(result.violations),
        rules: result.violations.map((rule) => `${rule.id}(${rule.nodes.length})`),
        needsReview: result.incomplete.map((rule) => `${rule.id}(${rule.nodes.length})`)
      });
    }
  } finally {
    cdp.socket.close();
    chrome.child.kill('SIGKILL');
    if (server) server.close();
    await sleep(200);
    rmSync(profileDir, { recursive: true, force: true });
  }

  writeFileSync(join(options.outDir, 'summary.json'), `${JSON.stringify(rows, null, 2)}\n`);
  printSummary(rows);
  console.log(`\nResults and screenshots: ${options.outDir}`);
  const blocking = rows.filter((row) => row.counts.critical + row.counts.serious > 0);
  if (setupFailure) process.exit(2);
  if (blocking.length) {
    console.log(`${blocking.length} route(s) with serious or critical violations.`);
    process.exit(1);
  }
}

main().catch((error) => {
  console.error(error.message);
  process.exit(2);
});
