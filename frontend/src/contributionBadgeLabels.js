// Human-readable labels for OrganizationBadge's frozen badge_type vocabulary
// (WP9.5's two computed badges). Kept in one shared lookup, mirroring
// productUsageEventLabels.js's own convention, so no later package or audit
// needs to hunt for display copy scattered inline.
const CONTRIBUTION_BADGE_LABELS = {
  first_contribution: 'First Contribution',
  contributor_organization: 'Contributor Organization',
};

function titleCaseFallback(badgeType) {
  return badgeType
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export function describeContributionBadgeType(badgeType) {
  return CONTRIBUTION_BADGE_LABELS[badgeType] || titleCaseFallback(String(badgeType || ''));
}

export { CONTRIBUTION_BADGE_LABELS };
