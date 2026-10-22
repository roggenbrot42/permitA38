"""Input validation primitives (first version of the sanity layer).

Request bodies are validated with the strict ``*In`` models in :mod:`permita38.core.models`; these types are their
building blocks. Hard limits only: a value that can never be right (a date like ``2026-13-45``, ``NaN``, minutes
beyond one day, a negative expense) is rejected with HTTP 422 and a readable message. "Suspicious but possible"
values are left to a later soft-warning layer.
"""

import math
import re
from datetime import date
from typing import Annotated, Any, Callable

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints, model_validator

DATE_MIN = date(2000, 1, 1)
DATE_MAX = date(2100, 12, 31)
MAX_MONEY = 1e9
MAX_QTY = 1e7
MAX_RATE = 10_000
MAX_LINES = 500
ID_PATTERN = r"^[0-9a-z]{6,32}$"
ATTACHMENT_PATTERN = r"^[0-9a-z]{6,32}\.[a-z0-9]{1,5}$"
ALLOWED_VAT_RATES = (0, 5, 7, 16, 19)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
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


def _clock_time(value: str) -> str:
    if not _TIME_RE.match(value):
        raise ValueError("must be a time like 08:30")
    return value


def _color(value: str) -> str:
    if not _COLOR_RE.match(value):
        raise ValueError("must be a colour like #2563eb")
    return value


def _optional(fn: Callable[[str], str]) -> Callable[[str | None], str | None]:
    return lambda v: None if v is None else fn(v)


ISODate = Annotated[str, AfterValidator(_iso_date)]
OptDate = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_iso_date))]
OptTime = Annotated[str | None, BeforeValidator(blank_to_none), AfterValidator(_optional(_clock_time))]
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

Amount = Annotated[float, Field(ge=-MAX_MONEY, le=MAX_MONEY, allow_inf_nan=False)]
NonNegAmount = Annotated[float, Field(ge=0, le=MAX_MONEY, allow_inf_nan=False)]
Qty = Annotated[float, Field(ge=-MAX_QTY, le=MAX_QTY, allow_inf_nan=False)]
Rate = Annotated[float, Field(ge=0, le=MAX_RATE, allow_inf_nan=False)]
Hours = Annotated[float, Field(ge=0, le=168, allow_inf_nan=False)]
TotalHours = Annotated[float, Field(ge=0, le=100_000, allow_inf_nan=False)]
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


class StrictRecord(BaseModel):
    """Base for request models: unknown fields are kept (forward compatibility) but every value is checked."""

    model_config = ConfigDict(extra="allow")

    @model_validator(mode="before")
    @classmethod
    def _sane_values(cls, data: Any) -> Any:
        _check_value(data)
        return data


def check_period(start: str | None, end: str | None, what: str = "end") -> None:
    """Both are already valid ISO dates (or None)."""
    if start and end and end < start:
        raise ValueError(f"{what} must not be before the start date")
