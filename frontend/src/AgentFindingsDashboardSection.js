import { createElement, useCallback, useEffect, useState } from 'react';

import {
  acknowledgeOrganizationAgentFinding,
  acknowledgePlatformAgentFinding,
  dismissOrganizationAgentFinding,
  dismissPlatformAgentFinding,
  escalatePlatformAgentFinding,
  fetchOrganizationAgentFindings,
  fetchPlatformAgentFindings,
  resolveOrganizationAgentFinding,
  resolvePlatformAgentFinding,
  runOrganizationSteward,
  runPlatformGovernance,
  runPlatformOrganizationSteward,
} from './api.js';
import { describeAgentFindingSeverity, describeAgentFindingStatus, describeAgentFindingType } from './agentFindingLabels.js';

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

const SEVERITY_ACCENT = { low: '#6b7280', medium: '#d97706', high: '#dc2626', critical: '#991b1b' };

function DismissForm({ onDismiss, busy }) {
  const [reason, setReason] = useState('');
  const [expanded, setExpanded] = useState(false);
  const validReason = reason.trim().length > 0;

  if (!expanded) {
    return createElement('button', { type: 'button', onClick: () => setExpanded(true), disabled: busy }, 'Dismiss…');
  }

  return createElement(
    'form',
    {
      onSubmit: async (event) => {
        event.preventDefault();
        if (!validReason) return;
        await onDismiss(reason.trim());
        setReason('');
        setExpanded(false);
      },
      style: { display: 'flex', gap: '8px', alignItems: 'flex-end', marginTop: '4px' },
    },
    createElement(
      'div',
      null,
      createElement('label', { htmlFor: 'dismiss-reason' }, 'Dismissal reason'),
      createElement('input', {
        id: 'dismiss-reason',
        value: reason,
        required: true,
        onChange: (event) => setReason(event.target.value),
      })
    ),
    createElement('button', { type: 'submit', disabled: busy || !validReason }, busy ? 'Dismissing…' : 'Confirm dismiss'),
    createElement('button', { type: 'button', onClick: () => setExpanded(false), disabled: busy }, 'Cancel')
  );
}

function EscalateForm({ onEscalate, busy }) {
  const [issueReference, setIssueReference] = useState('');
  const [expanded, setExpanded] = useState(false);

  if (!expanded) {
    return createElement('button', { type: 'button', onClick: () => setExpanded(true), disabled: busy }, 'Escalate…');
  }

  return createElement(
    'form',
    {
      onSubmit: async (event) => {
        event.preventDefault();
        await onEscalate({ issueReference: issueReference.trim() || null });
        setIssueReference('');
        setExpanded(false);
      },
      style: { display: 'flex', gap: '8px', alignItems: 'flex-end', marginTop: '4px' },
    },
    createElement(
      'div',
      null,
      createElement('label', { htmlFor: 'escalate-issue-reference' }, 'Issue reference (optional)'),
      createElement('input', {
        id: 'escalate-issue-reference',
        value: issueReference,
        onChange: (event) => setIssueReference(event.target.value),
      })
    ),
    createElement('button', { type: 'submit', disabled: busy }, busy ? 'Escalating…' : 'Confirm escalate'),
    createElement('button', { type: 'button', onClick: () => setExpanded(false), disabled: busy }, 'Cancel')
  );
}

function FindingCard({ finding, actions }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const actionable = finding.status === 'open' || finding.status === 'acknowledged';

  const run = useCallback(
    async (fn) => {
      setBusy(true);
      setError('');
      try {
        await fn();
      } catch (err) {
        setError(err.message || 'Action failed.');
      } finally {
        setBusy(false);
      }
    },
    []
  );

  return createElement(
    'div',
    {
      style: {
        border: '1px solid #e5e7eb',
        borderLeft: `4px solid ${SEVERITY_ACCENT[finding.severity] || '#6b7280'}`,
        borderRadius: '6px',
        padding: '12px 16px',
        marginBottom: '12px',
      },
    },
    createElement(
      'div',
      { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', flexWrap: 'wrap', gap: '8px' } },
      createElement('h3', { style: { margin: 0, fontSize: '0.95rem' } }, describeAgentFindingType(finding.findingType)),
      createElement(
        'span',
        { style: { fontSize: '0.8rem', color: SEVERITY_ACCENT[finding.severity] || '#6b7280', fontWeight: 600 } },
        `${describeAgentFindingSeverity(finding.severity)} · ${describeAgentFindingStatus(finding.status)}`
      )
    ),
    createElement('p', { style: { margin: '8px 0' } }, finding.summary),
    createElement(
      'dl',
      { style: { display: 'grid', gridTemplateColumns: 'max-content 1fr', gap: '4px 16px', margin: '0 0 8px', fontSize: '0.8rem', color: '#4b5563' } },
      createElement('dt', null, 'First seen'),
      createElement('dd', null, new Date(finding.firstSeenAt).toLocaleString()),
      createElement('dt', null, 'Last seen'),
      createElement('dd', null, new Date(finding.lastSeenAt).toLocaleString()),
      createElement('dt', null, 'Policy version'),
      createElement('dd', null, finding.policyVersion),
      finding.issueReference
        ? createElement('dt', null, 'Issue reference')
        : null,
      finding.issueReference ? createElement('dd', null, finding.issueReference) : null,
      finding.dismissReason ? createElement('dt', null, 'Dismissal reason') : null,
      finding.dismissReason ? createElement('dd', null, finding.dismissReason) : null
    ),
    ErrorMessage({ message: error }),
    actionable
      ? createElement(
          'div',
          { style: { display: 'flex', gap: '8px', flexWrap: 'wrap', alignItems: 'flex-start' } },
          finding.status === 'open'
            ? createElement('button', { type: 'button', disabled: busy, onClick: () => run(() => actions.acknowledge(finding.id)) }, 'Acknowledge')
            : null,
          createElement('button', { type: 'button', disabled: busy, onClick: () => run(() => actions.resolve(finding.id)) }, 'Mark resolved'),
          createElement(DismissForm, { busy, onDismiss: (reason) => run(() => actions.dismiss(finding.id, reason)) }),
          actions.escalate
            ? createElement(EscalateForm, { busy, onEscalate: (payload) => run(() => actions.escalate(finding.id, payload)) })
            : null
        )
      : null
  );
}

function FindingsDashboardBody({ headingId, title, fetchFindings, runLabel, onRun, actions }) {
  const [findings, setFindings] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchFindings();
      setFindings(data.items || []);
    } catch (err) {
      setFindings([]);
      setError(err.message || 'Findings load failed.');
    } finally {
      setLoading(false);
    }
  }, [fetchFindings]);

  useEffect(() => {
    load();
  }, [load]);

  const handleRun = useCallback(async () => {
    setRunning(true);
    setError('');
    try {
      await onRun();
      await load();
    } catch (err) {
      setError(err.message || 'Run failed.');
    } finally {
      setRunning(false);
    }
  }, [onRun, load]);

  const wrappedActions = {
    acknowledge: async (id) => { await actions.acknowledge(id); await load(); },
    dismiss: async (id, reason) => { await actions.dismiss(id, reason); await load(); },
    resolve: async (id) => { await actions.resolve(id); await load(); },
    escalate: actions.escalate ? async (id, payload) => { await actions.escalate(id, payload); await load(); } : null,
  };

  const activeFindings = findings.filter((f) => f.status === 'open' || f.status === 'acknowledged');
  const otherFindings = findings.filter((f) => f.status !== 'open' && f.status !== 'acknowledged');

  return createElement(
    'section',
    { 'aria-labelledby': headingId, style: { marginBottom: '32px' } },
    createElement(
      'div',
      { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', marginBottom: '16px', flexWrap: 'wrap', gap: '8px' } },
      createElement('h2', { id: headingId, style: { margin: 0 } }, title),
      onRun ? createElement('button', { type: 'button', onClick: handleRun, disabled: running }, running ? 'Running…' : runLabel) : null
    ),
    loading ? createElement('p', { role: 'status' }, 'Loading findings…') : null,
    ErrorMessage({ message: error }),
    !loading && findings.length === 0 ? createElement('p', { style: { color: '#6b7280' } }, 'No findings recorded.') : null,
    !loading && activeFindings.length > 0
      ? createElement('div', null, activeFindings.map((finding) => createElement(FindingCard, { key: finding.id, finding, actions: wrappedActions })))
      : null,
    !loading && otherFindings.length > 0
      ? createElement(
          'details',
          { style: { marginTop: '16px' } },
          createElement('summary', null, `Dismissed, resolved, and superseded findings (${otherFindings.length})`),
          otherFindings.map((finding) => createElement(FindingCard, { key: finding.id, finding, actions: wrappedActions }))
        )
      : null
  );
}

export function OrgAgentFindingsDashboardSection() {
  return createElement(FindingsDashboardBody, {
    headingId: 'org-agent-findings-heading',
    title: 'Organization Steward findings',
    fetchFindings: fetchOrganizationAgentFindings,
    runLabel: 'Run Organization Steward now',
    onRun: runOrganizationSteward,
    actions: {
      acknowledge: acknowledgeOrganizationAgentFinding,
      dismiss: dismissOrganizationAgentFinding,
      resolve: resolveOrganizationAgentFinding,
    },
  });
}

export function PlatformAgentFindingsDashboardSection({ organizationId, organizationLabel } = {}) {
  const fetchFindings = useCallback(
    () => fetchPlatformAgentFindings(organizationId ? { organizationId } : {}),
    [organizationId]
  );
  const onRun = organizationId
    ? () => runPlatformOrganizationSteward(organizationId)
    : () => runPlatformGovernance();
  return createElement(FindingsDashboardBody, {
    headingId: 'platform-agent-findings-heading',
    title: organizationId
      ? `Organization Steward findings: ${organizationLabel || organizationId}`
      : 'Platform Governance findings',
    fetchFindings,
    runLabel: organizationId ? 'Run Organization Steward now' : 'Run Platform Governance now',
    onRun,
    actions: {
      acknowledge: acknowledgePlatformAgentFinding,
      dismiss: dismissPlatformAgentFinding,
      resolve: resolvePlatformAgentFinding,
      escalate: escalatePlatformAgentFinding,
    },
  });
}
