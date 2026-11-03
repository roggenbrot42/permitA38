"""API of the document scan (docs/specs/document-scan.md). Invented data only; the reading is faked by file content
(see test_document_scans_service), except for the page image which renders a generated PDF."""

from datetime import date

import pytest

from permita38.api.deps import declared
from permita38.api.routers import document_scans
from permita38.core.service import document_scans as ds_mod

from .conftest import PASSWORD, _login
from .receipt_helpers import RECEIPT_LINES, receipt_pdf
from .test_document_scans_service import INVOICE, OFFER_BODY, TODAY, Reader, swap

assert TODAY == date(2026, 11, 3)

PUBLIC_KEYS = {"id", "status", "kind", "filename", "attachment", "engine", "flags", "notes", "draft", "doc", "created"}
HIDDEN_KEYS = {"created_by", "batch_id", "sha256", "doc_key"}


@pytest.fixture
def reader(monkeypatch):
    r = Reader()
    monkeypatch.setattr(ds_mod.runner, "read_file", r)
    return r


@pytest.fixture
def c(boss_client, app, reader):
    services = app.state.services
    services.document_scans._today = lambda: TODAY
    store = services.store
    store.save_settings(
        {
            **store.settings(),
            "company_name": "Max Muster IT-Beratung",
            "owner_name": "Max Muster",
            "vat_id": "DE123456789",
            "payment_days": 30,
        }
    )
    return boss_client


def up(client, content: bytes, name: str = "alt.pdf"):
    return client.post("/api/document-scans", files={"file": (name, content)})


def confirm_body(**over):
    data = {
        "kind": "invoice",
        "number": "RE-2024-007",
        "date": "2024-03-05",
        "customer": {"name": "Beispiel Kunde GmbH", "city": "Kundenstadt"},
        "vat_rate": "19",
        # net 750,00 = 10 x 50,00 + 4 x 62,50; VAT 19 % = 142,50; gross 892,50
        "lines": [
            {"description": "Beratung", "quantity": "10", "unit": "Std", "price": "50.00"},
            {"description": "Entwicklung", "quantity": "4", "unit": "Std", "price": "62.50"},
        ],
    }
    data.update(over)
    return data


def test_clean_invoice_is_added(c, reader):
    r = up(c, reader.add(INVOICE))
    assert r.status_code == 201
    out = r.json()
    assert out["status"] == "added"
    assert out["record"] == {"collection": "invoices", "id": out["record"]["id"], "number": "RE-2024-007", "year": 2024}
    assert set(out["scan"]) <= PUBLIC_KEYS | {"record_coll", "record_id"}
    assert not HIDDEN_KEYS & set(out["scan"])
    assert "/" not in out["scan"]["attachment"] and "\\" not in out["scan"]["attachment"]
    assert "sha256" not in out["scan"] and "doc_key" not in out["scan"]
    assert out["scan"]["record_id"] == out["record"]["id"]
    assert c.get(f"/api/invoices/{out['record']['id']}").json()["number"] == "RE-2024-007"
    assert c.get("/api/document-scans").json() == {"items": []}


def test_flagged_scan_is_listed_read_and_confirmed(c, reader):
    rows = swap(INVOICE, "Gesamtbetrag", "Gesamtbetrag 900,00 EUR")  # printed 900,00, computed 892,50
    r = up(c, reader.add(rows))
    assert r.status_code == 201 and r.json()["status"] == "flagged" and "record" not in r.json()
    scan = r.json()["scan"]
    assert [f["code"] for f in scan["flags"]] == ["totals_mismatch"] and scan["flags"][0]["message"]
    assert scan["doc"]["printed_total"] == "900.00"
    assert scan["attachment"] and "/" not in scan["attachment"]
    assert set(scan) == PUBLIC_KEYS and isinstance(scan["created"], int)
    assert not HIDDEN_KEYS & set(scan)
    listed = c.get("/api/document-scans").json()["items"][0]
    assert set(listed) == PUBLIC_KEYS and not HIDDEN_KEYS & set(listed)
    got = c.get(f"/api/document-scans/{scan['id']}").json()
    assert set(got) == PUBLIC_KEYS and not HIDDEN_KEYS & set(got)
    assert [s["id"] for s in c.get("/api/document-scans?status=flagged").json()["items"]] == [scan["id"]]
    assert c.get("/api/document-scans?status=added").status_code == 422
    assert c.get(f"/api/document-scans/{scan['id']}").json()["draft"]["number"] == "RE-2024-007"
    assert c.get("/api/document-scans/nope").status_code == 404

    refused = c.post(f"/api/document-scans/{scan['id']}/confirm", json=confirm_body())
    assert refused.status_code == 409
    assert refused.json()["code"] == "needs_acceptance" and refused.json()["missing"] == ["totals_mismatch"]
    assert refused.json()["detail"]
    assert c.get("/api/invoices").json() == []

    ok = c.post(f"/api/document-scans/{scan['id']}/confirm", json=confirm_body(accept_totals_mismatch=True))
    assert ok.status_code == 200
    body = ok.json()
    assert body["record"]["number"] == "RE-2024-007" and body["record"]["collection"] == "invoices"
    assert body["scan"]["status"] == "added" and body["scan"]["attachment"]
    inv = c.get(f"/api/invoices/{body['record']['id']}").json()
    assert inv["totals_unverified"] is True and inv["given_totals"]["gross"] == 900
    assert c.get("/api/document-scans").json() == {"items": []}
    again = c.post(f"/api/document-scans/{scan['id']}/confirm", json=confirm_body(accept_totals_mismatch=True))
    assert again.status_code == 409


def test_confirm_body_is_validated(c, reader):
    scan = up(c, reader.add(swap(INVOICE, "Rechnungsdatum", None))).json()["scan"]
    url = f"/api/document-scans/{scan['id']}/confirm"
    assert c.post(url, json=confirm_body(vat_rate="12")).status_code == 422
    assert c.post(url, json=confirm_body(lines=[])).status_code == 422
    assert c.post("/api/document-scans/nope/confirm", json=confirm_body()).status_code == 404


def test_discard_removes_a_flagged_scan_but_not_an_added_one(c, reader):
    flagged = up(c, reader.add(swap(INVOICE, "Rechnungsdatum", None))).json()["scan"]
    added = up(c, reader.add(OFFER_BODY)).json()["scan"]
    refused = c.delete(f"/api/document-scans/{added['id']}")
    assert refused.status_code == 409 and refused.json()["code"] == "not_flagged"
    assert c.delete(f"/api/document-scans/{flagged['id']}").status_code == 204
    assert c.get("/api/document-scans").json() == {"items": []}
    assert c.get(f"/api/document-scans/{flagged['id']}").json()["status"] == "discarded"
    assert c.delete("/api/document-scans/nope").status_code == 404


def test_same_file_twice_is_409_with_the_existing_record(c, reader):
    content = reader.add(INVOICE)
    first = up(c, content).json()
    r = up(c, content, "other-name.pdf")
    assert r.status_code == 409
    assert r.json() == {
        "detail": "This file was already added as RE-2024-007.",
        "code": "duplicate_file",
        "existing": {"collection": "invoices", "id": first["record"]["id"], "number": "RE-2024-007"},
    }


def test_same_file_twice_while_flagged_names_the_scan(c, reader):
    content = reader.add(swap(INVOICE, "Rechnungsdatum", None))
    first = up(c, content).json()
    r = up(c, content)
    assert r.status_code == 409
    assert r.json()["code"] == "duplicate_file" and r.json()["existing"] == {"scan_id": first["scan"]["id"]}


def test_same_number_in_another_file_is_409(c, reader):
    first = up(c, reader.add(INVOICE)).json()
    r = up(c, reader.add([*INVOICE, "Seite 1"]))  # same number, other bytes
    assert r.status_code == 409
    assert r.json() == {
        "detail": "A document with the number RE-2024-007 already exists.",
        "code": "duplicate_number",
        "existing": {"collection": "invoices", "id": first["record"]["id"], "number": "RE-2024-007"},
    }
    assert len(c.get("/api/invoices").json()) == 1


def test_png_webp_and_xml_are_refused_with_415(c, reader):
    png = b"\x89PNG\r\n\x1a\n" + b"fake image"
    assert up(c, png, "scan.png").status_code == 415
    assert up(c, b"RIFF\x00\x00\x00\x00WEBPVP8 ", "scan.webp").status_code == 415
    assert up(c, b'<?xml version="1.0"?><Invoice/>', "rechnung.xml").status_code == 415
    assert up(c, b"just text", "note.pdf").status_code == 415  # the content must match the extension
    assert c.get("/api/document-scans").json() == {"items": []}


def test_page_image_of_a_pdf_and_404_otherwise(c, reader):
    scan = up(c, receipt_pdf(pages=2, lines=RECEIPT_LINES)).json()["scan"]
    r = c.get(f"/api/document-scans/{scan['id']}/pages/1")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert "default-src 'none'" in r.headers["content-security-policy"]
    assert c.get(f"/api/document-scans/{scan['id']}/pages/2").status_code == 404
    assert c.get("/api/document-scans/nope/pages/0").status_code == 404


def test_rights(app, c, reader, member_client):
    content = reader.add(INVOICE)
    # an employee has no attachment and no invoice rights at all
    assert up(member_client, content).status_code == 403
    assert member_client.get("/api/document-scans").status_code == 403
    # an accountant may read and upload files, but not write invoices
    r = c.post("/api/users", json={"username": "ada", "name": "Ada Books", "role": "accountant", "password": PASSWORD})
    assert r.status_code == 200, r.text
    acc = _login(app, "ada")
    assert up(acc, content).status_code == 403
    assert acc.post("/api/document-scans/nope/confirm", json=confirm_body()).status_code == 403
    assert acc.delete("/api/document-scans/nope").status_code == 403
    assert acc.get("/api/document-scans").status_code == 200  # reading is allowed
    assert c.get("/api/invoices").json() == []  # nothing was added


def test_routes_also_need_customer_rights():
    """The three fixed roles cannot express "invoices but no customers", so the declared requirements are checked."""
    seen = 0
    for route in document_scans.router.routes:
        reqs = {(a, o) for a, o, _ in declared(route)}
        for method in route.methods - {"HEAD", "OPTIONS"}:
            seen += 1
            if method == "GET":
                assert ("customers", "read") in reqs, route.path
            elif route.path.endswith("/confirm") or route.path == "/document-scans":
                assert ("customers", "write") in reqs, (method, route.path)
    assert seen == 6
