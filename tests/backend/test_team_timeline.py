"""Team timeline: visibility per level, redaction on the server, the window bounds and the setting that sets the level.
Every expected value is written out by hand from the fixture (docs/contracts/team-timeline.md)."""

from datetime import date, timedelta

import pytest

from permita38.core.service import team_timeline as tt

from .conftest import PASSWORD, _login

TIMELINE = "/api/team/timeline?start=2026-11-02&end=2026-12-27"
SECRETS_OF_OTHERS = (  # notes of entries that are not the viewer's (Dana's own notes are not in this list)
    "Dentist", "Autumn break", "Flu", "Christmas shift", "Early start", "Company day", "Weekend trip", "Gift shop",
    "Website relaunch", "Example GmbH", "Support", "Inactive secret", "Long ago",
)  # fmt: skip


@pytest.fixture
def world(app, boss_client):
    """Three active people (Alex, Bea, Dana), one inactive (Cem) and contracts, absences inside and outside the window.

    Window 2026-11-02 (Mon) .. 2026-12-27 (Sun). 2026-12-25 is a Friday and a nationwide public holiday.
    """
    boss = boss_client
    ids = {"boss": boss.get("/api/auth/me").json()["id"]}

    def person(key, username, name, role, **extra):
        r = boss.post(
            "/api/users", json={"username": username, "name": name, "role": role, "password": PASSWORD, **extra}
        )
        assert r.status_code == 200, r.text
        ids[key] = r.json()["id"]
        return _login(app, username)

    alex = person("alex", "alexx", "Alex Example", "employee", color="#cc6633", weekly_hours=32)
    bea = person("bea", "beax", "Bea Example", "accountant", weekly_hours=38.5)
    dana = person("dana", "danx", "Dana Example", "employee", color="#339966")
    r = boss.post(
        "/api/users",
        json={"username": "cemx", "name": "Cem Example", "role": "employee", "password": PASSWORD, "active": False},
    )  # inactive: cannot sign in
    assert r.status_code == 200, r.text
    ids["cem"] = r.json()["id"]
    ids["customer"] = boss.post("/api/customers", json={"name": "Example GmbH"}).json()["id"]

    def absence(user, kind, start, end, note):
        r = boss.post(
            "/api/absences", json={"user_id": ids[user], "kind": kind, "start": start, "end": end, "note": note}
        )
        assert r.status_code == 200, r.text

    absence("bea", "vacation", "2026-10-30", "2026-11-02", "Early start")  # overlaps the window start: whole entry
    absence("alex", "other", "2026-11-03", "2026-11-03", "Dentist")
    absence("alex", "vacation", "2026-11-09", "2026-11-13", "Autumn break")
    absence("bea", "holiday", "2026-11-09", "2026-11-10", "Company day")
    absence("dana", "vacation", "2026-11-10", "2026-11-11", "Family visit")
    absence("cem", "vacation", "2026-11-09", "2026-11-09", "Inactive secret")  # inactive: never listed
    absence("alex", "sick", "2026-11-18", "2026-11-18", "Flu")
    absence("dana", "sick", "2026-11-18", "2026-11-18", "Migraine")
    absence("bea", "vacation", "2026-11-14", "2026-11-15", "Weekend trip")
    absence("dana", "vacation", "2026-11-14", "2026-11-15", "Hiking")
    absence("boss", "vacation", "2026-11-16", None, "Private")  # one day, no end given
    absence("dana", "vacation", "2026-10-01", "2026-10-02", "Long ago")  # outside the window
    absence("alex", "other", "2026-12-25", "2026-12-25", "Christmas shift")
    absence("bea", "vacation", "2026-12-25", "2026-12-25", "Gift shop")
    absence("dana", "vacation", "2026-12-25", "2026-12-25", "Home")

    def contract(title, customer, start, end, hours, users):
        r = boss.post(
            "/api/contracts",
            json={"title": title, "customer_id": customer, "start": start, "end": end, "hours_per_week": hours,
                  "user_ids": users},
        )  # fmt: skip
        assert r.status_code == 200, r.text
        return r.json()["id"]

    ids["c_web"] = contract(
        "Website relaunch", ids["customer"], "2026-10-01", "2026-12-31", 20, [ids["boss"], ids["alex"]]
    )
    ids["c_support"] = contract("Support", None, "2026-12-01", "2026-12-31", None, [ids["alex"]])
    ids["c_dana"] = contract("Dana project", None, "2026-11-01", "2026-11-30", 10, [ids["dana"]])
    contract("Last year", None, "2025-01-01", "2025-06-30", 5, [ids["dana"]])  # no overlap with the window
    return {"ids": ids, "alex": alex, "bea": bea, "dana": dana, "boss": boss}


def _set_visibility(world, **levels):
    r = world["boss"].put("/api/settings", json={"team_timeline_visibility": levels})
    assert r.status_code == 200, r.text


def _by_name(body):
    return {p["name"]: p for p in body["people"]}


def _web(ids):
    return {"id": ids["c_web"], "title": "Website relaunch", "customer": "Example GmbH", "start": "2026-10-01",
            "end": "2026-12-31", "hours_per_week": 20}  # fmt: skip


def _support(ids):
    return {"id": ids["c_support"], "title": "Support", "customer": None, "start": "2026-12-01", "end": "2026-12-31",
            "hours_per_week": None}  # fmt: skip


def _away(*spans):
    return [{"start": s, "end": e, "kind": None, "label": None} for s, e in spans]


def _clash_days(body, n):
    """Independent oracle: a day clashes when it is a Mon-Fri, not a public holiday and more than n people are away."""
    holidays = set(body["holidays"])
    day, last, out = date.fromisoformat(body["start"]), date.fromisoformat(body["end"]), []
    while day <= last:
        away = sum(
            1
            for p in body["people"]
            for a in p["absences"]
            if date.fromisoformat(a["start"]) <= day <= date.fromisoformat(a["end"])
        )
        if day.weekday() < 5 and day.isoformat() not in holidays and away > n:
            out.append(day.isoformat())
        day += timedelta(days=1)
    return out


# ------------------------------------------------------------------ the full level (boss): exact JSON
def test_boss_gets_everything_exactly(world):
    ids = world["ids"]
    body = world["boss"].get(TIMELINE).json()
    assert body == {
        "level": "full",
        "start": "2026-11-02",
        "end": "2026-12-27",
        "holidays": {"2026-12-25": "1. Weihnachtstag", "2026-12-26": "2. Weihnachtstag"},
        "people": [
            {
                "id": ids["boss"], "name": "Test Boss", "color": "#2563eb", "weekly_hours": 40,  # setup default
                "absences": [{"start": "2026-11-16", "end": "2026-11-16", "kind": "vacation", "label": "Private"}],
                "contracts": [_web(ids)],
            },
            {
                "id": ids["alex"], "name": "Alex Example", "color": "#cc6633", "weekly_hours": 32,
                "absences": [
                    {"start": "2026-11-03", "end": "2026-11-03", "kind": "other", "label": "Dentist"},
                    {"start": "2026-11-09", "end": "2026-11-13", "kind": "vacation", "label": "Autumn break"},
                    {"start": "2026-11-18", "end": "2026-11-18", "kind": "sick", "label": "Flu"},
                    {"start": "2026-12-25", "end": "2026-12-25", "kind": "other", "label": "Christmas shift"},
                ],
                "contracts": [_web(ids), _support(ids)],
            },
            {
                "id": ids["bea"], "name": "Bea Example", "color": None, "weekly_hours": 38.5,
                "absences": [
                    {"start": "2026-10-30", "end": "2026-11-02", "kind": "vacation", "label": "Early start"},
                    {"start": "2026-11-09", "end": "2026-11-10", "kind": "holiday", "label": "Company day"},
                    {"start": "2026-11-14", "end": "2026-11-15", "kind": "vacation", "label": "Weekend trip"},
                    {"start": "2026-12-25", "end": "2026-12-25", "kind": "vacation", "label": "Gift shop"},
                ],
                "contracts": [],
            },
            {
                "id": ids["dana"], "name": "Dana Example", "color": "#339966", "weekly_hours": 40,
                "absences": [
                    {"start": "2026-11-10", "end": "2026-11-11", "kind": "vacation", "label": "Family visit"},
                    {"start": "2026-11-14", "end": "2026-11-15", "kind": "vacation", "label": "Hiking"},
                    {"start": "2026-11-18", "end": "2026-11-18", "kind": "sick", "label": "Migraine"},
                    {"start": "2026-12-25", "end": "2026-12-25", "kind": "vacation", "label": "Home"},
                ],
                "contracts": [{"id": ids["c_dana"], "title": "Dana project", "customer": None, "start": "2026-11-01",
                               "end": "2026-11-30", "hours_per_week": 10}],  # fmt: skip
            },
        ],
    }  # fmt: skip


def test_the_viewer_is_first_then_by_name_and_inactive_people_are_missing(world):
    assert [p["name"] for p in world["boss"].get(TIMELINE).json()["people"]] == [
        "Test Boss", "Alex Example", "Bea Example", "Dana Example",
    ]  # fmt: skip
    assert [p["name"] for p in world["alex"].get(TIMELINE).json()["people"]] == [
        "Alex Example", "Bea Example", "Dana Example", "Test Boss",
    ]  # fmt: skip


def test_open_ended_contract_is_listed_with_null_end(world, app):
    store = app.state.services.store
    store.save("contracts", {"title": "Retainer", "start": "2026-11-05", "end": None, "user_ids": [world["ids"]["bea"]],
                             "hours_per_week": 5})  # fmt: skip
    body = world["boss"].get(TIMELINE).json()
    bea = _by_name(body)["Bea Example"]
    assert bea["contracts"] == [{"id": bea["contracts"][0]["id"], "title": "Retainer", "customer": None,
                                 "start": "2026-11-05", "end": None, "hours_per_week": 5}]  # fmt: skip


# ------------------------------------------------------------------ away (the default for employees)
def test_employee_default_away_shows_own_entries_in_full_and_nothing_else(world):
    ids = world["ids"]
    body = world["alex"].get(TIMELINE).json()
    assert body["level"] == "away"
    by = _by_name(body)
    assert by["Alex Example"] == {
        "id": ids["alex"], "name": "Alex Example", "color": "#cc6633", "weekly_hours": 32,
        "absences": [
            {"start": "2026-11-03", "end": "2026-11-03", "kind": "other", "label": "Dentist"},
            {"start": "2026-11-09", "end": "2026-11-13", "kind": "vacation", "label": "Autumn break"},
            {"start": "2026-11-18", "end": "2026-11-18", "kind": "sick", "label": "Flu"},
            {"start": "2026-12-25", "end": "2026-12-25", "kind": "other", "label": "Christmas shift"},
        ],
        "contracts": [],
    }  # fmt: skip
    assert by["Bea Example"]["absences"] == _away(
        ("2026-10-30", "2026-11-02"), ("2026-11-09", "2026-11-10"), ("2026-11-14", "2026-11-15"), ("2026-12-25", "2026-12-25")
    )  # fmt: skip
    assert by["Bea Example"]["weekly_hours"] is None and by["Bea Example"]["contracts"] == []
    assert by["Dana Example"]["absences"] == _away(
        ("2026-11-10", "2026-11-11"), ("2026-11-14", "2026-11-15"), ("2026-11-18", "2026-11-18"), ("2026-12-25", "2026-12-25")
    )  # fmt: skip
    assert by["Dana Example"]["color"] == "#339966"  # the colour is shown at every level
    assert by["Test Boss"]["absences"] == _away(("2026-11-16", "2026-11-16"))
    assert all(p["contracts"] == [] for p in body["people"])


def test_no_hidden_note_appears_anywhere_in_the_raw_response_at_away(world):
    raw = world["dana"].get(TIMELINE).text
    for secret in SECRETS_OF_OTHERS:
        assert secret not in raw, secret
    # the other people's kinds are removed, not masked: every kind of a non-viewer row is null
    body = world["dana"].get(TIMELINE).json()
    for p in body["people"]:
        if p["name"] != "Dana Example":
            assert all(a["kind"] is None and a["label"] is None for a in p["absences"]), p["name"]


# ------------------------------------------------------------------ time-off
def test_time_off_adds_kind_and_vacation_or_holiday_labels_but_not_sick_labels(world):
    _set_visibility(world, employee="time-off")
    body = world["dana"].get(TIMELINE).json()  # Dana is an employee: the level is now time-off
    assert body["level"] == "time-off"
    by = _by_name(body)
    assert by["Test Boss"]["absences"] == [
        {"start": "2026-11-16", "end": "2026-11-16", "kind": "vacation", "label": "Private"}
    ]
    assert by["Alex Example"]["absences"] == [  # other: kind, no label; sick: plain away
        {"start": "2026-11-03", "end": "2026-11-03", "kind": "other", "label": None},
        {"start": "2026-11-09", "end": "2026-11-13", "kind": "vacation", "label": "Autumn break"},
        {"start": "2026-11-18", "end": "2026-11-18", "kind": None, "label": None},
        {"start": "2026-12-25", "end": "2026-12-25", "kind": "other", "label": None},
    ]  # fmt: skip
    assert by["Bea Example"]["absences"][1] == {"start": "2026-11-09", "end": "2026-11-10", "kind": "holiday",
                                                 "label": "Company day"}  # fmt: skip
    assert by["Dana Example"]["absences"][2] == {
        "start": "2026-11-18",
        "end": "2026-11-18",
        "kind": "sick",
        "label": "Migraine",
    }  # own row: full
    assert by["Dana Example"]["weekly_hours"] == 40 and by["Alex Example"]["weekly_hours"] is None
    assert all(p["contracts"] == [] for p in body["people"])


# ------------------------------------------------------------------ full for employees
def test_full_for_an_employee_shows_the_same_data_as_the_boss(world):
    _set_visibility(world, employee="full")
    boss = world["boss"].get(TIMELINE).json()
    dana = world["dana"].get(TIMELINE).json()
    assert dana["level"] == "full" and dana["people"][0]["name"] == "Dana Example"
    assert sorted(dana["people"], key=lambda p: p["id"]) == sorted(boss["people"], key=lambda p: p["id"])
    assert dana["holidays"] == boss["holidays"]


# ------------------------------------------------------------------ the accountant: off by default
def test_off_is_403_and_changing_the_setting_takes_effect_on_the_next_request(world):
    bea = world["bea"]
    r = bea.get(TIMELINE)
    assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}
    _set_visibility(world, accountant="away")
    body = bea.get(TIMELINE).json()
    assert body["level"] == "away"
    by = _by_name(body)
    assert by["Bea Example"]["absences"][0] == {
        "start": "2026-10-30",
        "end": "2026-11-02",
        "kind": "vacation",
        "label": "Early start",
    }  # own row stays in full
    assert by["Alex Example"]["absences"][0] == {
        "start": "2026-11-03",
        "end": "2026-11-03",
        "kind": None,
        "label": None,
    }  # the others are away
    _set_visibility(world, accountant="off")
    assert bea.get(TIMELINE).status_code == 403


def test_the_boss_is_always_full_whatever_the_setting_says(world):
    _set_visibility(world, employee="away", accountant="off")
    assert world["boss"].get(TIMELINE).json()["level"] == "full"


def test_anonymous_gets_401(client):
    assert client.get(TIMELINE).status_code == 401


# ------------------------------------------------------------------ the setting
def test_settings_show_the_map_to_the_boss_only(world):
    assert world["boss"].get("/api/settings").json()["team_timeline_visibility"] == {
        "employee": "away",
        "accountant": "off",
    }
    assert "team_timeline_visibility" not in world["bea"].get("/api/settings").json()
    assert world["dana"].get("/api/settings").status_code == 403


def test_the_setting_is_strict(world):
    boss = world["boss"]
    for bad in ({"boss": "full"}, {"employee": "owner"}, {"employee": None}, {"accountant": 3}, {"owner": "full"}):
        r = boss.put("/api/settings", json={"team_timeline_visibility": bad})
        assert r.status_code == 422, bad
        assert isinstance(r.json()["detail"], list)
        assert r.json()["detail"][0]["loc"][:2] == ["body", "team_timeline_visibility"]
    assert boss.get("/api/settings").json()["team_timeline_visibility"] == {"employee": "away", "accountant": "off"}


def test_a_partial_map_keeps_the_other_role_and_other_settings_keep_the_map(world):
    boss = world["boss"]
    _set_visibility(world, employee="full")  # accountant untouched
    assert boss.get("/api/settings").json()["team_timeline_visibility"] == {"employee": "full", "accountant": "off"}
    assert boss.put("/api/settings", json={"company_name": "Demo Studio Two"}).status_code == 200
    assert boss.get("/api/settings").json()["team_timeline_visibility"] == {"employee": "full", "accountant": "off"}
    assert boss.put("/api/settings", json={"team_timeline_visibility": {}}).status_code == 200
    assert boss.get("/api/settings").json()["team_timeline_visibility"] == {"employee": "full", "accountant": "off"}
    put = boss.put("/api/settings", json={"team_timeline_visibility": {"accountant": "time-off"}})
    assert put.json()["team_timeline_visibility"] == {"employee": "full", "accountant": "time-off"}


def test_accountant_cannot_change_the_setting(world):
    assert world["bea"].put("/api/settings", json={"team_timeline_visibility": {"employee": "full"}}).status_code == 403


def test_the_holidays_follow_the_business_state(world):
    world["boss"].put("/api/settings", json={"state": "NW"})
    body = world["boss"].get("/api/team/timeline?start=2026-06-01&end=2026-06-10").json()
    assert body["holidays"] == {"2026-06-04": "Fronleichnam"}  # Corpus Christi 2026 (Easter 5 April + 60 days)


# ------------------------------------------------------------------ the window
@pytest.mark.parametrize(
    "query",
    [
        "start=2026-11-02",  # end missing
        "start=2026-02-30&end=2026-03-01",  # not a real date
        "start=1999-12-31&end=2000-01-01",  # before 2000
        "start=2026-11-02&end=2101-01-01",  # after 2100
        "start=2026-11-02&end=2026-11-03&foo=1",  # unknown parameter
    ],
)
def test_malformed_query_is_a_list_form_422(world, query):
    r = world["boss"].get(f"/api/team/timeline?{query}")
    assert r.status_code == 422
    assert isinstance(r.json()["detail"], list) and r.json()["detail"][0]["loc"][0] == "query"
    assert "2030" not in r.text  # never echoes the input


def test_end_before_start_is_a_plain_422(world):
    r = world["boss"].get("/api/team/timeline?start=2026-11-03&end=2026-11-02")
    assert r.status_code == 422 and r.json() == {"detail": "end must not be before start"}


def test_the_window_is_at_most_366_days_inclusive(world):
    boss = world["boss"]
    assert boss.get("/api/team/timeline?start=2026-01-01&end=2027-01-01").status_code == 200  # 366 days
    r = boss.get("/api/team/timeline?start=2026-01-01&end=2027-01-02")  # 367 days
    assert r.status_code == 422 and r.json() == {"detail": "the window may span at most 365 days"}


# ------------------------------------------------------------------ clash days (client-side rule, oracle here)
@pytest.mark.parametrize(
    ("n", "expected"),
    [
        (2, ["2026-11-10"]),  # strict: three people is a clash at 2, two people is not
        (1, ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-18"]),  # sick days count too
        (3, []),  # three people is not more than three
    ],
)
def test_clash_days_are_strictly_more_than_n_on_weekdays_that_are_no_holiday(world, n, expected):
    boss_view = world["boss"].get(TIMELINE).json()
    dana_view = world["dana"].get(TIMELINE).json()  # away: the same counts, sick days included
    assert _clash_days(boss_view, n) == expected
    assert _clash_days(dana_view, n) == expected
    # 2026-11-14/15 (weekend, two people) and 2026-12-25 (holiday, three people) never count


def test_visibility_and_level_helpers_defaults_and_legacy_roles():
    assert tt.visibility({}) == {"employee": "away", "accountant": "off"}
    assert tt.visibility({tt.SETTING: {"employee": "full", "accountant": "bogus"}}) == {
        "employee": "full", "accountant": "off"}  # fmt: skip
    assert tt.level_for("boss", {}) == "full"
    assert tt.level_for("member", {}) == "away"  # legacy role: employee
    assert tt.level_for("accountant", {tt.SETTING: {"accountant": "time-off"}}) == "time-off"
    assert tt.level_for("wizard", {}) == "off" and tt.level_for(None, {}) == "off"
