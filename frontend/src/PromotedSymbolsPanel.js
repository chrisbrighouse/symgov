import { createElement, useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { listPromotedOrganizationSymbols } from './api.js';
import { routeForCatalogSymbol } from './catalogRoutes.js';

// Unified submission & review WP7 (D1 kept, FR14 amended). Promotion makes the
// same symbol public, so it leaves the private list. This shows the
// organization where it went: Public, with its S-number, and the organization
// it was promoted from. Read-only; any change now goes through public review.

const DEFAULT_API = { listPromoted: listPromotedOrganizationSymbols };

function formatWhen(value) {
  return value ? new Date(value).toLocaleDateString() : '';
}

export function PromotedSymbolsPanel({ api = DEFAULT_API }) {
  const [items, setItems] = useState(null);
  const [error, setError] = useState('');

  useEffect(() => {
    api.listPromoted()
      .then((data) => setItems(data.items))
      .catch((err) => setError(err.message || 'Promoted symbols unavailable.'));
  }, [api]);

  // Nothing promoted yet: say nothing, rather than add an empty section.
  if (!error && (items === null || items.length === 0)) return null;

  return createElement(
    'section',
    { className: 'promoted-symbols-panel', 'aria-labelledby': 'promoted-symbols-heading' },
    createElement('h2', { id: 'promoted-symbols-heading' }, 'Promoted to public'),
    error ? createElement('p', { role: 'alert', className: 'set-admin-status error' }, error) : null,
    items && items.length > 0
      ? createElement('ul', { className: 'set-admin-list', 'aria-label': 'Symbols promoted to public' },
        items.map((item) => {
          const route = routeForCatalogSymbol(item.catalogSymbolId);
          return createElement('li', { key: item.id, className: 'set-admin-item promoted-symbol-item' },
            createElement('div', null,
              createElement('strong', null, `${item.catalogSymbolId || 'S-number pending'} · ${item.canonicalName}`),
              createElement('p', { className: 'set-admin-muted' },
                `Public · Promoted from ${item.promotedFromOrganizationName}${item.promotedAt ? ` on ${formatWhen(item.promotedAt)}` : ''}`),
            ),
            route
              ? createElement(Link, {
                to: route,
                className: 'action-button compact ghost',
                'aria-label': `Open ${item.catalogSymbolId} ${item.canonicalName} in the Catalog`,
              }, 'View in Catalog')
              : null,
          );
        }))
      : null,
  );
}
