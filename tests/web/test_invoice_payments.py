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

    def get(self, path):
        return httpx.get(self.base + path, headers=self.h).json()

    def issued_invoice(self, due=DUE):
        """An issued invoice with one line: net 100.00 + 19 % VAT = gross 119.00 (fake demo data)."""
        cust = self.post("customers", {"name": "Demo GmbH"})
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
