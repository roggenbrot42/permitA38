"""Typed records. `extra="allow"` keeps unknown fields so files written by newer versions or
other devices survive a round-trip through this version."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from . import validation as v
from .holidays import STATES
from .numbering import PatternError, parse_pattern


class Record(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    created: int | None = None
    updated: int | None = None


class Customer(Record):
    name: str
    customer_no: str | None = None
    street: str | None = None
    zip: str | None = None
    city: str | None = None
    country: str | None = None
    tax_number: str | None = None
    vat_id: str | None = None
    contact: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    rate: float | None = None
    external_id: str | None = None
    import_batch: str | None = None


class Project(Record):
    name: str
    customer_id: str | None = None
    personal: bool = False
    rate: float | None = None
    budget_hours: float | None = None
    status: str = "active"
    color: str | None = None
    notes: str | None = None
    member_ids: list[str] = []
    offer_id: str | None = None
    start: str | None = None
    end: str | None = None
    extensions: list[dict] | None = None


class Task(Record):
    project_id: str
    name: str
    rate: float | None = None


class Entry(Record):
    user_id: str | None = None
    date: str
    minutes: int
    project_id: str | None = None
    task_id: str | None = None
    note: str | None = None
    start: str | None = None
    end: str | None = None
    billable: bool = True
    invoice_id: str | None = None
    rate: float | None = None


class Contract(Record):
    title: str
    customer_id: str | None = None
    project_id: str | None = None
    start: str
    end: str
    hours_per_week: float | None = None
    total_hours: float | None = None
    rate: float | None = None
    status: str = "confirmed"
    user_ids: list[str] = []
    offer_id: str | None = None


class Absence(Record):
    user_id: str | None = None
    kind: str = "vacation"
    start: str
    end: str | None = None
    note: str | None = None


class ExpenseLine(BaseModel):
    """One line of an itemised bill (docs/specs/itemised-expenses.md). net/vat are derived by the server."""

    model_config = ConfigDict(extra="allow")
    description: str = ""
    gross: float | None = None
    vat_rate: float | None = None
    net: float | None = None
    vat: float | None = None
    deductible: float = 100
    vat_deductible: float | None = None
    category: str | None = None


class Expense(Record):
    date: str
    net: float
    vat: float = 0
    vat_rate: float | None = None
    vendor: str | None = None
    description: str | None = None
    category: str | None = None
    project_id: str | None = None
    deductible: float = 100
    vat_deductible: float | None = None  # share of the input VAT that is reclaimed; None = same as `deductible`
    attachment: str | None = None
    external_id: str | None = None
    import_batch: str | None = None
    lines: list[ExpenseLine] | None = None  # itemised bill; absent for a classic single-amount expense


class Line(BaseModel):
    model_config = ConfigDict(extra="allow")
    description: str = ""
    qty: float = 0
    unit: str | None = None
    price: float = 0
    vat: float | None = None
    amount: float | None = None


class Plan(BaseModel):
    model_config = ConfigDict(extra="allow")
    start: str | None = None
    end: str | None = None
    hours_per_week: float | None = None
    total_hours: float | None = None
    user_ids: list[str] = []
    rate: float | None = None
    win_probability: float | None = None


class Document(Record):
    """An offer or an invoice."""

    number: str | None = None
    customer_id: str | None = None
    project_id: str | None = None
    status: str = "draft"
    date: str | None = None
    due_date: str | None = None
    valid_until: str | None = None
    paid_date: str | None = None
    service_from: str | None = None
    service_to: str | None = None
    title: str | None = None
    reference: str | None = None
    template_id: str | None = None
    language: str | None = None
    intro: str | None = None
    outro: str | None = None
    notes: str | None = None
    vat_rate: float | None = None
    lines: list[Line] = []
    plan: Plan | None = None
    offer_id: str | None = None
    # set only by the document importer
    imported: bool | None = None
    original_number: str | None = None
    external_id: str | None = None
    import_batch: str | None = None
    totals_unverified: bool | None = None
    original_pdf: str | None = None


class Template(Record):
    name: str
    layout: str = "classic"
    accent: str = "#1f4e79"
    language: str = "de"
    show_logo: bool = True
    intro_offer: str | None = None
    outro_offer: str | None = None
    intro_invoice: str | None = None
    outro_invoice: str | None = None
    closing: str | None = None


class UserOut(Record):
    username: str | None = None
    name: str | None = None
    role: str | None = None
    weekly_hours: float | None = None
    workdays: list[int] | None = None
    default_rate: float | None = None
    cost_rate: float | None = None
    color: str | None = None
    tracking_start: str | None = None
    active: bool | None = None
    has_password: bool | None = None
    totp_enabled: bool | None = None
    permissions: dict[str, dict[str, Literal["none", "limited", "all"]]] | None = Field(
        None, description="read/write scope per area, derived from the role (never stored)"
    )
    work_start: str | None = None
    work_end: str | None = None
    timezone: str | None = None


class UserIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    username: str = Field(max_length=64)
    name: v.Name
    role: Literal["boss", "employee", "accountant", "member"] = Field(
        "employee", description='"member" is the old name of "employee" and is still accepted'
    )
    password: str | None = Field(default=None, max_length=1000)
    weekly_hours: v.Hours = 40
    workdays: list[int] = Field(default=[0, 1, 2, 3, 4], min_length=1, max_length=7)
    default_rate: v.Rate | None = None
    cost_rate: v.Rate | None = None
    color: v.OptColor = None
    tracking_start: v.OptDate = None
    vacation_days_per_year: Annotated[float, Field(ge=0, le=366, allow_inf_nan=False)] | None = None
    vacation_carry_over: Annotated[float, Field(ge=0, le=366, allow_inf_nan=False)] | None = None
    active: bool = True
    work_start: v.OptTime = None  # working time used for calendar capacity (default 09:00)
    work_end: v.OptTime = None  # (default 17:00)
    timezone: v.OptTimeZone = None  # IANA zone of the working time (default Europe/Berlin)

    @field_validator("workdays")
    @classmethod
    def _workdays(cls, days: list[int]) -> list[int]:
        if any(d < 0 or d > 6 for d in days) or len(set(days)) != len(days):
            raise ValueError("must be unique weekday numbers 0 (Monday) to 6 (Sunday)")
        return sorted(days)


class Settings(BaseModel):
    model_config = ConfigDict(extra="allow")
    company_name: str = ""
    owner_name: str = ""
    street: str = ""
    zip: str = ""
    city: str = ""
    email: str = ""
    phone: str = ""
    website: str = ""
    tax_number: str = ""
    vat_id: str = ""
    bank_name: str = ""
    iban: str = ""
    bic: str = ""
    logo: str | None = None
    company_type: str = "freiberufler"
    accounting: str = "eur"
    kleinunternehmer: bool = False
    vat_rate: float = 19
    state: str = ""
    hebesatz: float = 400
    default_rate: float = 80
    payment_days: int = 14
    offer_valid_days: int = 30
    default_template: str = "classic"
    invoice_pattern: str = "RE-{year}-{n:04d}"
    offer_pattern: str = "AN-{year}-{n:04d}"
    invoice_reset: str = "yearly"  # never | yearly | monthly (see core/numbering.py)
    offer_reset: str = "yearly"
    numbering_shared: bool = False  # invoices and offers share one sequence
    social_monthly: float = 0
    church_tax: float = 0
    splitting: bool = False
    other_income: float = 0
    other_deductions: float = 0
    planning_min_rate: float = 0
    planning_default_win_probability: float = 100


# ======================================================================================================
# Strict request models. They accept exactly what is sane; the lenient classes above describe responses
# (a record stored by an older version or another device must never turn a *response* into an error).
# ======================================================================================================
DOC_STATUS = Literal["draft", "sent", "accepted", "rejected", "paid", "cancelled"]


class CustomerIn(v.StrictRecord):
    name: v.Name
    customer_no: v.Short | None = None
    street: v.Short | None = None
    zip: Annotated[str, StringConstraints(max_length=20)] | None = None
    city: v.Short | None = None
    country: v.Short | None = None
    tax_number: Annotated[str, StringConstraints(max_length=50)] | None = None
    vat_id: Annotated[str, StringConstraints(max_length=30)] | None = None
    contact: v.Short | None = None
    email: Annotated[str, StringConstraints(max_length=254)] | None = None
    phone: Annotated[str, StringConstraints(max_length=50)] | None = None
    notes: v.Note | None = None
    rate: v.Rate | None = None


class ProjectIn(v.StrictRecord):
    name: v.Name
    customer_id: v.OptId = None
    personal: bool = False
    rate: v.Rate | None = None
    budget_hours: v.TotalHours | None = None
    status: Literal["active", "paused", "done", "archived"] = "active"
    color: v.OptColor = None
    notes: v.Note | None = None
    member_ids: v.IdList = []
    offer_id: v.OptId = None
    start: v.OptDate = None
    end: v.OptDate = None

    @model_validator(mode="after")
    def _period(self):
        v.check_period(self.start, self.end, "project end")
        return self


class TaskIn(v.StrictRecord):
    project_id: v.Id
    name: v.Name
    rate: v.Rate | None = None


class EntryIn(v.StrictRecord):
    user_id: v.OptId = None
    date: v.ISODate
    minutes: v.Minutes
    project_id: v.OptId = None
    task_id: v.OptId = None
    note: v.Note | None = None
    start: v.OptTime = None
    end: v.OptTime = None
    billable: bool = True
    invoice_id: v.OptId = None
    rate: v.Rate | None = None


class ContractIn(v.StrictRecord):
    title: v.Name
    customer_id: v.OptId = None
    project_id: v.OptId = None
    start: v.ISODate
    end: v.ISODate
    hours_per_week: v.Hours | None = None
    total_hours: v.TotalHours | None = None
    rate: v.Rate | None = None
    status: Literal["confirmed", "done"] = "confirmed"
    user_ids: v.IdList = []
    offer_id: v.OptId = None

    @model_validator(mode="after")
    def _period(self):
        v.check_period(self.start, self.end)
        return self


class AbsenceIn(v.StrictRecord):
    user_id: v.OptId = None
    kind: Literal["vacation", "holiday", "sick", "other"] = "vacation"
    start: v.ISODate
    end: v.OptDate = None
    note: v.Note | None = None

    @model_validator(mode="after")
    def _period(self):
        v.check_period(self.start, self.end)
        return self


class ExpenseLineIn(v.StrictRecord):
    """One line of an itemised bill. ``net`` and ``vat`` sent by a client are ignored (derived by the server)."""

    description: v.Desc = ""
    gross: v.NonNegAmount  # as printed on the bill, incl. VAT
    vat_rate: v.Percent
    deductible: v.Percent = 100
    vat_deductible: v.Percent | None = None
    category: v.Short | None = None

    @field_validator("gross")
    @classmethod
    def _whole_cents(cls, value: float) -> float:
        # decimal representation of the float (repr), never float arithmetic
        if Decimal(repr(value)) % Decimal("0.01") != 0:
            raise ValueError("must be a whole number of cents (a multiple of 0.01)")
        return value


class ExpenseIn(v.StrictRecord):
    date: v.ISODate
    net: v.NonNegAmount | None = None  # required without lines; with lines it is derived by the server
    vat: v.NonNegAmount = 0
    vat_rate: v.Percent | None = None
    vendor: v.Short | None = None
    description: v.Desc | None = None
    category: v.Short | None = None
    project_id: v.OptId = None
    deductible: v.Percent = 100
    vat_deductible: v.Percent | None = None
    attachment: v.OptAttachment = None
    lines: Annotated[list[ExpenseLineIn], Field(max_length=v.MAX_EXPENSE_LINES)] | None = None

    @model_validator(mode="after")
    def _net_or_lines(self):
        if not self.lines and self.net is None:
            raise ValueError("net is required for a bill without lines")
        return self


class LineIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    description: v.Desc = ""
    qty: v.Qty = 0
    unit: Annotated[str, StringConstraints(max_length=30)] | None = None
    price: v.Amount = 0
    vat: v.Percent | None = None
    amount: v.Amount | None = None  # ignored: always recomputed on the server


class PlanIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    start: v.OptDate = None
    end: v.OptDate = None
    hours_per_week: v.Hours | None = None
    total_hours: v.TotalHours | None = None
    user_ids: v.IdList = []
    rate: v.Rate | None = None
    win_probability: v.Percent | None = None

    @model_validator(mode="after")
    def _period(self):
        v.check_period(self.start, self.end)
        return self


class DocumentIn(v.StrictRecord):
    """An offer or an invoice."""

    number: Annotated[str, StringConstraints(max_length=50)] | None = None
    customer_id: v.OptId = None
    project_id: v.OptId = None
    status: DOC_STATUS = "draft"
    date: v.OptDate = None
    due_date: v.OptDate = None
    valid_until: v.OptDate = None
    paid_date: v.OptDate = None
    service_from: v.OptDate = None
    service_to: v.OptDate = None
    title: v.Short | None = None
    reference: v.Short | None = None
    template_id: v.OptId = None
    language: Literal["", "de", "en"] | None = None
    intro: v.Note | None = None
    outro: v.Note | None = None
    notes: v.Note | None = None
    vat_rate: v.Percent | None = None
    lines: list[LineIn] = Field(default=[], max_length=v.MAX_LINES)
    plan: PlanIn | None = None
    offer_id: v.OptId = None

    @model_validator(mode="after")
    def _period(self):
        v.check_period(self.service_from, self.service_to, "service period end")
        return self


class TemplateIn(v.StrictRecord):
    name: v.Name
    layout: Literal["classic", "modern", "minimal"] = "classic"
    accent: v.Color = "#1f4e79"
    language: Literal["de", "en"] = "de"
    show_logo: bool = True
    intro_offer: v.Note | None = None
    outro_offer: v.Note | None = None
    intro_invoice: v.Note | None = None
    outro_invoice: v.Note | None = None
    closing: v.Short | None = None


class SettingsIn(v.StrictRecord):
    """PUT /settings: any subset of the settings, each value checked."""

    company_name: v.Short = ""
    owner_name: v.Short = ""
    street: v.Short = ""
    zip: Annotated[str, StringConstraints(max_length=20)] = ""
    city: v.Short = ""
    email: Annotated[str, StringConstraints(max_length=254)] = ""
    phone: Annotated[str, StringConstraints(max_length=50)] = ""
    website: Annotated[str, StringConstraints(max_length=300)] = ""
    tax_number: Annotated[str, StringConstraints(max_length=50)] = ""
    vat_id: Annotated[str, StringConstraints(max_length=30)] = ""
    bank_name: v.Short = ""
    iban: Annotated[str, StringConstraints(max_length=40)] = ""
    bic: Annotated[str, StringConstraints(max_length=20)] = ""
    logo: v.OptAttachment = None
    company_type: Literal["freiberufler", "gewerbe", "gmbh"] = "freiberufler"
    accounting: Literal["eur", "accrual"] = "eur"
    kleinunternehmer: bool = False
    vat_rate: v.Percent = 19
    state: str = ""
    hebesatz: Annotated[float, Field(ge=0, le=1000, allow_inf_nan=False)] = 400
    default_rate: v.Rate = 80
    payment_days: Annotated[int, Field(ge=0, le=365)] = 14
    offer_valid_days: Annotated[int, Field(ge=0, le=365)] = 30
    default_template: v.Id = "classic"
    invoice_pattern: Annotated[str, StringConstraints(max_length=100)] = "RE-{year}-{n:04d}"
    offer_pattern: Annotated[str, StringConstraints(max_length=100)] = "AN-{year}-{n:04d}"
    invoice_reset: Literal["never", "yearly", "monthly"] = "yearly"
    offer_reset: Literal["never", "yearly", "monthly"] = "yearly"
    numbering_shared: bool = False
    social_monthly: v.NonNegAmount = 0
    church_tax: Annotated[float, Field(ge=0, le=15, allow_inf_nan=False)] = 0
    splitting: bool = False
    other_income: v.NonNegAmount = 0
    other_deductions: v.NonNegAmount = 0
    planning_min_rate: v.Rate = 0
    planning_default_win_probability: v.Percent = 100

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        if value not in STATES:
            raise ValueError("is not a German federal state code")
        return value

    @field_validator("invoice_pattern", "offer_pattern")
    @classmethod
    def _numbering_pattern(cls, value: str) -> str:
        try:
            parse_pattern(value, strict=False)  # stored legacy patterns must still round-trip
        except PatternError as e:
            raise ValueError(str(e)) from None
        return value


CalendarTitlePolicy = Literal["none", "redacted", "full"]


class CalendarIn(BaseModel):
    """Connect a calendar: an https ICS link (``url``) or a file uploaded alongside these fields."""

    model_config = ConfigDict(extra="ignore")
    name: v.Name
    url: str | None = Field(default=None, max_length=2048)
    refresh_minutes: int = Field(default=360, ge=15, le=10080)
    title_policy: CalendarTitlePolicy = "none"
    all_day_blocks: bool = False
    include_keywords: list[v.Keyword] = Field(default=[], max_length=20)
    exclude_keywords: list[v.Keyword] = Field(default=[], max_length=20)
    busy_default: bool = True


class CalendarPatchIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    name: v.Name | None = None
    refresh_minutes: int | None = Field(default=None, ge=15, le=10080)
    title_policy: CalendarTitlePolicy | None = None
    all_day_blocks: bool | None = None
    include_keywords: list[v.Keyword] | None = Field(default=None, max_length=20)
    exclude_keywords: list[v.Keyword] | None = Field(default=None, max_length=20)
    busy_default: bool | None = None


class CalendarOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str | None = None
    kind: str | None = None
    url_masked: str | None = None  # the subscription address is a secret: only a masked form is returned
    refresh_minutes: int | None = None
    title_policy: str | None = None
    all_day_blocks: bool = False
    include_keywords: list[str] = []
    exclude_keywords: list[str] = []
    busy_default: bool = True
    last_fetched: int | None = None
    last_status: str | None = None
    last_error: str | None = None
    event_count: int = 0
    reupload_advised: bool = False  # an uploaded file whose stored period has run short: upload it again
    last_warnings: list[str] = []  # notes from the last import (unsupported rules, unknown zones ...), no user data


class CalendarEventOut(BaseModel):
    start: str
    end: str
    all_day: bool = False
    title: str | None = None  # only when the source's title policy allows it
    ooo: bool = False
    source_id: str | None = None
    source_name: str | None = None


# ---- planning: what-if changes and named scenarios -------------------------------------------------------------
class AbsenceChange(BaseModel):
    """Changes one absence (``id`` given) or adds one (no ``id``). ``delete`` removes an existing one."""

    model_config = ConfigDict(extra="forbid")
    id: v.OptId = None
    delete: bool = False
    user_id: v.OptId = None
    kind: Literal["vacation", "holiday", "sick", "other"] = "vacation"
    start: v.OptDate = None
    end: v.OptDate = None

    @model_validator(mode="after")
    def _period(self):
        v.check_period(self.start, self.end)
        return self


class CommitmentChange(BaseModel):
    """Overrides the period or hours of an existing contract or open offer. Omitted fields keep their value."""

    model_config = ConfigDict(extra="forbid")
    kind: Literal["contract", "offer"]
    id: v.Id
    start: v.OptDate = None
    end: v.OptDate = None
    hours_per_week: v.Hours | None = None
    total_hours: v.TotalHours | None = None

    @model_validator(mode="after")
    def _period(self):
        v.check_period(self.start, self.end)
        return self


class ScenarioChanges(BaseModel):
    model_config = ConfigDict(extra="forbid")
    absences: list[AbsenceChange] = Field(default=[], max_length=500)
    commitments: list[CommitmentChange] = Field(default=[], max_length=500)


class ScenarioIn(v.StrictRecord):
    name: v.Name
    changes: ScenarioChanges = ScenarioChanges()


class PlanScenario(Record):
    name: str
    user_id: str | None = None
    changes: dict = {}
