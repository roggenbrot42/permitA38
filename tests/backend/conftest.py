import os
import re
import shutil
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core import charts
from permita38.core.config import Config
from permita38.core.service import auth
from permita38.core.service.journal import JournalSource, PostLine

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
        "ref.dangling": "reference",
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


CHART_FIXTURE = Path(__file__).parent / "fixtures" / "chart_synthetic.json"


@pytest.fixture
def chart_dir(tmp_path, monkeypatch):
    d = tmp_path / "charts"
    d.mkdir()
    shutil.copy(CHART_FIXTURE, d / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", d)
    return d


@pytest.fixture
def scenario(app, boss_client, chart_dir):
    """Four entries in 2026 (entry numbers 1..4):

    1  2026-01-10 invoice inv1: receivables +119.00, revenue -100.00, VAT -19.00 (tax key S19)
    2  2026-01-20 payment of 50.00 on inv1: bank +50.00, receivables -50.00
    3  2026-02-05 software expense: expense +30.00, input VAT +5.70, bank -35.70
    4  2026-02-10 other expense on credit exp1: expense +20.00, payables -20.00
    """
    r = boss_client.post("/api/accounting/years", json={"year": 2026, "chart": "skr03", "income_basis": "accrual"})
    assert r.status_code == 201, r.text
    d = date
    vat = PostLine(
        role="vat_out_s19", amount_cents=-1900, tax_key="S19", tax_kind="tax", tax_rate="19", tax_base_cents=10000,
        vat_date=d(2026, 1, 10), income_date=d(2026, 1, 10),
    )  # fmt: skip
    _post_entry(
        app, "e1", d(2026, 1, 10), "Invoice 1", "issue", "invoices", "inv1", "R-1",
        [
            PostLine(role="receivables", amount_cents=11900, open_item_id="inv1"),
            PostLine(role="revenue_s19", amount_cents=-10000, income_date=d(2026, 1, 10)),
            vat,
        ],
    )  # fmt: skip
    _post_entry(
        app, "e2", d(2026, 1, 20), "Payment 1", "payment", "invoices", "inv1", "R-1",
        [
            PostLine(role="bank", amount_cents=5000),
            PostLine(role="receivables", amount_cents=-5000, open_item_id="inv1"),
        ],
    )  # fmt: skip
    _post_entry(
        app, "e3", d(2026, 2, 5), "Software", "expense", "expenses", "exp0", None,
        [
            PostLine(role="expense_software", amount_cents=3000, income_date=d(2026, 2, 5)),
            PostLine(role="input_vat_clearing", amount_cents=570, income_date=d(2026, 2, 5)),
            PostLine(role="bank", amount_cents=-3570),
        ],
    )  # fmt: skip
    _post_entry(
        app, "e4", d(2026, 2, 10), "Other", "expense", "expenses", "exp1", None,
        [
            PostLine(role="expense_other", amount_cents=2000, income_date=d(2026, 2, 10)),
            PostLine(role="payables", amount_cents=-2000, open_item_id="exp1"),
        ],
    )  # fmt: skip
    return boss_client


@pytest.fixture
def eur_scenario(app, boss_client, chart_dir):
    """Two cash-basis (EÜR) years 2025 and 2026 (the 2026 chart file serves both). Entry numbers per year:

    2025/1  2025-11-03 paid invoice: bank +119.00, revenue -100.00, VAT -19.00 (income_date 2025-11-03)
    2025/2  2025-12-28 software: expense +30.00, input VAT +5.70, bank -35.70 (income_date 2025-12-28)
    2025/3  2025-12-30 recurring cost paid early (10-day rule shape): expense_other +20.00 / bank -20.00,
            income_date 2026-01-05
    2026/1  2026-01-15 software: expense +10.00 / bank -10.00 (income_date 2026-01-15)
    """
    for year in (2025, 2026):
        r = boss_client.post("/api/accounting/years", json={"year": year, "chart": "skr03", "income_basis": "eur"})
        assert r.status_code == 201, r.text
    d = date
    vat = PostLine(
        role="vat_out_s19", amount_cents=-1900, tax_key="S19", tax_kind="tax", tax_rate="19", tax_base_cents=10000,
        vat_date=d(2025, 11, 3), income_date=d(2025, 11, 3),
    )  # fmt: skip
    _post_entry(
        app, "x1", d(2025, 11, 3), "Paid invoice", "payment", "invoices", "inv9", "R-9",
        [
            PostLine(role="bank", amount_cents=11900),
            PostLine(role="revenue_s19", amount_cents=-10000, income_date=d(2025, 11, 3)),
            vat,
        ],
    )  # fmt: skip
    _post_entry(
        app, "x2", d(2025, 12, 28), "Software", "expense", "expenses", "exp7", None,
        [
            PostLine(role="expense_software", amount_cents=3000, income_date=d(2025, 12, 28)),
            PostLine(role="input_vat_clearing", amount_cents=570, income_date=d(2025, 12, 28)),
            PostLine(role="bank", amount_cents=-3570),
        ],
    )  # fmt: skip
    _post_entry(
        app, "x3", d(2025, 12, 30), "Recurring", "expense", "expenses", "exp8", None,
        [
            PostLine(role="expense_other", amount_cents=2000, income_date=d(2026, 1, 5)),
            PostLine(role="bank", amount_cents=-2000),
        ],
    )  # fmt: skip
    _post_entry(
        app, "x4", d(2026, 1, 15), "Software 2", "expense", "expenses", "exp9", None,
        [
            PostLine(role="expense_software", amount_cents=1000, income_date=d(2026, 1, 15)),
            PostLine(role="bank", amount_cents=-1000),
        ],
    )  # fmt: skip
    return boss_client


def _post_entry(app, key, day, text, event, coll, sid, number, lines):
    source = JournalSource(event=event, created_by="u1x000", source_coll=coll, source_id=sid, source_number=number)
    app.state.services.journal.post(key, day, text, source, lines)


@pytest.fixture
def none_filed(monkeypatch):
    """Opt-in: answer every ended VAT period with "none filed" (accounting.md 16 step 5b) when a test sends no
    ``external_returns`` itself; a test that passes any is left alone. Request it explicitly (``usefixtures``)."""
    from permita38.core import models
    from permita38.core.errors import ServiceError
    from permita38.core.service.accounting_init import AccountingInitService

    real = AccountingInitService.run

    def run(self, params, user=None, today=None):
        if not params.external_returns:
            try:
                periods = self.external_periods(params, today)
            except ServiceError:  # an invalid start: the run itself refuses
                periods = []
            none = [models.ExternalReturnIn(year=p.year, kind=p.kind, no=p.no, none_filed=True) for p in periods]
            params = params.model_copy(update={"external_returns": none})
        return real(self, params, user, today)

    monkeypatch.setattr(AccountingInitService, "run", run)
