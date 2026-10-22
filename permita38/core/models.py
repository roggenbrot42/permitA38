"""Typed records. `extra="allow"` keeps unknown fields so files written by newer versions or
other devices survive a round-trip through this version."""

from pydantic import BaseModel, ConfigDict


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
    attachment: str | None = None


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


class UserIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str | None = None
    username: str
    name: str
    role: str = "member"
    password: str | None = None
    weekly_hours: float = 40
    workdays: list[int] = [0, 1, 2, 3, 4]
    default_rate: float | None = None
    cost_rate: float | None = None
    color: str | None = None
    tracking_start: str | None = None
    active: bool = True


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
    social_monthly: float = 0
    church_tax: float = 0
    splitting: bool = False
    other_income: float = 0
    other_deductions: float = 0
