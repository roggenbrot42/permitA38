"""Suppliers over HTTP (docs/contracts/subcontractors.md, "Suppliers", "Shared types", "Expenses (links)"): master
data rules, the limited employee view, vendor suggestions and the adoption of expense vendors."""

import pytest


def make_supplier(c, **kw):
    body = {"name": "Demo Print Shop GmbH", "kind": "company", **kw}
    r = c.post("/api/suppliers", json=body)
    return r


def expense(c, vendor, net, **kw):
    r = c.post("/api/expenses", json={"date": "2026-03-05", "net": net, "vendor": vendor, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_create_returns_stored_record_with_derived_fields(boss_client):
    r = make_supplier(boss_client, iban="DE89 3704 0044 0532 0130 00", country="AT")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["iban"] == "DE89370400440532013000"  # spaces removed when stored
    assert body["vat_mode"] == "eu_reverse_charge"  # derived from the country
    assert body["sanity_warnings"] == []


def test_bad_iban_format_and_capacity_rules_are_422(boss_client):
    assert make_supplier(boss_client, iban="XX").status_code == 422
    assert make_supplier(boss_client, weekly_hours=10).status_code == 422  # capacity field on a company
    cap = boss_client.post("/api/suppliers", json={"name": "Person Capacity", "kind": "person-capacity"})
    assert cap.status_code == 422  # weekly_hours required for a subcontractor
    assert make_supplier(boss_client, login_user_id="u1b2c3d4").status_code == 422


def test_duplicate_supplier_number_is_a_warning_not_an_error(boss_client):
    assert make_supplier(boss_client, name="First", supplier_no="K-100").status_code == 201
    second = make_supplier(boss_client, name="Second", supplier_no="k-100")
    assert second.status_code == 201
    codes = [w["code"] for w in second.json()["sanity_warnings"]]
    assert codes == ["supplier.duplicate_number"]


def test_list_filters_and_sort_by_name(boss_client):
    make_supplier(boss_client, name="beta", tags=["print"], contact="Anna")
    make_supplier(boss_client, name="Alpha", tags=["Hosting"])
    make_supplier(boss_client, name="Gamma", active=False)
    names = [s["name"] for s in boss_client.get("/api/suppliers").json()]
    assert names == ["Alpha", "beta", "Gamma"]
    assert [s["name"] for s in boss_client.get("/api/suppliers?tag=hosting").json()] == ["Alpha"]
    assert [s["name"] for s in boss_client.get("/api/suppliers?active=false").json()] == ["Gamma"]
    assert [s["name"] for s in boss_client.get("/api/suppliers?q=ANNA").json()] == ["beta"]
    assert boss_client.get("/api/suppliers?kind=unknown").status_code == 422


def test_employee_sees_the_limited_view_only(boss_client, member_client):
    make_supplier(
        boss_client,
        name="Example Freelance Studio",
        kind="company",
        iban="DE89370400440532013000",
        contact="Secret Person",
        tags=["frontend"],
    )
    rows = member_client.get("/api/suppliers").json()
    assert rows == [
        {
            "id": rows[0]["id"],
            "name": "Example Freelance Studio",
            "kind": "company",
            "tags": ["frontend"],
            "active": True,
        }
    ]
    detail = member_client.get(f"/api/suppliers/{rows[0]['id']}").json()
    assert "iban" not in detail and "contact" not in detail
    # q matches the name only for the employee
    assert member_client.get("/api/suppliers?q=secret").json() == []


def test_vendor_suggestions_group_by_normalised_name(boss_client):
    s = make_supplier(boss_client).json()
    expense(boss_client, "Demo Print Shop GmbH", 10)
    expense(boss_client, "demo print shop", 20)
    expense(boss_client, "Example Hosting", 9.9)
    body = boss_client.get("/api/suppliers/vendor-suggestions").json()
    assert body["unlinked_expenses"] == 3
    first, second = body["groups"]
    assert first["key"] == "demo print shop" and first["count"] == 2 and first["net_total"] == 30.0
    assert first["variants"] == ["Demo Print Shop GmbH", "demo print shop"]
    assert first["supplier"] == {"id": s["id"], "name": "Demo Print Shop GmbH"}
    assert second["key"] == "example hosting" and second["supplier"] is None and second["net_total"] == 9.9


def test_adopt_vendors_dry_run_equals_real_run_and_is_idempotent(boss_client):
    s = make_supplier(boss_client).json()
    expense(boss_client, "Demo Print Shop GmbH", 10)
    expense(boss_client, "demo print shop", 20)
    expense(boss_client, "Example Hosting", 9.9)
    items = [
        {"vendors": ["Demo Print Shop GmbH", "demo print shop"], "supplier_id": s["id"]},
        {"vendors": ["Example Hosting"], "create": {"name": "Example Hosting", "kind": "company"}},
    ]
    dry = boss_client.post("/api/suppliers/adopt-vendors", json={"dry_run": True, "items": items}).json()
    assert dry["dry_run"] is True and dry["expenses_changed"] == 3
    assert dry["items"][0]["count"] == 2 and dry["items"][0]["supplier_created"] is False
    assert dry["items"][1]["would_create"] is True and dry["items"][1]["supplier_id"] is None
    assert all(e.get("supplier_id") is None for e in boss_client.get("/api/expenses").json())  # nothing written

    real = boss_client.post("/api/suppliers/adopt-vendors", json={"dry_run": False, "items": items}).json()
    assert [i["count"] for i in real["items"]] == [i["count"] for i in dry["items"]]
    created = real["items"][1]
    assert created["supplier_created"] is True and created["supplier_id"]
    vendors = sorted(e["vendor"] for e in boss_client.get("/api/expenses").json())
    assert vendors == ["Demo Print Shop GmbH", "Example Hosting", "demo print shop"]  # text untouched

    again = boss_client.post("/api/suppliers/adopt-vendors", json={"dry_run": False, "items": items}).json()
    assert again["expenses_changed"] == 0
    assert [i["count"] for i in again["items"]] == [0, 0]
    names = [s["name"] for s in boss_client.get("/api/suppliers").json()]
    assert names.count("Example Hosting") == 1


def test_adopt_vendors_body_rules(boss_client):
    both = {"vendors": ["x"], "supplier_id": "5d1c0aa2", "create": {"name": "X", "kind": "company"}}
    assert boss_client.post("/api/suppliers/adopt-vendors", json={"items": [both]}).status_code == 422
    unknown = {"vendors": ["x"], "supplier_id": "5d1c0aa2"}
    r = boss_client.post("/api/suppliers/adopt-vendors", json={"items": [unknown]})
    assert r.status_code == 404 and r.json() == {"detail": "Supplier not found"}


def test_expense_links_are_checked(boss_client):
    s = make_supplier(boss_client).json()
    bad = boss_client.post("/api/expenses", json={"date": "2026-03-05", "net": 5, "supplier_id": "5d1c0aa2"})
    assert bad.status_code == 422 and bad.json() == {"detail": "Unknown supplier"}
    no_sup = boss_client.post("/api/expenses", json={"date": "2026-03-05", "net": 5, "allocation_id": "5d1c0aa2x"})
    assert no_sup.status_code == 422 and no_sup.json() == {"detail": "allocation_id needs a supplier"}
    linked = boss_client.post(
        "/api/expenses", json={"date": "2026-03-05", "net": 5, "vendor": "typed", "supplier_id": s["id"]}
    )
    assert linked.status_code == 200 and linked.json()["vendor"] == "Demo Print Shop GmbH"


def test_delete_unreferenced_supplier_and_archive_referenced_one(boss_client):
    s = make_supplier(boss_client, name="Lonely").json()
    assert boss_client.delete(f"/api/suppliers/{s['id']}").status_code == 204
    assert boss_client.get(f"/api/suppliers/{s['id']}").status_code == 404
    kept = make_supplier(boss_client, name="Used").json()
    expense(boss_client, "Used", 5, supplier_id=kept["id"])
    assert boss_client.delete(f"/api/suppliers/{kept['id']}").status_code == 409
    archived = boss_client.put(
        f"/api/suppliers/{kept['id']}", json={"name": "Used", "kind": "company", "active": False}
    )
    assert archived.status_code == 200 and archived.json()["active"] is False


@pytest.mark.parametrize("kind", ["company", "person"])
def test_plain_suppliers_have_no_capacity(boss_client, kind):
    r = boss_client.post("/api/suppliers", json={"name": f"Plain {kind}", "kind": kind, "rate": 80.0})
    assert r.status_code == 422
