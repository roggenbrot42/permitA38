"""Document numbering: pattern parser, sequences, resets, atomic allocation, change safety and the API."""

import threading
from datetime import date

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.core.errors import ServiceError
from permita38.core.numbering import PatternError, parse_pattern, period_key, sorting_warnings
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.numbering import NumberingService
from permita38.core.service.records import RecordService
from permita38.core.service.sales import SalesService


class FakeClock:
    def __init__(self, day: date):
        self.day = day

    def __call__(self) -> date:
        return self.day


@pytest.fixture
def store(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    yield repo
    repo.close()


def make_sales(store, day):
    clock = FakeClock(day)
    numbering = NumberingService(store, clock=clock)
    return SalesService(store, RecordService(store), numbering=numbering), clock


def make_customer(store):
    return store.save("customers", dict(name="Demo Client GmbH"))


def make_invoice(store, customer_id, day, **extra):
    doc = dict(customer_id=customer_id, date=day.isoformat(), lines=[dict(description="Work", qty=1, price=100)])
    doc.update(extra)
    return store.save("invoices", doc)


# ---------------------------------------------------------------- pattern parser / formatter
def test_format_and_match_examples():
    p = parse_pattern("RE-{year}-{n:04d}")
    assert p.format(date(2026, 3, 1), 43) == "RE-2026-0043"
    assert p.match("RE-2026-0043") == 43
    assert p.match("RE-2026-43x") is None
    assert parse_pattern("{yy}{month}-{n:03d}").format(date(2026, 3, 1), 7) == "2603-007"
    assert parse_pattern("{n}").format(date(2026, 1, 1), 1234567) == "1234567"


@pytest.mark.parametrize(
    "text",
    [
        "",
        "RE-{n",
        "RE-}{n}",
        "{foo}{n}",
        "a{n}{n}",
        "RE-{year}",
        "{n:5d}",
        "{n:0d}",
        "{n:10d}",
        "x{n}#<",
        "a" * 101 + "{n}",
    ],
)
def test_invalid_patterns_are_rejected(text):
    with pytest.raises(PatternError):
        parse_pattern(text)


def test_literal_text_limit_and_umlauts():
    assert parse_pattern("Rechnung ÄÖÜ/2026 {n}").format(date(2026, 1, 1), 5) == "Rechnung ÄÖÜ/2026 5"
    with pytest.raises(PatternError):
        parse_pattern("a" * 41 + "{n}")


def test_sorting_warnings():
    assert sorting_warnings(parse_pattern("RE-{year}-{n:04d}"), "yearly") == []
    assert any("zero-padded" in w for w in sorting_warnings(parse_pattern("RE-{n}"), "never"))
    assert any("after the number" in w for w in sorting_warnings(parse_pattern("{n:04d}-{year}"), "never"))
    assert any("repeat" in w for w in sorting_warnings(parse_pattern("RE-{n:04d}"), "yearly"))


def test_period_keys():
    d = date(2026, 3, 31)
    assert period_key("never", d) == "all"
    assert period_key("yearly", d) == "2026"
    assert period_key("monthly", d) == "2026-03"
    with pytest.raises(ValueError):
        period_key("weekly", d)


@settings(max_examples=300, deadline=None)
@given(
    n=st.integers(min_value=1, max_value=999_999_999),
    width=st.integers(min_value=0, max_value=9),
    day=st.dates(min_value=date(2000, 1, 1), max_value=date(2100, 12, 31)),
    literal=st.text(alphabet="ABCabc-_/.# 0123456789", max_size=12),
)
def test_format_then_match_round_trip(n, width, day, literal):
    token = "{n:0%dd}" % width if width else "{n}"
    p = parse_pattern(f"{literal}{{year}}{token}")
    text = p.format(day, n)
    assert text.startswith(literal) and text.endswith(f"{day.year:04d}" + f"{n:0{width}d}" if width else str(n))
    assert p.match(text) == n


# ---------------------------------------------------------------- sequences, resets, seeding
def test_yearly_reset_with_fake_clock(store):
    sales, clock = make_sales(store, date(2026, 12, 30))
    cust = make_customer(store)
    numbers = []
    for day in (date(2026, 12, 30), date(2026, 12, 31), date(2027, 1, 2), date(2027, 1, 3)):
        inv = make_invoice(store, cust["id"], day)
        numbers.append(sales.finalize("invoices", inv["id"])["number"])
    assert numbers == ["RE-2026-0001", "RE-2026-0002", "RE-2027-0001", "RE-2027-0002"]


def test_monthly_and_never_reset(store):
    sales, _ = make_sales(store, date(2026, 3, 1))
    store.save_settings({**store.settings(), "invoice_reset": "monthly", "invoice_pattern": "{yy}{month}-{n:03d}"})
    cust = make_customer(store)
    a = sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 3, 2))["id"])["number"]
    b = sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 4, 2))["id"])["number"]
    assert (a, b) == ("2603-001", "2604-001")
    store.save_settings({**store.settings(), "invoice_reset": "never", "invoice_pattern": "R{n:03d}"})
    c = sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 4, 3))["id"])["number"]
    assert c == "R003"


def test_seed_from_existing_documents(store):
    sales, _ = make_sales(store, date(2026, 6, 1))
    cust = make_customer(store)
    for n in (17, 42):  # written by an older tool / imported, the highest is 42
        make_invoice(store, cust["id"], date(2026, 2, 1), number=f"RE-2026-{n:04d}", status="sent")
    make_invoice(store, cust["id"], date(2025, 12, 1), number="RE-2025-0900", status="sent")  # other year
    inv = make_invoice(store, cust["id"], date(2026, 6, 1))
    assert sales.finalize("invoices", inv["id"])["number"] == "RE-2026-0043"


def test_seed_falls_back_to_count_when_nothing_matches(store):
    sales, _ = make_sales(store, date(2026, 6, 1))
    cust = make_customer(store)
    make_invoice(store, cust["id"], date(2026, 2, 1), number="OLD/7", status="sent")
    make_invoice(store, cust["id"], date(2026, 3, 1), number="OLD/8", status="sent")
    assert sales.numbering.preview("invoice", 1) == ["RE-2026-0003"]


def test_shared_sequence_between_invoices_and_offers(store):
    sales, _ = make_sales(store, date(2026, 5, 5))
    store.save_settings({**store.settings(), "numbering_shared": True, "offer_pattern": "RE-{year}-{n:04d}"})
    cust = make_customer(store)
    offer = store.save("offers", dict(customer_id=cust["id"], date="2026-05-05", lines=[dict(qty=1, price=5)]))
    a = sales.finalize("offers", offer["id"])["number"]
    b = sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 5, 5))["id"])["number"]
    assert (a, b) == ("RE-2026-0001", "RE-2026-0002")


def test_numbers_already_in_use_are_skipped(store):
    sales, _ = make_sales(store, date(2026, 5, 5))
    cust = make_customer(store)
    make_invoice(store, cust["id"], date(2026, 5, 5), number="RE-2026-0001", status="sent")  # manual, no counter yet
    inv = make_invoice(store, cust["id"], date(2026, 5, 5))
    assert sales.finalize("invoices", inv["id"])["number"] == "RE-2026-0002"


def test_failed_finalize_gives_the_number_back(store):
    sales, _ = make_sales(store, date(2026, 5, 5))
    cust = make_customer(store)
    empty = store.save("invoices", dict(customer_id=cust["id"], date="2026-05-05", lines=[]))
    with pytest.raises(ServiceError):
        sales.finalize("invoices", empty["id"])
    ok = make_invoice(store, cust["id"], date(2026, 5, 5))
    assert sales.finalize("invoices", ok["id"])["number"] == "RE-2026-0001"


def test_cancelled_document_keeps_its_number(store):
    sales, _ = make_sales(store, date(2026, 5, 5))
    cust = make_customer(store)
    inv = sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 5, 5))["id"])
    inv["status"] = "cancelled"
    store.save("invoices", inv)
    nxt = sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 5, 6))["id"])
    assert (inv["number"], nxt["number"]) == ("RE-2026-0001", "RE-2026-0002")


def test_concurrent_finalize_is_unique_and_gapless(store):
    numbering = NumberingService(store, clock=FakeClock(date(2026, 5, 5)))
    errors: list[BaseException] = []
    got: list[str] = []
    lock = threading.Lock()

    def worker():
        try:
            for _ in range(50):
                with store.transaction():
                    number = numbering.allocate("invoices", date(2026, 5, 5))
                with lock:
                    got.append(number)
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(got) == 800 and len(set(got)) == 800
    assert sorted(got) == sorted(f"RE-2026-{i:04d}" for i in range(1, 801))


# ---------------------------------------------------------------- changes: preview, safety, log
def test_preview_does_not_move_the_counter(store):
    numbering = NumberingService(store, clock=FakeClock(date(2026, 5, 5)))
    assert numbering.preview("invoice") == [f"RE-2026-{i:04d}" for i in range(1, 6)]
    assert numbering.preview("invoice") == numbering.preview("invoice")


def test_start_number_change_with_warnings_needs_confirmation(store):
    sales, _ = make_sales(store, date(2026, 5, 5))
    cust = make_customer(store)
    numbering = sales.numbering
    # the adopter's old tool stopped at 1042
    result = numbering.update("invoice", next_number=1043)
    assert result["applied"] is True and result["next"] == 1043
    assert numbering.preview("invoice", 1) == ["RE-2026-1043"]
    sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 5, 5))["id"])
    # going backwards is risky
    risky = numbering.update("invoice", next_number=1000)
    assert risky["applied"] is False
    assert [w["code"] for w in risky["warnings"]] == ["backwards"]
    assert numbering.preview("invoice", 1) == ["RE-2026-1044"]
    # a gap is risky too
    assert numbering.check("invoice", next_number=2000)["warnings"][0]["code"] == "gap"
    forced = numbering.update("invoice", next_number=1000, confirm=True, reason="cleanup")
    assert forced["applied"] is True
    log = numbering.log("invoice")
    assert sorted(row["action"] for row in log) == ["change", "update"]
    assert [row["reason"] for row in log if row["action"] == "change"] == ["cleanup"]


def test_duplicate_warning_when_number_is_taken(store):
    cust = make_customer(store)
    make_invoice(store, cust["id"], date(2026, 5, 5), number="RE-2026-0010", status="sent")
    numbering = NumberingService(store, clock=FakeClock(date(2026, 5, 5)))
    codes = [w["code"] for w in numbering.check("invoice", next_number=10)["warnings"]]
    assert "backwards" in codes and "duplicate" in codes


def test_pattern_change_is_previewed_and_warned(store):
    sales, _ = make_sales(store, date(2026, 5, 5))
    cust = make_customer(store)
    sales.finalize("invoices", make_invoice(store, cust["id"], date(2026, 5, 5))["id"])
    check = sales.numbering.check("invoice", pattern="RE/{year}/{n:05d}")
    assert check["preview"][0] == "RE/2026/00002"
    assert "pattern_change" in [w["code"] for w in check["warnings"]]
    with pytest.raises(ServiceError):
        sales.numbering.check("invoice", pattern="RE-{n}-{foo}")


def test_legacy_settings_keep_working(store):
    store.save_settings({"invoice_pattern": "RE-{year}-{n:04d}", "offer_pattern": "AN-{year}-{n:04d}"})
    numbering = NumberingService(store, clock=FakeClock(date(2026, 5, 5)))
    assert numbering.config("invoice").reset == "yearly"
    assert numbering.preview("offer", 1) == ["AN-2026-0001"]


def test_numbering_api(boss_client):
    from datetime import date as _date

    year = _date.today().year
    r = boss_client.get("/api/numbering/preview", params={"type": "invoice"})
    assert r.status_code == 200 and r.json()["next"][0] == f"RE-{year}-0001"
    assert len(r.json()["next"]) == 5
    r = boss_client.put("/api/numbering", json={"type": "invoice", "next": 1043, "reason": "old tool"})
    assert r.status_code == 200 and r.json()["applied"] is True
    assert boss_client.get("/api/numbering/preview").json()["next"][0] == f"RE-{year}-1043"
    # a numbered invoice of this year (e.g. from an older tool) makes 1000 a step backwards
    boss_client.app.state.services.store.save(
        "invoices", dict(customer_id="cust0001", date=f"{year}-02-01", number=f"RE-{year}-1043", status="sent")
    )
    r = boss_client.put("/api/numbering", json={"type": "invoice", "next": 1000})
    assert r.status_code == 409
    assert r.json()["detail"]["applied"] is False
    assert r.json()["detail"]["warnings"][0]["code"] == "backwards"
    r = boss_client.put("/api/numbering", json={"type": "invoice", "next": 1000, "confirm": True})
    assert r.status_code == 200
    r = boss_client.post("/api/numbering/check", json={"type": "invoice", "pattern": "{n}"})
    assert r.status_code == 200
    assert any(w["code"] == "sorting" for w in r.json()["warnings"])
    assert boss_client.post("/api/numbering/check", json={"type": "invoice", "pattern": "RE-{x}"}).status_code == 400
    r = boss_client.put("/api/numbering", json={"type": "invoice", "next": 0})
    assert r.status_code == 422
    assert len(boss_client.get("/api/numbering/log", params={"type": "invoice"}).json()) == 2
