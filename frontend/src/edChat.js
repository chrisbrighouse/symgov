import { createElement } from 'react';

// Ed application guru, Stage 5. The chat surface for `POST /api/v1/ed/chat`.
//
// Reachability mirrors the API's pilot gate: `/auth/me` reports
// `capabilities.edEnabled` from the same check the route enforces, so the
// entry appears only where a question could be answered. The API remains the
// authority -- it answers 404 outside the pilot whatever the UI renders.
//
// The transcript lives in component state only (spec section 10.2): nothing
// is written to storage and nothing survives leaving the page.

export const ED_PROMPT_LIMIT = 1000;

// Spec section 3.3's example questions. Fixed prompts, never generated from
// the user's data.
export const ED_SUGGESTED_QUESTIONS = [
  'What does a project control, and how is it different from an organization?',
  'Which symbol sets are available in my current project?',
  'How do classification schemes relate to a symbol\'s discipline?',
  'Where did the ICS taxonomy come from?',
  'Why can I see this symbol but not edit it?',
  'What can an administrator do that a normal user cannot?',
];

export const ED_MODE_LABELS = {
  knowledge: 'From approved Symgov knowledge',
  live_data: 'From your live Symgov records',
  mixed: 'From approved knowledge and your live records',
  cannot_answer: 'No answer',
  blocked: 'Declined',
};

export const ED_SOURCE_LABELS = {
  approved_knowledge: 'Approved knowledge',
  live_record: 'Live record',
};

export function canUseEd(user) {
  if (!user) return false;
  if (user.session?.purpose !== 'application') return false;
  return user.capabilities?.edEnabled === true;
}

export function EdAccess({ auth, children }) {
  if (canUseEd(auth?.user)) return children;
  return createElement(
    'section',
    { className: 'workspace-empty-state' },
    createElement('p', { className: 'eyebrow' }, 'Ed'),
    createElement('h2', null, 'Ed is not available in this session yet.'),
    createElement(
      'p',
      null,
      'Ed is being piloted with selected organizations. Sign in to a pilot organization to use it.',
    ),
  );
}

function stringList(value, limit) {
  return Array.isArray(value)
    ? value.filter((item) => typeof item === 'string' && item.trim()).slice(0, limit)
    : [];
}

function objectList(value, limit) {
  return Array.isArray(value) ? value.filter((item) => item && typeof item === 'object').slice(0, limit) : [];
}

// The response contract is bounded server-side; this only guards rendering
// against a missing or malformed field.
export function normalizeEdResponse(payload) {
  const status = ['answered', 'refused', 'unavailable'].includes(payload?.status) ? payload.status : 'unavailable';
  const mode = Object.hasOwn(ED_MODE_LABELS, payload?.mode) ? payload.mode : 'cannot_answer';
  return {
    answer: typeof payload?.answer === 'string' && payload.answer.trim()
      ? payload.answer
      : 'Ed could not answer that.',
    status,
    mode,
    citations: objectList(payload?.citations, 12).map((citation) => ({
      sourceType: Object.hasOwn(ED_SOURCE_LABELS, citation.sourceType) ? citation.sourceType : 'live_record',
      title: String(citation.title || 'Source'),
      reference: String(citation.reference || ''),
      asOf: typeof citation.asOf === 'string' ? citation.asOf : null,
    })),
    context: {
      organization: typeof payload?.context?.organization === 'string' ? payload.context.organization : null,
      project: typeof payload?.context?.project === 'string' ? payload.context.project : null,
    },
    warnings: stringList(payload?.warnings, 8),
    suggestedFollowups: stringList(payload?.suggestedFollowups, 4),
    attributions: objectList(payload?.attributions, 2).map((item) => ({
      source: String(item.source || ''),
      attribution: String(item.attribution || ''),
      licenseCode: String(item.licenseCode || ''),
      licenseUrl: typeof item.licenseUrl === 'string' && /^https:\/\//.test(item.licenseUrl) ? item.licenseUrl : null,
      clarification: String(item.clarification || ''),
    })),
    knowledgeVersion: typeof payload?.knowledgeVersion === 'string' ? payload.knowledgeVersion : null,
  };
}

export function describeEdError(result) {
  switch (result?.status) {
    case 401:
      return 'Your session has ended. Sign in again to use Ed.';
    case 403:
      return 'Ed cannot be used in this session. Finish any required account step, then try again.';
    case 404:
      return 'Ed is not available for your organization yet.';
    case 422:
      return `Ed could not read that question. Keep it to ${ED_PROMPT_LIMIT.toLocaleString('en-GB')} characters or fewer.`;
    case 429:
      return 'You have asked Ed several questions in a short time. Wait a minute, then try again.';
    case 0:
    case undefined:
      return 'Ed could not be reached. Check your connection and try again.';
    default:
      return 'Ed is unavailable right now. Try again shortly.';
  }
}

// Operator-readable, unambiguous and in UTC, like the rest of the workspace.
export function formatEdTimestamp(value) {
  if (typeof value !== 'string') return null;
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return null;
  return `${parsed.toISOString().slice(0, 16).replace('T', ' ')} UTC`;
}

// A version is a `sha256:<hex>` digest; people need only enough to tell two
// apart.
export function shortVersion(value) {
  if (typeof value !== 'string') return null;
  const hex = value.replace(/^sha256:/, '');
  return /^[0-9a-f]{12,}$/.test(hex) ? hex.slice(0, 12) : null;
}
