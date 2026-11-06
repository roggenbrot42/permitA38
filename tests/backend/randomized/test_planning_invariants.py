"""Randomized invariants of the project-centric planner (docs/specs/project-centric-planning.md, S1-S6, D1-D6, C1-C6).

Random worlds: people (boss and employees), subcontractors, assignments (weekly or total, to customer and internal
projects), contracts and offers (weekly or total, with and without a project), bookings (also after today), public
holidays and absences. For every world the planner (``PlannerService.plan`` with a fixed "today") must keep:

* shortfall >= 0, covered >= 0 and covered + shortfall == remaining for every item; remaining == max(0, promised -
  booked) for total items; without offers the worst case equals the contracts' shortfall;
* adding supply (an assignment) never increases any contract shortfall, adding an offer never changes a contract verdict;
* the supply of the projects and the assigned hours of the people equal an independent re-computation of S1 (the
  spreading over the days after today inside the range); the week capacity equals an independent oracle;
* per person and day: unassigned >= 0 and capacity == assigned + unassigned - over_assigned (S3);
* nothing in the engine (plan, simulate, suggestions, checks) writes to the store.
"""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core.holidays import holidays
from permita38.core.repository import TABLES
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.planner import load as planner_load
from permita38.core.service.planner import supply as supply_mod
from permita38.core.service.planner.service import PlannerService
from permita38.core.settings import effective_settings

TODAY = date(2026, 11, 11)  # a Wednesday: the current week is cut in half
MON = TODAY - timedelta(days=TODAY.weekday())
WEEKS = 10  # reaches over 25.12., 26.12. and 01.01. (public holidays in every state)
LAST = MON + timedelta(weeks=WEEKS) - timedelta(days=1)
TOL = 0.011  # two-decimal rounding of one value
WORKDAYS = [[0, 1, 2, 3, 4], [0, 2, 4], [0, 1, 2, 3]]
PROJECTS = ("P0", "P1", "I", None)  # two customer projects, one internal project, "no project" (contracts, offers)


def day(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


@st.composite
def worlds(draw):
    n_users = draw(st.integers(1, 3))
    users = [
        dict(
            weekly=draw(st.sampled_from([0, 10, 20, 24, 40])),
            workdays=draw(st.sampled_from(WORKDAYS)),
            role="boss" if i == 0 else "employee",
        )
        for i in range(n_users)
    ]
    subs = [
        dict(workdays=draw(st.sampled_from(WORKDAYS)), rate=draw(st.sampled_from([60, 90.5])))
        for _ in range(draw(st.integers(0, 2)))
    ]
    owners = [("u", i) for i in range(n_users)] + [("s", i) for i in range(len(subs))]

    def hours(max_week=60.0):
        if draw(st.booleans()):
            return dict(hpw=draw(st.sampled_from([0.5, 5.0, 10.0, 24.0, max_week])), total=None)
        return dict(hpw=None, total=draw(st.sampled_from([1.0, 10.0, 50.0, 200.0])))

    assignments = []
    for _ in range(draw(st.integers(0, 6))):
        kind, idx = draw(st.sampled_from(owners))
        # a subcontractor works for customer projects only (its hours are not part of the internal figures)
        project = draw(st.sampled_from(("P0", "P1") if kind == "s" else ("P0", "P1", "I")))
        start = draw(st.integers(-14, 50))
        assignments.append(
            dict(kind=kind, idx=idx, project=project, start=start, end=start + draw(st.integers(0, 70)), **hours())
        )
    items = []
    for kind in ("contract", "offer"):
        for _ in range(draw(st.integers(0, 4 if kind == "contract" else 3))):
            start = draw(st.integers(-14, 45))
            items.append(
                dict(
                    kind=kind,
                    project=draw(st.sampled_from(PROJECTS)),
                    start=start,
                    end=start + draw(st.integers(0, 60)),
                    win=draw(st.sampled_from([0, 25, 50, 100])),
                    **hours(40.0),
                )
            )
    bookings = [
        dict(
            user=draw(st.integers(0, n_users - 1)),
            project=draw(st.sampled_from(("P0", "P1", "I", None))),
            day=draw(st.integers(-25, 6)),  # also after today: those entries are not history
            minutes=draw(st.sampled_from([15, 60, 240, 480, 600])),
        )
        for _ in range(draw(st.integers(0, 8)))
    ]
    absences = [
        dict(
            user=draw(st.integers(0, n_users - 1)),
            start=draw(st.integers(-5, 50)),
            days=draw(st.integers(0, 10)),
            kind=draw(st.sampled_from(["vacation", "sick"])),
        )
        for _ in range(draw(st.integers(0, 3)))
    ]
    return dict(users=users, subs=subs, assignments=assignments, items=items, bookings=bookings, absences=absences)


class Built:
    """A repository filled from a world; ``ids`` maps the symbolic names to the stored ids."""

    def __init__(self, repo, world, with_offers=True):
        self.repo = repo
        self.world = world
        customer = repo.save("customers", dict(name="Customer"))["id"]
        self.pid = {None: None}
        for name in ("P0", "P1"):
            self.pid[name] = repo.save(
                "projects", dict(name=f"Project {name}", kind="customer", customer_id=customer, status="active")
            )["id"]
        self.pid["I"] = repo.save("projects", dict(name="Internal", kind="internal", status="active"))["id"]
        self.users = [
            repo.save(
                "users",
                dict(
                    username=f"u{i}",
                    name=f"User {i}",
                    role=u["role"],
                    active=True,
                    weekly_hours=u["weekly"],
                    workdays=u["workdays"],
                ),
            )["id"]
            for i, u in enumerate(world["users"])
        ]
        self.subs = [
            repo.save(
                "suppliers",
                dict(
                    name=f"Sub {i}",
                    kind="person-capacity",
                    active=True,
                    workdays=s["workdays"],
                    rate=s["rate"],
                    country="DE",
                    vat_mode="domestic",
                ),
            )["id"]
            for i, s in enumerate(world["subs"])
        ]
        for a in world["assignments"]:
            self.add_assignment(a)
        # a tiny contract per customer project, so that every project has a verdict (and shows its supply)
        for name in ("P0", "P1"):
            repo.save(
                "contracts",
                dict(
                    title="Sentinel",
                    project_id=self.pid[name],
                    start=day(0),
                    end=day(300),
                    total_hours=0.01,
                    rate=50,
                    status="confirmed",
                ),
            )
        for i, it in enumerate(world["items"]):
            if it["kind"] == "contract" or with_offers:
                self.add_item(it, i)
        for b in world["bookings"]:
            repo.save(
                "entries",
                dict(
                    user_id=self.users[b["user"]],
                    project_id=self.pid[b["project"]],
                    date=day(b["day"]),
                    minutes=b["minutes"],
                ),
            )
        for a in world["absences"]:
            end = date.fromisoformat(day(a["start"])) + timedelta(days=a["days"])
            repo.save(
                "absences",
                dict(user_id=self.users[a["user"]], kind=a["kind"], start=day(a["start"]), end=end.isoformat()),
            )

    def add_assignment(self, a):
        row = dict(
            project_id=self.pid[a["project"]],
            start=day(a["start"]),
            end=day(a["end"]),
            hours_per_week=a["hpw"],
            total_hours=a["total"],
            status="active",
        )
        if a["kind"] == "u":
            row["user_id"] = self.users[a["idx"]]
        else:
            row.update(supplier_id=self.subs[a["idx"]], rate=self.world["subs"][a["idx"]]["rate"])
        return self.repo.save("allocations", {k: v for k, v in row.items() if v is not None})

    def add_item(self, it, i):
        if it["kind"] == "contract":
            row = dict(
                title=f"C{i}",
                project_id=self.pid[it["project"]],
                start=day(it["start"]),
                end=day(it["end"]),
                hours_per_week=it["hpw"],
                total_hours=it["total"],
                rate=50,
                status="confirmed",
            )
            return self.repo.save("contracts", {k: v for k, v in row.items() if v is not None})
        plan = dict(
            start=day(it["start"]),
            end=day(it["end"]),
            hours_per_week=it["hpw"],
            total_hours=it["total"],
            win_probability=it["win"],
            rate=50,
        )
        row = dict(
            number=f"AN-{i + 1}",
            status="sent",
            lines=[],
            valid_until=None,
            project_id=self.pid[it["project"]],
            plan={k: v for k, v in plan.items() if v is not None},
        )
        return self.repo.save("offers", {k: v for k, v in row.items() if v is not None})


def run(world, check, with_offers=True):
    with tempfile.TemporaryDirectory() as tmp:
        repo = SQLiteRepository(Path(tmp) / "d")
        repo.startup()
        try:
            check(Built(repo, world, with_offers), PlannerService(repo))
        finally:
            repo.close()


def dump(repo):
    return json.dumps(
        {**{k: repo.list(k) for k in TABLES if k != "settings"}, "settings": repo.settings()},
        sort_keys=True,
        default=str,
    )


def items_of(body):
    return [it for p in body["projects"] if p["kind"] == "customer" for it in p["items"]]


# ---------------------------------------------------------------- item and project verdicts
@settings(max_examples=60, deadline=None)
@given(worlds())
def test_item_verdicts_are_consistent(world):
    def check(b, svc):
        body = svc.plan(WEEKS, today=TODAY)
        for p in body["projects"]:
            if p["kind"] != "customer":
                continue
            for it in p["items"]:
                assert it["shortfall"] >= 0 and it["covered"] >= 0, it
                assert abs(it["covered"] + it["shortfall"] - it["remaining"]) <= TOL, it
                if it["type"] == "total":
                    assert abs(it["remaining"] - max(0.0, it["promised"] - it["booked"])) <= TOL, it
                else:
                    assert abs(it["shortfall"] - sum(w["shortfall"] for w in it["weekly_shortfalls"])) <= 0.05, it
                    assert all(w["shortfall"] > 0 for w in it["weekly_shortfalls"]), it
            contracts = [i for i in p["items"] if i["kind"] == "contract"]
            offers = [i for i in p["items"] if i["kind"] == "offer"]
            assert abs(p["shortfall_contracts"] - sum(i["shortfall"] for i in contracts)) <= 0.05 * (len(contracts) + 1)
            assert p["shortfall_worst_case"] + TOL >= p["shortfall_contracts"]
            assert p["shortfall_expected"] + TOL >= p["shortfall_contracts"]
            assert p["shortfall_worst_case"] + TOL >= p["shortfall_expected"]
            assert (p["status"] == "short") == (
                p["shortfall_contracts"] > 0 or any(i["shortfall"] > 0 for i in contracts)
            )
            if not offers:
                assert abs(p["shortfall_worst_case"] - p["shortfall_contracts"]) <= TOL, p
                assert abs(p["shortfall_expected"] - p["shortfall_contracts"]) <= TOL, p

    run(world, check)


@settings(max_examples=40, deadline=None)
@given(worlds())
def test_without_offers_the_worst_case_is_the_contracts_shortfall(world):
    def check(b, svc):
        body = svc.plan(WEEKS, today=TODAY)
        for p in body["projects"]:
            if p["kind"] == "customer":
                assert p["remaining_offers"] == 0.0 and not [i for i in p["items"] if i["kind"] == "offer"]
                assert p["shortfall_worst_case"] == p["shortfall_contracts"] == p["shortfall_expected"], p

    run(world, check, with_offers=False)


# ---------------------------------------------------------------- monotony
@settings(max_examples=50, deadline=None)
@given(worlds(), st.data())
def test_adding_supply_never_increases_a_contract_shortfall(world, data):
    extra_kind = data.draw(st.sampled_from(["u", "s"] if world["subs"] else ["u"]))
    start = data.draw(st.integers(-14, 50))
    extra = dict(
        kind=extra_kind,
        idx=data.draw(st.integers(0, (len(world["users"]) if extra_kind == "u" else len(world["subs"])) - 1)),
        project=data.draw(st.sampled_from(("P0", "P1"))),
        start=start,
        end=start + data.draw(st.integers(0, 80)),
        **data.draw(
            st.sampled_from([dict(hpw=10.0, total=None), dict(hpw=40.0, total=None), dict(hpw=None, total=30.0)])
        ),
    )

    def check(b, svc):
        before = svc.plan(WEEKS, today=TODAY)
        b.add_assignment(extra)
        after = svc.plan(WEEKS, today=TODAY)
        was = {it["id"]: it["shortfall"] for it in items_of(before) if it["kind"] == "contract"}
        for it in items_of(after):
            if it["kind"] == "contract":
                assert it["shortfall"] <= was[it["id"]] + TOL, (it, was[it["id"]])
        proj = {p["project_id"]: p["shortfall_contracts"] for p in before["projects"] if p["kind"] == "customer"}
        for p in after["projects"]:
            if p["kind"] == "customer":
                assert p["shortfall_contracts"] <= proj.get(p["project_id"], 0.0) + TOL

    run(world, check)


@settings(max_examples=50, deadline=None)
@given(worlds(), st.data())
def test_adding_an_offer_never_changes_the_contract_verdicts(world, data):
    # D4 lets a weekly offer take booked hours of its week before a total contract does (weekly items first), so a
    # new offer can change a total contract's remaining when hours were booked; the claim holds without bookings.
    world = dict(world, bookings=[])
    start = data.draw(st.integers(-14, 45))
    offer = dict(
        kind="offer",
        project=data.draw(st.sampled_from(PROJECTS)),
        start=start,
        end=start + data.draw(st.integers(0, 60)),
        win=data.draw(st.sampled_from([0, 50, 100])),
        **data.draw(st.sampled_from([dict(hpw=20.0, total=None), dict(hpw=None, total=80.0)])),
    )

    def contracts(body):
        out = {}
        for p in body["projects"]:
            if p["kind"] == "customer":
                out[p["project_id"]] = (
                    [i for i in p["items"] if i["kind"] == "contract"],
                    {k: p[k] for k in ("remaining_contracts", "shortfall_contracts", "supply", "contract_value")},
                )
        return out

    def check(b, svc):
        before = contracts(svc.plan(WEEKS, today=TODAY))
        b.add_item(offer, 99)
        after = contracts(svc.plan(WEEKS, today=TODAY))
        assert {k: v for k, v in after.items() if k in before} == before
        assert all(not v[0] for k, v in after.items() if k not in before)  # a new scope holds the offer only

    run(world, check)


# ---------------------------------------------------------------- supply, capacity (independent oracles)
def oracle_available(world, state, absent):
    """(owner key, day) -> available: a workday that is neither a public holiday nor a day off."""

    def avail(kind, idx, d):
        workdays = (world["users"] if kind == "u" else world["subs"])[idx]["workdays"]
        return d.weekday() in workdays and d not in holidays(d.year, state) and (kind, idx, d) not in absent

    return avail


def oracle_supply(world, b, state, hi=LAST):
    """S1 from the rules, written here again: hours per assignment inside the plan range (days after today).
    Weekly: hours_per_week / available days of the (whole) Monday-Sunday week on every available day of the period;
    total: (total - hours the person booked on the project in the period up to today) / available days after today
    of the period, on every one of them. Returns {assignment index: hours inside MON..hi after today}."""
    absent = set()
    for a in world["absences"]:
        s = date.fromisoformat(day(a["start"]))
        for k in range(a["days"] + 1):
            absent.add(("u", a["user"], s + timedelta(days=k)))
    avail = oracle_available(world, state, absent)
    out = {}
    for n, a in enumerate(world["assignments"]):
        s, e = TODAY + timedelta(days=a["start"]), TODAY + timedelta(days=a["end"])
        days = [s + timedelta(days=k) for k in range((e - s).days + 1)]
        future = [d for d in days if d > TODAY and avail(a["kind"], a["idx"], d)]
        if a["hpw"] is not None:
            per = {}
            for d in future:
                mon = d - timedelta(days=d.weekday())
                if mon not in per:
                    per[mon] = sum(avail(a["kind"], a["idx"], mon + timedelta(days=k)) for k in range(7))
            hours = {d: a["hpw"] / per[d - timedelta(days=d.weekday())] for d in future}
        else:
            booked = 0.0
            if a["kind"] == "u":
                booked = sum(
                    x["minutes"] / 60
                    for x in world["bookings"]
                    if x["user"] == a["idx"]
                    and x["project"] == a["project"]
                    and s <= TODAY + timedelta(days=x["day"]) <= min(e, TODAY)
                )
            rest = max(0.0, a["total"] - booked)
            hours = {d: rest / len(future) for d in future} if future and rest > 0 else {}
        out[n] = sum(h for d, h in hours.items() if MON <= d <= hi)
    return out


@settings(max_examples=60, deadline=None)
@given(worlds())
def test_project_supply_and_assigned_hours_equal_the_spreading(world):
    def check(b, svc):
        state = effective_settings(b.repo.settings()).get("state") or ""
        expected = oracle_supply(world, b, state)
        # spec: "people and projects[].supply cover the same range as weeks" (the requested weeks, after today)
        judged = expected
        body = svc.plan(WEEKS, today=TODAY)
        by_project = {p["project_id"]: p for p in body["projects"]}
        for name in ("P0", "P1"):
            want = sum(judged[n] for n, a in enumerate(world["assignments"]) if a["project"] == name)
            got = by_project[b.pid[name]]["supply"]
            assert abs(got["total"] - want) <= 0.03, (name, got, want)
            emp = sum(
                judged[n] for n, a in enumerate(world["assignments"]) if a["project"] == name and a["kind"] == "u"
            )
            assert abs(got["employees"] - emp) <= 0.03 and abs(got["subcontractors"] - (want - emp)) <= 0.03, got
        want = sum(expected[n] for n, a in enumerate(world["assignments"]) if a["project"] == "I")
        entry = by_project[b.pid["I"]]
        # an internal project that a generated (legacy) item still refers to is judged like a customer project
        got_i = entry["assigned"] if entry["kind"] == "internal" else entry["supply"]["total"]
        assert abs(got_i - want) <= 0.03
        for i, uid in enumerate(b.users):
            row = next(p for p in body["people"] if p["user_id"] == uid)
            want = sum(expected[n] for n, a in enumerate(world["assignments"]) if a["kind"] == "u" and a["idx"] == i)
            assert abs(row["assigned"] - want) <= 0.03, (row, want)
            assert abs(sum(x["hours"] for x in row["assignments"]) - want) <= 0.03 * (len(row["assignments"]) + 1)

    run(world, check)


@settings(max_examples=60, deadline=None)
@given(worlds())
def test_capacity_and_unassigned_add_up(world):
    def check(b, svc):
        state = effective_settings(b.repo.settings()).get("state") or ""
        body = svc.plan(WEEKS, today=TODAY)
        absent = set()
        for a in world["absences"]:
            s = date.fromisoformat(day(a["start"]))
            absent |= {("u", a["user"], s + timedelta(days=k)) for k in range(a["days"] + 1)}
        avail = oracle_available(world, state, absent)
        assert [w["week"] for w in body["weeks"]] == [(MON + timedelta(weeks=k)).isoformat() for k in range(WEEKS)]
        for w in body["weeks"]:
            mon = date.fromisoformat(w["week"])
            cap = sum(
                u["weekly"] / len(u["workdays"])
                for i, u in enumerate(world["users"])
                for k in range(7)
                if mon + timedelta(days=k) > TODAY and avail("u", i, mon + timedelta(days=k))
            )
            assert abs(w["capacity"] - cap) <= TOL, (w["week"], w["capacity"], cap)
            assert w["unassigned"] >= 0 and w["over_assigned"] >= 0 and w["capacity"] >= 0
            assert w["assigned_customer"] >= 0 and w["assigned_internal"] >= 0
            lhs = w["capacity"]
            rhs = w["assigned_customer"] + w["assigned_internal"] + w["unassigned"] - w["over_assigned"]
            assert abs(lhs - rhs) <= 0.04, w
            assert w["subcontractor_hours"] >= 0
        assert abs(sum(w["capacity"] for w in body["weeks"]) - sum(p["capacity"] for p in body["people"])) <= 0.1
        assert abs(sum(w["unassigned"] for w in body["weeks"]) - sum(p["unassigned"] for p in body["people"])) <= 0.1
        # per person and day, on the engine's own supply
        snap = planner_load.load(b.repo, TODAY)
        sup = supply_mod.build(snap.owners, snap.assignments, snap.holiday, TODAY, MON, LAST)
        for rows in sup.people.values():
            for d, pd in rows.items():
                assert d > TODAY and pd.capacity >= 0 and pd.assigned >= 0
                unassigned, over = max(0.0, pd.capacity - pd.assigned), max(0.0, pd.assigned - pd.capacity)
                assert unassigned >= 0 and over >= 0
                assert abs(pd.capacity - (pd.assigned + unassigned - over)) < 1e-9
                assert abs(pd.assigned - pd.assigned_customer - pd.assigned_internal) < 1e-9

    run(world, check)


# ---------------------------------------------------------------- the engine never writes
@settings(max_examples=30, deadline=None)
@given(worlds())
def test_nothing_in_the_engine_writes_to_the_store(world):
    def check(b, svc):
        before = dump(b.repo)
        uid = b.users[0]
        changes = {
            "absences": [{"user_id": uid, "start": day(3), "end": day(9)}],
            "commitments": [],
            "allocations": [
                {"user_id": uid, "project_id": b.pid["P0"], "start": day(1), "end": day(30), "hours_per_week": 8}
            ],
        }
        svc.plan(WEEKS, today=TODAY)
        svc.plan(WEEKS, user_id=uid, project_id=b.pid["P0"], today=TODAY)
        svc.simulate(changes, WEEKS, today=TODAY)
        svc.suggest_vacation(uid, 3, TODAY + timedelta(days=1), TODAY + timedelta(days=60), today=TODAY)
        svc.suggest_start(20.0, TODAY + timedelta(days=60), project_id=b.pid["P0"], today=TODAY)
        svc.what_must_move(dict(start=day(5), end=day(40), hours_per_week=10.0, project_id=b.pid["P0"]), today=TODAY)
        svc.check_offer(
            dict(project_id=b.pid["P1"], plan=dict(start=day(5), end=day(40), hours_per_week=10.0), lines=[]),
            today=TODAY,
        )
        for coll in ("contracts", "offers"):
            for rec in b.repo.list(coll):
                svc.warnings_for(coll, rec, today=TODAY)
        assert dump(b.repo) == before

    run(world, check)


@settings(max_examples=25, deadline=None)
@given(worlds())
def test_simulating_nothing_changes_nothing(world):
    """A what-if without changes has delta 0 everywhere and the same body as the plan."""

    def check(b, svc):
        plain = svc.plan(WEEKS, today=TODAY)
        sim = svc.simulate({}, WEEKS, today=TODAY)
        delta = sim.pop("delta")
        assert delta["total"]["delta"] == 0 and all(p["delta"] == 0 for p in delta["projects"])
        assert sim == plain

    run(world, check)
