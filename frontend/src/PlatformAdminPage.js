import { createElement, Fragment, useCallback, useEffect, useRef, useState } from 'react';
import { runWithStepUp } from './adminJourneys.js';
import {
  demoteGovernedSymbol,
  fetchAdminUsers,
  fetchDemotionImpactPreview,
  openOrganizationSymbolPromotionReview,
  requestJson,
  submitWorkspaceReviewDecision,
} from './api.js';
import { canMountOrganizationSymbolDrafts } from './projectContext.js';
import { formatReviewTimestamp } from './SemanticReviewPage.js';
import { PlatformOrganizationUsageDashboardSection } from './UsageDashboardSection.js';
import { PlatformOrganizationContributionSection } from './ContributionSection.js';
import { PlatformAgentFindingsDashboardSection } from './AgentFindingsDashboardSection.js';
import { AgentConfigurationSection } from './AgentConfigurationSection.js';

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

async function apiPost(path, body) {
  return resultValue(await requestJson(path, {
    method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

async function apiPatch(path, body) {
  return resultValue(await requestJson(path, {
    method: 'PATCH', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  }));
}

async function apiDelete(path) {
  resultValue(await requestJson(path, {
    method: 'DELETE', credentials: 'include', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  }));
}


export function grantExistingPlatformAdmin({ userId, protect }) {
  return protect(() => apiPost('/platform/admins', { userId }));
}

function ErrorMessage({ message }) {
  if (!message) return null;
  return createElement(
    'p',
    { role: 'alert', className: 'form-message error platform-admin-message' },
    message
  );
}

function HelpText({ children }) {
  return createElement('p', { className: 'platform-admin-help' }, children);
}

/**
 * Status pill. `.admin-status-pill` already carries the active/inactive
 * treatment used by the site user table, so entitlement states map onto it
 * rather than growing a second vocabulary: the word shown is always the real
 * backend value.
 */
function StatusPill({ status }) {
  const modifier = status === 'active' ? 'active' : 'inactive';
  return createElement('span', { className: `admin-status-pill ${modifier}` }, status);
}

/**
 * The usage, contribution and agent-findings sections are shared with the
 * organization page and carry no chrome of their own; give them the page panel
 * so a drill-down reads like the rest of the surface.
 */
function EmbeddedPanel({ children }) {
  return createElement('div', { className: 'glass-panel pane platform-admin-embedded' }, children);
}

/**
 * Section shell: one heading, an optional one-line explanation, and an
 * optional action slot on the heading row rather than buried in the body.
 */
// `headingId` rather than `id`: the heading ids below are asserted with
// findByProps({ id }), which would otherwise match this wrapper element first.
function AdminSection({ headingId, title, description, actions, children }) {
  return createElement(
    'section',
    { className: 'glass-panel pane platform-admin-section', 'aria-labelledby': headingId },
    createElement(
      'div',
      { className: 'detail-heading platform-admin-section-heading' },
      createElement(
        'div',
        null,
        createElement('h2', { id: headingId }, title),
        description ? createElement('p', { className: 'title-support' }, description) : null
      ),
      actions || null
    ),
    children
  );
}

/**
 * Tablist with a roving tabindex, used both for the page sections and for the
 * organization detail pane. `role="tablist"` promises that the arrow keys move
 * between tabs and that Tab leaves the group, so both must be true.
 */
function TabList({ className, label, tabs, activeKey, onSelect, idFor, panelId }) {
  function onKeyDown(event) {
    const moves = { ArrowLeft: -1, ArrowRight: 1, Home: 'first', End: 'last' };
    const move = moves[event.key];
    if (move === undefined) return;
    event.preventDefault?.();
    const index = tabs.findIndex((tab) => tab.key === activeKey);
    const last = tabs.length - 1;
    const nextIndex = move === 'first'
      ? 0
      : move === 'last'
        ? last
        : (index + move + tabs.length) % tabs.length;
    const next = tabs[nextIndex];
    onSelect(next.key);
    // Only present on a real DOM event; the tab still changes without it.
    event.currentTarget?.querySelector?.(`#${idFor(next.key)}`)?.focus?.();
  }

  return createElement(
    'div',
    { className, role: 'tablist', 'aria-label': label, onKeyDown },
    tabs.map((tab) => createElement(
      'button',
      {
        key: tab.key,
        type: 'button',
        role: 'tab',
        id: idFor(tab.key),
        'aria-selected': tab.key === activeKey,
        'aria-controls': panelId,
        tabIndex: tab.key === activeKey ? 0 : -1,
        className: `platform-admin-tab${tab.key === activeKey ? ' active' : ''}`,
        onClick: () => onSelect(tab.key),
      },
      tab.label
    ))
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

  const row = createElement(
    'tr',
    { 'aria-busy': busy || undefined },
    createElement(
      'td',
      null,
      createElement('strong', { className: 'platform-admin-principal-name' }, admin.displayName),
      createElement('span', { className: 'platform-admin-principal-email' }, admin.email)
    ),
    createElement('td', null, StatusPill({ status: admin.userIsActive ? 'active' : 'inactive user' })),
    createElement('td', null, createElement('span', { className: 'platform-admin-timestamp' }, formatReviewTimestamp(admin.grantedAt))),
    createElement(
      'td',
      { className: 'platform-admin-row-actions' },
      createElement(
        'button',
        {
          type: 'button',
          onClick: handleRevoke,
          disabled: busy,
          className: 'action-button compact danger',
          'aria-label': `Revoke platform admin for ${admin.displayName}`,
        },
        busy ? 'Revoking…' : 'Revoke'
      )
    )
  );

  if (!error) return row;

  return createElement(
    Fragment,
    null,
    row,
    createElement(
      'tr',
      { className: 'platform-admin-error-row' },
      createElement('td', { colSpan: 4 }, createElement('p', { role: 'alert', className: 'form-message error' }, error))
    )
  );
}

/**
 * One organization. The destructive Suspend sits in its own cell, a full
 * cell-padding away from the read-only drill-downs, so that reaching for
 * "View usage" cannot land on "Suspend".
 */
function OrganizationRow({
  organization, onSuspend, onReactivate, onSelectView, selectedView, isSelected, agentOversightUiEnabled,
}) {
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

  const views = [
    { key: 'members', label: 'Members', ariaLabel: `View members for ${organization.displayName}` },
    { key: 'usage', label: 'Usage', ariaLabel: `View usage dashboard for ${organization.displayName}` },
    { key: 'contributions', label: 'Contributions', ariaLabel: `View contributions for ${organization.displayName}` },
    agentOversightUiEnabled
      ? { key: 'findings', label: 'Agent findings', ariaLabel: `View Organization Steward findings for ${organization.displayName}` }
      : null,
  ].filter(Boolean);

  const row = createElement(
    'tr',
    {
      className: `platform-organization-row${isSelected ? ' is-selected' : ''}`,
      'aria-current': isSelected ? 'true' : undefined,
      'aria-busy': busy || undefined,
    },
    createElement(
      'td',
      null,
      createElement('strong', { className: 'platform-admin-principal-name' }, organization.displayName),
      createElement('code', { className: 'platform-admin-principal-code' }, organization.code)
    ),
    createElement(
      'td',
      null,
      createElement(
        'div',
        { className: 'platform-admin-cell-stack' },
        StatusPill({ status: organization.entitlementStatus }),
        organization.isProtected
          ? createElement('span', { className: 'protected-owner-label' }, 'protected')
          : null
      )
    ),
    createElement(
      'td',
      null,
      createElement(
        'div',
        { className: 'platform-admin-row-views', role: 'group', 'aria-label': `Details for ${organization.displayName}` },
        views.map((view) => createElement(
          'button',
          {
            key: view.key,
            type: 'button',
            className: `action-button compact${isSelected && selectedView === view.key ? ' selected' : ''}`,
            'aria-pressed': isSelected && selectedView === view.key,
            onClick: () => onSelectView(organization, view.key),
            'aria-label': view.ariaLabel,
          },
          view.label
        ))
      )
    ),
    createElement(
      'td',
      { className: 'platform-admin-row-actions' },
      organization.isProtected
        ? createElement('span', { className: 'muted-text' }, '—')
        : createElement(
            'button',
            {
              type: 'button',
              onClick: handleToggle,
              disabled: busy,
              className: `action-button compact${suspended ? '' : ' danger'}`,
              'aria-label': `${suspended ? 'Reactivate' : 'Suspend'} organization ${organization.displayName}`,
            },
            suspended ? 'Reactivate' : 'Suspend'
          )
    )
  );

  if (!error) return row;

  return createElement(
    Fragment,
    null,
    row,
    createElement(
      'tr',
      { className: 'platform-admin-error-row' },
      createElement('td', { colSpan: 4 }, createElement('p', { role: 'alert', className: 'form-message error' }, error))
    )
  );
}

function DiagnosticMemberRow({ member, onReactivate }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  async function handleSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await onReactivate(member.membershipId, reason);
      setReason('');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return createElement(
    'li',
    { className: 'platform-admin-diagnostic-member' },
    createElement(
      'div',
      { className: 'platform-admin-diagnostic-identity' },
      createElement('strong', { className: 'platform-admin-principal-name' }, member.displayName),
      createElement('span', { className: 'platform-admin-principal-email' }, member.email)
    ),
    createElement(
      'div',
      { className: 'platform-admin-cell-stack' },
      StatusPill({ status: member.status }),
      createElement('span', { className: 'admin-status-pill' }, member.baseRole)
    ),
    member.status === 'inactive'
      ? createElement(
          'form',
          { onSubmit: handleSubmit, className: 'platform-admin-inline-form' },
          createElement(
            'label',
            { htmlFor: `reactivation-reason-${member.membershipId}`, className: 'field' },
            createElement('span', null, 'Reactivation reason'),
            createElement('input', {
              id: `reactivation-reason-${member.membershipId}`,
              value: reason,
              minLength: 10,
              maxLength: 1000,
              required: true,
              onChange: (event) => setReason(event.target.value),
            })
          ),
          createElement(
            'button',
            {
              type: 'submit',
              className: 'action-button primary',
              disabled: busy || reason.trim().length < 10,
            },
            busy ? 'Reactivating…' : 'Reactivate membership'
          ),
          error ? createElement(ErrorMessage, { message: error }) : null
        )
      : null
  );
}

function MemberDiagnostics({ organization, members, loading, error, onReactivate }) {
  if (!organization) return null;
  return createElement(
    'section',
    { 'aria-labelledby': 'member-diagnostics-heading', className: 'platform-admin-detail-section' },
    createElement('h3', { id: 'member-diagnostics-heading' }, `Member diagnostics: ${organization.displayName}`),
    error ? createElement(ErrorMessage, { message: error }) : null,
    loading ? createElement('p', { role: 'status', className: 'platform-admin-help' }, 'Loading member diagnostics…') : null,
    members && members.length === 0 ? createElement(HelpText, null, 'No memberships found.') : null,
    members && members.length > 0
      ? createElement(
          'ul',
          { className: 'platform-admin-diagnostic-list' },
          members.map((member) => createElement(DiagnosticMemberRow, {
            key: member.membershipId,
            member,
            onReactivate,
          }))
        )
      : null
  );
}

const ORGANIZATION_DETAIL_PANEL_ID = 'platform-organization-detail-panel';

function detailTabId(key) {
  return `platform-org-detail-tab-${key}`;
}

/**
 * The four drill-downs used to render one under another inside the
 * Organizations section and accumulate: view members on one organization, then
 * usage on another, and both panels sat below the list with nothing tying them
 * to the row they came from. They are now a single-selection detail pane —
 * one organization, one view, always named in the heading.
 */
function OrganizationDetailPane({
  organization, view, onSelectView, onClose, agentOversightUiEnabled,
  members, membersLoading, membersError, onReactivateMembership,
}) {
  const views = [
    { key: 'members', label: 'Members' },
    { key: 'usage', label: 'Usage' },
    { key: 'contributions', label: 'Contributions' },
    agentOversightUiEnabled ? { key: 'findings', label: 'Agent findings' } : null,
  ].filter(Boolean);
  const currentView = views.some((entry) => entry.key === view) ? view : views[0].key;

  return createElement(
    'section',
    {
      className: 'glass-panel pane platform-admin-detail',
      'aria-labelledby': 'platform-organization-detail-heading',
    },
    createElement(
      'div',
      { className: 'detail-heading platform-admin-section-heading' },
      createElement(
        'div',
        null,
        createElement('p', { className: 'eyebrow' }, 'Organization detail'),
        createElement('h2', { id: 'platform-organization-detail-heading' }, organization.displayName),
        createElement('p', { className: 'title-support' }, organization.code)
      ),
      createElement(
        'button',
        {
          type: 'button',
          className: 'action-button compact ghost',
          onClick: onClose,
          'aria-label': `Close detail for ${organization.displayName}`,
        },
        'Close'
      )
    ),
    createElement(TabList, {
      className: 'platform-admin-tabs platform-admin-detail-tabs',
      label: `Detail views for ${organization.displayName}`,
      tabs: views,
      activeKey: currentView,
      onSelect: (key) => onSelectView(organization, key),
      idFor: detailTabId,
      panelId: ORGANIZATION_DETAIL_PANEL_ID,
    }),
    createElement(
      'div',
      {
        className: 'platform-admin-detail-body',
        id: ORGANIZATION_DETAIL_PANEL_ID,
        role: 'tabpanel',
        'aria-labelledby': detailTabId(currentView),
      },
      currentView === 'members'
        ? createElement(MemberDiagnostics, {
            organization,
            members,
            loading: membersLoading,
            error: membersError,
            onReactivate: onReactivateMembership,
          })
        : null,
      currentView === 'usage'
        ? createElement(EmbeddedPanel, null, createElement(PlatformOrganizationUsageDashboardSection, {
            organizationId: organization.id,
            organizationLabel: organization.displayName,
          }))
        : null,
      currentView === 'contributions'
        ? createElement(EmbeddedPanel, null, createElement(PlatformOrganizationContributionSection, {
            organizationId: organization.id,
            organizationLabel: organization.displayName,
          }))
        : null,
      currentView === 'findings' && agentOversightUiEnabled
        ? createElement(EmbeddedPanel, null, createElement(PlatformAgentFindingsDashboardSection, {
            organizationId: organization.id,
            organizationLabel: organization.displayName,
          }))
        : null
    )
  );
}

// The labels stay direct children of the form: the grid does the layout so
// that each control keeps its own label association without a wrapper.
function ProtectedMemberAddForm({ onAdd }) {
  const [userId, setUserId] = useState('');
  const [baseRole, setBaseRole] = useState('user');
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const validReason = reason.trim().length >= 10 && reason.trim().length <= 1000;

  async function handleSubmit(event) {
    event.preventDefault();
    setBusy(true);
    setError('');
    try {
      await onAdd({ userId, baseRole, reason });
      setUserId(''); setBaseRole('user'); setReason('');
    } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  return createElement(
    'form',
    { onSubmit: handleSubmit, className: 'platform-admin-record-form platform-admin-protected-add' },
    createElement('p', { className: 'platform-admin-record-form-title' }, 'Add a protected member'),
    createElement(
      'label',
      { htmlFor: 'protected-member-user-id', className: 'field' },
      createElement('span', null, 'Existing user ID'),
      createElement('input', {
        id: 'protected-member-user-id', value: userId, required: true,
        onChange: (event) => setUserId(event.target.value),
      })
    ),
    createElement(
      'label',
      { htmlFor: 'protected-member-base-role', className: 'field' },
      createElement('span', null, 'Base role'),
      createElement(
        'select',
        { id: 'protected-member-base-role', value: baseRole, onChange: (event) => setBaseRole(event.target.value) },
        createElement('option', { value: 'user' }, 'User'),
        createElement('option', { value: 'admin' }, 'Administrator')
      )
    ),
    createElement(
      'label',
      { htmlFor: 'protected-member-reason', className: 'field' },
      createElement('span', null, 'Reason'),
      createElement('input', {
        id: 'protected-member-reason', value: reason, minLength: 10, maxLength: 1000, required: true,
        onChange: (event) => setReason(event.target.value),
      })
    ),
    createElement(
      'button',
      {
        type: 'submit',
        className: 'action-button primary platform-admin-record-form-submit',
        disabled: busy || !userId || !validReason,
      },
      busy ? 'Adding…' : 'Add protected member'
    ),
    error ? createElement(ErrorMessage, { message: error }) : null
  );
}

function ProtectedMemberRow({ member, onRoleChange, onDeactivate }) {
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const validReason = reason.trim().length >= 10 && reason.trim().length <= 1000;

  async function mutate(operation) {
    setBusy(true); setError('');
    try { await operation(reason); setReason(''); } catch (err) { setError(err.message); } finally { setBusy(false); }
  }

  if (member.status !== 'active') {
    return createElement(
      'tr',
      null,
      createElement(
        'td',
        null,
        createElement('strong', { className: 'platform-admin-principal-name' }, member.displayName),
        createElement('span', { className: 'platform-admin-principal-email' }, member.email)
      ),
      createElement('td', null, StatusPill({ status: 'inactive' })),
      createElement('td', { colSpan: 2 }, createElement('span', { className: 'muted-text' }, 'No changes available for an inactive membership.'))
    );
  }

  const nextRole = member.baseRole === 'admin' ? 'user' : 'admin';
  const roleVerb = member.baseRole === 'admin' ? 'Demote' : 'Promote';

  const row = createElement(
    'tr',
    { 'aria-busy': busy || undefined },
    createElement(
      'td',
      null,
      createElement('strong', { className: 'platform-admin-principal-name' }, member.displayName),
      createElement('span', { className: 'platform-admin-principal-email' }, member.email)
    ),
    createElement('td', null, createElement('span', { className: 'admin-status-pill' }, member.baseRole)),
    createElement(
      'td',
      null,
      createElement(
        'label',
        { htmlFor: `protected-member-mutation-reason-${member.membershipId}`, className: 'field' },
        createElement('span', null, 'Reason for this change'),
        createElement('input', {
          id: `protected-member-mutation-reason-${member.membershipId}`,
          value: reason, minLength: 10, maxLength: 1000, required: true,
          onChange: (event) => setReason(event.target.value),
        })
      )
    ),
    createElement(
      'td',
      { className: 'platform-admin-row-actions' },
      createElement(
        'div',
        { className: 'platform-admin-cell-stack' },
        createElement(
          'button',
          {
            type: 'button',
            className: 'action-button compact',
            disabled: busy || !validReason,
            'aria-label': `${roleVerb} ${member.displayName}`,
            onClick: () => mutate((value) => onRoleChange(member.membershipId, nextRole, value)),
          },
          roleVerb
        ),
        createElement(
          'button',
          {
            type: 'button',
            className: 'action-button compact danger',
            disabled: busy || !validReason,
            'aria-label': `Deactivate ${member.displayName}`,
            onClick: () => mutate((value) => onDeactivate(member.membershipId, value)),
          },
          'Deactivate'
        )
      )
    )
  );

  if (!error) return row;

  return createElement(
    Fragment,
    null,
    row,
    createElement(
      'tr',
      { className: 'platform-admin-error-row' },
      createElement('td', { colSpan: 4 }, createElement('p', { role: 'alert', className: 'form-message error' }, error))
    )
  );
}

function ProtectedSymgovMembers({ members, total, loading, error, onAdd, onRoleChange, onDeactivate }) {
  return createElement(
    AdminSection,
    {
      headingId: 'protected-symgov-members-heading',
      title: `Protected Symgov members (${total})`,
      description: 'Membership of the protected Symgov organization. Every change needs a recorded reason.',
    },
    error ? createElement(ErrorMessage, { message: error }) : null,
    members ? createElement(ProtectedMemberAddForm, { onAdd }) : null,
    loading ? createElement('p', { role: 'status', className: 'platform-admin-help' }, 'Loading protected members…') : null,
    members
      ? createElement(
          'div',
          { className: 'admin-users-table-shell' },
          createElement(
            'table',
            { className: 'platform-admin-grid platform-symgov-grid' },
            createElement(
              'thead',
              null,
              createElement(
                'tr',
                null,
                createElement('th', { scope: 'col' }, 'Member'),
                createElement('th', { scope: 'col' }, 'Base role'),
                createElement('th', { scope: 'col' }, 'Reason'),
                createElement('th', { scope: 'col' }, 'Actions')
              )
            ),
            createElement(
              'tbody',
              null,
              members.length === 0
                ? createElement(
                    'tr',
                    null,
                    createElement('td', { colSpan: 4, className: 'admin-users-empty' }, 'No protected members found.')
                  )
                : members.map((member) => createElement(ProtectedMemberRow, {
                    key: member.membershipId, member, onRoleChange, onDeactivate,
                  }))
            )
          )
        )
      : null
  );
}

// Load every user so the initial admin can be picked rather than typed.
// POST /platform/organizations accepts any valid user as an organization's
// first Organization Admin -- they need not hold the site admin role -- so the
// list is deliberately all users, not a filtered subset (decided 2026-09-08).
async function loadAllUsers() {
  const collected = [];
  let page = 1;
  for (;;) {
    // pageSize is capped at 200 by routes/admin.py list_users.
    const result = await fetchAdminUsers({ page, pageSize: 200, sort: 'name', sortDirection: 'asc' });
    if (!result.ok) {
      const error = new Error(result.message || 'User list unavailable.');
      error.status = result.status;
      throw error;
    }
    collected.push(...result.items);
    if (result.items.length === 0 || collected.length >= result.total) break;
    page += 1;
  }
  return collected;
}

export function userPickerOptions(users) {
  // Deactivated or deleted accounts are excluded: an organization whose first
  // admin cannot sign in has nobody able to administer it.
  return users
    .filter((user) => user.isActive && !user.isDeleted)
    .map((user) => ({
      id: user.id,
      label: `${user.displayName || user.email} (${user.email})`,
    }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

export function CreateOrganizationForm({ onCreate, loadUsers = loadAllUsers }) {
  const [code, setCode] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [initialAdminUserId, setInitialAdminUserId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const [users, setUsers] = useState(null);
  const [usersError, setUsersError] = useState('');

  useEffect(() => {
    let cancelled = false;
    loadUsers()
      .then((loaded) => { if (!cancelled) setUsers(userPickerOptions(loaded)); })
      // Listing users needs the site admin role (routes/admin.py list_users,
      // require_any_role({"admin"})), which a platform admin need not hold.
      // Fall back to entering the id by hand rather than blocking creation.
      .catch((err) => { if (!cancelled) setUsersError(err.message || 'User list unavailable.'); });
    return () => { cancelled = true; };
  }, [loadUsers]);

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
    { onSubmit: handleSubmit, className: 'platform-admin-record-form platform-admin-create-org' },
    createElement('p', { className: 'platform-admin-record-form-title' }, 'Create an organization'),
    createElement(
      'label',
      { htmlFor: 'new-org-code', className: 'field' },
      createElement('span', null, 'Code'),
      createElement('input', {
        id: 'new-org-code',
        type: 'text',
        value: code,
        // Organization codes must be entered uppercase ("must start with an
        // uppercase letter and contain only uppercase alphanumeric characters
        // or hyphens"); the API lower-cases them for the normalized form.
        autoCapitalize: 'characters',
        spellCheck: false,
        onChange: (e) => setCode(e.target.value.toUpperCase()),
        required: true,
      })
    ),
    createElement(
      'label',
      { htmlFor: 'new-org-display-name', className: 'field' },
      createElement('span', null, 'Display name'),
      createElement('input', {
        id: 'new-org-display-name',
        type: 'text',
        value: displayName,
        onChange: (e) => setDisplayName(e.target.value),
        required: true,
      })
    ),
    createElement(
      'label',
      { htmlFor: 'new-org-initial-admin', className: 'field' },
      createElement('span', null, users ? 'Initial admin' : 'Initial admin user ID'),
      users
        ? createElement(
          'select',
          {
            id: 'new-org-initial-admin',
            value: initialAdminUserId,
            onChange: (e) => setInitialAdminUserId(e.target.value),
            required: true,
          },
          createElement('option', { value: '' }, 'Select a user…'),
          ...users.map((user) => createElement('option', { key: user.id, value: user.id }, user.label)),
        )
        : createElement('input', {
          id: 'new-org-initial-admin',
          type: 'text',
          value: initialAdminUserId,
          onChange: (e) => setInitialAdminUserId(e.target.value),
          required: true,
          'aria-describedby': usersError ? 'new-org-initial-admin-hint' : undefined,
        }),
    ),
    createElement(
      'button',
      {
        type: 'submit',
        className: 'action-button primary platform-admin-record-form-submit',
        disabled: saving || !code || !displayName || !initialAdminUserId,
      },
      saving ? 'Creating…' : 'Create organization'
    ),
    // Kept out of the label: two lines of hint inside one grid cell lift that
    // field's input off the baseline the rest of the row sits on.
    usersError
      ? createElement('p', { id: 'new-org-initial-admin-hint', className: 'field-hint platform-admin-record-form-hint' },
        `User list unavailable (${usersError}) — enter the user's ID.`)
      : null,
    error ? ErrorMessage({ message: error }) : null
  );
}

export function GrantAdminForm({ onGrant }) {
  const [userId, setUserId] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const userIdInputRef = useRef(null);

  async function handleSubmit(e) {
    e.preventDefault();
    setSaving(true);
    setError('');
    try {
      await onGrant(userId);
      setUserId('');
    } catch (err) {
      setError(err.message);
      // A step-up refusal is fixed in the page-level PIN panel, which has
      // already taken focus; pulling it back here would send the operator to
      // the one field that is not the problem.
      if (!err?.requiresStepUp) queueMicrotask(() => userIdInputRef.current?.focus());
    } finally {
      setSaving(false);
    }
  }

  return createElement(
    'form',
    { onSubmit: handleSubmit, className: 'platform-admin-record-form platform-admin-grant-form' },
    createElement('p', { className: 'platform-admin-record-form-title' }, 'Grant platform administration'),
    createElement(
      'label',
      { htmlFor: 'platform-admin-user-id', className: 'field' },
      createElement('span', null, 'User ID'),
      createElement('input', {
        id: 'platform-admin-user-id',
        type: 'text',
        value: userId,
        ref: userIdInputRef,
        onChange: (e) => setUserId(e.target.value),
        required: true,
      })
    ),
    createElement(
      'button',
      {
        type: 'submit',
        className: 'action-button primary platform-admin-record-form-submit',
        disabled: saving || !userId,
      },
      saving ? 'Granting…' : 'Grant platform admin'
    ),
    error ? ErrorMessage({ message: error }) : null
  );
}

function DemotionConsole({ protect }) {
  const [symbolId, setSymbolId] = useState('');
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState('');
  const [preview, setPreview] = useState(null);
  const [reason, setReason] = useState('');
  const [demoting, setDemoting] = useState(false);
  const [demoteError, setDemoteError] = useState('');
  const [demoteResult, setDemoteResult] = useState(null);

  async function loadPreview(event) {
    event.preventDefault();
    const trimmed = symbolId.trim();
    if (!trimmed) return;
    setPreviewLoading(true);
    setPreviewError('');
    setPreview(null);
    setDemoteResult(null);
    setDemoteError('');
    try {
      const data = await fetchDemotionImpactPreview(trimmed);
      setPreview(data);
    } catch (err) {
      setPreviewError(err.message);
    } finally {
      setPreviewLoading(false);
    }
  }

  async function handleDemote() {
    if (!preview || !preview.eligible) return;
    if (!window.confirm(`Demote governed symbol ${preview.governedSymbolId} from public visibility? This withdraws it from every public reader.`)) return;
    setDemoting(true);
    setDemoteError('');
    try {
      const result = await protect(() => demoteGovernedSymbol(preview.governedSymbolId, { reason: reason.trim() }));
      setDemoteResult(result);
      setPreview(null);
      setReason('');
    } catch (err) {
      setDemoteError(err.message);
    } finally {
      setDemoting(false);
    }
  }

  const validReason = reason.trim().length > 0;

  return createElement(
    AdminSection,
    {
      headingId: 'demotion-console-heading',
      title: 'Demote a public symbol',
      description: 'Withdraws a governed symbol from every public reader. Preview the impact first — the demotion itself is a separate, confirmed step.',
    },
    createElement(
      'form',
      { onSubmit: loadPreview, className: 'platform-admin-inline-form' },
      createElement(
        'label',
        { htmlFor: 'demotion-symbol-id', className: 'field' },
        createElement('span', null, 'Governed symbol ID'),
        createElement('input', {
          id: 'demotion-symbol-id',
          type: 'text',
          value: symbolId,
          onChange: (event) => setSymbolId(event.target.value),
          required: true,
        })
      ),
      createElement(
        'button',
        { type: 'submit', className: 'action-button', disabled: previewLoading || !symbolId.trim() },
        previewLoading ? 'Loading…' : 'Preview demotion impact'
      )
    ),
    ErrorMessage({ message: previewError }),
    preview
      ? createElement(
          'div',
          {
            role: 'group',
            'aria-labelledby': 'demotion-preview-heading',
            className: `platform-admin-preview${preview.eligible ? ' is-actionable' : ''}`,
          },
          createElement('h3', { id: 'demotion-preview-heading' }, `Impact preview: ${preview.governedSymbolId}`),
          createElement('p', { className: 'platform-admin-preview-verdict' }, preview.eligible ? 'Eligible for demotion.' : 'Not eligible for demotion.'),
          preview.reasons.length > 0
            ? createElement('ul', { className: 'platform-admin-preview-reasons' }, preview.reasons.map((r, i) => createElement('li', { key: i }, r)))
            : null,
          createElement(HelpText, null, `Favourites referencing this symbol: ${preview.favouritesCount}`),
          preview.blockingOrganizationIds.length > 0
            ? createElement(HelpText, null, `Blocked by references from ${preview.blockingOrganizationIds.length} other organization(s).`)
            : null,
          preview.eligible
            ? createElement(
                'div',
                { className: 'platform-admin-danger-zone' },
                createElement(
                  'label',
                  { htmlFor: 'demotion-reason', className: 'field' },
                  createElement('span', null, 'Reason for demotion'),
                  createElement('textarea', {
                    id: 'demotion-reason',
                    value: reason,
                    onChange: (event) => setReason(event.target.value),
                    required: true,
                    rows: 2,
                  })
                ),
                ErrorMessage({ message: demoteError }),
                createElement('button', {
                  type: 'button',
                  onClick: handleDemote,
                  disabled: demoting || !validReason,
                  className: 'action-button danger',
                  'aria-label': `Demote governed symbol ${preview.governedSymbolId}`,
                }, demoting ? 'Demoting…' : 'Demote symbol')
              )
            : null
        )
      : null,
    demoteResult
      ? createElement('p', { role: 'status', className: 'form-message success' },
          `Demoted. Visibility is now "${demoteResult.visibility}". ${demoteResult.symbolRevisionIds.length} revision(s) withdrawn, ${demoteResult.publishedPageIds.length} page(s) retired, ${demoteResult.retiredPackIds.length} pack(s) retired.`)
      : null
  );
}

function PromotionReviewPanel() {
  const [symbolId, setSymbolId] = useState('');
  const [requestId, setRequestId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [request, setRequest] = useState(null);
  const [decided, setDecided] = useState(false);

  async function handleOpenReview(event) {
    event.preventDefault();
    const trimmedSymbolId = symbolId.trim();
    const trimmedRequestId = requestId.trim();
    if (!trimmedSymbolId || !trimmedRequestId) return;
    setBusy(true);
    setError('');
    setDecided(false);
    try {
      const opened = await openOrganizationSymbolPromotionReview(trimmedSymbolId, trimmedRequestId);
      setRequest(opened);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function handleAccept() {
    if (!request?.reviewCaseId) return;
    if (!window.confirm('Accept this promotion request and publish the symbol?')) return;
    setBusy(true);
    setError('');
    try {
      await submitWorkspaceReviewDecision(request.reviewCaseId, { decisionCode: 'approve' });
      setDecided(true);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return createElement(
    AdminSection,
    {
      headingId: 'promotion-review-heading',
      title: 'Review a promotion request',
      description: 'Enter the governed symbol ID and promotion request ID provided by the submitting organization’s admin. Accept-only: reject/changes-requested handling is not yet built.',
    },
    createElement(
      'form',
      { onSubmit: handleOpenReview, className: 'platform-admin-inline-form' },
      createElement(
        'label',
        { htmlFor: 'promotion-review-symbol-id', className: 'field' },
        createElement('span', null, 'Governed symbol ID'),
        createElement('input', {
          id: 'promotion-review-symbol-id',
          type: 'text',
          value: symbolId,
          onChange: (event) => setSymbolId(event.target.value),
          required: true,
        })
      ),
      createElement(
        'label',
        { htmlFor: 'promotion-review-request-id', className: 'field' },
        createElement('span', null, 'Promotion request ID'),
        createElement('input', {
          id: 'promotion-review-request-id',
          type: 'text',
          value: requestId,
          onChange: (event) => setRequestId(event.target.value),
          required: true,
        })
      ),
      createElement(
        'button',
        { type: 'submit', className: 'action-button', disabled: busy || !symbolId.trim() || !requestId.trim() },
        busy ? 'Working…' : 'Open for review'
      )
    ),
    ErrorMessage({ message: error }),
    request
      ? createElement(
          'div',
          {
            role: 'group',
            'aria-labelledby': 'promotion-review-request-heading',
            className: 'platform-admin-preview is-actionable',
          },
          createElement('h3', { id: 'promotion-review-request-heading' }, `Promotion request: ${request.id}`),
          createElement('p', { className: 'platform-admin-preview-verdict' }, `Status: ${request.status}`),
          createElement(HelpText, null, `Reason given: ${request.reason}`),
          decided
            ? createElement('p', { role: 'status', className: 'form-message success' }, 'Accepted. The symbol has been published.')
            : createElement(
                'div',
                { className: 'platform-admin-form-actions' },
                createElement('button', {
                  type: 'button',
                  className: 'action-button primary',
                  onClick: handleAccept,
                  disabled: busy || !request.reviewCaseId,
                  'aria-label': `Accept promotion request ${request.id}`,
                }, busy ? 'Working…' : 'Accept')
              )
        )
      : null
  );
}

/**
 * Step-up PIN. The PIN is spent on the first protected call that needs it, so
 * it is a page-level control rather than a field on any one form, and it stays
 * mounted on every tab. The panel says what it is for and whether one is
 * currently held.
 */
function StepUpPanel({ pin, onPinChange, inputRef, needed }) {
  return createElement(
    'section',
    {
      className: `glass-panel platform-admin-stepup${needed ? ' needs-pin' : ''}`,
      'aria-labelledby': 'platform-step-up-heading',
    },
    createElement(
      'div',
      { className: 'platform-admin-stepup-copy' },
      createElement('h2', { id: 'platform-step-up-heading' }, 'Protected changes'),
      createElement(
        'p',
        { className: 'platform-admin-help' },
        'Creating or suspending an organization, granting or revoking platform administration, changing a protected Symgov membership and demoting a public symbol all need your sign-in PIN. Enter it here first — it is used once and then cleared.'
      )
    ),
    createElement(
      'label',
      { htmlFor: 'platform-step-up-pin', className: 'field platform-admin-stepup-field' },
      createElement('span', null, 'PIN for protected changes'),
      createElement('input', {
        id: 'platform-step-up-pin', type: 'password', inputMode: 'numeric',
        autoComplete: 'off', value: pin, maxLength: 4,
        ref: inputRef,
        'aria-describedby': 'platform-step-up-state',
        onChange: (event) => onPinChange(event.target.value),
      })
    ),
    createElement(
      'p',
      {
        id: 'platform-step-up-state',
        role: 'status',
        className: `platform-admin-stepup-state${pin ? ' is-ready' : ''}`,
      },
      pin ? 'PIN entered — protected changes are ready.' : 'No PIN entered.'
    )
  );
}

const ADMIN_TAB_PANEL_ID = 'platform-admin-panel';

function tabId(key) {
  return `platform-admin-tab-${key}`;
}

export function PlatformAdminPage({ auth }) {
  const [admins, setAdmins] = useState(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stepUpPin, setStepUpPin] = useState('');
  const [stepUpNeeded, setStepUpNeeded] = useState(false);
  const [activeTab, setActiveTab] = useState('organizations');
  const stepUpPinRef = useRef(null);
  const PAGE_SIZE = 50;

  const [organizations, setOrganizations] = useState(null);
  const [orgTotal, setOrgTotal] = useState(0);
  const [orgPage, setOrgPage] = useState(1);
  const [orgLoading, setOrgLoading] = useState(false);
  const [orgError, setOrgError] = useState('');
  const ORG_PAGE_SIZE = 50;
  // One organization, one view. See OrganizationDetailPane.
  const [detailOrganization, setDetailOrganization] = useState(null);
  const [detailView, setDetailView] = useState('members');
  const [diagnosticMembers, setDiagnosticMembers] = useState(null);
  const [diagnosticLoading, setDiagnosticLoading] = useState(false);
  const [diagnosticError, setDiagnosticError] = useState('');
  const [protectedMembers, setProtectedMembers] = useState(null);
  const [protectedMemberTotal, setProtectedMemberTotal] = useState(0);
  const [protectedMemberLoading, setProtectedMemberLoading] = useState(false);
  const [protectedMemberError, setProtectedMemberError] = useState('');

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

  const handlePinChange = useCallback((value) => {
    setStepUpPin(value);
    if (value) setStepUpNeeded(false);
  }, []);

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

  const loadProtectedMembers = useCallback(async () => {
    setProtectedMemberLoading(true);
    setProtectedMemberError('');
    try {
      const data = await apiGet('/platform/organizations/symgov/members?page=1&pageSize=50');
      setProtectedMembers(data.items);
      setProtectedMemberTotal(data.total);
    } catch (err) {
      setProtectedMembers(null);
      setProtectedMemberTotal(0);
      setProtectedMemberError(err.message);
    } finally {
      setProtectedMemberLoading(false);
    }
  }, []);

  useEffect(() => { load(1); }, [load]);
  useEffect(() => { loadOrganizations(1); }, [loadOrganizations]);
  useEffect(() => { loadProtectedMembers(); }, [loadProtectedMembers]);

  async function handleGrant(userId) {
    await grantExistingPlatformAdmin({ userId, protect });
    await load(page);
  }

  async function handleRevoke(userId) {
    await protect(() => apiDelete(`/platform/admins/${userId}`));
    await load(page);
  }

  async function handleCreateOrganization({ code, displayName, initialAdminUserId }) {
    await protect(() => apiPost('/platform/organizations', { code, displayName, initialAdminUserId }));
    await loadOrganizations(orgPage);
  }

  async function handleSuspendOrganization(organizationId) {
    await protect(() => apiPost(`/platform/organizations/${organizationId}/suspend`));
    await loadOrganizations(orgPage);
  }

  async function handleReactivateOrganization(organizationId) {
    await protect(() => apiPost(`/platform/organizations/${organizationId}/reactivate`));
    await loadOrganizations(orgPage);
  }

  const loadMemberDiagnostics = useCallback(async (organization) => {
    setDiagnosticMembers(null);
    setDiagnosticLoading(true);
    setDiagnosticError('');
    try {
      const data = await apiGet(`/platform/organizations/${organization.id}/members?page=1&pageSize=50`);
      setDiagnosticMembers(data.items);
    } catch (err) {
      setDiagnosticError(err.message);
    } finally {
      setDiagnosticLoading(false);
    }
  }, []);

  // Selecting a drill-down selects the row it came from, so the pane can never
  // outlive or misattribute its subject.
  const selectOrganizationView = useCallback(async (organization, view) => {
    setDetailOrganization(organization);
    setDetailView(view);
    if (view === 'members') await loadMemberDiagnostics(organization);
  }, [loadMemberDiagnostics]);

  function closeOrganizationDetail() {
    setDetailOrganization(null);
    setDiagnosticMembers(null);
    setDiagnosticError('');
  }

  async function handleReactivateMembership(membershipId, reason) {
    await protect(() => apiPost(`/platform/memberships/${membershipId}/reactivate`, { reason }));
    await loadMemberDiagnostics(detailOrganization);
  }

  async function handleAddProtectedMember({ userId, baseRole, reason }) {
    await protect(() => apiPost('/platform/organizations/symgov/members', { userId, baseRole, reason }));
    await loadProtectedMembers();
  }

  async function handleProtectedMemberRoleChange(membershipId, baseRole, reason) {
    await protect(() => apiPatch(`/platform/organizations/symgov/members/${membershipId}`, { baseRole, reason }));
    await loadProtectedMembers();
  }

  async function handleDeactivateProtectedMember(membershipId, reason) {
    await protect(() => apiPost(`/platform/organizations/symgov/members/${membershipId}/deactivate`, { reason }));
    await loadProtectedMembers();
  }

  if (error && !admins) {
    return createElement(
      'section',
      { className: 'experience-shell platform-admin-shell' },
      createElement(
        'div',
        { className: 'hero-panel glass-panel' },
        createElement('h1', null, 'Platform administration')
      ),
      ErrorMessage({ message: error })
    );
  }

  if (!admins) {
    return createElement(
      'section',
      { className: 'experience-shell platform-admin-shell' },
      createElement(
        'div',
        { className: 'glass-panel pane' },
        createElement('p', { role: 'status' }, 'Loading…')
      )
    );
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const orgTotalPages = Math.ceil(orgTotal / ORG_PAGE_SIZE);
  const symbolPromotionUiEnabled = canMountOrganizationSymbolDrafts(auth) && auth?.user?.capabilities?.platformAdminEnabled === true;
  const agentOversightUiEnabled = auth?.user?.capabilities?.organizationAgentsEnabled === true;

  const tabs = [
    { key: 'organizations', label: 'Organizations' },
    { key: 'admins', label: 'Platform admins' },
    { key: 'symgov', label: 'Symgov members' },
    symbolPromotionUiEnabled ? { key: 'symbols', label: 'Symbol governance' } : null,
    agentOversightUiEnabled ? { key: 'agents', label: 'Agents' } : null,
  ].filter(Boolean);
  const currentTab = tabs.some((tab) => tab.key === activeTab) ? activeTab : tabs[0].key;

  const organizationsSection = createElement(
    AdminSection,
    {
      headingId: 'platform-organizations-heading',
      title: `Organizations (${orgTotal})`,
      description: 'Every organization on the platform. Select one to see its members, usage, contributions and agent findings.',
    },
    createElement(CreateOrganizationForm, { onCreate: handleCreateOrganization }),
    ErrorMessage({ message: orgError }),
    orgLoading ? createElement('p', { role: 'status', className: 'platform-admin-help' }, 'Loading…') : null,
    organizations
      ? createElement(
          'div',
          { className: 'admin-users-table-shell' },
          createElement(
            'table',
            { className: 'platform-admin-grid platform-organizations-grid' },
            createElement(
              'thead',
              null,
              createElement(
                'tr',
                null,
                createElement('th', { scope: 'col' }, 'Organization'),
                createElement('th', { scope: 'col' }, 'Entitlement'),
                createElement('th', { scope: 'col' }, 'Details'),
                createElement('th', { scope: 'col' }, 'Actions')
              )
            ),
            createElement(
              'tbody',
              null,
              orgTotal === 0
                ? createElement(
                    'tr',
                    null,
                    createElement('td', { colSpan: 4, className: 'admin-users-empty' }, 'No organizations found.')
                  )
                : organizations.map((o) => createElement(OrganizationRow, {
                    key: o.id,
                    organization: o,
                    agentOversightUiEnabled,
                    onSuspend: handleSuspendOrganization,
                    onReactivate: handleReactivateOrganization,
                    onSelectView: selectOrganizationView,
                    selectedView: detailView,
                    isSelected: detailOrganization?.id === o.id,
                  }))
            )
          )
        )
      : null,
    orgTotalPages > 1
      ? createElement(
          'nav',
          { 'aria-label': 'Organization list pagination', className: 'admin-user-pagination' },
          createElement(
            'button',
            { type: 'button', className: 'action-button compact', onClick: () => loadOrganizations(orgPage - 1), disabled: orgPage <= 1 || orgLoading },
            'Previous'
          ),
          createElement('span', { className: 'muted-text' }, `Page ${orgPage} of ${orgTotalPages}`),
          createElement(
            'button',
            { type: 'button', className: 'action-button compact', onClick: () => loadOrganizations(orgPage + 1), disabled: orgPage >= orgTotalPages || orgLoading },
            'Next'
          )
        )
      : null
  );

  const adminsSection = createElement(
    AdminSection,
    {
      headingId: 'platform-admins-heading',
      title: `Platform admins (${total})`,
      description: 'Accounts holding platform administration. Granting and revoking both need a PIN.',
    },
    createElement(GrantAdminForm, { onGrant: handleGrant }),
    ErrorMessage({ message: error }),
    loading ? createElement('p', { role: 'status', className: 'platform-admin-help' }, 'Loading…') : null,
    createElement(
      'div',
      { className: 'admin-users-table-shell' },
      createElement(
        'table',
        { className: 'platform-admin-grid platform-admins-grid' },
        createElement(
          'thead',
          null,
          createElement(
            'tr',
            null,
            createElement('th', { scope: 'col' }, 'Admin'),
            createElement('th', { scope: 'col' }, 'Account'),
            createElement('th', { scope: 'col' }, 'Granted'),
            createElement('th', { scope: 'col' }, 'Actions')
          )
        ),
        createElement(
          'tbody',
          null,
          total === 0
            ? createElement(
                'tr',
                null,
                createElement('td', { colSpan: 4, className: 'admin-users-empty' }, 'No platform admins found.')
              )
            : admins.map((a) => createElement(AdminRow, { key: a.userId, admin: a, onRevoke: handleRevoke }))
        )
      )
    ),
    totalPages > 1
      ? createElement(
          'nav',
          { 'aria-label': 'Platform admin list pagination', className: 'admin-user-pagination' },
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

  return createElement(
    'section',
    { className: 'experience-shell platform-admin-shell' },
    createElement(
      'div',
      { className: 'hero-panel glass-panel platform-admin-hero' },
      createElement(
        'div',
        null,
        createElement('p', { className: 'eyebrow' }, 'Symgov platform'),
        createElement('h1', null, 'Platform administration'),
        createElement(
          'p',
          { className: 'platform-admin-hero-meta' },
          `${orgTotal} organization${orgTotal === 1 ? '' : 's'} · ${total} platform admin${total === 1 ? '' : 's'}`
        )
      ),
      createElement('p', { className: 'page-status-text' }, 'Signed in as a platform admin')
    ),
    createElement(StepUpPanel, {
      pin: stepUpPin,
      onPinChange: handlePinChange,
      inputRef: stepUpPinRef,
      needed: stepUpNeeded,
    }),
    createElement(TabList, {
      className: 'platform-admin-tabs',
      label: 'Platform administration sections',
      tabs,
      activeKey: currentTab,
      onSelect: setActiveTab,
      idFor: tabId,
      panelId: ADMIN_TAB_PANEL_ID,
    }),
    createElement(
      'div',
      {
        className: 'platform-admin-panel',
        id: ADMIN_TAB_PANEL_ID,
        role: 'tabpanel',
        'aria-labelledby': tabId(currentTab),
      },
      currentTab === 'organizations'
        ? createElement(
            'div',
            { className: `platform-admin-org-layout${detailOrganization ? ' has-detail' : ''}` },
            organizationsSection,
            detailOrganization
              ? createElement(OrganizationDetailPane, {
                  organization: detailOrganization,
                  view: detailView,
                  onSelectView: selectOrganizationView,
                  onClose: closeOrganizationDetail,
                  agentOversightUiEnabled,
                  members: diagnosticMembers,
                  membersLoading: diagnosticLoading,
                  membersError: diagnosticError,
                  onReactivateMembership: handleReactivateMembership,
                })
              : null
          )
        : null,
      currentTab === 'admins' ? adminsSection : null,
      currentTab === 'symgov'
        ? createElement(ProtectedSymgovMembers, {
            members: protectedMembers,
            total: protectedMemberTotal,
            loading: protectedMemberLoading,
            error: protectedMemberError,
            onAdd: handleAddProtectedMember,
            onRoleChange: handleProtectedMemberRoleChange,
            onDeactivate: handleDeactivateProtectedMember,
          })
        : null,
      currentTab === 'symbols'
        ? createElement(
            Fragment,
            null,
            createElement(DemotionConsole, { protect }),
            createElement(PromotionReviewPanel, null)
          )
        : null,
      currentTab === 'agents'
        ? createElement(
            Fragment,
            null,
            createElement(EmbeddedPanel, null, createElement(AgentConfigurationSection, { protect, organizations: organizations || [] })),
            createElement(EmbeddedPanel, null, createElement(PlatformAgentFindingsDashboardSection, {}))
          )
        : null
    )
  );
}
