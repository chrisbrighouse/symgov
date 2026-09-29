"""Ed's pilot gate (spec section 10.1), shared by the route and the session.

A small module on its own so that `/auth/me` can report the same answer the
Ed route enforces without importing the Ed service.
"""

from __future__ import annotations

from .auth import AuthenticatedUser
from .settings import SymgovAPISettings


def ed_pilot_allows(user: AuthenticatedUser, settings: SymgovAPISettings) -> bool:
    """Whether the session's active organization is a named Ed pilot.

    The organization comes from the authenticated session, never the
    request. A personal session belongs to no organization, so it is outside
    every pilot, as is a session opened only to change credentials.
    """
    if user.session_purpose != "application":
        return False
    if user.session_mode != "organization" or not user.active_organization_id:
        return False
    code = (user.organization_code or "").strip().lower()
    return bool(code) and code in settings.ed_pilot_organization_codes
