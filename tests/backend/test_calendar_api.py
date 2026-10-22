"""Calendar API: connecting, refreshing, privacy of the subscription link, ownership, and busy hours."""

import logging
from datetime import date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.netsafe import FetchError
from tests.backend.calendar_helpers import (
    SECRET_TOKEN,
    SECRET_URL,
    FakeFetcher,
    all_day,
    ics,
    next_weekday,
    timed,
)
from tests.backend.conftest import PASSWORD, _login


def soon(days: int = 30, hour: int = 10, length: int = 1) -> tuple[datetime, datetime]:
    """Wall-clock start and end a few weeks ahead: inside the stored window whatever day the tests run."""
    start = datetime.combine(date.today() + timedelta(days=days), datetime.min.time()).replace(hour=hour)
    return start, start + timedelta(hours=length)


@pytest.fixture
def fake():
    return FakeFetcher(ics(timed("m1@example.test", "Planning", *soon())))


@pytest.fixture
def app(config, fake):
    return get_app(config, calendar_fetcher=fake)


@pytest.fixture
def boss2_client(app, boss_client):
    r = boss_client.post(
        "/api/users", json={"username": "ina", "name": "Ina Second", "role": "boss", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    c = _login(app, "ina")
    yield c
    c.close()


def connect(client, url=SECRET_URL, **extra):
    body = {"name": "Work calendar", "url": url, **extra}
    return client.post("/api/calendars", json=body)


def test_connect_by_link_returns_only_a_masked_url(boss_client, fake):
    r = connect(boss_client)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["url_masked"] == "https://calendar.example.test/…/****"
    assert body["event_count"] == 1 and body["last_status"] == "ok"
    assert SECRET_TOKEN not in r.text
    listing = boss_client.get("/api/calendars")
    assert SECRET_TOKEN not in listing.text
    assert fake.calls[0]["url"] == SECRET_URL and fake.calls[0]["https_only"] is True


def test_webcal_is_rewritten_to_https(boss_client, fake):
    r = connect(boss_client, url=SECRET_URL.replace("https://", "webcal://"))
    assert r.status_code == 200, r.text
    assert fake.calls[0]["url"] == SECRET_URL


@pytest.mark.parametrize("url", ["http://calendar.example.test/x.ics", "ftp://calendar.example.test/x.ics"])
def test_only_https_links_are_accepted(boss_client, fake, url):
    r = connect(boss_client, url=url)
    assert r.status_code == 422
    assert fake.calls == []


def test_secret_never_appears_in_logs_or_errors(boss_client, fake, caplog):
    caplog.set_level(logging.DEBUG)
    fake.error = FetchError("network_error", "The calendar server could not be reached.")
    r = connect(boss_client)
    assert r.status_code == 502
    assert SECRET_TOKEN not in r.text
    assert SECRET_TOKEN not in caplog.text


def test_blocked_addresses_are_refused_by_the_real_fetcher(tmp_path):
    client = TestClient(get_app(Config(data_dir=tmp_path / "data", secret_key="k" * 32, web_dir=None)))
    client.post(
        "/api/auth/setup", json={"username": "boss", "name": "Boss", "password": PASSWORD, "company_name": "Demo"}
    )
    boss = _login(client.app, "boss")
    for url in (
        "https://127.0.0.1/ics/x.ics",
        "https://localhost/ics/x.ics",
        "https://192.168.1.10/ics/x.ics",
        "https://intranet.example.test:8443/x.ics",
    ):
        r = connect(boss, url=url)
        assert r.status_code in (422, 502), (url, r.text)
        assert "192.168" not in r.text and "8443" not in r.text
    assert boss.get("/api/calendars").json() == []


def test_upload_of_a_file_stores_events_and_no_titles_by_default(boss_client):
    data = ics(timed("u1@example.test", "Private dentist", *soon(40, 9)))
    r = boss_client.post(
        "/api/calendars/upload",
        data={"name": "Exported file", "title_policy": "none"},
        files={"file": ("export.ics", data, "text/calendar")},
    )
    assert r.status_code == 200, r.text
    source = r.json()
    assert source["kind"] == "ics_file" and source["url_masked"] is None and source["event_count"] == 1
    preview = boss_client.get(f"/api/calendars/{source['id']}/preview?days=60").json()
    assert [item["title"] for item in preview] == [None]  # the title is never stored under the default policy
    assert "Private dentist" not in boss_client.get("/api/calendars").text
    assert "Private dentist" not in boss_client.get(f"/api/calendars/{source['id']}/preview?days=60").text


def test_upload_rejects_non_calendar_and_oversized_files(boss_client):
    r = boss_client.post(
        "/api/calendars/upload", data={"name": "Bad"}, files={"file": ("x.ics", b"<html></html>", "text/html")}
    )
    assert r.status_code == 422 and "iCalendar" in r.text
    big = b"BEGIN:VCALENDAR\r\n" + b"X" * (5 * 1024 * 1024)
    r = boss_client.post(
        "/api/calendars/upload", data={"name": "Big"}, files={"file": ("big.ics", big, "text/calendar")}
    )
    assert r.status_code == 413


def test_failed_refresh_keeps_the_old_events(boss_client, fake):
    source = connect(boss_client).json()
    fake.error = FetchError("timeout", "The calendar server did not answer in time.")
    r = boss_client.post(f"/api/calendars/{source['id']}/refresh")
    assert r.status_code == 200
    body = r.json()
    assert body["last_status"] == "error" and body["event_count"] == 1
    assert SECRET_TOKEN not in r.text


def test_unchanged_calendar_is_not_downloaded_again(boss_client, fake):
    source = connect(boss_client).json()
    r = boss_client.post(f"/api/calendars/{source['id']}/refresh")
    assert r.json()["last_status"] == "not_modified"
    assert fake.calls[-1]["conditional"] == {"etag": '"v1"', "last_modified": None}


def test_refresh_replaces_events_atomically(boss_client, fake):
    source = connect(boss_client).json()
    fake.body = ics(
        timed("a@example.test", "One", *soon(31, 9)),
        timed("b@example.test", "Two", *soon(32, 9)),
    )
    fake.etag = '"v2"'
    r = boss_client.post(f"/api/calendars/{source['id']}/refresh")
    assert r.json()["event_count"] == 2 and r.json()["last_status"] == "ok"


def test_delete_removes_the_calendar_and_its_events(app, boss_client):
    source = connect(boss_client).json()
    assert boss_client.delete(f"/api/calendars/{source['id']}").status_code == 204
    assert boss_client.get("/api/calendars").json() == []
    assert app.state.services.store.find("calendar_events", source_id=source["id"]) == []


def test_title_policy_changes_apply_to_stored_titles(app, boss_client, fake):
    fake.body = ics(timed("t@example.test", "Board review", *soon()))
    source = connect(boss_client, title_policy="full").json()
    store = app.state.services.store
    assert [e.get("title") for e in store.find("calendar_events", source_id=source["id"])] == ["Board review"]
    r = boss_client.put(f"/api/calendars/{source['id']}", json={"title_policy": "none"})
    assert r.status_code == 200
    assert [e.get("title") for e in store.find("calendar_events", source_id=source["id"])] == [None]


def test_changing_keywords_resyncs_a_subscription(boss_client, fake):
    source = connect(boss_client).json()
    calls = len(fake.calls)
    boss_client.put(f"/api/calendars/{source['id']}", json={"exclude_keywords": ["planning"]})
    assert len(fake.calls) == calls + 1


def test_changing_keywords_on_a_file_asks_for_a_new_upload(boss_client):
    r = boss_client.post(
        "/api/calendars/upload",
        data={"name": "File"},
        files={"file": ("f.ics", ics(timed("f@example.test", "X", *soon())), "text/calendar")},
    )
    source = r.json()
    updated = boss_client.put(f"/api/calendars/{source['id']}", json={"include_keywords": ["team"]}).json()
    assert updated["last_status"] == "needs_upload"


def test_members_have_no_access(member_client):
    assert member_client.get("/api/calendars").status_code == 403
    assert connect(member_client).status_code == 403
    assert (
        member_client.get(
            "/api/calendars/busy", params={"user_id": member_client.user_id, "start": "2030-01-01", "end": "2030-01-02"}
        ).status_code
        == 403
    )


def test_other_bosses_cannot_see_or_change_a_calendar(boss_client, boss2_client):
    source = connect(boss_client).json()
    sid = source["id"]
    assert boss2_client.get("/api/calendars").json() == []
    assert boss2_client.get(f"/api/calendars/{sid}/preview").status_code == 404
    assert boss2_client.put(f"/api/calendars/{sid}", json={"name": "Hijacked"}).status_code == 404
    assert boss2_client.post(f"/api/calendars/{sid}/refresh").status_code == 404
    assert boss2_client.delete(f"/api/calendars/{sid}").status_code == 404
    assert boss_client.get(f"/api/calendars/{sid}/preview").status_code == 200


def test_busy_endpoint_only_answers_for_yourself(boss_client, boss2_client):
    other_id = boss2_client.get("/api/auth/me").json()["id"]
    user_id = boss_client.get("/api/auth/me").json()["id"]
    r = boss_client.get("/api/calendars/busy", params={"user_id": other_id, "start": "2030-01-01", "end": "2030-01-02"})
    assert r.status_code == 403
    bad = boss_client.get(
        "/api/calendars/busy", params={"user_id": user_id, "start": "2030-02-01", "end": "2030-01-01"}
    )
    assert bad.status_code == 422


def test_busy_hours_follow_working_time_and_caps(boss_client, fake):
    today = date.today()
    monday = next_weekday(today, 0)
    wednesday = monday + timedelta(days=2)
    tuesday = monday + timedelta(days=1)
    # Working time in the default setup: 09:00-17:00 Europe/Berlin, Mon-Fri, 40 h a week => 8 h per day
    fake.body = ics(
        timed(
            "m1@example.test",
            "Standup",
            datetime.combine(monday, datetime.min.time()).replace(hour=10),
            datetime.combine(monday, datetime.min.time()).replace(hour=11),
        ),
        timed(
            "m2@example.test",
            "Overlapping review",
            datetime.combine(monday, datetime.min.time()).replace(hour=10, minute=30),
            datetime.combine(monday, datetime.min.time()).replace(hour=12),
        ),
        timed(
            "m3@example.test",
            "Early call",
            datetime.combine(monday, datetime.min.time()).replace(hour=7),
            datetime.combine(monday, datetime.min.time()).replace(hour=8),
        ),
        timed(
            "m4@example.test",
            "Late call",
            datetime.combine(monday, datetime.min.time()).replace(hour=16, minute=30),
            datetime.combine(monday, datetime.min.time()).replace(hour=18, minute=30),
        ),
        timed(
            "w1@example.test",
            "Workshop",
            datetime.combine(wednesday, datetime.min.time()).replace(hour=10),
            datetime.combine(wednesday, datetime.min.time()).replace(hour=18),
        ),
        all_day("h1@example.test", "Company day", tuesday),
    )
    assert connect(boss_client).status_code == 200
    user_id = boss_client.get("/api/auth/me").json()["id"]
    r = boss_client.get(
        "/api/calendars/busy",
        params={"user_id": user_id, "start": monday.isoformat(), "end": (monday + timedelta(days=6)).isoformat()},
    )
    assert r.status_code == 200, r.text
    # Monday: 10-12 merged (2 h) + 16:30-17:00 (0.5 h); the early call is outside working time.
    # Wednesday: 10:00-18:00 clipped to the working day end (17:00) = 7 h.
    # Tuesday: the all-day entry does not block the day because the source has all_day_blocks off.
    assert r.json() == {monday.isoformat(): 2.5, wednesday.isoformat(): 7.0}


def test_daily_target_caps_the_busy_hours(boss_client, fake):
    monday = next_weekday(date.today(), 0)
    fake.body = ics(timed("c1@example.test", "Long workshop", *soon_on(monday, 9, 17)))
    assert connect(boss_client).status_code == 200
    user = boss_client.get("/api/auth/me").json()
    # 20 h a week over five working days = 4 h a day: the 8 h of meetings count as 4 h
    r = boss_client.put(
        f"/api/users/{user['id']}",
        json={"username": user["username"], "name": user["name"], "role": "boss", "weekly_hours": 20},
    )
    assert r.status_code == 200, r.text
    got = boss_client.get(
        "/api/calendars/busy", params={"user_id": user["id"], "start": monday.isoformat(), "end": monday.isoformat()}
    ).json()
    assert got == {monday.isoformat(): 4.0}


def soon_on(day: date, start_hour: int, end_hour: int) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time())
    return start.replace(hour=start_hour), start.replace(hour=end_hour)


def test_upload_defaults_count_toward_capacity_when_flags_are_absent(boss_client):
    data = ics(timed("df@example.test", "Default flags", *soon()))
    r = boss_client.post("/api/calendars/upload", data={"name": "Defaults"}, files={"file": ("d.ics", data, "text/calendar")})
    assert r.status_code == 200, r.text
    assert r.json()["busy_default"] is True and r.json()["all_day_blocks"] is False
