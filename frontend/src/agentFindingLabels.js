// Human-readable labels for AgentFinding's frozen finding_type vocabulary
// (Stage 10 WP10.1's seven shipped v1 slugs). cross_tenant_authorization_failure
// and unresolved_governance_exception are deliberately not in this list --
// neither exists in the schema's own CheckConstraint yet, see the Stage 10
// plan doc §4 Q2.
const AGENT_FINDING_TYPE_LABELS = {
  reviewer_coverage_gap: 'Reviewer coverage gap',
  review_backlog_stale: 'Review backlog stale',
  project_health_issue: 'Project health issue',
  symbol_set_health_issue: 'Symbol set health issue',
  unresolved_reference: 'Unresolved reference',
  platform_admin_continuity_risk: 'Platform Admin continuity risk',
  duplicate_organization_suspected: 'Duplicate organization suspected',
};

const AGENT_FINDING_SEVERITY_LABELS = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  critical: 'Critical',
};

const AGENT_FINDING_STATUS_LABELS = {
  open: 'Open',
  acknowledged: 'Acknowledged',
  dismissed: 'Dismissed',
  resolved: 'Resolved',
  superseded: 'Superseded',
};

function titleCaseFallback(value) {
  return String(value || '')
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

export function describeAgentFindingType(findingType) {
  return AGENT_FINDING_TYPE_LABELS[findingType] || titleCaseFallback(findingType);
}

export function describeAgentFindingSeverity(severity) {
  return AGENT_FINDING_SEVERITY_LABELS[severity] || titleCaseFallback(severity);
}

export function describeAgentFindingStatus(status) {
  return AGENT_FINDING_STATUS_LABELS[status] || titleCaseFallback(status);
}

export { AGENT_FINDING_TYPE_LABELS, AGENT_FINDING_SEVERITY_LABELS, AGENT_FINDING_STATUS_LABELS };
