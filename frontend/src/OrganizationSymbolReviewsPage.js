import { createElement } from 'react';

import { OrganizationSymbolReviewQueuePanel } from './OrganizationSymbolReviewQueuePanel.js';
import { canReviewOrganizationSymbols } from './projectContext.js';

export function OrganizationSymbolReviewsPage({ auth }) {
  if (!canReviewOrganizationSymbols(auth)) {
    return createElement(
      'section',
      { className: 'workspace-empty-state' },
      createElement('p', { className: 'eyebrow' }, 'Access controlled'),
      createElement('h2', null, 'Organization symbol review is not available for this session.'),
    );
  }
  // `auth` reaches the panel for decision Q10 only: the governed semantic
  // state it can show is behind the semantic review API's *platform* role
  // boundary and its default-off flag, which are a different axis from the
  // organization capability that gates this page.
  return createElement(OrganizationSymbolReviewQueuePanel, { auth });
}
