import { createElement, useCallback, useEffect, useState } from 'react';

import { fetchOrganizationContributions, fetchPlatformOrganizationContributions } from './api.js';
import { describeContributionBadgeType } from './contributionBadgeLabels.js';

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

function ContributionDashboardBody({ headingId, title, fetchSummary }) {
  const [summary, setSummary] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchSummary();
      setSummary(data);
    } catch (err) {
      setSummary(null);
      setError(
        err.status === 404
          ? 'The contribution dashboard is not available right now.'
          : err.message || 'Contribution stats load failed.'
      );
    } finally {
      setLoading(false);
    }
  }, [fetchSummary]);

  useEffect(() => {
    load();
  }, [load]);

  return createElement(
    'section',
    { 'aria-labelledby': headingId, style: { marginBottom: '32px' } },
    createElement('h2', { id: headingId, style: { marginBottom: '16px' } }, title),
    loading ? createElement('p', { role: 'status' }, 'Loading contribution stats…') : null,
    ErrorMessage({ message: error }),
    !loading && !error && summary
      ? createElement(
          'div',
          null,
          createElement(
            'dl',
            { style: { display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '4px 16px', margin: '0 0 16px' } },
            createElement('dt', null, 'Accepted contributions'),
            createElement('dd', null, String(summary.acceptedContributionCount)),
            createElement('dt', null, 'Reversed contributions'),
            createElement('dd', null, String(summary.reversedContributionCount))
          ),
          summary.badges && summary.badges.length > 0
            ? createElement(
                'ul',
                { style: { listStyle: 'none', padding: 0, margin: 0, display: 'flex', gap: '8px', flexWrap: 'wrap' } },
                summary.badges.map((badge) =>
                  createElement(
                    'li',
                    {
                      key: badge.badgeType,
                      style: {
                        fontSize: '0.75rem',
                        background: '#dcfce7',
                        color: '#166534',
                        padding: '2px 10px',
                        borderRadius: '9999px',
                      },
                    },
                    describeContributionBadgeType(badge.badgeType)
                  )
                )
              )
            : createElement('p', { style: { color: '#6b7280' } }, 'No badges earned yet.')
        )
      : null
  );
}

export function OrgContributionSection({ fetchSummary = fetchOrganizationContributions }) {
  return createElement(ContributionDashboardBody, {
    headingId: 'org-contribution-dashboard-heading',
    title: 'Contributions',
    fetchSummary,
  });
}

export function PlatformOrganizationContributionSection({ organizationId, organizationLabel, fetchSummary }) {
  const bound = useCallback(
    () => (fetchSummary || fetchPlatformOrganizationContributions)(organizationId),
    [organizationId, fetchSummary]
  );
  return createElement(ContributionDashboardBody, {
    headingId: 'platform-org-contribution-dashboard-heading',
    title: `Contributions: ${organizationLabel || organizationId}`,
    fetchSummary: bound,
  });
}
