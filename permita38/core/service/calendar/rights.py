"""Who may manage calendars. The one place to change when RBAC is merged (see the roadmap)."""

MANAGE_OWN = "calendar.manage_own"  # a user manages only their own calendar sources


def may_manage_own(user: dict) -> bool:
    # boss-only for now; the RBAC branch replaces this with a lookup of MANAGE_OWN
    return user.get("role") == "boss"
