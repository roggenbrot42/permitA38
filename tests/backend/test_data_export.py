"""Data export: archive content, manifest, secrets, CSV, master data, info (docs/contracts/data-export.md)."""

import hashlib
import json
import stat
import zipfile

import pytest

from permita38.core.database import head_id
from permita38.core.repository.schema import TABLES
from permita38.core.service.transfer import csvout, manifest
from permita38.core.service.transfer.importer import LIST_REFERENCES, REFERENCES

from .transfer_helpers import export_archive, make_instance, seed, zip_files

CANARIES = {
    "totp_secret": "JBSWY3DPEHPK3PXPCANARY",
    "recovery": "recovery-canary-code-0042",
    "calendar_url": "https://example.invalid/secret-feed-token",
    "api_key": "sk-canary-api-key-9981",
}


@pytest.fixture
def source(tmp_path):
    app, client = make_instance(tmp_path, "src")
    store = app.state.services.store
    ids = seed(store)
    boss = store.list("users")[0]
    store.save(
        "users",
        {
            **boss,
            "totp_secret": CANARIES["totp_secret"],
            "totp_enabled": True,
            "totp_last_step": 123456789,
            "totp_recovery": [CANARIES["recovery"]],
        },
    )
    st = store.settings()
    st["x_api_key"] = CANARIES["api_key"]
    store.save_settings(st)
    return app, client, store, ids


def test_every_table_is_exported_or_explicitly_excluded():
    exported = set(manifest.exported_collections())
    assert exported | set(manifest.EXCLUDED_TABLES) == set(TABLES)
    assert not exported & set(manifest.EXCLUDED_TABLES)
    assert all(reason for reason in manifest.EXCLUDED_TABLES.values())


def test_every_id_column_is_in_the_reference_table():
    id_columns = {c.name for t in TABLES.values() for c in t.columns if c.type == "id" and c.name != "id"}
    assert id_columns <= set(REFERENCES), id_columns - set(REFERENCES)
    for key in LIST_REFERENCES:
        coll, col = key.split(".")
        assert TABLES[coll].column(col) is not None


def test_no_stored_setting_name_matches_the_denylist_unexcluded(source):
    _, _, store, _ = source
    exported = manifest.exportable_settings(store.settings())
    assert not any(manifest.SECRET_SETTING_RE.search(k) for k in exported)
    assert "x_api_key" not in exported and manifest.LOG_KEY not in exported


def test_manifest_counts_hashes_and_head(source):
    _, client, store, _ = source
    files = zip_files(export_archive(client))
    man = json.loads(files["manifest.json"])
    assert man["format"] == "permita38-export" and man["format_version"] == 1
    assert man["schema_head"] == head_id() and man["scope"] == "full"
    assert man["excluded"] and man["include_accounts"] is False
    assert man["counts"] == {c: len(store.list(c)) for c in manifest.exported_collections()}
    assert set(man["files"]) == set(files) - {"manifest.json"}
    for name, meta in man["files"].items():
        assert meta["bytes"] == len(files[name])
        assert meta["sha256"] == hashlib.sha256(files[name]).hexdigest()
    assert man["numbering"] == {"invoices": {"period": "2026", "last_value": 7}}


def test_json_files_are_lossless_with_unknown_fields(source):
    _, client, _, ids = source
    files = zip_files(export_archive(client))
    body = json.loads(files["data/customers.json"])
    assert body["collection"] == "customers" and body["count"] == 1
    assert body["records"][0]["x_future_field"] == {"tags": ["a", "b"], "rank": 3}
    assert body["records"][0]["id"] == ids["customer"]
    assert files[f"attachments/{ids['attachment']}"] == b"%PDF-1.4 demo bill"


def test_secrets_never_leave_the_server(source):
    _, client, _, _ = source
    for accounts in (False, True):
        blob = export_archive(client, include_accounts=accounts)
        for name, content in zip_files(blob).items():
            for label, canary in CANARIES.items():
                assert canary.encode() not in content, (name, label)
        assert b"test-secret-key" not in blob
        users = json.loads(zip_files(blob)["data/users.json"])["records"]
        assert all(not any(k.startswith("totp") for k in u) for u in users)
        assert ("password_hash" in users[0]) is accounts
        cal = json.loads(zip_files(blob)["data/calendar_sources.json"])["records"]
        assert cal[0]["url"] == ""
    csv_users = zip_files(blob)["csv/users.csv"].decode("utf-8-sig")
    assert "totp" not in csv_users and "password_hash" in csv_users


def test_users_without_accounts_have_no_hash_in_any_file(source):
    _, client, store, _ = source
    h = store.list("users")[0]["password_hash"].encode()
    for content in zip_files(export_archive(client)).values():
        assert h not in content


def test_csv_golden_bytes():
    cols = ["id", "created", "name", "rate", "flag"]
    recs = [
        {"id": "a1b2c3d4", "created": 1, "name": "=1+1", "rate": 95.0, "flag": True, "x_note": "a;b"},
        {"id": "e5f6a7b8", "created": 2, "name": 'say "hi"\nthere', "rate": -12.5, "flag": False},
    ]
    expected = (
        "﻿id;created;name;rate;flag;x_note\r\na1b2c3d4;1;'=1+1;95.0;true;a;b\r\n".replace("a;b", '"a;b"')
        + 'e5f6a7b8;2;"say ""hi""\nthere";-12.5;false;\r\n'
    )
    assert csvout.csv_bytes(cols, recs) == expected.encode("utf-8")


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r", "  =", "  @"])
def test_csv_formula_injection_is_defused(lead):
    value = lead + "cmd|' /C calc'!A0"
    out = csvout.csv_bytes(["id", "note"], [{"id": "a1b2c3d4", "note": value}]).decode("utf-8-sig")
    assert csvout.safe_text(value).startswith("'")
    assert "'" + value in out.replace('"', "")


def test_csv_headers_are_defused_and_numbers_are_not_prefixed():
    out = csvout.csv_bytes(["id", "n"], [{"id": "a1b2c3d4", "n": -5, "=evil": "x"}]).decode("utf-8-sig")
    assert out.splitlines()[0] == "id;n;'=evil"
    assert out.splitlines()[1] == "a1b2c3d4;-5;x"
    assert csvout.safe_text("") == "" and csvout.safe_text("plain") == "plain"


def test_csv_in_archive_has_bom_and_crlf(source):
    _, client, _, _ = source
    raw = zip_files(export_archive(client))["csv/expenses.csv"]
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    assert b"'=Shop" in raw  # the vendor starts with "=" and is defused


def test_without_attachments_writes_no_attachment_entries(source):
    _, client, _, _ = source
    files = zip_files(export_archive(client, include_attachments=False))
    assert not [n for n in files if n.startswith("attachments/")]
    assert json.loads(files["manifest.json"])["include_attachments"] is False


def test_master_data_archive(source):
    _, client, _, _ = source
    files = zip_files(export_archive(client, scope="master_data"))
    assert sorted(n for n in files if n.startswith("data/")) == ["data/customers.json"]
    assert sorted(n for n in files if n.startswith("csv/")) == ["csv/customers.csv"]
    assert not [n for n in files if n.startswith("attachments/")]
    man = json.loads(files["manifest.json"])
    assert man["scope"] == "master_data" and man["counts"] == {"customers": 1}
    for bad in ({"include_accounts": True}, {"include_attachments": False}):
        r = client.post("/api/data/export", json={"scope": "master_data", **bad})
        assert r.status_code == 422


def test_document_importer_accepts_the_master_data_json(source, tmp_path):
    _, client, _, _ = source
    body = zip_files(export_archive(client, scope="master_data"))["data/customers.json"]
    app2, c2 = make_instance(tmp_path, "other")
    r = c2.post("/api/import/preview", files={"file": ("customers.json", body)}, data={"type": "customers"})
    assert r.status_code == 200, r.text
    assert r.json()["counts"]["create"] == 1


def test_archive_file_mode_and_temp_file_removed(source):
    app, client, _, _ = source
    export_archive(client)
    tmp = app.state.services.config.data_dir / "tmp"
    assert not list(tmp.glob("permita38-*"))


def test_export_info_shape(source):
    _, client, store, _ = source
    info = client.get("/api/data/export/info").json()
    assert set(info) == {"counts", "attachments", "estimated_bytes", "app_version", "schema_head", "import"}
    assert info["counts"] == {c: len(store.list(c)) for c in manifest.exported_collections()}
    assert info["attachments"]["files"] == 1 and info["schema_head"] == head_id()
    assert info["import"]["possible"] is False and "already contains data" in info["import"]["reason"]


def test_contract_examples_have_the_documented_keys(source, tmp_path):
    """The key sets of the JSON examples in docs/contracts/data-export.md."""
    _, client, _, _ = source
    text = (zipfile_root() / "docs/contracts/data-export.md").read_text(encoding="utf-8")
    blocks = [b.split("```", 1)[0] for b in text.split("```json\n")[1:]]
    examples = [json.loads(b) for b in blocks if b.lstrip().startswith("{")]
    by_keys = {frozenset(e): e for e in examples if e.get("ok") is not False}
    app2, c2 = make_instance(tmp_path, "shape")
    blob = export_archive(client)
    preview = c2.post("/api/data/import/preview", files={"file": ("a.zip", blob)}).json()
    assert frozenset(preview) in by_keys, sorted(preview)
    assert set(preview["manifest"]) == set(by_keys[frozenset(preview)]["manifest"])
    info = client.get("/api/data/export/info").json()
    assert frozenset(info) in by_keys
    man = json.loads(zip_files(blob)["manifest.json"])
    assert frozenset(man) in by_keys
    run = c2.post("/api/data/import/run", files={"file": ("a.zip", blob)}, data={"confirm": "true"}).json()
    assert set(run) == {"imported", "counts", "attachments", "relogin", "users", "numbering", "warnings"}
    assert set(run["users"]) == {"replaces_setup_user", "kept_setup_user", "without_password", "with_login", "bosses"}
    log = c2.post("/api/auth/login", json={"username": "boss", "password": "correct horse battery"})
    assert log.status_code == 200
    c2.headers["Authorization"] = "Bearer " + log.json()["token"]
    entry = c2.get("/api/data/transfers").json()[0]
    assert set(entry) == {"at", "user_id", "action", "ok", "include_accounts", "counts", "archive_sha256"}


def zipfile_root():
    from pathlib import Path

    return Path(__file__).resolve().parents[2]


def test_archive_entries_are_plain_unencrypted_files(source):
    _, client, _, _ = source
    blob = export_archive(client)
    import io

    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        for info in zf.infolist():
            assert stat.S_ISREG(info.external_attr >> 16) and not info.flag_bits & 1
