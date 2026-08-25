import { createElement, useCallback, useEffect, useReducer, useRef, useState } from 'react';
import { runWithStepUp } from './adminJourneys.js';
import { requestJson } from './api.js';
import { canMountProjectContext } from './projectContext.js';
import { ProjectContextBar } from './ProjectContextBar.js';
import { OrganizationProjectsPanel } from './OrganizationProjectsPanel.js';
import { OrganizationSymbolSetsPanel } from './OrganizationSymbolSetsPanel.js';

function resultValue(result) {
  if (!result.ok) {
    const error = new Error(result.message);
    error.status = result.status;
    throw error;
  }
  return result.payload;
}

async function apiGet(path) {
  return resultValue(await requestJson(path, { cache: 'no-store' }));
}

async function apiPatch(path, body) {
  return resultValue(await requestJson(path, {
    method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

async function apiPost(path, body) {
  return resultValue(await requestJson(path, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

async function apiDelete(path) {
  resultValue(await requestJson(path, {
    method: 'DELETE', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  }));
}

async function apiDeleteJson(path) {
  return resultValue(await requestJson(path, {
    method: 'DELETE', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  }));
}


export function addExistingOrganizationMember({ userId, baseRole, protect }) {
  return protect(() => apiPost('/org/me/members', { userId, baseRole }));
}

function StatusBadge({ status }) {
  const styles = {
    active: { background: '#d1fae5', color: '#065f46' },
    inactive: { background: '#fee2e2', color: '#991b1b' },
    invited: { background: '#e0f2fe', color: '#075985' },
  };
  const s = styles[status] || { background: '#f3f4f6', color: '#374151' };
  return createElement(
    'span',
    {
      style: {
        ...s,
        fontSize: '0.75rem',
        padding: '2px 8px',
        borderRadius: '9999px',
        fontWeight: 600,
        textTransform: 'capitalize',
      },
    },
    status
  );
}

function RoleBadge({ role }) {
  const isAdmin = role === 'admin';
  return createElement(
    'span',
    {
      style: {
        background: isAdmin ? '#dbeafe' : '#f3f4f6',
        color: isAdmin ? '#1e40af' : '#374151',
        fontSize: '0.75rem',
        padding: '2px 8px',
        borderRadius: '9999px',
        fontWeight: 600,
        textTransform: 'capitalize',
      },
    },
    role
  );
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

function OrgDetailSection({ org, isAdmin, onUpdate, protect }) {
  const [editing, setEditing] = useState(false);
  const [displayName, setDisplayName] = useState(org.displayName);
  const [legalName, setLegalName] = useState(org.legalName || '');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  async function handleSave(e) {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      const updated = await protect(() => apiPatch('/org/me', {
        displayName: displayName || undefined,
        legalName: legalName || undefined,
      }));
      onUpdate(updated);
      setEditing(false);
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return createElement(
    'section',
    { 'aria-labelledby': 'org-detail-heading', style: { marginBottom: '32px' } },
    createElement('h2', { id: 'org-detail-heading', style: { marginBottom: '16px' } }, 'Organization details'),
    ErrorMessage({ message: error }),
    editing
      ? createElement(
          'form',
          { onSubmit: handleSave, style: { display: 'flex', flexDirection: 'column', gap: '12px', maxWidth: '480px' } },
          createElement(
            'label',
            { htmlFor: 'org-display-name' },
            'Display name',
            createElement('input', {
              id: 'org-display-name',
              type: 'text',
              value: displayName,
              onChange: (e) => setDisplayName(e.target.value),
              required: true,
              style: { display: 'block', width: '100%', marginTop: '4px' },
            })
          ),
          createElement(
            'label',
            { htmlFor: 'org-legal-name' },
            'Legal name',
            createElement('input', {
              id: 'org-legal-name',
              type: 'text',
              value: legalName,
              onChange: (e) => setLegalName(e.target.value),
              style: { display: 'block', width: '100%', marginTop: '4px' },
            })
          ),
          createElement(
            'div',
            { style: { display: 'flex', gap: '8px' } },
            createElement(
              'button',
              { type: 'submit', disabled: saving },
              saving ? 'Saving…' : 'Save changes'
            ),
            createElement(
              'button',
              { type: 'button', onClick: () => setEditing(false) },
              'Cancel'
            )
          )
        )
      : createElement(
          'dl',
          { style: { display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '4px 16px' } },
          createElement('dt', null, 'Code'),
          createElement('dd', null, createElement('code', null, org.code)),
          createElement('dt', null, 'Display name'),
          createElement('dd', null, org.displayName),
          createElement('dt', null, 'Legal name'),
          createElement('dd', null, org.legalName || '—'),
          createElement('dt', null, 'Locale'),
          createElement('dd', null, org.locale),
          createElement('dt', null, 'Status'),
          createElement(
            'dd',
            null,
            StatusBadge({ status: org.isActive ? org.entitlementStatus : 'inactive' })
          ),
          isAdmin && !org.isProtected
            ? createElement(
                'dd',
                { style: { gridColumn: '1/-1', marginTop: '12px' } },
                createElement('button', { onClick: () => setEditing(true) }, 'Edit details')
              )
            : null
        )
  );
}

const ALLOWED_ICON_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const MAX_ICON_BYTES = 512 * 1024;

function OrgIconSection({ org, isAdmin, iconUploadEnabled, onUpdate, protect }) {
  const [file, setFile] = useState(null);
  const [previewUrl, setPreviewUrl] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => () => {
    if (previewUrl) URL.revokeObjectURL(previewUrl);
  }, [previewUrl]);

  function handleFileChange(e) {
    const f = e.target.files[0];
    setError('');
    setFile(null);
    setPreviewUrl(null);
    if (!f) return;
    if (!ALLOWED_ICON_TYPES.has(f.type)) {
      setError('Only PNG, JPEG, and WEBP images are supported.');
      return;
    }
    if (f.size > MAX_ICON_BYTES) {
      setError(`Icon must be under ${MAX_ICON_BYTES / 1024} KB.`);
      return;
    }
    setFile(f);
    setPreviewUrl(URL.createObjectURL(f));
  }

  async function handleUpload(e) {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    setError('');
    try {
      const base64 = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => {
          // readAsDataURL yields "data:<type>;base64,<data>" — take only the data part
          const comma = reader.result.indexOf(',');
          resolve(comma >= 0 ? reader.result.slice(comma + 1) : reader.result);
        };
        reader.onerror = () => reject(new Error('Could not read the selected file.'));
        reader.readAsDataURL(file);
      });
      const updated = await protect(() => apiPost('/org/me/icon', { contentType: file.type, contentBase64: base64 }));
      onUpdate(updated);
      setFile(null);
      setPreviewUrl(null);
    } catch (err) {
      setError(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function handleRemove() {
    if (!window.confirm('Remove the custom icon and revert to the generated fallback?')) return;
    setRemoving(true);
    setError('');
    try {
      const updated = await protect(() => apiDeleteJson('/org/me/icon'));
      onUpdate(updated);
    } catch (err) {
      setError(err.message);
    } finally {
      setRemoving(false);
    }
  }

  const canManage = isAdmin && !org.isProtected && iconUploadEnabled;

  return createElement(
    'section',
    { 'aria-labelledby': 'org-icon-heading', style: { marginBottom: '32px' } },
    createElement('h2', { id: 'org-icon-heading', style: { marginBottom: '16px' } }, 'Organization icon'),
    ErrorMessage({ message: error }),
    createElement(
      'div',
      { style: { display: 'flex', alignItems: 'flex-start', gap: '24px', flexWrap: 'wrap' } },
      createElement(
        'div',
        null,
        createElement('p', { style: { margin: '0 0 8px', fontSize: '0.875rem', color: '#6b7280' } },
          org.hasCustomIcon ? 'Custom icon' : 'Generated fallback icon'
        ),
        org.iconUrl
          ? createElement('img', {
              src: org.iconUrl,
              alt: `${org.displayName} icon`,
              width: 64,
              height: 64,
              style: { borderRadius: '8px', border: '1px solid #e5e7eb', display: 'block' },
            })
          : createElement('div', {
              style: {
                width: '64px', height: '64px', borderRadius: '8px',
                background: '#f3f4f6', border: '1px solid #e5e7eb',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                fontSize: '0.7rem', color: '#9ca3af', textAlign: 'center',
              },
            }, 'Generated'),
        canManage && org.hasCustomIcon
          ? createElement(
              'button',
              {
                onClick: handleRemove,
                disabled: removing,
                style: { marginTop: '8px', fontSize: '0.8rem', color: '#dc2626', display: 'block' },
                'aria-label': 'Remove custom icon',
              },
              removing ? 'Removing…' : 'Remove icon'
            )
          : null
      ),
      canManage
        ? createElement(
            'form',
            { onSubmit: handleUpload, style: { display: 'flex', flexDirection: 'column', gap: '10px' } },
            createElement(
              'label',
              { htmlFor: 'org-icon-file', style: { fontSize: '0.875rem' } },
              'Upload new icon',
              createElement('input', {
                id: 'org-icon-file',
                type: 'file',
                accept: 'image/png,image/jpeg,image/webp',
                onChange: handleFileChange,
                style: { display: 'block', marginTop: '4px' },
              })
            ),
            previewUrl
              ? createElement('img', {
                  src: previewUrl,
                  alt: 'Icon preview',
                  width: 64,
                  height: 64,
                  style: { borderRadius: '8px', border: '1px solid #e5e7eb' },
                })
              : null,
            createElement(
              'button',
              { type: 'submit', disabled: !file || uploading },
              uploading ? 'Uploading…' : 'Upload icon'
            ),
            createElement(
              'p',
              { style: { fontSize: '0.75rem', color: '#9ca3af', margin: 0 } },
              'PNG, JPEG or WEBP · max 512 KB · 32–1024 px per side'
            )
          )
        : null
    )
  );
}

function MemberRow({ member, isAdmin, onRoleChange, onCapabilityChange, onDeactivate }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function handleRoleChange(newRole) {
    setBusy(true);
    setError('');
    try {
      await onRoleChange(member.membershipId, newRole);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleCapability(action, capability) {
    setBusy(true);
    setError('');
    try {
      await onCapabilityChange(member.membershipId, action, capability);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleDeactivate() {
    if (!window.confirm(`Remove ${member.displayName} from this organization?`)) return;
    setBusy(true);
    setError('');
    try {
      await onDeactivate(member.membershipId);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const hasContributor = member.capabilities.some((c) => c.capability === 'contributor');
  const hasReviewer = member.capabilities.some((c) => c.capability === 'symbol_reviewer');

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
      createElement('strong', null, member.displayName),
      createElement('br', null),
      createElement('small', { style: { color: '#6b7280' } }, member.email)
    ),
    createElement('div', { style: { display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap' } },
      StatusBadge({ status: member.status }),
      RoleBadge({ role: member.baseRole }),
      hasContributor && createElement('span', { style: { fontSize: '0.7rem', background: '#fef3c7', color: '#92400e', padding: '2px 6px', borderRadius: '9999px' } }, 'contributor'),
      hasReviewer && createElement('span', { style: { fontSize: '0.7rem', background: '#fef3c7', color: '#92400e', padding: '2px 6px', borderRadius: '9999px' } }, 'reviewer')
    ),
    error ? createElement('span', { style: { color: '#dc2626', fontSize: '0.8rem', width: '100%' } }, error) : null,
    isAdmin && member.status === 'active'
      ? createElement(
          'div',
          { style: { display: 'flex', gap: '6px', flexWrap: 'wrap' } },
          createElement(
            'button',
            {
              onClick: () => handleRoleChange(member.baseRole === 'admin' ? 'user' : 'admin'),
              disabled: busy,
              style: { fontSize: '0.8rem' },
              'aria-label': `${member.baseRole === 'admin' ? 'Demote' : 'Promote'} ${member.displayName}`,
            },
            member.baseRole === 'admin' ? 'Demote' : 'Promote'
          ),
          createElement(
            'button',
            {
              onClick: () => handleCapability(hasContributor ? 'revoke' : 'grant', 'contributor'),
              disabled: busy,
              style: { fontSize: '0.8rem' },
            },
            hasContributor ? '−Contributor' : '+Contributor'
          ),
          createElement(
            'button',
            {
              onClick: () => handleCapability(hasReviewer ? 'revoke' : 'grant', 'symbol_reviewer'),
              disabled: busy,
              style: { fontSize: '0.8rem' },
            },
            hasReviewer ? '−Reviewer' : '+Reviewer'
          ),
          createElement(
            'button',
            {
              onClick: handleDeactivate,
              disabled: busy,
              style: { fontSize: '0.8rem', color: '#dc2626' },
              'aria-label': `Remove ${member.displayName}`,
            },
            'Remove'
          )
        )
      : null
  );
}

export function OrganizationMemberAddForm({ onAdd }) {
  const [userId, setUserId] = useState('');
  const [baseRole, setBaseRole] = useState('user');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const userIdInputRef = useRef(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      await onAdd({ userId, baseRole });
      setUserId('');
      setBaseRole('user');
    } catch (err) {
      setError(err.message);
      queueMicrotask(() => userIdInputRef.current?.focus());
    } finally {
      setSaving(false);
    }
  }

  return createElement(
    'form',
    { onSubmit: handleSubmit, style: { display: 'flex', gap: '8px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '16px' } },
    createElement('label', { htmlFor: 'organization-member-user-id' },
      'Existing user ID',
      createElement('input', {
        id: 'organization-member-user-id', type: 'text', value: userId, required: true,
        ref: userIdInputRef,
        onChange: (event) => setUserId(event.target.value),
        style: { display: 'block', marginTop: '4px' },
      })
    ),
    createElement('label', { htmlFor: 'organization-member-base-role' },
      'Base role',
      createElement('select', {
        id: 'organization-member-base-role', value: baseRole,
        onChange: (event) => setBaseRole(event.target.value),
        style: { display: 'block', marginTop: '4px' },
      },
      createElement('option', { value: 'user' }, 'User'),
      createElement('option', { value: 'admin' }, 'Admin'))
    ),
    createElement('button', { type: 'submit', disabled: saving || !userId }, saving ? 'Adding…' : 'Add member'),
    error ? createElement(ErrorMessage, { message: error }) : null
  );
}

function MemberListSection({ isAdmin, protect }) {
  const [members, setMembers] = useState(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const PAGE_SIZE = 25;

  const load = useCallback(async (p) => {
    setLoading(true);
    setError('');
    try {
      const data = await apiGet(`/org/me/members?page=${p}&pageSize=${PAGE_SIZE}`);
      setMembers(data.items);
      setTotal(data.total);
      setPage(p);
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(1); }, [load]);

  async function handleRoleChange(membershipId, newRole) {
    const updated = await protect(() => apiPatch(`/org/me/members/${membershipId}`, { baseRole: newRole }));
    setMembers((prev) => prev.map((m) => (m.membershipId === membershipId ? updated : m)));
  }

  async function handleCapabilityChange(membershipId, action, capability) {
    const body = action === 'grant' ? { grantCapability: capability } : { revokeCapability: capability };
    const updated = await protect(() => apiPatch(`/org/me/members/${membershipId}`, body));
    setMembers((prev) => prev.map((m) => (m.membershipId === membershipId ? updated : m)));
  }

  async function handleDeactivate(membershipId) {
    await protect(() => apiDelete(`/org/me/members/${membershipId}`));
    await load(page);
  }

  async function handleAdd({ userId, baseRole }) {
    await addExistingOrganizationMember({ userId, baseRole, protect });
    await load(1);
  }

  if (error) return createElement(ErrorMessage, { message: error });
  if (!members) return createElement('p', null, 'Loading members…');

  const totalPages = Math.ceil(total / PAGE_SIZE);

  return createElement(
    'section',
    { 'aria-labelledby': 'members-heading' },
    createElement(
      'h2',
      { id: 'members-heading', style: { marginBottom: '16px' } },
      `Members (${total})`
    ),
    isAdmin ? createElement(OrganizationMemberAddForm, { onAdd: handleAdd }) : null,
    loading && createElement('p', null, 'Loading…'),
    createElement(
      'ul',
      { style: { listStyle: 'none', padding: 0, margin: 0 } },
      members.map((m) =>
        createElement(MemberRow, {
          key: m.membershipId,
          member: m,
          isAdmin,
          onRoleChange: handleRoleChange,
          onCapabilityChange: handleCapabilityChange,
          onDeactivate: handleDeactivate,
        })
      )
    ),
    total === 0 && createElement('p', { style: { color: '#6b7280' } }, 'No members found.'),
    totalPages > 1
      ? createElement(
          'nav',
          { 'aria-label': 'Member list pagination', style: { display: 'flex', gap: '8px', marginTop: '16px', alignItems: 'center' } },
          createElement(
            'button',
            { onClick: () => load(page - 1), disabled: page <= 1 || loading },
            'Previous'
          ),
          createElement('span', null, `Page ${page} of ${totalPages}`),
          createElement(
            'button',
            { onClick: () => load(page + 1), disabled: page >= totalPages || loading },
            'Next'
          )
        )
      : null
  );
}

export function OrganizationAdminPage({ auth }) {
  const [org, setOrg] = useState(null);
  const [error, setError] = useState('');
  const [stepUpPin, setStepUpPin] = useState('');
  const [contextRefreshToken, setContextRefreshToken] = useState(0);

  useEffect(() => {
    apiGet('/org/me')
      .then(setOrg)
      .catch((err) => setError(err.message));
  }, []);

  const isAdmin = auth?.user?.organization?.baseRole === 'admin';
  const protect = useCallback((operation) => runWithStepUp({
    pin: stepUpPin,
    operation,
    reauthenticate: (pin) => auth.reauthenticate({ pin }),
    clearPin: () => setStepUpPin(''),
  }), [auth, stepUpPin]);
  const symbolSetsUiEnabled = canMountProjectContext(auth);
  const notifyContextChange = useCallback(() => {
    setContextRefreshToken((current) => current + 1);
  }, []);

  if (error) {
    return createElement(
      'section',
      { style: { padding: '24px' } },
      createElement('h1', null, 'Organization'),
      ErrorMessage({ message: error })
    );
  }

  if (!org) {
    return createElement('section', { style: { padding: '24px' } }, createElement('p', { role: 'status' }, 'Loading…'));
  }

  return createElement(
    'section',
    { style: { padding: '24px', maxWidth: '800px' } },
    createElement('h1', { style: { marginBottom: '24px' } }, 'Organization administration'),
    isAdmin ? createElement('label', { htmlFor: 'organization-step-up-pin' },
      'PIN for protected changes',
      createElement('input', {
        id: 'organization-step-up-pin', type: 'password', inputMode: 'numeric',
        autoComplete: 'off', value: stepUpPin, maxLength: 4,
        onChange: (event) => setStepUpPin(event.target.value),
      })
    ) : null,
    symbolSetsUiEnabled
      ? createElement(ProjectContextBar, {
          auth,
          refreshToken: contextRefreshToken,
          onContextChanged: notifyContextChange,
        })
      : null,
    createElement(OrgDetailSection, { org, isAdmin, onUpdate: setOrg, protect }),
    createElement(OrgIconSection, {
      org,
      isAdmin,
      iconUploadEnabled: auth?.user?.capabilities?.organizationIconUploadEnabled === true,
      onUpdate: setOrg,
      protect,
    }),
    symbolSetsUiEnabled
      ? createElement(OrganizationProjectsPanel, {
          isAdmin,
          onContextChanged: notifyContextChange,
        })
      : null,
    symbolSetsUiEnabled
      ? createElement(OrganizationSymbolSetsPanel, {
          isAdmin,
          onContextChanged: notifyContextChange,
        })
      : null,
    createElement(MemberListSection, { isAdmin, protect })
  );
}
