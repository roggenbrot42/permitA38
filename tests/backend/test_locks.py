"""Period locks (docs/specs/accounting.md 7.8, 8.1, 11, 17-18): LockService, vat_date_for and the two routes."""

from datetime import date

import pytest

from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.locks import LockService, first_unlocked_from, vat_date_for

from .test_rbac import PASSWORD, _login

TODAY = date(2026, 6, 15)
URL = "/api/accounting/locks"


def fy(year: int) -> dict:
    return {
        "year": year, "chart": "skr03", "income_basis": "eur", "vat_basis": "soll", "vat_frequency": "month",
        "dauerfrist": False, "small_business": False, "rules_version": "r1", "opened_at": 1, "opened_by": "u1x000",
    }  # fmt: skip


@pytest.fixture
def repo(tmp_path):
    r = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    r.startup()
    r.save("fiscal_years", fy(2025))
    r.save("fiscal_years", fy(2026))
    yield r
    r.close()


@pytest.fixture
def svc(repo):
    return LockService(repo, today=lambda: TODAY, clock=lambda: 1_800_000_000)


def months(rows) -> list[tuple[int, int]]:
    return [(r["year"], r["month"]) for r in rows]


# ------------------------------------------------------------------ service
def test_lock_up_to_locks_every_month_from_the_accounting_start_in_order(svc, repo):
    new = svc.lock_up_to(2025, 3, "manual", "u1x000")
    assert months(new) == [(2025, 1), (2025, 2), (2025, 3)]
    assert all(r["locked_by"] == "u1x000" and r["reason"] == "manual" and r["locked_at"] == 1_800_000_000 for r in new)
    assert svc.locked_months(2025) == [1, 2, 3] and svc.locked_months(2026) == []
    assert svc.first_unlocked_day() == date(2025, 4, 1)
    again = svc.lock_up_to(2026, 2, "vat_return:r1", "u1x000")  # across the year boundary
    assert months(again) == [(2025, m) for m in range(4, 13)] + [(2026, 1), (2026, 2)]
    assert {r["reason"] for r in again} == {"vat_return:r1"}
    assert len(repo.list("period_locks")) == 14


def test_lock_up_to_is_idempotent(svc, repo):
    svc.lock_up_to(2025, 6, "manual", "u1x000")
    assert svc.lock_up_to(2025, 4, "manual", "u1x000") == []
    assert svc.lock_up_to(2025, 6, "manual", "u1x000") == []
    assert len(repo.list("period_locks")) == 6


def test_first_unlocked_day_without_locks_is_date_min_and_december_rolls_into_january(svc):
    assert svc.first_unlocked_day() == date.min
    svc.lock_up_to(2025, 12, "year_close", "u1x000")
    assert svc.first_unlocked_day() == date(2026, 1, 1)
    assert first_unlocked_from([]) is None


def test_a_month_that_has_not_ended_is_422(svc, repo):
    svc.lock_up_to(2026, 5, "manual", "u1x000")  # May has ended on 2026-06-15
    for ym in ((2026, 6), (2026, 7), (2027, 1)):
        with pytest.raises(ServiceError) as err:
            svc.lock_up_to(*ym, "manual", "u1x000")
        assert err.value.status == 422
    assert svc.locked_months(2026) == [1, 2, 3, 4, 5]
    last = LockService(repo, today=lambda: date(2026, 7, 1))
    assert months(last.lock_up_to(2026, 6, "manual", "u1x000")) == [(2026, 6)]  # ended on 30 June


def test_bad_month_reason_start_and_no_fiscal_year(svc, repo, tmp_path):
    for bad in (0, 13):
        with pytest.raises(ServiceError) as err:
            svc.lock_up_to(2025, bad, "manual", "u1x000")
        assert err.value.status == 422
    with pytest.raises(ServiceError) as err:
        svc.lock_up_to(2024, 12, "manual", "u1x000")  # before the accounting start (January 2025)
    assert err.value.status == 422
    with pytest.raises(ServiceError) as err:
        svc.lock_up_to(2025, 1, "because", "u1x000")
    assert err.value.status == 422
    empty = create_repository(Config(data_dir=tmp_path / "other", secret_key="x" * 16))
    empty.startup()
    with pytest.raises(ServiceError) as err:
        LockService(empty, today=lambda: TODAY).lock_up_to(2025, 1, "manual", "u1x000")
    assert err.value.status == 409
    empty.close()
    assert repo.list("period_locks") == []


def test_preview_lists_the_gap_without_writing(svc, repo):
    svc.lock_up_to(2025, 2, "manual", "u1x000")
    assert svc.preview_lock_up_to(2025, 5) == [(2025, 3), (2025, 4), (2025, 5)]
    assert svc.preview_lock_up_to(2025, 2) == []
    assert len(repo.list("period_locks")) == 2
    # a vat return for 2026-01 would also lock the unlocked months between
    assert svc.preview_lock_up_to(2026, 1)[0] == (2025, 3) and svc.preview_lock_up_to(2026, 1)[-1] == (2026, 1)
    with pytest.raises(ServiceError):
        svc.preview_lock_up_to(2026, 6)


def test_locks_cannot_be_updated_or_deleted(svc, repo):
    row = svc.lock_up_to(2025, 1, "manual", "u1x000")[0]
    with pytest.raises(Exception, match="(?i)append|refus|not allowed"):
        repo.save("period_locks", {**row, "reason": "year_close"})
    with pytest.raises(Exception, match="(?i)append|refus|not allowed"):
        repo.delete("period_locks", row["id"])
    assert len(repo.list("period_locks")) == 1


# ------------------------------------------------------------------ vat_date_for
FIRST = date(2026, 2, 1)


@pytest.mark.parametrize("kind", ["soll_issue", "cash_event", "rc_tax_date", "eu_service"])
def test_drift_kinds_keep_a_locked_date_and_flag_it(kind):
    assert vat_date_for(kind, date(2026, 1, 28), FIRST) == (date(2026, 1, 28), True)
    assert vat_date_for(kind, date(2026, 1, 31), FIRST) == (date(2026, 1, 31), True)
    assert vat_date_for(kind, date(2026, 2, 1), FIRST) == (date(2026, 2, 1), False)  # open month: no drift
    assert vat_date_for(kind, date(2026, 3, 9), FIRST) == (date(2026, 3, 9), False)


@pytest.mark.parametrize("kind", ["input_vat", "credit_note", "write_off", "other"])
def test_non_drift_kinds_move_to_the_first_unlocked_day(kind):
    assert vat_date_for(kind, date(2026, 1, 28), FIRST) == (FIRST, False)
    assert vat_date_for(kind, date(2026, 2, 1), FIRST) == (FIRST, False)
    assert vat_date_for(kind, date(2026, 2, 20), FIRST) == (date(2026, 2, 20), False)


def test_year_boundary_december_locked_means_january_is_the_first_open_day():
    jan = date(2027, 1, 1)
    assert vat_date_for("input_vat", date(2026, 12, 20), jan) == (jan, False)
    assert vat_date_for("soll_issue", date(2026, 12, 20), jan) == (date(2026, 12, 20), True)
    assert vat_date_for("other", date(2027, 1, 1), jan) == (jan, False)
    assert vat_date_for("cash_event", date(2026, 12, 31), jan) == (date(2026, 12, 31), True)


def test_manual_vat_date_in_a_locked_month_is_422_and_otherwise_kept():
    with pytest.raises(ServiceError) as err:
        vat_date_for("manual", date(2026, 1, 31), FIRST)
    assert err.value.status == 422
    assert vat_date_for("manual", date(2026, 2, 1), FIRST) == (date(2026, 2, 1), False)
    assert vat_date_for("manual", date(2026, 1, 31), date.min) == (date(2026, 1, 31), False)  # nothing locked


def test_unknown_kind_is_422():
    with pytest.raises(ServiceError) as err:
        vat_date_for("whatever", date(2026, 1, 1), FIRST)
    assert err.value.status == 422


# ------------------------------------------------------------------ API
@pytest.fixture
def clients(app, boss_client):
    out = {"boss": boss_client}
    for role in ("accountant", "employee"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        out[role] = _login(app, role[:3] + "x")
    return out


def test_api_lock_and_list_as_boss_and_accountant(app, clients):
    boss, acc, emp = clients["boss"], clients["accountant"], clients["employee"]
    assert (
        boss.post("/api/accounting/years", json={"year": 2024, "chart": "skr03", "income_basis": "eur"}).status_code
        == 201
    )
    assert boss.get(URL).json() == []
    r = boss.post(URL, json={"year": 2024, "month": 3})
    assert r.status_code == 201, r.text
    assert [(x["year"], x["month"], x["reason"]) for x in r.json()] == [(2024, m, "manual") for m in (1, 2, 3)]
    assert all(x["locked_by"] for x in r.json())
    r2 = acc.post(URL, json={"year": 2024, "month": 4})
    assert r2.status_code == 201 and [x["month"] for x in r2.json()] == [4]
    assert acc.post(URL, json={"year": 2024, "month": 4}).json() == []  # idempotent
    assert [x["month"] for x in acc.get(URL).json()] == [1, 2, 3, 4]
    assert boss.post(URL, json={"year": 2999, "month": 1}).status_code == 422  # the future
    assert boss.post(URL, json={"year": 2024, "month": 13}).status_code == 422
    assert emp.get(URL).status_code == 403
    assert emp.post(URL, json={"year": 2024, "month": 5}).status_code == 403
    assert len(app.state.services.store.list("period_locks")) == 4


@pytest.mark.parametrize("method", ["put", "patch", "delete"])
def test_there_is_no_unlock_route(clients, method):
    boss = clients["boss"]
    for path in (URL, f"{URL}/1", f"{URL}/2024/1"):
        assert getattr(boss, method)(path).status_code in (404, 405)
