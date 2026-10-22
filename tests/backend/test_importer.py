"""Import of the legacy file-per-record JSON layout."""

import json

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.cli.main import main
from permita38.core.config import Config
from permita38.core.repository.importer import import_json, is_legacy_layout
from permita38.core.repository.sqlite import SQLiteRepository


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj), encoding="utf-8")


@pytest.fixture
def legacy(tmp_path):
    old = tmp_path / "old"
    write(old / "settings.json", {"company_name": "Old Studio", "vat_rate": 19, "kleinunternehmer": False})
    write(old / "customers" / "cust00000001.json", {
        "id": "cust00000001", "name": "ACME GmbH", "rate": 95, "created": 1700000000, "updated": 1700000100,
        "city": "München", "legacy_field": {"x": [1]},
    })  # fmt: skip
    write(
        old / "customers" / "cust00000001.sync-conflict-20260101-AAA.json", {"id": "cust00000001", "name": "CONFLICT"}
    )
    write(old / "projects" / "proj00000001.json", {
        "id": "proj00000001", "name": "Website", "customer_id": "cust00000001", "member_ids": [], "personal": False,
    })  # fmt: skip
    write(old / "entries" / "entr00000001.json", {
        "id": "entr00000001", "user_id": "user00000001", "date": "2026-01-05", "minutes": 90, "billable": True,
        "project_id": "proj00000001",
    })  # fmt: skip
    write(old / "users" / "user00000001.json", {
        "id": "user00000001", "name": "Alex Example", "role": "boss", "weekly_hours": 40, "pin_hash": "abc", "salt": "00",
    })  # fmt: skip
    write(old / "users" / "user00000002.json", {"id": "user00000002", "name": "Alex  Example!", "role": "member"})
    write(old / "timers" / "user00000001.json", {"id": "user00000001", "running": True, "started": 1700000000})
    (old / "attachments").mkdir()
    (old / "attachments" / "recpt0000001.pdf").write_bytes(b"%PDF")
    return old


def test_detects_the_legacy_layout(tmp_path, legacy):
    assert is_legacy_layout(legacy)
    assert not is_legacy_layout(tmp_path / "nothing")
    (tmp_path / "empty").mkdir()
    assert not is_legacy_layout(tmp_path / "empty")


def test_import_preserves_records_and_reports(tmp_path, legacy):
    repo = SQLiteRepository(tmp_path / "new")
    rep = import_json(repo, legacy)
    assert rep.imported == {"customers": 1, "projects": 1, "entries": 1, "users": 2, "timers": 1}
    assert rep.settings and rep.attachments == 1
    c = repo.get("customers", "cust00000001")
    assert c == {
        "id": "cust00000001",
        "name": "ACME GmbH",
        "rate": 95.0,
        "created": 1700000000,
        "updated": 1700000100,
        "city": "München",
        "legacy_field": {"x": [1]},
    }  # fmt: skip  (timestamps preserved, unknown fields kept, conflict copy ignored)
    assert repo.settings()["company_name"] == "Old Studio"
    assert repo.get("entries", "entr00000001")["minutes"] == 90
    assert repo.get("timers", "user00000001")["running"] is True
    assert repo.attachment_path("recpt0000001.pdf")
    # users had no usernames: derived, unique, and flagged as unable to log in
    names = sorted(u["username"] for u in repo.list("users"))
    assert names == ["alex-example", "alex-example-2"]
    assert len(rep.usernames_derived) == 2
    assert sorted(rep.users_without_password) == names
    repo.close()


def test_import_is_idempotent_and_keeps_existing_data(tmp_path, legacy):
    repo = SQLiteRepository(tmp_path / "new")
    import_json(repo, legacy)
    repo.save("customers", {"id": "cust00000001", "name": "Edited after import"})
    repo.save_settings({"company_name": "Edited"})
    rep = import_json(repo, legacy)
    assert rep.imported == {} or all(n == 0 for n in rep.imported.values())
    assert repo.get("customers", "cust00000001")["name"] == "Edited after import"
    assert repo.settings() == {"company_name": "Edited"}
    assert len(repo.list("customers")) == 1
    repo.close()


def test_import_is_atomic(tmp_path, legacy, monkeypatch):
    repo = SQLiteRepository(tmp_path / "new")
    real = repo.save

    def flaky(coll, obj, touch=True):
        if coll == "entries":
            raise RuntimeError("disk full")
        return real(coll, obj, touch)

    monkeypatch.setattr(repo, "save", flaky)
    with pytest.raises(RuntimeError):
        import_json(repo, legacy)
    assert repo.list("customers") == [] and repo.settings() == {}  # nothing half-imported
    repo.close()


def test_cli_import_then_login_after_set_password(tmp_path, legacy, capsys):
    cfg_file = tmp_path / "cfg.yaml"
    data = tmp_path / "new"
    main(["-c", str(cfg_file), "--data", str(data), "import-json", str(legacy)])
    out = capsys.readouterr().out
    assert "customers: 1 imported" in out and "permita38 set-password" in out
    cfg = Config(data_dir=data, secret_key="s" * 16, web_dir=None)
    app = get_app(cfg)
    with TestClient(app) as c:
        assert c.get("/api/auth/status").json()["setup_needed"] is False
        app.state.services.auth.set_password("alex-example", "a long enough password")
        r = c.post("/api/auth/login", json={"username": "alex-example", "password": "a long enough password"})
        assert r.status_code == 200
        assert c.post("/api/auth/login", json={"username": "alex-example-2", "password": "x" * 9}).status_code == 401


def test_nonexistent_source_directory(tmp_path):
    repo = SQLiteRepository(tmp_path / "new")
    with pytest.raises(FileNotFoundError):
        import_json(repo, tmp_path / "nope")
    repo.close()
