"""Input validation primitives (first version of the sanity layer).

Request bodies are validated with the strict ``*In`` models in :mod:`permita38.core.models`; these types are their
building blocks. Hard limits only: a value that can never be right (a date like ``2026-13-45``, ``NaN``, minutes
beyond one day, a negative expense) is rejected with HTTP 422 and a readable message. "Suspicious but possible"
values are left to a later soft-warning layer.
"""

import math
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Callable, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    StringConstraints,
    WithJsonSchema,
    model_validator,
)

from permita38.core import money

DATE_MIN = date(2000, 1, 1)
DATE_MAX = date(2100, 12, 31)
MAX_MONEY = 1e9
MAX_QTY = 1e7
MAX_RATE = 10_000
MAX_LINES = 500
MAX_EXPENSE_LINES = 50
ID_PATTERN = r"^[0-9a-z]{6,32}$"
ATTACHMENT_PATTERN = r"^[0-9a-z]{6,32}\.[a-z0-9]{1,5}$"
ALLOWED_VAT_RATES = (0, 5, 7, 16, 19)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_LOCAL_DT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T([01]\d|2[0-3]):[0-5]\d$")
_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_INT64 = 2**63


def blank_to_none(value: Any) -> Any:
    """The web forms send empty strings for 'not set'."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _iso_date(value: str) -> str:
    if not _DATE_RE.match(value):
        raise ValueError("must be a date like 2026-10-22")
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise ValueError("is not a real calendar date") from None
    if not DATE_MIN <= parsed <= DATE_MAX:
        raise ValueError(f"must be between {DATE_MIN} and {DATE_MAX}")
    return value


iso_date = _iso_date  # public name for read-only checks (stored data check)


def _local_datetime(value: str) -> str:
    """``YYYY-MM-DDTHH:MM`` in local time (business trips); a real calendar date and time of day."""
    if not _LOCAL_DT_RE.match(value):
        raise ValueError("must be a local date and time like 2026-11-09T08:30")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("is not a real date and time") from None
    return value


local_datetime = _local_datetime


def _clock_time(value: str) -> str:
    if not _TIME_RE.match(value):
        raise ValueError("must be a time like 08:30")
    return value


def _timezone(value: str) -> str:
    if len(value) > 64 or not re.match(r"^[A-Za-z][A-Za-z0-9_+\-]*(/[A-Za-z0-9_+\-]+)*$", value):
        raise ValueError("must be an IANA time zone name like Europe/Berlin")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("is not a known IANA time zone") from None
    return value


def _keyword(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("must not be empty")
    return value


def _color(value: str) -> str:
    if not _COLOR_RE.match(value):
        raise ValueError("must be a colour like #2563eb")
    return value


_COUNTRY_RE = re.compile(r"^[A-Z]{2}$")
_IBAN_RE = re.compile(r"^[A-Z]{2}[0-9]{2}[A-Z0-9]{1,30}$")


def _country(value: str) -> str:
    if not _COUNTRY_RE.match(value):
        raise ValueError("must be a two-letter country code like DE")
    return value


def _iban(value: str) -> str:
    """Spaces are removed and letters upper-cased; the format (not the checksum, that is a warning) is a hard bound."""
    if len(value) > 60:
        raise ValueError("is too long for an IBAN")
    compact = "".join(value.split()).upper()
    if len(compact) > 34 or not _IBAN_RE.match(compact):
        raise ValueError("must be an IBAN like DE02120300000000202051 (spaces are allowed)")
    return compact


def _weekdays(value: list[int]) -> list[int]:
    if len(set(value)) != len(value):
        raise ValueError("must be unique weekday numbers 0 (Monday) to 6 (Sunday)")
    return sorted(value)


def _tags(value: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in value:
        tag = raw.strip()
        if not 1 <= len(tag) <= 50:
            raise ValueError("each tag must have 1 to 50 characters")
        if tag.casefold() not in seen:
            seen.add(tag.casefold())
            out.append(tag)
    return out


def _optional(fn: Callable[[str], str]) -> Callable[[str | None], str | None]:
    return lambda v: None if v is None else fn(v)


ISODate = Annotated[str, AfterValidator(_iso_date)]
LocalDateTime = Annotated[str, AfterValidator(_local_datetime)]
OptDate = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_iso_date))]
OptTime = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_clock_time))]
OptTimeZone = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_timezone))]
OptColor = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_color))]
Color = Annotated[str, AfterValidator(_color)]

Id = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
OptId = Annotated[Id | None, BeforeValidator(blank_to_none)]
IdList = Annotated[list[Id], Field(max_length=200)]
Attachment = Annotated[str, StringConstraints(pattern=ATTACHMENT_PATTERN)]
OptAttachment = Annotated[Attachment | None, BeforeValidator(blank_to_none)]


def _not_blank(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be empty")
    return value


# values are stored exactly as typed (no silent trimming); a name just must not be blank
Name = Annotated[str, StringConstraints(min_length=1, max_length=200), AfterValidator(_not_blank)]
Short = Annotated[str, StringConstraints(max_length=200)]
Desc = Annotated[str, StringConstraints(max_length=2000)]
Note = Annotated[str, StringConstraints(max_length=5000)]
Keyword = Annotated[str, StringConstraints(max_length=100), AfterValidator(_keyword)]


# ---- money input: a JSON number (as before) or a decimal string "12.50" (docs/specs/decimal-money-api.md rule 2) ----
MONEY_STR_PATTERN = r"^-?[0-9]{1,10}(\.[0-9]{1,6})?$"  # matched with fullmatch: no trailing newline either
_MONEY_STR_RE = re.compile(MONEY_STR_PATTERN)


def _money_input(value: Any) -> Any:
    """Number or decimal string -> float (the value keeps today's Python type). Bools and every other string form
    (comma, exponent, spaces, thousands separators) are refused."""
    if isinstance(value, bool):
        raise ValueError("must be a number or a decimal string like 12.50")
    if isinstance(value, str):
        if not _MONEY_STR_RE.fullmatch(value):
            raise ValueError("must be a number or a decimal string like 12.50 (dot as decimal separator)")
        try:
            return float(Decimal(value))
        except InvalidOperation:  # pragma: no cover - the pattern guarantees a valid number
            raise ValueError("must be a number or a decimal string like 12.50") from None
    return value


def _money_in(ge: float, le: float) -> Any:
    number: dict[str, Any] = {"type": "number", "minimum": ge, "maximum": le}
    schema = {"anyOf": [number, {"type": "string", "pattern": MONEY_STR_PATTERN}]}
    return Annotated[
        float, Field(ge=ge, le=le, allow_inf_nan=False), BeforeValidator(_money_input), WithJsonSchema(schema)
    ]


Amount = _money_in(-MAX_MONEY, MAX_MONEY)
NonNegAmount = _money_in(0, MAX_MONEY)
MonthlyAmount = _money_in(0, 100_000)
Qty = Annotated[float, Field(ge=-MAX_QTY, le=MAX_QTY, allow_inf_nan=False)]
Rate = _money_in(0, MAX_RATE)
Hours = Annotated[float, Field(ge=0, le=168, allow_inf_nan=False)]
TotalHours = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]
# hours per week of one allocation (docs/contracts/planner-per-project.md); stored as sent, no rounding
# contract / offer plan / commitment hours per week: (0, 10000]; total hours: (0, 100000]
ContractHours = Annotated[float, Field(gt=0, le=10_000, allow_inf_nan=False)]
PositiveTotalHours = Annotated[float, Field(gt=0, le=100_000, allow_inf_nan=False)]
AllocHours = Annotated[float, Field(ge=0.25, le=80, allow_inf_nan=False)]
UNASSIGNED = "unassigned"
# a planner scope: a project id or the literal "unassigned" (which also matches the id pattern)
ScopeId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]


def _not_unassigned(value: str | None) -> str | None:
    if value == UNASSIGNED:
        raise ValueError("project_id must be a project, not 'unassigned'")
    return value


# a stored project reference: like Id / OptId, but never the implicit planner scope "unassigned"
ProjectId = Annotated[Id, AfterValidator(_not_unassigned)]
OptProjectId = Annotated[ProjectId | None, BeforeValidator(blank_to_none)]
NonNegQty = Annotated[float, Field(ge=0, le=MAX_QTY, allow_inf_nan=False)]
Country = Annotated[str, AfterValidator(_country)]
Iban = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_iban))]  # None allowed
Weekdays = Annotated[
    list[Annotated[int, Field(strict=True, ge=0, le=6)]], Field(min_length=1, max_length=7), AfterValidator(_weekdays)
]
Tags = Annotated[list[Annotated[str, StringConstraints(max_length=200)]], Field(max_length=20), AfterValidator(_tags)]
PayDays = Annotated[int, Field(ge=0, le=365)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]


def _real_number(value: Any) -> Any:
    """JSON numbers only: no strings, no booleans (an int is fine for a float field)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("must be a number")
    return value


def _real_int(value: Any) -> Any:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("must be an integer")
    return value


_NUM = BeforeValidator(_real_number)
_INT = BeforeValidator(_real_int)
StrictNonNegAmount = _money_in(0, MAX_MONEY)
StrictMonthlyAmount = _money_in(0, 100_000)
StrictAmount100k = _money_in(0, 100_000)
StrictAmount10k = _money_in(0, 10_000)
StrictPercent10 = Annotated[float, Field(ge=0, le=10, allow_inf_nan=False), _NUM]
StrictPercent15 = Annotated[float, Field(ge=0, le=15, allow_inf_nan=False), _NUM]
StrictCount15 = Annotated[int, Field(ge=0, le=15), _INT]
StrictTaxClass = Annotated[int, Field(ge=1, le=6), _INT]
Minutes = Annotated[int, Field(ge=1, le=1440)]


class DecimalOutput:
    """Marker in the metadata of ``Money`` / ``UnitPrice``: ``api.responses.present`` converts such fields."""

    def __init__(self, kind: str) -> None:
        self.kind = kind

    def __repr__(self) -> str:
        return f"DecimalOutput({self.kind!r})"


def _lenient_money(value: Any) -> Any:
    """Stored float, int or decimal string -> float (the Python value keeps its stored type). Anything else is left
    for the float validation to judge."""
    if isinstance(value, str) and _MONEY_STR_RE.fullmatch(value):
        return float(Decimal(value))
    return value


def _decimal_out(value: Any) -> Any:
    """Decimal string for JSON; a NaN/inf value (damaged row) is returned unchanged instead of raising."""
    if isinstance(value, float) and not math.isfinite(value):
        return value
    return money.to_decimal_str(value)


_MONEY_OUT_SCHEMA = {"type": "string", "format": "decimal", "pattern": r"^-?[0-9]+\.[0-9]{2,6}$"}


def _money_out(kind: str) -> Any:
    return Annotated[
        float,
        BeforeValidator(_lenient_money),
        PlainSerializer(_decimal_out, return_type=Any, when_used="json"),
        WithJsonSchema(_MONEY_OUT_SCHEMA),
        DecimalOutput(kind),
    ]


# response amounts (net, VAT, gross, totals) and unit prices (EUR/h, EUR/km, price per unit): a decimal string in JSON
Money = _money_out("money")
UnitPrice = _money_out("unit_price")


# ---- Decimal-yielding money input of the accounting models (docs/specs/accounting.md 7a) ----
CENT_STEP = Decimal("0.01")


def _decimal_money(value: Any) -> Any:
    """Number or decimal string -> exact Decimal (floats via ``repr``, never float arithmetic). Bools, NaN/inf and every
    other string form are refused."""
    if isinstance(value, bool):
        raise ValueError("must be a number or a decimal string like 12.50")
    if isinstance(value, str):
        if not _MONEY_STR_RE.fullmatch(value):
            raise ValueError("must be a number or a decimal string like 12.50 (dot as decimal separator)")
        return Decimal(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("must be a finite number")
        return Decimal(repr(value))
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("must be a finite number")
        return value
    raise ValueError("must be a number or a decimal string like 12.50")


def money_in(lo: str, hi: str, *, exclusive_min: bool = False) -> Any:
    """A strict money input yielding ``Decimal`` in whole cents within ``[lo, hi]`` (``(lo, hi]`` when
    ``exclusive_min``). Accepts a JSON number or a decimal string; more than two decimals are refused."""
    low, high = Decimal(lo), Decimal(hi)

    def check(value: Decimal) -> Decimal:
        if value != value.quantize(CENT_STEP):
            raise ValueError("must be a whole number of cents (at most two decimals)")
        if (value <= low) if exclusive_min else (value < low):
            raise ValueError(f"must be greater than {lo}" if exclusive_min else f"must be at least {lo}")
        if value > high:
            raise ValueError(f"must be at most {hi}")
        return value.quantize(CENT_STEP)

    number: dict[str, Any] = {"type": "number", "maximum": float(high)}
    number["exclusiveMinimum" if exclusive_min else "minimum"] = float(low)
    schema = {"anyOf": [number, {"type": "string", "pattern": MONEY_STR_PATTERN}]}
    return Annotated[Decimal, BeforeValidator(_decimal_money), AfterValidator(check), WithJsonSchema(schema)]


MoneyIn = money_in("-1000000000", "1000000000")
PositiveMoneyIn = money_in("0", "1000000000", exclusive_min=True)
PaymentAmountIn = money_in("0", "10000000", exclusive_min=True)  # one payment: > 0 and <= 10,000,000.00

# tax treatments (docs/specs/credit-notes-payments.md 5). Values reserved for later releases are refused with 422.
RESERVED_SALES_TREATMENTS = (
    "reverse_charge_domestic",
    "eu_b2b_goods_exempt",
    "export_exempt",
    "non_taxable_other",
    "exempt_with_deduction",
    "exempt_no_deduction",
)
RESERVED_EXPENSE_TREATMENTS = ("reverse_charge_domestic", "import_vat")


def _refuse_reserved(reserved: tuple[str, ...]) -> Callable[[Any], Any]:
    def check(value: Any) -> Any:
        if isinstance(value, str) and value in reserved:
            raise ValueError("not supported in this release")
        return value

    return check


SalesTreatment = Annotated[
    Literal["standard", "small_business", "eu_b2b_service"],
    BeforeValidator(_refuse_reserved(RESERVED_SALES_TREATMENTS)),
]
ExpenseTreatment = Annotated[
    Literal["standard", "no_input_vat", "reverse_charge_eu_service", "reverse_charge_third_country"],
    BeforeValidator(_refuse_reserved(RESERVED_EXPENSE_TREATMENTS)),
]
CreditKind = Literal["storno", "partial", "discount", "write_off", "write_off_reversal"]  # stored kinds
CreditNoteKindIn = Literal["storno", "partial", "write_off"]  # the kinds a client may request
PaymentKindIn = Literal["payment", "refund"]
PaymentMethod = Literal["bank", "cash"]
_RATE_KEY_RE = re.compile(r"[0-9]{1,2}(\.[0-9]{1,2})?")


def _rate_key(value: Any) -> Any:
    """A VAT rate as a map key, in its canonical form ("19.00" -> "19", "10.70" -> "10.7")."""
    if not isinstance(value, str) or not _RATE_KEY_RE.fullmatch(value):
        raise ValueError("must be a VAT rate such as 19 or 10.7")
    return format(Decimal(value).normalize(), "f")


RateKey = Annotated[
    str,
    BeforeValidator(_rate_key),
    WithJsonSchema({"type": "string", "pattern": r"^[0-9]{1,2}(\.[0-9]{1,2})?$"}),
]  # the service checks the rate against the invoice
MAX_CREDIT_RATES = 20


def _unique_rate_keys(value: Any) -> Any:
    """Refuse two keys that name one rate after normalisation (so "19" and "19.00" cannot both be sent)."""
    if not isinstance(value, dict):
        return value
    seen: set[Any] = set()
    for key in value:
        canonical = key
        if isinstance(key, str) and _RATE_KEY_RE.fullmatch(key):
            canonical = _rate_key(key)
        if canonical in seen:
            raise ValueError(f"the rate {canonical} is given twice")
        seen.add(canonical)
    return value


def _check_value(value: Any, depth: int = 0) -> None:
    """Reject values that can never be stored or computed with, anywhere in a request body."""
    if depth > 8:
        raise ValueError("is nested too deeply")
    if isinstance(value, str):
        if len(value) > 100_000:
            raise ValueError("text is too long")
        if "\x00" in value:
            raise ValueError("contains a NUL character")
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            raise ValueError("contains characters that cannot be stored (invalid Unicode)") from None
    elif isinstance(value, bool) or value is None:
        return
    elif isinstance(value, int):
        if not -_INT64 < value < _INT64:
            raise ValueError("number is out of range")
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("number must be finite")
        if abs(value) > 1e300:
            raise ValueError("number is out of range")
    elif isinstance(value, dict):
        if len(value) > 1000:
            raise ValueError("too many fields")
        for k, v in value.items():
            if not isinstance(k, str) or len(k) > 100:
                raise ValueError("invalid field name")
            _check_value(k, depth + 1)
            _check_value(v, depth + 1)
    elif isinstance(value, (list, tuple)):
        if len(value) > 5000:
            raise ValueError("too many items")
        for v in value:
            _check_value(v, depth + 1)


class CheckedBody(BaseModel):
    """Base for every request body: each value in the raw body (also unknown fields) is checked for NUL characters,
    lone surrogates, length and nesting before the declared fields are validated. Keeps the model's own ``extra``
    setting."""

    @model_validator(mode="before")
    @classmethod
    def _sane_values(cls, data: Any) -> Any:
        _check_value(data)
        return data


class StrictRecord(CheckedBody):
    """Base for request models: unknown fields are kept (forward compatibility) but every value is checked."""

    model_config = ConfigDict(extra="allow")


def form_value_errors(fields: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Multipart text fields have no model: they get the same checks as a JSON body. Returns pydantic-style errors
    with ``loc == (field name,)``; the API layer turns them into the usual 422 shape."""
    errors: list[dict[str, Any]] = []
    for name, value in fields.items():
        try:
            _check_value(value)
        except ValueError as err:
            errors.append({"loc": (name,), "msg": f"Value error, {err}", "type": "value_error"})
    return errors


def check_period(start: str | None, end: str | None, what: str = "end") -> None:
    """Both are already valid ISO dates (or None)."""
    if start and end and end < start:
        raise ValueError(f"{what} must not be before the start date")


# ---- accounting journal primitives (docs/specs/accounting.md 4, 8.1, 9.8, 11) ----
AccountNo = Annotated[str, StringConstraints(pattern=r"^[0-9A-Za-z_.-]{1,20}$")]  # a chart account number
RoleName = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,59}$")]  # a stable role name of the chart


def _known_tax_key(value: str) -> str:
    from permita38.core.vat_rules import TAX_KEYS

    if value not in TAX_KEYS:
        raise ValueError(f"unknown tax key; use one of {sorted(TAX_KEYS)}")
    return value


# S19, VST, EU_SVC, RC_EU_SVC, NONE ... (the keys of docs/specs/accounting.md 6)
TaxKeyName = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,29}$"), AfterValidator(_known_tax_key)]
TaxKind = Literal["tax", "input", "base_only"]
TaxRateText = Annotated[str, StringConstraints(pattern=r"^[0-9]{1,2}(\.[0-9]{1,2})?$")]
EntryText = Annotated[str, StringConstraints(min_length=1, max_length=200), AfterValidator(_not_blank)]
FiscalYearNo = Annotated[int, Field(ge=2000, le=2100, strict=True)]
MonthNo = Annotated[int, Field(ge=1, le=12, strict=True)]
QuarterNo = Annotated[int, Field(ge=1, le=4, strict=True)]
Chart = Literal["skr03", "skr04"]
IncomeBasis = Literal["eur", "accrual"]
VatBasis = Literal["soll", "ist"]
VatFrequency = Literal["month", "quarter", "none"]
VatPeriodKind = Literal["month", "quarter"]
Reference = Annotated[str, StringConstraints(max_length=100)]
LongNote = Annotated[str, StringConstraints(max_length=500)]
NonNegMoneyIn = money_in("0", "1000000000")
