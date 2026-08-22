import { createElement } from 'react';
import { Route } from 'react-router-dom';

import { OrganizationAdminPage } from './OrganizationAdminPage.js';
import { PlatformAdminPage } from './PlatformAdminPage.js';
import { OrganizationAdminAccess, PlatformAdminAccess } from './adminJourneys.js';

export function adminRouteElements(auth, RequireAuth) {
  return [
    createElement(Route, {
      key: 'organization-admin',
      path: '/organization/admin',
      element: createElement(
        RequireAuth,
        null,
        createElement(
          OrganizationAdminAccess,
          { auth },
          createElement(OrganizationAdminPage, { auth }),
        ),
      ),
    }),
    createElement(Route, {
      key: 'platform-admin',
      path: '/platform/admin',
      element: createElement(
        RequireAuth,
        null,
        createElement(
          PlatformAdminAccess,
          { auth },
          createElement(PlatformAdminPage, { auth }),
        ),
      ),
    }),
  ];
}
