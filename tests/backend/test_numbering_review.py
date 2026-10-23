"""Numbering: follow-ups of the review (legacy patterns, rollback, counter persistence, bounds, permissions)."""

import threading
from datetime import date

import pytest

from permita38.core.errors import ServiceError
from permita38.core.numbering import parse_pattern, sorting_warnings
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.numbering import NumberingService
from permita38.core.service.records import RecordService
from permita38.core.service.sales import SalesService


@pytest.fixture
def store(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    yield repo
    repo.close()


def make_sales(store, day):
    numbering = NumberingService(store, clock=lambda: day)
    return SalesService(store, RecordService(store), numbering=numbering)


def make_customer(store):
    return store.save("customers", dict(name="Demo Client GmbH"))


def make_invoice(store, customer_id, day, **extra):
    doc = dict(customer_id=customer_id, date=day.isoformat(), lines=[dict(description="Work", qty=1, price=100)])
    doc.update(extra)
    return store.save("invoices", doc)


DAY = date(2026, 5, 5)
LEGACY = ["RE+{year}({n:04d}", "{n:4d}", "R_{year}_{n}", "Rechnung Nr. {year}/{n:03d} (alt)", "{year}§{n:d}"]


@pytest.mark.parametrize("legacy", LEGACY)
def test_legacy_stored_patterns_do_not_crash(store, legacy):
    """Patterns saved by older versions (free literal text, python-format widths) keep working: no HTTP 500."""
    sales = make_sales(store, DAY)
    store.save_settings({**store.settings(), "invoice_pattern": legacy})
    cust = make_customer(store)
    assert sales.numbering.status("invoice")["next"], legacy
    a = sales.finalize("invoices", make_invoice(store, cust["id"], DAY)["id"])["number"]
    b = sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 5, 6))["id"])["number"]
    assert a and b and a != b
    assert sales.numbering.check("invoice")["preview"]


def test_legacy_width_without_zero_is_zero_padded():
    assert parse_pattern("{n:4d}").format(date(2026, 1, 1), 7) == "0007"
    assert parse_pattern("{n:04d}").match("0007") == 7


def test_unparseable_stored_pattern_is_a_service_error_not_a_crash(store):
    sales = make_sales(store, DAY)
    store.save_settings({**store.settings(), "invoice_pattern": "RE-{foo}"})
    for call in (lambda: sales.numbering.status("invoice"), lambda: sales.numbering.preview("invoice")):
        with pytest.raises(ServiceError) as e:
            call()
        assert e.value.status == 409
    res = sales.numbering.update("invoice", pattern="RE-{year}-{n:04d}", confirm=True)  # repairable
    assert res["applied"] and res["previous"]["next"] is None


def test_finalize_rolls_the_counter_back_when_saving_fails(store, monkeypatch):
    sales = make_sales(store, DAY)
    cust = make_customer(store)
    inv = make_invoice(store, cust["id"], DAY)
    real_save = store.save
    state = {"fail": True}

    def flaky(coll, doc):
        if coll == "invoices" and state["fail"]:
            state["fail"] = False
            raise RuntimeError("disk full")
        return real_save(coll, doc)

    monkeypatch.setattr(store, "save", flaky)
    with pytest.raises(RuntimeError):
        sales.finalize("invoices", inv["id"])  # the counter was advanced inside the transaction before this failed
    assert store.get("invoices", inv["id"]).get("number") is None
    assert sales.finalize("invoices", inv["id"])["number"] == "RE-2026-0001"


def test_import_after_counter_row_exists_moves_forward_and_never_back(store):
    sales = make_sales(store, DAY)
    cust = make_customer(store)
    sales.finalize("invoices", make_invoice(store, cust["id"], DAY)["id"])  # counter row, last_value 1
    n = sales.numbering
    assert n.observe_imported("invoices", "RE-2026-1043", DAY) is True
    assert n.preview("invoice", 1) == ["RE-2026-1044"]
    assert n.observe_imported("invoices", "RE-2026-0007", DAY) is False
    assert n.preview("invoice", 1) == ["RE-2026-1044"]


def test_imported_counter_survives_deleting_the_imported_documents(store):
    """The counter row is persisted even if the seed already covers the number (undo must not reset it)."""
    sales = make_sales(store, DAY)
    cust = make_customer(store)
    doc = make_invoice(store, cust["id"], DAY, number="RE-2026-1043", status="sent")
    assert sales.numbering.observe_imported("invoices", "RE-2026-1043", DAY) is False  # seed already covers it
    store.delete("invoices", doc["id"])
    assert sales.numbering.preview("invoice", 1) == ["RE-2026-1044"]


def test_imported_number_of_another_year_does_not_move_this_years_counter(store):
    sales = make_sales(store, DAY)
    assert sales.numbering.observe_imported("invoices", "RE-2025-0900", date(2026, 1, 2)) is False
    assert sales.numbering.imported_number_note("invoices", "RE-2025-0900", date(2026, 1, 2))
    assert sales.numbering.imported_number_note("invoices", "RE-2026-0900", date(2026, 1, 2)) is None
    assert sales.numbering.preview("invoice", 1) == ["RE-2026-0001"]


def test_numbers_in_use_are_skipped_after_update_next_one(store):
    sales = make_sales(store, DAY)
    cust = make_customer(store)
    for n in (1, 2):
        make_invoice(store, cust["id"], DAY, number=f"RE-2026-{n:04d}", status="sent")
    sales.numbering.update("invoice", next_number=1, confirm=True)
    assert sales.numbering.preview("invoice", 2) == ["RE-2026-0003", "RE-2026-0004"]
    assert sales.numbering.check("invoice", next_number=1)["preview"][0] == "RE-2026-0003"  # dry run == real
    inv = make_invoice(store, cust["id"], DAY)
    assert sales.finalize("invoices", inv["id"])["number"] == "RE-2026-0003"


def test_gap_boundary_and_next_bounds(store):
    sales = make_sales(store, DAY)
    cust = make_customer(store)
    make_invoice(store, cust["id"], DAY, number="RE-2026-1043", status="sent")
    n = sales.numbering

    def codes(**kw):
        return {w["code"] for w in n.check("invoice", **kw)["warnings"]}

    assert codes(next_number=1044) == set()
    assert codes(next_number=1045) == {"gap"}
    assert "backwards" in codes(next_number=1043)
    for bad in (0, -1, 1_000_000_001):
        with pytest.raises(ServiceError):
            n.check("invoice", next_number=bad)
    assert n.check("invoice", next_number=1)["next"] == 1
    assert n.check("invoice", next_number=1_000_000_000)["next"] == 1_000_000_000


def test_monthly_reset_without_month_token_is_warned():
    assert any("{month}" in w for w in sorting_warnings(parse_pattern("RE-{year}-{n:04d}"), "monthly"))
    assert not any("{month}" in w for w in sorting_warnings(parse_pattern("RE-{yy}{month}-{n:04d}"), "monthly"))


def test_parse_pattern_escapes_regex_characters_and_pads_dates():
    p = parse_pattern("A.B/{n}")
    assert p.match("A.B/5") == 5
    assert p.match("AxB/5") is None  # '.' is a literal dot, not "any character"
    q = parse_pattern("R#-{yy}{month}-{n:03d}")
    assert q.format(date(2026, 3, 9), 5) == "R#-2603-005"
    assert q.match("R#-2603-005") == 5 and q.match("R#-26-03-005") is None
    assert q.format(date(2005, 11, 9), 5) == "R#-0511-005"


def test_date_consistency_of_numbers():
    p = parse_pattern("RE-{year}-{month}-{n}")
    assert p.date_consistent("RE-2026-03-5", date(2026, 3, 1))
    assert not p.date_consistent("RE-2026-03-5", date(2026, 4, 1))
    assert not p.date_consistent("RE-2025-03-5", date(2026, 3, 1))


@pytest.mark.parametrize(
    "key", ["invoice_pattern", "offer_pattern", "invoice_reset", "offer_reset", "numbering_shared"]
)
def test_numbering_keys_are_rejected_on_settings_put_but_unchanged_values_pass(boss_client, key):
    current = boss_client.get("/api/settings").json()
    assert boss_client.put("/api/settings", json={key: current[key]}).status_code == 200
    other = {"invoice_pattern": "X-{n}", "offer_pattern": "X-{n}", "invoice_reset": "never", "offer_reset": "never",
             "numbering_shared": not current.get("numbering_shared", False)}[key]  # fmt: skip
    if other == current[key]:
        other = "monthly"
    r = boss_client.put("/api/settings", json={key: other})
    assert r.status_code == 400 and key in r.json()["detail"]
    assert boss_client.get("/api/settings").json()[key] == current[key]


def test_threaded_sales_finalize_is_unique_and_gapless(store):
    sales = make_sales(store, DAY)
    cust = make_customer(store)
    ids = [make_invoice(store, cust["id"], DAY)["id"] for _ in range(40)]
    out: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker(chunk):
        try:
            for rid in chunk:
                num = sales.finalize("invoices", rid)["number"]
                with lock:
                    out.append(num)
        except BaseException as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(ids[i::8],)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert sorted(out) == [f"RE-2026-{i:04d}" for i in range(1, 41)]


ENDPOINTS = [
    ("GET", "/api/numbering/preview"),
    ("GET", "/api/numbering/status"),
    ("POST", "/api/numbering/check"),
    ("PUT", "/api/numbering"),
    ("GET", "/api/numbering/log"),
    ("GET", "/api/import/batches"),
    ("POST", "/api/import/preview"),
    ("POST", "/api/import/run"),
    ("POST", "/api/import/batches/x/undo"),
]


@pytest.mark.parametrize("method,path", ENDPOINTS)
def test_numbering_and_import_endpoints_need_the_boss(client, member_client, method, path):
    body = {"type": "invoice"}
    assert client.request(method, path, json=body).status_code == 401
    assert member_client.request(method, path, json=body).status_code == 403
