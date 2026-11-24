# ruff: noqa: F811
"""Manual entries and their reversal (docs/specs/accounting.md 7, 9.8, 11, 14.5, 17, 18), synthetic chart."""

import shutil

import pytest
from fastapi.testclient import TestClient

from permita38.core import charts, models
from permita38.core.errors import ServiceError
from permita38.core.service.journal import JournalService
from permita38.core.service.manual_entries import ManualEntryService

from .test_journal_service import BANK, FIXTURE, REVENUE, VAT_OUT, lock, post, repo, svc  # noqa: F401  (fixtures, helpers)
from .test_rbac import clients  # noqa: F401  (fixture)

USER = {"id": "u1x000", "role": "boss"}


@pytest.fixture
def manual(repo):
    return ManualEntryService(repo, JournalService(repo, clock=lambda: 1_800_000_000))


def entry_body(**over):
    body = {
        "date": "2026-03-10",
        "text": "Private deposit",
        "lines": [{"account": BANK, "amount": "100.00"}, {"account": REVENUE, "amount": -100}],
    }
    body.update(over)
    return models.ManualEntryIn.model_validate(body)


def tax_body(**over):
    tax = {
        "account": VAT_OUT, "amount": "-19.00", "tax_key": "S19", "tax_kind": "tax", "tax_rate": "19",
        "tax_base": "-100.00", "vat_date": "2026-03-10",
    }  # fmt: skip
    tax.update(over)
    return entry_body(lines=[{"account": BANK, "amount": "119.00"}, {"account": REVENUE, "amount": "-100.00"}, tax])


def test_valid_entry_amounts_as_strings_and_numbers(manual):
    e = manual.post(entry_body(), USER)
    assert e.origin == "manual" and e.event == "manual_entry" and e.entry_no == 1
    assert e.event_key is not None and e.event_key.startswith("manual:") and e.created_by == "u1x000"
    assert [ln.amount for ln in e.lines] == [100, -100]
    other = manual.post(entry_body(), USER)
    assert other.event_key != e.event_key and other.entry_no == 2


def test_unbalanced_and_short_entries_are_refused_by_the_model():
    with pytest.raises(ValueError):
        entry_body(lines=[{"account": BANK, "amount": "1.00"}, {"account": REVENUE, "amount": "-2.00"}])
    with pytest.raises(ValueError):
        entry_body(lines=[{"account": BANK, "amount": "1.00"}])


def test_text_is_required_and_bounded():
    for bad in ("", "  ", "x" * 201):
        with pytest.raises(ValueError):
            entry_body(text=bad)


def test_unknown_account_is_422(manual):
    body = entry_body(lines=[{"account": "no-such", "amount": "5.00"}, {"account": REVENUE, "amount": "-5.00"}])
    with pytest.raises(ServiceError) as err:
        manual.post(body, USER)
    assert err.value.status == 422


def test_tax_line_rules():
    for bad in ({"tax_kind": None}, {"vat_date": None}, {"tax_base": None}):
        with pytest.raises(ValueError):
            tax_body(**bad)
    with pytest.raises(ValueError):  # tax fields without a tax key
        tax_body(tax_key=None)


def test_valid_tax_line_is_stored(manual):
    tax = manual.post(tax_body(), USER).lines[2]
    assert (tax.tax_key, tax.tax_kind, tax.tax_rate, tax.vat_date) == ("S19", "tax", "19", "2026-03-10")
    assert (tax.tax_base, tax.amount) == (-100, -19)


def test_vat_date_in_a_locked_month_is_422_but_a_late_booking_date_is_fine(manual, repo):
    lock(repo, 2026, 3)
    with pytest.raises(ServiceError) as err:
        manual.post(tax_body(), USER)  # vat_date 2026-03-10 is locked
    assert err.value.status == 422
    e = manual.post(tax_body(vat_date="2026-04-02"), USER)
    assert e.booking_date == "2026-04-01"  # document 03-10 books in the first open month


def test_accrual_income_date_outside_the_year_is_422_eur_allowed(manual, repo):
    lines = [
        {"account": BANK, "amount": "10.00"},
        {"account": REVENUE, "amount": "-10.00", "income_date": "2025-12-31"},
    ]
    manual.post(entry_body(lines=lines), USER)  # EÜR year: allowed
    year = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**year, "income_basis": "accrual"})
    with pytest.raises(ServiceError) as err:
        manual.post(entry_body(lines=lines), USER)
    assert err.value.status == 422 and "income_date" in err.value.message
    ok = [lines[0], {**lines[1], "income_date": "2026-12-31"}]
    assert manual.post(entry_body(lines=ok), USER).fiscal_year == 2026


def test_reverse_mirrors_lines_and_uses_the_rev_key(manual, repo):
    orig = manual.post(tax_body(), USER)
    rev = manual.reverse(orig.id, None, None, USER)
    assert rev.event_key == orig.event_key + ":rev" and rev.reverses_id == orig.id and rev.event == "manual_reversal"
    assert rev.text == "Reversal: Private deposit" and rev.booking_date == "2026-03-10"
    assert [(ln.account, ln.amount) for ln in rev.lines] == [(ln.account, -ln.amount) for ln in orig.lines]
    tax = rev.lines[2]
    assert (tax.tax_key, tax.tax_kind, tax.tax_base, tax.vat_date) == ("S19", "tax", 100, "2026-03-10")
    stored = repo.get_journal_entry_by_event_key(orig.event_key + ":rev")
    assert stored is not None and sum(ln["amount_cents"] for ln in stored["lines"]) == 0
    assert JournalService(repo).verify_chain(2026) == []


def test_reverse_with_date_and_text_books_on_the_first_open_day(manual, repo):
    orig = manual.post(entry_body(), USER)
    lock(repo, 2026, 1)
    lock(repo, 2026, 2)  # the original (03-10) stays reversible
    rev = manual.reverse(orig.id, "2026-01-05", "Wrong account", USER)
    assert (rev.text, rev.document_date, rev.booking_date) == ("Wrong account", "2026-01-05", "2026-03-01")


def test_reverse_twice_and_reversing_a_reversal_are_409(manual):
    orig = manual.post(entry_body(), USER)
    rev = manual.reverse(orig.id, None, None, USER)
    for target in (orig.id, rev.id):
        with pytest.raises(ServiceError) as err:
            manual.reverse(target, None, None, USER)
        assert err.value.status == 409


def test_reverse_in_a_locked_month_is_409(manual, repo):
    orig = manual.post(entry_body(), USER)
    for m in (1, 2, 3):
        lock(repo, 2026, m)
    with pytest.raises(ServiceError) as err:
        manual.reverse(orig.id, None, None, USER)
    assert err.value.status == 409


def test_automatic_entries_are_not_reversed_here_and_unknown_is_404(manual, svc):
    auto = post(svc)
    with pytest.raises(ServiceError) as err:
        manual.reverse(auto.id, None, None, USER)
    assert (err.value.status, err.value.message) == (409, "Reverse through the source document")
    with pytest.raises(ServiceError) as err:
        manual.reverse("nope", None, None, USER)
    assert err.value.status == 404


def test_templates_are_service_data(manual):
    assert [t["key"] for t in manual.templates()] == [
        "opening_balance", "private_withdrawal", "private_deposit", "vat_payment", "income_taxes", "depreciation",
        "bank_fees",
    ]  # fmt: skip


# ------------------------------------------------------------------ API
BODY = {
    "date": "2026-01-05",
    "text": "Demo",
    "lines": [{"account": "1200", "amount": "10.00"}, {"account": "8400", "amount": "-10.00"}],
}


def test_rights_per_role_on_the_routes(clients):
    for role, c in clients.items():
        r = c.post("/api/accounting/entries", json=BODY)
        e = c.post("/api/accounting/entries/nope/reverse", json={})
        if role == "employee":
            assert (r.status_code, e.status_code) == (403, 403)
        else:
            assert r.status_code == 409  # no fiscal year yet: past the rights check, refused by the service
            assert e.status_code == 404


def test_api_round_trip_and_validation(boss_client, tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURE, charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    body = {**BODY, "lines": [{"account": BANK, "amount": "10.00"}, {"account": REVENUE, "amount": "-10.00"}]}
    year = {"year": 2026, "chart": "skr03", "income_basis": "eur"}
    assert boss_client.post("/api/accounting/years", json=year).status_code == 201
    r = boss_client.post("/api/accounting/entries", json=body)
    assert r.status_code == 201, r.text
    entry = r.json()
    assert entry["origin"] == "manual" and [ln["amount"] for ln in entry["lines"]] == ["10.00", "-10.00"]
    rev = boss_client.post(f"/api/accounting/entries/{entry['id']}/reverse", json={"text": "Oops"})
    assert rev.status_code == 201 and rev.json()["reverses_id"] == entry["id"]
    assert boss_client.post(f"/api/accounting/entries/{entry['id']}/reverse", json={}).status_code == 409
    bad = {**BODY, "lines": [{"account": BANK, "amount": "10.00"}, {"account": REVENUE, "amount": "-9.00"}]}
    assert boss_client.post("/api/accounting/entries", json=bad).status_code == 422
    unknown = {**BODY, "lines": [{"account": BANK, "amount": "10.00"}, {"account": "99999", "amount": "-10.00"}]}
    assert boss_client.post("/api/accounting/entries", json=unknown).status_code == 422


def test_tokens_are_refused(app, clients):
    for role in ("boss", "accountant"):
        for read_only in (True, False):
            tok = clients[role].post("/api/tokens", json={"label": "t", "read_only": read_only}).json()["token"]
            c = TestClient(app)
            c.headers["Authorization"] = f"Bearer {tok}"
            assert c.post("/api/accounting/entries", json=BODY).status_code == 403
            assert c.post("/api/accounting/entries/x/reverse", json={}).status_code == 403
