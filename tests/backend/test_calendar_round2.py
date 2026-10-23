# ruff: noqa: F811
"""Second review round: long busy ranges, unsorted BYMONTH, floating UNTIL, update() races, re-upload advice, EXDATE."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.calendar.busy_provider import CalendarBusyProvider
from permita38.core.service.calendar.ics import parse_ics
from permita38.core.service.calendar.recurrence import iter_dates, parse_rule
from permita38.core.service.calendar.service import CalendarService
from tests.backend.calendar_helpers import SECRET_URL, FakeFetcher, ics
from tests.backend.test_calendar_service import (  # noqa: F401  (fixtures)
    FIXED_NOW,
    USER,
    event,
    fetcher,
    repo,
    service,
    source,
)

ME = {"id": USER, "timezone": "Europe/Berlin"}


# ---------------------------------------------------------------------------- 1. long busy ranges
def test_busy_hours_answer_ranges_longer_than_one_chunk(repo):
    source(repo, "srclong001")
    first = date(2026, 11, 2)  # a Monday
    days = [first + timedelta(days=7 * k) for k in (0, 20, 45, 60, 90)]  # up to 630 days ahead: > 400-day chunk
    for d in days:
        event(repo, "srclong001", f"{d.isoformat()}T10:00:00Z", f"{d.isoformat()}T11:00:00Z")
    provider = CalendarBusyProvider(repo)
    whole = provider.busy_hours(USER, first, first + timedelta(weeks=100) - timedelta(days=1))
    # reference: many short queries
    reference: dict[date, float] = {}
    cursor = first
    while cursor < first + timedelta(weeks=100):
        reference.update(provider.busy_hours(USER, cursor, cursor + timedelta(days=27)))
        cursor += timedelta(days=28)
    assert whole == reference
    assert len(whole) == len(days) and sum(whole.values()) == len(days)  # every event counted (1 h each)


def test_beyond_the_stored_horizon_there_is_simply_no_busy_time(repo):
    source(repo, "srchor0001")
    event(repo, "srchor0001", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z")
    got = CalendarBusyProvider(repo).busy_hours(USER, date(2026, 11, 2), date(2026, 11, 2) + timedelta(weeks=156))
    assert got == {date(2026, 11, 2): 1.0}


# ---------------------------------------------------------------------- 2. unsorted BYMONTH
def dates(rule, n, start=date(2026, 1, 1)):
    out = []
    for d in iter_dates(parse_rule(rule), start, lambda: 0.0, 1e18):
        out.append(d.isoformat())
        if len(out) == n:
            break
    return out


def test_yearly_bymonth_order_does_not_matter():
    assert dates("FREQ=YEARLY;BYMONTH=6,3;BYMONTHDAY=10", 4) == ["2026-03-10", "2026-06-10", "2027-03-10", "2027-06-10"]


def test_unsorted_bymonth_with_count_until_and_setpos():
    raw = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20260101T100000Z\r\nDURATION:PT1H\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=6,3;BYMONTHDAY=10;COUNT=3\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:b\r\nDTSTART:20260101T100000Z\r\nDURATION:PT1H\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=6,3;BYMONTHDAY=10;UNTIL=20270401T000000Z\r\nEND:VEVENT\r\n"
        "BEGIN:VEVENT\r\nUID:c\r\nDTSTART:20260101T100000Z\r\nDURATION:PT1H\r\n"
        "RRULE:FREQ=YEARLY;BYMONTH=6,3;BYMONTHDAY=10;BYSETPOS=-1;COUNT=2\r\nEND:VEVENT\r\n"
        "END:VCALENDAR\r\n"
    ).encode()
    got = parse_ics(raw, default_tz=UTC, window=(datetime(2026, 1, 1, tzinfo=UTC), datetime(2030, 1, 1, tzinfo=UTC)))
    by_uid: dict[str, list[str]] = {}
    for o in got.occurrences:
        by_uid.setdefault(o.uid, []).append(o.start[:10])
    # COUNT=3 counts DTSTART too (2026-01-01 does not match the rule but is kept as the first instance)
    assert by_uid["a"] == ["2026-01-01", "2026-03-10", "2026-06-10"]
    assert by_uid["b"] == ["2026-01-01", "2026-03-10", "2026-06-10", "2027-03-10"]  # nothing after the UNTIL
    assert by_uid["c"] == ["2026-01-01", "2026-06-10"]  # COUNT=2 incl. DTSTART; the last of {March, June} is June


# ----------------------------------------------------------------------- 3. floating UNTIL
def test_floating_until_is_read_in_the_zone_of_dtstart():
    raw = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:ny\r\n"
        "DTSTART;TZID=America/New_York:20240101T090000\r\nDURATION:PT1H\r\n"
        "RRULE:FREQ=DAILY;UNTIL=20240103T090000\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    berlin = ZoneInfo("Europe/Berlin")
    got = parse_ics(raw, default_tz=berlin, window=(datetime(2024, 1, 1, tzinfo=UTC), datetime(2024, 2, 1, tzinfo=UTC)))
    # 09:00 New York = 14:00Z. In the Berlin zone the UNTIL would be 08:00Z on 3 Jan and drop the last day.
    assert [o.start for o in got.occurrences] == [
        "2024-01-01T14:00:00Z",
        "2024-01-02T14:00:00Z",
        "2024-01-03T14:00:00Z",
    ]


# ---------------------------------------------------------------------------- 4. update() races
def test_update_does_not_overwrite_fields_written_by_a_concurrent_import(repo, service, monkeypatch):
    view = service.add_upload(ME, {"name": "f"}, ics())
    sid = view["id"]
    stale = repo.get("calendar_sources", sid)
    fresh = repo.get("calendar_sources", sid)
    fresh.update(event_count=99, imported_at=12345)
    repo.save("calendar_sources", fresh)
    monkeypatch.setattr(service, "_own", lambda user, source_id: dict(stale))  # what a racing request would have read
    service.update(ME, sid, {"name": "renamed"})
    row = repo.get("calendar_sources", sid)
    assert row["name"] == "renamed" and row["event_count"] == 99 and row["imported_at"] == 12345


def test_filter_change_during_a_running_refresh_is_refused_and_not_saved(repo, service, fetcher):
    sid = service.add_url(ME, {"name": "w"}, SECRET_URL)["id"]
    assert service._begin(sid)  # a refresh is "running"
    with pytest.raises(ServiceError) as err:
        service.update(ME, sid, {"exclude_keywords": ["planning"], "name": "changed"})
    assert err.value.status_code == 409 if hasattr(err.value, "status_code") else True
    row = repo.get("calendar_sources", sid)
    assert row["exclude_keywords"] == [] and row["name"] == "w"  # nothing was saved before the refusal
    service._end(sid)


# ----------------------------------------------------------------------- 5. re-upload advice
def test_uploaded_files_advise_a_new_upload_after_half_a_year(repo):
    now = FIXED_NOW
    service = CalendarService(repo, FakeFetcher(ics()), clock=lambda: now)
    sid = service.add_upload(ME, {"name": "f"}, ics())["id"]
    assert service.get_source(ME, sid)["reupload_advised"] is False
    later = CalendarService(repo, FakeFetcher(ics()), clock=lambda: now + 181 * 24 * 3600)
    assert later.get_source(ME, sid)["reupload_advised"] is True
    url_id = service.add_url(ME, {"name": "u"}, SECRET_URL)["id"]
    assert later.get_source(ME, url_id)["reupload_advised"] is False  # subscriptions re-read themselves


# ------------------------------------------------------------------------------- 7. EXDATE
def test_exdate_with_a_date_value_removes_that_day_of_a_timed_series():
    raw = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:x\r\n"
        "DTSTART;TZID=Europe/Berlin:20261102T100000\r\nDURATION:PT1H\r\nRRULE:FREQ=DAILY;COUNT=3\r\n"
        "EXDATE;VALUE=DATE:20261103\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    got = parse_ics(
        raw,
        default_tz=ZoneInfo("Europe/Berlin"),
        window=(datetime(2026, 11, 1, tzinfo=UTC), datetime(2026, 12, 1, tzinfo=UTC)),
    )
    assert [o.start[:10] for o in got.occurrences] == ["2026-11-02", "2026-11-04"]
