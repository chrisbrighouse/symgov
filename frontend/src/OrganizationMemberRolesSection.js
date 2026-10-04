import { createElement, useCallback, useEffect, useState } from 'react';

import {
  fetchPlatformOrganizationMemberRoles,
  fetchPlatformOrganizationMembers,
  grantPlatformMemberRole,
  revokePlatformMemberRole,
} from './api.js';
import {
  MIN_REASON_LENGTH,
  ORGANIZATION_CAPABILITY_NOTE,
  PLAN_ROLES_SCOPE_NOTE,
  describeRole,
  describeRolesEffect,
  rolesByMembership,
} from './organizationSubscription.js';

function RoleLegend({ roles }) {
  return createElement(
    'dl',
    { className: 'platform-admin-plan-summary', 'aria-label': 'What each plan role allows' },
    roles.flatMap((role) => {
      const info = describeRole(role);
      return [
        createElement('dt', { key: `${role}-t` }, info.label),
        createElement('dd', { key: `${role}-d` }, info.summary),
      ];
    })
  );
}

function MemberRow({ member, roles, assigned, busy, onToggle }) {
  return createElement(
    'li',
    { className: 'platform-admin-diagnostic-member' },
    createElement(
      'div',
      { className: 'platform-admin-diagnostic-identity' },
      createElement('strong', { className: 'platform-admin-principal-name' }, member.displayName),
      createElement('span', { className: 'platform-admin-principal-email' }, `${member.email} · ${member.baseRole}`)
    ),
    createElement(
      'div',
      { role: 'group', 'aria-label': `Plan roles for ${member.email}`, className: 'platform-admin-cell-stack' },
      roles.map((role) => {
        const info = describeRole(role);
        const id = `plan-role-${member.membershipId}-${role}`;
        return createElement(
          'label',
          { key: role, htmlFor: id, className: 'field-inline' },
          createElement('input', {
            id,
            type: 'checkbox',
            checked: assigned.has(role),
            disabled: busy,
            'aria-label': `${info.label} for ${member.email}`,
            'aria-describedby': `plan-role-help-${role}`,
            onChange: (event) => onToggle(member, role, event.target.checked),
          }),
          ' ',
          info.label
        );
      })
    )
  );
}

/**
 * Who in this organization holds which plan role. Changes go through the page's
 * step-up wrapper and need a recorded reason, shared by whatever is ticked next.
 */
export function OrganizationMemberRolesSection({ organization, plan, protect }) {
  const [members, setMembers] = useState(null);
  const [listing, setListing] = useState(null);
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const [memberList, roleList] = await Promise.all([
        fetchPlatformOrganizationMembers(organization.id),
        fetchPlatformOrganizationMemberRoles(organization.id),
      ]);
      setMembers((memberList.items || []).filter((member) => member.status === 'active'));
      setListing(roleList);
    } catch (err) {
      setError(err.message);
    }
  }, [organization.id]);

  useEffect(() => {
    setMembers(null);
    setListing(null);
    setNotice('');
    load();
  }, [load]);

  async function toggle(member, role, wanted) {
    setError('');
    setNotice('');
    const trimmed = reason.trim();
    if (trimmed.length < MIN_REASON_LENGTH) {
      setError(`Give a reason of at least ${MIN_REASON_LENGTH} characters first.`);
      return;
    }
    const info = describeRole(role);
    const verb = wanted ? 'Give' : 'Remove';
    const prep = wanted ? 'to' : 'from';
    if (!window.confirm(`${verb} “${info.label}” ${prep} ${member.email}?`)) return;
    setBusy(true);
    try {
      const change = wanted ? grantPlatformMemberRole : revokePlatformMemberRole;
      setListing(await protect(() => change(organization.id, member.membershipId, role, trimmed)));
      setNotice(wanted ? `${info.label} given to ${member.email}.` : `${info.label} removed from ${member.email}.`);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  const roles = listing?.assignableRoles || [];
  const assigned = rolesByMembership(listing?.items);
  const effect = listing ? describeRolesEffect(plan, listing.rolesEnabled) : '';

  return createElement(
    'section',
    { 'aria-labelledby': 'plan-roles-heading', className: 'platform-admin-detail-section' },
    createElement('h4', { id: 'plan-roles-heading' }, 'Plan roles'),
    createElement('p', { className: 'platform-admin-help' }, `${PLAN_ROLES_SCOPE_NOTE} ${ORGANIZATION_CAPABILITY_NOTE}`),
    error ? createElement('p', { role: 'alert', className: 'form-message error platform-admin-message' }, error) : null,
    notice ? createElement('p', { role: 'status', className: 'form-message success platform-admin-message' }, notice) : null,
    effect ? createElement('p', { role: 'status', className: 'form-message platform-admin-message' }, effect) : null,
    !listing && !error ? createElement('p', { role: 'status', className: 'platform-admin-help' }, 'Loading plan roles…') : null,
    listing ? createElement(RoleLegend, { roles }) : null,
    listing
      ? roles.map((role) => createElement('span', { key: role, id: `plan-role-help-${role}`, hidden: true }, describeRole(role).summary))
      : null,
    listing
      ? createElement(
          'label',
          { htmlFor: 'plan-roles-reason', className: 'field' },
          createElement('span', null, 'Reason for the next change (recorded)'),
          createElement('input', {
            id: 'plan-roles-reason',
            value: reason,
            minLength: MIN_REASON_LENGTH,
            maxLength: 1000,
            onChange: (event) => setReason(event.target.value),
          })
        )
      : null,
    members && members.length === 0 ? createElement('p', { className: 'platform-admin-help' }, 'This organization has no active members.') : null,
    members && members.length > 0 && listing
      ? createElement(
          'ul',
          { className: 'platform-admin-diagnostic-list' },
          members.map((member) => createElement(MemberRow, {
            key: member.membershipId,
            member,
            roles,
            assigned: assigned[member.membershipId] || new Set(),
            busy,
            onToggle: toggle,
          }))
        )
      : null
  );
}
