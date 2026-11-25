"""Accounting status (docs/specs/accounting.md 16 last paragraph, 22 row W9): is accounting initialised, which years
exist with which bases, and whether the rules in use are still ``to_verify`` (the adviser banner)."""

from datetime import date

from pydantic import BaseModel

from permita38.core.charts import ChartMissing, chart_for
from permita38.core.datev import DatevRulesMissing, datev_rules_for
from permita38.core.repository import Repository
from permita38.core.settings import accounting_start
from permita38.core.vat.ustva_export import rules_unconfirmed as vat_rules_unconfirmed

BANNER_TEXT = "Tax rules not confirmed by a tax adviser — check the figures before filing"
DEFAULT_CHART = "skr03"


class StatusYear(BaseModel):
    year: int
    income_basis: str | None = None
    vat_basis: str | None = None
    closed: bool


class AccountingStatusOut(BaseModel):
    initialised: bool
    start: str | None
    years: list[StatusYear]
    rules_unconfirmed: bool
    banner_text: str


def chart_unconfirmed(chart: str, year: int) -> bool:
    try:
        return chart_for(chart, year).status == "to_verify"  # type: ignore[arg-type]
    except ChartMissing:
        return True


def datev_unconfirmed(year: int) -> bool:
    try:
        return datev_rules_for(year).status == "to_verify"
    except DatevRulesMissing:
        return True


class AccountingStatusService:
    def __init__(self, store: Repository):
        self.store = store

    def status(self, today: date | None = None) -> AccountingStatusOut:
        year = (today or date.today()).year
        start = accounting_start(self.store.settings())
        rows = sorted(self.store.list("fiscal_years"), key=lambda r: int(r["year"]))
        current = next((r for r in rows if int(r["year"]) == year), None)
        chart = (current or {}).get("chart") or DEFAULT_CHART
        unconfirmed = vat_rules_unconfirmed(year) or chart_unconfirmed(chart, year) or datev_unconfirmed(year)
        return AccountingStatusOut(
            initialised=start is not None,
            start=start.isoformat() if start else None,
            years=[
                StatusYear(
                    year=int(r["year"]),
                    income_basis=r.get("income_basis"),
                    vat_basis=r.get("vat_basis"),
                    closed=bool(r.get("closed_at")),
                )
                for r in rows
            ],
            rules_unconfirmed=unconfirmed,
            banner_text=BANNER_TEXT,
        )
