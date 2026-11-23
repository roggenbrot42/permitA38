"""D3b: receipt and document scan responses carry the money of their untyped draft/doc dicts as decimal strings
(decimal-money-api.md rule 4); vat_rate and the other percents stay numbers. Fake data only, no real PDFs."""

import re
import time

import pytest

from permita38.core.service import document_scans as ds_mod
from permita38.core.service import receipts as svc_mod

from . import test_document_scans_api as docs
from . import test_receipts_api as rcpt
from .test_document_scans_service import INVOICE, swap

DEC = re.compile(r"^-?[0-9]+\.[0-9]{2,6}$")


@pytest.fixture
def rc(boss_client, app, monkeypatch):
    r = rcpt.Reader()
    monkeypatch.setattr(svc_mod.runner, "read_file", r)
    s = app.state.services
    s.receipts._today = lambda: rcpt.TODAY
    s.receipts._ocr_cache = (time.monotonic(), False)
    return boss_client, r


@pytest.fixture
def dc(boss_client, app, monkeypatch):
    r = docs.Reader()
    monkeypatch.setattr(ds_mod.runner, "read_file", r)
    s = app.state.services
    s.document_scans._today = lambda: docs.TODAY
    store = s.store
    store.save_settings(
        {
            **store.settings(),
            "company_name": "Max Muster IT-Beratung",
            "owner_name": "Max Muster",
            "vat_id": "DE123456789",
            "payment_days": 30,
        }
    )
    return boss_client, r


def _is_num(v):
    return isinstance(v, int | float) and not isinstance(v, bool)


def test_receipt_scan_draft_single_rate_is_decimal_string(rc):
    c, reader = rc
    # items add up to 56,00 but the bill says 50,00: flagged, so the draft stays a draft
    r = c.post("/api/receipts", files={"file": ("bill.pdf", reader.add(rcpt.TANK))})
    assert r.status_code == 201, r.text
    scan = r.json()["scan"]
    assert scan["draft"]["gross"] == "50.00"
    assert _is_num(scan["draft"]["vat_rate"]) and scan["draft"]["vat_rate"] == 19
    assert _is_num(scan["draft"]["deductible"])
    for got in (
        c.get(f"/api/receipts/{scan['id']}").json(),
        c.get("/api/receipts?status=flagged").json()["items"][0],
    ):
        assert got["draft"]["gross"] == "50.00" and got["draft"]["vat_rate"] == 19
    # the doc dict keeps its strings and its non-money numbers
    assert scan["doc"]["pages"] == 1 and scan["doc"]["printed_total"] == "50.00"


def test_receipt_scan_draft_lines_are_decimal_strings_and_confirm_books_the_same(rc):
    c, reader = rc
    rows = [x if not x.startswith("Gesamt") else "Gesamt 200,00 EUR" for x in rcpt.HOTEL]
    r = c.post("/api/receipts", files={"file": ("bill.pdf", reader.add(rows))})
    assert r.status_code == 201, r.text
    scan = r.json()["scan"]
    assert r.json()["outcome"] == "flagged"
    draft = scan["draft"]
    assert draft["gross"] == "200.00"
    # the three VAT groups of the bill, read from the table rows 160,00 / 30,00 / 6,00
    assert [ln["gross"] for ln in draft["lines"]] == ["160.00", "30.00", "6.00"]
    assert [ln["vat_rate"] for ln in draft["lines"]] == [7, 19, 0]
    assert all(_is_num(ln["vat_rate"]) for ln in draft["lines"])
    # confirm with the strings the client received books the same amounts as a clean bill would
    body = {
        "date": draft["date"],
        "vendor": draft["vendor"],
        "invoice_no": draft["invoice_no"],
        "category": draft["category"],
        "net": "0",
        "lines": [
            {"description": ln["description"], "gross": ln["gross"], "vat_rate": ln["vat_rate"]}
            for ln in draft["lines"]
        ],
    }
    # 7 %: 160,00 -> vat 10.47, net 149.53; 19 %: 30,00 -> 4.79 / 25.21; 0 %: net 6.00
    ok = c.post(f"/api/receipts/{scan['id']}/confirm", json={**body, "accepted": ["total_differs"]})
    assert ok.status_code == 200, ok.text
    e = ok.json()["expense"]
    assert (e["net"], e["vat"]) == ("180.74", "15.26")
    assert [(ln["gross"], ln["net"], ln["vat"]) for ln in e["lines"]] == [
        ("160.00", "149.53", "10.47"),
        ("30.00", "25.21", "4.79"),
        ("6.00", "6.00", "0.00"),
    ]


def test_document_scan_draft_money_strings_and_confirm_round_trip(dc):
    c, reader = dc
    rows = swap(INVOICE, "Gesamtbetrag", "Gesamtbetrag 900,00 EUR")  # printed 900,00, computed 892,50
    r = c.post("/api/document-scans", files={"file": ("alt.pdf", reader.add(rows))})
    assert r.status_code == 201, r.text
    scan = r.json()["scan"]
    draft, doc = scan["draft"], scan["doc"]
    assert [ln["price"] for ln in draft["lines"]] == ["50.00", "62.50"]
    assert all(DEC.match(ln["price"]) for ln in draft["lines"])
    assert (doc["printed_net"], doc["printed_total"]) == ("750.00", "900.00")
    # percentages, quantities and counts are not money: left exactly as the service builds them
    assert draft["vat_rate"] == "19" and [ln["quantity"] for ln in draft["lines"]] == ["10", "4"]
    assert doc["pages"] == 1
    got = c.get(f"/api/document-scans/{scan['id']}").json()
    assert got["draft"] == draft and got["doc"] == doc
    assert c.get("/api/document-scans").json()["items"][0]["draft"] == draft
    # confirm with the returned strings: 10 x 50.00 + 4 x 62.50 = 750.00, VAT 19 % 142.50, gross 892.50
    body = {
        "kind": draft["kind"],
        "number": draft["number"],
        "date": draft["date"],
        "customer": {"name": "Beispiel Kunde GmbH"},
        "vat_rate": draft["vat_rate"],
        "lines": [{k: ln[k] for k in ("description", "quantity", "price")} for ln in draft["lines"]],
        "accept_totals_mismatch": True,
    }
    ok = c.post(f"/api/document-scans/{scan['id']}/confirm", json=body)
    assert ok.status_code == 200, ok.text
    inv = c.get(f"/api/invoices/{ok.json()['record']['id']}").json()
    assert [(ln["price"], ln["qty"]) for ln in inv["lines"]] == [("50.00", 10), ("62.50", 4)]
