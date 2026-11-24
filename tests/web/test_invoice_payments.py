"""Browser tests (work package W1): issued invoices are frozen, the payments panel, the overdue badge by open amount."""

from datetime import date, timedelta

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server, chromium  # noqa: E402,F401  (shared harness)

TODAY = date.today()
INVOICE_DATE = (TODAY - timedelta(days=30)).isoformat()
DUE = (TODAY - timedelta(days=10)).isoformat()


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


class Api:
    def __init__(self, srv):
        self.base = srv.base + "/api/"
        self.h = {"Authorization": "Bearer " + srv.token}

    def post(self, path, body=None):
        r = httpx.post(self.base + path, headers=self.h, json=body or {})
        assert r.status_code in (200, 201), r.text
        return r.json()

    def put(self, path, body):
        r = httpx.put(self.base + path, headers=self.h, json=body)
        assert r.status_code == 200, r.text
        return r.json()

    def get(self, path):
        return httpx.get(self.base + path, headers=self.h).json()

    def issued_invoice(self, due=DUE, cust=None):
        """An issued invoice with one line: net 100.00 + 19 % VAT = gross 119.00 (fake demo data)."""
        cust = cust or self.post("customers", {"name": "Demo GmbH"})
        draft = self.post(
            "invoices",
            {
                "customer_id": cust["id"],
                "title": "Demo work",
                "date": INVOICE_DATE,
                "due_date": due,
                "lines": [{"description": "Work", "qty": 1, "unit": "h", "price": "100.00", "vat": 19}],
            },
        )
        return self.post(f"invoices/{draft['id']}/finalize")


def open_page(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 1000})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    pg = ctx.new_page()
    pg.on("dialog", lambda d: d.accept())
    return ctx, pg


def text(pg, sel):
    return " ".join(pg.inner_text(sel).split()).replace("\xa0", " ")


def record_payment(pg, amount, kind="payment"):
    pg.click("[data-a=payRecord]")
    pg.wait_for_selector("#f_amount")
    pg.fill("#f_date", INVOICE_DATE)
    pg.fill("#f_amount", amount)
    pg.select_option("#f_kind", kind)
    pg.click("#m-save")
    pg.wait_for_selector("#f_amount", state="detached")


def test_issued_invoice_is_read_only_except_the_allowed_fields(browser, srv):
    inv = Api(srv).issued_invoice()
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/doc/invoices/" + inv["id"])
    pg.wait_for_selector("#d_frozen")
    assert "issue a credit note" in pg.inner_text("#d_frozen")
    for frozen in ("#d_cust", "#d_title", "#d_date", "#d_tt", '[data-k="description"]', '[data-k="price"]'):
        assert pg.is_disabled(frozen), frozen
    for editable in ("#d_due", "#d_ref", "#d_notes"):
        assert pg.is_enabled(editable), editable
    assert pg.locator("[data-a=addLine]").count() == 0 and pg.locator("[data-a=delLine]").count() == 0
    assert pg.locator("#pay_panel").count() == 1
    # the allowed fields can still be saved
    pg.fill("#d_notes", "Called the customer")
    pg.click("[data-a=docSave]")
    pg.wait_for_selector("#toast.on")
    assert Api(srv).get("invoices/" + inv["id"])["notes"] == "Called the customer"
    ctx.close()


def test_draft_stays_editable_and_has_a_tax_treatment_select(browser, srv):
    cust = Api(srv).post("customers", {"name": "Demo GmbH"})
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/doc/invoices/new-" + cust["id"])
    pg.wait_for_selector("#d_totals")
    assert pg.is_enabled("#d_title") and pg.is_enabled("#d_date")
    assert pg.locator("#pay_panel").count() == 0 and pg.locator("#d_frozen").count() == 0
    assert pg.eval_on_selector_all("#d_tt option", "els => els.map(e => e.value)") == ["standard", "eu_b2b_service"]
    ctx.close()


def test_partial_payment_shows_open_amount_then_reverse(browser, srv):
    inv = Api(srv).issued_invoice()
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/doc/invoices/" + inv["id"])
    pg.wait_for_selector("#pay_panel")
    assert text(pg, "#pay_open") == "119,00 €"
    record_payment(pg, "50,00")
    pg.wait_for_function("() => document.querySelector('#pay_paid')?.textContent.includes('50,00')")
    assert text(pg, "#pay_open") == "69,00 €"
    assert "50,00 €" in text(pg, "#pay_list")
    assert pg.locator("[data-warn]").count() == 0
    pg.click("[data-a=payReverse]")
    pg.wait_for_function("() => document.querySelector('#pay_open')?.textContent.includes('119,00')")
    assert text(pg, "#pay_paid") == "0,00 €"
    ctx.close()


def test_overpayment_warning_is_shown(browser, srv):
    inv = Api(srv).issued_invoice()
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/doc/invoices/" + inv["id"])
    pg.wait_for_selector("#pay_panel")
    record_payment(pg, "200,00")
    pg.wait_for_selector("[data-warn=overpayment]")
    assert text(pg, "#pay_open") == "-81,00 €"
    ctx.close()


def test_overdue_badge_follows_the_open_amount(browser, srv):
    api = Api(srv)
    open_inv = api.issued_invoice()
    paid_inv = api.issued_invoice()
    api.post(f"invoices/{paid_inv['id']}/payments", {"date": INVOICE_DATE, "amount": "119.00"})
    written_off = api.issued_invoice()
    api.post(
        f"invoices/{written_off['id']}/credit-notes",
        {"credit_kind": "write_off", "date": TODAY.isoformat(), "reason": "uncollectible"},
    )
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/sales/invoices")
    pg.wait_for_selector("tr.click")

    def badge(inv):
        return pg.locator(f'tr[data-href="#/doc/invoices/{inv["id"]}"] .pill').last.inner_text()

    assert badge(open_inv) == "overdue"
    assert badge(paid_inv) != "overdue"
    assert badge(written_off) != "overdue"
    ctx.close()


def pill(pg, inv):
    return " ".join(pg.locator(f'tr[data-href="#/doc/invoices/{inv["id"]}"] .pill').last.inner_text().split())


def test_pill_texts_for_paid_written_off_and_partly_paid_overdue(browser, srv):
    api = Api(srv)
    partly = api.issued_invoice()
    api.post(f"invoices/{partly['id']}/payments", {"date": INVOICE_DATE, "amount": "50.00"})
    paid = api.issued_invoice()
    api.post(f"invoices/{paid['id']}/payments", {"date": INVOICE_DATE, "amount": "119.00"})
    written_off = api.issued_invoice()
    api.post(
        f"invoices/{written_off['id']}/credit-notes",
        {"credit_kind": "write_off", "date": TODAY.isoformat(), "reason": "uncollectible"},
    )
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/sales/invoices")
    pg.wait_for_selector("tr.click")
    assert pill(pg, partly) == "overdue"
    assert pill(pg, paid) == "paid"
    assert pill(pg, written_off) == api.get("invoices/" + written_off["id"])["status"]
    assert pill(pg, written_off) != "overdue"
    ctx.close()


def test_customer_open_column_after_partial_payment_and_credit_note(browser, srv):
    api = Api(srv)
    cust = api.post("customers", {"name": "Demo GmbH"})
    inv = api.issued_invoice(cust=cust)
    api.post(f"invoices/{inv['id']}/payments", {"date": INVOICE_DATE, "amount": "50.00"})
    other = api.issued_invoice(cust=cust)  # 119.00 open
    api.post(
        f"invoices/{other['id']}/credit-notes", {"credit_kind": "storno", "date": TODAY.isoformat(), "reason": "x"}
    )
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/customers")
    pg.wait_for_selector("tr.click")
    # 119.00 - 50.00 = 69.00 open; the fully credited invoice and the credit note itself add nothing
    assert text(pg, "tr.click td.num:last-child") == "69,00 €"
    pg.goto(srv.base + "/#/sales")
    pg.wait_for_function(
        "() => [...document.querySelectorAll('.kpi')].some(k => k.textContent.includes('Outstanding'))"
    )
    assert "69,00 €" in text(pg, ".kpi:has-text('Outstanding') .v")
    ctx.close()


def test_legacy_virtual_payment_can_be_reversed_in_the_ui(browser, srv):
    cust = Api(srv).post("customers", {"name": "Demo GmbH"})
    line = {"description": "Work", "qty": 1, "unit": "h", "price": "100.00", "vat": 19, "amount": 100.0}
    inv = srv.app.state.services.store.save(  # a paid invoice as an older version stored it: no payments rows
        "invoices",
        {"customer_id": cust["id"], "number": "RE-2025-0001", "status": "paid", "date": INVOICE_DATE, "due_date": DUE,
         "paid_date": INVOICE_DATE, "lines": [line], "template_id": "classic"},
    )  # fmt: skip
    got = Api(srv).get("invoices/" + inv["id"])
    assert got["payments"][0]["id"] is None and got["open_amount"] == "0.00"
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/doc/invoices/" + inv["id"])
    pg.wait_for_selector("#pay_panel")
    assert text(pg, "#pay_open") == "0,00 €"
    pg.click("[data-a=payReverse]")
    pg.wait_for_function("() => document.querySelector('#pay_open')?.textContent.includes('119,00')")
    assert text(pg, "#pay_paid") == "0,00 €"
    ctx.close()


def test_small_business_year_shows_the_fixed_tax_treatment(browser, srv):
    api = Api(srv)
    cust = api.post("customers", {"name": "Demo GmbH"})
    s = api.get("settings")
    api.put("settings", {**s, "kleinunternehmer": True})
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/doc/invoices/new-" + cust["id"])
    pg.wait_for_selector("#d_totals")
    assert pg.is_disabled("#d_tt")
    assert pg.eval_on_selector_all("#d_tt option", "els => els.map(e => e.textContent)") == [
        "Small business (§ 19 UStG)"
    ]
    ctx.close()


def test_payment_note_is_rendered_as_text(browser, srv):
    inv = Api(srv).issued_invoice()
    payload = '<img src=x onerror="window.__xss=1"><b>bold</b>'
    ctx, pg = open_page(browser, srv)
    pg.goto(srv.base + "/#/doc/invoices/" + inv["id"])
    pg.wait_for_selector("#pay_panel")
    pg.click("[data-a=payRecord]")
    pg.wait_for_selector("#f_amount")
    pg.fill("#f_date", INVOICE_DATE)
    pg.fill("#f_amount", "10,00")
    pg.fill("#f_note", payload)
    pg.click("#m-save")
    pg.wait_for_selector("#pay_list")
    assert payload in pg.inner_text("#pay_list")
    assert pg.locator("#pay_list img, #pay_list b").count() == 0
    assert pg.evaluate("() => window.__xss") is None
    ctx.close()
