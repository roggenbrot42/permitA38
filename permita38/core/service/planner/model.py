"""Input data classes of the planner engine (WP2/WP3 interface of docs/specs/project-centric-planning.md).

Pure data: no storage, no calculation. Hours are ``float``, money is ``Decimal``. Weeks are ISO weeks.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Literal

OwnerKind = Literal["employee", "boss", "subcontractor"]
DayOff = Literal["holiday", "vacation", "sick", "other", "unavailable"]
#: the public holiday's name or None (``core/holidays.py`` for the state)
Holiday = Callable[[date], str | None]


@dataclass(frozen=True)
class WorkLimits:
    """S6: the boss's own limits ("My settings")."""

    info_day: float = 10.0
    warn_day: float = 12.0
    max_day: float = 16.0
    info_week: float = 60.0
    info_average: float = 48.0


@dataclass(frozen=True)
class Owner:
    id: str
    name: str
    kind: OwnerKind
    weekly_hours: float  # 0 for a subcontractor (not team capacity)
    workdays: tuple[int, ...]  # 0 = Monday
    days_off: Mapping[date, DayOff] = field(default_factory=dict)  # absences or unavailable windows, expanded
    busy: Mapping[date, float] = field(default_factory=dict)  # out-of-office / all-day block hours (D10)
    limits: WorkLimits | None = None  # only for kind "boss"


@dataclass(frozen=True)
class Assignment:
    id: str
    owner_id: str
    project_id: str
    project_kind: Literal["customer", "internal"]
    start: date
    end: date
    hours_per_week: float | None  # exactly one of the two is set
    total_hours: float | None
    booked: float = 0.0  # total assignments: hours the owner booked on the project in the period


@dataclass(frozen=True)
class Item:
    """A contract (must) or an open offer (could)."""

    kind: Literal["contract", "offer"]
    id: str
    title: str
    project_id: str | None  # None = the scope "unassigned" (C5)
    start: date
    end: date
    hours_per_week: float | None
    total_hours: float | None
    rate: Decimal
    win: float = 1.0  # offers: 0..1; contracts always 1.0


@dataclass(frozen=True)
class Booking:
    """Time entries summed per project and day (all people)."""

    project_id: str
    day: date
    hours: float
