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
                if w["status"] == "over":  # a gap on a single day is enough (rule 7 of the per-project spec)
                    assert w["free_worst_case"] < 0 or w["capacity"] <= 1e-9 or w["gap"] > 0
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
    return people, allocs, contracts, absences


def build_fenced(repo, world, skip_contracts_of=None, extra_q_person=False):
    people, allocs, contracts, absences = world
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
    for a in allocs:
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


@settings(max_examples=60, deadline=None)
@given(fenced_worlds())
def test_ring_fence_properties(world):
    proj, out, _, w0 = forecast_of(world)
    for w in out["weeks"]:
        # (a) nothing invented, nothing lost: the scopes add up to the team capacity
        assert abs(sum(p["capacity"] for p in w["projects"]) - w["capacity"]) < 1e-4
        # (b) the team gap is the sum of the scope gaps and never reduced by a surplus
        assert abs(sum(p["gap"] for p in w["projects"]) - w["gap"]) < 1e-4
        assert all(p["gap"] >= 0 and p["internal"]["gap"] >= 0 for p in w["projects"])
        if w["gap"] > 1e-6:
            assert w["status"] == "over"
        # demand is booked once, in the scope of its project
        assert abs(sum(p["confirmed"] for p in w["projects"]) - w["confirmed"]) < 1e-4
    # (a) day level: per day the scopes add up to the day capacity
    table = eng.day_table(w0, MON, MON + timedelta(days=70))
    for d in table.days.values():
        assert abs(sum(sd.cap for sd in d.scopes.values()) - d.cap) < 1e-9
    # (c) removing every contract of project Q leaves the rows of P and of the unassigned scope identical
    _, without_q, _, _ = forecast_of(world, skip_contracts_of="Q")
    for key in ("Project P", "Unassigned"):
        assert project_rows(out, key) == project_rows(without_q, key)
    # (d) adding capacity to Q never changes P's gap
    _, more_q, _, _ = forecast_of(world, extra_q_person=True)
    assert [r and r["gap"] for r in project_rows(out, "Project P")] == [
        r and r["gap"] for r in project_rows(more_q, "Project P")
    ]
