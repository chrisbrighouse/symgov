import { createElement, useCallback, useEffect, useRef, useState } from 'react';
import { runWithStepUp } from './adminJourneys.js';
import {
  demoteGovernedSymbol,
  fetchDemotionImpactPreview,
  openOrganizationSymbolPromotionReview,
  requestJson,
  submitWorkspaceReviewDecision,
} from './api.js';
import { canMountOrganizationSymbolDrafts } from './projectContext.js';
import { PlatformOrganizationUsageDashboardSection } from './UsageDashboardSection.js';
import { PlatformOrganizationContributionSection } from './ContributionSection.js';

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

function OrganizationRow({ organization, onSuspend, onReactivate, onViewMembers, onViewUsage, onViewContributions }) {
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
        ),
    createElement('button', {
      type: 'button',
      onClick: () => onViewMembers(organization),
      'aria-label': `View members for ${organization.displayName}`,
    }, 'View members'),
    createElement('button', {
      type: 'button',
      onClick: () => onViewUsage(organization),
      'aria-label': `View usage dashboard for ${organization.displayName}`,
    }, 'View usage'),
    createElement('button', {
      type: 'button',
      onClick: () => onViewContributions(organization),
      'aria-label': `View contributions for ${organization.displayName}`,
    }, 'View contributions')
  );
}

function MemberDiagnostics({ organization, members, loading, error, onReactivate }) {
  if (!organization) return null;
  return createElement('section', { 'aria-labelledby': 'member-diagnostics-heading' },
    createElement('h3', { id: 'member-diagnostics-heading' }, `Member diagnostics: ${organization.displayName}`),
    error ? createElement(ErrorMessage, { message: error }) : null,
    loading ? createElement('p', { role: 'status' }, 'Loading member diagnostics…') : null,
    members && members.length === 0 ? createElement('p', null, 'No memberships found.') : null,
    members ? createElement('ul', { style: { listStyle: 'none', padding: 0 } },
      members.map((member) => createElement(DiagnosticMemberRow, {
        key: member.membershipId,
        member,
        onReactivate,
      }))) : null
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

  return createElement('li', null,
    createElement('strong', null, member.displayName),
    createElement('span', null, ` — ${member.email} — ${member.status} — ${member.baseRole}`),
    member.status === 'inactive' ? createElement('form', { onSubmit: handleSubmit },
      createElement('label', { htmlFor: `reactivation-reason-${member.membershipId}` },
        'Reactivation reason',
        createElement('input', {
          id: `reactivation-reason-${member.membershipId}`,
          value: reason,
          minLength: 10,
          maxLength: 1000,
          required: true,
          onChange: (event) => setReason(event.target.value),
        })),
      createElement('button', { type: 'submit', disabled: busy || reason.trim().length < 10 }, busy ? 'Reactivating…' : 'Reactivate membership'),
      error ? createElement(ErrorMessage, { message: error }) : null
    ) : null
  );
}

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

  return createElement('form', { onSubmit: handleSubmit, style: { display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
    createElement('label', { htmlFor: 'protected-member-user-id' }, 'Existing user ID',
      createElement('input', { id: 'protected-member-user-id', value: userId, required: true, onChange: (event) => setUserId(event.target.value) })),
    createElement('label', { htmlFor: 'protected-member-base-role' }, 'Base role',
      createElement('select', { id: 'protected-member-base-role', value: baseRole, onChange: (event) => setBaseRole(event.target.value) },
        createElement('option', { value: 'user' }, 'User'), createElement('option', { value: 'admin' }, 'Administrator'))),
    createElement('label', { htmlFor: 'protected-member-reason' }, 'Reason',
      createElement('input', { id: 'protected-member-reason', value: reason, minLength: 10, maxLength: 1000, required: true, onChange: (event) => setReason(event.target.value) })),
    createElement('button', { type: 'submit', disabled: busy || !userId || !validReason }, busy ? 'Adding…' : 'Add protected member'),
    error ? createElement(ErrorMessage, { message: error }) : null);
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
  if (member.status !== 'active') return createElement('li', null, `${member.displayName} — inactive`);
  const nextRole = member.baseRole === 'admin' ? 'user' : 'admin';
  const roleVerb = member.baseRole === 'admin' ? 'Demote' : 'Promote';
  return createElement('li', { style: { padding: '10px 0', borderBottom: '1px solid #e5e7eb' } },
    createElement('strong', null, member.displayName), createElement('span', null, ` — ${member.email} — ${member.baseRole}`),
    createElement('div', { style: { display: 'flex', flexWrap: 'wrap', gap: '8px', alignItems: 'flex-end' } },
      createElement('label', { htmlFor: `protected-member-mutation-reason-${member.membershipId}` }, 'Mutation reason',
        createElement('input', { id: `protected-member-mutation-reason-${member.membershipId}`, value: reason, minLength: 10, maxLength: 1000, required: true, onChange: (event) => setReason(event.target.value) })),
      createElement('button', { type: 'button', disabled: busy || !validReason, 'aria-label': `${roleVerb} ${member.displayName}`, onClick: () => mutate((value) => onRoleChange(member.membershipId, nextRole, value)) }, roleVerb),
      createElement('button', { type: 'button', disabled: busy || !validReason, 'aria-label': `Deactivate ${member.displayName}`, onClick: () => mutate((value) => onDeactivate(member.membershipId, value)) }, 'Deactivate')),
    error ? createElement(ErrorMessage, { message: error }) : null);
}

function ProtectedSymgovMembers({ members, total, loading, error, onAdd, onRoleChange, onDeactivate }) {
  return createElement('section', { 'aria-labelledby': 'protected-symgov-members-heading', style: { marginBottom: '32px' } },
    createElement('h2', { id: 'protected-symgov-members-heading' }, `Protected Symgov members (${total})`),
    error ? createElement(ErrorMessage, { message: error }) : null,
    members ? createElement(ProtectedMemberAddForm, { onAdd }) : null,
    loading ? createElement('p', { role: 'status' }, 'Loading protected members…') : null,
    members ? createElement('ul', { style: { listStyle: 'none', padding: 0 } }, members.map((member) => createElement(ProtectedMemberRow, { key: member.membershipId, member, onRoleChange, onDeactivate }))) : null);
}

export function CreateOrganizationForm({ onCreate }) {
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
      queueMicrotask(() => userIdInputRef.current?.focus());
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
        ref: userIdInputRef,
        onChange: (e) => setUserId(e.target.value),
        required: true,
        style: { display: 'block', marginTop: '4px' },
      })
    ),
    createElement('button', { type: 'submit', disabled: saving || !userId }, saving ? 'Granting…' : 'Grant platform admin'),
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
    'section',
    { 'aria-labelledby': 'demotion-console-heading', style: { marginBottom: '32px' } },
    createElement('h2', { id: 'demotion-console-heading', style: { marginBottom: '16px' } }, 'Demote a public symbol'),
    createElement('form', { onSubmit: loadPreview, style: { display: 'flex', gap: '8px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '16px' } },
      createElement('label', { htmlFor: 'demotion-symbol-id' },
        'Governed symbol ID',
        createElement('input', {
          id: 'demotion-symbol-id',
          type: 'text',
          value: symbolId,
          onChange: (event) => setSymbolId(event.target.value),
          required: true,
          style: { display: 'block', marginTop: '4px' },
        })
      ),
      createElement('button', { type: 'submit', disabled: previewLoading || !symbolId.trim() }, previewLoading ? 'Loading…' : 'Preview demotion impact')
    ),
    ErrorMessage({ message: previewError }),
    preview
      ? createElement(
          'div',
          { role: 'group', 'aria-labelledby': 'demotion-preview-heading', style: { border: '1px solid #e5e7eb', borderRadius: '6px', padding: '12px', marginBottom: '16px' } },
          createElement('h3', { id: 'demotion-preview-heading', style: { marginTop: 0 } }, `Impact preview: ${preview.governedSymbolId}`),
          createElement('p', null, preview.eligible ? 'Eligible for demotion.' : 'Not eligible for demotion.'),
          preview.reasons.length > 0
            ? createElement('ul', null, preview.reasons.map((r, i) => createElement('li', { key: i }, r)))
            : null,
          createElement('p', null, `Favourites referencing this symbol: ${preview.favouritesCount}`),
          preview.blockingOrganizationIds.length > 0
            ? createElement('p', null, `Blocked by references from ${preview.blockingOrganizationIds.length} other organization(s).`)
            : null,
          preview.eligible
            ? createElement(
                'div',
                { style: { marginTop: '12px' } },
                createElement('label', { htmlFor: 'demotion-reason' },
                  'Reason for demotion',
                  createElement('textarea', {
                    id: 'demotion-reason',
                    value: reason,
                    onChange: (event) => setReason(event.target.value),
                    required: true,
                    rows: 2,
                    style: { display: 'block', width: '100%', marginTop: '4px' },
                  })
                ),
                ErrorMessage({ message: demoteError }),
                createElement('button', {
                  type: 'button',
                  onClick: handleDemote,
                  disabled: demoting || !validReason,
                  style: { color: '#dc2626', marginTop: '8px' },
                  'aria-label': `Demote governed symbol ${preview.governedSymbolId}`,
                }, demoting ? 'Demoting…' : 'Demote symbol')
              )
            : null
        )
      : null,
    demoteResult
      ? createElement('p', { role: 'status' },
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
    'section',
    { 'aria-labelledby': 'promotion-review-heading', style: { marginBottom: '32px' } },
    createElement('h2', { id: 'promotion-review-heading', style: { marginBottom: '8px' } }, 'Review a promotion request'),
    createElement('p', { style: { color: '#6b7280', fontSize: '0.875rem' } },
      'Enter the governed symbol ID and promotion request ID provided by the submitting organization’s admin. Accept-only: reject/changes-requested handling is not yet built.'),
    createElement('form', { onSubmit: handleOpenReview, style: { display: 'flex', gap: '8px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '16px' } },
      createElement('label', { htmlFor: 'promotion-review-symbol-id' },
        'Governed symbol ID',
        createElement('input', {
          id: 'promotion-review-symbol-id',
          type: 'text',
          value: symbolId,
          onChange: (event) => setSymbolId(event.target.value),
          required: true,
          style: { display: 'block', marginTop: '4px' },
        })
      ),
      createElement('label', { htmlFor: 'promotion-review-request-id' },
        'Promotion request ID',
        createElement('input', {
          id: 'promotion-review-request-id',
          type: 'text',
          value: requestId,
          onChange: (event) => setRequestId(event.target.value),
          required: true,
          style: { display: 'block', marginTop: '4px' },
        })
      ),
      createElement('button', { type: 'submit', disabled: busy || !symbolId.trim() || !requestId.trim() }, busy ? 'Working…' : 'Open for review')
    ),
    ErrorMessage({ message: error }),
    request
      ? createElement(
          'div',
          { role: 'group', 'aria-labelledby': 'promotion-review-request-heading', style: { border: '1px solid #e5e7eb', borderRadius: '6px', padding: '12px' } },
          createElement('h3', { id: 'promotion-review-request-heading', style: { marginTop: 0 } }, `Promotion request: ${request.id}`),
          createElement('p', null, `Status: ${request.status}`),
          createElement('p', null, `Reason given: ${request.reason}`),
          decided
            ? createElement('p', { role: 'status' }, 'Accepted. The symbol has been published.')
            : createElement('button', {
                type: 'button',
                onClick: handleAccept,
                disabled: busy || !request.reviewCaseId,
                'aria-label': `Accept promotion request ${request.id}`,
              }, busy ? 'Working…' : 'Accept')
        )
      : null
  );
}

export function PlatformAdminPage({ auth }) {
  const [admins, setAdmins] = useState(null);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [stepUpPin, setStepUpPin] = useState('');
  const PAGE_SIZE = 50;

  const [organizations, setOrganizations] = useState(null);
  const [orgTotal, setOrgTotal] = useState(0);
  const [orgPage, setOrgPage] = useState(1);
  const [orgLoading, setOrgLoading] = useState(false);
  const [orgError, setOrgError] = useState('');
  const ORG_PAGE_SIZE = 50;
  const [diagnosticOrganization, setDiagnosticOrganization] = useState(null);
  const [diagnosticMembers, setDiagnosticMembers] = useState(null);
  const [diagnosticLoading, setDiagnosticLoading] = useState(false);
  const [diagnosticError, setDiagnosticError] = useState('');
  const [usageOrganization, setUsageOrganization] = useState(null);
  const [contributionOrganization, setContributionOrganization] = useState(null);
  const [protectedMembers, setProtectedMembers] = useState(null);
  const [protectedMemberTotal, setProtectedMemberTotal] = useState(0);
  const [protectedMemberLoading, setProtectedMemberLoading] = useState(false);
  const [protectedMemberError, setProtectedMemberError] = useState('');
  const protect = useCallback((operation) => runWithStepUp({
    pin: stepUpPin,
    operation,
    reauthenticate: (pin) => auth.reauthenticate({ pin }),
    clearPin: () => setStepUpPin(''),
  }), [auth, stepUpPin]);

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

  async function loadMemberDiagnostics(organization) {
    setDiagnosticOrganization(organization);
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
  }

  function loadUsageDashboard(organization) {
    setUsageOrganization(organization);
  }

  function loadContributionDashboard(organization) {
    setContributionOrganization(organization);
  }

  async function handleReactivateMembership(membershipId, reason) {
    await protect(() => apiPost(`/platform/memberships/${membershipId}/reactivate`, { reason }));
    await loadMemberDiagnostics(diagnosticOrganization);
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
      { style: { padding: '24px' } },
      createElement('h1', null, 'Platform administration'),
      ErrorMessage({ message: error })
    );
  }

  if (!admins) {
    return createElement('section', { style: { padding: '24px' } }, createElement('p', { role: 'status' }, 'Loading…'));
  }

  const totalPages = Math.ceil(total / PAGE_SIZE);
  const orgTotalPages = Math.ceil(orgTotal / ORG_PAGE_SIZE);
  const symbolPromotionUiEnabled = canMountOrganizationSymbolDrafts(auth) && auth?.user?.capabilities?.platformAdminEnabled === true;

  return createElement(
    'section',
    { style: { padding: '24px', maxWidth: '800px' } },
    createElement('h1', { style: { marginBottom: '24px' } }, 'Platform administration'),
    createElement('label', { htmlFor: 'platform-step-up-pin' },
      'PIN for protected changes',
      createElement('input', {
        id: 'platform-step-up-pin', type: 'password', inputMode: 'numeric',
        autoComplete: 'off', value: stepUpPin, maxLength: 4,
        onChange: (event) => setStepUpPin(event.target.value),
      })
    ),
    createElement(ProtectedSymgovMembers, {
      members: protectedMembers,
      total: protectedMemberTotal,
      loading: protectedMemberLoading,
      error: protectedMemberError,
      onAdd: handleAddProtectedMember,
      onRoleChange: handleProtectedMemberRoleChange,
      onDeactivate: handleDeactivateProtectedMember,
    }),
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
                onViewMembers: loadMemberDiagnostics,
                onViewUsage: loadUsageDashboard,
                onViewContributions: loadContributionDashboard,
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
        : null,
      createElement(MemberDiagnostics, {
        organization: diagnosticOrganization,
        members: diagnosticMembers,
        loading: diagnosticLoading,
        error: diagnosticError,
        onReactivate: handleReactivateMembership,
      }),
      usageOrganization
        ? createElement(PlatformOrganizationUsageDashboardSection, {
            organizationId: usageOrganization.id,
            organizationLabel: usageOrganization.displayName,
          })
        : null,
      contributionOrganization
        ? createElement(PlatformOrganizationContributionSection, {
            organizationId: contributionOrganization.id,
            organizationLabel: contributionOrganization.displayName,
          })
        : null
    ),
    symbolPromotionUiEnabled ? createElement(DemotionConsole, { protect }) : null,
    symbolPromotionUiEnabled ? createElement(PromotionReviewPanel, null) : null,
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
