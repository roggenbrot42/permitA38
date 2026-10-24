"""What an archive contains: collections derived from the schema, secret filtering, manifest constants."""

import re
from typing import Any

from permita38.core.repository.schema import INTERNAL_COLLECTIONS, TABLES

FORMAT = "permita38-export"
FORMAT_VERSION = 1
SETTINGS_FILE = "data/settings.json"
LOG_KEY = "data_transfer_log"

#: Tables that are deliberately NOT exported, each with the reason. A guard test fails when a table is neither
#: exported nor listed here, so a new feature table is exported by default.
EXCLUDED_TABLES: dict[str, str] = {
    "settings": "exported as data/settings.json, not as records",
    "calendar_events": "cache of the calendar links, refreshed after the import",
    "api_tokens": "credentials of scripts; never leave the server",
    "audit_log": "token audit trail, tied to the excluded tokens",
    "timers": "transient running-timer state of single users, meaningless on another instance",
}

#: Setting keys that are never exported besides the ones matching SECRET_SETTING_RE.
SECRET_SETTING_KEYS: tuple[str, ...] = (LOG_KEY,)
#: Real setting names (core/models.py Settings) must not match; tests/backend/test_data_export.py pins that.
SECRET_SETTING_RE = re.compile(r"(?i)(secret|token|pass|cred|auth|api[_-]?key|private|key$)")

EXCLUDED_NOTES = [
    "secret key",
    "two-factor secrets and recovery codes",
    "API tokens",
    "calendar link URLs",
    "calendar events (cache)",
]


def exported_collections() -> list[str]:
    return [name for name in TABLES if name not in EXCLUDED_TABLES]


def csv_collections() -> list[str]:
    return [name for name in exported_collections() if name not in INTERNAL_COLLECTIONS]


def master_data_collections() -> list[str]:
    return [name for name in ("customers", "suppliers") if name in TABLES]


def is_secret_setting(key: str) -> bool:
    return key in SECRET_SETTING_KEYS or bool(SECRET_SETTING_RE.search(key))


def exportable_settings(settings: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in settings.items() if not is_secret_setting(k)}


def scrub_record(coll: str, rec: dict, include_accounts: bool) -> dict:
    """The record as it goes into the archive: no two-factor data ever, no password hash unless asked, no link URLs."""
    out = dict(rec)
    if coll == "users":
        for k in [k for k in out if k.startswith("totp")]:
            del out[k]
        if not include_accounts:
            out.pop("password_hash", None)
            out.pop("pw_changed", None)
    elif coll == "calendar_sources":
        out["url"] = ""
    return out


def columns_for(coll: str, include_accounts: bool) -> list[str]:
    """Columns of the CSV: those of schema.py minus secret ones."""
    cols = TABLES[coll].column_names
    if coll == "users":
        cols = [
            c
            for c in cols
            if not c.startswith("totp") and (include_accounts or c not in ("password_hash", "pw_changed"))
        ]
    return cols
