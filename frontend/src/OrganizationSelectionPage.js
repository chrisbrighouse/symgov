import { createElement, useEffect, useRef, useState } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { destinationFromRouterState } from './catalogRoutes.js';
import { selectOrganization, startOrganizationSwitch } from './organizationSession.js';

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
  message = '',
  eyebrow = 'Identity verified',
  cancelLabel = 'Cancel and return to sign-in',
  onCancel = () => (typeof window !== 'undefined' && window.location.reload())
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
      createElement('p', { className: 'eyebrow' }, eyebrow),
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
          onClick: onCancel
        },
        cancelLabel
      )
    )
  );
}

export default function OrganizationSelectionPage({ auth }) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState('');
  const location = useLocation();
  const navigate = useNavigate();
  const destination = destinationFromRouterState(location.state);

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
        if (result.status === 401) {
          navigate('/login', { replace: true });
        } else {
          setError(result.message);
        }
      } else if (result.session?.user) {
        const target = result.session.user.mustChangePin ? '/change-pin' : destination;
        navigate(
          target,
          result.session.user.mustChangePin
            ? { replace: true, state: { from: destination } }
            : { replace: true }
        );
      } else {
        setError('Organization selection could not establish a session.');
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

export function OrganizationSwitchPage({ auth }) {
  const navigate = useNavigate();
  const [challenge, setChallenge] = useState(null);
  const [loadError, setLoadError] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const startedRef = useRef(false);

  useEffect(() => {
    if (startedRef.current) return undefined;
    startedRef.current = true;
    let cancelled = false;
    startOrganizationSwitch().then((result) => {
      if (cancelled) return;
      if (result.ok && result.challenge) {
        setChallenge(result.challenge);
      } else {
        setLoadError(result.message || 'Organization choices could not be loaded.');
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const handleSelect = async (organizationId) => {
    setIsSubmitting(true);
    setError('');
    try {
      const result = await selectOrganization({ token: challenge.token, organizationId });
      if (!result.ok) {
        setError(result.message || 'Organization could not be selected.');
        return;
      }
      await auth.refresh();
      navigate('/standards', { replace: true });
    } catch (err) {
      setError(err.message || 'An unexpected error occurred.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const goBack = () => navigate('/standards', { replace: true });

  if (loadError) {
    return createElement(
      'section',
      { className: 'page-frame workspace-empty-state', 'aria-labelledby': 'switch-unavailable-title' },
      createElement('p', { className: 'eyebrow' }, 'Switch organization'),
      createElement('h2', { id: 'switch-unavailable-title' }, 'Organizations cannot be switched right now'),
      createElement('p', { className: 'form-message error', role: 'alert' }, loadError),
      createElement('button', { type: 'button', className: 'primary-button', onClick: goBack }, 'Back to Standards')
    );
  }

  if (!challenge) {
    return createElement(
      'section',
      { className: 'workspace-empty-state' },
      createElement('p', { className: 'eyebrow' }, 'Switch organization'),
      createElement('h2', null, 'Loading your organizations…')
    );
  }

  return createElement(
    'div',
    { className: 'page-frame mode-auth' },
    createElement(OrganizationSelectionScreen, {
      challenge,
      onSelect: handleSelect,
      isSubmitting,
      message: error,
      eyebrow: 'Switch organization',
      cancelLabel: 'Cancel and stay in the current organization',
      onCancel: goBack
    })
  );
}
