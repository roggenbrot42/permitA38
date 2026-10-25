"""Typed records. `extra="allow"` keeps unknown fields so files written by newer versions or
other devices survive a round-trip through this version."""

from datetime import datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_serializer,
    model_validator,
)

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
    trip_id: str | None = None  # the bill belongs to this business trip (its figures are not in the expense)


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


class AuthInfo(BaseModel):
    model_config = ConfigDict(extra="allow")

    @model_serializer(mode="wrap")
    def _without_nulls(self, handler):
        return {k: val for k, val in handler(self).items() if val is not None}

    kind: Literal["session", "token"]
    token_id: str | None = Field(None, max_length=32)
    label: str | None = Field(None, max_length=80)
    read_only: bool | None = None
    expires_at: int | None = Field(None, ge=0, le=4_102_444_800 * 2)


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
    auth: AuthInfo | None = Field(
        None, description="how this request was authenticated (GET /auth/me only): a session or an API token"
    )


class UserIn(v.CheckedBody):
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


class TimelineAbsence(BaseModel):
    """One stored absence as the viewer may see it (``kind`` and ``label`` are null where the level hides them)."""

    model_config = ConfigDict(extra="allow")
    start: str
    end: str
    kind: str | None = None
    label: str | None = None


class TimelineContract(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str | None = None
    title: str | None = None
    customer: str | None = None
    start: str
    end: str | None = None
    hours_per_week: float | None = None


class TimelinePerson(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    name: str | None = None
    color: str | None = None
    weekly_hours: float | None = None
    absences: list[TimelineAbsence] = []
    contracts: list[TimelineContract] = []


class TeamTimeline(BaseModel):
    """GET /api/team/timeline (docs/contracts/team-timeline.md)."""

    model_config = ConfigDict(extra="allow")
    level: str
    start: str
    end: str
    holidays: dict[str, str] = {}
    people: list[TimelinePerson] = []


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
    trip_id: v.OptId = None

    @model_validator(mode="after")
    def _net_or_lines(self):
        if not self.lines and self.net is None:
            raise ValueError("net is required for a bill without lines")
        return self


class LineIn(v.CheckedBody):
    model_config = ConfigDict(extra="allow")
    description: v.Desc = ""
    qty: v.Qty = 0
    unit: Annotated[str, StringConstraints(max_length=30)] | None = None
    price: v.Amount = 0
    vat: v.Percent | None = None
    amount: v.Amount | None = None  # ignored: always recomputed on the server


class PlanIn(v.CheckedBody):
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


TeamLevel = Literal["off", "away", "time-off", "full"]


class TeamTimelineVisibilityIn(v.StrictRecord):
    """``team_timeline_visibility`` of PUT /settings: the level per role. Only ``employee`` and ``accountant`` (the
    boss is always ``full``); any other key, the boss included, is rejected. A missing key keeps its value."""

    model_config = ConfigDict(extra="forbid")
    employee: TeamLevel = "away"
    accountant: TeamLevel = "off"


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
    team_timeline_visibility: TeamTimelineVisibilityIn = TeamTimelineVisibilityIn()

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


class CalendarIn(v.CheckedBody):
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


class CalendarPatchIn(v.CheckedBody):
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
class AbsenceChange(v.CheckedBody):
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


class CommitmentChange(v.CheckedBody):
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


ALLOCATION_MAX_DAYS = 1830  # longest period of one allocation (about five years)


def _check_alloc_period(start: str | None, end: str | None) -> None:
    v.check_period(start, end)
    if start and end and (datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")).days > ALLOCATION_MAX_DAYS:
        raise ValueError(f"the period may be at most {ALLOCATION_MAX_DAYS} days")


class AllocationChange(v.CheckedBody):
    """What-if change of an employee allocation. ``id`` null = a new one; omitted fields keep the stored value."""

    model_config = ConfigDict(extra="forbid")
    id: v.OptId = None
    delete: bool = False
    user_id: v.OptId = None
    project_id: v.OptId = None  # never "unassigned": that scope is implicit
    start: v.OptDate = None
    end: v.OptDate = None
    hours_per_week: v.AllocHours | None = None

    @model_validator(mode="after")
    def _rules(self):
        _check_alloc_period(self.start, self.end)
        if self.project_id == v.UNASSIGNED:
            raise ValueError("project_id must be a project")
        if self.delete and not self.id:
            raise ValueError("delete needs an id")
        if not self.id and not self.delete:
            missing = [
                k for k in ("user_id", "project_id", "start", "end", "hours_per_week") if getattr(self, k) is None
            ]
            if missing:
                raise ValueError("a new allocation needs " + ", ".join(missing))
        return self


class ScenarioChanges(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    absences: list[AbsenceChange] = Field(default=[], max_length=500)
    commitments: list[CommitmentChange] = Field(default=[], max_length=500)
    allocations: list[AllocationChange] = Field(default=[], max_length=500)


class AllocationIn(v.CheckedBody):
    """An employee allocation: hours per week of one person given to one project for a period."""

    model_config = ConfigDict(extra="forbid")
    user_id: v.Id
    project_id: v.Id  # never "unassigned": that scope is implicit
    start: v.ISODate
    end: v.ISODate  # end < start is a 409 of the service; a period beyond 1830 days is a 422
    hours_per_week: v.AllocHours

    @model_validator(mode="after")
    def _rules(self):
        if self.project_id == v.UNASSIGNED:
            raise ValueError("project_id must be a project")
        if self.end >= self.start:
            _check_alloc_period(self.start, self.end)
        return self


class AllocationBootstrapIn(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    confirm: bool = False


class Allocation(BaseModel):
    """Response: an employee or (once the subcontractor feature exists) external allocation."""

    model_config = ConfigDict(extra="allow")
    id: str
    kind: Literal["employee", "external"]
    user_id: str | None = None
    supplier_id: str | None = None
    incoming_offer_id: str | None = None
    project_id: str
    contract_id: str | None = None
    customer_id: str | None = None
    start: str
    end: str
    hours_per_week: float | None = None
    total_hours: float | None = None
    rate: float | None = None
    status: str = "active"
    cancelled_from: str | None = None
    cancel_reason: str | None = None
    created: int | None = None
    updated: int | None = None
    # response only
    project_name: str | None = None
    user_name: str | None = None
    supplier_name: str | None = None
    planned_hours: float | None = None
    planning_warnings: list[dict] | None = None  # only in POST/PUT answers


class TimerStartIn(v.CheckedBody):
    """Start the running timer of the current user (replaces a running one)."""

    project_id: v.OptId = None
    task_id: v.OptId = None
    note: v.Note = ""


class TimerStopIn(v.CheckedBody):
    """Stop the running timer and save it as a time entry; the note is optional."""

    note: v.Note | None = None


class ScenarioIn(v.StrictRecord):
    name: v.Name
    changes: ScenarioChanges = ScenarioChanges()


class PlanScenario(Record):
    name: str
    user_id: str | None = None
    changes: dict = {}


# -- business trips (docs/specs/business-trips.md) -------------------------------------------------------------
TRIP_MAX_DAYS = 90
TRIP_MAX_PLACES = 20
TRIP_MAX_MILEAGE = 100


class TripPlace(BaseModel):
    """The destination from a given day on (the first entry is the outbound destination)."""

    model_config = ConfigDict(extra="allow")
    from_date: str
    country: str
    city: str | None = None


class TripMeal(BaseModel):
    model_config = ConfigDict(extra="allow")
    date: str
    breakfast: bool = False
    lunch: bool = False
    dinner: bool = False


class TripMileage(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    date: str
    from_: str | None = Field(None, alias="from")
    to: str | None = None
    km: float
    purpose: str | None = None


class TripReduction(BaseModel):
    model_config = ConfigDict(extra="allow")
    meal: Literal["breakfast", "lunch", "dinner"]
    amount: float


class TripDay(BaseModel):
    """One calendar day of the trip (stored with the trip, returned by preview and the trip)."""

    model_config = ConfigDict(extra="allow")
    date: str
    country: str  # the country whose rate row was used (LU after fallback)
    city: str | None = None  # omitted when the place has no city
    absence_hours: float
    base: float
    reductions: list[TripReduction] = []
    amount: float
    note: str = ""


class TripBills(BaseModel):
    """The linked bills (expenses with this trip_id): count and sums of their net and VAT."""

    count: int = 0
    net: float = 0
    vat: float = 0


class TripChecklistItem(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str
    label: str
    hint: str
    state: Literal["added", "open", "not_needed"]
    count: int = 0
    sum: float = 0


class TripFigures(BaseModel):
    """Computed figures; the same numbers the trip stores, preview returns them without saving."""

    model_config = ConfigDict(extra="allow")
    days: list[TripDay] = []
    allowance_total: float = 0
    mileage_km: float = 0
    mileage_rate: float | None = None
    mileage_total: float = 0
    rules_version: str | None = None


class Trip(Record):
    user_id: str | None = None
    title: str | None = None
    purpose: str | None = None
    customer_id: str | None = None
    project_id: str | None = None
    start: str | None = None
    end: str | None = None
    places: list[TripPlace] = []
    meals_provided: list[TripMeal] = []
    mileage: list[TripMileage] = []
    not_needed: list[str] = []
    cost_centre: Literal["project", "general"] | None = None
    status: Literal["draft", "final"] = "draft"
    allowance_total: float | None = None
    mileage_km: float | None = None
    mileage_total: float | None = None
    rules_version: str | None = None
    days: list[TripDay] = []
    bills: TripBills | None = None  # response-only, computed on every read
    checklist: list[TripChecklistItem] | None = None  # response-only, computed on every read


class TripPlaceIn(v.StrictRecord):
    from_date: v.ISODate
    country: Annotated[str, StringConstraints(pattern=r"^[A-Z]{2}$")]  # ISO 3166-1 alpha-2, e.g. DE, AT
    city: v.Short | None = None


class TripMealIn(v.StrictRecord):
    date: v.ISODate
    breakfast: bool = False
    lunch: bool = False
    dinner: bool = False


class TripMileageIn(v.StrictRecord):
    date: v.ISODate
    from_: v.Short | None = Field(None, alias="from")
    to: v.Short | None = None
    km: Annotated[float, Field(ge=0.1, le=5000, allow_inf_nan=False)]
    purpose: v.Short | None = None

    @field_validator("km")
    @classmethod
    def _one_decimal(cls, value: float) -> float:
        if Decimal(repr(value)) % Decimal("0.1") != 0:
            raise ValueError("must have at most one decimal place (0.1 km)")
        return value


class TripIn(v.StrictRecord):
    """A business trip. Figures (allowance, mileage, days) are computed by the server, never taken from the client."""

    title: v.Short | None = None
    purpose: v.Name
    customer_id: v.OptId = None
    project_id: v.OptId = None
    start: v.LocalDateTime
    end: v.LocalDateTime
    places: Annotated[list[TripPlaceIn], Field(max_length=TRIP_MAX_PLACES)] = []
    meals_provided: Annotated[list[TripMealIn], Field(max_length=90)] = []
    mileage: Annotated[list[TripMileageIn], Field(max_length=TRIP_MAX_MILEAGE)] = []
    not_needed: Annotated[list[v.Short], Field(max_length=20)] = []
    cost_centre: Literal["project", "general"] | None = None
    status: Literal["draft", "final"] = "draft"

    @model_validator(mode="after")
    def _period(self):
        from .trips_calc import effective_last  # an end of exactly 00:00 belongs to the day before

        start = datetime.strptime(self.start, "%Y-%m-%dT%H:%M")
        end = datetime.strptime(self.end, "%Y-%m-%dT%H:%M")
        if end <= start:
            raise ValueError("end must be after start")
        last = effective_last(start, end)
        days = (last - start.date()).days + 1
        if days > TRIP_MAX_DAYS:
            raise ValueError(f"a trip may cover at most {TRIP_MAX_DAYS} calendar days (this one covers {days})")
        for meal in self.meals_provided:
            if not start.date().isoformat() <= meal.date <= last.isoformat():
                raise ValueError(f"meal date {meal.date} is outside the trip")
        for row in self.mileage:
            if not start.date().isoformat() <= row.date <= last.isoformat():
                raise ValueError(f"mileage date {row.date} is outside the trip")
        if self.cost_centre == "project" and not (self.project_id or self.customer_id):
            raise ValueError("Choose a customer or project, or general")
        return self


class ExportRequest(v.CheckedBody):
    """Options of POST /api/data/export (docs/contracts/data-export.md). Unknown fields are refused."""

    model_config = ConfigDict(extra="forbid")

    scope: Literal["full", "master_data"] = "full"
    include_accounts: bool = False
    include_attachments: bool = True

    @model_validator(mode="after")
    def _master_data_is_plain(self) -> "ExportRequest":
        if self.scope == "master_data" and (self.include_accounts or "include_attachments" in self.model_fields_set):
            raise ValueError("include_accounts and include_attachments cannot be used with scope master_data")
        return self


# ---------------------------------------------------------------- API tokens (docs/contracts/api-tokens.md)
API_TOKEN_MAX_ACTIVE = 20
API_TOKEN_LABEL_MAX = 80
API_TOKEN_MAX_DAYS = 365
API_TOKEN_DEFAULT_DAYS = 90
EPOCH_MAX = 4_102_444_800  # 2100-01-01


def _token_label(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    if len(value) > API_TOKEN_LABEL_MAX:
        raise ValueError(f"must be at most {API_TOKEN_LABEL_MAX} characters")
    if any(ord(c) < 32 or ord(c) == 127 or 0x80 <= ord(c) < 0xA0 for c in value):
        raise ValueError("must not contain control characters")
    return value


class ApiTokenCreateIn(v.CheckedBody):
    """Create an API token for the signed-in user (the secret is in the response, once)."""

    model_config = ConfigDict(extra="forbid")
    label: Annotated[str, Field(max_length=400), AfterValidator(_token_label)]
    expires_in_days: Annotated[StrictInt, Field(ge=1, le=API_TOKEN_MAX_DAYS)] = API_TOKEN_DEFAULT_DAYS
    read_only: StrictBool = True


class ApiTokenOut(BaseModel):
    """A token as listed: never the secret and never the hash."""

    model_config = ConfigDict(extra="allow")
    id: str = Field(max_length=32)
    user_id: str = Field(max_length=32)
    label: str = Field(max_length=400)
    hint: str = Field(max_length=16)
    read_only: bool
    created: int
    expires_at: int
    last_used_at: int | None = None
    revoked_at: int | None = None
    status: Literal["active", "expired", "revoked"]
    owner_name: str | None = Field(None, max_length=400, description="only with ?all=true")


class ApiTokenCreated(BaseModel):
    token: str = Field(description="the secret, shown only in this response", max_length=64)
    item: ApiTokenOut


class RevokeAllResult(BaseModel):
    revoked: int


class AuditEntryOut(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str = Field(max_length=32)
    at: int
    token_id: str = Field(max_length=32)
    user_id: str = Field(max_length=32)
    method: str = Field(max_length=16)
    route: str = Field(max_length=255)
    status: int


class AuditPage(BaseModel):
    items: list[AuditEntryOut]
    next: int | None = Field(None, description="`before` value for the next page, null at the end")
    next_id: str | None = Field(None, description="`before_id` value for the next page (with `next`), null at the end")
