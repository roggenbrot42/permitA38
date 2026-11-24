"""Trip postings (docs/specs/accounting.md 9.5, 10, 11, 16). Expected amounts are hand-computed:

Mon 9 Nov 2026 08:00 to Wed 11 Nov 18:00, domestic: allowance 14 + 28 + 14 = 56,00 EUR; 10 km x 0,30 EUR = 3,00 EUR
mileage; total 59,00 EUR = 5900 cents. The trip ends 11 Nov 2026: that is the ``income_date``.
"""

import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from permita38.core import charts, models
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.journal import JournalService
from permita38.core.service.locks import LockService
from permita38.core.service.trip_postings import TripPostingService
from permita38.core.service.trips import TripService
from permita38.core.settings import ACCOUNTING_START_KEY

FIXTURES = Path(__file__).parent / "fixtures"
HAMBURG = [{"from_date": "2026-11-09", "country": "DE", "city": "Hamburg"}]
FINAL_OK = {"cost_centre": "general", "not_needed": ["accommodation", "transportation", "food", "material"]}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURES / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    r = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    r.startup()
    yield r
    r.close()


def init(repo, start: str | None = "2026-01-01") -> None:
    repo.save(
        "fiscal_years",
        {
            "year": 2026, "chart": "skr03", "income_basis": "accrual", "vat_basis": "soll", "vat_frequency": "month",
            "dauerfrist": False, "small_business": False, "rules_version": "skr03-2026/vat-2026",
            "opened_at": 1, "opened_by": "u1x000",
        },
    )  # fmt: skip
    if start:
        repo.save_settings({ACCOUNTING_START_KEY: start})


def boss(repo) -> dict:
    return repo.save("users", {"username": "boss1", "name": "Demo Boss", "role": "boss"})


def member(repo) -> dict:
    return repo.save("users", {"username": "emp1", "name": "Demo Employee", "role": "employee"})


def body(**kw: Any) -> dict:
    out: dict[str, Any] = {
        "purpose": "Customer visit Hamburg",
        "start": "2026-11-09T08:00",
        "end": "2026-11-11T18:00",
        "places": HAMBURG,
        "mileage": [{"date": "2026-11-09", "km": 10}],
        "status": "final",
        **FINAL_OK,
    }
    out.update(kw)
    return out


def rows(repo) -> list:
    return JournalService(repo).entries(2026)


def lines_of(entry) -> list[tuple[str, int, Any]]:
    return [(ln.role, int(round(float(ln.amount) * 100)), ln.income_date) for ln in entry.lines]


def lock_through(repo, month: int) -> None:
    LockService(repo, today=lambda: date(2026, 12, 15)).lock_up_to(2026, month, "manual", "u1x000")


def test_owner_trip_posts_on_finalise(repo):
    init(repo)
    me = boss(repo)
    out = TripService(repo).create(me, body())
    assert out["post_rev"] == 1
    (entry,) = rows(repo)
    assert entry.event_key == f"trip:{out['id']}:r1"
    assert lines_of(entry) == [
        ("travel_costs", 5900, "2026-11-11"),
        ("private_deposit", -5900, None),
    ]
    assert str(entry.document_date) == "2026-11-11"
    assert all(ln.tax_key is None and ln.vat_date is None for ln in entry.lines)


def test_team_member_trip_credits_payables_employee(repo):
    init(repo)
    out = TripService(repo).create(member(repo), body())
    (entry,) = rows(repo)
    assert [(ln[0], ln[1]) for ln in lines_of(entry)] == [("travel_costs", 5900), ("payables_employee", -5900)]
    assert out["post_rev"] == 1


def test_draft_posts_nothing(repo):
    init(repo)
    out = TripService(repo).create(boss(repo), body(status="draft"))
    assert rows(repo) == []
    assert out.get("post_rev") is None


def test_reopen_posts_reversal_and_refinalise_posts_r2(repo):
    init(repo)
    me = boss(repo)
    trips = TripService(repo)
    tid = trips.create(me, body())["id"]
    reopened = trips.reopen(me, tid)
    assert reopened["status"] == "draft" and reopened["post_rev"] == 1
    first, rev = rows(repo)
    assert rev.event_key == f"trip:{tid}:r1:rev" and rev.reverses_id == first.id
    assert [(ln[0], ln[1]) for ln in lines_of(rev)] == [("travel_costs", -5900), ("private_deposit", 5900)]
    # final again with the same figures: r1 is reversed, so a fresh r2 posts
    trips.update(me, tid, body())
    assert [e.event_key for e in rows(repo)] == [f"trip:{tid}:r1", f"trip:{tid}:r1:rev", f"trip:{tid}:r2"]
    assert trips.get(me, tid)["post_rev"] == 2


def test_change_after_reopen_posts_r2_with_new_amount(repo):
    init(repo)
    me = boss(repo)
    trips = TripService(repo)
    tid = trips.create(me, body())["id"]
    trips.reopen(me, tid)
    # 20 km x 0,30 = 6,00 -> 56,00 + 6,00 = 62,00 EUR
    trips.update(me, tid, body(mileage=[{"date": "2026-11-09", "km": 20}]))
    r1, r1rev, r2 = rows(repo)
    assert r1rev.event_key == r1.event_key + ":rev"
    assert lines_of(r2)[0][:2] == ("travel_costs", 6200)
    assert r2.event_key == f"trip:{tid}:r2"


def test_reopen_of_locked_trip_is_409_and_changes_nothing(repo):
    init(repo)
    me = boss(repo)
    trips = TripService(repo)
    tid = trips.create(me, body())["id"]
    lock_through(repo, 11)
    with pytest.raises(ServiceError) as exc:
        trips.reopen(me, tid)
    assert exc.value.status == 409
    assert len(rows(repo)) == 1
    stored = repo.get("trips", tid)
    assert stored["status"] == "final" and stored["post_rev"] == 1


def test_trip_before_the_start_posts_nothing(repo):
    init(repo, start="2026-12-01")  # the trip ends 11 Nov, before the start
    TripService(repo).create(boss(repo), body())
    assert rows(repo) == []


def test_before_initialisation_nothing_posts(repo):
    out = TripService(repo).create(boss(repo), body())
    assert out.get("post_rev") is None
    assert repo.find("journal_entries") == []


def test_zero_amount_trip_posts_nothing(repo):
    init(repo)
    short = body(places=[], mileage=[], end="2026-11-09T12:00")
    out = TripService(repo).create(boss(repo), short)
    assert rows(repo) == [] and out.get("post_rev") is None


def test_post_rev_is_not_accepted_from_a_client(repo):
    parsed = models.TripIn.model_validate({**body(), "post_rev": 9})
    assert "post_rev" not in parsed.model_dump(exclude_unset=True)
    init(repo)
    me = boss(repo)
    trips = TripService(repo)
    tid = trips.create(me, body())["id"]
    trips.reopen(me, tid)
    out = trips.update(me, tid, {**body(), "post_rev": 7})
    assert out["post_rev"] == 2


def accountant(repo) -> dict:
    return repo.save("users", {"username": "acc1", "name": "Demo Accountant", "role": "accountant"})


def test_accountant_traveller_credits_payables_employee(repo):
    init(repo)
    acc = accountant(repo)
    # the accountant has no write right on trips (so TripService refuses): the hook is fed a final trip directly
    trip = repo.save("trips", {**body(), "user_id": acc["id"], "allowance_total": "56.00", "mileage_total": "3.00"})
    TripPostingService(repo).sync(trip, acc)
    (entry,) = rows(repo)
    assert [(ln[0], ln[1]) for ln in lines_of(entry)] == [("travel_costs", 5900), ("payables_employee", -5900)]


def test_unknown_user_credits_payables_employee(repo):
    init(repo)
    ghost = {"id": "ghost0", "username": "ghost", "role": "boss"}  # not stored: no role can be looked up
    TripService(repo).create(ghost, body())
    (entry,) = rows(repo)
    assert lines_of(entry)[1][:2] == ("payables_employee", -5900)


def test_role_change_between_posting_and_refinalise(repo):
    init(repo)
    me = boss(repo)
    trips = TripService(repo)
    tid = trips.create(me, body())["id"]
    trips.reopen(me, tid)
    repo.save("users", {**repo.get("users", me["id"]), "role": "accountant"})
    trips.update(me, tid, body())
    r1, rev, r2 = rows(repo)
    # the reversal mirrors the original (private_deposit), the new posting uses the role now
    assert [(ln[0], ln[1]) for ln in lines_of(r1)] == [("travel_costs", 5900), ("private_deposit", -5900)]
    assert [(ln[0], ln[1]) for ln in lines_of(rev)] == [("travel_costs", -5900), ("private_deposit", 5900)]
    assert [(ln[0], ln[1]) for ln in lines_of(r2)] == [("travel_costs", 5900), ("payables_employee", -5900)]


def test_trip_ending_in_a_locked_month_books_on_the_first_open_day(repo):
    init(repo)
    lock_through(repo, 11)  # November is locked; the trip ends 11 Nov
    me = boss(repo)
    out = TripService(repo).create(me, body())
    (entry,) = rows(repo)
    assert str(entry.document_date) == "2026-11-11"
    assert str(entry.booking_date) == "2026-12-01"
    assert lines_of(entry)[0] == ("travel_costs", 5900, "2026-11-11")  # open year: income_date = trip end date
    assert out["post_rev"] == 1


def test_trip_ending_in_a_closed_year_uses_the_booking_date_as_income_date(repo):
    init(repo)
    repo.save("fiscal_years", {**repo.find("fiscal_years", year=2026)[0], "closed_at": 5, "closed_by": "u1x000"})
    me = boss(repo)
    trips = TripService(repo)
    tid = trips.create(me, body())["id"]
    (entry,) = JournalService(repo).entries(2027)
    assert str(entry.document_date) == "2026-11-11" and str(entry.booking_date) == "2027-01-01"
    assert lines_of(entry)[0] == ("travel_costs", 5900, "2027-01-01")
    trips.reopen(me, tid)
    rev = [e for e in JournalService(repo).entries(2027) if e.event_key.endswith(":rev")][0]
    assert str(rev.booking_date) == "2027-01-01"
    assert lines_of(rev)[0] == ("travel_costs", -5900, "2027-01-01")


def test_before_start_trip_reopened_and_refinalised_posts_nothing(repo):
    init(repo, start="2026-12-01")
    me = boss(repo)
    trips = TripService(repo)
    tid = trips.create(me, body())["id"]
    trips.reopen(me, tid)
    out = trips.update(me, tid, body())
    assert repo.find("journal_entries") == []
    assert out.get("post_rev") is None
