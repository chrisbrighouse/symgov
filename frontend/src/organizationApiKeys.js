import { createElement, useCallback, useEffect, useState } from 'react';

import {
  claimMyOrganizationApiKey,
  issueOrganizationApiKey,
  listMyOrganizationApiKeys,
  listOrganizationApiKeys,
  listOrganizationMembersForKeys,
  revokeOrganizationApiKey,
} from './api.js';

export const KEY_SCOPES = [
  { value: 'catalog.read', label: 'Search and read the Catalog', defaultOn: true },
  { value: 'catalog.preview', label: 'Previews and thumbnails', defaultOn: true },
  { value: 'catalog.ed.query', label: 'Catalog query interpreter (rule-based)', defaultOn: false },
  { value: 'catalog.feedback.write', label: 'Send feedback', defaultOn: false },
];

export const KEY_STATE_LABELS = {
  pending_claim: 'Waiting for the holder to collect it',
  active: 'Active',
  expired: 'Expired',
  revoked: 'Revoked',
};

const DEFAULT_API = {
  listKeys: listOrganizationApiKeys,
  listMembers: listOrganizationMembersForKeys,
  issueKey: issueOrganizationApiKey,
  revokeKey: revokeOrganizationApiKey,
  listMyKeys: listMyOrganizationApiKeys,
  claimKey: claimMyOrganizationApiKey,
};

export function emptyKeyForm() {
  return {
    holderUserId: '',
    integrationName: '',
    scopes: KEY_SCOPES.filter((scope) => scope.defaultOn).map((scope) => scope.value),
    rateLimitPerMinute: '',
    allowedOrigins: '',
  };
}

// The request the API takes, or `{ error }` saying what to fix first.
export function issueKeyRequest(form, { maxRateLimit = 600 } = {}) {
  const integrationName = String(form.integrationName || '').trim();
  if (!form.holderUserId) return { error: 'Choose who will hold the key.' };
  if (!integrationName) return { error: 'Name what the key is for.' };
  if (!form.scopes?.length) return { error: 'Choose at least one thing the key may do.' };
  const request = { holderUserId: form.holderUserId, integrationName, scopes: [...form.scopes] };
  const limit = String(form.rateLimitPerMinute ?? '').trim();
  if (limit) {
    const value = Number(limit);
    if (!Number.isInteger(value) || value < 1 || value > maxRateLimit) {
      return { error: `The rate limit must be a whole number from 1 to ${maxRateLimit}.` };
    }
    request.rateLimitPerMinute = value;
  }
  const origins = String(form.allowedOrigins || '').split(/[\s,]+/).map((origin) => origin.trim()).filter(Boolean);
  if (origins.length) request.allowedOrigins = origins;
  return { request };
}

function describeError(error, fallback) {
  const message = error?.message;
  return typeof message === 'string' && message ? message : fallback;
}

function formatWhen(value) {
  if (!value) return 'never';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? 'never' : date.toISOString().replace('T', ' ').slice(0, 16) + ' UTC';
}

// `protect` runs a change under the admin page's PIN step-up.
export function OrganizationApiKeysPanel({ isAdmin, api = DEFAULT_API, protect = (operation) => operation() }) {
  const [state, setState] = useState({ loading: true, error: '', keys: [], members: [], defaults: {} });
  const [form, setForm] = useState(emptyKeyForm);
  const [status, setStatus] = useState({ mode: '', message: '' });
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [keys, members] = await Promise.all([api.listKeys(), api.listMembers()]);
      setState({ loading: false, error: '', keys: keys.items, defaults: keys.defaults || {}, members });
    } catch (error) {
      setState((current) => ({ ...current, loading: false, error: describeError(error, 'API keys could not be loaded.') }));
    }
  }, [api]);

  useEffect(() => { refresh(); }, [refresh]);

  const maxRateLimit = state.defaults.maxRateLimitPerMinute || 600;

  async function submit(event) {
    event.preventDefault();
    const built = issueKeyRequest(form, { maxRateLimit });
    if (built.error) {
      setStatus({ mode: 'error', message: built.error });
      return;
    }
    setBusy(true);
    try {
      await protect(() => api.issueKey(built.request));
      setForm(emptyKeyForm());
      setStatus({ mode: 'info', message: 'Key issued. The holder collects it from their profile; it is shown to them once.' });
      await refresh();
    } catch (error) {
      setStatus({ mode: 'error', message: describeError(error, 'The API key could not be issued.') });
    } finally {
      setBusy(false);
    }
  }

  async function revoke(key) {
    if (!window.confirm(`Revoke the “${key.integrationName}” key held by ${key.holderDisplayName}? This cannot be undone.`)) return;
    setBusy(true);
    try {
      await protect(() => api.revokeKey(key.keyId));
      setStatus({ mode: 'info', message: 'Key revoked.' });
      await refresh();
    } catch (error) {
      setStatus({ mode: 'error', message: describeError(error, 'The API key could not be revoked.') });
    } finally {
      setBusy(false);
    }
  }

  const activeMembers = state.members.filter((member) => member.status === 'active' && member.userIsActive !== false);

  return createElement(
    'section',
    { className: 'glass-panel pane organization-admin-section', 'aria-labelledby': 'organization-api-keys-heading' },
    createElement('h2', { id: 'organization-api-keys-heading' }, 'Integrations'),
    createElement('p', { className: 'title-support' },
      'API keys let a member’s software read the Catalog for this organization. Each key belongs to one member and stops working, for good, if that member leaves or the organization is suspended.'),
    state.loading ? createElement('p', { role: 'status' }, 'Loading API keys…') : null,
    state.error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, state.error) : null,
    status.message ? createElement('p', { role: status.mode === 'error' ? 'alert' : 'status', className: `set-admin-status ${status.mode}` }, status.message) : null,
    !state.loading && !state.error && state.keys.length === 0
      ? createElement('p', { role: 'status', className: 'set-admin-muted' }, 'No API keys have been issued.')
      : null,
    state.keys.length
      ? createElement('ul', { className: 'set-admin-list' },
        state.keys.map((key) => createElement('li', { key: key.keyId, className: 'set-admin-item' },
          createElement('div', null,
            createElement('strong', null, key.integrationName),
            createElement('p', { className: 'set-admin-muted' }, `Held by ${key.holderDisplayName} (${key.holderEmail})`),
            createElement('p', { className: 'set-admin-muted' }, `${KEY_STATE_LABELS[key.state] || key.state}${key.keyPrefix ? ` · ${key.keyPrefix}…` : ''}`),
            createElement('p', { className: 'set-admin-muted' },
              `Limit ${key.rateLimitPerMinute || state.defaults.rateLimitPerMinute || '—'} requests a minute · last used ${formatWhen(key.lastUsedAt)}`),
            key.allowedOrigins?.length
              ? createElement('p', { className: 'set-admin-muted' }, `Browser origins: ${key.allowedOrigins.join(', ')}`)
              : null,
          ),
          isAdmin && key.state !== 'revoked'
            ? createElement('button', {
              type: 'button',
              className: 'action-button danger',
              disabled: busy,
              onClick: () => { revoke(key); },
              'aria-label': `Revoke API key ${key.integrationName} held by ${key.holderDisplayName}`,
            }, 'Revoke')
            : null,
        )))
      : null,
    isAdmin
      ? createElement('form', { className: 'set-admin-form', onSubmit: submit, 'aria-label': 'Issue an API key' },
        createElement('h3', null, 'Issue a key'),
        createElement('label', { htmlFor: 'api-key-holder' }, 'Held by',
          createElement('select', {
            id: 'api-key-holder', value: form.holderUserId, required: true,
            onChange: (event) => setForm({ ...form, holderUserId: event.target.value }),
          },
          createElement('option', { value: '' }, 'Choose a member'),
          activeMembers.map((member) => createElement('option', { key: member.userId, value: member.userId }, `${member.displayName} (${member.email})`)))),
        createElement('label', { htmlFor: 'api-key-name' }, 'What it is for',
          createElement('input', {
            id: 'api-key-name', type: 'text', maxLength: 120, required: true, value: form.integrationName,
            onChange: (event) => setForm({ ...form, integrationName: event.target.value }),
          })),
        createElement('fieldset', null,
          createElement('legend', null, 'The key may'),
          KEY_SCOPES.map((scope) => createElement('label', { key: scope.value, className: 'remember-row' },
            createElement('input', {
              type: 'checkbox', checked: form.scopes.includes(scope.value),
              onChange: (event) => setForm({
                ...form,
                scopes: event.target.checked ? [...form.scopes, scope.value] : form.scopes.filter((value) => value !== scope.value),
              }),
            }),
            ` ${scope.label}`))),
        createElement('label', { htmlFor: 'api-key-limit' }, `Requests a minute (optional, up to ${maxRateLimit}; default ${state.defaults.rateLimitPerMinute || 120})`,
          createElement('input', {
            id: 'api-key-limit', type: 'number', min: 1, max: maxRateLimit, value: form.rateLimitPerMinute,
            onChange: (event) => setForm({ ...form, rateLimitPerMinute: event.target.value }),
          })),
        createElement('label', { htmlFor: 'api-key-origins' }, 'Browser origins allowed to use it (optional, one per line)',
          createElement('textarea', {
            id: 'api-key-origins', rows: 2, value: form.allowedOrigins, placeholder: 'https://tools.example.com',
            onChange: (event) => setForm({ ...form, allowedOrigins: event.target.value }),
          })),
        createElement('button', { type: 'submit', className: 'action-button', disabled: busy }, busy ? 'Working…' : 'Issue key'))
      : null,
  );
}

export function MyApiKeysCard({ api = DEFAULT_API }) {
  const [keys, setKeys] = useState(null);
  const [error, setError] = useState('');
  const [busyId, setBusyId] = useState('');
  const [collected, setCollected] = useState(null);

  const refresh = useCallback(async () => {
    try {
      const result = await api.listMyKeys();
      setKeys(result.items);
    } catch (err) {
      setError(describeError(err, 'Your API keys could not be loaded.'));
      setKeys([]);
    }
  }, [api]);

  useEffect(() => { refresh(); }, [refresh]);

  async function claim(key) {
    setBusyId(key.keyId);
    setError('');
    try {
      const result = await api.claimKey(key.keyId);
      // Held in this component only: closing the card or leaving the page loses it.
      setCollected({ keyId: key.keyId, name: key.integrationName, rawKey: result.rawKey });
      await refresh();
    } catch (err) {
      setError(describeError(err, 'The API key could not be claimed.'));
    } finally {
      setBusyId('');
    }
  }

  if (keys === null || (keys.length === 0 && !error)) return null;

  return createElement(
    'article',
    { className: 'profile-card', 'aria-labelledby': 'my-api-keys-heading' },
    createElement('p', { className: 'eyebrow' }, 'Integrations'),
    createElement('h3', { id: 'my-api-keys-heading' }, 'Your organization API keys'),
    error ? createElement('p', { className: 'form-message error', role: 'alert' }, error) : null,
    collected
      ? createElement('div', { className: 'profile-confirmation', role: 'group', 'aria-label': 'Your new API key' },
        createElement('p', null, `Your “${collected.name}” key. Copy it now: it is shown once and cannot be shown again.`),
        createElement('code', { 'data-testid': 'collected-api-key' }, collected.rawKey),
        createElement('div', { className: 'action-stack horizontal' },
          createElement('button', { type: 'button', className: 'ghost-button', onClick: () => setCollected(null) }, 'I have saved it')))
      : null,
    createElement('ul', { className: 'set-admin-list' },
      keys.map((key) => createElement('li', { key: key.keyId, className: 'set-admin-item' },
        createElement('div', null,
          createElement('strong', null, key.integrationName),
          createElement('p', { className: 'set-admin-muted' }, `${key.organizationName || 'Organization'} · ${KEY_STATE_LABELS[key.state] || key.state}${key.keyPrefix ? ` · ${key.keyPrefix}…` : ''}`),
        ),
        key.state === 'pending_claim'
          ? createElement('button', {
            type: 'button', className: 'action-button', disabled: busyId === key.keyId,
            onClick: () => { claim(key); },
            'aria-label': `Collect API key ${key.integrationName}`,
          }, busyId === key.keyId ? 'Collecting…' : 'Collect key')
          : null,
      ))),
  );
}
