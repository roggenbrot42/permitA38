"""Fiscal years (docs/specs/accounting.md 4, 8.1, 18): one row per calendar year with its bases.

Rules: a year exists once (409); a company of type ``gmbh`` must use the accrual basis (422); once the year has a
journal entry its bases (chart, income basis, VAT basis, small business) are frozen, and a closed year accepts no
change at all (409). The year number itself never changes. No SQL here: the rows live in the ``fiscal_years``
collection of the repository; the entries are only looked up (``journal_entries.fiscal_year``).
"""

import time
from collections.abc import Mapping

from pydantic import ValidationError

from permita38.core import models
from permita38.core.charts import ChartMissing, chart_for
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.repository.base import Record
from permita38.core.settings import effective_settings
from permita38.core.vat_rules import VatRulesMissing, vat_rules_for

COLLECTION = "fiscal_years"
ENTRIES = "journal_entries"
VAT_RETURNS = "vat_returns"

# fields a change may name; `year` is the identity and cannot change
EDITABLE = ("chart", "income_basis", "vat_basis", "vat_frequency", "dauerfrist", "small_business", "vat_regular_from")
# frozen as soon as the year has an entry (accounting.md 4: "a year's bases change only before its first entry")
FROZEN_AFTER_ENTRY = ("chart", "income_basis", "vat_basis", "small_business")
# frozen as soon as a VAT return record (draft or final) exists for the year (accounting.md 4)
FROZEN_AFTER_RETURN = ("vat_frequency", "dauerfrist", "vat_regular_from")
FORM_FIELDS = ("year", *EDITABLE)

GMBH_MESSAGE = "A GmbH must use the accrual basis (income_basis = accrual)"


def rules_version(chart: str, year: int) -> str:
    """Which chart and VAT rule files the year was opened with, e.g. ``skr03-2026/vat-2026``; ``none`` when a file
    is missing (the files are looked up by the nearest older year, so a stored value is what was in use then)."""
    try:
        chart_part = f"{chart}-{chart_for(chart, year).year}"  # type: ignore[arg-type]
    except ChartMissing:
        chart_part = f"{chart}-none"
    try:
        vat_part = f"vat-{vat_rules_for(year).form_year}"
    except VatRulesMissing:
        vat_part = "vat-none"
    return f"{chart_part}/{vat_part}"


class FiscalYearService:
    def __init__(self, store: Repository):
        self.store = store

    # ---------- reads
    def list(self) -> list[Record]:
        return sorted(self.store.list(COLLECTION), key=lambda r: int(r["year"]))

    def find(self, year: int) -> Record | None:
        rows = self.store.find(COLLECTION, year=year)
        return rows[0] if rows else None

    def get(self, year: int) -> Record:
        row = self.find(year)
        if row is None:
            raise ServiceError(404, "Fiscal year not found")
        return row

    def has_entries(self, year: int) -> bool:
        # the Repository offers no exists/limit query; `find` is an indexed query, only the rows are read needlessly
        return bool(self.store.find(ENTRIES, fiscal_year=year))

    def has_returns(self, year: int) -> bool:
        """True when a VAT return record (draft or final) exists for ``year``; same note on `find` as above."""
        return bool(self.store.find(VAT_RETURNS, year=year))

    # ---------- writes
    def _check_company(self, income_basis: str) -> None:
        company_type = effective_settings(self.store.settings()).get("company_type")
        if company_type == "gmbh" and income_basis != "accrual":
            raise ServiceError(422, GMBH_MESSAGE)

    def create(self, user: Mapping[str, object], body: models.FiscalYearIn) -> Record:
        with self.store.transaction():
            if self.find(body.year):
                raise ServiceError(409, f"Fiscal year {body.year} already exists")
            self._check_company(body.income_basis)
            row: Record = body.model_dump()
            row["rules_version"] = rules_version(body.chart, body.year)
            row["opened_at"] = int(time.time())
            row["opened_by"] = user.get("id")
            return self.store.save(COLLECTION, row)

    def update(self, year: int, changes: Mapping[str, object]) -> Record:
        """Change bases of ``year``. The result must be a valid :class:`models.FiscalYearIn` again."""
        unknown = sorted(set(changes) - set(EDITABLE))
        if unknown:
            raise ServiceError(422, f"Not changeable: {', '.join(unknown)}")
        with self.store.transaction():
            row = self.get(year)
            if row.get("closed_at") is not None:
                raise ServiceError(409, f"Fiscal year {year} is closed")
            if self.has_entries(year):
                frozen = sorted(k for k in FROZEN_AFTER_ENTRY if k in changes and changes[k] != row.get(k))
                if frozen:
                    raise ServiceError(409, f"Fiscal year {year} has entries; {', '.join(frozen)} cannot change")
            if self.has_returns(year):
                frozen = sorted(k for k in FROZEN_AFTER_RETURN if k in changes and changes[k] != row.get(k))
                if frozen:
                    raise ServiceError(409, f"Fiscal year {year} has a VAT return; {', '.join(frozen)} cannot change")
            merged = {k: row.get(k) for k in FORM_FIELDS if row.get(k) is not None}
            merged.update(changes)
            try:
                valid = models.FiscalYearIn.model_validate(merged)
            except ValidationError as e:
                raise ServiceError(422, "; ".join(f"{'.'.join(map(str, d['loc']))}: {d['msg']}" for d in e.errors()))
            self._check_company(valid.income_basis)
            new: Record = {**row, **valid.model_dump()}
            if valid.chart != row.get("chart"):
                new["rules_version"] = rules_version(valid.chart, year)
            return self.store.save(COLLECTION, new)
