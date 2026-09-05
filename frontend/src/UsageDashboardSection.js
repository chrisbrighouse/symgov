import { createElement, useCallback, useEffect, useState } from 'react';

import { fetchOrganizationUsageSummary, fetchPlatformOrganizationUsageSummary } from './api.js';
import { describeProductUsageEventType } from './productUsageEventLabels.js';

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

function EventTypeTile({ item }) {
  const mostRecentDay = item.days.length > 0 ? item.days[item.days.length - 1] : null;
  return createElement(
    'div',
    { style: { border: '1px solid #e5e7eb', borderRadius: '6px', padding: '12px 16px' } },
    createElement('h3', { style: { margin: '0 0 8px', fontSize: '0.95rem' } }, describeProductUsageEventType(item.eventType)),
    createElement(
      'dl',
      { style: { display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '4px 16px', margin: 0 } },
      createElement('dt', null, 'Total events (window)'),
      createElement('dd', null, String(item.totalEventCount)),
      createElement('dt', null, 'Most recent day with data'),
      createElement(
        'dd',
        null,
        mostRecentDay
          ? `${mostRecentDay.date} · ${mostRecentDay.eventCount} event(s), ${mostRecentDay.distinctUserCount} user(s)`
          : 'None in window'
      ),
      createElement('dt', null, 'Days suppressed (below reporting threshold)'),
      createElement('dd', null, String(item.suppressedDayCount))
    )
  );
}

function UsageDashboardBody({ headingId, title, fetchSummary }) {
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
          ? 'The usage dashboard is not available right now.'
          : err.message || 'Usage summary load failed.'
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
    loading ? createElement('p', { role: 'status' }, 'Loading usage summary…') : null,
    ErrorMessage({ message: error }),
    !loading && !error && summary
      ? createElement(
          'div',
          null,
          createElement(
            'p',
            { style: { color: '#6b7280', fontSize: '0.875rem', marginBottom: '16px' } },
            `Window: ${summary.since} to ${summary.until}.`
          ),
          summary.eventTypes.length === 0
            ? createElement('p', { style: { color: '#6b7280' } }, 'No usage recorded in this window.')
            : createElement(
                'div',
                { style: { display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))', gap: '12px' } },
                summary.eventTypes.map((item) => createElement(EventTypeTile, { key: item.eventType, item }))
              )
        )
      : null
  );
}

export function OrgUsageDashboardSection({ fetchSummary = fetchOrganizationUsageSummary }) {
  return createElement(UsageDashboardBody, {
    headingId: 'org-usage-dashboard-heading',
    title: 'Usage dashboard',
    fetchSummary,
  });
}

export function PlatformOrganizationUsageDashboardSection({ organizationId, organizationLabel, fetchSummary }) {
  const bound = useCallback(
    () => (fetchSummary || fetchPlatformOrganizationUsageSummary)(organizationId),
    [organizationId, fetchSummary]
  );
  return createElement(UsageDashboardBody, {
    headingId: 'platform-org-usage-dashboard-heading',
    title: `Usage dashboard: ${organizationLabel || organizationId}`,
    fetchSummary: bound,
  });
}
