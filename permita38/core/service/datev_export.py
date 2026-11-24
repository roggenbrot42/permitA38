"""DATEV export (docs/specs/datev-export.md "File", "From journal entries to rows" 7, "Self-check", "API and UI").

:class:`DatevExportService` reads the journal entries of a date range (one fiscal year), builds the DATEV rows with
the pure functions of ``core/accounting/datev.py``, writes the EXTF file(s) and the self-check report and returns the
zip. Rows of locked months carry Festschreibung 1, rows of unlocked months 0; a batch must agree, so a range that
holds both becomes two EXTF files. No SQL here.
"""

from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from permita38.core.accounting.datev import DatevBatchError, DatevRow, batch_rows
from permita38.core.accounting.datev_file import (
    DatevHeaderParams,
    datev_zip_files,
    extf_csv,
    extf_filename,
    self_check_report,
)
from permita38.core.accounting.types import Cents, Entry, Line, Role, TaxKey
from permita38.core.charts import Chart, ChartMissing, ChartName, chart_for
from permita38.core.datev import DatevRulesMissing, datev_rules_for
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.service.fiscal_years import FiscalYearService
from permita38.core.service.locks import LockService

__all__ = ["ACCOUNT_LENGTH_KEY", "CLIENT_KEY", "CONSULTANT_KEY", "LABEL_KEY", "VAT_RATES", "DatevExportService"]

CONSULTANT_KEY = "datev_consultant_no"
CLIENT_KEY = "datev_client_no"
ACCOUNT_LENGTH_KEY = "datev_account_length"
LABEL_KEY = "datev_label"
DEFAULT_ACCOUNT_LENGTH = 4
DEFAULT_LABEL = "permitA38"
CONSULTANT_RANGE = (1001, 9999999)
CLIENT_RANGE = (1, 99999)
ACCOUNT_LENGTH_RANGE = (4, 8)
MAX_LABEL = 30
PAGE = 500

#: The German VAT rates per tax key used for DATEV's own split (the same for every year the chart files cover).
VAT_RATES: Mapping[str, Decimal] = {
    "S19": Decimal(19),
    "S7": Decimal(7),
    "RC_EU_SVC": Decimal(19),
    "RC_3C_SVC": Decimal(19),
}

LOCKED_SUFFIX = "_locked"
OPEN_SUFFIX = "_unlocked"


def _number(settings: Mapping[str, Any], key: str, low: int, high: int) -> int | None:
    """The setting as an integer in ``[low, high]``; ``None`` when missing; 422 when present but not valid."""
    raw = settings.get(key)
    if raw is None or raw == "":
        return None
    ok = (isinstance(raw, int) and not isinstance(raw, bool)) or (isinstance(raw, str) and raw.strip().isdigit())
    value = int(raw) if ok else None
    if value is None or not low <= value <= high:
        raise ServiceError(422, f"{key} must be a whole number from {low} to {high}")
    return value


class DatevExportService:
    def __init__(
        self,
        store: Repository,
        locks: LockService,
        fiscal_years: FiscalYearService,
        now: Callable[[], datetime] = datetime.now,
    ):
        self.store = store
        self.locks = locks
        self.fiscal_years = fiscal_years
        self.now = now

    # ---------- checks
    def _settings(self) -> tuple[int, int, int, str]:
        """Consultant no, client no, account length, label; 422 listing the missing numbers."""
        raw = self.store.settings()
        consultant = _number(raw, CONSULTANT_KEY, *CONSULTANT_RANGE)
        client = _number(raw, CLIENT_KEY, *CLIENT_RANGE)
        missing = [k for k, v in ((CONSULTANT_KEY, consultant), (CLIENT_KEY, client)) if v is None]
        if consultant is None or client is None:
            raise ServiceError(
                422, "Set the consultant and client number for DATEV in the settings: " + ", ".join(missing)
            )
        length = _number(raw, ACCOUNT_LENGTH_KEY, *ACCOUNT_LENGTH_RANGE) or DEFAULT_ACCOUNT_LENGTH
        label = raw.get(LABEL_KEY)
        if label is None or label == "":
            label = DEFAULT_LABEL
        if not isinstance(label, str) or len(label) > MAX_LABEL:
            raise ServiceError(422, f"{LABEL_KEY} must be text of at most {MAX_LABEL} characters")
        return consultant, client, length, label

    @staticmethod
    def _check_range(date_from: date, date_to: date) -> None:
        if date_from > date_to:
            raise ServiceError(422, "from must not be after to")
        if date_from.year != date_to.year:
            raise ServiceError(422, "A DATEV export covers one fiscal year; export each year on its own")

    # ---------- reads
    @staticmethod
    def _entry(row: Mapping[str, Any], role_of: Mapping[str, str]) -> Entry:
        lines: list[Line] = []
        for ln in row.get("lines") or []:
            role = ln.get("role") or role_of.get(str(ln.get("account")))
            if not role:
                raise ServiceError(
                    422, f"Entry {row.get('event_key')}: account {ln.get('account')} has no role in the chart"
                )
            base = ln.get("tax_base_cents")
            lines.append(
                Line(
                    Role(str(role)),
                    Cents(int(ln.get("amount_cents") or 0)),
                    TaxKey(str(ln["tax_key"])) if ln.get("tax_key") else None,
                    ln.get("tax_kind"),
                    ln.get("tax_rate"),
                    Cents(int(base)) if base is not None else None,
                    date.fromisoformat(str(ln["vat_date"])) if ln.get("vat_date") else None,
                    date.fromisoformat(str(ln["income_date"])) if ln.get("income_date") else None,
                    ln.get("open_item_id"),
                )
            )
        return Entry(
            tuple(lines),
            date.fromisoformat(str(row["booking_date"])),
            date.fromisoformat(str(row["document_date"])),
            str(row["event_key"]),
        )

    def _entries(self, chart: Chart, date_from: date, date_to: date) -> list[Entry]:
        role_of: dict[str, str] = {}
        for role, acc in chart.roles:
            role_of.setdefault(str(acc.account), str(role))
        out: list[Entry] = []
        offset = 0
        while True:
            rows = self.store.list_journal_entries(None, date_from.isoformat(), date_to.isoformat(), PAGE, offset)
            out += [self._entry(r, role_of) for r in rows]
            if len(rows) < PAGE:
                return out
            offset += PAGE

    # ---------- export
    def export(self, date_from: date, date_to: date, user: Mapping[str, object]) -> bytes:
        """The zip with the EXTF file(s) and ``export-report.txt`` for the bookings from ``date_from`` to ``date_to``.

        422: the range crosses a fiscal year, ``from`` is after ``to``, or the DATEV numbers are missing in the
        settings; 404: the fiscal year does not exist; 409: no chart or DATEV rules for it.
        """
        self._check_range(date_from, date_to)
        consultant, client, length, label = self._settings()
        year = date_from.year
        fy = self.fiscal_years.get(year)
        try:
            chart = chart_for(cast(ChartName, fy["chart"]), year)
            rules = datev_rules_for(year)
        except (ChartMissing, DatevRulesMissing) as e:
            raise ServiceError(409, f"DATEV export is not available for {year}: {e}") from None
        entries = self._entries(chart, date_from, date_to)
        locked_months = set(self.locks.locked_months(year))
        groups = {
            True: [e for e in entries if e.booking_date.month in locked_months],
            False: [e for e in entries if e.booking_date.month not in locked_months],
        }
        try:
            batches = [
                (flag, batch_rows(es, chart, rules, VAT_RATES, festschreibung=flag))
                for flag, es in groups.items()
                if es
            ]
        except DatevBatchError as e:
            raise ServiceError(422, str(e)) from None
        if not batches:
            batches = [(False, [])]
        created = self.now()
        params = DatevHeaderParams(
            rules.header, created, consultant, client, date(year, 1, 1), length, date_from, date_to, label
        )
        split = len(batches) > 1
        files: list[tuple[str, bytes]] = []
        all_rows: list[DatevRow] = []
        for flag, rows in batches:
            suffix = (LOCKED_SUFFIX if flag else OPEN_SUFFIX) if split else ""
            files.append((extf_filename(date_from, date_to, suffix), extf_csv(rows, params)))
            all_rows += rows
        report = self_check_report(entries, all_rows, chart, rules, VAT_RATES, header_params=params)
        if split:
            report += (
                "\nThe range holds locked and unlocked months; one file each (rows of one file agree on "
                "Festschreibung):\n" + "".join(f"- {name}\n" for name, _ in files)
            )
        if rules.warning:
            report += f"\nDATEV rules: {rules.warning}\n"
        return datev_zip_files(files, report)
