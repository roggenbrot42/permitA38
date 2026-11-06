"""Storage-layer behaviour of the database guards: the API answer, untranslated integrity errors and the startup
log (docs/specs/db-constraints.md, "Storage layer"). All data is invented."""

import logging
import sqlite3

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.numbering import NumberingService

from .conftest import PASSWORD
from .test_migrations import DIALECT_NAMES, real_chain_copy, use_chain


def _draft(c, customer_id):
    return c.post(
        "/api/invoices",
        json={"customer_id": customer_id, "lines": [{"description": "Work", "qty": 1, "unit": "h", "price": 10}]},
    ).json()


def test_forced_duplicate_number_is_a_409_with_the_code(boss_client, monkeypatch):
    c = boss_client
    cust = c.post("/api/customers", json={"name": "ACME GmbH"}).json()
    first = _draft(c, cust["id"])
    fin = c.post(f"/api/invoices/{first['id']}/finalize")
    assert fin.status_code == 200
    number = fin.json()["number"]
    monkeypatch.setattr(NumberingService, "allocate", lambda self, coll, day: number)
    second = _draft(c, cust["id"])
    r = c.post(f"/api/invoices/{second['id']}/finalize")
    assert r.status_code == 409
    assert r.json() == {
        "detail": "The database refused the change (numbering.duplicate).",
        "code": "numbering.duplicate",
    }
    again = c.get(f"/api/invoices/{second['id']}").json()
    assert again["status"] == "draft" and not again.get("number")  # nothing was written


def test_other_integrity_errors_keep_their_type(app, boss_client):
    store = app.state.services.store
    with pytest.raises(sqlite3.IntegrityError) as e:
        store.save("users", {"username": "boss", "name": "Second Boss"})
    assert not str(e.value).startswith("permita38:")


# ------------------------------------------------------------------ startup log
def _config(tmp_path):
    return Config(data_dir=tmp_path / "data", secret_key="test-secret-key", web_dir=None)


@pytest.fixture
def db_with_duplicate(tmp_path, monkeypatch):
    """A database at the head before the guards (0016) holding two invoices with the same number."""
    root = real_chain_copy(tmp_path)
    for d in DIALECT_NAMES:
        # 0017 requires 0016 and 0018 requires 0017: remove every migration after 0015
        for later in (root / d).glob("*.sql"):
            if int(later.name[:4]) > 15:
                later.unlink()
    with monkeypatch.context() as m:
        use_chain(m, root)
        old = SQLiteRepository(tmp_path / "data")
        old.startup()
        old.save("invoices", {"number": "RE-2026-0007", "title": "inv001 first"})
        old.save("invoices", {"number": "RE-2026-0007", "title": "inv001 second"})
        old.save("invoices", {"number": "RE-2026-0008", "title": "inv001 other"})
        old.close()


def test_startup_logs_one_error_line_with_the_duplicate_numbers(db_with_duplicate, tmp_path, caplog):
    with caplog.at_level(logging.DEBUG):
        with TestClient(get_app(_config(tmp_path))):
            pass
    errors = [r for r in caplog.records if r.levelno == logging.ERROR and "uplicate" in r.getMessage()]
    assert len(errors) == 1
    msg = errors[0].getMessage()
    assert "RE-2026-0007" in msg and "RE-2026-0008" not in msg and "inv001" not in msg


def test_startup_is_silent_on_a_clean_database(tmp_path, caplog):
    with caplog.at_level(logging.DEBUG):
        with TestClient(get_app(_config(tmp_path))) as c:
            c.post(
                "/api/auth/setup",
                json={"username": "boss", "name": "Test Boss", "password": PASSWORD, "company_name": "Demo Studio"},
            )
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING and "uplicate" in r.getMessage()]


def test_a_crashing_check_is_logged_at_warning_and_never_fails_startup(tmp_path, caplog, monkeypatch):
    from permita38.api import deps

    def boom(ctx):
        raise RuntimeError("check broke")

    monkeypatch.setattr(deps, "rule_numbering_duplicates", boom)
    with caplog.at_level(logging.DEBUG):
        with TestClient(get_app(_config(tmp_path))):
            pass
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "check broke" in r.getMessage()]
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]
