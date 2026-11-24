"""DATEV export (docs/specs/datev-export.md "File", "From journal entries to rows" 7, "Self-check", "API and UI").

:class:`DatevExportService` reads the journal entries of a date range (one fiscal year), builds the DATEV rows with
the pure functions of ``core/accounting/datev.py``, writes the EXTF file(s) and the self-check report and returns the
zip. Rows of locked months carry Festschreibung 1, rows of unlocked months 0; a batch must agree, so a range that
holds both becomes two EXTF files. No SQL here.
"""

import re
from collections.abc import Callable, Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any, cast

from pydantic import ValidationError

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
from permita38.core.models import Settings
from permita38.core.repository import Repository
from permita38.core.service.fiscal_years import FiscalYearService
from permita38.core.service.locks import LockService
from permita38.core.vat_rules import VatRulesMissing, vat_rules_for

__all__ = ["ACCOUNT_LENGTH_KEY", "CLIENT_KEY", "CONSULTANT_KEY", "LABEL_KEY", "DatevExportService"]

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

LOCKED_SUFFIX = "_locked"
OPEN_SUFFIX = "_unlocked"


#: box (Kz) of the year's VAT rules whose ``computed_tax_rate`` is the rate of S19 / S7 lines without a ``tax_rate``
_BOX_OF_KEY = {"S19": "81", "S7": "86"}
_RC_KEYS = ("RC_EU_SVC", "RC_3C_SVC")
#: tax lines of these keys need a rate for DATEV's own split
_VAT_KEYS = frozenset({"S19", "S7", "SX", "VST", *_RC_KEYS})


def _number(raw: object, key: str, low: int, high: int) -> int | None:
    """The setting as an integer in ``[low, high]``; ``None`` when missing; 422 when present but not valid."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):
        value = None
    elif isinstance(raw, int):
        value = raw
    else:
        value = int(raw) if isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw.strip()) else None
    if value is None or not low <= value <= high:
        raise ServiceError(422, f"{key} must be a whole number from {low} to {high}")
    return value


#: position of the Festschreibung flag in the EXTF header line (``extf_csv`` derives it from the rows only)
_HEADER_FESTSCHREIBUNG = 20


def _header_locked(data: bytes) -> bytes:
    """The EXTF file with Festschreibung 1 in the header (for an empty batch, which has no rows to derive it from)."""
    head, sep, rest = data.partition(b"\r\n")
    fields = head.split(b";")
    fields[_HEADER_FESTSCHREIBUNG] = b"1"
    return b";".join(fields) + sep + rest


def _rate_text(rate: Decimal) -> str:
    return format(rate.normalize(), "f")


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
        try:
            st = Settings.model_validate(self.store.settings())
        except ValidationError as e:
            bad = ", ".join(str(err["loc"][0]) for err in e.errors() if err["loc"])
            raise ServiceError(422, f"The settings are not valid: {bad}") from None
        consultant = _number(st.datev_consultant_no, CONSULTANT_KEY, *CONSULTANT_RANGE)
        client = _number(st.datev_client_no, CLIENT_KEY, *CLIENT_RANGE)
        missing = [k for k, v in ((CONSULTANT_KEY, consultant), (CLIENT_KEY, client)) if v is None]
        if consultant is None or client is None:
            raise ServiceError(
                422, "Set the consultant and client number for DATEV in the settings: " + ", ".join(missing)
            )
        length = _number(st.datev_account_length, ACCOUNT_LENGTH_KEY, *ACCOUNT_LENGTH_RANGE) or DEFAULT_ACCOUNT_LENGTH
        label = st.datev_label or DEFAULT_LABEL
        if len(label) > MAX_LABEL:
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
    def _entry(
        row: Mapping[str, Any],
        role_of: Mapping[str, list[str]],
        box_rates: Mapping[str, Decimal],
        unrated: list[str],
    ) -> Entry:
        lines: list[Line] = []
        for ln in row.get("lines") or []:
            role = ln.get("role")
            if not role:
                roles = role_of.get(str(ln.get("account")), [])
                if len(roles) > 1:
                    raise ServiceError(
                        422,
                        f"Entry {row.get('event_key')}: account {ln.get('account')} is shared by the roles "
                        + ", ".join(roles)
                        + "; the line has no role of its own",
                    )
                role = roles[0] if roles else None
            if not role:
                raise ServiceError(
                    422, f"Entry {row.get('event_key')}: account {ln.get('account')} has no role in the chart"
                )
            base = ln.get("tax_base_cents")
            key = str(ln["tax_key"]) if ln.get("tax_key") else None
            rate = ln.get("tax_rate")
            if rate is None and ln.get("tax_kind") in ("tax", "input") and key in _VAT_KEYS:
                if key in box_rates:
                    rate = _rate_text(box_rates[key])
                else:
                    unrated.append(str(row.get("event_key")))
            lines.append(
                Line(
                    Role(str(role)),
                    Cents(int(ln.get("amount_cents") or 0)),
                    TaxKey(key) if key else None,
                    ln.get("tax_kind"),
                    rate,
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

    def _entries(self, chart: Chart, box_rates: Mapping[str, Decimal], date_from: date, date_to: date) -> list[Entry]:
        role_of: dict[str, list[str]] = {}
        for role, acc in chart.roles:
            role_of.setdefault(str(acc.account), []).append(str(role))
        out: list[Entry] = []
        unrated: list[str] = []
        offset = 0
        while True:
            rows = self.store.list_journal_entries(None, date_from.isoformat(), date_to.isoformat(), PAGE, offset)
            out += [self._entry(r, role_of, box_rates, unrated) for r in rows]
            if len(rows) < PAGE:
                break
            offset += PAGE
        if unrated:
            raise ServiceError(422, "Tax lines without a tax rate (entries: " + ", ".join(dict.fromkeys(unrated)) + ")")
        return out

    @staticmethod
    def _box_rates(year: int) -> dict[str, Decimal]:
        """The rates of S19 and S7 lines without ``tax_rate``: ``computed_tax_rate`` of Kz 81 / 86 of the year."""
        try:
            rules = vat_rules_for(year)
        except VatRulesMissing as e:
            raise ServiceError(409, f"DATEV export is not available for {year}: {e}") from None
        by_kz = {b.kz: b.computed_tax_rate for b in rules.boxes}
        return {key: rate for key, kz in _BOX_OF_KEY.items() if (rate := by_kz.get(kz)) is not None}

    @staticmethod
    def _simulation_rates(entries: list[Entry], box_rates: Mapping[str, Decimal]) -> dict[str, Decimal]:
        """Rate per tax key for the DATEV simulation: the box rates plus the one rate the § 13b lines carry."""
        rates = dict(box_rates)
        for key in _RC_KEYS:
            seen = {ln.tax_rate for e in entries for ln in e.lines if ln.tax_key == key and ln.tax_rate is not None}
            if len(seen) == 1:
                rates[key] = Decimal(next(iter(seen)))
        return rates

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
        box_rates = self._box_rates(year)
        entries = self._entries(chart, box_rates, date_from, date_to)
        rates = self._simulation_rates(entries, box_rates)
        locked_months = set(self.locks.locked_months(year))
        groups = {
            True: [e for e in entries if e.booking_date.month in locked_months],
            False: [e for e in entries if e.booking_date.month not in locked_months],
        }
        try:
            batches = [
                (flag, batch_rows(es, chart, rules, rates, festschreibung=flag)) for flag, es in groups.items() if es
            ]
        except DatevBatchError as e:
            raise ServiceError(422, str(e)) from None
        if not batches:  # an empty range is Festschreibung 1 when every month of it is locked
            months = range(date_from.month, date_to.month + 1)
            batches = [(all(m in locked_months for m in months), [])]
        created = self.now()
        params = DatevHeaderParams(
            rules.header, created, consultant, client, date(year, 1, 1), length, date_from, date_to, label
        )
        split = len(batches) > 1
        files: list[tuple[str, bytes]] = []
        all_rows: list[DatevRow] = []
        for flag, rows in batches:
            suffix = (LOCKED_SUFFIX if flag else OPEN_SUFFIX) if split else ""
            data = extf_csv(rows, params)
            if flag and not rows:
                data = _header_locked(data)
            files.append((extf_filename(date_from, date_to, suffix), data))
            all_rows += rows
        report = self_check_report(entries, all_rows, chart, rules, rates, header_params=params)
        if split:
            report += (
                "\nThe range holds locked and unlocked months; one file each (rows of one file agree on "
                "Festschreibung):\n" + "".join(f"- {name}\n" for name, _ in files)
            )
        if rules.warning:
            report += f"\nDATEV rules: {rules.warning}\n"
        return datev_zip_files(files, report)
