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

function OrganizationRow({ organization, onSuspend, onReactivate }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const suspended = organization.entitlementStatus === 'suspended';

  async function handleToggle() {
    const action = suspended ? onReactivate : onSuspend;
    const verb = suspended ? 'Reactivate' : 'Suspend';
    if (!window.confirm(`${verb} organization ${organization.displayName}?`)) return;
    setBusy(true);
    setError('');
    try {
      await action(organization.id);
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
      createElement('strong', null, organization.displayName),
      createElement('br', null),
      createElement('small', { style: { color: '#6b7280' } }, organization.code)
    ),
    createElement(
      'span',
      {
        style: {
          fontSize: '0.75rem',
          background: suspended ? '#fee2e2' : '#dcfce7',
          color: suspended ? '#991b1b' : '#166534',
          padding: '2px 8px',
          borderRadius: '9999px',
        },
      },
      organization.entitlementStatus
    ),
    organization.isProtected
      ? createElement(
          'span',
          { style: { fontSize: '0.75rem', color: '#6b7280' } },
          'protected'
        )
      : null,
    error ? createElement('span', { style: { color: '#dc2626', fontSize: '0.8rem', width: '100%' } }, error) : null,
    organization.isProtected
      ? null
      : createElement(
          'button',
          {
            onClick: handleToggle,
            disabled: busy,
            style: { fontSize: '0.8rem', color: suspended ? '#166534' : '#dc2626' },
            'aria-label': `${suspended ? 'Reactivate' : 'Suspend'} organization ${organization.displayName}`,
          },
          suspended ? 'Reactivate' : 'Suspend'
        )
  );
}

function CreateOrganizationForm({ onCreate }) {
  const [code, setCode] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [initialAdminUserId, setInitialAdminUserId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      await onCreate({ code, displayName, initialAdminUserId });
      setCode('');
      setDisplayName('');
      setInitialAdminUserId('');
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return createElement(
    'form',
    { onSubmit: handleSubmit, style: { display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end', marginBottom: '16px' } },
    createElement(
      'label',
      { htmlFor: 'new-org-code' },
      'Code',
      createElement('input', {
        id: 'new-org-code',
        type: 'text',
        value: code,
        onChange: (e) => setCode(e.target.value),
        required: true,
        style: { display: 'block', marginTop: '4px' },
      })
    ),
    createElement(
      'label',
      { htmlFor: 'new-org-display-name' },
      'Display name',
      createElement('input', {
        id: 'new-org-display-name',
        type: 'text',
        value: displayName,
        onChange: (e) => setDisplayName(e.target.value),
        required: true,
        style: { display: 'block', marginTop: '4px' },
      })
    ),
    createElement(
      'label',
      { htmlFor: 'new-org-initial-admin' },
      'Initial admin user ID',
      createElement('input', {
        id: 'new-org-initial-admin',
        type: 'text',
        value: initialAdminUserId,
        onChange: (e) => setInitialAdminUserId(e.target.value),
        required: true,
        style: { display: 'block', marginTop: '4px' },
      })
    ),
    createElement(
      'button',
      { type: 'submit', disabled: saving || !code || !displayName || !initialAdminUserId },
      saving ? 'Creating…' : 'Create organization'
    ),
    error ? ErrorMessage({ message: error }) : null
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

  const [organizations, setOrganizations] = useState(null);
  const [orgTotal, setOrgTotal] = useState(0);
  const [orgPage, setOrgPage] = useState(1);
  const [orgLoading, setOrgLoading] = useState(false);
  const [orgError, setOrgError] = useState('');
  const ORG_PAGE_SIZE = 50;

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

  const loadOrganizations = useCallback(async (p) => {
    setOrgLoading(true);
    setOrgError('');
    try {
      const data = await apiGet(`/platform/organizations?page=${p}&pageSize=${ORG_PAGE_SIZE}`);
      setOrganizations(data.items);
      setOrgTotal(data.total);
      setOrgPage(p);
    } catch (err) {
      setOrgError(err.message);
    } finally {
      setOrgLoading(false);
    }
  }, []);

  useEffect(() => { load(1); }, [load]);
  useEffect(() => { loadOrganizations(1); }, [loadOrganizations]);

  async function handleGrant(userId) {
    await apiPost('/platform/admins', { userId });
    await load(page);
  }

  async function handleRevoke(userId) {
    await apiDelete(`/platform/admins/${userId}`);
    await load(page);
  }

  async function handleCreateOrganization({ code, displayName, initialAdminUserId }) {
    await apiPost('/platform/organizations', { code, displayName, initialAdminUserId });
    await loadOrganizations(orgPage);
  }

  async function handleSuspendOrganization(organizationId) {
    await apiPost(`/platform/organizations/${organizationId}/suspend`);
    await loadOrganizations(orgPage);
  }

  async function handleReactivateOrganization(organizationId) {
    await apiPost(`/platform/organizations/${organizationId}/reactivate`);
    await loadOrganizations(orgPage);
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
  const orgTotalPages = Math.ceil(orgTotal / ORG_PAGE_SIZE);

  return createElement(
    'main',
    { style: { padding: '24px', maxWidth: '800px' } },
    createElement('h1', { style: { marginBottom: '24px' } }, 'Platform administration'),
    createElement(
      'section',
      { 'aria-labelledby': 'platform-organizations-heading', style: { marginBottom: '32px' } },
      createElement('h2', { id: 'platform-organizations-heading', style: { marginBottom: '16px' } }, `Organizations (${orgTotal})`),
      createElement(CreateOrganizationForm, { onCreate: handleCreateOrganization }),
      ErrorMessage({ message: orgError }),
      orgLoading && createElement('p', null, 'Loading…'),
      organizations
        ? createElement(
            'ul',
            { style: { listStyle: 'none', padding: 0, margin: 0 } },
            organizations.map((o) =>
              createElement(OrganizationRow, {
                key: o.id,
                organization: o,
                onSuspend: handleSuspendOrganization,
                onReactivate: handleReactivateOrganization,
              })
            )
          )
        : null,
      organizations && orgTotal === 0 && createElement('p', { style: { color: '#6b7280' } }, 'No organizations found.'),
      orgTotalPages > 1
        ? createElement(
            'nav',
            { 'aria-label': 'Organization list pagination', style: { display: 'flex', gap: '8px', marginTop: '16px', alignItems: 'center' } },
            createElement('button', { onClick: () => loadOrganizations(orgPage - 1), disabled: orgPage <= 1 || orgLoading }, 'Previous'),
            createElement('span', null, `Page ${orgPage} of ${orgTotalPages}`),
            createElement('button', { onClick: () => loadOrganizations(orgPage + 1), disabled: orgPage >= orgTotalPages || orgLoading }, 'Next')
          )
        : null
    ),
    createElement(GrantAdminForm, { onGrant: handleGrant }),
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
