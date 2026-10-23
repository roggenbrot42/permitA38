"""Rights used by the export/import and GDPR features, in ONE place.

Until the roles-and-rights catalog (permita38/core/rights.py) is merged these are enforced as "boss only";
the merge replaces `require` by the real right check without touching the routers.
"""

from permita38.core.errors import ServiceError

EXPORT_RUN = "export.run"  # data export, tax-audit export, data import
GDPR_MANAGE = "gdpr.manage"  # subject export, erasure, records of processing activities


def require(user: dict, right: str) -> dict:
    if user.get("role") != "boss":
        raise ServiceError(403, "Forbidden")
    return user
