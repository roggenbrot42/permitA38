"""Import and records: second review round (skipped rows, expenses, transactions, line mode, forged fields)."""

from datetime import date

import pytest

from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.importer import ImportService
from permita38.core.service.numbering import NumberingService

HEAD = "Nummer;Datum;Kunde;Netto;USt;Brutto\n"


@pytest.fixture
def store(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    yield repo
    repo.close()


@pytest.fixture
def svc(store):
    return ImportService(store, numbering=NumberingService(store, clock=lambda: date(2026, 5, 5)))


def _b(text: str) -> bytes:
    return text.encode("utf-8")


def test_skipped_rows_do_not_create_their_customer(store, svc):
    svc.run(_b(HEAD + "RE-2026-0001;01.01.2026;Acme;100;19;119\n"), "x.csv", "invoices")
    report = svc.run(_b(HEAD + "RE-2026-0001;01.01.2026;Ghost Kunde GmbH;100;19;119\n"), "x.csv", "invoices")
    assert report["counts"]["skip"] == 1 and report["counts"]["customers_created"] == 0
    assert [c["name"] for c in store.list("customers")] == ["Acme"]
    ext = HEAD.replace("Brutto", "Brutto;Externe ID")
    svc.run(_b(ext + "N-1;01.01.2026;Acme;10;1,90;11,90;X1\n"), "x.csv", "invoices")
    report = svc.run(_b(ext + "N-2;01.01.2026;Phantom AG;10;1,90;11,90;X1\n"), "x.csv", "invoices")
    assert report["counts"]["skip"] == 1
    assert "Phantom AG" not in [c["name"] for c in store.list("customers")]


def test_gross_net_vat_of_expenses_must_agree(svc):
    head = "Datum;Lieferant;Netto;USt;Brutto\n"

    def plan(csv_text, **opts):
        return svc.plan(_b(csv_text), "e.csv", "expenses", options=opts or None)["rows"][0]

    row = plan(head + "01.01.2026;Cafe;100;19;999\n")
    assert row["action"] == "error" and "gross: file 999.00, computed 119.00" in row["messages"][0]
    ok = plan(head + "01.01.2026;Cafe;100;19;999\n", accept_unverified=True)
    assert ok["action"] == "create" and "unverified" in ok["messages"][0]
    assert plan(head + "01.01.2026;Cafe;100;19;119\n")["action"] == "create"
    bad_rate = plan(head + "01.01.2026;Cafe;100;37;137\n")
    assert bad_rate["action"] == "error" and "VAT rate" in bad_rate["messages"][0]


def test_gross_only_expense_file_is_imported(store, svc):
    report = svc.run(_b("Datum;Lieferant;Brutto\n01.01.2026;Cafe;119,00\n"), "e.csv", "expenses")
    assert report["applied"] is True
    exp = store.list("expenses")[0]
    assert (exp["net"], exp["vat"], exp["vat_rate"]) == (100.0, 19.0, 19.0)


def test_plan_is_built_inside_the_write_transaction(store, svc, monkeypatch):
    depth = []
    real_build = svc._build

    def spy(*a, **k):
        depth.append(store._depth)  # > 0: a transaction is open while the existing numbers are read
        return real_build(*a, **k)

    monkeypatch.setattr(svc, "_build", spy)
    svc.run(_b(HEAD + "A-1;01.01.2026;Acme;10;1,90;11,90\n"), "x.csv", "invoices")
    svc.plan(_b(HEAD + "A-2;01.01.2026;Acme;10;1,90;11,90\n"), "x.csv", "invoices")
    assert depth[0] > 0 and depth[1] == 0


def test_line_mode_total_cap_and_inconsistent_member_rows(svc):
    head = "Nummer;Datum;Kunde;Position;Menge;Preis\n"

    def first(csv_text):
        return svc.plan(_b(head + csv_text), "x.csv", "invoices")["rows"][0]

    big = first("L-1;01.01.2026;Acme;A;1;900000000\nL-1;01.01.2026;Acme;B;1;900000000\n")
    assert big["action"] == "error" and "too large" in big["messages"][0]
    date_diff = first("L-2;01.01.2026;Acme;A;1;5\nL-2;02.01.2026;Acme;B;1;5\n")
    assert date_diff["action"] == "error" and "another date" in date_diff["messages"][0]
    cust_diff = first("L-3;01.01.2026;Acme;A;1;5\nL-3;01.01.2026;Other Corp;B;1;5\n")
    assert cust_diff["action"] == "error" and "another customer" in cust_diff["messages"][0]
    assert first("L-4;01.01.2026;Acme;A;1;5\nL-4;;;B;1;5\n")["action"] == "create"  # blank = same as first row


def test_legacy_pattern_does_not_block_every_settings_save(boss_client, app):
    c = boss_client
    store = app.state.services.store
    legacy = "RE+{year}({n:04d}"
    store.save_settings({**store.settings(), "invoice_pattern": legacy})
    current = c.get("/api/settings").json()
    assert current["invoice_pattern"] == legacy
    r = c.put("/api/settings", json={**current, "company_name": "Renamed Studio"})
    assert r.status_code == 200, r.text
    assert c.get("/api/settings").json()["company_name"] == "Renamed Studio"
    assert c.put("/api/settings", json={"invoice_pattern": "RE-{foo}"}).status_code == 422
    assert c.put("/api/settings", json={"invoice_pattern": "X-{n}"}).status_code == 400  # only via /api/numbering


def test_clients_cannot_forge_import_fields(boss_client):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "Demo GmbH"}).json()
    forged = {
        "customer_id": cust["id"],
        "lines": [{"description": "x", "qty": 1, "price": 1}],
        "imported": True,
        "original_pdf": "evil.pdf",
        "original_pdf_sha256": "0" * 64,
        "import_batch": "b",
    }
    created = c.post("/api/invoices", json=forged).json()
    for k in ("imported", "original_pdf", "original_pdf_sha256", "import_batch"):
        assert k not in created, k
    updated = c.put(f"/api/invoices/{created['id']}", json={**forged, "date": "2026-01-02"}).json()
    assert not ({"imported", "original_pdf", "import_batch"} & set(updated))


def test_put_without_date_keeps_the_documents_date(boss_client):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "Demo GmbH"}).json()
    body = {"customer_id": cust["id"], "date": "2026-02-03", "lines": [{"description": "x", "qty": 1, "price": 1}]}
    doc = c.post("/api/invoices", json=body).json()
    body.pop("date")
    assert c.put(f"/api/invoices/{doc['id']}", json=body).json()["date"] == "2026-02-03"


def test_numbered_invoice_delete_is_409(boss_client):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "Demo GmbH"}).json()
    doc = c.post(
        "/api/invoices", json={"customer_id": cust["id"], "lines": [{"description": "x", "qty": 1, "price": 1}]}
    ).json()
    assert c.post(f"/api/invoices/{doc['id']}/finalize").status_code == 200
    assert c.delete(f"/api/invoices/{doc['id']}").status_code == 409
    assert c.get(f"/api/invoices/{doc['id']}").status_code == 200
