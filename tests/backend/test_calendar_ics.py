"""ICS parser: golden occurrences (hand-computed), recurrence rules, DST, overrides and hostile input."""

from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from permita38.core.service.calendar import ics, recurrence
from permita38.core.service.calendar.ics import IcsError, parse_ics
from permita38.core.service.calendar.recurrence import iter_dates, parse_rule

FIXTURES = Path(__file__).parent / "fixtures" / "calendar"
BERLIN = ZoneInfo("Europe/Berlin")


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def run(text: str | bytes, start=utc(2026, 1, 1), end=utc(2027, 1, 1), **kw):
    data = text if isinstance(text, bytes) else text.encode("utf-8")
    return parse_ics(data, default_tz=BERLIN, window=(start, end), **kw)


def ics_doc(*events: str) -> str:
    body = "".join(f"BEGIN:VEVENT\r\n{e}\r\nEND:VEVENT\r\n" for e in events)
    return "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n" + body + "END:VCALENDAR\r\n"


def starts(result) -> list[str]:
    return [o.start for o in result.occurrences]


# ------------------------------------------------------------------ recurrence rules (golden)
def first_dates(rule: str, start: date, n: int) -> list[str]:
    out = []
    for d in iter_dates(parse_rule(rule), start, lambda: 0.0, 1e18):
        out.append(d.isoformat())
        if len(out) == n:
            break
    return out


def test_last_friday_of_month():
    # Fridays that are the last one in their month: 30 Jan, 27 Feb, 27 Mar, 24 Apr 2026
    assert first_dates("FREQ=MONTHLY;BYDAY=-1FR", date(2026, 1, 1), 4) == [
        "2026-01-30",
        "2026-02-27",
        "2026-03-27",
        "2026-04-24",
    ]


def test_first_monday_or_tuesday_with_bysetpos():
    # first of the Mon/Tue days of each month: Jan 5, Feb 2, Mar 2, Apr 6 2026
    assert first_dates("FREQ=MONTHLY;BYDAY=MO,TU;BYSETPOS=1", date(2026, 1, 1), 4) == [
        "2026-01-05",
        "2026-02-02",
        "2026-03-02",
        "2026-04-06",
    ]


def test_biweekly_with_wkst_and_byday():
    # DTSTART Fri 2 Jan 2026; weeks start on Monday; every second week Mon and Fri
    assert first_dates("FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,FR", date(2026, 1, 2), 5) == [
        "2026-01-02",
        "2026-01-12",
        "2026-01-16",
        "2026-01-26",
        "2026-01-30",
    ]


def test_yearly_last_sunday_of_march():
    assert first_dates("FREQ=YEARLY;BYMONTH=3;BYDAY=SU;BYSETPOS=-1", date(2026, 1, 1), 3) == [
        "2026-03-29",
        "2027-03-28",
        "2028-03-26",
    ]


def test_monthday_negative_means_from_month_end():
    # BYMONTHDAY=-1 is the last day of each month: 28 Feb 2026 (not a leap year), 31 Mar, 30 Apr
    assert first_dates("FREQ=MONTHLY;BYMONTHDAY=-1", date(2026, 1, 1), 3) == [
        "2026-01-31",
        "2026-02-28",
        "2026-03-31",
    ]


@pytest.mark.parametrize(
    "rule",
    [
        "FREQ=HOURLY",
        "FREQ=DAILY;BYHOUR=9",
        "FREQ=WEEKLY;BYWEEKNO=3",
        "FREQ=YEARLY;BYYEARDAY=1",
        "FREQ=DAILY;COUNT=0",
        "FREQ=DAILY;INTERVAL=0",
        "FREQ=MONTHLY;BYMONTHDAY=0",
        "FREQ=MONTHLY;BYDAY=0MO",
        "FREQ=MONTHLY;BYDAY=XX",
        "FREQ=DAILY;FREQ=DAILY",
        "garbage",
    ],
)
def test_unsupported_or_malformed_rules_are_rejected(rule):
    with pytest.raises(recurrence.RruleError):
        parse_rule(rule)


# ------------------------------------------------------------------------- full parser (golden)
def test_proton_like_export_golden(tmp_path):
    raw = (FIXTURES / "proton_export.ics").read_bytes()
    result = parse_ics(raw, default_tz=BERLIN, window=(utc(2026, 2, 1), utc(2026, 6, 1)))
    got = [(o.start, o.end, o.summary, o.recurrence_id, o.all_day, o.ooo) for o in result.occurrences]
    # Weekly Mon-Fri 09:00 Berlin until 13 Mar 00:00 UTC: 2-6 Mar and 9-12 Mar; 4 Mar moved to 10:00 (override).
    # Berlin is UTC+1 until the DST change on 29 Mar, so 09:00 local is 08:00 UTC.
    assert got == [
        ("2026-03-02T08:00:00Z", "2026-03-02T08:30:00Z", "Team standup", None, False, False),
        ("2026-03-03T08:00:00Z", "2026-03-03T08:30:00Z", "Team standup", None, False, False),
        ("2026-03-04T09:00:00Z", "2026-03-04T09:30:00Z", "Team standup (moved)", "2026-03-04T08:00:00Z", False, False),
        ("2026-03-05T08:00:00Z", "2026-03-05T08:30:00Z", "Team standup", None, False, False),
        ("2026-03-06T08:00:00Z", "2026-03-06T08:30:00Z", "Team standup", None, False, False),
        ("2026-03-09T08:00:00Z", "2026-03-09T08:30:00Z", "Team standup", None, False, False),
        ("2026-03-10T08:00:00Z", "2026-03-10T08:45:00Z", "Dentist, Dr. Example", None, False, False),
        ("2026-03-10T08:00:00Z", "2026-03-10T08:30:00Z", "Team standup", None, False, False),
        ("2026-03-11T08:00:00Z", "2026-03-11T08:30:00Z", "Team standup", None, False, False),
        ("2026-03-12T08:00:00Z", "2026-03-12T08:30:00Z", "Team standup", None, False, False),
        ("2026-04-20", "2026-04-25", "Vacation", None, True, True),
    ]
    # the CANCELLED lunch and the TRANSPARENT focus block are not busy time
    assert all("Cancelled" not in o.summary and "Focus" not in o.summary for o in result.occurrences)


def test_dst_shifts_the_utc_time_of_a_wall_clock_series():
    weekly = ics_doc(
        "UID:dst@test\r\nDTSTART;TZID=Europe/Berlin:20260323T090000\r\nDTEND;TZID=Europe/Berlin:20260323T100000\r\nRRULE:FREQ=WEEKLY\r\nSUMMARY:Weekly"
    )
    # 23 Mar is CET (UTC+1): 08:00Z; 30 Mar is CEST (UTC+2) after the 29 Mar switch: 07:00Z
    result = run(weekly, utc(2026, 3, 20), utc(2026, 4, 7))
    assert starts(result) == ["2026-03-23T08:00:00Z", "2026-03-30T07:00:00Z", "2026-04-06T07:00:00Z"]


def test_dst_end_in_october():
    weekly = ics_doc(
        "UID:dst2@test\r\nDTSTART;TZID=Europe/Berlin:20261019T090000\r\nDTEND;TZID=Europe/Berlin:20261019T100000\r\nRRULE:FREQ=WEEKLY\r\nSUMMARY:Weekly"
    )
    # 19 Oct is CEST (07:00Z); 26 Oct is CET after the 25 Oct switch (08:00Z)
    result = run(weekly, utc(2026, 10, 18), utc(2026, 10, 28))
    assert starts(result) == ["2026-10-19T07:00:00Z", "2026-10-26T08:00:00Z"]
    assert [o.end for o in result.occurrences] == ["2026-10-19T08:00:00Z", "2026-10-26T09:00:00Z"]


def test_exdate_removes_an_instance_but_count_still_counts_it():
    doc = ics_doc(
        "UID:ex@test\r\nDTSTART:20260305T100000Z\r\nDTEND:20260305T103000Z\r\nRRULE:FREQ=WEEKLY;COUNT=4\r\n"
        "EXDATE:20260312T100000Z\r\nSUMMARY:Review"
    )
    assert starts(run(doc)) == ["2026-03-05T10:00:00Z", "2026-03-19T10:00:00Z", "2026-03-26T10:00:00Z"]


def test_rdate_adds_instances_and_duplicates_are_ignored():
    doc = ics_doc(
        "UID:rd@test\r\nDTSTART:20260305T100000Z\r\nDTEND:20260305T110000Z\r\n"
        "RDATE:20260407T100000Z,20260408T100000Z,20260305T100000Z\r\nSUMMARY:Extra"
    )
    assert starts(run(doc)) == ["2026-03-05T10:00:00Z", "2026-04-07T10:00:00Z", "2026-04-08T10:00:00Z"]


def test_count_limits_occurrences():
    doc = ics_doc("UID:c@test\r\nDTSTART:20260105T100000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Three")
    assert starts(run(doc)) == ["2026-01-05T10:00:00Z", "2026-01-06T10:00:00Z", "2026-01-07T10:00:00Z"]


def test_until_in_utc_is_inclusive_of_the_instant():
    doc = ics_doc("UID:u@test\r\nDTSTART:20260105T100000Z\r\nRRULE:FREQ=DAILY;UNTIL=20260107T100000Z\r\nSUMMARY:U")
    assert starts(run(doc)) == ["2026-01-05T10:00:00Z", "2026-01-06T10:00:00Z", "2026-01-07T10:00:00Z"]


def test_until_in_utc_is_applied_as_an_instant_for_local_times():
    # 07 Jan 10:00 Berlin is 09:00Z, which is before UNTIL 09:30Z, so it stays; 08 Jan is excluded
    doc = ics_doc(
        "UID:u2@test\r\nDTSTART;TZID=Europe/Berlin:20260105T100000\r\n"
        "RRULE:FREQ=DAILY;UNTIL=20260107T093000Z\r\nSUMMARY:U2"
    )
    assert starts(run(doc)) == ["2026-01-05T09:00:00Z", "2026-01-06T09:00:00Z", "2026-01-07T09:00:00Z"]


def test_all_day_multi_day_recurring_has_exclusive_end():
    doc = ics_doc(
        "UID:ad@test\r\nDTSTART;VALUE=DATE:20260601\r\nDTEND;VALUE=DATE:20260603\r\nRRULE:FREQ=YEARLY\r\nSUMMARY:Trip"
    )
    result = run(doc, utc(2026, 5, 1), utc(2027, 7, 1))
    assert [(o.start, o.end, o.all_day) for o in result.occurrences] == [
        ("2026-06-01", "2026-06-03", True),
        ("2027-06-01", "2027-06-03", True),
    ]


def test_all_day_without_end_lasts_one_day_and_duration_is_respected():
    doc = ics_doc(
        "UID:ad2@test\r\nDTSTART;VALUE=DATE:20260110\r\nSUMMARY:Single",
        "UID:ad3@test\r\nDTSTART;VALUE=DATE:20260111\r\nDURATION:P2D\r\nSUMMARY:Two",
    )
    result = run(doc)
    assert sorted((o.start, o.end) for o in result.occurrences) == [
        ("2026-01-10", "2026-01-11"),
        ("2026-01-11", "2026-01-13"),
    ]


def test_recurrence_id_override_and_cancelled_instance():
    doc = ics_doc(
        "UID:o@test\r\nDTSTART:20260105T100000Z\r\nDTEND:20260105T110000Z\r\nRRULE:FREQ=DAILY;COUNT=3\r\nSUMMARY:Daily",
        "UID:o@test\r\nRECURRENCE-ID:20260106T100000Z\r\nDTSTART:20260106T140000Z\r\nDTEND:20260106T150000Z\r\nSUMMARY:Moved",
        "UID:o@test\r\nRECURRENCE-ID:20260107T100000Z\r\nDTSTART:20260107T100000Z\r\nSTATUS:CANCELLED\r\nSUMMARY:Gone",
    )
    got = [(o.start, o.recurrence_id) for o in run(doc).occurrences]
    assert got == [("2026-01-05T10:00:00Z", None), ("2026-01-06T14:00:00Z", "2026-01-06T10:00:00Z")]


def test_cancelled_master_and_free_markers_are_ignored():
    doc = ics_doc(
        "UID:x1@test\r\nDTSTART:20260105T100000Z\r\nSTATUS:CANCELLED\r\nSUMMARY:Off",
        "UID:x2@test\r\nDTSTART:20260105T120000Z\r\nDTEND:20260105T130000Z\r\nX-MICROSOFT-CDO-BUSYSTATUS:FREE\r\nSUMMARY:Free",
        "UID:x3@test\r\nDTSTART:20260105T140000Z\r\nDTEND:20260105T150000Z\r\nSUMMARY:Busy",
    )
    assert [o.summary for o in run(doc).occurrences] == ["Busy"]


def test_window_clips_and_keywords_filter():
    doc = ics_doc(
        "UID:k1@test\r\nDTSTART:20260105T100000Z\r\nDTEND:20260105T110000Z\r\nSUMMARY:Team sync\r\nCATEGORIES:Work",
        "UID:k2@test\r\nDTSTART:20260105T120000Z\r\nDTEND:20260105T130000Z\r\nSUMMARY:Dentist\r\nCATEGORIES:Health",
        "UID:k3@test\r\nDTSTART:20260201T120000Z\r\nDTEND:20260201T130000Z\r\nSUMMARY:Later",
    )
    assert [o.summary for o in run(doc, utc(2026, 1, 1), utc(2026, 1, 31)).occurrences] == ["Team sync", "Dentist"]
    assert [o.summary for o in run(doc, include_keywords=["sync"]).occurrences] == ["Team sync"]
    assert [o.summary for o in run(doc, exclude_keywords=["DENTIST"]).occurrences] == ["Team sync", "Later"]
    assert [o.summary for o in run(doc, include_keywords=["health"]).occurrences] == ["Dentist"]


def test_out_of_office_detection_on_summary_and_categories():
    doc = ics_doc(
        "UID:v1@test\r\nDTSTART;VALUE=DATE:20260420\r\nDTEND;VALUE=DATE:20260421\r\nSUMMARY:Urlaub",
        "UID:v2@test\r\nDTSTART;VALUE=DATE:20260422\r\nDTEND;VALUE=DATE:20260423\r\nSUMMARY:Sick\r\nCATEGORIES:OOO",
        "UID:v3@test\r\nDTSTART;VALUE=DATE:20260424\r\nDTEND;VALUE=DATE:20260425\r\nSUMMARY:Project planning",
    )
    assert [o.ooo for o in run(doc, utc(2026, 4, 1), utc(2026, 5, 1)).occurrences] == [True, True, False]


def test_missing_dtstart_and_bad_values_are_skipped_with_warning():
    doc = ics_doc(
        "UID:b1@test\r\nSUMMARY:No start",
        "UID:b2@test\r\nDTSTART:20269999T000000Z\r\nSUMMARY:Bad date",
        "UID:b3@test\r\nDTSTART:20260105T100000Z\r\nDTEND:20260105T110000Z\r\nSUMMARY:Fine",
    )
    result = run(doc)
    assert [o.summary for o in result.occurrences] == ["Fine"]
    assert "bad_event" in result.warnings


def test_unknown_timezone_uses_default_zone_and_warns():
    doc = ics_doc(
        "UID:tz@test\r\nDTSTART;TZID=Mars/Olympus:20260105T100000\r\nDTEND;TZID=Mars/Olympus:20260105T110000\r\nSUMMARY:M"
    )
    result = run(doc)
    assert starts(result) == ["2026-01-05T09:00:00Z"]
    assert "unknown_timezone" in result.warnings


def test_line_folding_and_text_escapes():
    doc = (
        "BEGIN:VCALENDAR\r\nBEGIN:VEVENT\r\nUID:f@test\r\nDTSTART:20260105T100000Z\r\nDTEND:20260105T110000Z\r\n"
        "SUMMARY:Planning\\, review\\; part\r\n two\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    )
    assert run(doc).occurrences[0].summary == "Planning, review; parttwo"


# ----------------------------------------------------------------------------------- hostile input
def test_input_size_is_bounded():
    with pytest.raises(IcsError) as err:
        run(b"x" * (ics.MAX_INPUT_BYTES + 1))
    assert err.value.code == "too_large"


def test_non_calendar_input_is_rejected():
    with pytest.raises(IcsError) as err:
        run("<html>not a calendar</html>")
    assert err.value.code == "invalid_ics"


def test_deep_nesting_is_rejected():
    doc = "BEGIN:VCALENDAR\r\n" + "BEGIN:VX\r\n" * 20
    with pytest.raises(IcsError) as err:
        run(doc)
    assert err.value.code == "too_deep"


def test_huge_count_is_time_boxed_not_exhausted():
    doc = ics_doc("UID:h@test\r\nDTSTART:20260105T100000Z\r\nRRULE:FREQ=DAILY;COUNT=999999999\r\nSUMMARY:Forever")
    with pytest.raises(IcsError) as err:
        run(doc, end=utc(2026, 1, 10))
    assert err.value.code == "too_complex"


def test_deadline_is_enforced_with_an_injected_clock():
    ticks = iter(range(10_000))
    doc = ics_doc("UID:d@test\r\nDTSTART:20260105T100000Z\r\nRRULE:FREQ=DAILY\r\nSUMMARY:Daily")
    with pytest.raises(IcsError) as err:
        run(doc, end=utc(2030, 1, 1), deadline_seconds=0.5, clock=lambda: float(next(ticks)))
    assert err.value.code == "too_complex"


def test_too_many_occurrences_are_rejected():
    doc = ics_doc(
        f"UID:m@test\r\nDTSTART:20260105T100000Z\r\nRRULE:FREQ=DAILY;COUNT={ics.MAX_OCCURRENCES + 1}\r\nSUMMARY:Many"
    )
    with pytest.raises(IcsError) as err:
        run(doc, end=utc(2090, 1, 1))
    assert err.value.code == "too_many_events"


@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(
    st.lists(
        st.sampled_from(
            [
                "BEGIN:VCALENDAR",
                "END:VCALENDAR",
                "BEGIN:VEVENT",
                "END:VEVENT",
                "BEGIN:VTIMEZONE",
                "END:VTIMEZONE",
                "BEGIN:STANDARD",
                "END:STANDARD",
                "UID:x",
                "DTSTART:20260305T100000Z",
                "DTSTART;VALUE=DATE:20261301",
                "DTSTART;TZID=Nowhere/Land:20260305T100000",
                "DTEND:20260305T090000Z",
                "DURATION:P-3DT4H",
                "RRULE:FREQ=DAILY;COUNT=99999999999",
                "RRULE:FREQ=WEEKLY;BYDAY=XX;;",
                "RRULE:FREQ=MONTHLY;BYDAY=-53MO",
                "RRULE:FREQ=YEARLY;BYMONTH=2;BYMONTHDAY=30",
                "EXDATE:garbage",
                "RDATE:20260101T000000Z,zz",
                "RECURRENCE-ID:20260101",
                " folded",
                "\t",
                "SUMMARY:\\",
                "\x00\xff",
                "",
                "TZOFFSETTO:+9999",
                "X:::::",
                ";;;",
                "DTSTART:" + "9" * 300,
            ]
        ),
        max_size=60,
    )
)
def test_random_garbage_never_escapes_as_an_unexpected_error(lines):
    text = "BEGIN:VCALENDAR\r\n" + "\r\n".join(lines) + "\r\nEND:VCALENDAR\r\n"
    try:
        result = run(text, deadline_seconds=2.0)
    except IcsError as err:
        assert err.code in {"too_large", "invalid_ics", "too_deep", "too_complex", "too_many_events"}
    else:
        assert len(result.occurrences) <= ics.MAX_OCCURRENCES
        for o in result.occurrences:
            assert len(o.summary) <= ics.MAX_TITLE_CHARS
