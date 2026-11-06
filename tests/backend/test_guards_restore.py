"""Verbatim restore: guards are suspended for date/non_negative/order only, the number guard never is."""

import json
import logging

import pytest

from permita38.core.repository.importer import ImportRefused, drop_empty_number, import_json
from permita38.core.repository.sqlite import SQLiteRepository

from .transfer_helpers import build_zip, export_archive, make_instance, seed, zip_files


def triggers(store) -> set[str]:
    with store.lock:
        return {r[0] for r in store._rows("SELECT name FROM sqlite_master WHERE type = 'trigger'")}


@pytest.fixture
def source(tmp_path):
    app, client = make_instance(tmp_path, "src")
    return app, client, seed(app.state.services.store)


@pytest.fixture
def target(tmp_path):
    app, client = make_instance(tmp_path, "dst")
    return app, client, app.state.services.store


def post_run(client, path):
    return client.post("/api/data/import/run", files={"file": ("a.zip", path.read_bytes())}, data={"confirm": "true"})


def _records(body):
    return body["records"] if isinstance(body, dict) else body


def edited_archive(client, tmp_path, edit):
    files = zip_files(export_archive(client))
    for name in ("data/invoices.json", "data/offers.json"):
        body = json.loads(files[name])
        edit(name, _records(body))
        body["count"] = len(body["records"])
        files[name] = json.dumps(body).encode()
    return build_zip(files, tmp_path / "edited.zip")


def test_reversed_period_is_restored_with_a_warning_and_the_guards_stay(source, target, tmp_path):
    src_app, src_client, ids = source
    src = src_app.state.services.store
    with src.transaction(), src.guards_suspended({"order"}):
        src.save("projects", {**src.get("projects", ids["project"]), "start": "2026-05-01", "end": "2026-04-01"})
    _, dst_client, dst = target
    before = triggers(dst)
    assert before
    path = tmp_path / "rev.zip"
    path.write_bytes(export_archive(src_client))
    r = post_run(dst_client, path)
    assert r.status_code == 200, r.text
    assert dst.get("projects", ids["project"])["end"] == "2026-04-01"
    assert [w["code"] for w in r.json()["warnings"]].count("state.period_reversed") == 1
    assert triggers(dst) == before


def test_duplicate_invoice_number_is_refused_and_nothing_is_written(source, target, tmp_path):
    _, src_client, _ = source
    _, dst_client, dst = target

    def edit(name, recs):
        if name == "data/invoices.json":
            recs.append({**recs[0], "id": "inv00000002"})

    r = post_run(dst_client, edited_archive(src_client, tmp_path, edit))
    assert r.status_code == 422, r.text
    errs = r.json()["errors"]
    assert [e["code"] for e in errs] == ["numbering.duplicate"]
    assert "RE-2026-0007" in errs[0]["message"]
    assert "permita38 check-db" in errs[0]["message"]
    assert dst.list("invoices") == [] and dst.list("customers") == []


def test_duplicate_offer_number_is_refused_and_nothing_is_written(source, target, tmp_path):
    _, src_client, _ = source
    _, dst_client, dst = target

    def edit(name, recs):
        if name == "data/offers.json":
            recs.append({"id": "off00000001", "number": "AN-2026-0001"})
            recs.append({"id": "off00000002", "number": "AN-2026-0001"})

    r = post_run(dst_client, edited_archive(src_client, tmp_path, edit))
    assert r.status_code == 422, r.text
    errs = r.json()["errors"]
    assert [e["code"] for e in errs] == ["numbering.duplicate"]
    assert "AN-2026-0001" in errs[0]["message"]
    assert dst.list("offers") == [] and dst.list("customers") == []


def test_same_number_in_an_invoice_and_an_offer_is_fine(source, target, tmp_path):
    _, src_client, _ = source

    def edit(name, recs):
        if name == "data/offers.json":
            recs.append({"id": "off00000001", "number": "RE-2026-0007"})

    r = post_run(target[1], edited_archive(src_client, tmp_path, edit))
    assert r.status_code == 200, r.text
    assert target[2].get("offers", "off00000001")["number"] == "RE-2026-0007"


def test_empty_number_is_dropped(source, target, tmp_path):
    _, src_client, _ = source

    def edit(name, recs):
        if name == "data/invoices.json":
            recs.append({**recs[0], "id": "inv00000002", "number": ""})
            recs.append({**recs[0], "id": "inv00000003", "number": ""})

    r = post_run(target[1], edited_archive(src_client, tmp_path, edit))
    assert r.status_code == 200, r.text
    assert "number" not in target[2].get("invoices", "inv00000002")
    assert "number" not in target[2].get("invoices", "inv00000003")
    rec = {"number": ""}
    drop_empty_number(rec)
    assert rec == {}


def test_failing_restore_leaves_the_guards_in_place(source, target, tmp_path, monkeypatch):
    _, src_client, _ = source
    _, dst_client, dst = target
    before = triggers(dst)

    def boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(dst, "save_settings", boom)
    path = tmp_path / "ok.zip"
    path.write_bytes(export_archive(src_client))
    try:
        r = post_run(dst_client, path)
        assert r.status_code >= 500
    except RuntimeError:
        pass
    assert triggers(dst) == before
    assert dst.list("customers") == []


# ------------------------------------------------------------------ import-json
def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


def inv(folder, rid, number):
    write(folder / "invoices" / f"{rid}.json", {"id": rid, "number": number})


def test_import_json_refuses_a_duplicate_inside_the_folder(tmp_path):
    old = tmp_path / "old"
    write(old / "customers" / "cust00000001.json", {"id": "cust00000001", "name": "ACME"})
    inv(old, "inv000001", "RE-2026-0001")
    inv(old, "inv000002", "RE-2026-0001")
    repo = SQLiteRepository(tmp_path / "new")
    with pytest.raises(ValueError, match="RE-2026-0001") as e:
        import_json(repo, old)
    assert isinstance(e.value, ImportRefused) and e.value.code == "numbering.duplicate"
    assert "permita38 check-db" in str(e.value)
    assert repo.list("invoices") == [] and repo.list("customers") == []


def test_import_json_refuses_a_duplicate_offer_number(tmp_path):
    old = tmp_path / "old"
    write(old / "customers" / "cust00000001.json", {"id": "cust00000001", "name": "ACME"})
    for rid in ("offr000001", "offr000002"):
        write(old / "offers" / f"{rid}.json", {"id": rid, "number": "AN-2026-0001"})
    repo = SQLiteRepository(tmp_path / "new")
    with pytest.raises(ImportRefused, match="AN-2026-0001") as e:
        import_json(repo, old)
    assert e.value.code == "numbering.duplicate"
    assert "permita38 check-db" in str(e.value)
    assert repo.list("offers") == [] and repo.list("customers") == []


def test_import_json_same_id_and_number_twice_is_not_a_duplicate(tmp_path):
    old = tmp_path / "old"
    write(old / "invoices" / "inv000001.json", {"id": "inv000001", "number": "RE-2026-0001"})
    write(old / "invoices" / "inv000001b.json", {"id": "inv000001", "number": "RE-2026-0001"})
    repo = SQLiteRepository(tmp_path / "new")
    repo.startup()
    import_json(repo, old)
    assert [r["id"] for r in repo.list("invoices")] == ["inv000001"]


def test_import_json_refuses_a_duplicate_against_a_stored_invoice(tmp_path):
    repo = SQLiteRepository(tmp_path / "new")
    repo.save("invoices", {"number": "RE-2026-0001", "title": "inv001"})
    old = tmp_path / "old"
    write(old / "customers" / "cust00000001.json", {"id": "cust00000001", "name": "ACME"})
    inv(old, "inv000002", "RE-2026-0001")
    with pytest.raises(ValueError, match="RE-2026-0001"):
        import_json(repo, old)
    assert len(repo.list("invoices")) == 1 and repo.list("customers") == []


def test_import_json_imports_a_valid_folder_with_warnings_and_keeps_the_guards(tmp_path, caplog):
    old = tmp_path / "old"
    inv(old, "inv000001", "RE-2026-0001")
    inv(old, "inv000002", "")
    inv(old, "inv000003", "")
    write(old / "offers" / "offr000001.json", {"id": "offr000001", "number": "RE-2026-0001"})
    write(
        old / "projects" / "proj00000001.json",
        {"id": "proj00000001", "name": "P", "start": "2026-05-01", "end": "2026-04-01"},
    )
    repo = SQLiteRepository(tmp_path / "new")
    repo.startup()
    before = triggers(repo)
    with caplog.at_level(logging.WARNING):
        rep = import_json(repo, old)
    assert rep.imported["invoices"] == 3 and rep.imported["offers"] == 1
    assert "number" not in repo.get("invoices", "inv000002")
    assert [w["code"] for w in rep.warnings] == ["state.period_reversed"]
    assert "state.period_reversed" in caplog.text and "proj00000001" in caplog.text
    assert triggers(repo) == before


def test_restore_warns_about_a_violated_required_guard_and_the_trigger_is_back(source, target, tmp_path, monkeypatch):
    from permita38.core.repository import schema
    from permita38.core.repository.schema import Guard

    guard = Guard("guard_customers_name_required", "customers", "required", ("name",), "state.required")
    monkeypatch.setattr(schema, "GUARDS", (*schema.GUARDS, guard))
    _, src_client, _ = source
    _, dst_client, dst = target
    path = tmp_path / "req.zip"

    files = zip_files(export_archive(src_client))
    body = json.loads(files["data/customers.json"])
    rec = _records(body)[0]
    rec["name"] = ""
    files["data/customers.json"] = json.dumps(body).encode()
    path.write_bytes(build_zip(files, tmp_path / "req2.zip").read_bytes())
    before = triggers(dst)
    r = post_run(dst_client, path)
    assert r.status_code == 200, r.text
    warns = [w for w in r.json()["warnings"] if w["code"] == "state.required"]
    assert len(warns) == 1
    assert f"customers {rec['id']}: name:" in warns[0]["message"]
    assert triggers(dst) == before


# ------------------------------------------------------------------ reference guards (ref.dangling / ref.in_use)
def edit_allocations(client, tmp_path, edit):
    files = zip_files(export_archive(client))
    body = json.loads(files["data/allocations.json"])
    edit(_records(body))
    body["count"] = len(body["records"])
    files["data/allocations.json"] = json.dumps(body).encode()
    return build_zip(files, tmp_path / "alloc.zip")


def test_transfer_with_dangling_allocation_references_imports_with_a_warning(source, target, tmp_path):
    _, src_client, ids = source
    _, dst_client, dst = target
    before = triggers(dst)
    assert "guard_allocations_project_id_ref_del" in before

    def edit(recs):
        recs[0]["project_id"] = "prj00000404"
        recs[0]["user_id"] = "usr00000404"

    r = post_run(dst_client, edit_allocations(src_client, tmp_path, edit))
    assert r.status_code == 200, r.text
    alloc = dst.list("allocations")[0]
    assert (alloc["project_id"], alloc["user_id"]) == ("prj00000404", "usr00000404")
    aid = alloc["id"]
    dangling = [w for w in r.json()["warnings"] if w["code"].startswith("ref.")]
    assert sorted(w["code"] for w in dangling) == ["ref.dangling", "ref.dangling"]
    found = set()
    for w in dangling:
        head, field, _ = w["message"].split(": ", 2)
        coll, rid = head.split(" ")
        found.add((coll, rid, field))
    assert found == {("allocations", aid, "project_id"), ("allocations", aid, "user_id")}
    assert triggers(dst) == before
    assert {"guard_allocations_project_id_ref_del", "guard_allocations_user_id_ref_del"} <= triggers(dst)


def test_restore_warns_only_for_guarded_references(source, target, tmp_path):
    """An invoice with a dangling customer_id (an unguarded column) gives no reference warning."""
    _, src_client, ids = source
    _, dst_client, dst = target

    def edit(name, recs):
        if name == "data/invoices.json":
            recs[0]["customer_id"] = "cust00000404"

    r = post_run(dst_client, edited_archive(src_client, tmp_path, edit))
    assert r.status_code == 200, r.text
    assert dst.list("invoices")[0]["customer_id"] == "cust00000404"
    codes = [w["code"] for w in r.json()["warnings"]]
    assert not [c for c in codes if c.startswith("ref.")]
    assert "ref.dangling_audit" not in codes and "ref.dangling" not in codes


def test_users_are_written_before_the_allocations(source, target, tmp_path, monkeypatch):
    _, src_client, ids = source
    _, dst_client, dst = target
    path = tmp_path / "ok.zip"
    path.write_bytes(export_archive(src_client))
    writes = []
    real_save, real_delete = dst.save, dst.delete

    def save(coll, rec, *a, **k):
        writes.append((coll, rec.get("id")))
        return real_save(coll, rec, *a, **k)

    def delete(coll, rid, *a, **k):
        writes.append((coll, rid))
        return real_delete(coll, rid, *a, **k)

    monkeypatch.setattr(dst, "save", save)
    monkeypatch.setattr(dst, "delete", delete)
    r = post_run(dst_client, path)
    assert r.status_code == 200, r.text
    colls = [c for c, _ in writes]
    assert "users" in colls and "allocations" in colls
    assert colls.index("users") < colls.index("allocations")
    assert max(i for i, c in enumerate(colls) if c == "users") < colls.index("allocations")
    assert "ref.dangling" not in [w["code"] for w in r.json()["warnings"]]
    assert len(dst.list("allocations")) == 1


def test_import_json_with_a_dangling_reference_imports_with_a_warning(tmp_path):
    old = tmp_path / "old"
    write(old / "projects" / "prj000001.json", {"id": "prj000001", "name": "Demo"})
    write(
        old / "entries" / "ent000001.json",
        {"id": "ent000001", "project_id": "prj000404", "date": "2026-03-02", "minutes": 30},
    )
    repo = SQLiteRepository(tmp_path / "new")
    repo.startup()
    before = triggers(repo)
    report = import_json(repo, old)
    assert repo.get("entries", "ent000001")["project_id"] == "prj000404"
    assert [(w["code"], w["collection"], w["id"]) for w in report.warnings if w["code"] == "ref.dangling"] == [
        ("ref.dangling", "entries", "ent000001")
    ]
    assert triggers(repo) == before


def test_repository_delete_of_a_used_target_raises_constraint_violation(tmp_path):
    from permita38.core.repository.base import ConstraintViolation

    repo = SQLiteRepository(tmp_path / "new")
    repo.startup()
    proj = repo.save("projects", {"name": "Demo"})
    repo.save("entries", {"project_id": proj["id"], "date": "2026-03-02", "minutes": 30})
    with pytest.raises(ConstraintViolation) as e:
        repo.delete("projects", proj["id"])
    assert e.value.code == "ref.in_use" and e.value.collection == "projects"
    with pytest.raises(ConstraintViolation) as e2:
        repo.delete_where("projects", id=proj["id"])
    assert e2.value.code == "ref.in_use"
    assert repo.get("projects", proj["id"]) is not None


def test_delete_side_is_suspended_inside_a_restore_block_and_back_afterwards(tmp_path):
    from permita38.core.repository.base import ConstraintViolation

    repo = SQLiteRepository(tmp_path / "new")
    repo.startup()
    before = triggers(repo)
    assert "guard_entries_project_id_ref_del" in before
    used = repo.save("projects", {"name": "Demo"})
    repo.save("entries", {"project_id": used["id"], "date": "2026-03-02", "minutes": 30})
    with repo.transaction(), repo.guards_suspended({"reference"}):
        assert "guard_entries_project_id_ref_del" not in triggers(repo)
        deleted = repo.delete("projects", used["id"])
        assert deleted is True
    assert repo.get("projects", used["id"]) is None
    assert triggers(repo) == before
    again = repo.save("projects", {"name": "Demo 2"})
    repo.save("entries", {"project_id": again["id"], "date": "2026-03-02", "minutes": 30})
    with pytest.raises(ConstraintViolation) as e:
        repo.delete("projects", again["id"])
    assert e.value.code == "ref.in_use"
    assert repo.get("projects", again["id"]) is not None


def test_delete_side_triggers_are_back_and_the_delete_rolled_back_after_an_exception(tmp_path):
    repo = SQLiteRepository(tmp_path / "new")
    repo.startup()
    before = triggers(repo)
    used = repo.save("projects", {"name": "Demo"})
    repo.save("entries", {"project_id": used["id"], "date": "2026-03-02", "minutes": 30})
    try:
        with repo.transaction(), repo.guards_suspended({"reference"}):
            deleted = repo.delete("projects", used["id"])
            assert deleted is True
            raise RuntimeError("boom")
    except RuntimeError as err:
        assert str(err) == "boom"
    else:
        pytest.fail("the block must raise")
    assert triggers(repo) == before
    assert repo.get("projects", used["id"]) is not None
