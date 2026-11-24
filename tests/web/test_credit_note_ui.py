"""Browser tests (work package W2): credit notes (storno, partial), write-off and settle with discount on an issued invoice."""

import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import chromium  # noqa: E402,F401  (shared harness)
from tests.web.test_invoice_payments import (  # noqa: E402,F401
    DUE,
    INVOICE_DATE,
    Api,
    open_page,
    pill,
    srv,
    text,
)


def two_rate_invoice(api):
    """Net 100.00 @ 19 % (gross 119.00) plus net 100.00 @ 7 % (gross 107.00): gross 226.00 (fake demo data)."""
    cust = api.post("customers", {"name": "Demo GmbH"})
    draft = api.post(
        "invoices",
        {
            "customer_id": cust["id"],
            "title": "Demo work",
            "date": INVOICE_DATE,
            "due_date": DUE,
            "lines": [
                {"description": "Work", "qty": 1, "unit": "h", "price": "100.00", "vat": 19},
                {"description": "Books", "qty": 1, "unit": "pc", "price": "100.00", "vat": 7},
            ],
        },
    )
    return api.post(f"invoices/{draft['id']}/finalize")


def goto_invoice(pg, srv, inv):
    pg.goto(srv.base + "/#/doc/invoices/" + inv["id"])
    pg.wait_for_selector("#cn_panel")


def fill_reason(pg, reason="Customer complaint"):
    pg.wait_for_selector("#f_reason")
    pg.fill("#f_reason", reason)


def test_storno_cancels_the_invoice(browser, srv):
    inv = Api(srv).issued_invoice()
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    pg.click("[data-a=cnStorno]")
    assert "119,00 €" in text(pg, "#cn_confirm")  # the dialog says what will happen
    fill_reason(pg)
    pg.click("#m-save")
    pg.wait_for_selector("#cn_list")
    assert text(pg, "h1 .pill") == "cancelled"
    row = text(pg, "#cn_list tr[data-cn]")
    assert "RK-" in row and "Cancellation" in row and "119,00 €" in row
    assert pg.locator("#cn_list a.cn-pdf").count() == 1
    assert pg.locator("[data-a=cnStorno], [data-a=cnPartial]").count() == 0
    ctx.close()


def test_partial_credit_per_rate_group_and_open_amount(browser, srv):
    api = Api(srv)
    inv = two_rate_invoice(api)
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    assert text(pg, "#pay_open") == "226,00 €"
    pg.click("[data-a=cnPartial]")
    pg.wait_for_selector("#f_amt0")
    labels = pg.eval_on_selector_all("#modal-root label", "els => els.map(e => e.textContent)")
    assert any("7 %" in x for x in labels) and any("19 %" in x for x in labels)
    pg.fill("#f_amt0", "10,00")  # 7 % group: net 10.00 + VAT 0.70 = 10.70
    pg.fill("#f_amt1", "50,00")  # 19 % group: net 50.00 + VAT 9.50 = 59.50
    fill_reason(pg)
    pg.click("#m-save")
    pg.wait_for_function("() => document.querySelector('#pay_credited')?.textContent.includes('70,20')")
    assert text(pg, "#pay_open") == "155,80 €"
    assert "Credit note" in text(pg, "#cn_list tr[data-cn]")
    assert api.get("invoices/" + inv["id"])["open_amount"] == "155.80"
    ctx.close()


def test_write_off_shows_the_written_off_pill_and_no_overdue(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()  # due 10 days ago: overdue while open
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    assert text(pg, "h1 .pill") == "overdue"
    pg.click("[data-a=cnWriteOff]")
    assert "119,00 €" in text(pg, "#cn_confirm")
    fill_reason(pg, "Uncollectible")
    pg.click("#m-save")
    pg.wait_for_function("() => document.querySelector('h1 .pill')?.textContent === 'written off'")
    assert text(pg, "#pay_open") == "0,00 €"
    row = text(pg, "#cn_list tr[data-cn]")
    assert "write-off" in row and "Write-off" in row
    assert pg.locator("#cn_list a.cn-pdf").count() == 0  # no number, no PDF
    assert pg.locator("[data-a=cnWriteOff], [data-a=cnSettle]").count() == 0  # nothing open
    pg.goto(srv.base + "/#/sales/invoices")
    pg.wait_for_selector("tr.click")
    assert pill(pg, inv) == "written off"
    ctx.close()


def test_settle_with_discount_leaves_nothing_open(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    pg.click("[data-a=cnSettle]")
    pg.wait_for_selector("#f_amount_paid")
    pg.fill("#f_amount_paid", "116,00")
    pg.click("#m-save")
    pg.wait_for_function("() => document.querySelector('#pay_open')?.textContent.includes('0,00')")
    assert text(pg, "#pay_paid") == "116,00 €"
    assert text(pg, "#pay_credited") == "3,00 €"
    assert "Discount" in text(pg, "#cn_list tr[data-cn]")
    assert text(pg, "h1 .pill") == "paid"
    ctx.close()


def test_api_error_for_over_credit_is_shown(browser, srv):
    inv = Api(srv).issued_invoice()
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    pg.click("[data-a=cnPartial]")
    pg.wait_for_selector("#f_amt0")
    pg.fill("#f_amt0", "150,00")  # the invoice has net 100.00 at 19 %
    fill_reason(pg)
    pg.click("#m-save")
    pg.wait_for_selector("#toast.on")
    assert pg.inner_text("#toast").strip() != ""
    assert pg.locator("#f_amt0").count() == 1  # the dialog stays open
    assert Api(srv).get("invoices/" + inv["id"])["credit_notes"] == []
    ctx.close()


def test_credit_notes_in_the_list_have_their_own_kind_and_negative_amount(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()
    api.post(f"invoices/{inv['id']}/credit-notes", {"credit_kind": "storno", "date": INVOICE_DATE, "reason": "x"})
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/sales/invoices")
    pg.wait_for_selector("tr.click")
    cn = pg.locator("tr.click:has([data-cn-kind=storno])")
    assert cn.count() == 1
    row = " ".join(cn.inner_text().split()).replace("\xa0", " ")
    assert "Cancellation" in row and "-119,00 €" in row
    assert pg.locator("tr.click:has-text('Invoice')").count() == 1  # the original stays a normal invoice
    ctx.close()


def test_reason_is_rendered_as_text(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()
    payload = '<img src=x onerror="window.__xss=1"><b>bold</b>'
    api.post(f"invoices/{inv['id']}/credit-notes", {"credit_kind": "write_off", "date": INVOICE_DATE, "reason": payload})
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    assert payload in pg.inner_text("#cn_list .cn-reason")  # the hostile text is really rendered, as text
    assert pg.locator("#cn_list img, #cn_list b, #cn_panel img").count() == 0
    assert pg.evaluate("() => window.__xss") is None
    pg.goto(srv.base + "/#/sales/invoices")
    pg.wait_for_selector("tr.click")
    assert pg.locator("table img, table b > b").count() == 0
    assert pg.evaluate("() => window.__xss") is None
    # the reason typed into the dialog is sent as text too
    inv2 = api.issued_invoice()
    goto_invoice(pg, srv, inv2)
    pg.click("[data-a=cnPartial]")
    pg.wait_for_selector("#f_amt0")
    pg.fill("#f_amt0", "10,00")
    fill_reason(pg, payload)
    pg.click("#m-save")
    pg.wait_for_selector("#cn_list")
    assert api.get("invoices/" + inv2["id"])["credit_notes"][0]["credit_kind"] == "partial"
    assert pg.evaluate("() => window.__xss") is None
    ctx.close()


def test_written_off_then_paid_in_full_reads_paid(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()
    api.post(f"invoices/{inv['id']}/credit-notes", {"credit_kind": "write_off", "date": INVOICE_DATE, "reason": "gone"})
    api.post(f"invoices/{inv['id']}/payments", {"date": INVOICE_DATE, "amount": "119.00"})  # reverses the write-off
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    assert text(pg, "h1 .pill") == "paid"
    assert pg.locator("#cn_panel .pill:has-text('written off')").count() == 0
    ctx.close()


def test_partial_payment_after_a_write_off_is_not_written_off(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()  # due in the past
    api.post(f"invoices/{inv['id']}/credit-notes", {"credit_kind": "write_off", "date": INVOICE_DATE, "reason": "gone"})
    api.post(f"invoices/{inv['id']}/payments", {"date": INVOICE_DATE, "amount": "50.00"})
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    assert text(pg, "#pay_open") == "0,00 €"  # the payment reversed only 50.00 of the write-off
    assert text(pg, "h1 .pill") == "paid"  # the derived server status wins over "written off"
    ctx.close()


def test_partial_dialog_shows_and_prefills_the_creditable_net(browser, srv):
    api = Api(srv)
    inv = two_rate_invoice(api)
    api.post(
        f"invoices/{inv['id']}/credit-notes",
        {"credit_kind": "partial", "date": INVOICE_DATE, "reason": "x", "amounts": {"19": "40.00"}},
    )
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    pg.click("[data-a=cnPartial]")
    pg.wait_for_selector("#f_amt0")
    assert pg.input_value("#f_amt0") == "100,00"  # 7 %: untouched
    assert pg.input_value("#f_amt1") == "60,00"  # 19 %: 100.00 - 40.00
    assert "still creditable 60,00" in text(pg, "label[for=f_amt1]")
    ctx.close()


def test_empty_reason_is_refused_before_any_request(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    pg.click("[data-a=cnWriteOff]")
    fill_reason(pg, "   ")
    pg.click("#m-save")
    pg.wait_for_selector("#toast.on")
    assert "reason" in pg.inner_text("#toast").lower()
    assert pg.locator("#f_reason").count() == 1  # the dialog stays open
    assert api.get("invoices/" + inv["id"])["credit_notes"] == []
    ctx.close()


def test_settle_note_is_sent_with_the_payment(browser, srv):
    api = Api(srv)
    inv = api.issued_invoice()
    ctx, pg = open_page(browser, srv)
    goto_invoice(pg, srv, inv)
    pg.click("[data-a=cnSettle]")
    pg.wait_for_selector("#f_amount_paid")
    pg.fill("#f_amount_paid", "116,00")
    pg.fill("#f_note", "Skonto agreed by phone")
    pg.click("#m-save")
    pg.wait_for_function("() => document.querySelector('#pay_open')?.textContent.includes('0,00')")
    got = api.get("invoices/" + inv["id"])
    assert [p.get("note") for p in got["payments"]] == ["Skonto agreed by phone"]
    ctx.close()
