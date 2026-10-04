import { createElement, useCallback, useEffect, useState } from 'react';

import {
  fetchPlatformOrganizationSubscription,
  renewPlatformOrganizationSubscription,
  savePlatformOrganizationSubscription,
} from './api.js';
import {
  MIN_REASON_LENGTH,
  buildPlanChange,
  buildRenewal,
  describeEventChange,
  describePlanStatus,
  describeSeats,
} from './organizationSubscription.js';
import { formatReviewTimestamp } from './SemanticReviewPage.js';

function Message({ kind, children }) {
  if (!children) return null;
  return createElement(
    'p',
    { role: kind === 'error' ? 'alert' : 'status', className: `form-message ${kind} platform-admin-message` },
    children
  );
}

function ReasonField({ id, value, onChange }) {
  return createElement(
    'label',
    { htmlFor: id, className: 'field' },
    createElement('span', null, 'Reason (recorded in the plan history)'),
    createElement('input', {
      id,
      value,
      minLength: MIN_REASON_LENGTH,
      maxLength: 1000,
      required: true,
      onChange: (event) => onChange(event.target.value),
    })
  );
}

function NumberField({ id, label, value, onChange, min, max }) {
  return createElement(
    'label',
    { htmlFor: id, className: 'field' },
    createElement('span', null, label),
    createElement('input', {
      id,
      type: 'number',
      inputMode: 'numeric',
      min,
      max,
      step: 1,
      value,
      required: true,
      onChange: (event) => onChange(event.target.value),
    })
  );
}

function RenewForm({ organization, plan, disabled, onSubmit }) {
  const [months, setMonths] = useState('12');
  const [reason, setReason] = useState('');
  return createElement(
    'form',
    {
      className: 'platform-admin-inline-form',
      'aria-label': `Renew plan for ${organization.displayName}`,
      onSubmit: async (event) => {
        event.preventDefault();
        if (await onSubmit({ months, reason })) setReason('');
      },
    },
    createElement('h4', null, 'Renew'),
    createElement(
      'p',
      { className: 'platform-admin-help' },
      plan.status === 'expired'
        ? 'The plan has lapsed, so renewing starts a new term from today and keeps the seat limit.'
        : 'Adds time after the current expiry, so renewing early loses nothing. The seat limit is kept.'
    ),
    createElement(NumberField, { id: 'plan-renew-months', label: 'Months to add', value: months, onChange: setMonths, min: 1, max: 120 }),
    createElement(ReasonField, { id: 'plan-renew-reason', value: reason, onChange: setReason }),
    createElement('button', { type: 'submit', className: 'action-button primary', disabled }, 'Renew plan')
  );
}

function ChangeForm({ organization, plan, disabled, onSubmit }) {
  const [seatLimit, setSeatLimit] = useState(plan.metered ? String(plan.seatLimit) : '25');
  const [months, setMonths] = useState('12');
  const [reason, setReason] = useState('');
  return createElement(
    'form',
    {
      className: 'platform-admin-inline-form',
      'aria-label': `${plan.metered ? 'Change' : 'Create'} plan for ${organization.displayName}`,
      onSubmit: async (event) => {
        event.preventDefault();
        if (await onSubmit({ seatLimit, months, reason })) setReason('');
      },
    },
    createElement('h4', null, plan.metered ? 'Change seats or restart the term' : 'Create a plan'),
    createElement(
      'p',
      { className: 'platform-admin-help' },
      'The term runs from today for the months given, replacing the current expiry.'
    ),
    createElement(NumberField, { id: 'plan-seat-limit', label: 'Seats', value: seatLimit, onChange: setSeatLimit, min: 1, max: 100000 }),
    createElement(NumberField, { id: 'plan-term-months', label: 'Term in months from today', value: months, onChange: setMonths, min: 1, max: 120 }),
    createElement(ReasonField, { id: 'plan-change-reason', value: reason, onChange: setReason }),
    createElement('button', { type: 'submit', className: 'action-button primary', disabled }, plan.metered ? 'Save plan' : 'Create plan')
  );
}

function PlanHistory({ events }) {
  if (!events || events.length === 0) return null;
  return createElement(
    'section',
    { 'aria-labelledby': 'plan-history-heading' },
    createElement('h4', { id: 'plan-history-heading' }, 'Plan history'),
    createElement(
      'ul',
      { className: 'platform-admin-diagnostic-list' },
      events.map((event, index) => createElement(
        'li',
        { key: `${event.createdAt}-${index}`, className: 'platform-admin-diagnostic-member' },
        createElement(
          'div',
          { className: 'platform-admin-diagnostic-identity' },
          createElement('strong', { className: 'platform-admin-principal-name' }, `${event.action === 'created' ? 'Created' : 'Updated'}: ${describeEventChange(event)}`),
          createElement('span', { className: 'platform-admin-principal-email' }, `${formatReviewTimestamp(event.createdAt)} · ${event.actorEmail || 'system'}`)
        ),
        createElement('span', { className: 'platform-admin-help' }, event.reason || '')
      ))
    )
  );
}

/**
 * One organization's seat plan: what it is, how full it is, and the two ways a
 * platform admin changes it. Both changes are step-up protected, so they run
 * through the page's `protect` wrapper, and both need a recorded reason.
 */
export function OrganizationSubscriptionPanel({ organization, protect }) {
  const [plan, setPlan] = useState(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setPlan(await fetchPlatformOrganizationSubscription(organization.id));
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }, [organization.id]);

  useEffect(() => {
    setPlan(null);
    setNotice('');
    load();
  }, [load]);

  async function apply(built, perform, confirmText, doneText) {
    setError('');
    setNotice('');
    if (built.error) {
      setError(built.error);
      return false;
    }
    if (!window.confirm(confirmText)) return false;
    setBusy(true);
    try {
      setPlan(await protect(() => perform(built.body)));
      setNotice(doneText);
      return true;
    } catch (err) {
      setError(err.message);
      return false;
    } finally {
      setBusy(false);
    }
  }

  const renew = (values) => apply(
    buildRenewal(values),
    (body) => renewPlatformOrganizationSubscription(organization.id, body),
    `Renew the plan for ${organization.displayName} by ${values.months} months?`,
    'Plan renewed.'
  );

  const change = (values) => apply(
    buildPlanChange(values, plan),
    (body) => savePlatformOrganizationSubscription(organization.id, body),
    `Set ${organization.displayName} to ${values.seatLimit} seats for ${values.months} months from today?`,
    'Plan saved.'
  );

  return createElement(
    'section',
    { 'aria-labelledby': 'organization-plan-heading', className: 'platform-admin-detail-section' },
    createElement('h3', { id: 'organization-plan-heading' }, `Plan: ${organization.displayName}`),
    loading ? createElement('p', { role: 'status', className: 'platform-admin-help' }, 'Loading plan…') : null,
    createElement(Message, { kind: 'error' }, error),
    createElement(Message, { kind: 'success' }, notice),
    plan
      ? createElement(
          'div',
          null,
          createElement('p', { className: plan.status === 'expired' ? 'form-message error' : '' }, describePlanStatus(plan)),
          createElement(
            'dl',
            { className: 'platform-admin-plan-summary' },
            createElement('dt', null, 'Seats'),
            createElement('dd', null, describeSeats(plan)),
            plan.metered ? createElement('dt', null, 'Term') : null,
            plan.metered ? createElement('dd', null, `${plan.startedOn} to ${plan.expiresOn}`) : null
          ),
          organization.isProtected
            ? createElement('p', { className: 'platform-admin-help' }, 'The platform organization has no plan and cannot be given one.')
            : createElement(
                'div',
                null,
                plan.metered ? createElement(RenewForm, { organization, plan, disabled: busy, onSubmit: renew }) : null,
                createElement(ChangeForm, { key: plan.version ?? 'none', organization, plan, disabled: busy, onSubmit: change })
              ),
          createElement(PlanHistory, { events: plan.events })
        )
      : null
  );
}
