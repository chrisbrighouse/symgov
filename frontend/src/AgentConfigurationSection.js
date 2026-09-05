import { createElement, useCallback, useEffect, useState } from 'react';

import { createPlatformAgentConfiguration, fetchPlatformAgentConfigurations, updatePlatformAgentConfiguration } from './api.js';

function ErrorMessage({ message }) {
  if (!message) return null;
  return createElement(
    'p',
    { role: 'alert', style: { color: '#dc2626', background: '#fee2e2', border: '1px solid #fca5a5', borderRadius: '6px', padding: '8px 12px', marginBottom: '12px', fontSize: '0.875rem' } },
    message
  );
}

const LOGICAL_AGENT_NAME_LABELS = {
  organization_steward: 'Organization Steward',
  platform_governance: 'Platform Governance',
};

function ConfigurationRow({ config, organizations, protect, onChanged }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const organization = config.scopeId ? (organizations || []).find((org) => org.id === config.scopeId) : null;

  const toggleEnabled = useCallback(async () => {
    setBusy(true);
    setError('');
    try {
      await protect(() => updatePlatformAgentConfiguration(config.id, { enabled: !config.enabled }));
      await onChanged();
    } catch (err) {
      setError(err.message || 'Update failed.');
    } finally {
      setBusy(false);
    }
  }, [config, protect, onChanged]);

  return createElement(
    'tr',
    null,
    createElement('td', null, LOGICAL_AGENT_NAME_LABELS[config.logicalAgentName] || config.logicalAgentName),
    createElement('td', null, config.scopeType === 'platform' ? 'Platform' : (organization ? organization.displayName : config.scopeId)),
    createElement('td', null, config.enabled ? 'Enabled' : 'Disabled'),
    createElement('td', null, config.modelAlias || '—'),
    createElement(
      'td',
      null,
      createElement('button', { type: 'button', disabled: busy, onClick: toggleEnabled }, busy ? 'Saving…' : (config.enabled ? 'Disable' : 'Enable')),
      ErrorMessage({ message: error })
    )
  );
}

function CreateConfigurationForm({ organizations, protect, onCreated }) {
  const [logicalAgentName, setLogicalAgentName] = useState('organization_steward');
  const [scopeType, setScopeType] = useState('organization');
  const [organizationId, setOrganizationId] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const effectiveScopeType = logicalAgentName === 'platform_governance' ? 'platform' : scopeType;
  const validOrganization = effectiveScopeType !== 'organization' || Boolean(organizationId);

  const handleSubmit = useCallback(
    async (event) => {
      event.preventDefault();
      if (!validOrganization) return;
      setBusy(true);
      setError('');
      try {
        await protect(() =>
          createPlatformAgentConfiguration({
            logicalAgentName,
            scopeType: effectiveScopeType,
            scopeId: effectiveScopeType === 'organization' ? organizationId : null,
            enabled: false,
          })
        );
        setOrganizationId('');
        await onCreated();
      } catch (err) {
        setError(err.message || 'Create failed.');
      } finally {
        setBusy(false);
      }
    },
    [logicalAgentName, effectiveScopeType, organizationId, validOrganization, protect, onCreated]
  );

  return createElement(
    'form',
    { onSubmit: handleSubmit, style: { display: 'flex', gap: '12px', alignItems: 'flex-end', flexWrap: 'wrap', marginBottom: '16px' } },
    createElement(
      'div',
      null,
      createElement('label', { htmlFor: 'agent-config-capability' }, 'Capability'),
      createElement(
        'select',
        {
          id: 'agent-config-capability',
          value: logicalAgentName,
          onChange: (event) => setLogicalAgentName(event.target.value),
        },
        createElement('option', { value: 'organization_steward' }, 'Organization Steward'),
        createElement('option', { value: 'platform_governance' }, 'Platform Governance')
      )
    ),
    logicalAgentName === 'organization_steward'
      ? createElement(
          'div',
          null,
          createElement('label', { htmlFor: 'agent-config-organization' }, 'Organization'),
          createElement(
            'select',
            {
              id: 'agent-config-organization',
              value: organizationId,
              required: true,
              onChange: (event) => setOrganizationId(event.target.value),
            },
            createElement('option', { value: '' }, 'Select an organization…'),
            (organizations || []).map((org) => createElement('option', { key: org.id, value: org.id }, org.displayName))
          )
        )
      : null,
    createElement('button', { type: 'submit', disabled: busy || !validOrganization }, busy ? 'Creating…' : 'Add configuration'),
    ErrorMessage({ message: error })
  );
}

export function AgentConfigurationSection({ protect, organizations }) {
  const [configs, setConfigs] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const data = await fetchPlatformAgentConfigurations();
      setConfigs(data.items || []);
    } catch (err) {
      setConfigs([]);
      setError(err.message || 'Agent configuration load failed.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  return createElement(
    'section',
    { 'aria-labelledby': 'agent-configuration-heading', style: { marginBottom: '32px' } },
    createElement('h2', { id: 'agent-configuration-heading', style: { marginBottom: '16px' } }, 'Agent configuration'),
    createElement(CreateConfigurationForm, { organizations, protect, onCreated: load }),
    loading ? createElement('p', { role: 'status' }, 'Loading agent configurations…') : null,
    ErrorMessage({ message: error }),
    !loading && configs.length === 0
      ? createElement('p', { style: { color: '#6b7280' } }, 'No agent configurations exist yet.')
      : null,
    !loading && configs.length > 0
      ? createElement(
          'table',
          null,
          createElement(
            'thead',
            null,
            createElement(
              'tr',
              null,
              createElement('th', null, 'Capability'),
              createElement('th', null, 'Scope'),
              createElement('th', null, 'Status'),
              createElement('th', null, 'Model alias'),
              createElement('th', null, 'Actions')
            )
          ),
          createElement(
            'tbody',
            null,
            configs.map((config) => createElement(ConfigurationRow, { key: config.id, config, organizations, protect, onChanged: load }))
          )
        )
      : null
  );
}
