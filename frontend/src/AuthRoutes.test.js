import { after, afterEach, before, beforeEach, test } from 'node:test';
import assert from 'node:assert/strict';
import React, { createElement, useEffect } from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { MemoryRouter, useLocation } from 'react-router-dom';
import { createServer } from 'vite';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

let App;
let vite;
let originalFetch;
let originalDocument;
let originalReact;

function response(status, payload) {
  const body = payload == null ? '' : JSON.stringify(payload);
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 401 ? 'Unauthorized' : 'OK',
    text: async () => body,
    json: async () => payload
  };
}

function user({ mustChangePin = false, mode = 'personal' } = {}) {
  return {
    id: 'u-1',
    email: 'user@example.test',
    displayName: 'Test User',
    roles: [],
    mustChangePin,
    subscription: { tier: 'free', status: 'active' },
    session: {
      mode,
      purpose: mustChangePin ? 'credential_change' : 'application',
      activeOrganizationId: mode === 'organization' ? 'org-1' : null
    },
    organization: mode === 'organization'
      ? { id: 'org-1', code: 'ORG1', displayName: 'Organization 1', baseRole: 'user', capabilities: [] }
      : null,
    capabilities: {}
  };
}

function LocationProbe({ onLocation }) {
  const location = useLocation();
  useEffect(() => {
    onLocation({
      pathname: location.pathname,
      search: location.search,
      hash: location.hash,
      state: location.state
    });
  }, [location, onLocation]);
  return createElement(
    'output',
    { id: 'auth-location-probe' },
    JSON.stringify({
      pathname: location.pathname,
      search: location.search,
      hash: location.hash,
      state: location.state
    })
  );
}

async function mount(initialEntry, fetchImpl) {
  const locations = [];
  globalThis.fetch = fetchImpl;
  let renderer;
  const onLocation = (location) => locations.push(location);
  await act(async () => {
    renderer = TestRenderer.create(createElement(
      MemoryRouter,
      { initialEntries: [initialEntry] },
      createElement(App),
      createElement(LocationProbe, { onLocation })
    ));
  });
  return { renderer, locations };
}

function currentLocation(renderer) {
  return JSON.parse(renderer.root.findByProps({ id: 'auth-location-probe' }).children[0]);
}

function loginForm(renderer) {
  return renderer.root.find((node) => node.type === 'form' && node.props.className === 'auth-card');
}

async function submitLogin(renderer) {
  const inputs = renderer.root.findAllByType('input');
  const email = inputs.find((node) => node.props.type === 'email');
  const pin = inputs.find((node) => node.props.autoComplete === 'current-password');
  await act(async () => {
    email.props.onChange({ target: { value: 'user@example.test' } });
    pin.props.onChange({ target: { value: '1234' } });
  });
  await act(async () => {
    await loginForm(renderer).props.onSubmit({ preventDefault() {} });
  });
}

function ordinaryLoginFetch({ initialUser = null, loginFrom = null } = {}) {
  const requests = [];
  const fetchImpl = async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ url, method, body: options.body });
    if (url.endsWith('/auth/me')) {
      return initialUser
        ? response(200, { user: initialUser })
        : response(401, { detail: 'Authentication required.' });
    }
    if (url.endsWith('/auth/login')) {
      return response(200, { user: loginFrom || user() });
    }
    if (url.endsWith('/published/symbols')) return response(200, { items: [] });
    throw new Error(`Unexpected request: ${method} ${url}`);
  };
  return { fetchImpl, requests };
}

const selectionChallenge = {
  token: 'challenge-token',
  expiresAt: '2026-08-28T18:00:00Z',
  choices: [{ organizationId: 'org-1', code: 'ORG1', displayName: 'Organization 1' }],
  page: 1,
  pageSize: 5,
  total: 1,
  hasMore: false
};

function authFlowFetch({ me = null, login, select, changePin, logout } = {}) {
  const requests = [];
  const resolve = async (configured, fallback) => {
    if (typeof configured === 'function') return configured();
    return configured || fallback;
  };
  const fetchImpl = async (url, options = {}) => {
    const method = options.method || 'GET';
    requests.push({ url, method, body: options.body });
    if (url.endsWith('/auth/me')) {
      return me ? response(200, { user: me }) : response(401, { detail: 'Authentication required.' });
    }
    if (url.endsWith('/auth/login')) return resolve(login, response(200, { user: user() }));
    if (url.endsWith('/auth/select-organization')) {
      return resolve(select, response(200, { user: user({ mode: 'organization' }) }));
    }
    if (url.endsWith('/auth/change-pin')) return resolve(changePin, response(200, { user: user() }));
    if (url.endsWith('/auth/logout')) return resolve(logout, response(200, { ok: true, revoked: true }));
    if (url.endsWith('/published/symbols')) return response(200, { items: [] });
    throw new Error(`Unexpected request: ${method} ${url}`);
  };
  return { fetchImpl, requests };
}

async function submitPinChange(renderer) {
  const form = renderer.root.find((node) => node.type === 'form' && node.props.className === 'submission-form');
  const inputs = form.findAllByType('input');
  await act(async () => {
    inputs[0].props.onChange({ target: { value: '1234' } });
    inputs[1].props.onChange({ target: { value: '5678' } });
    inputs[2].props.onChange({ target: { value: '5678' } });
  });
  await act(async () => {
    await form.props.onSubmit({ preventDefault() {} });
  });
}

function buttonWithText(renderer, text) {
  return renderer.root.find(
    (node) => node.type === 'button' && (
      node.children.join('') === text ||
      (text === 'Select →' && node.props['aria-label'] === 'Select Organization 1')
    )
  );
}

function deferred() {
  let resolve;
  const promise = new Promise((complete) => { resolve = complete; });
  return { promise, resolve };
}

function profilePayload(currentUser = user()) {
  return {
    user: currentUser,
    plan: {
      minimumYears: 1,
      maximumYears: 3,
      annualPricePence: 5000,
      currency: 'GBP',
      upgradeOptions: [{ years: 1, totalPricePence: 5000, expiresOn: '2027-08-28' }]
    }
  };
}

before(async () => {
  originalDocument = globalThis.document;
  originalReact = globalThis.React;
  globalThis.React = React;
  globalThis.document = {
    activeElement: null,
    addEventListener() {},
    removeEventListener() {}
  };
  vite = await createServer({
    configFile: false,
    root: process.cwd(),
    server: { middlewareMode: true, hmr: false },
    appType: 'custom'
  });
  ({ default: App } = await vite.ssrLoadModule('/frontend/src/App.jsx'));
});

beforeEach(() => {
  originalFetch = globalThis.fetch;
});

afterEach(() => {
  globalThis.fetch = originalFetch;
});

after(async () => {
  await vite.close();
  globalThis.document = originalDocument;
  globalThis.React = originalReact;
});

test('logged-out protected navigation carries one semantic pathname, query and fragment to login', async () => {
  const { fetchImpl } = ordinaryLoginFetch();
  const { renderer } = await mount('/standards?symbol=S-000001#detail', fetchImpl);

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/login',
    search: '',
    hash: '',
    state: { from: '/standards?symbol=S-000001#detail' }
  });
  await act(async () => renderer.unmount());
});

test('ordinary login resumes the exact validated destination with one target navigation', async () => {
  const { fetchImpl } = ordinaryLoginFetch();
  const { renderer, locations } = await mount(
    { pathname: '/login', state: { from: '/standards?symbol=S-000001#detail' } },
    fetchImpl
  );

  await submitLogin(renderer);

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/standards',
    search: '?symbol=S-000001',
    hash: '#detail',
    state: null
  });
  assert.equal(
    locations.filter(({ pathname, search, hash }) => `${pathname}${search}${hash}` === '/standards?symbol=S-000001#detail').length,
    1
  );
  await act(async () => renderer.unmount());
});

test('ordinary login rejects malformed, external or auth-normalizing continuation state', async () => {
  for (const from of ['https://evil.example/x', '/bad%2', '/safe/../login']) {
    const { fetchImpl } = ordinaryLoginFetch();
    const { renderer, locations } = await mount({ pathname: '/login', state: { from } }, fetchImpl);

    await submitLogin(renderer);

    assert.equal(currentLocation(renderer).pathname, '/standards');
    assert.equal(currentLocation(renderer).search, '');
    assert.equal(currentLocation(renderer).hash, '');
    assert.equal(locations.filter(({ pathname }) => pathname === '/login').length, 1);
    await act(async () => renderer.unmount());
  }
});

test('direct authenticated login visit ignores stale continuation state and cannot loop', async () => {
  const { fetchImpl } = ordinaryLoginFetch({ initialUser: user() });
  const { renderer, locations } = await mount(
    { pathname: '/login', state: { from: '/support?stale=1#old' } },
    fetchImpl
  );

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/standards',
    search: '',
    hash: '',
    state: null
  });
  assert.equal(locations.filter(({ pathname }) => pathname === '/login').length, 1);
  await act(async () => renderer.unmount());
});

test('failed login stays on login and retains safe retry state without exposing it in the error', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({ login: response(503, { detail: 'Login service unavailable.' }) });
  const { renderer } = await mount({ pathname: '/login', state: { from: destination } }, fetchImpl);

  await submitLogin(renderer);

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/login', search: '', hash: '', state: { from: destination }
  });
  const alert = renderer.root.findByProps({ role: 'alert' }).children.join('');
  assert.match(alert, /Login service unavailable/);
  assert.doesNotMatch(alert, /S-000001|standards|detail/);
  await act(async () => renderer.unmount());
});

test('organization challenge preserves the validated destination', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({ login: response(200, { user: null, selectionChallenge }) });
  const { renderer } = await mount({ pathname: '/login', state: { from: destination } }, fetchImpl);

  await submitLogin(renderer);

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/select-organization', search: '', hash: '', state: { from: destination }
  });
  await act(async () => renderer.unmount());
});

test('successful organization selection resumes the destination', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({
    login: response(200, { user: null, selectionChallenge }),
    select: response(200, { user: user({ mode: 'organization' }) })
  });
  const { renderer } = await mount({ pathname: '/login', state: { from: destination } }, fetchImpl);
  await submitLogin(renderer);

  await act(async () => {
    await buttonWithText(renderer, 'Select →').props.onClick();
  });

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/standards', search: '?symbol=S-000001', hash: '#detail', state: null
  });
  await act(async () => renderer.unmount());
});

test('retryable organization selection failure retains challenge and destination', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({
    login: response(200, { user: null, selectionChallenge }),
    select: response(503, { detail: 'Selection service unavailable.' })
  });
  const { renderer } = await mount({ pathname: '/login', state: { from: destination } }, fetchImpl);
  await submitLogin(renderer);

  await act(async () => {
    await buttonWithText(renderer, 'Select →').props.onClick();
  });

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/select-organization', search: '', hash: '', state: { from: destination }
  });
  assert.match(renderer.root.findByProps({ role: 'alert' }).children.join(''), /Selection service unavailable/);
  assert.equal(renderer.root.findAllByProps({ 'aria-label': 'Select Organization 1' }).length, 1);
  await act(async () => renderer.unmount());
});

test('terminal organization challenge loss returns to stateless sign-in', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({
    login: response(200, { user: null, selectionChallenge }),
    select: response(401, { detail: 'Organization selection challenge is invalid or unavailable.' })
  });
  const { renderer } = await mount({ pathname: '/login', state: { from: destination } }, fetchImpl);
  await submitLogin(renderer);

  await act(async () => {
    await buttonWithText(renderer, 'Select →').props.onClick();
  });

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/login', search: '', hash: '', state: null
  });
  await act(async () => renderer.unmount());
});

test('mandatory PIN login forwards and successful PIN change resumes the destination', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({ login: response(200, { user: user({ mustChangePin: true }) }) });
  const { renderer } = await mount({ pathname: '/login', state: { from: destination } }, fetchImpl);

  await submitLogin(renderer);
  assert.deepEqual(currentLocation(renderer), {
    pathname: '/change-pin', search: '', hash: '', state: { from: destination }
  });

  await submitPinChange(renderer);
  assert.deepEqual(currentLocation(renderer), {
    pathname: '/standards', search: '?symbol=S-000001', hash: '#detail', state: null
  });
  await act(async () => renderer.unmount());
});

test('organization selection forwards the same destination through mandatory PIN change', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({
    login: response(200, { user: null, selectionChallenge }),
    select: response(200, { user: user({ mustChangePin: true, mode: 'organization' }) })
  });
  const { renderer } = await mount({ pathname: '/login', state: { from: destination } }, fetchImpl);
  await submitLogin(renderer);

  await act(async () => {
    await buttonWithText(renderer, 'Select →').props.onClick();
  });
  assert.deepEqual(currentLocation(renderer), {
    pathname: '/change-pin', search: '', hash: '', state: { from: destination }
  });

  await submitPinChange(renderer);
  assert.deepEqual(currentLocation(renderer), {
    pathname: '/standards', search: '?symbol=S-000001', hash: '#detail', state: null
  });
  await act(async () => renderer.unmount());
});

test('missing or unsafe PIN continuation falls back to standards', async () => {
  for (const state of [undefined, { from: 'https://evil.example/x' }]) {
    const { fetchImpl } = authFlowFetch({ me: user({ mustChangePin: true }) });
    const { renderer } = await mount({ pathname: '/change-pin', state }, fetchImpl);

    await submitPinChange(renderer);

    assert.deepEqual(currentLocation(renderer), {
      pathname: '/standards', search: '', hash: '', state: null
    });
    await act(async () => renderer.unmount());
  }
});

test('failed PIN change stays on the page with safe retry state', async () => {
  const destination = '/standards?symbol=S-000001#detail';
  const { fetchImpl } = authFlowFetch({
    me: user({ mustChangePin: true }),
    changePin: response(400, { detail: 'Current PIN is incorrect.' })
  });
  const { renderer } = await mount({ pathname: '/change-pin', state: { from: destination } }, fetchImpl);

  await submitPinChange(renderer);

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/change-pin', search: '', hash: '', state: { from: destination }
  });
  assert.match(JSON.stringify(renderer.toJSON()), /Current PIN is incorrect/);
  await act(async () => renderer.unmount());
});

test('successful logout reaches login without continuation state', async () => {
  const { fetchImpl } = authFlowFetch({ me: user() });
  const { renderer } = await mount('/standards?symbol=S-000001#detail', fetchImpl);

  await act(async () => {
    await buttonWithText(renderer, 'Sign out').props.onClick();
  });

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/login', search: '', hash: '', state: null
  });
  await act(async () => renderer.unmount());
});

test('failed logout preserves the session and current route', async () => {
  const { fetchImpl } = authFlowFetch({
    me: user(),
    logout: response(503, { detail: 'Logout service unavailable.' })
  });
  const { renderer } = await mount('/standards?symbol=S-000001#detail', fetchImpl);

  await act(async () => {
    await buttonWithText(renderer, 'Sign out').props.onClick();
  });

  assert.deepEqual(currentLocation(renderer), {
    pathname: '/standards', search: '?symbol=S-000001', hash: '#detail', state: null
  });
  assert.match(renderer.root.findByProps({ role: 'alert' }).children.join(''), /Logout service unavailable/);
  assert.equal(buttonWithText(renderer, 'Sign out').type, 'button');
  await act(async () => renderer.unmount());
});

test('a stale authenticated refresh cannot restore the session after successful logout', async () => {
  const staleRefresh = deferred();
  const currentUser = user();
  let authMeCalls = 0;
  const fetchImpl = async (url, options = {}) => {
    const method = options.method || 'GET';
    if (url.endsWith('/auth/me')) {
      authMeCalls += 1;
      return authMeCalls === 1
        ? response(200, { user: currentUser })
        : staleRefresh.promise;
    }
    if (url.endsWith('/profile') && method === 'GET') return response(200, profilePayload(currentUser));
    if (url.endsWith('/profile/contributions') && method === 'GET') {
      return response(200, { acceptedContributionCount: 0, reversedContributionCount: 0 });
    }
    if (url.endsWith('/profile/subscription/upgrade') && method === 'POST') {
      return response(200, profilePayload({
        ...currentUser,
        subscription: { tier: 'plus', status: 'active', isActive: true }
      }));
    }
    if (url.endsWith('/auth/logout')) return response(200, { ok: true, revoked: true });
    if (url.endsWith('/published/symbols')) return response(200, { items: [] });
    throw new Error(`Unexpected request: ${method} ${url}`);
  };
  const { renderer } = await mount('/profile', fetchImpl);

  await act(async () => buttonWithText(renderer, 'Review upgrade').props.onClick());
  let upgradePromise;
  await act(async () => {
    upgradePromise = buttonWithText(renderer, 'Confirm upgrade').props.onClick();
    await Promise.resolve();
  });
  assert.equal(authMeCalls, 2);

  await act(async () => {
    await buttonWithText(renderer, 'Sign out').props.onClick();
  });
  assert.deepEqual(currentLocation(renderer), {
    pathname: '/login', search: '', hash: '', state: null
  });

  await act(async () => {
    staleRefresh.resolve(response(200, { user: currentUser }));
    await upgradePromise;
  });
  assert.deepEqual(currentLocation(renderer), {
    pathname: '/login', search: '', hash: '', state: null
  });
  assert.equal(
    renderer.root.findAll((node) => node.type === 'button' && node.children.join('') === 'Sign out').length,
    0
  );
  await act(async () => renderer.unmount());
});

test('a stale unauthenticated refresh cannot overwrite a newer successful login', async () => {
  const staleRefresh = deferred();
  const currentUser = user();
  let authMeCalls = 0;
  const fetchImpl = async (url, options = {}) => {
    const method = options.method || 'GET';
    if (url.endsWith('/auth/me')) {
      authMeCalls += 1;
      return authMeCalls === 1
        ? response(200, { user: currentUser })
        : staleRefresh.promise;
    }
    if (url.endsWith('/profile') && method === 'GET') return response(200, profilePayload(currentUser));
    if (url.endsWith('/profile/contributions') && method === 'GET') {
      return response(200, { acceptedContributionCount: 0, reversedContributionCount: 0 });
    }
    if (url.endsWith('/profile/subscription/upgrade') && method === 'POST') {
      return response(200, profilePayload(currentUser));
    }
    if (url.endsWith('/auth/logout')) return response(200, { ok: true, revoked: true });
    if (url.endsWith('/auth/login')) return response(200, { user: currentUser });
    if (url.endsWith('/published/symbols')) return response(200, { items: [] });
    throw new Error(`Unexpected request: ${method} ${url}`);
  };
  const { renderer, locations } = await mount('/profile', fetchImpl);

  await act(async () => buttonWithText(renderer, 'Review upgrade').props.onClick());
  let upgradePromise;
  await act(async () => {
    upgradePromise = buttonWithText(renderer, 'Confirm upgrade').props.onClick();
    await Promise.resolve();
  });
  await act(async () => {
    await buttonWithText(renderer, 'Sign out').props.onClick();
  });
  await submitLogin(renderer);
  assert.equal(currentLocation(renderer).pathname, '/standards');

  await act(async () => {
    staleRefresh.resolve(response(401, { detail: 'Authentication required.' }));
    await upgradePromise;
  });
  assert.deepEqual(currentLocation(renderer), {
    pathname: '/standards', search: '', hash: '', state: null
  });
  assert.equal(locations.filter(({ pathname }) => pathname === '/login').length, 1);
  await act(async () => renderer.unmount());
});
