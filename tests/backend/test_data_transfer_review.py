"""Data export/import: rules found by review. Account handling, strict validation, empty-instance precondition,
staged attachments and atomicity, limits that really reach their check, numbering, secrets, pinned lists."""

import json
import os
import re
import zipfile
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from permita38.core.database import list_migrations
from permita38.core.errors import ServiceError
from permita38.core.models import Settings
from permita38.core.repository.schema import TABLES
from permita38.core.service.numbering import NumberingService
from permita38.core.service.team_timeline import SETTING as TEAM_TIMELINE_KEY
from permita38.core.service.transfer import importer, limits, manifest, upgrades
from permita38.core.service.transfer.archive import ArchiveError, ArchiveReader
from permita38.core.service.transfer.service import clean_stale_tmp

from .conftest import PASSWORD
from .transfer_helpers import build_zip, export_archive, make_instance, seed, zip_files

HASH = "pbkdf2_sha256$1000$" + "ab" * 16 + "$" + "cd" * 32


@pytest.fixture
def src(tmp_path):
    app, client = make_instance(tmp_path, "src")
    store = app.state.services.store
    ids = seed(store)
    return app, client, store, ids


@pytest.fixture
def dst(tmp_path):
    app, client = make_instance(tmp_path, "dst")
    return app, client, app.state.services.store


def run(client, blob: bytes):
    return client.post("/api/data/import/run", files={"file": ("a.zip", blob)}, data={"confirm": "true"})


def preview(client, blob: bytes):
    return client.post("/api/data/import/preview", files={"file": ("a.zip", blob)})


def codes(r) -> list[str]:
    return [e["code"] for e in r.json()["errors"]]


def rebuild(blob: bytes, tmp: Path, edit=None, manifest_patch=None) -> bytes:
    """The archive with ``edit(files)`` applied and the manifest hashes recomputed."""
    files = zip_files(blob)
    if edit:
        edit(files)
    return build_zip(files, tmp / "hand.zip", manifest_patch).read_bytes()


def edit_records(name: str, mutate):
    """An ``edit`` for rebuild(): ``mutate(records)`` changes the records of data/<name>.json in place."""

    def edit(files):
        body = json.loads(files[f"data/{name}.json"])
        mutate(body["records"])
        body["count"] = len(body["records"])
        files[f"data/{name}.json"] = json.dumps(body).encode()

    return edit


def state(store) -> dict:
    """Everything a failed import must leave untouched (also the tables that are not exported)."""
    settings = {k: v for k, v in store.settings().items() if k != manifest.LOG_KEY}
    return {
        "tables": {t: store.list(t) for t in TABLES if t != "settings"},
        "settings": settings,
        "attachments": store.list_attachments(),
    }


def stages(app) -> list[str]:
    folder = app.state.services.config.data_dir / "attachments"
    return sorted(p.name for p in folder.glob(".import-*")) if folder.is_dir() else []


# ------------------------------------------------------------------ users and accounts
def test_without_include_accounts_hand_made_hashes_are_dropped_and_nobody_can_log_in(src, dst, tmp_path):
    _, sc, _, _ = src
    dapp, dc, dstore = dst
    blob = export_archive(sc)  # include_accounts is false

    def add_boss(recs):
        recs.append({"id": "eeeeeee1", "username": "eve", "name": "Eve", "role": "boss", "password_hash": HASH})
        recs.append({"id": "eeeeeee2", "username": "max", "name": "Max", "role": "employee", "pw_changed": 5})

    hand = rebuild(blob, tmp_path, edit_records("users", add_boss))
    r = run(dc, hand)
    assert r.status_code == 200, r.text
    users = {u["username"]: u for u in dstore.list("users")}
    assert not users["eve"].get("password_hash") and users["eve"]["active"] is False
    assert "pw_changed" not in users["max"] and users["max"]["active"] is False
    body = r.json()["users"]
    assert body["with_login"] == ["boss"] and body["bosses"] == ["boss"]
    assert sorted(body["without_password"]) == ["eve", "max"]
    resp = dc.post("/api/auth/login", json={"username": "eve", "password": PASSWORD})
    assert resp.status_code in (401, 403)


def test_with_include_accounts_the_result_lists_who_can_log_in(src, dst, tmp_path):
    sapp, sc, sstore, _ = src
    _, dc, _ = dst
    sc.post("/api/users", json={"username": "ann", "name": "Ann", "role": "boss", "password": PASSWORD})
    sc.post("/api/users", json={"username": "tom", "name": "Tom", "role": "employee", "password": PASSWORD})
    blob = export_archive(sc, include_accounts=True)
    pre = preview(dc, blob).json()
    assert sorted(pre["users"]["with_login"]) == ["ann", "boss", "tom"]
    assert sorted(pre["users"]["bosses"]) == ["ann", "boss"]
    warning = next(w for w in pre["warnings"] if w["code"] == "accounts_can_login")
    assert "3 accounts incl. 2 bosses" in warning["message"]
    res = run(dc, blob).json()
    assert res["users"]["with_login"] == pre["users"]["with_login"] and res["users"]["bosses"] == pre["users"]["bosses"]


def test_a_hash_with_a_wrong_shape_is_refused_when_accounts_are_included(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, dstore = dst
    blob = export_archive(sc, include_accounts=True)

    def bad(recs):
        recs[0]["password_hash"] = "plaintext-secret"

    r = run(dc, rebuild(blob, tmp_path, edit_records("users", bad)))
    assert r.status_code == 422 and codes(r) == ["record_invalid"]
    assert dstore.list("users")[0]["username"] == "boss"


def test_matched_setup_user_keeps_local_boss_and_active_and_matches_case_insensitively(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, dstore = dst
    local = dstore.list("users")[0]
    blob = export_archive(sc, include_accounts=True)

    def demote(recs):
        recs[0].update({"username": "BOSS", "role": "employee", "active": False})

    r = run(dc, rebuild(blob, tmp_path, edit_records("users", demote)))
    assert r.status_code == 200, r.text
    assert r.json()["users"]["replaces_setup_user"] == "boss"
    users = dstore.list("users")
    assert len(users) == 1
    assert users[0]["role"] == "boss" and users[0]["active"] is not False
    assert users[0]["password_hash"] == local["password_hash"]


def test_two_factor_data_in_a_hand_made_archive_is_never_stored(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, dstore = dst
    blob = export_archive(sc, include_accounts=True)

    def totp(recs):
        for u in recs:
            u.update({"totp_secret": "JBSWY3DPEHPK3PXP", "totp_enabled": True, "totp_recovery": ["abc"], "totp_x": 1})
        recs.append({"id": "aaaaaaa1", "username": "zed", "name": "Z", "role": "employee", "password_hash": HASH})
        recs[-1]["totp_secret"] = "ZZZZ"

    resp = run(dc, rebuild(blob, tmp_path, edit_records("users", totp)))
    assert resp.status_code == 200
    for u in dstore.list("users"):
        assert not u.get("totp_enabled")
        assert not any(k in u for k in ("totp_secret", "totp_recovery", "totp_x", "totp_last_step"))


def test_users_keep_created_and_updated_of_the_archive(src, dst):
    _, sc, sstore, _ = src
    _, dc, dstore = dst
    sc.post("/api/users", json={"username": "ann", "name": "Ann", "role": "employee", "password": PASSWORD})
    blob = export_archive(sc, include_accounts=True)
    resp = run(dc, blob)
    assert resp.status_code == 200
    mine = {u["id"]: (u["created"], u["updated"]) for u in dstore.list("users")}
    theirs = {u["id"]: (u["created"], u["updated"]) for u in sstore.list("users")}
    assert mine == theirs


def test_without_accounts_pw_changed_is_in_neither_json_nor_csv(src):
    _, sc, _, _ = src
    files = zip_files(export_archive(sc))
    assert b"pw_changed" not in files["data/users.json"] and b"password_hash" not in files["data/users.json"]
    header = files["csv/users.csv"].decode("utf-8-sig").splitlines()[0]
    assert "pw_changed" not in header and "password_hash" not in header
    with_accounts = zip_files(export_archive(sc, include_accounts=True))
    assert "pw_changed" in with_accounts["csv/users.csv"].decode("utf-8-sig").splitlines()[0]


# ------------------------------------------------------------------ strict validation
def _dup_username(recs):
    recs.append({**recs[0], "id": "dddddd01", "username": recs[0]["username"].upper()})


def _bad_role(recs):
    recs[0]["role"] = "superuser"


def _no_username(recs):
    recs[0]["username"] = "  "


def _huge_float(recs):
    recs[0]["weekly_hours"] = 1e30


def _huge_sequence(recs):
    recs[0]["last_value"] = 10**10


def _dup_sequence(recs):
    recs.append({**recs[0], "id": "ssssss01"})


@pytest.mark.parametrize(
    "coll,mutate",
    [
        ("users", _dup_username),
        ("users", _bad_role),
        ("users", _no_username),
        ("users", _huge_float),
        ("number_sequences", _huge_sequence),
        ("number_sequences", _dup_sequence),
    ],
)
def test_semantic_errors_are_a_clean_422(coll, mutate, src, dst, tmp_path):
    _, sc, _, _ = src
    dapp, dc, dstore = dst
    before = state(dstore)
    hand = rebuild(export_archive(sc), tmp_path, edit_records(coll, mutate))
    r = run(dc, hand)
    assert r.status_code == 422, r.text
    assert set(codes(r)) <= {"duplicate_id", "record_invalid"} and codes(r)
    assert "Traceback" not in r.text
    assert state(dstore) == before and stages(dapp) == []


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_non_finite_numbers_are_a_clean_422(literal, src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, _ = dst

    def poison(files):
        text = files["data/customers.json"].decode()
        files["data/customers.json"] = text.replace('"rate": 95.0', f'"rate": {literal}').encode()
        assert literal in files["data/customers.json"].decode()

    r = run(dc, rebuild(export_archive(sc), tmp_path, poison))
    assert r.status_code == 422 and codes(r) == ["json_invalid"]


def test_invalid_imported_settings_are_an_error(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, _ = dst

    def bad(files):
        files["data/settings.json"] = json.dumps({"settings": {"vat_rate": "lots", "payment_days": {}}}).encode()

    r = run(dc, rebuild(export_archive(sc), tmp_path, bad))
    assert r.status_code == 422 and codes(r) == ["record_invalid"]


def test_an_unexpected_exception_becomes_a_clean_error(src, dst, tmp_path, monkeypatch):
    _, sc, _, _ = src
    dapp, dc, _ = dst
    blob = export_archive(sc)

    def boom(*a, **k):
        raise RuntimeError("secret internal detail /home/x")

    monkeypatch.setattr(importer, "_check_references", boom)
    r = run(dc, blob)
    assert r.status_code == 500 and "secret internal detail" not in r.text and "Traceback" not in r.text
    assert not list((dapp.state.services.config.data_dir / "tmp").glob("permita38-*"))


# ------------------------------------------------------------------ the empty-instance precondition
def _leftover_attachment(store):
    store.save_attachment("x.pdf", b"%PDF-1.4 leftover")
    return "attachments: 1"


def _two_users(store):
    store.save("users", {"username": "second", "name": "Second", "role": "employee"})
    return "users: 2"


def _non_boss(store):
    me = store.list("users")[0]
    store.save("users", {**me, "role": "employee"})
    return "users: 1 (not a boss)"


def _row_in(table):
    def fill(store):
        store.save(table, {"name": "x"})
        return f"{table}: 1"

    return fill


FILLERS = [
    pytest.param(_leftover_attachment, id="attachment"),
    pytest.param(_two_users, id="two-users"),
    pytest.param(_non_boss, id="non-boss"),
    *[pytest.param(_row_in(t), id=t) for t in TABLES if t not in ("users", "settings")],
]


@pytest.fixture(scope="module")
def blob_for_conflicts(tmp_path_factory):
    app, client = make_instance(tmp_path_factory.mktemp("conflict-src"), "src")
    seed(app.state.services.store)
    blob = export_archive(client)
    return blob


@pytest.mark.parametrize("fill", FILLERS)
def test_every_kind_of_existing_data_gives_409_and_changes_nothing(fill, blob_for_conflicts, dst, tmp_path):
    dapp, dc, dstore = dst
    expected = fill(dstore)
    before = state(dstore)
    service = dapp.state.services.transfer  # the service: the only caller who may be a non-boss here
    path = tmp_path / "a.zip"
    path.write_bytes(blob_for_conflicts)
    plan = service.preview(None, path)
    assert plan["ok"] is False and [e["code"] for e in plan["errors"]] == ["not_empty"]
    assert expected in plan["errors"][0]["message"]
    with pytest.raises(ServiceError) as exc:
        service.run(None, path)
    assert exc.value.status == 409 and expected in exc.value.message
    if expected != "users: 1 (not a boss)":  # over HTTP the caller must be a boss
        r = run(dc, blob_for_conflicts)
        assert r.status_code == 409 and expected in r.json()["detail"], r.text
    assert state(dstore) == before and stages(dapp) == []


def test_the_instance_is_checked_again_inside_the_write_transaction(src, dst, monkeypatch):
    _, sc, _, _ = src
    dapp, dc, dstore = dst
    blob = export_archive(sc)
    real = importer.validate

    def validate_then_race(*a, **k):
        plan = real(*a, **k)
        dstore.save("customers", {"name": "appeared meanwhile"})  # somebody else wrote after the check
        return plan

    monkeypatch.setattr(importer, "validate", validate_then_race)
    r = run(dc, blob)
    assert r.status_code == 409 and "customers: 1" in r.json()["detail"]
    assert [c["name"] for c in dstore.list("customers")] == ["appeared meanwhile"]
    assert dstore.list_attachments() == [] and stages(dapp) == []


# ------------------------------------------------------------------ attachments: staging, rollback, magic bytes
def test_attachments_with_the_wrong_magic_bytes_refuse_the_archive(src, dst, tmp_path):
    _, sc, _, ids = src
    dapp, dc, dstore = dst
    name = ids["attachment"]  # a .pdf

    def swap(files):
        files[f"attachments/{name}"] = b"<html><script>alert(1)</script>"

    r = run(dc, rebuild(export_archive(sc), tmp_path, swap))
    assert r.status_code == 422 and codes(r) == ["attachment_invalid"]
    assert dstore.list_attachments() == [] and stages(dapp) == []


def _fail_write_users(dstore, monkeypatch):
    def boom(*a, **k):
        raise OSError("users failed")

    monkeypatch.setattr(importer, "_write_users", boom)


def _fail_save_settings(dstore, monkeypatch):
    def boom(*a, **k):
        raise OSError("settings failed")

    monkeypatch.setattr(dstore, "save_settings", boom)


def _fail_commit(dstore, monkeypatch):
    real_rollback = dstore._rollback

    def boom():
        real_rollback()
        raise OSError("commit failed")

    monkeypatch.setattr(dstore, "_commit", boom)


@pytest.mark.parametrize("inject", [_fail_write_users, _fail_save_settings, _fail_commit])
def test_a_failure_inside_the_write_leaves_nothing_behind(inject, src, dst, monkeypatch):
    _, sc, _, _ = src
    dapp, dc, dstore = dst
    blob = export_archive(sc)
    before = state(dstore)
    inject(dstore, monkeypatch)
    with TestClient(dapp, raise_server_exceptions=False) as c:
        c.headers["Authorization"] = dc.headers["Authorization"]
        r = run(c, blob)
    assert r.status_code == 500 and "failed" not in r.json()["detail"].replace("failed unexpectedly", "")
    monkeypatch.undo()
    assert state(dstore) == before
    assert stages(dapp) == [] and dstore.list_attachments() == []
    assert not list((dapp.state.services.config.data_dir / "tmp").glob("permita38-*"))
    resp = run(dc, blob)
    assert resp.status_code == 200  # and the instance is importable afterwards


def test_an_unexpected_failure_over_http_leaves_no_temp_files(src, dst, monkeypatch):
    _, sc, _, _ = src
    dapp, dc, _ = dst
    blob = export_archive(sc)

    def boom(*a, **k):
        raise RuntimeError("router level failure")

    monkeypatch.setattr(dapp.state.services.transfer, "run", boom)
    with TestClient(dapp, raise_server_exceptions=False) as c:
        c.headers["Authorization"] = dc.headers["Authorization"]
        resp = run(c, blob)
        assert resp.status_code == 500
        assert preview(c, b"junk").json()["ok"] is False
    assert not list((dapp.state.services.config.data_dir / "tmp").glob("permita38-*"))


def test_an_interrupt_removes_the_staging_folder(src, dst, monkeypatch):
    _, sc, _, _ = src
    dapp, dc, dstore = dst
    blob = export_archive(sc)

    def interrupted(*a, **k):
        raise KeyboardInterrupt

    monkeypatch.setattr(dstore, "save_settings", interrupted)
    path = dapp.state.services.config.data_dir / "tmp" / "permita38-import-x.zip"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(blob)
    with pytest.raises(KeyboardInterrupt):
        dapp.state.services.transfer.run(None, path)
    assert stages(dapp) == [] and dstore.list_attachments() == []
    assert dstore.list("customers") == []


def test_a_crash_between_the_attachments_and_the_commit_leaves_the_instance_importable(src, dst):
    _, sc, _, ids = src
    dapp, dc, dstore = dst
    stage = dstore.new_stage()  # what a killed process leaves: a staging folder with files in it
    dstore.stage_attachment(stage, ids["attachment"], [b"%PDF-1.4 half"])
    assert stages(dapp) == [stage] and dstore.list_attachments() == []
    info = dc.get("/api/data/export/info").json()
    assert info["import"] == {"possible": True, "reason": None}
    resp = run(dc, export_archive(sc))
    assert resp.status_code == 200
    assert dstore.list_attachments() == [ids["attachment"]]
    assert stages(dapp) == [stage]  # the old stage is somebody else's: only the stale cleaner removes it
    clean_stale_tmp(dapp.state.services.config.data_dir / "tmp", max_age=0, store=dstore)
    assert stages(dapp) == []


def test_restore_attachment_unlinks_its_tmp_and_keeps_an_existing_identical_file(src, monkeypatch):
    _, _, store, _ = src
    folder = Path(store.data_dir) / "attachments"
    store.restore_attachment("aaaaaa11.bin", b"one")

    def broken_replace(a, b):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", broken_replace)
    with pytest.raises(OSError):
        store.restore_attachment("bbbbbb22.bin", b"two")
    monkeypatch.undo()
    assert not list(folder.glob("*.tmp"))
    store.restore_attachment("aaaaaa11.bin", b"one")  # identical content: nothing to do, nothing deleted
    assert (folder / "aaaaaa11.bin").read_bytes() == b"one"


def test_commit_stage_never_replaces_or_deletes_an_existing_file(src):
    _, _, store, _ = src
    store.restore_attachment("aaaaaa11.bin", b"one")
    stage = store.new_stage()
    store.stage_attachment(stage, "aaaaaa11.bin", [b"one"])
    assert store.commit_stage(stage) == []
    assert store.attachment_path("aaaaaa11.bin") and store.list_attachments().count("aaaaaa11.bin") == 1
    stage = store.new_stage()
    store.stage_attachment(stage, "aaaaaa11.bin", [b"other"])
    with pytest.raises(FileExistsError):
        store.commit_stage(stage)
    store.discard_stage(stage)
    assert Path(store.attachment_path("aaaaaa11.bin")).read_bytes() == b"one"


def test_a_big_old_attachment_never_blocks_the_export(src, dst):
    _, sc, store, _ = src
    big = store.save_attachment("old.bin", bytes(26 * limits.MIB))  # above the 25 MiB the first draft refused
    blob = export_archive(sc)
    assert zipfile.ZipFile(__import__("io").BytesIO(blob)).getinfo(f"attachments/{big}").file_size == 26 * limits.MIB
    _, dc, dstore = dst
    resp = run(dc, blob)
    assert resp.status_code == 200 and big in dstore.list_attachments()


# ------------------------------------------------------------------ limits that really reach their check
def _zeros_zip(path: Path, name: str, size: int) -> Path:
    with (
        zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf,
        zf.open(name, "w", force_zip64=True) as f,
    ):
        block = bytes(limits.MIB)
        for _ in range(size // limits.MIB):
            f.write(block)
    return path


def test_the_pinned_limits():
    assert limits.MAX_DATA_FILE == 64 * limits.MIB
    assert limits.MAX_RECORDS == 200_000
    assert limits.MAX_UPLOAD == 512 * limits.MIB and limits.MAX_RATIO == 200


def test_the_ratio_check_itself_refuses_a_bomb_below_the_size_limit(tmp_path):
    path = _zeros_zip(tmp_path / "ratio.zip", "data/customers.json", 60 * limits.MIB)
    assert 60 * limits.MIB < limits.MAX_DATA_FILE  # the size check does not fire, the ratio check must
    with pytest.raises(ArchiveError) as e:
        ArchiveReader(path)
    assert e.value.code == "limit_exceeded" and "compression ratio" in e.value.message


def test_the_ratio_is_enforced_while_reading_too(tmp_path, monkeypatch):
    path = tmp_path / "r.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("data/customers.json", b"a" * 200_000)
    reader = ArchiveReader(path)  # opens: ratio is fine under the real limit
    monkeypatch.setattr(limits, "RATIO_EXEMPT", 1000)
    monkeypatch.setattr(limits, "MAX_RATIO", 2)
    with pytest.raises(ArchiveError) as e:
        reader.read("data/customers.json")
    reader.close()
    assert e.value.code == "limit_exceeded" and "compression ratio" in e.value.message


def test_the_total_limit_is_enforced_on_the_directory_and_while_reading(src, tmp_path, monkeypatch):
    _, sc, _, _ = src
    path = tmp_path / "t.zip"
    path.write_bytes(export_archive(sc))
    reader = ArchiveReader(path)  # the real limit: fine
    monkeypatch.setattr(limits, "MAX_TOTAL", 500)
    with pytest.raises(ArchiveError) as e:
        for name in reader.entries:
            reader.read(name)
    assert e.value.code == "limit_exceeded" and "2 GiB" in e.value.message
    reader.close()
    with pytest.raises(ArchiveError) as e2:
        ArchiveReader(path)
    assert e2.value.code == "limit_exceeded"


def test_the_export_refuses_over_the_total_limit_with_413(src, monkeypatch):
    _, sc, _, _ = src
    monkeypatch.setattr(limits, "MAX_TOTAL", 500)
    r = sc.post("/api/data/export", json={})
    assert r.status_code == 413 and "2 GiB" in r.json()["detail"]


def test_the_run_route_answers_413_for_an_oversized_upload(dst, monkeypatch):
    dapp, dc, _ = dst
    monkeypatch.setattr(limits, "MAX_UPLOAD", 1000)
    r = run(dc, b"x" * 2000)
    assert r.status_code == 413
    assert not list((dapp.state.services.config.data_dir / "tmp").glob("permita38-*"))


def test_a_master_data_archive_is_refused_over_http(src, dst):
    _, sc, _, _ = src
    _, dc, dstore = dst
    blob = export_archive(sc, scope="master_data")
    r = run(dc, blob)
    assert r.status_code == 422 and codes(r) == ["scope_not_importable"]
    assert dstore.list("customers") == []


# ------------------------------------------------------------------ numbering
def _numbered(tmp_path, name, fill, today=date(2026, 10, 24)):
    """Export an instance filled by ``fill(store)`` and import it into a fresh one; (result, target store)."""
    app, client = make_instance(tmp_path, name + "-src")
    fill(app.state.services.store)
    blob = export_archive(client)
    dapp, _ = make_instance(tmp_path, name + "-dst")
    path = tmp_path / f"{name}.zip"
    path.write_bytes(blob)
    result = dapp.state.services.transfer.run(None, path, today=today)
    return result, dapp.state.services.store


def _counter(store, group, period, last):
    sid = NumberingService(store)._row_id(group, period)
    store.save("number_sequences", {"id": sid, "doc_type": group, "period": period, "last_value": last})


def _docs(store, coll, prefix, numbers, day="2026-03-01"):
    for n in numbers:
        store.save(coll, {"number": f"{prefix}-2026-{n:04d}", "date": day, "status": "sent"})


def test_next_number_skips_numbers_in_use_after_a_stale_counter(tmp_path):
    def fill(store):
        _docs(store, "invoices", "RE", [1, 2, 6])
        _counter(store, "invoices", "2026", 5)

    result, store = _numbered(tmp_path, "gap", fill)
    assert result["numbering"]["invoices"]["next"] == "RE-2026-0007"
    assert NumberingService(store, lambda: date(2026, 10, 24)).preview("invoice", 1) == ["RE-2026-0007"]
    assert "numbering_counter_low" in [w["code"] for w in result["warnings"]]


def test_a_zero_counter_with_documents_warns_and_the_next_number_follows_the_documents(tmp_path):
    def fill(store):
        _docs(store, "invoices", "RE", [1, 2, 3])
        _counter(store, "invoices", "2026", 0)

    result, _ = _numbered(tmp_path, "zero", fill)
    assert result["numbering"]["invoices"]["next"] == "RE-2026-0004"
    assert "numbering_counter_low" in [w["code"] for w in result["warnings"]]


def test_offers_have_their_own_sequence(tmp_path):
    def fill(store):
        _docs(store, "invoices", "RE", [1, 2])
        _docs(store, "offers", "AN", [1, 2, 3])

    result, store = _numbered(tmp_path, "offers", fill)
    assert result["numbering"]["invoices"]["next"] == "RE-2026-0003"
    assert result["numbering"]["offers"]["next"] == "AN-2026-0004"
    assert NumberingService(store, lambda: date(2026, 10, 24)).preview("offer", 1) == ["AN-2026-0004"]


def test_shared_numbering_counts_invoices_and_offers_together(tmp_path):
    def fill(store):
        store.save_settings(
            {
                **store.settings(),
                "numbering_shared": True,
                "invoice_pattern": "D-{year}-{n:04d}",
                "offer_pattern": "D-{year}-{n:04d}",
            }
        )
        _docs(store, "invoices", "D", [1, 3])
        _docs(store, "offers", "D", [2, 5])
        _counter(store, "shared", "2026", 5)

    result, store = _numbered(tmp_path, "shared", fill)
    assert result["numbering"]["invoices"]["next"] == "D-2026-0006"
    assert result["numbering"]["offers"]["next"] == "D-2026-0006"
    assert NumberingService(store, lambda: date(2026, 10, 24)).preview("invoice", 1) == ["D-2026-0006"]


def test_several_periods_use_the_counter_of_the_current_period(tmp_path):
    def fill(store):
        store.save_settings({**store.settings(), "invoice_pattern": "{yy}{month}-{n:03d}", "invoice_reset": "monthly"})
        store.save("invoices", {"number": "2609-001", "date": "2026-09-05", "status": "sent"})
        store.save("invoices", {"number": "2609-004", "date": "2026-09-20", "status": "sent"})
        store.save("invoices", {"number": "2610-001", "date": "2026-10-02", "status": "sent"})
        _counter(store, "invoices", "2026-09", 4)
        _counter(store, "invoices", "2026-10", 1)

    result, store = _numbered(tmp_path, "periods", fill)
    assert result["numbering"]["invoices"]["next"] == "2610-002"
    assert NumberingService(store, lambda: date(2026, 10, 24)).preview("invoice", 1) == ["2610-002"]
    # a day in the previous month continues that month's counter
    assert NumberingService(store, lambda: date(2026, 9, 28)).preview("invoice", 1) == ["2609-005"]


# ------------------------------------------------------------------ secrets in settings
SECRET_NAMES = [
    "my_token",
    "db_password",
    "passwd",
    "private_key",
    "app_secret",
    "smtp_pass",
    "aws_credentials",
    "oauth_auth",
    "license_key",
    "stripe-api-key",
    "apikey",
    "webhook_Secret",
]


def test_every_alternative_of_the_secret_pattern_is_dropped_on_export(src):
    _, sc, store, _ = src
    st = store.settings()
    st.update({name: f"canary-{name}-value" for name in SECRET_NAMES})
    store.save_settings(st)
    files = zip_files(export_archive(sc))
    exported = json.loads(files[manifest.SETTINGS_FILE])["settings"]
    assert not [n for n in SECRET_NAMES if n in exported]
    for name, content in files.items():
        assert b"canary-" not in content, name


def test_real_setting_names_survive_the_secret_filter():
    real = [*Settings.model_fields, TEAM_TIMELINE_KEY, "company_name", "invoice_pattern", "logo", "iban"]
    dropped = [k for k in real if manifest.is_secret_setting(k)]
    assert dropped == []


def test_secret_settings_in_an_archive_are_not_imported_and_local_ones_stay(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, dstore = dst
    local = dstore.settings()
    local["local_token"] = "keep-me"
    dstore.save_settings(local)

    def smuggle(files):
        body = json.loads(files[manifest.SETTINGS_FILE])
        body["settings"].update({n: "evil" for n in SECRET_NAMES} | {"local_token": "evil"})
        files[manifest.SETTINGS_FILE] = json.dumps(body).encode()

    resp = run(dc, rebuild(export_archive(sc), tmp_path, smuggle))
    assert resp.status_code == 200
    got = dstore.settings()
    assert not [n for n in SECRET_NAMES if n in got]
    assert got["local_token"] == "keep-me"


def test_secret_key_canaries_are_in_no_decompressed_file(src):
    _, sc, store, _ = src
    st = store.settings()
    st["x_api_key"] = "sk-canary-1"
    store.save_settings(st)
    secret = b"test-secret-key-src"
    for accounts in (False, True):
        for name, content in zip_files(export_archive(sc, include_accounts=accounts)).items():
            assert secret not in content and b"sk-canary-1" not in content, name
            assert b"secret-feed-token" not in content, name  # calendar URL


# ------------------------------------------------------------------ pinned lists
def test_exported_collections_and_excluded_tables_are_pinned():
    assert manifest.exported_collections() == [
        "customers",
        "projects",
        "tasks",
        "entries",
        "contracts",
        "absences",
        "plan_scenarios",
        "offers",
        "invoices",
        "expenses",
        "trips",
        "users",
        "templates",
        "calendar_sources",
        "number_sequences",
        "numbering_log",
        "import_batches",
    ]
    assert sorted(manifest.EXCLUDED_TABLES) == ["calendar_events", "settings", "timers"]
    assert all(manifest.EXCLUDED_TABLES.values())  # every exclusion has a reason


def test_user_csv_columns_are_pinned_and_hold_no_secret():
    plain = manifest.columns_for("users", False)
    assert plain == [
        "id", "created", "updated", "username", "name", "role", "weekly_hours", "workdays", "default_rate",
        "cost_rate", "color", "tracking_start", "active", "work_start", "work_end", "timezone",
    ]  # fmt: skip
    full = manifest.columns_for("users", True)
    assert full == [*plain[:13], "password_hash", "pw_changed", *plain[13:]]
    assert not [c for c in full if re.search(r"totp|secret|token", c)]


def test_timers_are_not_exported(src):
    _, sc, store, _ = src
    store.save("timers", {"running": True, "started": 1, "note": "n"})
    files = zip_files(export_archive(sc))
    assert "data/timers.json" not in files and "csv/timers.csv" not in files
    assert "timers" not in json.loads(files["manifest.json"])["counts"]


# ------------------------------------------------------------------ old archives
def test_every_migration_after_the_first_has_an_upgrade_hook_or_a_reason():
    chain = [m.id for m in list_migrations("sqlite")]
    unknown = [m for m in chain[1:] if m not in upgrades.UPGRADES and m not in upgrades.NO_UPGRADE_NEEDED]
    assert unknown == [], f"add an upgrade hook or a NO_UPGRADE_NEEDED reason for {unknown}"
    assert set(upgrades.UPGRADES) | set(upgrades.NO_UPGRADE_NEEDED) <= set(chain)
    assert not set(upgrades.UPGRADES) & set(upgrades.NO_UPGRADE_NEEDED)


def test_the_roles_hook_maps_member_to_employee():
    chain = [m.id for m in list_migrations("sqlite")]
    before_roles = chain[chain.index("4d5e0131") - 1]
    records = {"users": [{"role": "member"}, {"role": "boss"}]}
    upgrades.upgrade(records, before_roles, chain)
    assert [u["role"] for u in records["users"]] == ["employee", "boss"]
    same = {"users": [{"role": "member"}]}
    upgrades.upgrade(same, chain[-1], chain)  # an archive of the current schema is not touched
    assert same["users"][0]["role"] == "member"


def test_an_old_archive_with_member_users_imports_as_employees(src, dst, tmp_path):
    _, sc, _, _ = src
    _, dc, dstore = dst
    chain = [m.id for m in list_migrations("sqlite")]
    sc.post("/api/users", json={"username": "ann", "name": "Ann", "role": "employee", "password": PASSWORD})

    def to_member(recs):
        for u in recs:
            if u["username"] == "ann":
                u["role"] = "member"

    old = rebuild(
        export_archive(sc),
        tmp_path,
        edit_records("users", to_member),
        {"schema_head": chain[chain.index("4d5e0131") - 1]},
    )
    resp = run(dc, old)
    assert resp.status_code == 200
    assert {u["username"]: u["role"] for u in dstore.list("users")}["ann"] == "employee"


# ------------------------------------------------------------------ CLI and HTTP rules
def test_cli_dry_run_writes_no_transfer_log(src, tmp_path):
    from permita38.cli.main import main

    sapp, _, _, _ = src
    sapp.state.services.store.close()
    out = tmp_path / "x.zip"
    cfg = ["-c", str(tmp_path / "src.yaml"), "--data", str(tmp_path / "src")]
    main([*cfg, "export", str(out)])
    fresh = ["-c", str(tmp_path / "f.yaml"), "--data", str(tmp_path / "fresh")]
    main([*fresh, "import", str(out), "--dry-run"])
    main([*fresh, "import", str(out), "--dry-run"])
    from permita38.core.repository.sqlite import SQLiteRepository

    repo = SQLiteRepository(tmp_path / "fresh")
    repo.startup()
    try:
        assert manifest.LOG_KEY not in repo.settings()
    finally:
        repo.close()


def test_the_session_cookie_is_strict_and_http_only_so_the_post_routes_are_not_forgeable(dst):
    dapp, _, _ = dst
    with TestClient(dapp) as c:
        r = c.post("/api/auth/login", json={"username": "boss", "password": PASSWORD})
    header = r.headers["set-cookie"].lower()
    assert "samesite=strict" in header and "httponly" in header


def test_the_routes_demand_the_boss_with_the_full_scope():
    from permita38.api.deps import declared
    from permita38.api.routers import data_transfer

    seen = {r.path for r in data_transfer.router.routes}
    assert len(seen) == 5
    for route in data_transfer.router.routes:
        assert declared(route) == [("settings", "write", True)], route.path


def test_a_member_is_refused_on_all_five_routes(app, boss_client, member_client):
    for method, path in [
        ("GET", "/api/data/export/info"),
        ("POST", "/api/data/export"),
        ("POST", "/api/data/import/preview"),
        ("POST", "/api/data/import/run"),
        ("GET", "/api/data/transfers"),
    ]:
        kwargs = {"files": {"file": ("a.zip", b"x")}} if "import" in path else {}
        resp = member_client.request(method, path, **kwargs)
        assert resp.status_code == 403, path
