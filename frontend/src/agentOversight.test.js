/**
 * Tests for Stage 10 WP10.5/10.6 Organization Steward / Platform Governance
 * UI: OrgAgentFindingsDashboardSection, PlatformAgentFindingsDashboardSection,
 * AgentConfigurationSection. Full end-to-end coverage (real routes, real
 * Postgres) lives in test_wp104_agent_routes_postgresql.py -- these are unit
 * tests for the React components against a mocked fetch, mirroring
 * organizationAdmin.test.js/platformAdmin.test.js's own harness style.
 */

import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { createElement } from 'react';
import { act, create } from 'react-test-renderer';

import { OrgAgentFindingsDashboardSection, PlatformAgentFindingsDashboardSection } from './AgentFindingsDashboardSection.js';
import { AgentConfigurationSection } from './AgentConfigurationSection.js';

globalThis.IS_REACT_ACT_ENVIRONMENT = true;

function jsonResponse(body, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? 'OK' : 'Error',
    json: async () => body,
  };
}

const mockFinding = {
  id: 'finding-1',
  agentConfigId: 'config-1',
  logicalAgentName: 'organization_steward',
  severity: 'high',
  findingType: 'reviewer_coverage_gap',
  entityType: 'organization',
  entityId: 'org-1',
  summary: 'No active reviewer capability exists in this organization.',
  evidence: { activeSubmissionCount: 1 },
  policyVersion: 'org_steward_v1',
  status: 'open',
  firstSeenAt: '2026-09-05T00:00:00Z',
  lastSeenAt: '2026-09-05T00:00:00Z',
  acknowledgedAt: null,
  dismissedAt: null,
  dismissReason: null,
  resolvedAt: null,
  supersededByFindingId: null,
  assigneeUserId: null,
  issueReference: null,
};

function findAllByType(root, type) {
  try {
    return root.findAllByType(type);
  } catch {
    return [];
  }
}

describe('OrgAgentFindingsDashboardSection', () => {
  it('lists findings and runs Organization Steward on demand', async () => {
    let runCalled = false;
    globalThis.fetch = async (url, opts) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/org/me/agent-findings') return jsonResponse({ items: [mockFinding] });
      if (path === '/api/v1/org/me/agents/organization-steward/run' && opts?.method === 'POST') {
        runCalled = true;
        return jsonResponse({ touchedFindingIds: ['finding-1'] });
      }
      return jsonResponse({ detail: 'not found' }, 404);
    };

    let renderer;
    await act(async () => {
      renderer = create(createElement(OrgAgentFindingsDashboardSection));
    });

    const text = JSON.stringify(renderer.toJSON());
    assert.ok(text.includes('Reviewer coverage gap'));
    assert.ok(text.includes('No active reviewer capability'));

    const runButton = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Run Organization Steward now')[0];
    await act(async () => {
      runButton.props.onClick();
    });
    assert.equal(runCalled, true);

    await act(async () => renderer.unmount());
  });

  it('acknowledges then dismisses a finding with a reason', async () => {
    let currentFinding = { ...mockFinding };
    let dismissReasonSent = null;
    globalThis.fetch = async (url, opts) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/org/me/agent-findings') return jsonResponse({ items: [currentFinding] });
      if (path === '/api/v1/org/me/agent-findings/finding-1/acknowledge') {
        currentFinding = { ...currentFinding, status: 'acknowledged', acknowledgedAt: '2026-09-05T01:00:00Z' };
        return jsonResponse(currentFinding);
      }
      if (path === '/api/v1/org/me/agent-findings/finding-1/dismiss') {
        const body = JSON.parse(opts.body);
        dismissReasonSent = body.reason;
        currentFinding = { ...currentFinding, status: 'dismissed', dismissReason: body.reason };
        return jsonResponse(currentFinding);
      }
      return jsonResponse({ detail: 'not found' }, 404);
    };

    let renderer;
    await act(async () => {
      renderer = create(createElement(OrgAgentFindingsDashboardSection));
    });

    const ackButton = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Acknowledge')[0];
    await act(async () => {
      ackButton.props.onClick();
    });
    assert.equal(currentFinding.status, 'acknowledged');

    const dismissTrigger = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Dismiss…')[0];
    await act(async () => {
      dismissTrigger.props.onClick();
    });
    const reasonInput = renderer.root.findByProps({ id: 'dismiss-reason' });
    await act(async () => {
      reasonInput.props.onChange({ target: { value: 'Reviewer capability granted out of band.' } });
    });
    const form = renderer.root.findAll((node) => node.type === 'form')[0];
    await act(async () => {
      await form.props.onSubmit({ preventDefault: () => {} });
    });

    assert.equal(dismissReasonSent, 'Reviewer capability granted out of band.');
    assert.equal(currentFinding.status, 'dismissed');

    await act(async () => renderer.unmount());
  });
});

describe('PlatformAgentFindingsDashboardSection', () => {
  it('runs Platform Governance when no organizationId is given', async () => {
    let calledPath = null;
    globalThis.fetch = async (url, opts) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/agent-findings') return jsonResponse({ items: [] });
      if (opts?.method === 'POST') {
        calledPath = path;
        return jsonResponse({ touchedFindingIds: [] });
      }
      return jsonResponse({ detail: 'not found' }, 404);
    };

    let renderer;
    await act(async () => {
      renderer = create(createElement(PlatformAgentFindingsDashboardSection));
    });

    const runButton = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Run Platform Governance now')[0];
    await act(async () => {
      runButton.props.onClick();
    });
    assert.equal(calledPath, '/api/v1/platform/agents/platform-governance/run');

    await act(async () => renderer.unmount());
  });

  it('scopes to one organization and offers escalation', async () => {
    let queriedUrl = null;
    let escalatePayload = null;
    globalThis.fetch = async (url, opts) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/agent-findings') {
        queriedUrl = url;
        return jsonResponse({ items: [mockFinding] });
      }
      if (path === '/api/v1/platform/agent-findings/finding-1/escalate') {
        escalatePayload = JSON.parse(opts.body);
        return jsonResponse({ ...mockFinding, issueReference: escalatePayload.issueReference });
      }
      return jsonResponse({ detail: 'not found' }, 404);
    };

    let renderer;
    await act(async () => {
      renderer = create(createElement(PlatformAgentFindingsDashboardSection, { organizationId: 'org-1', organizationLabel: 'ACME' }));
    });

    assert.ok(String(queriedUrl).includes('organizationId=org-1'));

    const escalateTrigger = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Escalate…')[0];
    await act(async () => {
      escalateTrigger.props.onClick();
    });
    const issueInput = renderer.root.findByProps({ id: 'escalate-issue-reference' });
    await act(async () => {
      issueInput.props.onChange({ target: { value: 'ED-9999' } });
    });
    const form = renderer.root.findAll((node) => node.type === 'form')[0];
    await act(async () => {
      await form.props.onSubmit({ preventDefault: () => {} });
    });

    assert.equal(escalatePayload.issueReference, 'ED-9999');

    await act(async () => renderer.unmount());
  });
});

describe('AgentConfigurationSection', () => {
  it('lists configurations and toggles enablement through the protect() step-up wrapper', async () => {
    let currentConfig = { id: 'config-1', logicalAgentName: 'platform_governance', scopeType: 'platform', scopeId: null, enabled: false, modelAlias: null, allowedCapabilities: [], updatedAt: '2026-09-05T00:00:00Z' };
    let protectCalls = 0;
    globalThis.fetch = async (url) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/agent-configurations') return jsonResponse({ items: [currentConfig] });
      return jsonResponse({ detail: 'not found' }, 404);
    };
    const protect = async (operation) => {
      protectCalls += 1;
      currentConfig = { ...currentConfig, enabled: true };
      return operation();
    };

    let renderer;
    await act(async () => {
      renderer = create(createElement(AgentConfigurationSection, { protect, organizations: [] }));
    });

    const text = JSON.stringify(renderer.toJSON());
    assert.ok(text.includes('Platform Governance'));
    assert.ok(text.includes('Disabled'));

    const toggleButton = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Enable')[0];
    await act(async () => {
      toggleButton.props.onClick();
    });

    assert.equal(protectCalls, 1);

    await act(async () => renderer.unmount());
  });

  it('requires an organization selection before creating an Organization Steward configuration', async () => {
    globalThis.fetch = async (url) => {
      const path = new URL(url, 'http://test').pathname;
      if (path === '/api/v1/platform/agent-configurations') return jsonResponse({ items: [] });
      return jsonResponse({ detail: 'not found' }, 404);
    };
    const protect = async (operation) => operation();

    let renderer;
    await act(async () => {
      renderer = create(createElement(AgentConfigurationSection, { protect, organizations: [{ id: 'org-1', displayName: 'ACME Corp' }] }));
    });

    const submitButton = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Add configuration')[0];
    assert.equal(submitButton.props.disabled, true);

    const orgSelect = renderer.root.findByProps({ id: 'agent-config-organization' });
    await act(async () => {
      orgSelect.props.onChange({ target: { value: 'org-1' } });
    });

    const submitButtonAfter = renderer.root.findAll((node) => node.type === 'button' && node.props.children === 'Add configuration')[0];
    assert.equal(submitButtonAfter.props.disabled, false);

    await act(async () => renderer.unmount());
  });
});
