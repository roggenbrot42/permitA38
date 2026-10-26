"""Randomized invariants of the capacity engine: random people, absences, contracts, offers and what-if changes.

For every generated world and every week: capacity >= 0; free after confirmed = capacity - confirmed; worst case
free = capacity - confirmed - binding offers; the status matches the worst-case utilisation; a what-if run never
writes; the scenario and forecast revenue never go negative.
"""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core.repository import TABLES
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service import planning as eng
from permita38.core.service.plan_service import PlanningService

MON = date(2026, 2, 2)


@st.composite
def worlds(draw):
    n_users = draw(st.integers(1, 3))
    users = [
        dict(
            name=f"U{i}",
            weekly_hours=draw(st.sampled_from([0, 10, 20, 24, 40])),
            workdays=draw(st.sampled_from([[0, 1, 2, 3, 4], [0, 2, 4], [0, 1, 2]])),
            role="boss" if i == 0 else "employee",
        )
        for i in range(n_users)
    ]
    contracts = [
        dict(
            start=(MON + timedelta(days=draw(st.integers(-7, 40)))).isoformat(),
            end=None,
            hours_per_week=draw(st.one_of(st.none(), st.floats(0, 60, allow_nan=False))),
            total_hours=draw(st.one_of(st.none(), st.floats(0, 300, allow_nan=False))),
            user=draw(st.integers(0, n_users - 1)),
        )
        for _ in range(draw(st.integers(0, 4)))
    ]
    for c in contracts:
        c["end"] = (date.fromisoformat(c["start"]) + timedelta(days=draw(st.integers(0, 60)))).isoformat()
        if c["hours_per_week"] is None and c["total_hours"] is None:
            c["hours_per_week"] = 5.0
    offers = [
        dict(
            start=(MON + timedelta(days=draw(st.integers(0, 40)))).isoformat(),
            days=draw(st.integers(0, 60)),
            hpw=draw(st.floats(0, 40, allow_nan=False)),
            user=draw(st.integers(0, n_users - 1)),
            valid=draw(st.sampled_from([None, "2026-01-01", "2026-12-31"])),
        )
        for _ in range(draw(st.integers(0, 3)))
    ]
    absences = [
        dict(
            user=draw(st.integers(0, n_users - 1)),
            start=(MON + timedelta(days=draw(st.integers(0, 40)))).isoformat(),
            days=draw(st.integers(0, 10)),
        )
        for _ in range(draw(st.integers(0, 3)))
    ]
    return users, contracts, offers, absences


def build(repo, world):
    users, contracts, offers, absences = world
    ids = [repo.save("users", dict(u, username=f"u{i}", active=True))["id"] for i, u in enumerate(users)]
    for c in contracts:
        repo.save(
            "contracts",
            dict(
                title="C",
                start=c["start"],
                end=c["end"],
                hours_per_week=c["hours_per_week"],
                total_hours=c["total_hours"],
                rate=50,
                status="confirmed",
                user_ids=[ids[c["user"]]],
            ),
        )
    for o in offers:
        end = (date.fromisoformat(o["start"]) + timedelta(days=o["days"])).isoformat()
        repo.save(
            "offers",
            dict(
                number="AN-1",
                status="sent",
                lines=[],
                valid_until=o["valid"],
                plan=dict(start=o["start"], end=end, hours_per_week=round(o["hpw"], 2), user_ids=[ids[o["user"]]]),
            ),
        )
    for a in absences:
        end = (date.fromisoformat(a["start"]) + timedelta(days=a["days"])).isoformat()
        repo.save("absences", dict(user_id=ids[a["user"]], kind="vacation", start=a["start"], end=end))
    return ids


def dump(repo):
    return json.dumps({k: repo.list(k) for k in TABLES if k != "settings"}, sort_keys=True, default=str)


def check_status_and_gap(w):
    """Two-way: the gap covers every hour the week is over by, and the status is ``over`` exactly when the old
    utilisation rule says so or a project has a gap. The utilisation counts the allocations (decision D4): the rule
    reads the row's used hours with ``status_for``, the same unrounded rule the row status uses."""
    assert w["gap"] + 1e-6 >= max(0.0, -w["free_worst_case"])
    old_over = eng.status_for(w["capacity"], w["used"])[0] == "over"
    assert (w["status"] == "over") == (old_over or w["gap"] > 1e-6)
    assert w["gap_confirmed"] <= w["gap"] + 1e-6 and w["gap_expected"] <= w["gap"] + 1e-6
    assert w["gap_confirmed"] <= w["gap_expected"] + 1e-6
    for p in w.get("projects", []):
        assert p["gap_confirmed"] <= p["gap_expected"] + 1e-6 <= p["gap"] + 2e-6
        assert (p["status"] == "over") == (p["utilisation"] is None or p["utilisation"] > 100.0 or p["gap"] > 1e-6) or (
            p["status"] != "over" and p["gap"] <= 1e-6
        )
        assert not (p["status"] == "ok" and p["gap"] > 1e-6)


@given(worlds())
def test_engine_invariants_hold_for_random_worlds(world):
    with tempfile.TemporaryDirectory() as tmp:
        repo = SQLiteRepository(Path(tmp) / "d")
        repo.startup()
        try:
            build(repo, world)
            s = PlanningService(repo)
            out = s.forecast(8, None, True, today=MON)
            for w in out["weeks"]:
                assert w["capacity"] >= -1e-9
                assert abs(w["free_after_confirmed"] - (w["capacity"] - w["confirmed"])) < 1e-5
                assert abs(w["free_worst_case"] - (w["capacity"] - w["confirmed"] - w["binding_offers"])) < 1e-5
                assert w["status"] in ("ok", "tight", "over")
                assert w["revenue"] >= 0 and w["contracted_revenue"] >= 0 and w["offer_revenue"] >= 0
                check_status_and_gap(w)
            before = dump(repo)
            change = {
                "absences": [{"user_id": repo.list("users")[0]["id"], "start": "2026-02-10", "end": "2026-02-12"}],
                "commitments": [],
            }
            s.simulate(change, 8, MON, None, today=MON)
            assert dump(repo) == before
        finally:
            repo.close()


# ---------------------------------------------------------------- ring fence (docs/specs/planner-per-project.md)
@st.composite
def fenced_worlds(draw):
    """People with random allocations to two projects (over-allocation allowed) and random contracts per scope."""
    people = [
        dict(
            weekly=draw(st.sampled_from([8, 20, 32, 40])),
            workdays=draw(st.sampled_from([[0, 1, 2, 3, 4], [0, 1, 2, 3], [1, 3]])),
        )
        for _ in range(draw(st.integers(1, 3)))
    ]
    allocs = [
        dict(
            user=draw(st.integers(0, len(people) - 1)),
            project=draw(st.sampled_from("PQ")),
            start=draw(st.integers(0, 20)),
            days=draw(st.integers(0, 40)),
            hpw=draw(st.sampled_from([2.5, 10, 20, 30, 40, 80])),
        )
        for _ in range(draw(st.integers(0, 5)))
    ]
    contracts = [
        dict(
            user=draw(st.integers(0, len(people) - 1)),
            project=draw(st.sampled_from(["P", "Q", None])),
            start=draw(st.integers(0, 20)),
            days=draw(st.integers(0, 40)),
            hpw=draw(st.sampled_from([5, 10, 25, 40])),
        )
        for _ in range(draw(st.integers(0, 5)))
    ]
    absences = [
        (draw(st.integers(0, len(people) - 1)), draw(st.integers(0, 30))) for _ in range(draw(st.integers(0, 2)))
    ]
    offers = [
        dict(
            user=draw(st.integers(0, len(people) - 1)),
            project=draw(st.sampled_from(["P", "Q", None])),
            start=draw(st.integers(0, 20)),
            days=draw(st.integers(0, 40)),
            hpw=draw(st.sampled_from([5, 10, 25])),
            win=draw(st.sampled_from([0, 25, 50, 100])),
        )
        for _ in range(draw(st.integers(0, 2)))
    ]
    return people, allocs, contracts, absences, offers


def build_fenced(repo, world, skip_contracts_of=None, extra_q_person=False, with_allocations=True):
    people, allocs, contracts, absences, offers = world
    proj = {k: repo.save("projects", dict(name=f"Project {k}", status="active"))["id"] for k in "PQ"}
    ids = [
        repo.save(
            "users",
            dict(
                username=f"u{i}",
                name=f"U{i}",
                role="boss" if i == 0 else "employee",
                active=True,
                weekly_hours=p["weekly"],
                workdays=p["workdays"],
            ),
        )["id"]
        for i, p in enumerate(people)
    ]
    day = lambda n: (MON + timedelta(days=n)).isoformat()  # noqa: E731
    for a in allocs if with_allocations else []:
        repo.save(
            "allocations",
            dict(
                user_id=ids[a["user"]],
                project_id=proj[a["project"]],
                start=day(a["start"]),
                end=day(a["start"] + a["days"]),
                hours_per_week=a["hpw"],
                status="active",
            ),
        )
    for c in contracts:
        if skip_contracts_of and c["project"] == skip_contracts_of:
            continue
        rec = dict(
            title="C",
            start=day(c["start"]),
            end=day(c["start"] + c["days"]),
            hours_per_week=c["hpw"],
            rate=50,
            status="confirmed",
            user_ids=[ids[c["user"]]],
        )
        if c["project"]:
            rec["project_id"] = proj[c["project"]]
        repo.save("contracts", rec)
    for o in offers:
        if skip_contracts_of and o["project"] == skip_contracts_of:
            continue
        plan = dict(
            start=day(o["start"]),
            end=day(o["start"] + o["days"]),
            hours_per_week=o["hpw"],
            user_ids=[ids[o["user"]]],
            win_probability=o["win"],
        )
        rec = dict(number="AN-1", status="sent", lines=[], valid_until="2099-12-31", plan=plan)
        if o["project"]:
            rec["project_id"] = proj[o["project"]]
        repo.save("offers", rec)
    for uid, d in absences:
        repo.save("absences", dict(user_id=ids[uid], kind="vacation", start=day(d), end=day(d + 2)))
    if extra_q_person:  # new capacity for Q only: a new person who is allocated to Q alone
        extra = repo.save(
            "users",
            dict(
                username="extra", name="Extra", role="employee", active=True, weekly_hours=40, workdays=[0, 1, 2, 3, 4]
            ),
        )
        repo.save(
            "allocations",
            dict(
                user_id=extra["id"],
                project_id=proj["Q"],
                start=day(0),
                end=day(120),
                hours_per_week=40,
                status="active",
            ),
        )
    return proj


def project_rows(out, name):
    """The rows of one scope by name (ids differ between two runs); the ids are dropped for the same reason."""
    rows = [next((p for p in w["projects"] if p["name"] == name), None) for w in out["weeks"]]
    return [r and {k: v for k, v in r.items() if k != "project_id"} for r in rows]


def forecast_of(world, **kw):
    with tempfile.TemporaryDirectory() as tmp:
        repo = SQLiteRepository(Path(tmp) / "d")
        repo.startup()
        try:
            proj = build_fenced(repo, world, **kw)
            s = PlanningService(repo)
            return proj, s.forecast(10, None, True, today=MON), repo, eng.load(repo, MON)
        finally:
            repo.close()


def oracle_week_capacity(world, monday):
    """Independent oracle: sum over people and workdays that are neither a public holiday nor an absence day of
    weekly_hours / len(workdays)."""
    from permita38.core.holidays import holidays

    people, _, _, absences, _ = world
    total = 0.0
    for i, p in enumerate(people):
        off = {MON + timedelta(days=d + k) for ui, d in absences if ui == i for k in range(3)}
        for k in range(7):
            x = monday + timedelta(days=k)
            if x.weekday() in p["workdays"] and x not in off and x not in holidays(x.year, ""):
                total += p["weekly"] / len(p["workdays"])
    return total


def oracle_allocations(world, monday):
    """Independent oracle for the employee allocations of one week (rules 1 to 4 of docs/specs/planner-per-project.md).

    Per person and available day (a workday that is neither a public holiday nor an absence day): each allocation whose
    period covers the day gives ``hours_per_week / available days of that week``, unscaled. The real capacity of the day
    is ``weekly_hours / workdays``; when the allocations of the day add up to more than that, each is scaled pro rata;
    the rest is the unassigned capacity. Returns ``{project name or None: [allocated, capacity]}`` for the week.
    The arithmetic is written here again from the rules, not taken from the engine.
    """
    from permita38.core.holidays import holidays

    people, allocs, _, absences, _ = world
    out = {"P": [0.0, 0.0], "Q": [0.0, 0.0], None: [0.0, 0.0]}
    week_days = [monday + timedelta(days=k) for k in range(7)]
    for i, p in enumerate(people):
        off = {MON + timedelta(days=d + k) for ui, d in absences if ui == i for k in range(3)}

        def available(x):
            return x.weekday() in p["workdays"] and x not in off and not holidays(x.year, "").get(x)

        n_avail = sum(available(x) for x in week_days)
        for x in week_days:
            if not available(x):
                continue
            real = p["weekly"] / len(p["workdays"])
            day = (x - MON).days
            today = [
                (a["project"], a["hpw"] / n_avail)
                for a in allocs
                if a["user"] == i and a["start"] <= day <= a["start"] + a["days"]
            ]
            total = sum(h for _, h in today)
            scale = real / total if total > real + 1e-9 else 1.0
            for name, h in today:
                out[name][0] += h
                out[name][1] += h * scale
            if real - total > 1e-9:
                out[None][1] += real - total
    return out


def check_allocations_oracle(world, out, proj):
    """Week ``allocated`` and the project ``allocated`` / ``capacity`` (and the unassigned capacity) equal the oracle; the
    team ``used`` is at least the team ``allocated``. Only the allocation part is re-computed: the project ``used`` is
    not in the public rows and demand is not re-computed, so ``used`` is checked as a bound only. Tolerance 0.1 h."""
    tol = 0.1
    ids = {v: k for k, v in proj.items()}
    for w in out["weeks"]:
        exp = oracle_allocations(world, date.fromisoformat(w["week"]))
        rows = {p["project_id"]: p for p in w["projects"]}
        for pid, name in ids.items():
            r = rows.get(pid, {"allocated": 0.0, "capacity": 0.0})
            assert abs(r["allocated"] - exp[name][0]) <= tol, (w["week"], name, r["allocated"], exp[name])
            assert abs(r["capacity"] - exp[name][1]) <= tol, (w["week"], name, r["capacity"], exp[name])
        un = rows.get(None, {"allocated": 0.0, "capacity": 0.0})
        assert abs(un["allocated"]) <= tol and abs(un["capacity"] - exp[None][1]) <= tol, (w["week"], un)
        assert abs(w["allocated"] - sum(v[0] for v in exp.values())) <= tol, (w["week"], w["allocated"])
        assert w["used"] + tol >= w["allocated"], (w["week"], w["used"], w["allocated"])


NOT_COMPARED = (
    "status",
    "gap",
    "gap_confirmed",
    "gap_expected",
    "projects",
    "contributions",
    "utilisation",
    "used",
    "allocated",
)


@settings(max_examples=60, deadline=None)
@given(fenced_worlds())
def test_ring_fence_properties(world):
    proj, out, _, w0 = forecast_of(world)
    for w in out["weeks"]:
        # (a) nothing invented, nothing lost: the scopes add up to the team capacity, and that equals the independent
        # oracle (people x workdays that are not holiday or absence)
        assert abs(sum(p["capacity"] for p in w["projects"]) - w["capacity"]) < 1e-4
        assert abs(w["capacity"] - oracle_week_capacity(world, date.fromisoformat(w["week"]))) < 1e-4
        # (b) the team gap is the sum of the scope gaps and never reduced by a surplus
        assert abs(sum(p["gap"] for p in w["projects"]) - w["gap"]) < 1e-4
        assert all(p["gap"] >= 0 and p["internal"]["gap"] >= 0 for p in w["projects"])
        check_status_and_gap(w)
        # demand is booked once, in the scope of its project
        assert abs(sum(p["confirmed"] for p in w["projects"]) - w["confirmed"]) < 1e-4
        assert abs(sum(p["binding_offers"] for p in w["projects"]) - w["binding_offers"]) < 1e-4
    check_allocations_oracle(world, out, proj)  # independent oracle for the allocated hours and the scaled capacity
    # (a) day level: per day the scopes add up to the day capacity
    table = eng.day_table(w0, MON, MON + timedelta(days=70))
    for d in table.days.values():
        assert abs(sum(sd.cap for sd in d.scopes.values()) - d.cap) < 1e-9
    # (c) removing every contract and offer of project Q leaves the rows of P and of the unassigned scope identical
    _, without_q, _, _ = forecast_of(world, skip_contracts_of="Q")
    for key in ("Project P", "Unassigned"):
        assert project_rows(out, key) == project_rows(without_q, key)
    # (d) adding capacity to Q never changes P's row (not only its gap)
    _, more_q, _, _ = forecast_of(world, extra_q_person=True)
    assert project_rows(out, "Project P") == project_rows(more_q, "Project P")


@settings(max_examples=60, deadline=None)
@given(fenced_worlds())
def test_allocations_never_change_the_team_numbers(world):
    """Equivalence: the same data with and without allocations gives the same team capacity, demand, free hours,
    revenue and day-table cap/confirmed/binding; only status, gap, utilisation, used and allocated may differ. Utilisation
    counts the allocations as used (decision D4), so it never drops when allocations are added."""
    _, with_a, _, w_with = forecast_of(world)
    _, without_a, _, w_without = forecast_of(world, with_allocations=False)
    for a, b in zip(with_a["weeks"], without_a["weeks"]):
        assert b["allocated"] == 0.0 and a["allocated"] >= 0.0
        if a["utilisation"] is not None and b["utilisation"] is not None:
            assert a["utilisation"] >= b["utilisation"] - 0.06
        elif b["utilisation"] is not None:
            assert a["utilisation"] is None  # cap 0 with used hours, only possible when allocations add used hours
        strip = lambda cs: sorted((c["kind"], c["title"], c["hours"]) for c in cs)  # noqa: E731  (ids differ per run)
        assert strip(a["contributions"]) == strip(b["contributions"])
        assert {k: v for k, v in a.items() if k not in NOT_COMPARED} == {
            k: v for k, v in b.items() if k not in NOT_COMPARED
        }
    t1 = eng.day_table(w_with, MON, MON + timedelta(days=70))
    t2 = eng.day_table(w_without, MON, MON + timedelta(days=70))
    for x in t1.days:
        for f in ("cap", "confirmed", "binding", "expected"):
            assert abs(getattr(t1.days[x], f) - getattr(t2.days[x], f)) < 1e-9


def _strip_ids(x):
    """Random ids differ between two databases: they are dropped before the forecasts are compared."""
    if isinstance(x, dict):
        return {k: _strip_ids(v) for k, v in x.items() if k != "id"}
    if isinstance(x, list):  # the order of records follows the random ids: compare them as multisets
        return sorted((_strip_ids(v) for v in x), key=lambda v: json.dumps(v, sort_keys=True, default=str))
    return x


def _forecast_without_externals(world, with_suppliers: bool):
    """The forecast with include_external=0 of the world, optionally with a subcontractor assigned to every contract
    and holding an external allocation (the flag must make the two worlds equal)."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = SQLiteRepository(Path(tmp) / "d")
        repo.startup()
        try:
            build(repo, world)
            if with_suppliers:
                sup = repo.save(
                    "suppliers",
                    dict(
                        name="Subcontractor",
                        kind="person-capacity",
                        active=True,
                        weekly_hours=24.0,
                        workdays=[0, 1, 2, 3],
                        rate=80.0,
                        country="DE",
                        vat_mode="domestic",
                    ),
                )
                for c in repo.list("contracts"):
                    repo.save("contracts", dict(c, user_ids=[*c["user_ids"], sup["id"]]))
                proj = repo.save("projects", dict(name="Ext project", status="active"))
                repo.save(
                    "allocations",
                    dict(
                        supplier_id=sup["id"],
                        project_id=proj["id"],
                        start="2026-02-02",
                        end="2026-03-29",
                        hours_per_week=20.0,
                        status="active",
                        rate=80.0,
                    ),
                )
            out = _strip_ids(PlanningService(repo).forecast(10, None, True, today=MON, include_external=False))
            out["expired_offers"] = len(out["expired_offers"])  # ids of the old offers: random
            return out
        finally:
            repo.close()


@given(worlds())
@settings(max_examples=15, deadline=None)
def test_include_external_off_equals_the_world_without_suppliers(world):
    assert _forecast_without_externals(world, True) == _forecast_without_externals(world, False)
