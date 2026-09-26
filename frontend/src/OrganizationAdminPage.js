import { createElement, Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { runWithStepUp } from './adminJourneys.js';
import { requestJson } from './api.js';
import { canMountAgentOversight, canMountOrganizationSymbolDrafts, canMountProjectContext } from './projectContext.js';
import { ProjectContextBar } from './ProjectContextBar.js';
import { OrganizationProjectsPanel } from './OrganizationProjectsPanel.js';
import { OrganizationSymbolSetsPanel } from './OrganizationSymbolSetsPanel.js';
import { SymbolSetBuilderPanel } from './SymbolSetBuilderPanel.js';
import { PromotionSubmissionPanel } from './PromotionSubmissionPanel.js';
import { OrgUsageDashboardSection } from './UsageDashboardSection.js';
import { OrgContributionSection } from './ContributionSection.js';
import { OrgAgentFindingsDashboardSection } from './AgentFindingsDashboardSection.js';

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


export function addExistingOrganizationMember({ email, baseRole, protect }) {
  return protect(() => apiPost('/org/me/members', { email: email.trim(), baseRole }));
}

const STATUS_BADGE_MODIFIERS = {
  active: 'is-active',
  inactive: 'is-inactive',
  invited: 'is-invited',
};

function StatusBadge({ status }) {
  const modifier = STATUS_BADGE_MODIFIERS[status] || 'is-neutral';
  return createElement('span', { className: `organization-admin-badge ${modifier}` }, status);
}

function RoleBadge({ role }) {
  const modifier = role === 'admin' ? 'is-admin' : 'is-neutral';
  return createElement('span', { className: `organization-admin-badge ${modifier}` }, role);
}

function ErrorMessage({ message }) {
  if (!message) return null;
  return createElement(
    'p',
    { role: 'alert', className: 'form-message error organization-admin-message' },
    message
  );
}

function HelpText({ children }) {
  return createElement('p', { className: 'organization-admin-help' }, children);
}

/**
 * The usage, contribution and agent-findings sections are shared with the
 * platform page and carry no chrome of their own; give them the page panel so
 * the Activity tab reads like the rest of the surface.
 */
function EmbeddedPanel({ children }) {
  return createElement('div', { className: 'glass-panel pane organization-admin-embedded' }, children);
}

/**
 * Section shell: one heading, an optional one-line explanation, and an
 * optional action slot on the heading row rather than buried in the body.
 */
function AdminSection({ id, title, description, actions, children }) {
  return createElement(
    'section',
    { className: 'glass-panel pane organization-admin-section', 'aria-labelledby': id },
    createElement(
      'div',
      { className: 'detail-heading organization-admin-section-heading' },
      createElement(
        'div',
        null,
        createElement('h2', { id }, title),
        description ? createElement('p', { className: 'title-support' }, description) : null
      ),
      actions || null
    ),
    children
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

  function startEditing() {
    setDisplayName(org.displayName);
    setLegalName(org.legalName || '');
    setError('');
    setEditing(true);
  }

  const canEdit = isAdmin && !org.isProtected;
  const editAction = canEdit && !editing
    ? createElement(
        'button',
        { type: 'button', className: 'action-button', onClick: startEditing },
        'Edit details'
      )
    : null;

  return createElement(
    AdminSection,
    {
      id: 'org-detail-heading',
      title: 'Organization details',
      description: 'The name and identifiers this organization is published under.',
      actions: editAction,
    },
    ErrorMessage({ message: error }),
    isAdmin && org.isProtected
      ? createElement(HelpText, null, 'This organization is protected — its details are managed by the platform team.')
      : null,
    editing
      ? createElement(
          'form',
          { onSubmit: handleSave, className: 'organization-admin-form' },
          createElement(
            'label',
            { htmlFor: 'org-display-name', className: 'field' },
            createElement('span', null, 'Display name'),
            createElement('input', {
              id: 'org-display-name',
              type: 'text',
              value: displayName,
              onChange: (e) => setDisplayName(e.target.value),
              required: true,
            })
          ),
          createElement(
            'label',
            { htmlFor: 'org-legal-name', className: 'field' },
            createElement('span', null, 'Legal name'),
            createElement('input', {
              id: 'org-legal-name',
              type: 'text',
              value: legalName,
              onChange: (e) => setLegalName(e.target.value),
            })
          ),
          createElement(
            'div',
            { className: 'organization-admin-form-actions' },
            createElement(
              'button',
              { type: 'submit', className: 'action-button primary', disabled: saving },
              saving ? 'Saving…' : 'Save changes'
            ),
            createElement(
              'button',
              { type: 'button', className: 'action-button', onClick: () => setEditing(false), disabled: saving },
              'Cancel'
            )
          )
        )
      : createElement(
          'dl',
          { className: 'organization-admin-detail-grid' },
          createElement('dt', null, 'Code'),
          createElement('dd', null, createElement('code', null, org.code)),
          createElement('dt', null, 'Display name'),
          createElement('dd', null, org.displayName),
          createElement('dt', null, 'Legal name'),
          createElement('dd', null, org.legalName || '—'),
          createElement('dt', null, 'Locale'),
          createElement('dd', null, org.locale || '—'),
          createElement('dt', null, 'Status'),
          createElement(
            'dd',
            null,
            StatusBadge({ status: org.isActive ? org.entitlementStatus : 'inactive' })
          )
        )
  );
}

const ALLOWED_ICON_TYPES = new Set(['image/png', 'image/jpeg', 'image/webp']);
const MAX_ICON_BYTES = 512 * 1024;

function describeIconLock({ isAdmin, org, iconUploadEnabled }) {
  if (!isAdmin) return 'Only organization admins can change the icon.';
  if (org.isProtected) return 'This organization is protected — its icon is managed by the platform team.';
  if (!iconUploadEnabled) return 'Icon upload is not enabled for this organization.';
  return '';
}

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
  const lockReason = canManage ? '' : describeIconLock({ isAdmin, org, iconUploadEnabled });

  return createElement(
    AdminSection,
    {
      id: 'org-icon-heading',
      title: 'Organization icon',
      description: 'Shown beside this organization across the product.',
    },
    ErrorMessage({ message: error }),
    createElement(
      'div',
      { className: 'organization-admin-icon-layout' },
      createElement(
        'div',
        { className: 'organization-admin-icon-current' },
        createElement('p', { className: 'eyebrow' }, org.hasCustomIcon ? 'Custom icon' : 'Generated fallback'),
        org.iconUrl
          ? createElement('img', {
              src: org.iconUrl,
              alt: `${org.displayName} icon`,
              width: 64,
              height: 64,
              className: 'organization-admin-icon',
            })
          : createElement(
              'div',
              { className: 'organization-admin-icon is-placeholder', role: 'img', 'aria-label': 'Generated fallback icon' },
              'Generated'
            ),
        canManage && org.hasCustomIcon
          ? createElement(
              'button',
              {
                type: 'button',
                onClick: handleRemove,
                disabled: removing,
                className: 'action-button compact danger organization-admin-icon-remove',
                'aria-label': 'Remove custom icon',
              },
              removing ? 'Removing…' : 'Remove icon'
            )
          : null
      ),
      canManage
        ? createElement(
            'form',
            { onSubmit: handleUpload, className: 'organization-admin-form organization-admin-icon-form' },
            createElement(
              'label',
              { htmlFor: 'org-icon-file', className: 'field' },
              createElement('span', null, 'Upload a new icon'),
              createElement('input', {
                id: 'org-icon-file',
                type: 'file',
                accept: 'image/png,image/jpeg,image/webp',
                onChange: handleFileChange,
                'aria-describedby': 'org-icon-constraints',
              })
            ),
            createElement(
              'p',
              { id: 'org-icon-constraints', className: 'organization-admin-help' },
              'PNG, JPEG or WEBP · max 512 KB · 32–1024 px per side'
            ),
            previewUrl
              ? createElement(
                  'div',
                  { className: 'organization-admin-icon-preview' },
                  createElement('p', { className: 'eyebrow' }, 'Preview'),
                  createElement('img', {
                    src: previewUrl,
                    alt: 'Icon preview',
                    width: 64,
                    height: 64,
                    className: 'organization-admin-icon',
                  })
                )
              : null,
            createElement(
              'div',
              { className: 'organization-admin-form-actions' },
              createElement(
                'button',
                { type: 'submit', className: 'action-button primary', disabled: !file || uploading },
                uploading ? 'Uploading…' : 'Upload icon'
              )
            )
          )
        : createElement(HelpText, null, lockReason)
    )
  );
}

function CapabilityToggle({ member, capability, label, granted, busy, onChange }) {
  const inputId = `member-${member.membershipId}-${capability}`;
  return createElement(
    'label',
    { className: 'checkbox-row compact organization-admin-capability', htmlFor: inputId },
    createElement('input', {
      id: inputId,
      type: 'checkbox',
      checked: granted,
      disabled: busy,
      onChange: () => onChange(granted ? 'revoke' : 'grant', capability),
      'aria-label': `${label} capability for ${member.displayName}`,
    }),
    createElement('span', null, label)
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

  const capabilities = member.capabilities || [];
  const hasContributor = capabilities.some((c) => c.capability === 'contributor');
  const hasReviewer = capabilities.some((c) => c.capability === 'symbol_reviewer');
  const manageable = isAdmin && member.status === 'active';
  const isOrgAdmin = member.baseRole === 'admin';

  const row = createElement(
    'tr',
    { className: 'organization-admin-member-row', 'aria-busy': busy || undefined },
    createElement(
      'td',
      null,
      createElement('strong', { className: 'organization-admin-member-name' }, member.displayName),
      createElement('span', { className: 'organization-admin-member-email' }, member.email)
    ),
    createElement('td', null, StatusBadge({ status: member.status })),
    createElement(
      'td',
      null,
      createElement(
        'div',
        { className: 'organization-admin-cell-stack' },
        RoleBadge({ role: member.baseRole }),
        manageable
          ? createElement(
              'button',
              {
                type: 'button',
                className: 'action-button compact',
                onClick: () => handleRoleChange(isOrgAdmin ? 'user' : 'admin'),
                disabled: busy,
                'aria-label': isOrgAdmin
                  ? `Change ${member.displayName} to member`
                  : `Make ${member.displayName} an organization admin`,
              },
              isOrgAdmin ? 'Make member' : 'Make admin'
            )
          : null
      )
    ),
    createElement(
      'td',
      null,
      manageable
        ? createElement(
            'div',
            { className: 'organization-admin-cell-stack', role: 'group', 'aria-label': `Capabilities for ${member.displayName}` },
            createElement(CapabilityToggle, {
              member, capability: 'contributor', label: 'Contributor',
              granted: hasContributor, busy, onChange: handleCapability,
            }),
            createElement(CapabilityToggle, {
              member, capability: 'symbol_reviewer', label: 'Reviewer',
              granted: hasReviewer, busy, onChange: handleCapability,
            })
          )
        : createElement(
            'div',
            { className: 'organization-admin-cell-stack' },
            hasContributor ? createElement('span', { className: 'organization-admin-badge is-capability' }, 'contributor') : null,
            hasReviewer ? createElement('span', { className: 'organization-admin-badge is-capability' }, 'reviewer') : null,
            !hasContributor && !hasReviewer ? createElement('span', { className: 'muted-text' }, '—') : null
          )
    ),
    isAdmin
      ? createElement(
          'td',
          { className: 'organization-admin-member-actions' },
          manageable
            ? createElement(
                'button',
                {
                  type: 'button',
                  className: 'action-button compact danger',
                  onClick: handleDeactivate,
                  disabled: busy,
                  'aria-label': `Remove ${member.displayName} from this organization`,
                },
                'Remove'
              )
            : createElement('span', { className: 'muted-text' }, '—')
        )
      : null
  );

  if (!error) return row;

  return createElement(
    Fragment,
    null,
    row,
    createElement(
      'tr',
      { className: 'organization-admin-member-error-row' },
      createElement(
        'td',
        { colSpan: isAdmin ? 5 : 4 },
        createElement('p', { role: 'alert', className: 'form-message error' }, error)
      )
    )
  );
}

export function OrganizationMemberAddForm({ onAdd }) {
  const [email, setEmail] = useState('');
  const [baseRole, setBaseRole] = useState('user');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const emailInputRef = useRef(null);

  async function handleSubmit(event) {
    event.preventDefault();
    setSaving(true);
    setError('');
    try {
      await onAdd({ email, baseRole });
      setEmail('');
      setBaseRole('user');
    } catch (err) {
      setError(err.message);
      queueMicrotask(() => emailInputRef.current?.focus());
    } finally {
      setSaving(false);
    }
  }

  // The labels stay direct children of the form: the grid does the layout so
  // that each control keeps its own label association without a wrapper.
  return createElement(
    'form',
    { onSubmit: handleSubmit, className: 'organization-admin-member-add' },
    createElement('p', { className: 'organization-admin-member-add-title' }, 'Add an existing user'),
    createElement('label', { htmlFor: 'organization-member-email', className: 'field' },
      createElement('span', null, 'Email address'),
      createElement('input', {
        id: 'organization-member-email', type: 'email', value: email, required: true,
        autoComplete: 'off',
        ref: emailInputRef,
        placeholder: 'name@example.com',
        'aria-describedby': 'organization-member-email-help',
        onChange: (event) => setEmail(event.target.value),
      })
    ),
    createElement('label', { htmlFor: 'organization-member-base-role', className: 'field' },
      createElement('span', null, 'Base role'),
      createElement('select', {
        id: 'organization-member-base-role', value: baseRole,
        onChange: (event) => setBaseRole(event.target.value),
      },
      createElement('option', { value: 'user' }, 'User'),
      createElement('option', { value: 'admin' }, 'Admin'))
    ),
    createElement('button', {
      type: 'submit',
      className: 'action-button primary organization-admin-member-add-submit',
      disabled: saving || !email.trim(),
    }, saving ? 'Adding…' : 'Add member'),
    createElement('p', { id: 'organization-member-email-help', className: 'organization-admin-help' },
      'The person must already have a Symgov account under this email address.'),
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

  async function handleAdd({ email, baseRole }) {
    await addExistingOrganizationMember({ email, baseRole, protect });
    await load(1);
  }

  if (error) return createElement(ErrorMessage, { message: error });
  if (!members) {
    return createElement(
      AdminSection,
      { id: 'members-heading', title: 'Members' },
      createElement('p', { role: 'status' }, 'Loading members…')
    );
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const columnCount = isAdmin ? 5 : 4;

  return createElement(
    AdminSection,
    {
      id: 'members-heading',
      title: 'Members',
      description: total === 1 ? '1 member' : `${total} members`,
    },
    isAdmin ? createElement(OrganizationMemberAddForm, { onAdd: handleAdd }) : null,
    loading ? createElement('p', { role: 'status', className: 'organization-admin-help' }, 'Loading…') : null,
    createElement(
      'div',
      { className: 'admin-users-table-shell' },
      createElement(
        'table',
        { className: 'organization-members-grid' },
        createElement(
          'thead',
          null,
          createElement(
            'tr',
            null,
            createElement('th', { scope: 'col' }, 'Member'),
            createElement('th', { scope: 'col' }, 'Status'),
            createElement('th', { scope: 'col' }, 'Role'),
            createElement('th', { scope: 'col' }, 'Capabilities'),
            isAdmin ? createElement('th', { scope: 'col' }, 'Actions') : null
          )
        ),
        createElement(
          'tbody',
          null,
          total === 0
            ? createElement(
                'tr',
                null,
                createElement('td', { colSpan: columnCount, className: 'admin-users-empty' }, 'No members found.')
              )
            : members.map((m) =>
                createElement(MemberRow, {
                  key: m.membershipId,
                  member: m,
                  isAdmin,
                  onRoleChange: handleRoleChange,
                  onCapabilityChange: handleCapabilityChange,
                  onDeactivate: handleDeactivate,
                })
              )
        )
      )
    ),
    totalPages > 1
      ? createElement(
          'nav',
          { 'aria-label': 'Member list pagination', className: 'admin-user-pagination' },
          createElement(
            'button',
            { type: 'button', className: 'action-button compact', onClick: () => load(page - 1), disabled: page <= 1 || loading },
            'Previous'
          ),
          createElement('span', { className: 'muted-text' }, `Page ${page} of ${totalPages}`),
          createElement(
            'button',
            { type: 'button', className: 'action-button compact', onClick: () => load(page + 1), disabled: page >= totalPages || loading },
            'Next'
          )
        )
      : null
  );
}

/**
 * Step-up PIN. The PIN is spent on the first protected call that needs it, so
 * it is a page-level control rather than a field on any one form; the panel
 * says what it is for, and says whether one is currently held.
 */
function StepUpPanel({ pin, onPinChange, inputRef, needed }) {
  return createElement(
    'section',
    {
      className: `glass-panel organization-admin-stepup${needed ? ' needs-pin' : ''}`,
      'aria-labelledby': 'organization-step-up-heading',
    },
    createElement(
      'div',
      { className: 'organization-admin-stepup-copy' },
      createElement('h2', { id: 'organization-step-up-heading' }, 'Protected changes'),
      createElement(
        'p',
        { className: 'organization-admin-help' },
        'Renaming the organization, changing a member’s role or capabilities, and removing a member all need your sign-in PIN. Enter it here first — it is used once and then cleared.'
      )
    ),
    createElement(
      'label',
      { htmlFor: 'organization-step-up-pin', className: 'field organization-admin-stepup-field' },
      createElement('span', null, 'PIN for protected changes'),
      createElement('input', {
        id: 'organization-step-up-pin', type: 'password', inputMode: 'numeric',
        autoComplete: 'off', value: pin, maxLength: 4,
        ref: inputRef,
        'aria-describedby': 'organization-step-up-state',
        onChange: (event) => onPinChange(event.target.value),
      })
    ),
    createElement(
      'p',
      {
        id: 'organization-step-up-state',
        role: 'status',
        className: `organization-admin-stepup-state${pin ? ' is-ready' : ''}`,
      },
      pin ? 'PIN entered — protected changes are ready.' : 'No PIN entered.'
    )
  );
}

const ADMIN_TAB_PANEL_ID = 'organization-admin-panel';

function tabId(key) {
  return `organization-admin-tab-${key}`;
}

export function OrganizationAdminPage({ auth }) {
  const [org, setOrg] = useState(null);
  const [error, setError] = useState('');
  const [stepUpPin, setStepUpPin] = useState('');
  const [stepUpNeeded, setStepUpNeeded] = useState(false);
  const [contextRefreshToken, setContextRefreshToken] = useState(0);
  const [activeTab, setActiveTab] = useState('members');
  const stepUpPinRef = useRef(null);

  useEffect(() => {
    apiGet('/org/me')
      .then(setOrg)
      .catch((err) => setError(err.message));
  }, []);

  const isAdmin = auth?.user?.organization?.baseRole === 'admin';
  const protect = useCallback(async (operation) => {
    try {
      const value = await runWithStepUp({
        pin: stepUpPin,
        operation,
        reauthenticate: (pin) => auth.reauthenticate({ pin }),
        clearPin: () => setStepUpPin(''),
      });
      setStepUpNeeded(false);
      return value;
    } catch (err) {
      // runWithStepUp flags the one case the operator can fix here: the change
      // needed a step-up and no PIN was held. Say so in those terms, and put
      // the cursor where the fix is.
      if (err?.requiresStepUp) {
        setStepUpNeeded(true);
        stepUpPinRef.current?.focus?.();
        const guided = new Error('This change needs your PIN. Enter it under “Protected changes”, then try again.');
        guided.status = err.status;
        guided.requiresStepUp = true;
        throw guided;
      }
      throw err;
    }
  }, [auth, stepUpPin]);
  const symbolSetsUiEnabled = canMountProjectContext(auth);
  const symbolPromotionUiEnabled = canMountOrganizationSymbolDrafts(auth);
  const agentOversightUiEnabled = canMountAgentOversight(auth);
  const notifyContextChange = useCallback(() => {
    setContextRefreshToken((current) => current + 1);
  }, []);

  const handlePinChange = useCallback((value) => {
    setStepUpPin(value);
    if (value) setStepUpNeeded(false);
  }, []);

  if (error) {
    return createElement(
      'section',
      { className: 'experience-shell organization-admin-shell' },
      createElement(
        'div',
        { className: 'hero-panel glass-panel' },
        createElement('h1', null, 'Organization administration')
      ),
      ErrorMessage({ message: error })
    );
  }

  if (!org) {
    return createElement(
      'section',
      { className: 'experience-shell organization-admin-shell' },
      createElement(
        'div',
        { className: 'glass-panel pane' },
        createElement('p', { role: 'status' }, 'Loading…')
      )
    );
  }

  const libraryTabEnabled = symbolSetsUiEnabled || symbolPromotionUiEnabled;
  const tabs = [
    { key: 'members', label: 'Members' },
    { key: 'organization', label: 'Organization' },
    libraryTabEnabled ? { key: 'library', label: 'Projects & symbol sets' } : null,
    { key: 'activity', label: 'Activity' },
  ].filter(Boolean);
  const currentTab = tabs.some((tab) => tab.key === activeTab) ? activeTab : tabs[0].key;

  function onTabKeyDown(event) {
    const moves = { ArrowLeft: -1, ArrowRight: 1, Home: 'first', End: 'last' };
    const move = moves[event.key];
    if (move === undefined) return;
    event.preventDefault?.();
    const index = tabs.findIndex((tab) => tab.key === currentTab);
    const last = tabs.length - 1;
    const nextIndex = move === 'first'
      ? 0
      : move === 'last'
        ? last
        : (index + move + tabs.length) % tabs.length;
    const next = tabs[nextIndex];
    setActiveTab(next.key);
    // Only present on a real DOM event; the tab still changes without it.
    event.currentTarget?.querySelector?.(`#${tabId(next.key)}`)?.focus?.();
  }

  const orgStatus = org.isActive ? org.entitlementStatus : 'inactive';

  return createElement(
    'section',
    { className: 'experience-shell organization-admin-shell' },
    createElement(
      'div',
      { className: 'hero-panel glass-panel organization-admin-hero' },
      createElement(
        'div',
        { className: 'organization-admin-hero-identity' },
        org.iconUrl
          ? createElement('img', {
              src: org.iconUrl,
              alt: '',
              width: 44,
              height: 44,
              className: 'organization-admin-hero-icon',
            })
          : null,
        createElement(
          'div',
          null,
          createElement('p', { className: 'eyebrow' }, 'Organization administration'),
          createElement('h1', null, org.displayName),
          createElement(
            'p',
            { className: 'organization-admin-hero-meta' },
            createElement('code', null, org.code),
            StatusBadge({ status: orgStatus })
          )
        )
      ),
      createElement(
        'p',
        { className: 'page-status-text' },
        isAdmin ? 'Signed in as an organization admin' : 'Read-only — organization admin required to make changes'
      )
    ),
    isAdmin
      ? createElement(StepUpPanel, {
          pin: stepUpPin,
          onPinChange: handlePinChange,
          inputRef: stepUpPinRef,
          needed: stepUpNeeded,
        })
      : null,
    symbolSetsUiEnabled
      ? createElement(ProjectContextBar, {
          auth,
          // The panels below bump this after changing Projects or Sets. The
          // bar must not also report back through `notifyContextChange`, or
          // each refresh would trigger the next.
          refreshToken: contextRefreshToken,
        })
      : null,
    createElement(
      'nav',
      {
        className: 'organization-admin-tabs',
        role: 'tablist',
        'aria-label': 'Organization administration sections',
        onKeyDown: onTabKeyDown,
      },
      tabs.map((tab) => createElement(
        'button',
        {
          key: tab.key,
          type: 'button',
          role: 'tab',
          id: tabId(tab.key),
          'aria-selected': tab.key === currentTab,
          'aria-controls': ADMIN_TAB_PANEL_ID,
          // Roving tabindex: `role="tablist"` promises the arrow keys move
          // between tabs and that Tab leaves the group.
          tabIndex: tab.key === currentTab ? 0 : -1,
          className: `organization-admin-tab${tab.key === currentTab ? ' active' : ''}`,
          onClick: () => setActiveTab(tab.key),
        },
        tab.label
      ))
    ),
    createElement(
      'div',
      {
        className: 'organization-admin-panel',
        id: ADMIN_TAB_PANEL_ID,
        role: 'tabpanel',
        'aria-labelledby': tabId(currentTab),
      },
      currentTab === 'members'
        ? createElement(MemberListSection, { isAdmin, protect })
        : null,
      currentTab === 'organization'
        ? createElement(
            Fragment,
            null,
            createElement(OrgDetailSection, { org, isAdmin, onUpdate: setOrg, protect }),
            createElement(OrgIconSection, {
              org,
              isAdmin,
              iconUploadEnabled: auth?.user?.capabilities?.organizationIconUploadEnabled === true,
              onUpdate: setOrg,
              protect,
            })
          )
        : null,
      currentTab === 'library'
        ? createElement(
            Fragment,
            null,
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
            symbolSetsUiEnabled
              ? createElement(SymbolSetBuilderPanel, { isAdmin })
              : null,
            symbolPromotionUiEnabled
              ? createElement(PromotionSubmissionPanel, { isAdmin })
              : null
          )
        : null,
      currentTab === 'activity'
        ? createElement(
            Fragment,
            null,
            createElement(EmbeddedPanel, null, createElement(OrgUsageDashboardSection, {})),
            createElement(EmbeddedPanel, null, createElement(OrgContributionSection, {})),
            agentOversightUiEnabled
              ? createElement(EmbeddedPanel, null, createElement(OrgAgentFindingsDashboardSection, {}))
              : null
          )
        : null
    )
  );
}
