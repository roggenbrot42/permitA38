"""Receipt scan storage and the paid date (docs/specs/receipt-scan.md, WP A and B). Fake demo data only."""

import pytest

from permita38.core.database import list_migrations
from permita38.core.errors import ServiceError
from permita38.core.repository import INTERNAL_COLLECTIONS, TABLES
from permita38.core.service.transfer import manifest

from .repository_registry import REGISTRY, integrity_error, registry_params
from .test_data_import import post_run, snapshot
from .transfer_helpers import export_archive, make_instance, seed


@pytest.fixture(params=registry_params())
def repo(request, tmp_path):
    r = REGISTRY[request.param](tmp_path)
    r.startup()
    yield r
    r.close()


# ------------------------------------------------------------------ schema, repository, migration
def test_table_and_columns_are_declared():
    assert "receipt_scans" in INTERNAL_COLLECTIONS
    assert "receipt_scans" not in manifest.EXCLUDED_TABLES
    assert "receipt_scans" in manifest.exported_collections()
    assert {"paid_on", "invoice_no"} <= set(TABLES["expenses"].column_names)
    cols = TABLES["receipt_scans"].column_names
    for name in ("status", "sha256", "invoice_key", "attachment", "filename", "engine", "draft", "doc", "flags",
                 "expense_id", "created_by"):  # fmt: skip
        assert name in cols
    uniques = [i for i in TABLES["receipt_scans"].indexes if i.unique]
    assert [i.columns for i in uniques] == [("sha256",)]


def test_migration_exists_in_every_dialect():
    for dialect in ("sqlite", "postgresql", "mariadb"):
        assert [m.file_name for m in list_migrations(dialect)][-1] == "0014-receipt_scans.sql"


def test_scan_round_trip_with_json_columns_and_unknown_fields(repo):
    draft = {"date": "2026-03-10", "net": None, "vendor": "Café Müller"}
    doc = {"printed_total": 17.7, "items": [{"d": "x"}], "payment": {"kind": "cash"}}
    row = repo.save(
        "receipt_scans",
        {"status": "flagged", "sha256": "a" * 64, "draft": draft, "doc": doc, "flags": ["total_mismatch"],
         "expense_id": None, "future": {"k": 1}},
    )  # fmt: skip
    got = repo.get("receipt_scans", row["id"])
    assert got["draft"] == draft and got["doc"] == doc and got["flags"] == ["total_mismatch"]
    assert got["future"] == {"k": 1} and "expense_id" not in got
    assert repo.find("receipt_scans", sha256="a" * 64) == [got]


def test_sha256_is_unique_and_expense_id_is_findable(repo):
    repo.save("receipt_scans", {"status": "known", "sha256": "b" * 64, "expense_id": "exp1"})
    with pytest.raises(integrity_error(repo)):
        repo.save("receipt_scans", {"status": "known", "sha256": "b" * 64})
    assert len(repo.find("receipt_scans", expense_id="exp1")) == 1
    assert len(repo.list("receipt_scans")) == 1


def test_expense_keeps_paid_on_and_invoice_no(repo):
    e = repo.save("expenses", {"date": "2026-12-28", "net": 100.0, "paid_on": "2027-01-03", "invoice_no": "R-1/2026"})
    got = repo.get("expenses", e["id"])
    assert got["paid_on"] == "2027-01-03" and got["invoice_no"] == "R-1/2026"
    e2 = repo.save("expenses", {"date": "2026-12-28", "net": 1.0, "paid_on": None})
    assert "paid_on" not in repo.get("expenses", e2["id"])


# ------------------------------------------------------------------ export round trip
def test_export_import_round_trip_keeps_scans_and_expense_fields(tmp_path):
    src_app, src_client = make_instance(tmp_path, "src")
    store = src_app.state.services.store
    seed(store)
    exp = store.save(
        "expenses",
        {"date": "2026-12-28", "net": 100.0, "vat": 19.0, "paid_on": "2027-01-03", "invoice_no": "R-7",
         "external_id": "scan:abc", "attachment": None},
    )  # fmt: skip
    store.save("receipt_scans", {"status": "booked", "sha256": "c" * 64, "expense_id": exp["id"], "engine": "pdf-text",
                                 "doc": {"pages": 1}, "flags": [], "created_by": next(u["id"] for u in store.list("users") if u["username"] == "boss")})  # fmt: skip
    store.save("receipt_scans", {"status": "flagged", "sha256": "d" * 64, "draft": {"vendor": "Demo"}})
    blob = export_archive(src_client, include_accounts=True)
    _, dst_client = make_instance(tmp_path, "dst")
    r = post_run(dst_client, blob)
    assert r.status_code == 200, r.text
    dst_store = dst_client.app.state.services.store
    assert snapshot(dst_store)["records"]["receipt_scans"] == snapshot(store)["records"]["receipt_scans"]
    assert len(dst_store.list("receipt_scans")) == 2
    got = dst_store.get("expenses", exp["id"])
    assert (got["paid_on"], got["invoice_no"], got["external_id"]) == ("2027-01-03", "R-7", "scan:abc")


# ------------------------------------------------------------------ external_id rules
BILL = {"date": "2026-03-10", "net": 100.0, "vat": 19.0, "vat_rate": 19}


def records(app):
    return app.state.services.records


def boss_user(app):
    return next(u for u in app.state.services.store.list("users") if u["username"] == "boss")


def test_a_client_cannot_set_a_scan_external_id(boss_client):
    r = boss_client.post("/api/expenses", json={**BILL, "external_id": "scan:forged"})
    assert r.status_code in (200, 201), r.text
    assert "external_id" not in r.json()
    other = boss_client.post("/api/expenses", json={**BILL, "external_id": "bank:42"}).json()
    assert other["external_id"] == "bank:42"  # other uses are unchanged
    put = boss_client.put(f"/api/expenses/{other['id']}", json={**BILL, "external_id": "scan:forged"}).json()
    assert "external_id" not in put


def test_create_scanned_sets_the_marker_and_a_put_without_the_field_keeps_it(app, boss_client):
    rec = records(app).create_scanned(boss_user(app), {**BILL, "external_id": "evil", "vendor": "Demo"}, "s1abc")
    assert rec["external_id"] == "scan:s1abc"
    for body in ({**BILL}, {**BILL, "external_id": "scan:other"}, {**BILL, "external_id": "plain"}):
        r = boss_client.put(f"/api/expenses/{rec['id']}", json=body)
        assert r.status_code == 200, r.text
        assert r.json()["external_id"] == "scan:s1abc"
    assert boss_client.get(f"/api/expenses/{rec['id']}").json()["external_id"] == "scan:s1abc"


def test_create_scanned_uses_the_normal_write_rules(app, member_client):
    store = app.state.services.store
    user = next(u for u in store.list("users") if u["username"] == "mia")
    with pytest.raises(ServiceError) as err:
        records(app).create_scanned(user, dict(BILL), "s2")
    assert err.value.status == 403
    with pytest.raises(ServiceError) as err:
        records(app).create_scanned(boss_user(app), {**BILL, "vat_rate": 13}, "s3")  # not an allowed rate
    assert err.value.status == 422


def test_deleting_the_expense_deletes_its_scan_row(app, boss_client):
    store = app.state.services.store
    rec = records(app).create_scanned(boss_user(app), dict(BILL), "s4")
    keep = store.save("receipt_scans", {"status": "flagged", "sha256": "e" * 64})
    store.save("receipt_scans", {"status": "booked", "sha256": "f" * 64, "expense_id": rec["id"]})
    deleted = boss_client.delete(f"/api/expenses/{rec['id']}")
    assert deleted.status_code in (200, 204)
    assert [r["id"] for r in store.list("receipt_scans")] == [keep["id"]]


def test_a_plain_expense_is_unchanged(boss_client):
    r = boss_client.post("/api/expenses", json={**BILL, "paid_on": "", "invoice_no": "R-1"})
    assert r.status_code in (200, 201), r.text
    body = r.json()
    assert "external_id" not in body and "paid_on" not in body and body["invoice_no"] == "R-1"
    deleted = boss_client.delete(f"/api/expenses/{body['id']}")
    assert deleted.status_code in (200, 204)


def test_bounds_of_the_new_fields(boss_client):
    assert boss_client.post("/api/expenses", json={**BILL, "paid_on": "not a date"}).status_code == 422
    assert boss_client.post("/api/expenses", json={**BILL, "invoice_no": "x" * 61}).status_code == 422


# ------------------------------------------------------------------ finance: paid date
def book(c, **kw):
    r = c.post("/api/expenses", json={"date": "2026-12-28", "net": 100.0, "vat": 19.0, "vat_rate": 19, **kw})
    assert r.status_code in (200, 201), r.text


def month(c, year, m):
    return c.get("/api/finance", params={"year": year}).json()["months"][m - 1]


@pytest.mark.parametrize("accounting", ["eur", "accrual"])
def test_paid_on_null_gives_todays_figures(boss_client, accounting):
    boss_client.put("/api/settings", json={"accounting": accounting})
    book(boss_client)
    dec = month(boss_client, 2026, 12)
    assert (dec["expenses"], dec["vat_in"]) == (100.0, 19.0)
    assert (month(boss_client, 2027, 1)["expenses"], month(boss_client, 2027, 1)["vat_in"]) == (0, 0)
    assert boss_client.get("/api/finance", params={"year": 2026}).json()["categories"] == {"Other": 100.0}


def test_eur_cost_follows_the_paid_date_and_input_vat_the_bill_date_across_the_year_end(boss_client):
    boss_client.put("/api/settings", json={"accounting": "eur"})
    book(boss_client, paid_on="2027-01-03")
    dec26, jan27 = month(boss_client, 2026, 12), month(boss_client, 2027, 1)
    assert (dec26["expenses"], dec26["vat_in"]) == (0, 19.0)
    assert (jan27["expenses"], jan27["vat_in"]) == (100.0, 0)
    cats26 = boss_client.get("/api/finance", params={"year": 2026}).json()["categories"]
    cats27 = boss_client.get("/api/finance", params={"year": 2027}).json()["categories"]
    assert cats26 == {} and cats27 == {"Other": 100.0}


def test_accrual_books_both_on_the_bill_date(boss_client):
    boss_client.put("/api/settings", json={"accounting": "accrual"})
    book(boss_client, paid_on="2027-01-03")
    dec26, jan27 = month(boss_client, 2026, 12), month(boss_client, 2027, 1)
    assert (dec26["expenses"], dec26["vat_in"]) == (100.0, 19.0)
    assert (jan27["expenses"], jan27["vat_in"]) == (0, 0)


def test_paid_on_in_the_same_year_moves_only_the_cost_month(boss_client):
    boss_client.put("/api/settings", json={"accounting": "eur"})
    book(boss_client, date="2026-03-30", paid_on="2026-04-02")
    mar, apr = month(boss_client, 2026, 3), month(boss_client, 2026, 4)
    assert (mar["expenses"], mar["vat_in"], apr["expenses"], apr["vat_in"]) == (0, 19.0, 100.0, 0)


def test_a_damaged_paid_date_falls_back_to_the_bill_date(app, boss_client):
    app.state.services.store.save("expenses", {"date": "2026-12-28", "net": 100.0, "vat": 19.0, "paid_on": "garbage"})
    dec = month(boss_client, 2026, 12)
    assert (dec["expenses"], dec["vat_in"]) == (100.0, 19.0)


def test_kleinunternehmer_cost_is_gross_on_the_paid_date(boss_client):
    boss_client.put("/api/settings", json={"accounting": "eur", "kleinunternehmer": True})
    book(boss_client, paid_on="2027-01-03")
    assert month(boss_client, 2026, 12)["expenses"] == 0
    jan = month(boss_client, 2027, 1)
    assert (jan["expenses"], jan["vat_in"]) == (119.0, 0)
