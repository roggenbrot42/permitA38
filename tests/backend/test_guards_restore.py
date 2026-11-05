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
