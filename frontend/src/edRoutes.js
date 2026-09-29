import { createElement } from 'react';
import { Route } from 'react-router-dom';

import { EdChatPage } from './EdChatPage.js';
import { EdAccess } from './edChat.js';

// Ed Stage 5. `/ed` for every signed-in user whose session the pilot gate
// admits (spec sections 3.1 and 10.1). Two gates in series, as for semantic
// review: `RequireAuth` for a session, then `EdAccess` for the capability the
// API's own pilot gate reports. The API stays the authority.

export function edRouteElements(auth, RequireAuth) {
  return [
    createElement(Route, {
      key: 'ed',
      path: '/ed',
      element: createElement(
        RequireAuth,
        null,
        createElement(EdAccess, { auth }, createElement(EdChatPage, { auth })),
      ),
    }),
  ];
}
