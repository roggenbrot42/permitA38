"""Data import: refusals leave the instance unchanged, atomicity, users, numbering, preview/run, CLI."""

import json
import os
import stat
from datetime import date

import pytest

from permita38.cli.main import main
from permita38.core.database import list_migrations
from permita38.core.errors import ServiceError
from permita38.core.service.numbering import NumberingService
from permita38.core.service.transfer import manifest

from .conftest import PASSWORD
from .transfer_helpers import build_zip, export_archive, login, make_instance, seed, zip_files


def snapshot(store) -> dict:
    settings = {k: v for k, v in store.settings().items() if k != manifest.LOG_KEY}
    return {
        "records": {c: store.list(c) for c in manifest.exported_collections()},
        "settings": settings,
        "attachments": store.list_attachments(),
    }


@pytest.fixture
def source(tmp_path):
    app, client = make_instance(tmp_path, "src")
    ids = seed(app.state.services.store)
    return app, client, ids


@pytest.fixture
def target(tmp_path):
    app, client = make_instance(tmp_path, "dst")
    return app, client, app.state.services.store


def post_run(client, blob: bytes, confirm: bool = True):
    return client.post("/api/data/import/run", files={"file": ("a.zip", blob)}, data={"confirm": str(confirm).lower()})


def post_preview(client, blob: bytes):
    return client.post("/api/data/import/preview", files={"file": ("a.zip", blob)})


def codes(items) -> list[str]:
    return [i["code"] for i in items]


# ------------------------------------------------------------------ happy path
def test_import_restores_everything_and_keeps_ids(source, target):
    src_app, src_client, ids = source
    _, dst_client, dst_store = target
    blob = export_archive(src_client, include_accounts=True)
    r = post_run(dst_client, blob)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["imported"] is True and body["relogin"] is True  # the setup user got the archive id
    assert dst_store.get("customers", ids["customer"])["x_future_field"] == {"tags": ["a", "b"], "rank": 3}
    assert dst_store.list_attachments() == [ids["attachment"]]
    path = dst_store.attachment_path(ids["attachment"])
    assert path is not None
    with open(path, "rb") as f:
        assert f.read() == b"%PDF-1.4 demo bill"


def test_records_keep_created_and_updated(source, target):
    src_app, src_client, ids = source
    _, dst_client, dst_store = target
    before = src_app.state.services.store.get("customers", ids["customer"])
    resp = post_run(dst_client, export_archive(src_client))
    assert resp.status_code == 200
    after = dst_store.get("customers", ids["customer"])
    assert (after["created"], after["updated"]) == (before["created"], before["updated"])


# ------------------------------------------------------------------ refusals
def test_non_empty_instance_is_refused_with_409(source, target):
    _, src_client, _ = source
    _, dst_client, dst_store = target
    blob = export_archive(src_client)
    resp = post_run(dst_client, blob)
    assert resp.status_code == 200
    again = post_run(login(target[0], "boss"), blob)
    assert again.status_code == 409
    assert again.json()["detail"].startswith("This instance already contains data (")
    pre = post_preview(login(target[0], "boss"), blob).json()
    assert pre["ok"] is False and "not_empty" in codes(pre["errors"])


def _flip(files, name):
    b = bytearray(files[name])
    b[len(b) // 2] ^= 0x01
    files[name] = bytes(b)


def _drop_manifest(files):
    del files["manifest.json"]


def _extra_file(files):
    files["data/customers.json "] = b"x"


def _invalid_json(files):
    files["data/customers.json"] = b"{not json"


def _record(files, mutate):
    body = json.loads(files["data/customers.json"])
    mutate(body)
    files["data/customers.json"] = json.dumps(body).encode()


REFUSALS = {
    "manifest_missing": (_drop_manifest, False, "manifest_missing"),
    "bad_json": (_invalid_json, True, "json_invalid"),
    "no_id": (lambda f: _record(f, lambda b: b["records"][0].pop("id")), True, "record_invalid"),
    "bad_id": (lambda f: _record(f, lambda b: b["records"][0].update(id="../x")), True, "record_invalid"),
    "wrong_type": (lambda f: _record(f, lambda b: b["records"][0].update(rate="high")), True, "record_invalid"),
    "duplicate_ids": (
        lambda f: _record(f, lambda b: (b["records"].append(dict(b["records"][0])), b.update(count=2))),
        True,
        "duplicate_id",
    ),
    "count_mismatch": (lambda f: _record(f, lambda b: b.update(count=5)), True, "json_invalid"),
    "record_too_large": (
        lambda f: _record(f, lambda b: b["records"][0].update(notes="x" * (1024 * 1024 + 1))),
        True,
        "limit_exceeded",
    ),
    "hash_flip": (lambda f: _flip(f, "data/customers.json"), False, "hash_mismatch"),
    "unlisted_file": (lambda f: f.update({"attachments/abcdef12.png": b"x"}), False, "file_unlisted"),
    "missing_file": (lambda f: f.pop("data/customers.json"), False, "file_missing"),
    "bad_attachment_name": (lambda f: f.update({"attachments/EVIL.PDF": b"x"}), True, "unsafe_entry"),
    "master_data": (None, True, "scope_not_importable"),
    "newer_format": (None, True, "format_too_new"),
    "wrong_format": (None, True, "format_unknown"),
    "unknown_schema": (None, True, "schema_unknown"),
}
PATCHES = {
    "master_data": {"scope": "master_data"},
    "newer_format": {"format_version": 2},
    "wrong_format": {"format": "something-else"},
    "unknown_schema": {"schema_head": "ffffffff"},
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_refused_archive_changes_nothing(case, source, target, tmp_path):
    _, src_client, _ = source
    dst_app, dst_client, dst_store = target
    files = zip_files(export_archive(src_client))
    mutate, rehash, code = REFUSALS[case]
    if mutate:
        mutate(files)
    path = build_zip(files, tmp_path / "bad.zip", PATCHES.get(case), rehash=rehash)
    before = snapshot(dst_store)
    run = post_run(dst_client, path.read_bytes())
    assert run.status_code == 422, run.text
    assert code in codes(run.json()["errors"]) and run.json()["detail"].startswith("The archive was not imported:")
    pre = post_preview(dst_client, path.read_bytes()).json()
    assert pre["ok"] is False and code in codes(pre["errors"])
    assert snapshot(dst_store) == before


def test_not_a_zip_and_truncated_zip(source, target):
    _, src_client, _ = source
    _, dst_client, dst_store = target
    blob = export_archive(src_client)
    before = snapshot(dst_store)
    for bad in (b"hello", blob[: len(blob) // 2], b""):
        r = post_run(dst_client, bad)
        assert r.status_code == 422 and codes(r.json()["errors"]) == ["not_a_zip"]
    assert snapshot(dst_store) == before


def test_attachment_hash_mismatch(source, target, tmp_path):
    _, src_client, ids = source
    _, dst_client, dst_store = target
    files = zip_files(export_archive(src_client))
    _flip(files, f"attachments/{ids['attachment']}")
    path = build_zip(files, tmp_path / "x.zip", rehash=False)
    r = post_run(dst_client, path.read_bytes())
    assert r.status_code == 422 and "hash_mismatch" in codes(r.json()["errors"])
    assert dst_store.list_attachments() == []


def test_older_schema_head_is_accepted_with_a_warning(source, target, tmp_path):
    _, src_client, _ = source
    _, dst_client, _ = target
    chain = [m.id for m in list_migrations("sqlite")]
    assert len(chain) > 1
    path = build_zip(zip_files(export_archive(src_client)), tmp_path / "old.zip", {"schema_head": chain[-2]})
    pre = post_preview(dst_client, path.read_bytes()).json()
    assert pre["ok"] is True and pre["compatibility"]["status"] == "older"
    assert "schema_older" in codes(pre["warnings"])
    resp = post_run(dst_client, path.read_bytes())
    assert resp.status_code == 200


def test_confirmation_is_required(source, target):
    _, src_client, _ = source
    _, dst_client, dst_store = target
    r = post_run(dst_client, export_archive(src_client), confirm=False)
    assert r.status_code == 422 and r.json()["detail"] == "Confirmation required."
    assert dst_store.list("customers") == []


# ------------------------------------------------------------------ atomicity
def test_failure_in_the_middle_of_the_records_writes_nothing(source, target, monkeypatch):
    _, src_client, ids = source
    dst_app, dst_client, dst_store = target
    blob = export_archive(src_client)
    before = snapshot(dst_store)
    real = dst_store.save
    calls = {"n": 0}

    def flaky(coll, obj, touch=True):
        calls["n"] += 1
        if calls["n"] == 4:
            raise RuntimeError("disk full")
        return real(coll, obj, touch=touch)

    monkeypatch.setattr(dst_store, "save", flaky)
    with pytest.raises(ServiceError) as exc:
        dst_app.state.services.transfer.run(None, _write(blob, dst_app))
    assert exc.value.status == 500 and "disk full" not in exc.value.message
    monkeypatch.setattr(dst_store, "save", real)
    assert snapshot(dst_store) == before and dst_store.list_attachments() == []
    assert not dst_store.attachment_path(ids["attachment"])


def test_failure_while_writing_attachments_writes_nothing(source, target, monkeypatch, tmp_path):
    src_app, src_client, _ = source
    dst_app, dst_client, dst_store = target
    for i in range(3):
        src_app.state.services.store.save_attachment(f"f{i}.png", b"\x89PNG\r\n\x1a\n%d" % i)
    blob = export_archive(src_client)
    before = snapshot(dst_store)
    real = dst_store.stage_attachment
    calls = {"n": 0}

    def flaky(stage, name, chunks):
        calls["n"] += 1
        if calls["n"] == 3:
            raise OSError("no space left")
        real(stage, name, chunks)

    monkeypatch.setattr(dst_store, "stage_attachment", flaky)
    with pytest.raises(ServiceError):
        dst_app.state.services.transfer.run(None, _write(blob, dst_app))
    monkeypatch.setattr(dst_store, "stage_attachment", real)
    assert snapshot(dst_store) == before and dst_store.list_attachments() == []


def _write(blob, app):
    p = app.state.services.config.data_dir / "upload.zip"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(blob)
    return p


# ------------------------------------------------------------------ users
def _two_users_archive(src_app, src_client, **export_opts):
    src_client.post("/api/users", json={"username": "mia", "name": "Mia", "role": "member", "password": PASSWORD})
    return export_archive(src_client, **export_opts)


def test_same_username_keeps_local_password_and_role(source, target):
    src_app, src_client, _ = source
    dst_app, dst_client, dst_store = target
    src_id = src_app.state.services.store.list("users")[0]["id"]
    local = dst_store.list("users")[0]
    blob = _two_users_archive(src_app, src_client, include_accounts=True)
    body = post_run(dst_client, blob).json()
    assert body["relogin"] is True and body["users"]["replaces_setup_user"] == "boss"
    users = {u["username"]: u for u in dst_store.list("users")}
    assert users["boss"]["id"] == src_id and users["boss"]["role"] == "boss"
    assert users["boss"]["password_hash"] == local["password_hash"]
    assert users["mia"]["password_hash"] and users["mia"]["active"] is not False
    assert all(not u.get("totp_enabled") for u in users.values())
    login(dst_app, "boss")  # the local password still works
    assert dst_app.state.services.auth.user_from_token(dst_client.headers["Authorization"][7:]) is None


def test_archive_without_accounts_makes_other_users_inactive(source, target):
    src_app, src_client, _ = source
    dst_app, dst_client, dst_store = target
    blob = _two_users_archive(src_app, src_client)
    body = post_run(dst_client, blob).json()
    assert body["users"]["without_password"] == ["mia"]
    assert "users_without_password" in codes(body["warnings"])
    mia = next(u for u in dst_store.list("users") if u["username"] == "mia")
    assert mia["active"] is False and not mia.get("password_hash")


def test_different_username_keeps_both(source, tmp_path):
    src_app, src_client, _ = source
    dst_app, dst_client = make_instance(tmp_path, "other")
    # rename the archive's only user so that no username matches the setup account
    files = zip_files(export_archive(src_client, include_accounts=True))
    body = json.loads(files["data/users.json"])
    body["records"][0]["username"] = "anna"
    files["data/users.json"] = json.dumps(body).encode()
    blob = build_zip(files, tmp_path / "r.zip").read_bytes()
    res = post_run(dst_client, blob).json()
    assert res["relogin"] is False and res["users"]["kept_setup_user"] == "boss"
    assert "setup_user_kept" in codes(res["warnings"])
    assert sorted(u["username"] for u in dst_app.state.services.store.list("users")) == ["anna", "boss"]


# ------------------------------------------------------------------ numbering, references, warnings
def test_numbering_continues_after_the_import(source, target):
    _, src_client, _ = source
    dst_app, dst_client, dst_store = target
    res = post_run(dst_client, export_archive(src_client)).json()
    inv = res["numbering"]["invoices"]
    assert inv["highest_imported"] == "RE-2026-0007" and inv["counter"] == 7 and inv["counter_period"] == "2026"
    numbering = NumberingService(dst_store, clock=lambda: date(2026, 4, 1))
    assert numbering.preview("invoice", 1) == ["RE-2026-0008"]
    assert numbering.allocate("invoices", date(2026, 4, 1)) == "RE-2026-0008"


def test_low_counter_gives_a_warning_and_is_not_changed(source, target, tmp_path):
    src_app, src_client, _ = source
    _, dst_client, dst_store = target
    files = zip_files(export_archive(src_client))
    body = json.loads(files["data/number_sequences.json"])
    body["records"][0]["last_value"] = 3
    files["data/number_sequences.json"] = json.dumps(body).encode()
    blob = build_zip(files, tmp_path / "low.zip").read_bytes()
    res = post_run(dst_client, blob).json()
    assert "numbering_counter_low" in codes(res["warnings"])
    assert dst_store.list("number_sequences")[0]["last_value"] == 3  # restored exactly, never recomputed


def test_dangling_references_are_warnings_not_errors(source, target, tmp_path):
    _, src_client, ids = source
    _, dst_client, dst_store = target
    files = zip_files(export_archive(src_client))
    body = json.loads(files["data/customers.json"])
    body.update(count=0, records=[])
    files["data/customers.json"] = json.dumps(body).encode()
    blob = build_zip(files, tmp_path / "d.zip").read_bytes()
    res = post_run(dst_client, blob)
    assert res.status_code == 200
    msgs = [w["message"] for w in res.json()["warnings"] if w["code"] == "dangling_reference"]
    assert any("projects[0].customer_id" in m and ids["customer"] in m for m in msgs)


def test_a_clean_archive_has_no_dangling_reference_warning(source, target):
    _, src_client, _ = source
    _, dst_client, _ = target
    res = post_run(dst_client, export_archive(src_client)).json()
    assert "dangling_reference" not in codes(res["warnings"])
    assert codes(res["warnings"]) == ["calendar_url_removed"]


def test_archive_without_attachments_warns_about_missing_files(source, target):
    _, src_client, _ = source
    _, dst_client, _ = target
    res = post_run(dst_client, export_archive(src_client, include_attachments=False)).json()
    assert "attachments_not_included" in codes(res["warnings"])


# ------------------------------------------------------------------ preview vs run, log
def test_preview_and_run_agree_and_preview_writes_nothing(source, target):
    _, src_client, _ = source
    dst_app, dst_client, dst_store = target
    blob = export_archive(src_client)
    before = snapshot(dst_store)
    pre = post_preview(dst_client, blob).json()
    assert snapshot(dst_store) == before
    run = post_run(dst_client, blob).json()
    for key in ("counts", "attachments", "users", "numbering", "warnings"):
        assert pre[key] == run[key], key
    assert pre["ok"] is True


def test_transfer_log_records_every_attempt_without_contents(source, target):
    src_app, src_client, _ = source
    dst_app, dst_client, _ = target
    blob = export_archive(src_client)
    post_run(dst_client, b"garbage")
    post_preview(dst_client, blob)
    post_run(dst_client, blob)
    client = login(dst_app, "boss")
    log = client.get("/api/data/transfers").json()
    assert [(e["action"], e["ok"]) for e in log] == [("import", True), ("import_preview", True), ("import", False)]
    assert all(set(e) == {"at", "user_id", "action", "ok", "include_accounts", "counts", "archive_sha256"} for e in log)
    assert b"Beispiel" not in json.dumps(log).encode()
    src_log = src_client.get("/api/data/transfers").json()
    assert src_log[0]["action"] == "export" and len(src_log[0]["archive_sha256"]) == 64
    assert manifest.LOG_KEY not in json.dumps(zip_files(export_archive(client))["data/settings.json"].decode())


def test_transfer_log_is_capped(source):
    app, client, _ = source
    for _i in range(55):
        app.state.services.transfer._log("u", "import_preview", True, None, {}, None)
    assert len(client.get("/api/data/transfers").json()) == 50


# ------------------------------------------------------------------ CLI
def _cli(tmp_path, name):
    return ["-c", str(tmp_path / f"{name}.yaml"), "--data", str(tmp_path / name)]


def test_cli_export_dry_run_and_import(source, tmp_path, capsys):
    src_app, _, _ = source
    src_app.state.services.store.close()
    out = tmp_path / "x.zip"
    main([*_cli(tmp_path, "src"), "export", str(out)])
    text = capsys.readouterr().out
    assert "SHA-256" in text and "customers: 1" in text
    if os.name != "nt":  # Windows has no POSIX file modes
        assert stat.S_IMODE(out.stat().st_mode) == 0o600
    with pytest.raises(SystemExit, match="exists"):
        main([*_cli(tmp_path, "src"), "export", str(out)])
    main([*_cli(tmp_path, "src"), "export", str(out), "--force"])
    capsys.readouterr()

    main([*_cli(tmp_path, "fresh"), "import", str(out), "--dry-run"])  # no users at all: allowed for the CLI
    assert "Dry run" in capsys.readouterr().out
    main([*_cli(tmp_path, "fresh"), "import", str(out), "--yes"])
    res = capsys.readouterr().out
    assert "Imported." in res and "next number RE-" in res and "set-password" in res
    with pytest.raises(SystemExit):  # now the instance has data
        main([*_cli(tmp_path, "fresh"), "import", str(out), "--yes"])


def test_cli_include_accounts_needs_yes_without_a_terminal(source, tmp_path):
    src_app, _, _ = source
    src_app.state.services.store.close()
    with pytest.raises(SystemExit, match="--yes"):
        main([*_cli(tmp_path, "src"), "export", str(tmp_path / "a.zip"), "--include-accounts"])
    main([*_cli(tmp_path, "src"), "export", str(tmp_path / "a.zip"), "--include-accounts", "--yes"])
    assert b"password_hash" in zip_files((tmp_path / "a.zip").read_bytes())["data/users.json"]


def test_cli_usage_error_exit_code(tmp_path):
    with pytest.raises(SystemExit) as e:
        main([*_cli(tmp_path, "x"), "import"])
    assert e.value.code == 2


def test_cli_refuses_a_corrupt_archive(tmp_path):
    bad = tmp_path / "bad.zip"
    bad.write_bytes(b"nope")
    with pytest.raises(SystemExit) as e:
        main([*_cli(tmp_path, "y"), "import", str(bad), "--dry-run"])
    assert e.value.code not in (0, 2)
