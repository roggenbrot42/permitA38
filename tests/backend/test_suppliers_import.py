"""Suppliers and offers through the data archive (kind "suppliers"): the dry run (preview) and the real run agree,
damaged JSON shapes are refused per record, and the expense duplicate rule compares supplier ids when both rows have one."""

import pytest

from .test_data_transfer_review import preview, rebuild, run
from .transfer_helpers import export_archive, make_instance, seed


@pytest.fixture
def src(tmp_path):
    app, client = make_instance(tmp_path, "src")
    store = app.state.services.store
    seed(store)
    sup = store.save(
        "suppliers",
        {
            "name": "Example Freelance Studio (Demo)",
            "kind": "person-capacity",
            "weekly_hours": 24.0,
            "workdays": [0, 1, 2, 3],
            "tags": ["frontend"],
            "country": "DE",
            "vat_mode": "domestic",
            "active": True,
        },
    )
    store.save(
        "suppliers",
        {
            "name": "Demo Print Shop GmbH",
            "kind": "company",
            "supplier_no": "K-1",
            "vat_mode": "domestic",
            "active": True,
        },
    )
    proj = store.list("projects")[0]
    store.save(
        "incoming_offers",
        {
            "supplier_id": sup["id"],
            "received_on": "2026-02-20",
            "lines": [{"description": "Frontend", "quantity": 8, "unit": "h", "unit_price": 80.0}],
            "net_total": 640.0,
            "vat_rate": 19.0,
            "vat": 121.6,
            "gross_total": 761.6,
            "vat_mode": "domestic",
            "hours": 8.0,
            "status": "received",
            "project_id": proj["id"],
            "customer_id": proj.get("customer_id"),
        },
    )
    return app, client, store, sup


@pytest.fixture
def dst(tmp_path):
    app, client = make_instance(tmp_path, "dst")
    return app, client, app.state.services.store


def test_suppliers_dry_run_equals_the_real_run(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, dstore = dst
    blob = export_archive(sc)
    pre = preview(dc, blob)
    assert pre.status_code == 200, pre.text
    counts = pre.json()["counts"]
    assert counts["suppliers"] == 2 and counts["incoming_offers"] == 1
    assert pre.json()["ok"] is True
    done = run(dc, blob)
    assert done.status_code == 200, done.text
    assert len(dstore.list("suppliers")) == counts["suppliers"]
    assert len(dstore.list("incoming_offers")) == counts["incoming_offers"]


def test_damaged_supplier_and_offer_shapes_are_refused_per_record(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, dstore = dst
    blob = export_archive(sc)

    def damage(files):
        import json

        name = next(n for n in files if n.endswith("suppliers.json"))
        body = json.loads(files[name])
        body["records"][0]["tags"] = "not a list"
        files[name] = json.dumps(body).encode()

    bad = rebuild(blob, tmp_path, edit=damage)
    pre = preview(dc, bad)
    assert pre.status_code == 200
    codes = [e["code"] for e in pre.json()["errors"]]
    assert "record_invalid" in codes
    refused = run(dc, bad)
    assert refused.status_code >= 400, refused.text
    assert dstore.list("suppliers") == [] and dstore.list("incoming_offers") == []


def test_expense_duplicate_rule_with_and_without_supplier_id(boss_client):
    a = boss_client.post("/api/suppliers", json={"name": "Alpha GmbH", "kind": "company"}).json()
    b = boss_client.post("/api/suppliers", json={"name": "Beta GmbH", "kind": "company"}).json()
    base = {"date": "2026-03-05", "net": 50, "vat": 0}
    boss_client.post("/api/expenses", json={**base, "vendor": "Shop", "supplier_id": a["id"]})
    # both rows have a supplier: the ids decide, the spelling does not matter
    other = boss_client.post("/api/expenses", json={**base, "vendor": "shop", "supplier_id": b["id"]}).json()
    assert not any(w["code"] == "expense.possible_duplicate" for w in other["sanity_warnings"])
    same = boss_client.post("/api/expenses", json={**base, "vendor": "SHOP", "supplier_id": a["id"]}).json()
    assert any(w["code"] == "expense.possible_duplicate" for w in same["sanity_warnings"])
    # one row without a supplier: the vendor text decides; the linked row's vendor is its supplier's name (snapshot)
    plain = boss_client.post("/api/expenses", json={**base, "vendor": "alpha gmbh"}).json()
    assert any(w["code"] == "expense.possible_duplicate" for w in plain["sanity_warnings"])
