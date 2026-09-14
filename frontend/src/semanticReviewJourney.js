import { createElement } from 'react';

// SM-P1-01 WP1.4. The capability gate for the semantic review surface.
//
// Deliberately *not* `adminJourneys.hasActiveOrganizationContext`: decision
// Q3 exposes `semanticReviewEnabled` as platform governance, not as an
// organization entitlement, so a personal-mode session is legitimate here and
// the router's own section 14.2 scope predicate decides what it may see
// (public symbols only). Gating the surface on an organization here would be
// a second, stricter boundary than the one the API actually enforces.
//
// The role set matches WP1.2/WP1.3's `require_any_role({"admin", "reviewer"})`
// exactly. This predicate does not replace that check: the API is
// authoritative and answers 404 for a dormant flag whatever the UI believes.

export const SEMANTIC_REVIEW_ROLES = ['admin', 'reviewer'];

export function canAccessSemanticReview(user) {
  if (!user) return false;
  if (user.session?.purpose !== 'application') return false;
  const roles = Array.isArray(user.roles) ? user.roles : [];
  if (!SEMANTIC_REVIEW_ROLES.some((role) => roles.includes(role))) return false;
  return user.capabilities?.semanticReviewEnabled === true;
}

export function SemanticReviewAccess({ auth, children }) {
  if (canAccessSemanticReview(auth?.user)) return children;
  return createElement(
    'section',
    { className: 'workspace-empty-state' },
    createElement('p', { className: 'eyebrow' }, 'Access controlled'),
    createElement('h2', null, 'You do not have access to this area.'),
    createElement('p', null, `Required role: ${SEMANTIC_REVIEW_ROLES.join(' or ')}`),
  );
}
