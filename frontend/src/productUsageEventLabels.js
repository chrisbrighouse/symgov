// Human-readable labels for ProductUsageEvent's frozen event_type vocabulary
// (WP9.1's seven browse-facing values plus WP9.2's nineteen governance-lifecycle
// values). Kept in one shared lookup per the Stage 9 plan doc so no later
// package or audit needs to hunt for display copy scattered inline.
const PRODUCT_USAGE_EVENT_LABELS = {
  personal_session_started: 'Personal sessions started',
  organization_selected: 'Organization selected',
  context_resolved: 'Context resolved',
  set_selected: 'Symbol set selected',
  symbol_previewed: 'Symbol previews',
  symbol_downloaded: 'Symbol downloads',
  favorite_changed: 'Favorites changed',
  organization_review_submitted: 'Organization review submitted',
  organization_review_decided: 'Organization review decided',
  organization_wide_changed: 'Organization-wide visibility changed',
  publication_submitted: 'Public promotion submitted',
  publication_decided: 'Public promotion decided',
  public_symbol_demoted: 'Public symbol demoted',
  project_created: 'Projects created',
  project_updated: 'Projects updated',
  project_archived: 'Projects archived',
  project_selected: 'Project selected',
  set_created: 'Symbol sets created',
  set_updated: 'Symbol sets updated',
  set_archived: 'Symbol sets archived',
  set_project_availability_changed: 'Symbol set project availability changed',
  organization_role_changed: 'Member role changed',
  platform_admin_assigned: 'Platform admin assigned',
  platform_admin_removed: 'Platform admin removed',
  organization_icon_uploaded: 'Organization icon uploaded',
  organization_icon_removed: 'Organization icon removed',
};

function titleCaseFallback(eventType) {
  return eventType
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export function describeProductUsageEventType(eventType) {
  return PRODUCT_USAGE_EVENT_LABELS[eventType] || titleCaseFallback(String(eventType || ''));
}

export { PRODUCT_USAGE_EVENT_LABELS };
