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
  return createElement(OrganizationSymbolReviewQueuePanel, null);
}
