"""Calendar service and busy-hours rules, against the real SQLite repository (fake network, invented data)."""

import threading
from datetime import UTC, date, datetime

import pytest

from permita38.core.errors import ServiceError
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.busy import BusyTimeProvider, NullBusyProvider
from permita38.core.service.calendar.busy_provider import CalendarBusyProvider
from permita38.core.service.calendar.service import MAX_SOURCES, CalendarService, mask_url, uid_hash
from tests.backend.calendar_helpers import SECRET_TOKEN, SECRET_URL, FakeFetcher, ics, timed

FIXED_NOW = datetime(2026, 10, 21, 12, tzinfo=UTC).timestamp()
USER = "useraaaa01"


@pytest.fixture
def repo(tmp_path):
    r = SQLiteRepository(tmp_path / "data")
    r.startup()
    r.save("users", {"id": USER, "username": "ann", "name": "Ann", "role": "boss", "weekly_hours": 40,
                     "workdays": [0, 1, 2, 3, 4], "timezone": "Europe/Berlin"})  # fmt: skip
    yield r
    r.close()


@pytest.fixture
def fetcher():
    return FakeFetcher(ics(timed("m1@example.test", "Planning", datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11))))


@pytest.fixture
def service(repo, fetcher):
    return CalendarService(repo, fetcher, clock=lambda: FIXED_NOW)


def source(repo, sid, **extra):
    row = {"id": sid, "user_id": USER, "name": sid, "kind": "ics_file", "url": None, "refresh_minutes": 360,
           "title_policy": "none", "all_day_blocks": False, "busy_default": True, "include_keywords": [],
           "exclude_keywords": [], "last_status": "ok", "event_count": 0}  # fmt: skip
    repo.save("calendar_sources", {**row, **extra})
    return sid


def event(repo, sid, start, end, all_day=False, ooo=False, title=None):
    repo.save("calendar_events", {"source_id": sid, "start_utc": start, "end_utc": end, "all_day": all_day,
                                   "busy": True, "ooo": ooo, "title": title, "categories": []})  # fmt: skip


def busy(repo, start, end, user_id=USER):
    return CalendarBusyProvider(repo).busy_hours(user_id, start, end)


# ------------------------------------------------------------------------------ busy hours (exact values)
def test_null_provider_and_protocol_shape(repo):
    provider: BusyTimeProvider = CalendarBusyProvider(repo)
    assert NullBusyProvider().busy_hours(USER, date(2026, 1, 1), date(2026, 1, 2)) == {}
    assert provider.busy_hours(USER, date(2026, 1, 1), date(2026, 1, 2)) == {}  # no calendars: same as null


def test_only_the_working_time_counts_and_overlaps_merge(repo):
    source(repo, "srca00001")
    source(repo, "srcb00001")
    # Monday 2 Nov 2026 (CET, UTC+1): working time 09:00-17:00 = 08:00Z-16:00Z
    event(repo, "srca00001", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z")
    event(repo, "srcb00001", "2026-11-02T10:30:00Z", "2026-11-02T12:00:00Z")  # overlaps: 10:00-12:00 = 2 h
    event(repo, "srca00001", "2026-11-02T06:00:00Z", "2026-11-02T07:30:00Z")  # before work: 0 h
    event(repo, "srca00001", "2026-11-02T15:30:00Z", "2026-11-02T18:00:00Z")  # 16:30-17:00 counts: 0.5 h
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 2)) == {date(2026, 11, 2): 2.5}


def test_dst_switch_day_uses_the_local_offset(repo):
    source(repo, "srcdst001")
    # Monday 30 Mar 2026 is already CEST (UTC+2): 09:00 local = 07:00Z, so a 09:30-10:30 local meeting is 07:30Z-08:30Z
    event(repo, "srcdst001", "2026-03-30T07:30:00Z", "2026-03-30T08:30:00Z")
    assert busy(repo, date(2026, 3, 30), date(2026, 3, 30)) == {date(2026, 3, 30): 1.0}


def test_all_day_events_block_only_with_the_source_flag(repo):
    source(repo, "srcday001")
    source(repo, "srcday002", all_day_blocks=True)
    event(repo, "srcday001", "2026-11-03", "2026-11-04", all_day=True)
    assert busy(repo, date(2026, 11, 3), date(2026, 11, 3)) == {}
    event(repo, "srcday002", "2026-11-03", "2026-11-04", all_day=True)
    assert busy(repo, date(2026, 11, 3), date(2026, 11, 3)) == {date(2026, 11, 3): 8.0}  # the whole working day


def test_daily_target_caps_the_busy_time(repo):
    source(repo, "srccap001")
    repo.save("users", {"id": USER, "weekly_hours": 20})  # 4 h per day over five working days
    event(repo, "srccap001", "2026-11-04T08:00:00Z", "2026-11-04T16:00:00Z")  # the whole working day
    assert busy(repo, date(2026, 11, 4), date(2026, 11, 4)) == {date(2026, 11, 4): 4.0}


def test_weekends_and_ignored_sources_do_not_count(repo):
    source(repo, "srcweek01")
    source(repo, "srcoff001", busy_default=False)
    event(repo, "srcweek01", "2026-11-07T09:00:00Z", "2026-11-07T10:00:00Z")  # Saturday
    event(repo, "srcoff001", "2026-11-02T09:00:00Z", "2026-11-02T10:00:00Z")  # Monday, but info only
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 8)) == {}


def test_custom_working_hours_and_time_zone(repo):
    source(repo, "srchrs001")
    repo.save("users", {"id": USER, "work_start": "08:00", "work_end": "12:00", "timezone": "Europe/Berlin"})
    event(repo, "srchrs001", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z")  # 11:00-12:00 local: 1 h
    event(repo, "srchrs001", "2026-11-02T15:00:00Z", "2026-11-02T16:00:00Z")  # after 12:00 local: 0 h
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 2)) == {date(2026, 11, 2): 1.0}
    repo.save("users", {"id": USER, "work_start": "09:00", "work_end": "17:00", "timezone": "America/New_York"})
    source(repo, "srcnyc001")
    event(repo, "srcnyc001", "2026-11-02T14:00:00Z", "2026-11-02T15:00:00Z")  # 09:00-10:00 EST: 1 h
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 2))[date(2026, 11, 2)] == 2.0


def test_bad_ranges_and_unknown_users_are_empty(repo):
    source(repo, "srcbad001")
    event(repo, "srcbad001", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z")
    assert busy(repo, date(2026, 11, 3), date(2026, 11, 2)) == {}
    assert busy(repo, date(2026, 1, 1), date(2028, 1, 1)) == {}
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 2), user_id="nosuchuser") == {}


# -------------------------------------------------------------------------- service rules
def test_connect_stores_hashed_uids_and_no_titles_or_categories_by_default(service, repo, fetcher):
    view = service.add_url(repo.get("users", USER), {"name": "Work"}, SECRET_URL)
    assert view["url_masked"] == mask_url(SECRET_URL) and SECRET_TOKEN not in str(view)
    (ev,) = repo.find("calendar_events", source_id=view["id"])
    assert ev["uid"] == uid_hash("m1@example.test") and len(ev["uid"]) == 24
    assert ev.get("title") is None and ev.get("categories") in (None, [])
    assert "m1@example.test" not in repr(repo.list("calendar_events"))
    assert fetcher.calls[0]["https_only"] is True


def test_full_title_policy_stores_titles_and_categories(service, repo):
    text = ics(
        timed("c1@example.test", "Board", datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11), "CATEGORIES:Work\r\n")
    )
    view = service.add_upload(repo.get("users", USER), {"name": "Full", "title_policy": "full"}, text)
    (ev,) = repo.find("calendar_events", source_id=view["id"])
    assert ev["title"] == "Board" and ev["categories"] == ["Work"]


def test_suggestions_hide_titles_unless_the_policy_allows(service, repo):
    text = ics(
        timed("v1@example.test", "Urlaub Anna", datetime(2026, 11, 9, 9), datetime(2026, 11, 9, 10)),
        timed("p1@example.test", "Planning", datetime(2026, 11, 9, 11), datetime(2026, 11, 9, 12)),
    )
    ann = repo.get("users", USER)
    service.add_upload(ann, {"name": "Default"}, text)
    (sug,) = service.suggestions(ann)
    assert sug["ooo"] is True and sug["title"] is None and sug["source_name"] == "Default"
    service.add_upload(ann, {"name": "Shown", "title_policy": "redacted"}, text)
    assert {s["title"] for s in service.suggestions(ann) if s["source_name"] == "Shown"} == {"Busy"}


def test_preview_covers_the_next_days_only(service, repo):
    text = ics(
        timed("n1@example.test", "Soon", datetime(2026, 10, 25, 10), datetime(2026, 10, 25, 11)),
        timed("n2@example.test", "Later", datetime(2026, 12, 25, 10), datetime(2026, 12, 25, 11)),
    )
    view = service.add_upload(repo.get("users", USER), {"name": "P"}, text)
    got = service.preview(repo.get("users", USER), view["id"], days=14)
    assert len(got) == 1 and got[0]["start"] == "2026-10-25T09:00:00Z"


def test_file_sources_cannot_be_synced_and_busy_sync_is_refused(service, repo):
    view = service.add_upload(repo.get("users", USER), {"name": "F"}, ics())
    with pytest.raises(ServiceError) as err:
        service.sync(repo.get("users", USER), view["id"])
    assert err.value.status == 409
    url_source = source(repo, "srcurl001", kind="ics_url", url=SECRET_URL)
    assert service._begin(url_source) is True
    with pytest.raises(ServiceError) as busy_err:
        service.sync(repo.get("users", USER), url_source)
    assert busy_err.value.status == 409
    service._end(url_source)


def test_quota_per_user(service, repo):
    for i in range(MAX_SOURCES):
        source(repo, f"srcq{i:04d}")
    with pytest.raises(ServiceError) as err:
        service.add_upload(repo.get("users", USER), {"name": "Too many"}, ics())
    assert err.value.status == 409


def test_stale_refresh_runs_in_the_background_once(repo):
    gate = threading.Event()
    calls = []

    class Blocking(FakeFetcher):
        def fetch_resource(self, url, **kw):
            calls.append(url)
            gate.wait(5)
            return super().fetch_resource(url, **kw)

    fetcher = Blocking(ics())
    service = CalendarService(repo, fetcher, clock=lambda: FIXED_NOW)
    source(repo, "srcstale1", kind="ics_url", url=SECRET_URL, last_fetched=0, refresh_minutes=15)
    threads = service.schedule_stale_refresh(USER)
    assert len(threads) == 1
    assert service.schedule_stale_refresh(USER) == []  # single-flight: the same source is not started twice
    gate.set()
    threads[0].join(5)
    assert len(calls) == 1
    assert repo.get("calendar_sources", "srcstale1")["last_status"] == "ok"
    assert service.schedule_stale_refresh(USER) == []  # fresh now


def test_fresh_subscriptions_are_not_refreshed(repo):
    fetcher = FakeFetcher(ics())
    service = CalendarService(repo, fetcher, clock=lambda: FIXED_NOW)
    source(repo, "srcfresh1", kind="ics_url", url=SECRET_URL, last_fetched=int(FIXED_NOW) - 60, refresh_minutes=60)
    assert service.schedule_stale_refresh(USER) == []
    assert fetcher.calls == []


def test_mask_url_hides_the_secret_path():
    assert mask_url(SECRET_URL) == "https://calendar.example.test/…/****"
    assert SECRET_TOKEN not in mask_url(SECRET_URL)
    assert mask_url(None) is None
