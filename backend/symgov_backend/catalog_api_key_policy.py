"""Who may hold which kind of Catalog API key.

`organization_api_keys_allowed` is the single place for any rule about whether
an organization may have working keys. Today that is "the organization is
active and its entitlement is active". A subscription rule, if one is wanted
later, goes here and nowhere else.
"""

from __future__ import annotations

from .models import Organization


def organization_api_keys_allowed(organization: Organization | None) -> bool:
    return bool(
        organization is not None
        and organization.is_active
        and organization.entitlement_status == "active"
    )
