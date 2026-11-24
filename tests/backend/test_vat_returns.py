"""VAT return records (docs/specs/ustva.md "Status, finalise, filed, reopen", accounting.md 8.1, 11): VatReturnService
and its routes. The figures of the oracle cases U1-U3 come from fixtures/accounting_oracle.json."""

import hashlib
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from permita38.core import charts
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.locks import LockService
from permita38.core.service.vat_returns import VatReturnListItem, VatReturnService, money

from .test_rbac import PASSWORD, _login

FIXTURES = Path(__file__).parent / "fixtures"
ORACLE = json.loads((FIXTURES / "accounting_oracle.json").read_text(encoding="utf-8"))
CASES = {c["id"]: c for c in ORACLE["ustva_cases"]}
TODAY = date(2026, 6, 15)
USER = {"id": "u1x000"}


def fiscal_year(year: int, **extra: Any) -> dict[str, Any]:
    return {
        "year": year, "chart": "skr03", "income_basis": "eur", "vat_basis": "soll", "vat_frequency": "month",
        "dauerfrist": False, "small_business": False, "rules_version": "skr03-2026/vat-2026", "opened_at": 1,
        "opened_by": "u1x000", **extra,
    }  # fmt: skip


@pytest.fixture
def repo(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURES / "chart_synthetic.json", charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    r = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    r.startup()
    r.save("fiscal_years", fiscal_year(2026))
    yield r
    r.close()


@pytest.fixture
def journal(repo):
    return JournalService(repo, clock=lambda: 1_800_000_000)


def make_service(repo, today: date = TODAY) -> VatReturnService:
    return VatReturnService(
        repo, LockService(repo, today=lambda: today), today=lambda: today, clock=lambda: 1_800_000_000
    )


@pytest.fixture
def svc(repo):
    return make_service(repo)


SRC = JournalSource(
    event="issue", created_by="u1x000", source_coll="invoices", source_id="inv1abc", source_number="R-1"
)
OUT_ROLE = {"S19": ("revenue_s19", "vat_out_s19", "19"), "S7": ("revenue_s7", "vat_out_s7", "7")}


def post_case(journal: JournalService, case: dict[str, Any], day: date) -> None:
    """Post every oracle line as its own balanced entry (bank against the sale or the expense)."""
    for i, s in enumerate(case["lines"]):
        if "input" in s:
            lines = [
                PostLine(role="expense_other", amount_cents=10000),
                PostLine(role="input_vat_s19", amount_cents=s["input"], tax_key="VST", tax_kind="input", vat_date=day),
                PostLine(role="bank", amount_cents=-(10000 + s["input"])),
            ]
        else:
            revenue, vat_out, rate = OUT_ROLE[s["tax_key"]]
            lines = [
                PostLine(role="bank", amount_cents=s["base"] + s["tax"]),
                PostLine(role=revenue, amount_cents=-s["base"]),
                PostLine(
                    role=vat_out, amount_cents=-s["tax"], tax_key=s["tax_key"], tax_kind="tax", tax_rate=rate,
                    tax_base_cents=s["base"], vat_date=day,
                ),
            ]  # fmt: skip
        journal.post(f"{case['id']}:{i}", day, f"{case['id']} line {i}", SRC, lines)


def check_boxes(row: dict[str, Any], case: dict[str, Any]) -> None:
    for kz, want in case["boxes"].items():
        if kz == "83":
            assert row["result_cents"] == want
            assert row["boxes"]["83"]["amount"] == money(want)
        elif isinstance(want, dict):
            box = row["boxes"][kz]
            assert box["base"] == money(want["base"] * 100), kz
            assert box["tax"] == money(want["tax"]), kz
            if "booked_base" in want:
                assert box["booked_base"] == money(want["booked_base"]), kz
            if "booked_tax" in want:
                assert box["booked_tax"] == money(want["booked_tax"]), kz
        else:
            assert row["boxes"][kz] == {"amount": money(want)}, kz
    if "rounding_difference" in case:
        assert row["rounding_difference_cents"] == case["rounding_difference"]


# ------------------------------------------------------------------ oracle
@pytest.mark.parametrize("cid", ["U1", "U2", "U3"])
def test_stored_figures_equal_the_oracle(svc, journal, repo, cid):
    case = CASES[cid]
    year, month = int(case["period"][:4]), int(case["period"][5:])
    post_case(journal, case, date(year, month, 15))
    check_boxes(svc.preview(year, "month", month), case)
    draft = svc.create_draft(year, "month", month)
    check_boxes(draft, case)  # a draft is computed live
    assert draft["status"] == "draft" and draft.get("finalised_at") is None
    final = svc.finalize(draft["id"], USER, confirm_gap=True)
    stored = repo.get("vat_returns", draft["id"])
    assert stored is not None and stored["status"] == "final"
    check_boxes(stored, case)
    check_boxes(final, case)
    assert stored["finalised_by"] == "u1x000" and stored["finalised_at"] == 1_800_000_000
    assert stored["rules_version"] == "skr03-2026/vat-2026" and stored["basis"] == "soll"
    assert stored["frequency"] == "month" and stored["dauerfrist"] is False and stored["small_business"] is False
    assert len(stored["source_lines"]) == len(case["lines"])
    # a later journal line does not change a final return
    late = {"id": "late", "lines": [{"tax_key": "S19", "base": 100000, "tax": 19000}]}
    post_case(journal, late, date(year, month, 20))
    assert svc.get(draft["id"])["boxes"] == stored["boxes"]


# ------------------------------------------------------------------ list, preview, create
def test_list_shows_periods_with_due_date_and_state(svc):
    rows = svc.list(2026)
    assert [r["no"] for r in rows] == list(range(1, 13)) and {r["kind"] for r in rows} == {"month"}
    jan, feb = rows[0], rows[1]
    assert (jan["from_date"], jan["to_date"], jan["due_date"], jan["id"]) == (
        "2026-01-01", "2026-01-31", "2026-02-10", None
    )  # fmt: skip
    assert feb["due_date"] == "2026-03-10"
    assert jan["state"] == "overdue" and rows[4]["state"] == "overdue"  # May was due on 10 Jun
    assert rows[5]["state"] == "open"  # June is due on 10 Jul


def test_list_states_follow_the_record(svc):
    d = svc.create_draft(2026, "month", 1)
    assert svc.list(2026)[0]["id"] == d["id"] and svc.list(2026)[0]["state"] == "overdue"
    svc.finalize(d["id"], USER)
    row = svc.list(2026)[0]
    assert (row["state"], row["result"], row["lines"]) == ("final", "0.00", 0)
    svc.filed(d["id"], "2026-02-09", "ref", None)
    assert svc.list(2026)[0]["state"] == "filed"


def test_due_soon_window(repo):
    states = {r["no"]: r["state"] for r in make_service(repo, date(2026, 7, 4)).list(2026)}
    assert states[5] == "overdue" and states[6] == "due soon" and states[7] == "open"  # June is due on 10 Jul


def test_preview_and_create_validate_the_period(svc, repo):
    for args in ((2026, "quarter", 1), (2026, "month", 13), (2026, "week", 1), (2030, "month", 1)):
        with pytest.raises(ServiceError) as err:
            svc.preview(*args)
        assert err.value.status == 422, args
    repo.save("fiscal_years", fiscal_year(2027, vat_frequency="none"))
    with pytest.raises(ServiceError) as err:
        svc.create_draft(2027, "month", 1)
    assert err.value.status == 422 and svc.list(2027) == []
    with pytest.raises(ServiceError) as err:
        svc.create_draft(2026, "month", 1, amends_id="x1y2z3")
    assert err.value.status == 404


def test_duplicate_period_is_409_and_a_deleted_draft_frees_it(svc):
    first = svc.create_draft(2026, "month", 2)
    with pytest.raises(ServiceError) as err:
        svc.create_draft(2026, "month", 2)
    assert err.value.status == 409
    svc.delete(first["id"])
    assert svc.create_draft(2026, "month", 2)["id"] != first["id"]
    with pytest.raises(ServiceError) as err:
        svc.get(first["id"])
    assert err.value.status == 404


def test_small_business_without_13b_lines_has_no_return(svc, repo):
    repo.save("fiscal_years", fiscal_year(2025, small_business=True))
    rows = svc.list(2025)
    assert [r["kind"] for r in rows] == ["quarter"] * 4 and {r["state"] for r in rows} == {"not_required"}
    for call in (lambda: svc.preview(2025, "quarter", 2), lambda: svc.create_draft(2025, "quarter", 2)):
        with pytest.raises(ServiceError) as err:
            call()
        assert err.value.status == 422
    with pytest.raises(ServiceError):
        svc.preview(2025, "month", 2)


# ------------------------------------------------------------------ finalise, locks, reopen, filed, delete
def test_finalize_locks_the_months_with_the_return_reason(svc, repo):
    d = svc.create_draft(2026, "month", 1)
    svc.finalize(d["id"], USER)
    locks = repo.list("period_locks")
    assert [(r["year"], r["month"], r["reason"], r["locked_by"]) for r in locks] == [
        (2026, 1, f"vat_return:{d['id']}", "u1x000")
    ]
    with pytest.raises(ServiceError) as err:
        svc.finalize(d["id"], USER)  # finalise twice
    assert err.value.status == 409


def test_gap_is_409_with_the_months_then_confirmed(svc, repo):
    d = svc.create_draft(2026, "month", 3)
    with pytest.raises(ServiceError) as err:
        svc.finalize(d["id"], USER)
    assert err.value.status == 409 and err.value.body == {"months": ["2026-01", "2026-02"]}
    assert repo.list("period_locks") == [] and svc.get(d["id"])["status"] == "draft"  # nothing changed
    svc.finalize(d["id"], USER, confirm_gap=True)
    assert [(r["month"], r["reason"]) for r in LockService(repo).all()] == [
        (m, f"vat_return:{d['id']}") for m in (1, 2, 3)
    ]


def test_quarter_locks_its_three_months_and_a_period_that_has_not_ended_is_422(svc, repo):
    repo.save("fiscal_years", fiscal_year(2025, vat_frequency="quarter"))
    q = svc.create_draft(2025, "quarter", 1)
    svc.finalize(q["id"], USER)
    assert [(r["year"], r["month"]) for r in LockService(repo).all()] == [(2025, 1), (2025, 2), (2025, 3)]
    june = svc.create_draft(2026, "month", 6)  # ends on 30 June, TODAY is 15 June
    with pytest.raises(ServiceError) as err:
        svc.finalize(june["id"], USER, confirm_gap=True)
    assert err.value.status == 422
    assert svc.get(june["id"])["status"] == "draft" and len(repo.list("period_locks")) == 3  # rolled back


def test_reopen_returns_to_draft_and_the_months_stay_locked(svc, repo):
    d = svc.create_draft(2026, "month", 1)
    svc.finalize(d["id"], USER)
    with pytest.raises(ServiceError) as err:
        svc.reopen(svc.create_draft(2026, "month", 2)["id"])  # a draft
    assert err.value.status == 409
    back = svc.reopen(d["id"])
    assert back["status"] == "draft" and back.get("finalised_at") is None and back.get("finalised_by") is None
    row = repo.get("vat_returns", d["id"])
    assert row is not None and row["status"] == "draft" and not row["boxes"] and row.get("source_lines") is None
    assert row.get("result_cents") is None
    assert row.get("special_prepayment_credit_cents") is None and row.get("rounding_difference_cents") is None
    assert [r["month"] for r in repo.list("period_locks")] == [1]
    again = svc.finalize(d["id"], USER)  # the months are locked already: no new lock rows
    assert again["status"] == "final" and len(repo.list("period_locks")) == 1


def test_filed_needs_final_and_blocks_reopen_and_delete(svc, repo):
    d = svc.create_draft(2026, "month", 1)
    with pytest.raises(ServiceError) as err:
        svc.filed(d["id"], "2026-02-09", None, None)
    assert err.value.status == 409
    svc.finalize(d["id"], USER)
    done = svc.filed(d["id"], "2026-02-09", "TICKET-1", "note")
    assert (done["filed_on"], done["reference"], done["note"]) == ("2026-02-09", "TICKET-1", "note")
    for call in (
        lambda: svc.reopen(d["id"]),
        lambda: svc.delete(d["id"]),
        lambda: svc.filed(d["id"], "2026-02-10", None, None),
    ):
        with pytest.raises(ServiceError) as err:
            call()
        assert err.value.status == 409
    stored = repo.get("vat_returns", d["id"])
    assert stored is not None and stored["filed_on"] == "2026-02-09"


def test_a_final_return_cannot_be_deleted(svc, repo):
    d = svc.create_draft(2026, "month", 1)
    svc.finalize(d["id"], USER)
    with pytest.raises(ServiceError) as err:
        svc.delete(d["id"])
    assert err.value.status == 409 and repo.get("vat_returns", d["id"]) is not None


# ------------------------------------------------------------------ drill-down
def test_drill_down_lists_the_lines_behind_a_box(svc, journal):
    post_case(journal, CASES["U2"], date(2026, 2, 15))
    d = svc.create_draft(2026, "month", 2)
    box81 = svc.lines(d["id"], "81")
    assert box81["kz"] == "81" and len(box81["lines"]) == 1
    ln = box81["lines"][0]
    assert (ln["entry_no"], ln["booking_date"], ln["document"], ln["base"], ln["tax"]) == (
        1, "2026-02-15", "R-1", "2000.00", "380.00"
    )  # fmt: skip
    assert [x["tax"] for x in svc.lines(d["id"], "66")["lines"]] == ["19.00", "7.00"]
    assert svc.lines(d["id"], "83")["lines"] == []
    with pytest.raises(ServiceError) as err:
        svc.lines(d["id"], "99")
    assert err.value.status == 404
    svc.finalize(d["id"], USER, confirm_gap=True)
    post_case(journal, {"id": "late", "lines": [{"tax_key": "S19", "base": 5000, "tax": 950}]}, date(2026, 2, 20))
    assert len(svc.lines(d["id"], "81")["lines"]) == 1  # a final return shows the lines it stored


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
    year = boss_client.post("/api/accounting/years", json={"year": 2024, "chart": "skr03", "income_basis": "eur"})
    assert year.status_code == 201, year.text
    return out


def test_api_cycle_and_rights(clients):
    boss, acc, emp = clients["boss"], clients["accountant"], clients["employee"]
    assert boss.get("/api/vat-returns?year=2024").json()[0]["state"] == "overdue"
    assert boss.get("/api/vat-returns/preview?year=2024&kind=month&no=1").json()["result"] == "0.00"
    created = acc.post("/api/vat-returns", json={"year": 2024, "kind": "month", "no": 2})
    assert created.status_code == 201, created.text
    rid = created.json()["id"]
    assert boss.post("/api/vat-returns", json={"year": 2024, "kind": "month", "no": 2}).status_code == 409
    gap = boss.post(f"/api/vat-returns/{rid}/finalize")
    assert gap.status_code == 409 and gap.json()["months"] == ["2024-01"]
    fin = acc.post(f"/api/vat-returns/{rid}/finalize?confirm_gap=true")
    assert fin.status_code == 200 and fin.json()["status"] == "final", fin.text
    locks = boss.get("/api/accounting/locks").json()
    assert [(x["month"], x["reason"]) for x in locks] == [(1, f"vat_return:{rid}"), (2, f"vat_return:{rid}")]
    assert boss.get(f"/api/vat-returns/{rid}/boxes/83").json() == {"kz": "83", "lines": []}
    assert boss.delete(f"/api/vat-returns/{rid}").status_code == 409
    assert boss.post(f"/api/vat-returns/{rid}/reopen").json()["status"] == "draft"
    assert boss.post(f"/api/vat-returns/{rid}/finalize").status_code == 200
    filed = boss.post(f"/api/vat-returns/{rid}/filed", json={"filed_on": "2024-03-08", "reference": "T-1"})
    assert filed.status_code == 200 and filed.json()["filed_on"] == "2024-03-08"
    assert boss.post(f"/api/vat-returns/{rid}/reopen").status_code == 409
    assert emp.get("/api/vat-returns?year=2024").status_code == 403
    assert emp.post("/api/vat-returns", json={"year": 2024, "kind": "month", "no": 3}).status_code == 403
    assert emp.get(f"/api/vat-returns/{rid}").status_code == 403
    assert emp.delete(f"/api/vat-returns/{rid}").status_code == 403
    d3 = boss.post("/api/vat-returns", json={"year": 2024, "kind": "month", "no": 3}).json()["id"]
    assert acc.delete(f"/api/vat-returns/{d3}").status_code == 204
    assert boss.get(f"/api/vat-returns/{d3}").status_code == 404


# ------------------------------------------------------------------ review fixes
def post_sale(
    journal: JournalService, key: str, booking: date, vat_day: date, base: int = 10000, tax: int = 1900
) -> None:
    journal.post(
        key, booking, key, SRC,
        [
            PostLine(role="bank", amount_cents=base + tax),
            PostLine(role="revenue_s19", amount_cents=-base),
            PostLine(
                role="vat_out_s19", amount_cents=-tax, tax_key="S19", tax_kind="tax", tax_rate="19",
                tax_base_cents=base, vat_date=vat_day,
            ),
        ],
    )  # fmt: skip


def repo_row(svc: VatReturnService, rid: str) -> dict[str, Any]:
    row = svc.store.get("vat_returns", rid)
    assert row is not None
    return row


def test_the_vat_date_not_the_booking_date_decides_the_period(svc, journal):
    post_sale(
        journal, "soll:1", date(2026, 3, 5), date(2026, 2, 20)
    )  # a Soll invoice issued in March, performed in Feb
    feb, mar = svc.preview(2026, "month", 2), svc.preview(2026, "month", 3)
    assert feb["boxes"]["81"]["base"] == "100.00" and feb["boxes"]["81"]["tax"] == "19.00"
    assert "81" not in mar["boxes"] and mar["source_lines"] == []
    assert len(feb["source_lines"]) == 1


def test_a_final_return_with_no_lines_shows_no_later_lines(svc, journal):
    d = svc.create_draft(2026, "month", 2)
    svc.finalize(d["id"], USER, confirm_gap=True)
    assert repo_row(svc, d["id"])["source_lines"] == []
    post_sale(journal, "late:1", date(2026, 3, 5), date(2026, 2, 20))  # drift: not part of the final return
    assert svc.lines(d["id"], "81")["lines"] == []
    assert "81" not in svc.get(d["id"])["boxes"]


def test_a_small_business_period_shows_no_input_lines(svc, repo, journal):
    repo.save("fiscal_years", fiscal_year(2025, small_business=True, vat_frequency="none"))
    day = date(2025, 2, 10)
    journal.post(
        "rc:1", day, "reverse charge", SRC,
        [
            PostLine(role="expense_other", amount_cents=2100),
            PostLine(
                role="rc_input_vat", amount_cents=399, tax_key="RC_3C_SVC", tax_kind="input", tax_rate="19",
                tax_base_cents=2100, vat_date=day,
            ),
            PostLine(
                role="rc_vat_out_3c", amount_cents=-399, tax_key="RC_3C_SVC", tax_kind="tax", tax_rate="19",
                tax_base_cents=2100, vat_date=day,
            ),
            PostLine(role="bank", amount_cents=-2100),
        ],
    )  # fmt: skip
    d = svc.create_draft(2025, "quarter", 1)
    assert len(d["source_lines"]) == 1  # the input line is excluded, as in the computation
    assert len(svc.lines(d["id"], "85")["lines"]) == 1
    assert svc.lines(d["id"], "67")["lines"] == []


def test_source_line_limit_stores_sums_and_a_hash_never_a_silent_null(svc, journal, repo, monkeypatch):
    monkeypatch.setattr("permita38.core.service.vat_returns.MAX_SOURCE_LINES", 2)
    post_case(journal, CASES["U2"], date(2026, 2, 15))  # four source lines
    all_ids = sorted(ln["id"] for ln in repo.journal_lines_by_vat_date("2026-02-01", "2026-02-28") if ln.get("tax_key"))
    want = hashlib.sha256("\n".join(all_ids).encode()).hexdigest()
    assert len(all_ids) == 4
    draft = svc.preview(2026, "month", 2)  # a preview says it is cut
    assert (draft["source_lines_truncated"], draft["source_lines_count"], draft["source_lines_hash"]) == (True, 4, want)
    assert len(draft["source_lines"]) == 2
    d = svc.create_draft(2026, "month", 2)
    fin = svc.finalize(d["id"], USER, confirm_gap=True)
    assert repo_row(svc, d["id"])["source_lines"] == {"truncated": True, "count": 4, "sha256": want}
    assert fin["source_lines"] == [] and fin["source_lines_truncated"] is True and fin["source_lines_hash"] == want
    assert svc.get(d["id"])["source_lines_count"] == 4
    assert next(r for r in svc.list(2026) if r["no"] == 2)["lines"] == 4
    assert len(svc.lines(d["id"], "81")["lines"]) == 1  # the drill-down queries the journal by period
    check_boxes(svc.get(d["id"]), CASES["U2"])  # the per-box sums are stored
    late = JournalService(repo, clock=lambda: 1_800_000_100)  # created after finalised_at: drift, not source
    post_case(late, CASES["U1"], date(2026, 2, 16))
    assert len(svc.lines(d["id"], "81")["lines"]) == 1
    assert svc.reopen(d["id"])["source_lines_truncated"] is True  # a draft shows the cut again
    assert repo_row(svc, d["id"]).get("source_lines") is None


def test_finalize_stores_the_rules_actually_used_and_refreshes_the_due_date(svc, repo):
    d = svc.create_draft(2026, "month", 1)
    repo.save("vat_returns", {**repo_row(svc, d["id"]), "due_date": "2026-02-11", "rules_version": "old/vat-1999"})
    fin = svc.finalize(d["id"], USER)
    assert fin["rules_version"] == "skr03-2026/vat-2026" and fin["due_date"] == "2026-02-10"


def test_finalize_is_atomic_when_the_lock_fails(svc, repo, monkeypatch):
    d = svc.create_draft(2026, "month", 1)
    real = svc.locks.lock_up_to

    def lock_then_fail(*args: Any, **kwargs: Any) -> Any:
        real(*args, **kwargs)  # the lock rows are written, then the call fails
        raise RuntimeError("boom")

    monkeypatch.setattr(svc.locks, "lock_up_to", lock_then_fail)
    with pytest.raises(RuntimeError):
        svc.finalize(d["id"], USER)
    row = repo_row(svc, d["id"])
    assert row["status"] == "draft" and not row.get("boxes") and row.get("finalised_at") is None
    assert repo.list("period_locks") == []


def test_reopen_after_filing_is_a_service_409_not_a_database_guard(svc):
    d = svc.create_draft(2026, "month", 1)
    svc.finalize(d["id"], USER)
    svc.filed(d["id"], "2026-02-09", None, None)
    with pytest.raises(ServiceError) as err:
        svc.reopen(d["id"])
    assert err.value.status == 409 and "amended return" in err.value.message


def test_list_rows_are_typed(svc):
    assert all(VatReturnListItem(**r) for r in svc.list(2026))
