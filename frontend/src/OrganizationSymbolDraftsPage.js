import { createElement } from 'react';

import { OrganizationSymbolDraftsPanel } from './OrganizationSymbolDraftsPanel.js';
import { canCreateOrganizationSymbolDrafts, canMountOrganizationSymbolDrafts } from './projectContext.js';

export function OrganizationSymbolDraftsPage({ auth }) {
  if (!canMountOrganizationSymbolDrafts(auth)) {
    return createElement(
      'section',
      { className: 'workspace-empty-state' },
      createElement('p', { className: 'eyebrow' }, 'Access controlled'),
      createElement('h2', null, 'Organization symbol drafts are not available for this session.'),
    );
  }
  return createElement(OrganizationSymbolDraftsPanel, { canCreate: canCreateOrganizationSymbolDrafts(auth) });
}
