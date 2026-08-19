import { createElement, useState } from 'react';
import { Link } from 'react-router-dom';

export function OrganizationIcon({ organization }) {
  if (organization.logoUrl) {
    return createElement('img', {
      src: organization.logoUrl,
      alt: '',
      className: 'org-selection-logo',
      'aria-hidden': 'true'
    });
  }

  const fallback = (organization.displayName || organization.code || '?').charAt(0).toUpperCase();
  return createElement(
    'span',
    { className: 'org-selection-fallback', 'aria-hidden': 'true' },
    fallback
  );
}

export function OrganizationSelectionScreen({
  challenge,
  onSelect,
  isSubmitting = false,
  message = ''
}) {
  if (!challenge || !challenge.choices) {
    return null;
  }

  return createElement(
    'div',
    { className: 'org-selection-frame' },
    createElement(
      'header',
      { className: 'org-selection-header' },
      createElement('p', { className: 'eyebrow' }, 'Identity verified'),
      createElement('h2', null, 'Select an organization'),
      createElement(
        'p',
        { className: 'org-selection-intro' },
        'Multiple organizations are eligible for this account. Choose one to continue.'
      )
    ),
    message && createElement('div', { className: 'org-selection-error', role: 'alert' }, message),
    createElement(
      'div',
      { className: 'org-selection-list', role: 'list' },
      challenge.choices.map((choice) =>
        createElement(
          'div',
          { key: choice.organizationId, className: 'org-selection-item', role: 'listitem' },
          createElement(
            'button',
            {
              type: 'button',
              className: 'org-selection-button',
              disabled: isSubmitting,
              onClick: () => onSelect(choice.organizationId),
              'aria-label': `Select ${choice.displayName}`
            },
            createElement(
              'div',
              { className: 'org-selection-identity' },
              createElement(OrganizationIcon, { organization: choice }),
              createElement(
                'div',
                { className: 'org-selection-details' },
                createElement('span', { className: 'org-selection-name' }, choice.displayName),
                createElement('span', { className: 'org-selection-code' }, choice.code)
              )
            ),
            createElement(
              'span',
              { className: 'org-selection-action', 'aria-hidden': 'true' },
              'Select →'
            )
          )
        )
      )
    ),
    challenge.total > challenge.choices.length && createElement(
      'div',
      { className: 'org-selection-pagination' },
      createElement('p', null, `Showing ${challenge.choices.length} of ${challenge.total} organizations. Contact support if you do not see your organization.`)
    ),
    createElement(
      'footer',
      { className: 'org-selection-footer' },
      createElement(
        'button',
        {
          type: 'button',
          className: 'link-button',
          onClick: () => (typeof window !== 'undefined' && window.location.reload())
        },
        'Cancel and return to sign-in'
      )
    )
  );
}

export default function OrganizationSelectionPage({ auth }) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');

  if (!auth.challenge) {
    return createElement(
      'section',
      { className: 'page-frame mode-auth workspace-empty-state', 'aria-labelledby': 'selection-unavailable-title' },
      createElement('p', { className: 'eyebrow' }, 'Sign-in required'),
      createElement('h2', { id: 'selection-unavailable-title' }, 'Organization selection is no longer available'),
      createElement(
        'p',
        { className: 'form-message error', role: 'alert' },
        auth.message || 'Organization selection challenge is invalid or unavailable.'
      ),
      createElement(Link, { to: '/login', className: 'primary-button' }, 'Return to sign-in')
    );
  }

  const handleSelect = async (organizationId) => {
    setIsSubmitting(true);
    setError('');
    try {
      const result = await auth.selectOrganization({
        token: auth.challenge.token,
        organizationId
      });
      if (!result.ok) {
        setError(result.message);
      }
    } catch (err) {
      setError(err.message || 'An unexpected error occurred.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return createElement(
    'div',
    { className: 'page-frame mode-auth' },
    createElement(OrganizationSelectionScreen, {
      challenge: auth.challenge,
      onSelect: handleSelect,
      isSubmitting,
      message: error || auth.message
    })
  );
}
