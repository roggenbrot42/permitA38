"""Issued invoices are frozen; legacy ``paid`` becomes a payment (docs/specs/credit-notes-payments.md 2.1, 2.2, 2.3).

All figures are fake: one line of net 1,000.00 at 19 % -> gross 1,190.00 (spec example P1).
"""

from datetime import date

import pytest

from permita38.core import calc
from permita38.core.service.sales import record_payment_row

FROZEN = "The invoice is issued and cannot be changed; issue a credit note"
TODAY = date.today().isoformat()
LINE = {"description": "Work", "qty": 1, "price": 1000, "vat": 19}


@pytest.fixture
def store(app):
    return app.state.services.store


@pytest.fixture
def cust(boss_client):
    return boss_client.post("/api/customers", json={"name": "Demo Client GmbH", "country": "DE"}).json()


def make_invoice(c, cust, day="2026-02-01", **extra):
    body = {"customer_id": cust["id"], "date": day, "lines": [LINE], **extra}
    r = c.post("/api/invoices", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def issue(c, cust, day="2026-02-01", **extra):
    inv = make_invoice(c, cust, day, **extra)
    r = c.post(f"/api/invoices/{inv['id']}/finalize")
    assert r.status_code == 200, r.text
    return c.get(f"/api/invoices/{inv['id']}").json()


def put(c, inv, **changes):
    body = {k: v for k, v in inv.items() if k not in ("id", "created", "updated")}
    body.update(changes)
    return c.put(f"/api/invoices/{inv['id']}", json=body)


# -- 2.1 changeable fields ----------------------------------------------------------------------------------------


def test_whole_record_put_of_an_unchanged_issued_invoice_passes(boss_client, cust):
    inv = issue(boss_client, cust)
    assert inv["status"] == "sent" and inv["issued"]["gross"] == 119000
    r = put(boss_client, inv)
    assert r.status_code == 200, r.text
    after = r.json()
    assert after["issued"] == inv["issued"] and after["number"] == inv["number"]
    assert after["lines"][0]["amount"] == 1000.0 or after["lines"][0]["amount"] == "1000.00"


def test_partial_put_leaves_everything_else_alone(boss_client, cust):
    inv = issue(boss_client, cust)
    r = boss_client.put(f"/api/invoices/{inv['id']}", json={"notes": "Thanks"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["notes"] == "Thanks" and out.get("title") == inv.get("title") and len(out["lines"]) == 1
    assert out["issued"] == inv["issued"]


@pytest.mark.parametrize(
    ("field", "value"),
    [("due_date", "2026-03-31"), ("notes", "Paid in two parts"), ("reference", "PO-77"), ("status", "accepted")],
)
def test_changeable_field_is_accepted(boss_client, cust, field, value):
    inv = issue(boss_client, cust)
    r = put(boss_client, inv, **{field: value})
    assert r.status_code == 200, r.text
    assert r.json()[field] == value
    assert boss_client.get(f"/api/invoices/{inv['id']}").json()[field] == value


def test_status_labels_change_among_each_other(boss_client, cust):
    inv = issue(boss_client, cust)
    for label in ("accepted", "rejected", "sent"):
        assert put(boss_client, inv, status=label).json()["status"] == label


def test_changeable_field_can_be_cleared_with_null(boss_client, cust):
    inv = issue(boss_client, cust, notes="first")
    r = boss_client.put(f"/api/invoices/{inv['id']}", json={"notes": None})
    assert r.status_code == 200 and "notes" not in r.json()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("title", "Another title"),
        ("intro", "Another intro"),
        ("outro", "Another outro"),
        ("date", "2026-02-02"),
        ("service_from", "2026-01-02"),
        ("service_to", "2026-01-30"),
        ("vat_rate", 7),
        ("language", "en"),
        ("template_id", "abcdef12"),
        ("lines", [{"description": "Work", "qty": 2, "price": 1000, "vat": 19}]),
        ("lines", [LINE, LINE]),
        ("lines", []),
        ("tax_treatment", "eu_b2b_service"),
    ],
)
def test_frozen_field_change_is_refused_with_the_exact_message(boss_client, cust, field, value):
    inv = issue(boss_client, cust, service_from="2026-01-01", service_to="2026-01-31", title="T", intro="I")
    before = boss_client.get(f"/api/invoices/{inv['id']}").json()
    r = put(boss_client, inv, **{field: value})
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": FROZEN}
    assert boss_client.get(f"/api/invoices/{inv['id']}").json() == before


def test_customer_change_is_refused(boss_client, cust):
    other = boss_client.post("/api/customers", json={"name": "Other Demo GmbH"}).json()
    inv = issue(boss_client, cust)
    r = put(boss_client, inv, customer_id=other["id"])
    assert r.status_code == 409 and r.json() == {"detail": FROZEN}


def test_unchanged_frozen_values_in_other_spellings_are_not_a_change(boss_client, cust):
    inv = issue(boss_client, cust, service_from="2026-01-01", service_to="2026-01-31")
    lines = [{"description": "Work", "qty": "1.00", "price": "1000.00", "vat": "19", "amount": 1000.0}]
    r = put(boss_client, inv, lines=lines, vat_rate=inv.get("vat_rate"), notes="n")
    assert r.status_code == 200, r.text


def test_unknown_extra_fields_round_trip(boss_client, cust):
    inv = issue(boss_client, cust)
    r = put(boss_client, inv, future_flag="x", future_obj={"a": 1})
    assert r.status_code == 200, r.text
    got = boss_client.get(f"/api/invoices/{inv['id']}").json()
    assert got["future_flag"] == "x" and got["future_obj"] == {"a": 1}
    assert put(boss_client, got).status_code == 200


def test_draft_and_offer_are_unchanged(boss_client, cust):
    d = make_invoice(boss_client, cust)
    r = put(boss_client, d, title="New", lines=[{"description": "Other", "qty": 3, "price": 10}])
    assert r.status_code == 200 and r.json()["title"] == "New" and r.json()["lines"][0]["qty"] == 3
    off = boss_client.post("/api/offers", json={"customer_id": cust["id"], "lines": [LINE]}).json()
    assert boss_client.post(f"/api/offers/{off['id']}/finalize").status_code == 200
    r = boss_client.put(f"/api/offers/{off['id']}", json={"title": "Changed", "lines": [LINE], "status": "sent"})
    assert r.status_code == 200 and r.json()["title"] == "Changed"


def test_delete_issued_invoice_and_credit_note_is_409(boss_client, cust, store):
    inv = issue(boss_client, cust)
    assert boss_client.delete(f"/api/invoices/{inv['id']}").status_code == 409
    cn = store.save(
        "invoices",
        {
            "customer_id": cust["id"], "date": "2026-02-05", "status": "sent", "kind": "credit_note",
            "corrects_id": inv["id"], "credit_kind": "partial", "lines": [], "issued": {"rates": {}, "gross": 5000},
        },
    )  # fmt: skip
    r = boss_client.delete(f"/api/invoices/{cn['id']}")
    assert r.status_code == 409
    assert boss_client.get(f"/api/invoices/{cn['id']}").status_code == 200
    d = make_invoice(boss_client, cust)
    assert boss_client.delete(f"/api/invoices/{d['id']}").status_code == 204


# -- 2.2 legacy paid ----------------------------------------------------------------------------------------------


def test_paid_with_date_creates_one_payment_of_the_open_amount(boss_client, cust, store):
    inv = issue(boss_client, cust)
    r = put(boss_client, inv, status="paid", paid_date="2026-02-03")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "paid" and out["paid_date"] == "2026-02-03"
    assert out["paid_amount"] == "1190.00" and out["open_amount"] == "0.00" and out["credited_amount"] == "0.00"
    assert out["payments"] == [
        {
            "id": out["payments"][0]["id"], "date": "2026-02-03", "amount": "1190.00", "kind": "payment",
            "method": "bank", "note": "recorded as paid",
        }
    ]  # fmt: skip
    rows = store.find("payments", document_id=inv["id"])
    assert len(rows) == 1 and rows[0]["amount_cents"] == 119000 and rows[0]["created_by"]
    mirror = store.get("invoices", inv["id"])  # compat mirror for readers of the stored fields
    assert (mirror["status"], mirror["paid_date"]) == ("paid", "2026-02-03")
    again = put(boss_client, inv, status="paid", paid_date="2026-02-03")  # P1: a second identical PUT changes nothing
    assert again.status_code == 200 and len(store.find("payments", document_id=inv["id"])) == 1


def test_paid_when_nothing_is_open_is_a_no_op(boss_client, cust, store):
    inv = issue(boss_client, cust)
    record_payment_row(store, inv["id"], date="2026-02-02", amount_cents=119000)
    r = put(boss_client, inv, status="paid", paid_date="2026-02-02")
    assert r.status_code == 200
    assert len(store.find("payments", document_id=inv["id"])) == 1
    assert r.json()["paid_date"] == "2026-02-02"
    assert put(boss_client, inv, status="paid", paid_date="2026-02-09").status_code == 409  # another date: a change


def test_paid_without_paid_date_is_422(boss_client, cust, store):
    inv = issue(boss_client, cust)
    r = boss_client.put(f"/api/invoices/{inv['id']}", json={"status": "paid"})
    assert r.status_code == 422, r.text
    assert store.find("payments", document_id=inv["id"]) == []


def test_paid_on_an_invoice_with_a_partial_payment_pays_the_rest(boss_client, cust, store):
    inv = issue(boss_client, cust)
    record_payment_row(store, inv["id"], date="2026-02-02", amount_cents=100000)
    out = put(boss_client, inv, status="paid", paid_date="2026-02-05").json()
    assert [p["amount"] for p in out["payments"]] == ["1000.00", "190.00"]
    assert out["paid_date"] == "2026-02-05" and out["status"] == "paid"


def test_paid_date_before_the_invoice_date_or_in_the_future_is_422(boss_client, cust, store):
    inv = issue(boss_client, cust, day="2026-02-10")
    assert put(boss_client, inv, status="paid", paid_date="2026-02-09").status_code == 422
    assert put(boss_client, inv, status="paid", paid_date="2999-01-01").status_code == 422
    assert store.find("payments", document_id=inv["id"]) == []


def test_paid_back_to_sent_is_409_when_a_real_payment_exists(boss_client, cust, store):
    inv = issue(boss_client, cust)
    paid = put(boss_client, inv, status="paid", paid_date="2026-02-03").json()
    r = put(boss_client, paid, status="sent")
    assert r.status_code == 409 and "reverse the payment" in r.json()["detail"]
    assert len(store.find("payments", document_id=inv["id"])) == 1


def test_paid_date_alone_changed_on_a_paid_invoice_is_409(boss_client, cust):
    inv = issue(boss_client, cust)
    paid = put(boss_client, inv, status="paid", paid_date="2026-02-03").json()
    r = put(boss_client, paid, paid_date="2026-02-04")
    assert r.status_code == 409
    assert put(boss_client, paid).status_code == 200  # the unchanged whole record passes


def test_cancelled_by_put_is_refused(boss_client, cust):
    inv = issue(boss_client, cust)
    r = put(boss_client, inv, status="cancelled")
    assert r.status_code == 409 and "credit note" in r.json()["detail"]


def test_status_draft_on_an_issued_invoice_is_refused(boss_client, cust):
    inv = issue(boss_client, cust)
    assert put(boss_client, inv, status="draft").status_code == 409


def test_a_credit_note_cannot_be_marked_paid(boss_client, cust, store):
    inv = issue(boss_client, cust)
    cn = store.save(
        "invoices",
        {
            "customer_id": cust["id"], "date": "2026-02-05", "status": "sent", "kind": "credit_note",
            "corrects_id": inv["id"], "credit_kind": "partial", "lines": [], "issued": {"rates": {}, "gross": 5000},
        },
    )  # fmt: skip
    r = boss_client.put(f"/api/invoices/{cn['id']}", json={"status": "paid", "paid_date": "2026-02-06"})
    assert r.status_code == 409


# -- 2.3 legacy paid invoices: virtual payment and materialisation --------------------------------------------------


def legacy(store, cust, **extra):
    """A paid invoice as an older version stored it: no snapshot, no payments rows."""
    row = {
        "customer_id": cust["id"], "number": "RE-2025-0001", "status": "paid", "date": "2026-02-01",
        "due_date": "2026-02-15", "lines": [{**LINE, "amount": 1000.0}], "template_id": "classic",
    }  # fmt: skip
    return store.save("invoices", {**row, **extra})


def test_legacy_paid_reads_as_one_virtual_payment_and_writes_nothing(boss_client, cust, store):
    inv = legacy(store, cust, paid_date="2026-02-10")
    out = boss_client.get(f"/api/invoices/{inv['id']}").json()
    assert out["status"] == "paid" and out["paid_date"] == "2026-02-10"
    assert out["paid_amount"] == "1190.00" and out["open_amount"] == "0.00"
    assert out["payments"] == [
        {"id": None, "date": "2026-02-10", "amount": "1190.00", "kind": "payment", "method": "bank", "note": "migrated"}
    ]
    assert store.find("payments", document_id=inv["id"]) == []
    assert "issued" not in store.get("invoices", inv["id"])
    assert boss_client.get(f"/api/invoices/{inv['id']}").json() == out


def test_legacy_paid_without_paid_date_reads_on_the_invoice_date(boss_client, cust, store):
    inv = legacy(store, cust)
    out = boss_client.get(f"/api/invoices/{inv['id']}").json()
    assert out["payments"][0]["date"] == "2026-02-01" and out["paid_date"] == "2026-02-01"
    assert out["status"] == "paid" and out["open_amount"] == "0.00"


def test_whole_record_put_of_a_legacy_paid_invoice_writes_no_payment(boss_client, cust, store):
    inv = legacy(store, cust, paid_date="2026-02-10")
    got = boss_client.get(f"/api/invoices/{inv['id']}").json()
    r = put(boss_client, got, notes="checked")
    assert r.status_code == 200, r.text
    assert store.find("payments", document_id=inv["id"]) == []
    assert r.json()["paid_date"] == "2026-02-10"


def test_legacy_paid_back_to_sent_materialises_and_reverses(boss_client, cust, store):
    inv = legacy(store, cust, paid_date="2026-02-10")
    got = boss_client.get(f"/api/invoices/{inv['id']}").json()
    r = put(boss_client, got, status="sent")
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["status"] == "sent" and out["paid_amount"] == "0.00" and out["open_amount"] == "1190.00"
    assert "paid_date" not in out
    rows = sorted(store.find("payments", document_id=inv["id"]), key=lambda r: r["amount_cents"])
    assert [(r["kind"], r["amount_cents"]) for r in rows] == [("reversal", -119000), ("payment", 119000)]
    assert rows[0]["reverses_id"] == rows[1]["id"] and rows[1]["note"] == "migrated"
    assert store.get("invoices", inv["id"])["issued"]["gross"] == 119000


def test_materialisation_writes_the_row_and_the_snapshot_exactly_once(store, cust, app):
    from permita38.core.service.sales import materialize_legacy_payment

    inv = legacy(store, cust, paid_date="2026-02-10")
    st = app.state.services.sales.settings()
    out = materialize_legacy_payment(store, inv, st, {"id": "u1"})
    assert out["issued"]["gross"] == 119000
    rows = store.find("payments", document_id=inv["id"])
    assert len(rows) == 1 and rows[0]["date"] == "2026-02-10" and rows[0]["amount_cents"] == 119000
    assert rows[0]["note"] == "migrated" and rows[0]["created_by"] == "u1"
    stamp = out["issued"]["issued_at"]
    again = materialize_legacy_payment(store, store.get("invoices", inv["id"]), st, {"id": "u1"})
    assert len(store.find("payments", document_id=inv["id"])) == 1 and again["issued"]["issued_at"] == stamp


def test_materialisation_keeps_a_legacy_paid_date_before_the_invoice_date(store, cust, app):
    from permita38.core.service.sales import materialize_legacy_payment

    inv = legacy(store, cust, paid_date="2026-01-15")  # before the invoice date 2026-02-01: stored as is
    materialize_legacy_payment(store, inv, app.state.services.sales.settings())
    assert store.find("payments", document_id=inv["id"])[0]["date"] == "2026-01-15"


def test_materialisation_of_a_paid_invoice_without_date_uses_the_invoice_date(store, cust, app):
    from permita38.core.service.sales import materialize_legacy_payment

    inv = legacy(store, cust)
    materialize_legacy_payment(store, inv, app.state.services.sales.settings())
    assert store.find("payments", document_id=inv["id"])[0]["date"] == "2026-02-01"


def test_materialisation_of_an_unpaid_invoice_writes_only_the_snapshot(store, cust, app):
    from permita38.core.service.sales import materialize_legacy_payment

    inv = legacy(store, cust, status="sent")
    out = materialize_legacy_payment(store, inv, app.state.services.sales.settings())
    assert out["issued"]["gross"] == 119000 and store.find("payments", document_id=inv["id"]) == []


def test_paying_a_legacy_invoice_without_snapshot_writes_the_snapshot(boss_client, cust, store):
    inv = legacy(store, cust, status="sent")
    got = boss_client.get(f"/api/invoices/{inv['id']}").json()
    assert got["open_amount"] == "1190.00" and "issued" not in got
    r = put(boss_client, got, status="paid", paid_date="2026-02-03")
    assert r.status_code == 200, r.text
    assert store.get("invoices", inv["id"])["issued"]["gross"] == 119000
    assert len(store.find("payments", document_id=inv["id"])) == 1


# -- draft preview ------------------------------------------------------------------------------------------------


def test_draft_preview_respects_the_small_business_year_of_its_date(store, cust, app, monkeypatch):
    store.save(
        "fiscal_years",
        {
            "year": 2026, "chart": "skr03", "income_basis": "accrual", "vat_basis": "soll", "vat_frequency": "month",
            "dauerfrist": False, "small_business": True, "rules_version": "skr03-2026/vat-2026",
            "opened_at": 1, "opened_by": "u1x000",
        },
    )  # fmt: skip
    draft = store.save("invoices", {"customer_id": cust["id"], "date": "2026-03-01", "lines": [dict(LINE)]})
    other_year = store.save("invoices", {"customer_id": cust["id"], "date": "2027-03-01", "lines": [dict(LINE)]})
    st = app.state.services.sales.settings()
    assert calc.doc_totals(dict(draft), st)["vat_total"] == 190.0  # without the helper the settings say regular
    assert calc.doc_totals(calc.with_year_regime(store, draft), st)["vat_total"] == 0
    assert calc.with_year_regime(store, other_year) is other_year
    seen = []
    monkeypatch.setattr("permita38.core.service.sales.documents.render", lambda s, k, doc: seen.append(doc) or b"%PDF")
    app.state.services.sales.pdf("invoices", draft["id"])
    assert seen[0]["tax_treatment"] == "small_business"
    assert calc.doc_totals(dict(seen[0]), st)["gross"] == 1000.0
    assert "tax_treatment" not in store.get("invoices", draft["id"])  # a preview never stores it
