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
from typing import Annotated, Any, Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

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


def _local_datetime(value: str) -> str:
    """``YYYY-MM-DDTHH:MM`` in local time (business trips); a real calendar date and time of day."""
    if not _LOCAL_DT_RE.match(value):
        raise ValueError("must be a local date and time like 2026-11-09T08:30")
    try:
        datetime.strptime(value, "%Y-%m-%dT%H:%M")
    except ValueError:
        raise ValueError("is not a real date and time") from None
    return value


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

Amount = Annotated[float, Field(ge=-MAX_MONEY, le=MAX_MONEY, allow_inf_nan=False)]
NonNegAmount = Annotated[float, Field(ge=0, le=MAX_MONEY, allow_inf_nan=False)]
Qty = Annotated[float, Field(ge=-MAX_QTY, le=MAX_QTY, allow_inf_nan=False)]
Rate = Annotated[float, Field(ge=0, le=MAX_RATE, allow_inf_nan=False)]
Hours = Annotated[float, Field(ge=0, le=168, allow_inf_nan=False)]
TotalHours = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]
# hours per week of one allocation (docs/contracts/planner-per-project.md); stored as sent, no rounding
AllocHours = Annotated[float, Field(ge=0.25, le=80, allow_inf_nan=False)]
UNASSIGNED = "unassigned"
# a planner scope: a project id or the literal "unassigned" (which also matches the id pattern)
ScopeId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
NonNegQty = Annotated[float, Field(ge=0, le=MAX_QTY, allow_inf_nan=False)]
Country = Annotated[str, AfterValidator(_country)]
Iban = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_iban))]  # None allowed
Weekdays = Annotated[list[Annotated[int, Field(strict=True, ge=0, le=6)]], Field(min_length=1, max_length=7), AfterValidator(_weekdays)]
Tags = Annotated[list[Annotated[str, StringConstraints(max_length=200)]], Field(max_length=20), AfterValidator(_tags)]
PayDays = Annotated[int, Field(ge=0, le=365)]
Percent = Annotated[float, Field(ge=0, le=100, allow_inf_nan=False)]
Minutes = Annotated[int, Field(ge=1, le=1440)]


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
