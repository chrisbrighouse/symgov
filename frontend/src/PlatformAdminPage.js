import { createElement, useCallback, useEffect, useState } from 'react';

const API_BASE = '/api/v1';

async function apiGet(path) {
  const r = await fetch(`${API_BASE}${path}`, { credentials: 'include' });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

async function apiPost(path, body) {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.json();
}

async function apiDelete(path) {
  const r = await fetch(`${API_BASE}${path}`, {
    method: 'DELETE',
    credentials: 'include',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
}

function ErrorMessage({ message }) {
  if (!message) return null;
  return createElement(
    'p',
    {
      role: 'alert',
      style: {
        color: '#dc2626',
        background: '#fee2e2',
        border: '1px solid #fca5a5',
        borderRadius: '6px',
        padding: '8px 12px',
        marginBottom: '12px',
        fontSize: '0.875rem',
      },
    },
    message
  );
}

function AdminRow({ admin, onRevoke }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function handleRevoke() {
    if (!window.confirm(`Revoke platform admin access for ${admin.displayName}?`)) return;
    setBusy(true);
    setError('');
    try {
      await onRevoke(admin.userId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return createElement(
    'li',
    {
      style: {
        display: 'flex',
        flexWrap: 'wrap',
        alignItems: 'center',
        gap: '8px',
        padding: '10px 0',
        borderBottom: '1px solid #e5e7eb',
      },
    },
    createElement(
      'div',
      { style: { flex: '1 1 200px' } },
      createElement('strong', null, admin.displayName),
      createElement('br', null),
      createElement('small', { style: { color: '#6b7280' } }, admin.email)
    ),
    !admin.userIsActive
      ? createElement(
          'span',
          { style: { fontSize: '0.75rem', background: '#fee2e2', color: '#991b1b', padding: '2px 8px', borderRadius: '9999px' } },
          'inactive user'
        )
      : null,
    error ? createElement('span', { style: { color: '#dc2626', fontSize: '0.8rem', width: '100%' } }, error) : null,
    createElement(
      'button',
      {
        onClick: handleRevoke,
        disabled: busy,
        style: { fontSize: '0.8rem', color: '#dc2626' },
        'aria-label': `Revoke platform admin for ${admin.displayName}`,
      },
      'Revoke'
    )
  );
}

function GrantAdminForm({ onGrant }) {
  const [userId, setUserId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      await onGrant(userId);
      setUserId('');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return createElement(
    'form',
    { onSubmit: handleSubmit, style: { display: 'flex', gap: '8px', alignItems: 'flex-end', marginBottom: '16px' } },
    createElement(
      'label',
      { htmlFor: 'platform-admin-user-id' },
      'User ID',
      createElement('input', {
        id: 'platform-admin-user-id',
        type: 'text',
        value: userId,
        onChange: (e) => setUserId(e.target.value),
        required: true,
        style: { display: 'block', marginTop: '4px' },
      })
    ),
    createElement('button', { type: 'submit', disabled: saving || !userId }, saving ? 'Granting…' : 'Grant platform admin'),
    error ? ErrorMessage({ message: error }) : null
  );
}

export function PlatformAdminPage() {
  const [admins, setAdmins] = useState(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const PAGE_SIZE = 50;

  const load = useCallback(async (p) => {
    setLoading(true);
    setError('');
    try {
      const data = await apiGet(`/platform/admins?page=${p}&pageSize=${PAGE_SIZE}`);
      setAdmins(data.items);
      setTotal(data.total);
      setPage(p);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(1); }, [load]);

  async function handleGrant(userId) {
    await apiPost('/platform/admins', { userId });
    await load(page);
  }

  async function handleRevoke(userId) {
    await apiDelete(`/platform/admins/${userId}`);
    await load(page);
  }

  if (error && !admins) {
    return createElement(
      'main',
      { style: { padding: '24px' } },
      createElement('h1', null, 'Platform administration'),
      ErrorMessage({ message: error })
    );
  }

  if (!admins) {
    return createElement('main', { style: { padding: '24px' } }, createElement('p', null, 'Loading…'));
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return createElement(
    'main',
    { style: { padding: '24px', maxWidth: '800px' } },
    createElement('h1', { style: { marginBottom: '24px' } }, 'Platform administration'),
    GrantAdminForm({ onGrant: handleGrant }),
    ErrorMessage({ message: error }),
    createElement(
      'section',
      { 'aria-labelledby': 'platform-admins-heading' },
      createElement('h2', { id: 'platform-admins-heading', style: { marginBottom: '16px' } }, `Platform admins (${total})`),
      loading && createElement('p', null, 'Loading…'),
      createElement(
        'ul',
        { style: { listStyle: 'none', padding: 0, margin: 0 } },
        admins.map((a) => createElement(AdminRow, { key: a.userId, admin: a, onRevoke: handleRevoke }))
      ),
      total === 0 && createElement('p', { style: { color: '#6b7280' } }, 'No platform admins found.'),
      totalPages > 1
        ? createElement(
            'nav',
            { 'aria-label': 'Platform admin list pagination', style: { display: 'flex', gap: '8px', marginTop: '16px', alignItems: 'center' } },
            createElement('button', { onClick: () => load(page - 1), disabled: page <= 1 || loading }, 'Previous'),
            createElement('span', null, `Page ${page} of ${totalPages}`),
            createElement('button', { onClick: () => load(page + 1), disabled: page >= totalPages || loading }, 'Next')
          )
        : null
    )
  );
}
