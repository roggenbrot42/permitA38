# ruff: noqa: F811
"""Merge-gate review additions: isolation, failure behaviour, busy-hour edge cases, parser edges, fetch hardening."""

import gzip
import threading
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from permita38.core.errors import ServiceError
from permita38.core.netsafe import FetchError, SafeFetcher
from permita38.core.service.calendar.ics import parse_ics
from permita38.core.service.calendar.service import MAX_SOURCES, CalendarService, stored_window
from tests.backend.calendar_helpers import SECRET_URL, FakeFetcher, ics, timed
from tests.backend.test_calendar_service import (  # noqa: F401  (fixtures are imported on purpose)
    FIXED_NOW,
    USER,
    busy,
    event,
    fetcher,
    repo,
    service,
    source,
)

OTHER = "useraaaa02"
BERLIN = ZoneInfo("Europe/Berlin")
ME = {"id": USER, "timezone": "Europe/Berlin"}


def stored_events(repo, sid):
    return sorted((e["start_utc"], e["end_utc"]) for e in repo.find("calendar_events", source_id=sid))


@pytest.fixture
def other_user(repo):
    repo.save(
        "users",
        {
            "id": OTHER,
            "username": "bob",
            "name": "Bob",
            "role": "boss",
            "weekly_hours": 40,
            "workdays": [0, 1, 2, 3, 4],
            "timezone": "Europe/Berlin",
        },
    )
    return {"id": OTHER, "timezone": "Europe/Berlin"}


# ------------------------------------------------------------------------------------ isolation
def test_busy_and_suggestions_ignore_other_users_sources(repo, service, other_user):
    source(repo, "srcmine001")
    source(repo, "srcoth0001", user_id=OTHER)
    event(repo, "srcoth0001", "2026-11-02T10:00:00Z", "2026-11-02T11:00:00Z")
    event(repo, "srcoth0001", "2027-12-02", "2027-12-03", all_day=True, ooo=True)
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 2)) == {}
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 2), user_id=OTHER) == {date(2026, 11, 2): 1.0}
    assert service.suggestions(ME) == []
    assert len(service.suggestions(other_user)) == 1


def test_quota_is_per_user(repo, service, other_user):
    for i in range(MAX_SOURCES):
        source(repo, f"srcq{i:06d}", user_id=OTHER)
    with pytest.raises(ServiceError):
        service.add_upload(other_user, {"name": "x"}, ics())
    assert service.add_upload(ME, {"name": "mine"}, ics())["name"] == "mine"


# ------------------------------------------------------------------------------ failures keep data
def make_url_source(service, repo):
    view = service.add_url(ME, {"name": "w"}, SECRET_URL)
    return view["id"], stored_events(repo, view["id"])


@pytest.mark.parametrize(
    "fail",
    [
        lambda f: setattr(f, "error", FetchError("timeout", "slow")),
        lambda f: setattr(f, "body", b"<html>not a calendar</html>"),
        lambda f: setattr(f, "error", FetchError("too_large", "big")),
    ],
)
def test_failed_refresh_keeps_events_and_marks_the_attempt(repo, service, fetcher, fail):
    sid, before = make_url_source(service, repo)
    assert before
    fetcher.etag = '"v2"'  # a changed file: the body is downloaded and parsed
    fail(fetcher)
    service.sync(ME, sid, force=True)
    row = repo.get("calendar_sources", sid)
    assert row["last_status"] == "error" and row["last_error"]
    assert row["last_fetched"] == int(FIXED_NOW)  # no retry storm: the failed attempt counts
    assert stored_events(repo, sid) == before
    assert SECRET_URL not in str(row["last_error"])


def test_background_failure_keeps_events(repo, fetcher):
    service = CalendarService(repo, fetcher, clock=lambda: FIXED_NOW)
    sid, before = make_url_source(service, repo)
    repo.save("calendar_sources", {**repo.get("calendar_sources", sid), "last_fetched": 0, "refresh_minutes": 15})
    fetcher.error = FetchError("network_error", "down")
    for t in service.schedule_stale_refresh(USER):
        t.join(5)
    assert repo.get("calendar_sources", sid)["last_status"] == "error"
    assert stored_events(repo, sid) == before


def test_not_modified_keeps_etag_and_stale_boundary(repo, service):
    sid, _ = make_url_source(service, repo)
    service.sync(ME, sid)
    row = repo.get("calendar_sources", sid)
    assert row["last_status"] == "not_modified" and row["etag"] == '"v1"'
    row.update(last_fetched=int(FIXED_NOW) - 15 * 60 + 1, refresh_minutes=15)
    assert not service.is_stale(row)
    row["last_fetched"] = int(FIXED_NOW) - 15 * 60
    assert service.is_stale(row)


def test_filter_change_forces_a_full_download(repo, service, fetcher):
    sid, _ = make_url_source(service, repo)
    service.update(ME, sid, {"exclude_keywords": ["planning"]})
    assert fetcher.calls[-1]["conditional"] == {}  # no If-None-Match: a 304 would keep the unfiltered events
    assert stored_events(repo, sid) == []


def test_refresh_in_flight_does_not_resurrect_a_deleted_calendar(repo):
    gate, started = threading.Event(), threading.Event()

    class Slow(FakeFetcher):
        def fetch_resource(self, url, **kw):
            started.set()
            gate.wait(5)
            return super().fetch_resource(url, **kw)

    slow = Slow(ics(timed("m@example.test", "x", datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11))))
    service = CalendarService(repo, slow, clock=lambda: FIXED_NOW)
    source(repo, "srcgone001", kind="ics_url", url=SECRET_URL, last_fetched=0, refresh_minutes=15)
    threads = service.schedule_stale_refresh(USER)
    assert started.wait(5)
    service.delete(ME, "srcgone001")
    gate.set()
    threads[0].join(5)
    assert repo.get("calendar_sources", "srcgone001") is None
    assert repo.find("calendar_events", source_id="srcgone001") == []


def test_import_warnings_are_stored_and_returned(service):
    raw = (
        "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nBEGIN:VEVENT\r\nUID:a\r\nDTSTART:20261105T100000Z\r\nDURATION:PT1H\r\n"
        "RRULE:FREQ=HOURLY;COUNT=3\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    ).encode()
    view = service.add_upload(ME, {"name": "w"}, raw)
    assert view["last_warnings"] == ["unsupported_rrule"]
    assert service.get_source(ME, view["id"])["last_warnings"] == ["unsupported_rrule"]


def test_categories_are_stored_only_with_the_full_policy(service, repo):
    raw = ics(
        timed("c@example.test", "Secret", datetime(2026, 11, 3, 10), datetime(2026, 11, 3, 11), "CATEGORIES:Health\r\n")
    )
    for policy, expected in (("none", []), ("redacted", []), ("full", ["Health"])):
        view = service.add_upload(ME, {"name": policy, "title_policy": policy}, raw)
        assert [e["categories"] for e in repo.find("calendar_events", source_id=view["id"])] == [expected]


# ------------------------------------------------------------------------------------- busy hours
def test_timed_event_spanning_days_and_crossing_midnight(repo):
    source(repo, "srcspan001")
    repo.save("users", {"id": USER, "weekly_hours": 80})  # 16 h target: the cap must not hide the sums
    # Mon 2 Nov 16:00 local .. Wed 4 Nov 10:00 local (CET): Mon 1 h, Tue 8 h, Wed 1 h
    event(repo, "srcspan001", "2026-11-02T15:00:00Z", "2026-11-04T09:00:00Z")
    assert busy(repo, date(2026, 11, 2), date(2026, 11, 4)) == {
        date(2026, 11, 2): 1.0,
        date(2026, 11, 3): 8.0,
        date(2026, 11, 4): 1.0,
    }
    # crossing midnight: Mon 9 Nov 22:00 local .. Tue 10:00 local counts only 09:00-10:00 on Tuesday
    event(repo, "srcspan001", "2026-11-09T21:00:00Z", "2026-11-10T09:00:00Z")
    assert busy(repo, date(2026, 11, 9), date(2026, 11, 10)) == {date(2026, 11, 10): 1.0}


def test_all_day_multi_day_and_weekend(repo):
    source(repo, "srcad000001", all_day_blocks=True)
    repo.save("users", {"id": USER, "weekly_hours": 80})
    # Fri 6 Nov .. Mon 9 Nov (end exclusive Tue 10): Friday and Monday are workdays, the weekend is not
    event(repo, "srcad000001", "2026-11-06", "2026-11-10", all_day=True)
    assert busy(repo, date(2026, 11, 6), date(2026, 11, 10)) == {date(2026, 11, 6): 8.0, date(2026, 11, 9): 8.0}
    event(repo, "srcad000001", "2026-11-14", "2026-11-16", all_day=True)  # Saturday and Sunday only
    assert busy(repo, date(2026, 11, 14), date(2026, 11, 15)) == {}


def test_stored_window_is_whole_local_days():
    lo, hi = stored_window(BERLIN, datetime(2026, 10, 21, 21, 30, tzinfo=UTC))  # 23:30 CEST: still 21 Oct locally
    assert lo == datetime(2026, 9, 21, tzinfo=BERLIN).astimezone(UTC)
    assert hi == datetime(2028, 4, 21, tzinfo=BERLIN).astimezone(UTC)
    next_day = stored_window(BERLIN, datetime(2026, 10, 21, 22, 30, tzinfo=UTC))  # 00:30 CEST on 22 Oct
    assert next_day[0] == datetime(2026, 9, 22, tzinfo=BERLIN).astimezone(UTC)


# ------------------------------------------------------------------------------------ parser edges
def cal(*events):
    body = "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in events)
    return ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n" + body + "END:VCALENDAR\r\n").encode()


def run(raw, lo=datetime(2026, 11, 1, tzinfo=UTC), hi=datetime(2026, 12, 1, tzinfo=UTC), tz=UTC, **kw):
    return parse_ics(raw, default_tz=tz, window=(lo, hi), **kw)


def test_window_edges_are_half_open():
    lo, hi = datetime(2026, 11, 10, 10, tzinfo=UTC), datetime(2026, 11, 10, 12, tzinfo=UTC)
    ends_at_start = "UID:a\r\nDTSTART:20261110T090000Z\r\nDTEND:20261110T100000Z"
    starts_at_end = "UID:b\r\nDTSTART:20261110T120000Z\r\nDTEND:20261110T130000Z"
    inside = "UID:c\r\nDTSTART:20261110T095959Z\r\nDTEND:20261110T100001Z"
    assert [o.uid for o in run(cal(ends_at_start, starts_at_end, inside), lo, hi).occurrences] == ["c"]


def test_exdate_on_first_instance_and_against_rdate():
    raw = cal(
        "UID:x\r\nDTSTART:20261102T100000Z\r\nDURATION:PT1H\r\nRRULE:FREQ=DAILY;COUNT=3\r\nEXDATE:20261102T100000Z",
        "UID:y\r\nDTSTART:20261102T100000Z\r\nDURATION:PT1H\r\nRDATE:20261110T100000Z,20261111T100000Z\r\n"
        "EXDATE:20261110T100000Z",
    )
    got = sorted((o.uid, o.start[:10]) for o in run(raw).occurrences)
    assert got == [("x", "2026-11-03"), ("x", "2026-11-04"), ("y", "2026-11-02"), ("y", "2026-11-11")]


def test_transparent_override_frees_one_instance_of_an_opaque_series():
    raw = cal(
        "UID:s\r\nDTSTART:20261102T100000Z\r\nDURATION:PT1H\r\nRRULE:FREQ=DAILY;COUNT=3",
        "UID:s\r\nRECURRENCE-ID:20261103T100000Z\r\nDTSTART:20261103T100000Z\r\nDURATION:PT1H\r\nTRANSP:TRANSPARENT",
    )
    assert [o.start[:10] for o in run(raw).occurrences] == ["2026-11-02", "2026-11-04"]


def test_keyword_filter_applies_to_overrides():
    raw = cal(
        "UID:s\r\nSUMMARY:Team sync\r\nDTSTART:20261102T100000Z\r\nDURATION:PT1H\r\nRRULE:FREQ=DAILY;COUNT=2",
        "UID:s\r\nSUMMARY:Dentist\r\nRECURRENCE-ID:20261103T100000Z\r\nDTSTART:20261103T150000Z\r\nDURATION:PT1H",
    )
    got = run(raw, exclude_keywords=["dentist"]).occurrences
    assert [o.start for o in got] == ["2026-11-02T10:00:00Z"]  # the replaced original stays suppressed


def test_override_moved_out_of_window_still_suppresses_the_original():
    raw = cal(
        "UID:s\r\nDTSTART:20261102T100000Z\r\nDURATION:PT1H\r\nRRULE:FREQ=DAILY;COUNT=2",
        "UID:s\r\nRECURRENCE-ID:20261103T100000Z\r\nDTSTART:20270301T100000Z\r\nDURATION:PT1H",
    )
    assert [o.start[:10] for o in run(raw).occurrences] == ["2026-11-02"]


def test_floating_times_use_the_default_zone():
    raw = cal("UID:f\r\nDTSTART:20261110T090000\r\nDTEND:20261110T100000")
    assert run(raw, tz=BERLIN).occurrences[0].start == "2026-11-10T08:00:00Z"
    assert run(raw, tz=ZoneInfo("America/New_York")).occurrences[0].start == "2026-11-10T14:00:00Z"


def test_weekly_0230_across_the_march_gap_and_october_fold():
    gap = cal("UID:g\r\nDTSTART;TZID=Europe/Berlin:20270321T023000\r\nDURATION:PT30M\r\nRRULE:FREQ=WEEKLY;COUNT=3")
    got = run(gap, datetime(2027, 3, 1, tzinfo=UTC), datetime(2027, 5, 1, tzinfo=UTC)).occurrences
    # 21 Mar CET 01:30Z; 28 Mar 02:30 does not exist (read with the pre-gap offset, 01:30Z); 4 Apr CEST 00:30Z
    assert [o.start for o in got] == ["2027-03-21T01:30:00Z", "2027-03-28T01:30:00Z", "2027-04-04T00:30:00Z"]
    fold = cal("UID:f\r\nDTSTART;TZID=Europe/Berlin:20261025T023000\r\nDURATION:PT30M")
    got = run(fold, datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)).occurrences
    assert got[0].start == "2026-10-25T00:30:00Z"  # the first of the two 02:30 (CEST)


# ------------------------------------------------------------------------------------ fetch hardening
class Scripted(SafeFetcher):
    """SafeFetcher whose network layer is scripted: one (status, headers, body) per hop. No sockets."""

    def __init__(self, hops, resolver=None):
        super().__init__("test-agent", resolver=resolver or (lambda host, port: ["93.184.216.34"]))
        self.hops = list(hops)
        self.requested = 0

    def _request(self, parsed, ip, started, **kw):
        self.requested += 1
        return self.hops.pop(0)


def get(fetcher, url="https://cal.example.org/a.ics"):
    return fetcher.fetch_resource(
        url, accept="text/calendar", allowed_types=("text/calendar",), max_bytes=1000, https_only=True
    )


def test_redirect_downgrade_to_http_is_refused():
    f = Scripted([(302, {"location": "http://cal.example.org/a.ics"}, b"")])
    with pytest.raises(FetchError) as err:
        get(f)
    assert err.value.code == "insecure_url" and f.requested == 1


def test_redirect_chain_longer_than_the_limit_is_refused():
    hops = [(302, {"location": f"https://cal.example.org/{i}.ics"}, b"") for i in range(6)]
    with pytest.raises(FetchError) as err:
        get(Scripted(hops))
    assert err.value.code == "too_many_redirects"


def test_resolver_answering_public_and_private_is_refused():
    f = Scripted([], resolver=lambda host, port: ["93.184.216.34", "10.0.0.5"])
    with pytest.raises(FetchError) as err:
        get(f)
    assert err.value.code == "blocked_address" and f.requested == 0


def test_gzip_bomb_is_cut_off_by_the_size_limit():
    from tests.backend.test_impressum_netsafe import _Site, make_fetcher

    s = _Site()
    try:
        s.routes["/b.ics"] = (
            200,
            {"Content-Type": "text/calendar", "Content-Encoding": "gzip"},
            gzip.compress(b"A" * 5_000_000),
        )
        with pytest.raises(FetchError) as err:
            make_fetcher(s).fetch_resource(
                s.url("/b.ics"), accept="text/calendar", allowed_types=("text/calendar",), max_bytes=64 * 1024
            )
        assert err.value.code == "too_large"
    finally:
        s.server.shutdown()
        s.server.server_close()
