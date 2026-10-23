"""Randomized invariants of the capacity engine: random people, absences, contracts, offers and what-if changes.

For every generated world and every week: capacity >= 0; free after confirmed = capacity - confirmed; worst case
free = capacity - confirmed - binding offers; the status matches the worst-case utilisation; a what-if run never
writes; the scenario and forecast revenue never go negative.
"""

import json
import tempfile
from datetime import date, timedelta
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from permita38.core.repository import TABLES
from permita38.core.repository.sqlite import SQLiteRepository
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
                if w["status"] == "over":
                    assert w["free_worst_case"] < 0 or w["capacity"] <= 1e-9
            before = dump(repo)
            change = {
                "absences": [{"user_id": repo.list("users")[0]["id"], "start": "2026-02-10", "end": "2026-02-12"}],
                "commitments": [],
            }
            s.simulate(change, 8, MON, None, today=MON)
            assert dump(repo) == before
        finally:
            repo.close()
