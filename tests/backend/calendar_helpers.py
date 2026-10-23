"""Shared helpers for calendar tests: a fake download server and ICS builders. All data is invented."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from permita38.core.netsafe import FetchedResource, FetchError

BERLIN = ZoneInfo("Europe/Berlin")
SECRET_TOKEN = "SECRET-TOKEN-7f3a9c51"
SECRET_URL = f"https://calendar.example.test/ics/{SECRET_TOKEN}/calendar.ics"


class FakeFetcher:
    """Stands in for the SSRF-safe fetcher: records calls, serves one body, honours ETag, can fail on demand."""

    def __init__(self, body: bytes = b"", etag: str = '"v1"'):
        self.body = body
        self.etag = etag
        self.error: FetchError | None = None
        self.calls: list[dict] = []

    def fetch_resource(self, url, *, accept, allowed_types, max_bytes, https_only=False, conditional=None, budget=None):
        self.calls.append({"url": url, "conditional": dict(conditional or {}), "https_only": https_only})
        if self.error is not None:
            raise self.error
        if conditional and conditional.get("etag") == self.etag:
            return FetchedResource(url=url, status=304, content_type="", body=b"")
        return FetchedResource(url=url, status=200, content_type="text/calendar", body=self.body, etag=self.etag)


def ics(*events: str) -> bytes:
    text = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//Fake Export//EN\r\n" + "".join(events) + "END:VCALENDAR\r\n"
    return text.encode("utf-8")


def timed(uid: str, summary: str, start: datetime, end: datetime, extra: str = "") -> str:
    """A timed event given in Europe/Berlin wall-clock time (the way calendar exports write it)."""
    fmt = "%Y%m%dT%H%M%S"
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\n"
        f"DTSTART;TZID=Europe/Berlin:{start.strftime(fmt)}\r\nDTEND;TZID=Europe/Berlin:{end.strftime(fmt)}\r\n"
        f"{extra}END:VEVENT\r\n"
    )


def all_day(uid: str, summary: str, day: date, days: int = 1) -> str:
    end = day + timedelta(days=days)
    return (
        f"BEGIN:VEVENT\r\nUID:{uid}\r\nSUMMARY:{summary}\r\nDTSTART;VALUE=DATE:{day:%Y%m%d}\r\n"
        f"DTEND;VALUE=DATE:{end:%Y%m%d}\r\nEND:VEVENT\r\n"
    )


def next_weekday(today: date, weekday: int) -> date:
    """The next date (at least one week ahead) with the given weekday (0 = Monday)."""
    return today + timedelta(days=((weekday - today.weekday()) % 7) + 7)
