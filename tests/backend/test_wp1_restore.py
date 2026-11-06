"""Restore of data written before migration project_planning (head e4b70dba): the upgrade hook and the legacy JSON
import map ``personal`` to ``kind``, drop the second hour field and fill missing statuses; a row that still violates a
guard is restored with a warning carrying the guard code."""

import json

import pytest

from permita38.core.repository.importer import import_json
from permita38.core.repository.sqlite import SQLiteRepository

from .transfer_helpers import build_zip, export_archive, make_instance, seed, zip_files

PREVIOUS_HEAD = "e4b70dba"


@pytest.fixture
def source(tmp_path):
    app, client = make_instance(tmp_path, "src")
    return app, client, seed(app.state.services.store)


@pytest.fixture
def target(tmp_path):
    app, client = make_instance(tmp_path, "dst")
    return app, client, app.state.services.store


def old_archive(client, tmp_path, rows: dict[str, list[dict]], ids: dict):
    """The export of ``source`` re-labelled as written by the previous head, with ``rows`` appended."""
    files = zip_files(export_archive(client))
    for coll, extra in rows.items():
        name = f"data/{coll}.json"
        body = json.loads(files[name]) if name in files else {"records": []}
        recs = body["records"]
        for rec in recs:
            if coll == "projects":
                rec.pop("kind", None)  # the previous head had no kind
        recs.extend(extra)
        body["count"] = len(recs)
        files[name] = json.dumps(body).encode()
    return build_zip(files, tmp_path / "old.zip", {"schema_head": PREVIOUS_HEAD})


def project_rows(customer_id):
    return [
        {"id": "pers0001", "name": "Personal plain", "personal": True},
        {"id": "pers0002", "name": "Personal with customer", "personal": True, "customer_id": customer_id},
        {"id": "pers0003", "name": "No customer, has a contract", "personal": False, "status": "paused"},
        {"id": "pers0004", "name": "No status", "personal": False},
        {"id": "pers0005", "name": "Empty customer", "customer_id": "", "status": "done"},
    ]


def contract_row(**kw):
    return {
        "id": "cont0001",
        "title": "Support",
        "project_id": "pers0003",
        "start": "2026-03-02",
        "end": "2026-03-27",
        "hours_per_week": 10,
        "total_hours": 50,
        **kw,
    }


def run(client, path):
    return client.post("/api/data/import/run", files={"file": ("a.zip", path.read_bytes())}, data={"confirm": "true"})


def check_planning(get):
    """The expectations of both restore paths; ``get(coll, id)`` returns the stored record."""
    kinds = {i: get("projects", i)["kind"] for i in ("pers0001", "pers0002", "pers0003", "pers0004", "pers0005")}
    assert kinds == {
        "pers0001": "internal",
        "pers0002": "customer",
        "pers0003": "customer",
        "pers0004": "internal",
        "pers0005": "internal",
    }
    assert get("projects", "pers0003")["status"] == "paused"
    assert get("projects", "pers0004")["status"] == "active"
    assert get("projects", "pers0005")["status"] == "done"
    for pid in kinds:
        assert "personal" not in get("projects", pid)
    c = get("contracts", "cont0001")
    assert c["hours_per_week"] == 10 and c.get("total_hours") is None
    assert c["status"] == "confirmed"


def test_transfer_archive_of_the_previous_head_is_upgraded(source, target, tmp_path):
    _, src_client, ids = source
    _, dst_client, dst = target
    rows = {"projects": project_rows(ids["customer"]), "contracts": [contract_row()]}
    r = run(dst_client, old_archive(src_client, tmp_path, rows, ids))
    assert r.status_code == 200, r.text
    check_planning(dst.get)
    assert dst.get("projects", ids["project"])["kind"] == "customer"  # has a customer
    assert dst.get("projects", "internaladmin")["name"] == "Administration"  # the migration's, not invented


def test_allocation_with_both_hour_fields_keeps_only_hours_per_week(source, target, tmp_path):
    _, src_client, ids = source
    _, dst_client, dst = target
    user = dst.list("users")[0]["id"]
    alloc = {
        "id": "alloc001",
        "user_id": user,
        "project_id": ids["project"],
        "start": "2026-03-02",
        "end": "2026-03-27",
        "hours_per_week": 20,
        "total_hours": 80,
    }
    r = run(dst_client, old_archive(src_client, tmp_path, {"allocations": [alloc]}, ids))
    assert r.status_code == 200, r.text
    got = dst.get("allocations", "alloc001")
    assert got["hours_per_week"] == 20 and got.get("total_hours") is None and got["status"] == "active"


def test_row_that_still_violates_a_guard_is_restored_with_a_warning(source, target, tmp_path):
    _, src_client, ids = source
    _, dst_client, dst = target
    row = contract_row(id="cont0002", project_id=ids["project"])
    row.pop("hours_per_week")
    row["total_hours"] = 0  # the upgrade leaves it alone (no second hour field); the range guard would refuse it
    r = run(dst_client, old_archive(src_client, tmp_path, {"contracts": [row]}, ids))
    assert r.status_code == 200, r.text
    assert dst.get("contracts", "cont0002")["total_hours"] == 0
    warned = [w for w in r.json()["warnings"] if w["code"] == "range.bounds"]
    assert warned and "cont0002" in json.dumps(warned)


def write_legacy(folder, coll, rec):
    (folder / coll).mkdir(parents=True, exist_ok=True)
    (folder / coll / f"{rec['id']}.json").write_text(json.dumps(rec), encoding="utf-8")


def test_legacy_json_folder_is_normalised(tmp_path):
    old = tmp_path / "old"
    write_legacy(old, "customers", {"id": "cust0001", "name": "Beispiel GmbH"})
    for row in project_rows("cust0001"):
        write_legacy(old, "projects", row)
    write_legacy(old, "contracts", contract_row())
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    try:
        report = import_json(repo, old)
        assert report.imported["projects"] == 5
        check_planning(repo.get)
    finally:
        repo.close()
