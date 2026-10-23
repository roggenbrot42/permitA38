"""The permitA38 export format (version 1): which collections, in which order, how fields are typed.

The JSON files are the canonical, re-importable representation. CSV and XLSX are for humans and other tools and
use the typing hints below (money, date and boolean columns).
"""

from permita38.core.repository.schema import COLLECTIONS, TABLES

FORMAT_NAME = "permita38-export"
FORMAT_VERSION = 1
MANIFEST_NAME = "manifest.json"
README_NAME = "README.txt"

# import order (parents before children) – the data has no foreign keys but references are checked
EXPORT_COLLECTIONS: list[str] = [
    "users",
    "customers",
    "projects",
    "tasks",
    "templates",
    "contracts",
    "offers",
    "invoices",
    "entries",
    "absences",
    "expenses",
]
assert set(EXPORT_COLLECTIONS) == set(COLLECTIONS), "a new collection needs an export position"

# users: credential material is only exported on explicit request (include_users="full")
SECRET_USER_FIELDS = ("password_hash", "pw_changed", "totp_secret", "totp_enabled", "totp_last_step", "totp_recovery")
USER_MODES = ("none", "public", "full")

# record references: field -> target collection. A trailing "[]" marks a list of ids.
REFERENCES: dict[str, dict[str, str]] = {
    "projects": {"customer_id": "customers", "member_ids[]": "users", "offer_id": "offers"},
    "tasks": {"project_id": "projects"},
    "entries": {"user_id": "users", "project_id": "projects", "task_id": "tasks", "invoice_id": "invoices"},
    "contracts": {"customer_id": "customers", "project_id": "projects", "user_ids[]": "users", "offer_id": "offers"},
    "absences": {"user_id": "users"},
    "offers": {"customer_id": "customers", "project_id": "projects", "template_id": "templates", "offer_id": "offers"},
    "invoices": {
        "customer_id": "customers",
        "project_id": "projects",
        "template_id": "templates",
        "offer_id": "offers",
    },
    "expenses": {"project_id": "projects"},
}
# built-in document templates are not records
BUILTIN_TEMPLATE_IDS = {"classic", "minimal", "modern"}

DOCUMENT_COLLECTIONS = ("offers", "invoices")

MONEY_FIELDS: dict[str, set[str]] = {
    "customers": {"rate"},
    "projects": {"rate"},
    "tasks": {"rate"},
    "entries": {"rate"},
    "contracts": {"rate"},
    "expenses": {"net", "vat"},
    "users": {"default_rate", "cost_rate"},
}
DATE_FIELDS: dict[str, set[str]] = {
    "entries": {"date"},
    "contracts": {"start", "end"},
    "absences": {"start", "end"},
    "offers": {"date", "due_date", "valid_until", "paid_date", "service_from", "service_to"},
    "invoices": {"date", "due_date", "valid_until", "paid_date", "service_from", "service_to"},
    "expenses": {"date"},
    "users": {"tracking_start"},
}
# the date used to filter a year-scoped export
YEAR_FIELD: dict[str, str] = {
    "entries": "date",
    "absences": "start",
    "contracts": "start",
    "offers": "date",
    "invoices": "date",
    "expenses": "date",
}
LINE_COLUMNS = ["document_id", "document_number", "position", "description", "qty", "unit", "price", "vat", "amount"]
TIMESTAMP_FIELDS = {"created", "updated"}


def columns_for(coll: str, records: list[dict], include_secrets: bool) -> list[str]:
    """Schema column order, plus `extra` when any record carries unknown fields."""
    cols = [c for c in TABLES[coll].column_names]
    if coll == "users" and not include_secrets:
        cols = [c for c in cols if c not in SECRET_USER_FIELDS]
    known = set(TABLES[coll].column_names)
    if any(k not in known for r in records for k in r):
        cols.append("extra")
    return cols


def split_extra(coll: str, record: dict) -> tuple[dict, dict]:
    known = set(TABLES[coll].column_names)
    base = {k: v for k, v in record.items() if k in known}
    extra = {k: v for k, v in record.items() if k not in known}
    return base, extra


def strip_user_secrets(record: dict, mode: str) -> dict:
    if mode == "full":
        return dict(record)
    return {k: v for k, v in record.items() if k not in SECRET_USER_FIELDS}
