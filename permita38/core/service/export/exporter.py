"""Build export archives (ZIP with JSON + CSV + attachments) and XLSX workbooks."""

import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from permita38 import __version__
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository

from .formats import (
    DATE_FIELDS,
    DOCUMENT_COLLECTIONS,
    EXPORT_COLLECTIONS,
    FORMAT_NAME,
    FORMAT_VERSION,
    LINE_COLUMNS,
    MANIFEST_NAME,
    MONEY_FIELDS,
    README_NAME,
    USER_MODES,
    YEAR_FIELD,
    strip_user_secrets,
)
from .tabular import LOCALES, line_rows, lines_csv, records_csv, table_rows
from .xlsx import Cell, Sheet, write_xlsx

README = """permitA38 data export (format version {version})
================================================

This archive was written by permitA38 {app} on {when} (UTC).

  manifest.json          what is inside: counts, schema revision, SHA-256 of every file
  data/<collection>.json canonical, re-importable records ({{"format_version": 1, "records": [...]}})
  data/settings.json     company settings (bank details, numbering, ...)
  csv/<collection>.csv   the same tables for spreadsheets ({csv_note})
  csv/*_lines.csv        invoice/offer lines, one row per line with the exact amount
  attachments/           uploaded receipts and the logo, under their original names
  issued/                stored PDFs of finalized documents (if present)

Import it again with `permita38 import <file>` or Settings > Data export & import (dry run first).
Amounts are exact decimals; JSON keeps the stored numbers. The format is documented in docs/export-import.md.
User credentials (password hashes, two-factor secrets) are {users_note}.
Scope: {scope}
"""


@dataclass
class ExportOptions:
    format: str = "zip"  # zip | xlsx
    include_users: str = "public"  # none | public | full
    csv_locale: str = "iso"  # iso | de
    csv_bom: bool = False
    include_attachments: bool = True
    collections: list[str] | None = None
    year: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if self.format not in ("zip", "xlsx"):
            raise ServiceError(400, "format must be zip or xlsx")
        if self.include_users not in USER_MODES:
            raise ServiceError(400, "include_users must be none, public or full")
        if self.csv_locale not in LOCALES:
            raise ServiceError(400, "csv_locale must be iso or de")
        if self.year is not None and not 2000 <= self.year <= 2100:
            raise ServiceError(400, "year must be between 2000 and 2100")
        for c in self.collections or []:
            if c not in EXPORT_COLLECTIONS:
                raise ServiceError(400, f"unknown collection {c!r}")

    @property
    def partial(self) -> bool:
        return self.year is not None or self.collections is not None or self.include_users != "full"


def canonical_json(obj: Any) -> bytes:
    return (json.dumps(obj, ensure_ascii=False, indent=1, sort_keys=True) + "\n").encode("utf-8")


def sha256(blob: bytes) -> str:
    return hashlib.sha256(blob).hexdigest()


class ExportService:
    def __init__(self, store: Repository):
        self.store = store

    # ------------------------------------------------------------------ data selection
    def collect(self, opts: ExportOptions) -> dict[str, list[dict]]:
        opts.validate()
        wanted = opts.collections or EXPORT_COLLECTIONS
        data: dict[str, list[dict]] = {}
        for coll in EXPORT_COLLECTIONS:
            if coll not in wanted or (coll == "users" and opts.include_users == "none"):
                continue
            rows = self.store.list(coll)
            if coll == "users":
                rows = [strip_user_secrets(r, opts.include_users) for r in rows]
            field_name = YEAR_FIELD.get(coll)
            if opts.year is not None and field_name:
                prefix = f"{opts.year:04d}-"
                rows = [r for r in rows if str(r.get(field_name) or "").startswith(prefix)]
            data[coll] = rows
        return data

    # ------------------------------------------------------------------ ZIP
    def export_zip(self, opts: ExportOptions, dest: Path | None = None) -> Path:
        opts.validate()
        data = self.collect(opts)
        settings = self.store.settings()
        locale = LOCALES[opts.csv_locale]
        files: list[tuple[str, bytes]] = []
        for coll, rows in data.items():
            files.append(
                (
                    f"data/{coll}.json",
                    canonical_json({"format": FORMAT_NAME, "format_version": FORMAT_VERSION, "collection": coll, "records": rows}),
                )
            )
            files.append((f"csv/{coll}.csv", records_csv(coll, rows, locale, opts.csv_bom, opts.include_users == "full")))
            if coll in DOCUMENT_COLLECTIONS:
                files.append((f"csv/{coll}_lines.csv", lines_csv(rows, locale, opts.csv_bom)))
        files.append(
            (
                "data/settings.json",
                canonical_json({"format": FORMAT_NAME, "format_version": FORMAT_VERSION, "settings": settings}),
            )
        )
        attachments = self._attachment_names(data, settings) if opts.include_attachments else []
        issued = self._issued_files() if opts.include_attachments else []
        now = datetime.now(timezone.utc)
        manifest = {
            "format": FORMAT_NAME,
            "format_version": FORMAT_VERSION,
            "app_version": __version__,
            "schema_revision": self.store.get_revision(),
            "exported_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "scope": {
                "collections": sorted(data),
                "year": opts.year,
                "users": opts.include_users,
                "attachments": opts.include_attachments,
                "complete": not opts.partial,
            },
            "counts": {c: len(r) for c, r in data.items()},
            "files": [],
        }
        dest = dest or Path(tempfile.mkstemp(prefix="permita38-export-", suffix=".zip")[1])
        tmp = dest.with_name(dest.name + ".part")
        try:
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for name, blob in files:
                    manifest["files"].append({"path": name, "size": len(blob), "sha256": sha256(blob)})
                    z.writestr(_zipinfo(name, now), blob)
                for name in attachments:
                    path = self.store.attachment_path(name)
                    if not path:
                        continue
                    blob = Path(path).read_bytes()
                    manifest["files"].append({"path": f"attachments/{name}", "size": len(blob), "sha256": sha256(blob)})
                    z.writestr(_zipinfo(f"attachments/{name}", now), blob)
                for rel, path in issued:
                    blob = path.read_bytes()
                    manifest["files"].append({"path": f"issued/{rel}", "size": len(blob), "sha256": sha256(blob)})
                    z.writestr(_zipinfo(f"issued/{rel}", now), blob)
                manifest["files"].sort(key=lambda f: f["path"])
                readme = README.format(
                    version=FORMAT_VERSION,
                    app=__version__,
                    when=manifest["exported_at"],
                    csv_note="semicolon, decimal comma, dd.mm.yyyy" if opts.csv_locale == "de" else "comma, decimal point, ISO dates",
                    users_note={"none": "not exported", "public": "NOT included", "full": "INCLUDED - keep this file secret"}[opts.include_users],
                    scope="complete database" if not opts.partial else "PARTIAL export (not a full backup)",
                ).encode("utf-8")
                z.writestr(_zipinfo(README_NAME, now), readme)
                z.writestr(_zipinfo(MANIFEST_NAME, now), canonical_json(manifest))
            tmp.replace(dest)
        finally:
            tmp.unlink(missing_ok=True)
        return dest

    def _attachment_names(self, data: dict[str, list[dict]], settings: dict) -> list[str]:
        names = {r.get("attachment") for r in data.get("expenses", []) if r.get("attachment")}
        if settings.get("logo"):
            names.add(settings["logo"])
        return sorted(n for n in names if isinstance(n, str))

    def _issued_files(self) -> list[tuple[str, Path]]:
        """Stored PDFs of finalized documents (written by the backup module); absent in older installs."""
        root = getattr(self.store, "data_dir", None)
        if not root:
            return []
        folder = Path(root) / "issued"
        if not folder.is_dir():
            return []
        return [(p.relative_to(folder).as_posix(), p) for p in sorted(folder.rglob("*.pdf")) if p.is_file()]

    # ------------------------------------------------------------------ XLSX
    def export_xlsx(self, opts: ExportOptions, dest: Path | None = None) -> Path:
        opts.validate()
        data = self.collect(opts)
        sheets: list[Sheet] = []
        for coll, rows in data.items():
            cols, trows = table_rows(coll, rows, opts.include_users == "full")
            sheet = Sheet(coll, cols)
            for r in trows:
                sheet.rows.append([_xlsx_cell(coll, c, r.get(c)) for c in cols])
            sheets.append(sheet)
            if coll in DOCUMENT_COLLECTIONS:
                lines = Sheet(f"{coll}_lines", LINE_COLUMNS)
                for doc in rows:
                    for lr in line_rows(doc):
                        lines.rows.append(
                            [
                                lr["document_id"],
                                lr["document_number"],
                                Cell(lr["position"], "int"),
                                lr["description"],
                                Cell(lr["qty"], "number"),
                                lr["unit"],
                                Cell(lr["price"], "money"),
                                Cell(lr["vat"], "number") if lr["vat"] is not None else None,
                                Cell(lr["amount"], "money"),
                            ]
                        )
                sheets.append(lines)
        dest = dest or Path(tempfile.mkstemp(prefix="permita38-export-", suffix=".xlsx")[1])
        write_xlsx(dest, sheets)
        return dest


def _zipinfo(name: str, when: datetime) -> zipfile.ZipInfo:
    zi = zipfile.ZipInfo(name, date_time=when.timetuple()[:6])
    zi.compress_type = zipfile.ZIP_DEFLATED
    zi.external_attr = 0o644 << 16
    return zi


def _xlsx_cell(coll: str, field_name: str, value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return Cell(value, "bool")
    if field_name in MONEY_FIELDS.get(coll, ()) and isinstance(value, (int, float)):
        return Cell(value, "money")
    if field_name in DATE_FIELDS.get(coll, ()) and isinstance(value, str):
        return Cell(value, "date") if _is_iso_date(value) else Cell(value, "text")
    if isinstance(value, int):
        return Cell(value, "int")
    if isinstance(value, float):
        return Cell(value, "number")
    if isinstance(value, (list, dict)):
        return Cell(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")), "text")
    return Cell(str(value), "text")


def _is_iso_date(text: str) -> bool:
    from datetime import date

    try:
        date.fromisoformat(text)
        return True
    except ValueError:
        return False
