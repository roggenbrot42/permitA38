"""JournalService (docs/specs/accounting.md 7, 8.1, 18) with a SYNTHETIC chart; the hash chain is recomputed here
independently of the service (hashlib + json.dumps written out below)."""

import hashlib
import json
import shutil
import threading
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from permita38.core import charts
from permita38.core.charts import Role, load_chart
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.journal import JournalService, JournalSource, PostLine, canonical_json

FIXTURE = Path(__file__).parent / "fixtures" / "chart_synthetic.json"
CHART = load_chart(FIXTURE)
BANK = str(CHART.account(Role("bank")))
REVENUE = str(CHART.account(Role("revenue_s19")))
VAT_OUT = str(CHART.account(Role("vat_out_s19")))
ZERO = "0" * 64


def fiscal_year(year: int, **extra):
    return {
        "year": year, "chart": "skr03", "income_basis": "eur", "vat_basis": "soll", "vat_frequency": "month",
        "dauerfrist": False, "small_business": False, "rules_version": "r1", "opened_at": 1, "opened_by": "u1x000",
        **extra,
    }  # fmt: skip


@pytest.fixture
def repo(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURE, charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)
    r = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    r.startup()
    r.save("fiscal_years", fiscal_year(2025))
    r.save("fiscal_years", fiscal_year(2026))
    yield r
    r.close()


@pytest.fixture
def svc(repo):
    return JournalService(repo, clock=lambda: 1_800_000_000)


def sale(net: int = 10000, vat: int = 1900, bank_role: bool = True) -> list[PostLine]:
    gross = net + vat
    return [
        PostLine(role="bank", amount_cents=gross) if bank_role else PostLine(account=BANK, amount_cents=gross),
        PostLine(role="revenue_s19", amount_cents=-net, income_date=date(2026, 1, 15)),
        PostLine(
            role="vat_out_s19", amount_cents=-vat, tax_key="S19", tax_kind="tax", tax_rate="19",
            tax_base_cents=net, vat_date=date(2026, 1, 15),
        ),
    ]  # fmt: skip


SRC = JournalSource(event="issue", created_by="u1x000", source_coll="invoices", source_id="inv1abc")


def post(svc, key="issue:1", day=date(2026, 1, 15), lines=None, text="Invoice 1"):
    return svc.post(key, day, text, SRC, sale() if lines is None else lines)


# ---------------------------------------------------------------- validation
def test_balanced_entry_is_stored_with_resolved_accounts(svc):
    e = post(svc)
    assert (e.fiscal_year, e.entry_no, e.booking_date, e.document_date) == (2026, 1, "2026-01-15", "2026-01-15")
    assert [(ln.line_no, ln.role, ln.account) for ln in e.lines] == [
        (1, "bank", BANK), (2, "revenue_s19", REVENUE), (3, "vat_out_s19", VAT_OUT),
    ]  # fmt: skip
    assert [ln.amount for ln in e.lines] == [Decimal("119"), Decimal("-100"), Decimal("-19")]
    assert e.prev_hash == ZERO and len(e.hash) == 64


def test_unbalanced_entry_is_422_and_leaves_no_trace(svc, repo):
    lines = sale()
    lines[0] = PostLine(role="bank", amount_cents=11899)
    with pytest.raises(ServiceError) as err:
        post(svc, lines=lines)
    assert err.value.status == 422
    assert repo.list("journal_entries") == [] and repo.list("number_sequences") == []


@pytest.mark.parametrize(
    "line",
    [
        PostLine(account="1234567", amount_cents=100),  # not in the chart
        PostLine(role="no_such_role", amount_cents=100),
        PostLine(role="bank", account=REVENUE, amount_cents=100),  # role and account disagree
        PostLine(amount_cents=100),  # neither
    ],
)
def test_unknown_account_or_role_is_422(svc, line):
    with pytest.raises(ServiceError) as err:
        post(svc, lines=[line, PostLine(role="bank", amount_cents=-100)])
    assert err.value.status == 422


def test_tax_key_needs_vat_date_and_known_vocabulary(svc):
    for bad in (
        PostLine(role="bank", amount_cents=100, tax_key="S19"),  # no vat_date
        PostLine(role="bank", amount_cents=100, tax_key="XX", vat_date=date(2026, 1, 1)),
        PostLine(role="bank", amount_cents=100, tax_kind="tax"),  # kind without key
    ):
        with pytest.raises(ServiceError) as err:
            post(svc, lines=[bad, PostLine(role="revenue_s19", amount_cents=-100)])
        assert err.value.status == 422


def test_missing_closed_year_is_409(svc, repo):
    with pytest.raises(ServiceError) as err:
        post(svc, day=date(2030, 1, 1))  # no fiscal year 2030
    assert err.value.status == 409
    year = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**year, "closed_at": 5, "closed_by": "u1x000"})
    with pytest.raises(ServiceError) as err:
        post(svc)
    assert err.value.status == 409 and "closed" in err.value.message
    assert repo.list("journal_entries") == []


# ---------------------------------------------------------------- idempotence
def test_repost_with_same_key_and_content_returns_the_existing_entry(svc, repo):
    first = post(svc)
    again = post(svc)
    assert again.id == first.id and again.hash == first.hash and again.entry_no == 1
    assert len(repo.list("journal_entries")) == 1 and len(repo.list("journal_lines")) == 3
    assert repo.find("number_sequences")[0]["last_value"] == 1
    # an equivalent line given by account instead of role is the same content
    assert post(svc, lines=sale(bank_role=False)).id == first.id


def test_same_key_with_different_lines_text_or_date_is_409(svc, repo):
    post(svc)
    for kwargs in (
        {"lines": sale(net=20000, vat=3800)},
        {"text": "Another text"},
        {"day": date(2026, 1, 16)},
    ):
        with pytest.raises(ServiceError) as err:
            post(svc, **kwargs)
        assert err.value.status == 409
    assert len(repo.list("journal_entries")) == 1


def test_idempotent_repost_still_works_after_the_year_is_closed(svc, repo):
    first = post(svc)
    year = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**year, "closed_at": 5, "closed_by": "u1x000"})
    assert post(svc).id == first.id


# ---------------------------------------------------------------- numbers
def test_entry_no_is_gapless_per_year_and_restarts(svc, repo):
    nos = [post(svc, key=f"k{i}", day=date(2025, 12, 1 + i)).entry_no for i in range(3)]
    nos += [post(svc, key=f"k{i}", day=date(2026, 1, 1 + i)).entry_no for i in range(3, 5)]
    assert nos == [1, 2, 3, 1, 2]
    with pytest.raises(ServiceError):  # a refused entry does not use up a number
        post(svc, key="bad", lines=[PostLine(role="bank", amount_cents=1)])
    assert post(svc, key="k5").entry_no == 3


def test_concurrent_posts_get_distinct_consecutive_numbers(svc, repo):
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            for j in range(5):
                post(svc, key=f"t{i}-{j}", day=date(2026, 3, 1))
        except BaseException as e:  # pragma: no cover - reported below
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    nos = sorted(r["entry_no"] for r in repo.list("journal_entries"))
    assert nos == list(range(1, 11))
    assert svc.verify_chain(2026) == []


# ---------------------------------------------------------------- hash chain (recomputed independently)
def expected_hash(prev: str, entry: dict, lines: list[dict]) -> str:
    entry_keys = (
        "fiscal_year", "entry_no", "document_date", "booking_date", "event", "event_key", "source_coll", "source_id",
        "source_number", "reverses_id", "counterparty_id", "cp_vat_id", "cp_country", "text", "origin", "created_at",
        "created_by", "prev_hash",
    )  # fmt: skip
    line_keys = (
        "line_no", "role", "account", "amount_cents", "tax_key", "tax_kind", "tax_rate", "tax_base_cents", "vat_date",
        "income_date", "open_item_id", "treatment",
    )  # fmt: skip
    body = {k: entry.get(k) for k in entry_keys}
    body["lines"] = [{k: ln.get(k) for k in line_keys} for ln in sorted(lines, key=lambda ln: ln["line_no"])]
    text = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((prev + text).encode("utf-8")).hexdigest()


def test_hash_chain_is_per_fiscal_year(svc, repo):
    post(svc, key="a", day=date(2025, 12, 30))
    post(svc, key="b", day=date(2025, 12, 31), lines=sale(net=5000, vat=950))
    post(svc, key="c", day=date(2026, 1, 2), lines=sale(net=777, vat=148))
    rows = sorted(repo.list_journal_entries(), key=lambda r: (r["fiscal_year"], r["entry_no"]))
    assert [(r["fiscal_year"], r["entry_no"]) for r in rows] == [(2025, 1), (2025, 2), (2026, 1)]
    prev = ZERO
    for r in rows:
        if r["entry_no"] == 1:
            prev = ZERO  # every year starts from 64 zeros
        assert r["prev_hash"] == prev
        want = expected_hash(prev, r, r["lines"])
        assert r["hash"] == want
        prev = want
    assert rows[0]["prev_hash"] == ZERO and rows[2]["prev_hash"] == ZERO and rows[1]["prev_hash"] == rows[0]["hash"]
    assert svc.verify_chain(2025) == [] and svc.verify_chain(2026) == []


def test_canonical_form_is_pinned(svc, repo):
    """One entry hashed against a literal canonical string: a change of the format breaks old journals."""
    post(svc, lines=sale())
    r = repo.list_journal_entries()[0]
    text = (
        '{"booking_date":"2026-01-15","counterparty_id":null,"cp_country":null,"cp_vat_id":null,'
        '"created_at":1800000000,"created_by":"u1x000","document_date":"2026-01-15","entry_no":1,'
        '"event":"issue","event_key":"issue:1","fiscal_year":2026,"lines":['
        f'{{"account":"{BANK}","amount_cents":11900,"income_date":null,"line_no":1,"open_item_id":null,'
        '"role":"bank","tax_base_cents":null,"tax_key":null,"tax_kind":null,"tax_rate":null,"treatment":null,'
        '"vat_date":null},'
        f'{{"account":"{REVENUE}","amount_cents":-10000,"income_date":"2026-01-15","line_no":2,"open_item_id":null,'
        '"role":"revenue_s19","tax_base_cents":null,"tax_key":null,"tax_kind":null,"tax_rate":null,'
        '"treatment":null,"vat_date":null},'
        f'{{"account":"{VAT_OUT}","amount_cents":-1900,"income_date":null,"line_no":3,"open_item_id":null,'
        '"role":"vat_out_s19","tax_base_cents":10000,"tax_key":"S19","tax_kind":"tax","tax_rate":"19",'
        '"treatment":null,"vat_date":"2026-01-15"}],'
        f'"origin":"auto","prev_hash":"{ZERO}","reverses_id":null,"source_coll":"invoices",'
        '"source_id":"inv1abc","source_number":null,"text":"Invoice 1"}'
    )
    assert r["hash"] == hashlib.sha256((ZERO + text).encode()).hexdigest()


def tamper(repo, sql: str, params=()):
    """Change the journal behind the app's back: drop the append-only trigger of the throwaway database first."""
    with repo.transaction():
        for t in ("guard_journal_lines_append_only_upd", "guard_journal_entries_append_only_upd"):
            repo._exec(f'DROP TRIGGER IF EXISTS "{t}"')
        repo._exec(sql, params)


def test_append_only_guard_refuses_update_and_delete(svc, repo):
    post(svc)
    for sql in (
        "UPDATE journal_lines SET amount_cents = 1",
        "DELETE FROM journal_lines",
        "UPDATE journal_entries SET text = 'x'",
        "DELETE FROM journal_entries",  # refused by the reference guard (lines point at it) or the append-only guard
    ):
        with pytest.raises(Exception, match="append_only|ref.in_use"):
            with repo.transaction():
                repo._exec(sql)


def test_verify_chain_detects_a_tampered_line(svc, repo):
    post(svc, key="a")
    post(svc, key="b", lines=sale(net=5000, vat=950))
    assert svc.verify_chain(2026) == []
    tamper(repo, "UPDATE journal_lines SET account = ? WHERE line_no = 2 AND amount_cents = -5000", (BANK,))
    findings = svc.verify_chain(2026)
    assert [(f.code, f.entry_no) for f in findings] == [("journal.hash", 2)]


def test_verify_chain_detects_a_changed_amount_unbalance_gap_and_broken_link(svc, repo):
    for k in "abc":
        post(svc, key=k)
    tamper(
        repo,
        "UPDATE journal_lines SET amount_cents = amount_cents + 1 WHERE line_no = 1 AND entry_id IN "
        "(SELECT id FROM journal_entries WHERE entry_no = 1)",
    )
    codes = [(f.code, f.entry_no) for f in svc.verify_chain(2026)]
    assert ("journal.unbalanced", 1) in codes and ("journal.hash", 1) in codes
    tamper(repo, "UPDATE journal_entries SET entry_no = 5 WHERE entry_no = 3")
    codes = [(f.code, f.entry_no) for f in svc.verify_chain(2026)]
    assert ("journal.gap", 5) in codes
    tamper(repo, "UPDATE journal_entries SET prev_hash = ? WHERE entry_no = 2", (ZERO,))
    assert ("journal.prev_hash", 2) in [(f.code, f.entry_no) for f in svc.verify_chain(2026)]


def test_verify_chain_detects_a_changed_year_start_link(svc, repo):
    post(svc, key="a", day=date(2026, 1, 2))
    tamper(repo, "UPDATE journal_entries SET prev_hash = ? WHERE fiscal_year = 2026", ("f" * 64,))
    assert ("journal.prev_hash", 1) in {(f.code, f.entry_no) for f in svc.verify_chain(2026)}


def test_late_entry_in_an_earlier_open_year_does_not_disturb_the_later_year(svc, repo):
    post(svc, key="a", day=date(2025, 12, 30))
    post(svc, key="b", day=date(2026, 1, 2))
    late = post(svc, key="late", day=date(2025, 12, 31), lines=sale(net=5000, vat=950))
    assert (late.fiscal_year, late.entry_no) == (2025, 2)
    firsts = [r for r in repo.list_journal_entries() if r["entry_no"] == 1]
    assert [(r["fiscal_year"], r["prev_hash"]) for r in firsts] == [(2025, ZERO), (2026, ZERO)]
    assert svc.verify_chain(2025) == [] and svc.verify_chain(2026) == []


def test_failed_insert_does_not_consume_a_number(svc, repo, monkeypatch):
    assert post(svc, key="a").entry_no == 1
    real = repo.insert_journal_entry

    def failing(entry, lines):
        assert entry["entry_no"] == 2  # the number was taken before the insert fails
        raise RuntimeError("insert failed")

    monkeypatch.setattr(repo, "insert_journal_entry", failing)
    with pytest.raises(RuntimeError):
        post(svc, key="b")
    monkeypatch.setattr(repo, "insert_journal_entry", real)
    assert post(svc, key="b").entry_no == 2
    assert [r["entry_no"] for r in repo.list_journal_entries(2026)] == [1, 2]
    assert svc.verify_chain(2026) == []


def test_canonical_json_writes_decimals_as_strings():
    entry = {"fiscal_year": 2026, "entry_no": 1}
    line = {"line_no": 1, "account": "1", "amount_cents": 1, "tax_rate": Decimal("7.00")}
    out = canonical_json(entry, [line])
    assert '"tax_rate":"7.00"' in out
    assert out == canonical_json(entry, [{**line, "tax_rate": "7.00"}])


# ---------------------------------------------------------------- booking date
def test_booking_date_without_locks_is_the_document_date(svc):
    assert post(svc, day=date(2026, 2, 3)).booking_date == "2026-02-03"


def test_booking_date_moves_into_the_first_unlocked_month(svc, repo):
    for m in (1, 2):
        repo.save("period_locks", {"year": 2026, "month": m, "locked_at": 1, "locked_by": "u1x000", "reason": "manual"})
    late = post(svc, key="late", day=date(2026, 1, 20))
    assert late.document_date == "2026-01-20" and late.booking_date == "2026-03-01"
    assert post(svc, key="later", day=date(2026, 5, 4)).booking_date == "2026-05-04"  # never moved backwards


def lock(repo, year: int, month: int):
    repo.save(
        "period_locks", {"year": year, "month": month, "locked_at": 1, "locked_by": "u1x000", "reason": "manual"}
    )


def test_a_december_lock_books_a_late_document_on_31_december_while_the_year_is_open(svc, repo):
    repo.save("fiscal_years", fiscal_year(2027))
    lock(repo, 2026, 12)
    e = post(svc, day=date(2026, 12, 20))
    assert (e.fiscal_year, e.booking_date, e.entry_no) == (2026, "2026-12-31", 1)


def test_late_bill_posted_in_february_with_december_locked(svc, repo):
    repo.save("fiscal_years", fiscal_year(2027))
    lock(repo, 2026, 12)  # Feb 2027 posting: document of 2026, the year still open
    e = post(svc, key="bill", day=date(2026, 11, 3))
    assert (e.fiscal_year, e.booking_date, e.document_date) == (2026, "2026-12-31", "2026-11-03")
    assert svc.verify_chain(2026) == []


def test_late_bill_after_the_year_is_closed_goes_to_the_first_unlocked_month_of_the_next_year(svc, repo):
    repo.save("fiscal_years", fiscal_year(2027))
    lock(repo, 2026, 12)
    year = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**year, "closed_at": 5, "closed_by": "u1x000"})
    e = post(svc, key="bill", day=date(2026, 11, 3))
    assert (e.fiscal_year, e.booking_date) == (2027, "2027-01-01")
    lock(repo, 2027, 1)
    assert post(svc, key="bill2", day=date(2026, 11, 4)).booking_date == "2027-02-01"


def test_all_months_locked_up_to_december_books_on_31_december_even_for_a_january_document(svc, repo):
    for m in range(1, 13):
        lock(repo, 2026, m)
    assert post(svc, day=date(2026, 1, 5)).booking_date == "2026-12-31"
