export const MIN_REASON_LENGTH = 10;

/** One line an operator can read at a glance: what state the plan is in. */
export function describePlanStatus(plan) {
  if (!plan) return '';
  if (!plan.metered) return 'No plan: this organization is not limited.';
  if (plan.status === 'expired') return `Expired on ${plan.expiresOn}. New members cannot be added until it is renewed.`;
  return `Active until ${plan.expiresOn}.`;
}

export function describeSeats(plan) {
  if (!plan || !plan.metered) return plan ? `${plan.seatsInUse} in use (no limit)` : '';
  const free = plan.seatLimit - plan.seatsInUse;
  const base = `${plan.seatsInUse} of ${plan.seatLimit} in use`;
  if (free < 0) return `${base} (${-free} over the limit)`;
  return `${base} (${free} free)`;
}

function wholeNumber(value) {
  const text = String(value ?? '').trim();
  return /^\d+$/.test(text) ? Number(text) : null;
}

/** Returns the request body, or `{ error }` naming what to fix. */
export function buildPlanChange({ seatLimit, months, reason }, plan) {
  const seats = wholeNumber(seatLimit);
  const term = wholeNumber(months);
  if (seats === null || seats < 1 || seats > 100000) return { error: 'Seats must be a whole number from 1 to 100000.' };
  if (term === null || term < 1 || term > 120) return { error: 'Term must be a whole number of months from 1 to 120.' };
  if (String(reason || '').trim().length < MIN_REASON_LENGTH) return { error: `Give a reason of at least ${MIN_REASON_LENGTH} characters.` };
  if (plan?.metered && seats < plan.seatsInUse) {
    return { error: `${plan.seatsInUse} seats are in use. Deactivate members before lowering the limit to ${seats}.` };
  }
  return { body: { seatLimit: seats, months: term, reason: String(reason).trim() } };
}

export function buildRenewal({ months, reason }) {
  const term = wholeNumber(months);
  if (term === null || term < 1 || term > 120) return { error: 'Term must be a whole number of months from 1 to 120.' };
  if (String(reason || '').trim().length < MIN_REASON_LENGTH) return { error: `Give a reason of at least ${MIN_REASON_LENGTH} characters.` };
  return { body: { months: term, reason: String(reason).trim() } };
}

export function describeEventChange(event) {
  const seats = event.previousSeatLimit === null || event.previousSeatLimit === undefined
    ? `${event.newSeatLimit} seats`
    : event.previousSeatLimit === event.newSeatLimit
      ? `${event.newSeatLimit} seats`
      : `${event.previousSeatLimit} → ${event.newSeatLimit} seats`;
  const expiry = event.previousExpiresOn
    ? `expires ${event.previousExpiresOn} → ${event.newExpiresOn}`
    : `expires ${event.newExpiresOn}`;
  return `${seats}, ${expiry}`;
}

/**
 * What each plan role lets a member do. Each line is the effect in the product,
 * not the internal role name: "reviewer" here is governance review in Symgov's
 * Reviews, which is a different thing from the Reviewer setting an organization
 * admin gives for the organization's own private symbols.
 */
export const PLAN_ROLE_INFO = {
  submitter: {
    label: 'Submissions',
    summary: 'Submit symbols and files to Symgov for governance review.',
  },
  reviewer: {
    label: 'Governance review',
    summary: 'Decide review and rights-review cases and triage promotion requests in Reviews.',
  },
  integrator: {
    label: 'Catalog developer',
    summary: 'Use the Catalog developer hub: create an API key, and use the OpenAPI spec, sandbox and integration assistant.',
  },
};

export const PLAN_ROLES_SCOPE_NOTE =
  'Plan roles apply to this member only while they work as this organization and only while its plan is active.';

export const ORGANIZATION_CAPABILITY_NOTE =
  'They are separate from the Contributor and Reviewer settings an organization admin gives for the organization’s own private symbols.';

export function describeRole(role) {
  return PLAN_ROLE_INFO[role] || { label: role, summary: '' };
}

/** `{ [membershipId]: Set<role> }` from the member-roles listing. */
export function rolesByMembership(items) {
  const grouped = {};
  for (const item of items || []) {
    (grouped[item.membershipId] ||= new Set()).add(item.role);
  }
  return grouped;
}

/** Why plan roles are not changing anyone's access right now, or '' when they are. */
export function describeRolesEffect(plan, rolesEnabled) {
  if (!rolesEnabled) {
    return 'Not switched on yet: assignments are saved but do not change anyone’s access until plan roles are activated.';
  }
  if (plan?.status === 'expired') {
    return 'The plan has expired, so assigned roles give no access until it is renewed.';
  }
  return '';
}
