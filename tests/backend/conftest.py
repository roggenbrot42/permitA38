import os
import re

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.service import auth

PASSWORD = "correct horse battery"
#: the real cost factor; tests run with a cheap one (see `cheap_password_hashing`) and one test pins this value
PRODUCTION_PBKDF2_ITERATIONS = auth.PBKDF2_ITERATIONS


@pytest.fixture(autouse=True)
def cheap_password_hashing(monkeypatch):
    """Every new password hash costs ~0.1 s at the production cost factor, which dominated the suite. Stored hashes
    carry their own iteration count, so verification is unaffected."""
    monkeypatch.setattr(auth, "PBKDF2_ITERATIONS", 1000)


def pytest_ignore_collect(collection_path, config):
    """`pytest` / `make test` is the fast suite. The randomized suite (tests/backend/randomized) is only collected
    when it is asked for: by path, with `-m randomized`, or with PERMITA38_RANDOM=1."""
    if collection_path.name != "randomized" or not collection_path.is_dir():
        return None
    asked = (
        os.environ.get("PERMITA38_RANDOM") == "1"
        or "randomized" in (config.getoption("markexpr") or "")
        or any("randomized" in str(a) for a in config.invocation_params.args)
    )
    return None if asked else True


@pytest.fixture(autouse=True)
def _close_transfer_clients():
    """Clients made by the data export/import helpers are closed after every test."""
    yield
    from .transfer_helpers import close_all

    close_all()


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path / "data", secret_key="test-secret-key", web_dir=None)


@pytest.fixture
def app(config):
    return get_app(config)


@pytest.fixture
def client(app):
    """Anonymous client."""
    with TestClient(app) as c:
        yield c


def _login(app, username, password=PASSWORD):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


@pytest.fixture
def boss_client(app, client):
    r = client.post(
        "/api/auth/setup",
        json={"username": "boss", "name": "Test Boss", "password": PASSWORD, "company_name": "Demo Studio"},
    )
    assert r.status_code == 200, r.text
    c = _login(app, "boss")
    yield c
    c.close()


@pytest.fixture
def member_client(app, boss_client):
    r = boss_client.post(
        "/api/users", json={"username": "mia", "name": "Mia Member", "role": "member", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    c = _login(app, "mia")
    c.user_id = r.json()["id"]  # type: ignore[attr-defined]
    yield c
    c.close()


@pytest.fixture
def scan_stubs(app, monkeypatch):
    """Receipt and document scans without an OCR / PDF engine: returns (receipt_reader, doc_reader); `reader.add(...)`
    yields the file content that the stub 'reads'. Both services share one runner module, so dispatch on the content."""
    from permita38.core.service import receipts as receipts_mod

    from .test_document_scans_service import TODAY as SCAN_TODAY
    from .test_document_scans_service import Reader as DocReader
    from .test_receipts_api import TODAY as RECEIPT_TODAY
    from .test_receipts_api import Reader as ReceiptReader

    receipt_reader, doc_reader = ReceiptReader(), DocReader()

    def read_file(path, config):
        with open(path, "rb") as f:
            data = f.read()
        return (receipt_reader if data in receipt_reader.by_content else doc_reader)(path, config)

    monkeypatch.setattr(receipts_mod.runner, "read_file", read_file)
    app.state.services.receipts._today = lambda: RECEIPT_TODAY
    app.state.services.document_scans._today = lambda: SCAN_TODAY
    return receipt_reader, doc_reader


@pytest.fixture
def unguarded():
    """plant(store, coll, rec, code): store a record as a database written before the guards could hold it.

    `code` is the guard code the row breaks. A normal save must first be refused with exactly that code (the guard
    is live and the bypass is needed) and store nothing; only then is the row saved with just that guard kind off."""
    from permita38.core.repository import schema
    from permita38.core.repository.base import ConstraintViolation
    from permita38.core.repository.dialect import DIALECTS

    dialect = DIALECTS["sqlite"]
    fixed = {
        "format.date": "date",
        "range.negative": "non_negative",
        "state.period_reversed": "order",
        "vocab.unknown": "one_of",
        "state.exactly_one": "exactly_one",
        "state.required": "required",
        "range.too_long": "max_days",
        "range.bounds": "range",
    }

    class _Kinds:
        """code -> guard kind a restore may suspend (never the unique number guard)."""

        def __contains__(self, code):
            return code in fixed or code.startswith("state.implies:")

        def __getitem__(self, code):
            return "implies" if code.startswith("state.implies:") else fixed[code]

    kinds = _Kinds()

    def guard_names(store):
        with store.lock:
            return {n for (n,) in store._conn.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")}

    def expected_names():
        return {
            m.group(1)
            for g in schema.GUARDS
            for s in dialect.guard_sql(g)
            if (m := re.match(r'CREATE TRIGGER "([^"]+)"', s))
        }

    def plant(store, coll, rec, code):
        # a row that breaks several guard kinds at once names all their codes (a tuple); the refusal is the first one
        codes = (code,) if isinstance(code, str) else tuple(code)
        before = len(store.list(coll))
        with pytest.raises(ConstraintViolation) as refused:
            store.save(coll, rec)
        assert refused.value.code in codes, "the row must break the guard it is planted for"
        assert len(store.list(coll)) == before, "a refused save stores nothing"
        if all(c in kinds for c in codes):
            with store.transaction(), store.guards_suspended({kinds[c] for c in codes}):
                saved = store.save(coll, rec)
        elif codes == ("numbering.duplicate",):
            # guards_suspended() refuses the unique guard on purpose; this models a database written before the guards
            with store.transaction():
                conn = store._conn
                unique = [g for g in schema.GUARDS if g.kind == "unique"]
                for g in unique:
                    for suffix in ("_ins", "_upd"):
                        conn.execute(f'DROP TRIGGER "{g.name}{suffix}"')
                try:
                    saved = store.save(coll, rec)
                finally:
                    for g in unique:
                        for stmt in dialect.guard_sql(g):
                            conn.execute(stmt)
        else:
            raise AssertionError(f"no bypass for guard codes {codes!r}")
        assert expected_names() <= guard_names(store), "all guard triggers are back"
        return saved

    return plant
