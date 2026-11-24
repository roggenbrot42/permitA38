# ruff: noqa: F401, F811  (fixtures imported from test_vat_returns)
"""Amended VAT returns, drift and external returns (docs/specs/ustva.md "Status, finalise, filed, reopen, amend, drift",
accounting.md 8.1 and 16 step 5b). The figures of example U16 come from fixtures/accounting_oracle.json."""

from datetime import date
from typing import Any

import pytest

from permita38.core.errors import ServiceError
from permita38.core.service.journal import (
    JournalService,
    PostLine,  # noqa: E402
)
from permita38.core.service.locks import LockService

from .test_vat_returns import (  # noqa: F401  (fixtures)
    CASES,
    SRC,
    USER,
    clients,
    fiscal_year,
    journal,
    make_service,
    money,
    post_case,
    post_sale,
    repo,
    repo_row,
    svc,
)

U16 = CASES["U16"]
LATE_CLOCK = 1_800_000_100  # after finalised_at (1_800_000_000)


def filed_january(svc, journal) -> str:
    """January filed with the U1 lines (Kz 83 589.00 = the fixture's ``filed`` figure)."""
    post_case(journal, CASES["U1"], date(2026, 1, 15))
    d = svc.create_draft(2026, "month", 1)
    svc.finalize(d["id"], USER)
    svc.filed(d["id"], "2026-02-09", "T-1", None)
    assert svc.get(d["id"])["result_cents"] == U16["filed"]["83"]
    return d["id"]


def post_late(repo) -> None:
    late = U16["late_line"]
    j = JournalService(repo, clock=lambda: LATE_CLOCK)
    post_sale(
        j,
        "late:1",
        date.fromisoformat(late["created"]),
        date.fromisoformat(late["vat_date"]),
        late["base"],
        late["tax"],
    )


# ------------------------------------------------------------------ U16
def test_u16_drift_then_amended_return(svc, journal, repo):
    orig = filed_january(svc, journal)
    assert svc.get(orig)["drift"] == {"entries": 0, "tax_cents": 0, "tax": "0.00", "lines": []}
    post_late(repo)
    got = svc.get(orig)
    drift = got["drift"]
    assert drift["entries"] == 1 and drift["tax_cents"] == U16["drift_tax"] and drift["tax"] == "19.00"
    assert [(x["vat_date"], x["base"], x["tax"], x["booking_date"]) for x in drift["lines"]] == [
        ("2026-01-28", "100.00", "19.00", "2026-02-20")
    ]
    assert got["result_cents"] == U16["filed"]["83"] and got["status"] == "final"  # nothing moves automatically
    assert repo_row(svc, orig).get("replaced_by") is None

    am = svc.create_draft(2026, "month", 1, amends_id=orig)
    assert am["amends_id"] == orig and am["amends_key"] == orig and am["status"] == "draft"
    want = U16["amended"]
    assert am["boxes"]["10"] == {"flag": want["10"]} and want["10"] == 1
    assert am["boxes"]["81"]["base"] == money(want["81"]["base"] * 100)
    assert am["boxes"]["81"]["tax"] == money(want["81"]["tax"]) == "684.00"
    assert am["boxes"]["66"] == {"amount": money(want["66"])}
    assert am["result_cents"] == want["83"] and am["boxes"]["83"]["amount"] == "608.00"
    assert svc.list(2026)[0]["id"] == am["id"]  # the amendment is the latest return of the period

    svc.clock = lambda: LATE_CLOCK + 100  # finalised after the late entry was created
    fin = svc.finalize(am["id"], USER)
    assert fin["status"] == "final" and fin["result_cents"] == want["83"] and fin["boxes"]["10"] == {"flag": 1}
    assert repo_row(svc, orig)["replaced_by"] == am["id"]
    assert svc.get(am["id"])["drift"]["entries"] == 0
    item = svc.list(2026)[0]
    assert (item["id"], item["state"], item["result"]) == (am["id"], "final", "608.00")
    assert [(r["month"], r["reason"]) for r in LockService(repo).all()] == [(1, f"vat_return:{orig}")]
    svc.filed(am["id"], "2026-03-01", None, None)
    assert svc.list(2026)[0]["state"] == "filed"


def test_amend_unfiled_or_other_period_or_unknown_is_refused(svc, journal):
    d = svc.create_draft(2026, "month", 1)
    with pytest.raises(ServiceError) as err:  # a draft
        svc.create_draft(2026, "month", 1, amends_id=d["id"])
    assert err.value.status == 409
    svc.finalize(d["id"], USER)
    with pytest.raises(ServiceError) as err:  # final but not filed
        svc.create_draft(2026, "month", 1, amends_id=d["id"])
    assert err.value.status == 409
    svc.filed(d["id"], "2026-02-09", None, None)
    with pytest.raises(ServiceError) as err:  # another period
        svc.create_draft(2026, "month", 2, amends_id=d["id"])
    assert err.value.status == 409
    with pytest.raises(ServiceError) as err:
        svc.create_draft(2026, "month", 1, amends_id="x1y2z3")
    assert err.value.status == 404


def test_double_amend_is_409_and_reopen_frees_the_original(svc, journal):
    orig = filed_january(svc, journal)
    am = svc.create_draft(2026, "month", 1, amends_id=orig)
    with pytest.raises(ServiceError) as err:  # an amendment is in preparation
        svc.create_draft(2026, "month", 1, amends_id=orig)
    assert err.value.status == 409
    svc.finalize(am["id"], USER)
    with pytest.raises(ServiceError) as err:  # replaced
        svc.create_draft(2026, "month", 1, amends_id=orig)
    assert err.value.status == 409
    with pytest.raises(ServiceError) as err:  # still one non-amended return per period
        svc.create_draft(2026, "month", 1)
    assert err.value.status == 409
    svc.reopen(am["id"])  # an unfiled amendment can go back to draft: the original is current again
    assert repo_row(svc, orig).get("replaced_by") is None
    svc.delete(am["id"])
    assert svc.list(2026)[0]["id"] == orig
    again = svc.create_draft(2026, "month", 1, amends_id=orig)
    assert again["id"] != am["id"]


def test_a_second_amendment_amends_the_first(svc, journal):
    orig = filed_january(svc, journal)
    first = svc.create_draft(2026, "month", 1, amends_id=orig)
    svc.finalize(first["id"], USER)
    svc.filed(first["id"], "2026-03-01", None, None)
    second = svc.create_draft(2026, "month", 1, amends_id=first["id"])
    svc.finalize(second["id"], USER)
    assert repo_row(svc, first["id"])["replaced_by"] == second["id"]
    assert repo_row(svc, orig)["replaced_by"] == first["id"]
    assert svc.list(2026)[0]["id"] == second["id"]


# ------------------------------------------------------------------ external returns
def test_external_return_create_edit_delete_and_drift(svc, journal, repo):
    post_case(journal, CASES["U1"], date(2026, 1, 15))  # the journal gives Kz 83 589.00
    ext = svc.create_external(2026, "month", 1, 57000, 0, "2026-02-08", USER)  # entered 570.00
    assert ext["status"] == "final" and ext["external"] is True and ext["filed_on"] == "2026-02-08"
    assert ext["boxes"] == {"83": {"amount": "570.00"}} and ext["result_cents"] == 57000
    assert ext["finalised_by"] == USER["id"] and ext["amends_key"] == ""
    assert [(r["month"], r["reason"]) for r in LockService(repo).all()] == [(1, f"vat_return:{ext['id']}")]
    d = ext["drift"]  # journal minus entered
    assert d["tax_cents"] == 1900 and d["tax"] == "19.00" and d["entries"] == 3 and len(d["lines"]) == 3
    assert svc.list(2026)[0]["state"] == "filed" and svc.list(2026)[0]["result"] == "570.00"

    fixed = svc.update_external(ext["id"], 57400, 1500)  # typo corrected, Kz 39 entered (589.00 - 15.00 = 574.00)
    assert fixed["result_cents"] == 57400 and fixed["special_prepayment_credit_cents"] == 1500
    assert fixed["boxes"] == {"83": {"amount": "574.00"}, "39": {"amount": "15.00"}}
    assert fixed["drift"] == {"entries": 0, "tax_cents": 0, "tax": "0.00", "lines": []}
    with pytest.raises(ServiceError) as err:
        svc.create_external(2026, "month", 1, 1, 0, "2026-02-08", USER)  # the period is taken
    assert err.value.status == 409
    svc.delete(ext["id"])
    assert repo.get("vat_returns", ext["id"]) is None and len(LockService(repo).all()) == 1  # no unlock
    again = svc.create_external(2026, "month", 1, 58900, 0, "2026-02-08", USER)
    assert again["id"] != ext["id"]


def test_external_return_is_replaced_by_an_amended_return(svc, journal, repo):
    post_case(journal, CASES["U1"], date(2026, 1, 15))
    ext = svc.create_external(2026, "month", 1, 57000, 0, "2026-02-08", USER)
    am = svc.create_draft(2026, "month", 1, amends_id=ext["id"])  # an external return counts as filed
    assert am["result_cents"] == 58900 and am["boxes"]["10"] == {"flag": 1}
    for call in (lambda: svc.update_external(ext["id"], 56000, 0), lambda: svc.delete(ext["id"])):
        with pytest.raises(ServiceError) as err:  # a draft amendment points at it (also when it dangles)
            call()
        assert err.value.status == 409
    svc.delete(am["id"])
    svc.update_external(ext["id"], 57000, 0)  # editable again once nothing points at it
    am = svc.create_draft(2026, "month", 1, amends_id=ext["id"])
    svc.finalize(am["id"], USER)
    assert repo_row(svc, ext["id"])["replaced_by"] == am["id"]
    for call in (lambda: svc.update_external(ext["id"], 1, 0), lambda: svc.delete(ext["id"])):
        with pytest.raises(ServiceError) as err:
            call()
        assert err.value.status == 409
    assert svc.list(2026)[0]["id"] == am["id"]


def test_external_return_validation(svc, journal):
    with pytest.raises(ServiceError) as err:
        svc.create_external(2026, "month", 1, 100, -1, "2026-02-08", USER)
    assert err.value.status == 422
    for kz83, kz39 in ((10**13 + 1, 0), (-(10**13) - 1, 0), (0, 10**13 + 1)):
        with pytest.raises(ServiceError) as err:
            svc.create_external(2026, "month", 1, kz83, kz39, "2026-02-08", USER)
        assert err.value.status == 422
    ok = svc.create_external(2026, "month", 1, -(10**13), 10**13, "2026-02-08", USER)  # the bounds are inclusive
    for kz83, kz39 in ((10**13 + 1, 0), (0, -1), (0, 10**13 + 1)):
        with pytest.raises(ServiceError) as err:
            svc.update_external(ok["id"], kz83, kz39)
        assert err.value.status == 422
    svc.delete(ok["id"])
    with pytest.raises(ServiceError) as err:
        svc.create_external(2026, "month", 1, 100, 0, "08.02.2026", USER)
    assert err.value.status == 422
    with pytest.raises(ServiceError) as err:
        svc.create_external(2026, "quarter", 1, 100, 0, "2026-02-08", USER)  # the year files monthly
    assert err.value.status == 422
    with pytest.raises(ServiceError) as err:
        svc.create_external(2026, "month", 7, 100, 0, "2026-08-08", USER)  # July has not ended
    assert err.value.status == 422
    assert svc.list(2026)[0]["id"] is None  # nothing was written
    post_case(journal, CASES["U1"], date(2026, 1, 15))
    normal = filed_january_other(svc)
    with pytest.raises(ServiceError) as err:
        svc.update_external(normal, 1, 0)  # not an external return
    assert err.value.status == 409


def filed_january_other(svc) -> str:
    d = svc.create_draft(2026, "month", 1)
    svc.finalize(d["id"], USER)
    return str(d["id"])


def test_drift_lists_a_line_created_in_the_second_of_finalising(svc, journal, repo):
    orig = filed_january(svc, journal)  # finalised_at 1_800_000_000
    j = JournalService(repo, clock=lambda: 1_800_000_000)  # the same second
    late = U16["late_line"]
    post_sale(
        j,
        "late:1",
        date.fromisoformat(late["created"]),
        date.fromisoformat(late["vat_date"]),
        late["base"],
        late["tax"],
    )
    assert svc.get(orig)["drift"]["entries"] == 1


def test_drift_lists_lines_without_tax_effect(svc, journal, repo):
    orig = filed_january(svc, journal)
    j = JournalService(repo, clock=lambda: LATE_CLOCK)
    j.post(  # a base-only line (Kz 21): no tax effect, but it changes the boxes
        "late:2", date(2026, 2, 20), "late:2", SRC,
        [
            PostLine(role="bank", amount_cents=10000),
            PostLine(role="revenue_eu_svc", amount_cents=-10000),
            PostLine(role="revenue_eu_svc", amount_cents=0, tax_key="EU_SVC", tax_kind="base_only",
                     tax_base_cents=10000, vat_date=date(2026, 1, 28)),
        ],
    )  # fmt: skip
    got = svc.get(orig)["drift"]
    assert got["entries"] == 1 and got["tax_cents"] == 0
    assert [x["tax_kind"] for x in got["lines"]] == ["base_only"]


def test_external_december_with_kz39_has_no_drift_and_its_amendment_keeps_the_credit(repo, journal):
    svc = make_service(repo, today=date(2027, 1, 15))
    case = CASES["U10-december"]
    post_case(journal, case, date(2026, 12, 15))
    ext = svc.create_external(2026, "month", 12, 0, case["special_prepayment"], "2027-01-08", USER)
    assert ext["boxes"]["39"] == {"amount": "600.00"} and ext["result_cents"] == 0
    assert ext["drift"] == {"entries": 0, "tax_cents": 0, "tax": "0.00", "lines": []}  # journal 600.00 - credit
    wrong = svc.update_external(ext["id"], 100, case["special_prepayment"])
    assert wrong["drift"]["tax_cents"] == -100
    svc.update_external(ext["id"], 0, case["special_prepayment"])
    am = svc.create_draft(2026, "month", 12, amends_id=ext["id"])
    assert am["special_prepayment_credit_cents"] == 60000 and am["result_cents"] == 0
    assert am["boxes"]["39"] == {"amount": "600.00"} and am["boxes"]["10"] == {"flag": 1}
    fin = svc.finalize(am["id"], USER, confirm_gap=True)
    assert fin["special_prepayment_credit_cents"] == 60000 and fin["result_cents"] == 0
    assert svc.get(am["id"])["drift"]["tax_cents"] == 0


# ------------------------------------------------------------------ API and rights
def test_api_amend_drift_and_rights(clients, app: Any):
    boss, acc, emp = clients["boss"], clients["accountant"], clients["employee"]
    first = boss.post("/api/vat-returns", json={"year": 2024, "kind": "month", "no": 1}).json()["id"]
    assert boss.post(f"/api/vat-returns/{first}/finalize").status_code == 200
    amend = {"year": 2024, "kind": "month", "no": 1, "amends_id": first}
    assert boss.post("/api/vat-returns", json=amend).status_code == 409  # not filed
    assert boss.post(f"/api/vat-returns/{first}/filed", json={"filed_on": "2024-02-08"}).status_code == 200
    got = boss.get(f"/api/vat-returns/{first}").json()
    assert got["drift"]["entries"] == 0 and got["drift"]["tax"] == "0.00"
    assert emp.post("/api/vat-returns", json=amend).status_code == 403
    created = acc.post("/api/vat-returns", json=amend)
    assert created.status_code == 201, created.text
    am = created.json()
    assert am["amends_id"] == first and am["boxes"]["10"]["flag"] == 1
    assert boss.post("/api/vat-returns", json=amend).status_code == 409  # already being amended
    assert acc.post(f"/api/vat-returns/{am['id']}/finalize").status_code == 200
    assert boss.get(f"/api/vat-returns/{first}").json()["replaced_by"] == am["id"]
    assert emp.get(f"/api/vat-returns/{first}").status_code == 403
    assert boss.get("/api/vat-returns?year=2024").json()[0]["id"] == am["id"]
