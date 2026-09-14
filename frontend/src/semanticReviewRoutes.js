import { createElement } from 'react';
import { Route } from 'react-router-dom';

import { SemanticReviewPage } from './SemanticReviewPage.js';
import { SEMANTIC_REVIEW_ROLES, SemanticReviewAccess } from './semanticReviewJourney.js';

// SM-P1-01 WP1.4. The route lives here rather than inline in `App.jsx`'s
// `<Routes>` block for the reason `adminRoutes.js` already exists: the shell
// file is 7700 lines, and a gated surface that carries its own role set and
// its own capability gate is easier to read and to test on its own.
//
// `/semantic-review`, deliberately distinct from the existing `/rights`
// route. That one is the legacy intake lane (`provenance_rights_review`), a
// different and intake-scoped domain from the `rights_records` table this
// surface reviews. WP1.4 adds a route; it repoints nothing.
//
// Two gates in series, matching the two the API applies: `RequireAnyRole`
// mirrors `require_any_role({"admin", "reviewer"})`, and `SemanticReviewAccess`
// mirrors the default-off `SYMGOV_SEMANTIC_REVIEW_ENABLED` guard. Neither is
// authoritative -- the router answers 404 for a dormant flag whatever the UI
// renders -- but rendering a surface whose every request would 404 is worse
// than saying so.

export function semanticReviewRouteElements(auth, RequireAnyRole) {
  return [
    createElement(Route, {
      key: 'semantic-review',
      path: '/semantic-review',
      element: createElement(
        RequireAnyRole,
        { roles: SEMANTIC_REVIEW_ROLES },
        createElement(
          SemanticReviewAccess,
          { auth },
          createElement(SemanticReviewPage, { auth }),
        ),
      ),
    }),
  ];
}
