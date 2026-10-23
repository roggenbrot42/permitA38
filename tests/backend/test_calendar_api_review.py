# ruff: noqa: F811
"""Merge-gate review additions for the calendar API (fixtures come from test_calendar_api)."""

import time

import pytest
from fastapi.testclient import TestClient

from tests.backend.calendar_helpers import ics
from tests.backend.test_calendar_api import app, connect, fake  # noqa: F401  (fixtures)


def make_stale(app, source_id, **extra):
    store = app.state.services.store
    row = store.get("calendar_sources", source_id)
    store.save("calendar_sources", {**row, "last_fetched": 0, "refresh_minutes": 15, **extra})


def wait_for(predicate, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_busy_request_refreshes_only_stale_subscriptions(app, boss_client, fake):
    from datetime import date

    user_id = boss_client.get("/api/auth/me").json()["id"]
    source = connect(boss_client).json()
    today = date.today().isoformat()
    params = {"user_id": user_id, "start": today, "end": today}
    calls = len(fake.calls)
    assert boss_client.get("/api/calendars/busy", params=params).status_code == 200  # fresh: no download
    assert not wait_for(lambda: len(fake.calls) > calls, 0.3)
    make_stale(app, source["id"])
    boss_client.get("/api/calendars/busy", params=params)
    assert wait_for(lambda: len(fake.calls) > calls)
    # a source that does not count as busy and an uploaded file are never refreshed
    make_stale(app, source["id"], busy_default=False)
    boss_client.post("/api/calendars/upload", data={"name": "File"}, files={"file": ("a.ics", ics(), "text/calendar")})
    fake.calls.clear()
    boss_client.get("/api/calendars/busy", params=params)
    assert not wait_for(lambda: len(fake.calls) > 0, 0.4)


@pytest.mark.parametrize(
    "form",
    [
        {"name": "x", "refresh_minutes": "5"},
        {"name": "x", "title_policy": "bogus"},
        {"name": "x", "include_keywords": "k" * 150},
        {"name": "x", "refresh_minutes": "abc"},
    ],
)
def test_upload_with_invalid_options_is_422_not_500(boss_client, form):
    r = boss_client.post("/api/calendars/upload", data=form, files={"file": ("a.ics", ics(), "text/calendar")})
    assert r.status_code == 422, r.text


def test_unauthenticated_calendar_calls_are_401(app):
    with TestClient(app) as anon:
        for method, path in (
            ("get", "/api/calendars"),
            ("post", "/api/calendars"),
            ("get", "/api/calendars/suggestions"),
            ("get", "/api/calendars/busy?user_id=x&start=2026-01-01&end=2026-01-02"),
            ("post", "/api/calendars/abc/refresh"),
            ("delete", "/api/calendars/abc"),
        ):
            assert getattr(anon, method)(path).status_code == 401, path


def test_import_notes_are_returned_by_the_api(boss_client, fake):
    fake.body = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20991105T100000Z\r\nDURATION:PT1H\r\n"
        "RRULE:FREQ=HOURLY\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    body = connect(boss_client).json()
    assert body["last_warnings"] == ["unsupported_rrule"]
    assert boss_client.get("/api/calendars").json()[0]["last_warnings"] == ["unsupported_rrule"]
