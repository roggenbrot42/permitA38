"""JournalService (docs/specs/accounting.md 7, 8.1, 18) with a SYNTHETIC chart; the hash chain is recomputed here
independently of the service (hashlib + json.dumps written out below)."""

import hashlib
import json
import shutil
import threading
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from permita38.core import charts
from permita38.core.charts import Role, load_chart
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.repository.base import JournalConflict
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
    assert err.value.status == 409 and "Fiscal year 2027 is not open" in err.value.message  # closed = locked
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
    repo.save("period_locks", {"year": year, "month": month, "locked_at": 1, "locked_by": "u1x000", "reason": "manual"})


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


def test_closed_year_without_lock_rows_books_into_the_first_unlocked_month_of_the_next_year(svc, repo):
    repo.save("fiscal_years", fiscal_year(2027))
    year = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**year, "closed_at": 5, "closed_by": "u1x000"})
    assert repo.list("period_locks") == []
    e = post(svc, key="late", day=date(2026, 11, 3))
    assert (e.fiscal_year, e.booking_date, e.document_date) == (2027, "2027-01-01", "2026-11-03")
    lock(repo, 2027, 1)
    assert post(svc, key="late2", day=date(2026, 11, 4)).booking_date == "2027-02-01"


# ---------------------------------------------------------------- review gaps: validation messages
def err_of(svc, **kwargs) -> ServiceError:
    with pytest.raises(ServiceError) as err:
        post(svc, **kwargs)
    return err.value


def test_zero_lines_single_zero_line_bool_oversize_and_bad_tax_rate_are_422(svc, repo):
    e = err_of(svc, lines=[])
    assert (e.status, e.message) == (422, "An entry needs at least one line")
    e = err_of(svc, lines=[PostLine(role="bank", amount_cents=0)])
    assert (e.status, e.message) == (422, "Line 1: amount 0 is only allowed on a base_only statistical line")
    e = err_of(svc, lines=[PostLine(role="bank", amount_cents=True), PostLine(role="revenue_s19", amount_cents=-1)])
    assert (e.status, e.message) == (422, "Line 1: amount_cents must be an integer")
    credit = PostLine(role="revenue_s19", amount_cents=-100)
    e = err_of(svc, lines=[PostLine(role="bank", amount_cents=100, tax_base_cents=True), credit])
    assert (e.status, e.message) == (422, "Line 1: tax_base_cents must be an integer")
    big = 10**13 + 1
    e = err_of(svc, lines=[PostLine(role="bank", amount_cents=big), PostLine(role="revenue_s19", amount_cents=-big)])
    assert (e.status, e.message) == (422, "Line 1: amounts are limited to +-10000000000000 cents")
    e = err_of(svc, lines=[PostLine(role="bank", amount_cents=100, tax_base_cents=big), credit])
    assert (e.status, e.message) == (422, "Line 1: amounts are limited to +-10000000000000 cents")
    for bad in ("19%", "abc", "100", "1.234", "", ".5", "-1", "1e1"):
        e = err_of(svc, lines=[PostLine(role="bank", amount_cents=100, tax_rate=bad), credit])
        assert e.status == 422 and e.message.startswith("Line 1: tax_rate must be a decimal string"), bad
    assert repo.list("journal_entries") == []


def test_amounts_at_the_bound_good_tax_rates_and_a_zero_statistical_line_are_accepted(svc):
    top = 10**13
    lines = [PostLine(role="bank", amount_cents=top), PostLine(role="revenue_s19", amount_cents=-top)]
    assert post(svc, key="top", lines=lines).entry_no == 1
    for i, rate in enumerate(("0", "7", "19", "7.5", "19.00")):
        lines = [
            PostLine(role="bank", amount_cents=100, tax_rate=rate),
            PostLine(role="revenue_s19", amount_cents=-100),
        ]
        assert post(svc, key=f"r{i}", lines=lines).entry_no == i + 2
    stat = PostLine(
        role="revenue_s19", amount_cents=0, tax_key="KU", tax_kind="base_only", tax_base_cents=500,
        vat_date=date(2026, 1, 15),
    )  # fmt: skip
    assert post(svc, key="stat", lines=[stat]).entry_no == 7


def test_unknown_role_and_unknown_account_have_their_own_messages(svc):
    bank_credit = PostLine(role="bank", amount_cents=-100)
    e = err_of(svc, lines=[PostLine(role="no_such_role", amount_cents=100), bank_credit])
    assert (e.status, e.message) == (422, "Line 1: unknown role 'no_such_role'")
    e = err_of(svc, lines=[PostLine(account="1234567", amount_cents=100), bank_credit])
    assert (e.status, e.message) == (422, "Line 1: unknown account '1234567' in chart skr03")
    e = err_of(svc, lines=[PostLine(role="bank", account=REVENUE, amount_cents=100), bank_credit])
    assert (e.status, e.message) == (422, f"Line 1: role 'bank' is account {BANK}, not {REVENUE}")


def test_text_event_key_and_origin_are_checked(svc, repo):
    assert err_of(svc, text="x" * 201).status == 422
    assert post(svc, key="max", text="x" * 200).entry_no == 1
    e = err_of(svc, text="  ")
    assert (e.status, e.message) == (422, "text is required and at most 200 characters")
    e = err_of(svc, key="  ")
    assert (e.status, e.message) == (422, "event_key is required")
    e = err_of(svc, key="")
    assert (e.status, e.message) == (422, "event_key is required")
    with pytest.raises(ServiceError) as err:
        svc.post("k", date(2026, 1, 15), "t", replace(SRC, origin="bogus"), sale())  # type: ignore[arg-type]
    assert (err.value.status, err.value.message) == (422, "Unknown origin 'bogus'")
    assert len(repo.list("journal_entries")) == 1


def test_chart_missing_is_409(svc, repo):
    year = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**year, "chart": "skr04"})  # the temp chart directory only holds skr03
    e = err_of(svc)
    assert e.status == 409 and e.message.startswith("No chart of accounts for fiscal year 2026")
    assert repo.list("journal_entries") == []


# ---------------------------------------------------------------- review gaps: one changed field = 409
def changed_field_cases() -> dict:
    def src(**kw):
        return {"source": replace(SRC, **kw)}

    def line(i, **kw):
        lines = sale()
        lines[i] = replace(lines[i], **kw)
        return {"lines": lines}

    swapped = sale()
    swapped[1], swapped[2] = swapped[2], swapped[1]
    return {
        "amount": {"lines": sale(net=10100, vat=1900)},
        "account": line(0, role=None, account=REVENUE),
        "tax_rate": line(2, tax_rate="7"),
        "tax_key": line(2, tax_key="S7"),
        "vat_date": line(2, vat_date=date(2026, 1, 16)),
        "line_order": {"lines": swapped},
        "event": src(event="payment"),
        "source_id": src(source_id="inv2abc"),
        "counterparty_id": src(counterparty_id="cust1x"),
    }


@pytest.mark.parametrize("name", list(changed_field_cases()))
def test_idempotent_repost_with_one_changed_field_is_409(svc, repo, name):
    first = post(svc)
    kwargs = changed_field_cases()[name]
    source = kwargs.get("source", SRC)
    lines = kwargs.get("lines", sale())
    with pytest.raises(ServiceError) as err:
        svc.post("issue:1", date(2026, 1, 15), "Invoice 1", source, lines)
    assert err.value.status == 409 and "different content" in err.value.message
    assert [r["id"] for r in repo.list("journal_entries")] == [first.id]


# ---------------------------------------------------------------- review gaps: verify_chain
def test_verify_chain_of_an_empty_year_is_clean(svc):
    assert svc.verify_chain(2026) == [] and svc.verify_chain(1999) == []


def test_verify_chain_pages_and_finds_a_tamper_on_a_later_page(svc, repo, monkeypatch):
    from permita38.core.service import journal

    monkeypatch.setattr(journal, "PAGE", 2)
    for i in range(5):
        post(svc, key=f"k{i}")
    assert svc.verify_chain(2026) == []
    tamper(repo, "UPDATE journal_entries SET text = 'changed' WHERE entry_no = 5")
    assert [(f.code, f.entry_no) for f in svc.verify_chain(2026)] == [("journal.hash", 5)]


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE journal_entries SET text = 'changed' WHERE entry_no = 1",
        "UPDATE journal_entries SET booking_date = '2026-01-16' WHERE entry_no = 1",
        "UPDATE journal_entries SET created_by = 'u2x000' WHERE entry_no = 1",
        "UPDATE journal_lines SET tax_key = 'S7' WHERE tax_key = 'S19'",
    ],
)
def test_every_hashed_field_breaks_the_hash_when_changed(svc, repo, sql):
    post(svc)
    assert svc.verify_chain(2026) == []
    tamper(repo, sql)
    assert [(f.code, f.entry_no) for f in svc.verify_chain(2026)] == [("journal.hash", 1)]


def drop_journal_triggers(repo):
    with repo.transaction():
        names = repo._rows("SELECT name FROM sqlite_master WHERE type = 'trigger' AND tbl_name LIKE 'journal_%'")
        for (name,) in names:
            repo._exec(f'DROP TRIGGER "{name}"')


def test_deleting_the_last_entry_of_an_open_year_reports_the_counter(svc, repo):
    for k in "abc":
        post(svc, key=k)
    assert svc.verify_chain(2026) == []
    drop_journal_triggers(repo)
    with repo.transaction():
        repo._exec("DELETE FROM journal_lines WHERE entry_id IN (SELECT id FROM journal_entries WHERE entry_no = 3)")
        repo._exec("DELETE FROM journal_entries WHERE entry_no = 3")
    findings = svc.verify_chain(2026)
    assert [(f.code, f.entry_no, f.severity) for f in findings] == [("journal.counter", 2, "error")]
    assert findings[0].message == "the journal counter of 2026 is 3 but the last entry is number 2"


def test_counter_of_a_closed_year_is_not_compared(svc, repo):
    post(svc)
    year = repo.find("fiscal_years", year=2026)[0]
    repo.save("fiscal_years", {**year, "closed_at": 5, "closed_by": "u1x000"})
    seq = repo.find("number_sequences")[0]
    repo.save("number_sequences", {**seq, "last_value": 9})
    assert svc.verify_chain(2026) == []  # a closed year is checked against closing_entry_no (E18)


# ---------------------------------------------------------------- review gaps: concurrency and unique violations
def test_two_repository_instances_on_one_sqlite_file_number_consecutively(repo, tmp_path):
    other = create_repository(Config(data_dir=tmp_path / "data", secret_key="x" * 16))
    other.startup()
    try:
        errors: list[BaseException] = []
        services = [JournalService(repo, clock=lambda: 1), JournalService(other, clock=lambda: 1)]

        def work(i: int) -> None:
            try:
                for j in range(6):
                    post(services[i], key=f"c{i}-{j}", day=date(2026, 3, 1))
            except BaseException as e:  # pragma: no cover - reported below
                errors.append(e)

        threads = [threading.Thread(target=work, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        assert sorted(r["entry_no"] for r in repo.list("journal_entries")) == list(range(1, 13))
        assert services[0].verify_chain(2026) == []
    finally:
        other.close()


def test_raw_unique_violations_are_translated(repo):
    def entry(no: int, key: str) -> dict:
        return {
            "fiscal_year": 2026, "entry_no": no, "document_date": "2026-01-05", "booking_date": "2026-01-05",
            "event": "issue", "event_key": key, "text": "t", "origin": "auto", "created_at": 1,
            "created_by": "u1x000", "prev_hash": ZERO, "hash": "1" * 64,
        }  # fmt: skip

    lines = [{"line_no": 1, "account": BANK, "amount_cents": 1}, {"line_no": 2, "account": REVENUE, "amount_cents": -1}]
    repo.insert_journal_entry(entry(1, "dup"), lines)
    with pytest.raises(JournalConflict) as err:
        repo.insert_journal_entry(entry(2, "dup"), lines)
    assert err.value.field == "event_key"
    with pytest.raises(JournalConflict) as err:
        repo.insert_journal_entry(entry(1, "other"), lines)
    assert err.value.field == "entry_no"


def test_lost_race_on_event_key_returns_the_stored_entry_or_409(svc, repo, monkeypatch):
    first = post(svc)
    real = repo.get_journal_entry_by_event_key
    calls: list[str] = []

    def blind_once(key):  # the first look (before booking) misses the entry the other writer just committed
        calls.append(key)
        return None if len(calls) == 1 else real(key)

    monkeypatch.setattr(repo, "get_journal_entry_by_event_key", blind_once)
    assert post(svc).id == first.id  # same content: the existing entry
    calls.clear()
    with pytest.raises(ServiceError) as err:
        post(svc, text="Another text")  # different content
    assert err.value.status == 409 and "different content" in err.value.message
    assert len(repo.list("journal_entries")) == 1


def test_lost_race_on_entry_no_is_a_409_retry_not_a_500(svc, repo, monkeypatch):
    post(svc, key="a")
    with monkeypatch.context() as m:
        m.setattr(repo, "next_number", lambda doc_type, period: 1)  # another writer took number 1
        with pytest.raises(ServiceError) as err:
            post(svc, key="b")
    assert (err.value.status, err.value.message) == (409, "concurrent write, retry")
    assert post(svc, key="b").entry_no == 2  # the retry works


# ---------------------------------------------------------------- E7: drift, manual VAT dates, reversal, A15/A16
ORACLE = json.loads((Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text())
CASES = {c["id"]: c for c in ORACLE["journal_cases"]}


def _fixture_lines(entry: dict, vat_kind: str, day: date, first_open: date) -> tuple[list[PostLine], list[dict]]:
    """The PostLines of one fixture entry; every taxed line's vat_date comes from vat_date_for, the fixture's
    value is returned separately for the comparison."""
    from permita38.core.service.locks import vat_date_for

    lines, expected = [], []
    for role, cents, opt in ((ln + [{}])[:3] for ln in entry["lines"]):
        vat_date = drift = None
        if opt.get("tax_key"):
            vat_date, drift = vat_date_for(vat_kind, day, first_open)
        lines.append(
            PostLine(
                role=role, amount_cents=cents, tax_key=opt.get("tax_key"), tax_kind=opt.get("tax_kind"),
                tax_rate="19" if opt.get("tax_key") in ("S19", "VST") else None,
                tax_base_cents=opt.get("tax_base"), vat_date=vat_date,
                income_date=date.fromisoformat(opt["income_date"]) if opt.get("income_date") else None,
                drift=bool(drift),
            )
        )  # fmt: skip
        expected.append(
            {"role": role, "amount": cents, "vat_date": opt.get("vat_date"), "income": opt.get("income_date")}
        )
    return lines, expected


def _stored(e) -> list[dict]:
    return [
        {"role": ln.role, "amount": int(ln.amount * 100), "vat_date": ln.vat_date, "income": ln.income_date}
        for ln in e.lines
    ]


def test_oracle_a15_late_bill_after_lock(svc, repo):
    case = CASES["A15-late-bill-after-lock"]
    for ym in case["locked_months"]:
        lock(repo, int(ym[:4]), int(ym[5:]))
    exp = case["entries"][0]
    day = date.fromisoformat(case["events"][0]["date"])
    lines, expected = _fixture_lines(exp, "input_vat", day, date(2026, 2, 1))
    e = svc.post("expense:x15:bill:r0", day, "Bill", JournalSource(event="expense_bill", created_by="u1x000"), lines)
    assert (e.document_date, e.booking_date) == (exp["document_date"], exp["booking_date"])
    assert _stored(e) == expected
    assert e.warnings == []  # input VAT never drifts


def test_oracle_a16_late_ist_payment_after_lock(svc, repo):
    case = CASES["A16-late-ist-payment-after-lock"]
    first, second = case["entries"]
    issue_lines = [
        PostLine(role=r, amount_cents=c, income_date=date.fromisoformat(o["income_date"]) if o.get("income_date") else None)
        for r, c, o in ((ln + [{}])[:3] for ln in first["lines"])
    ]  # fmt: skip
    src = JournalSource(event="issue", created_by="u1x000")
    a = svc.post("issue:inv16", date(2026, 1, 5), "Invoice", src, issue_lines)
    assert a.booking_date == first["booking_date"]
    lock(repo, 2026, 1)  # the fixture's "lock" event
    day = date.fromisoformat(case["events"][2]["date"])
    lines, expected = _fixture_lines(second, "cash_event", day, date(2026, 2, 1))
    b = svc.post("payment:p16", day, "Payment", JournalSource(event="payment", created_by="u1x000"), lines)
    assert (b.document_date, b.booking_date) == (second["document_date"], second["booking_date"])
    assert _stored(b) == expected  # vat_date stays 2026-01-30 (drift case 2)
    assert len(b.warnings) == 1 and "Line 4" in b.warnings[0] and "2026-01-30" in b.warnings[0]
    assert case["drift"] == {"2026-01": 19000}
    assert svc.get_by_event_key("payment:p16").warnings == b.warnings  # kept in the row, not hashed
    assert svc.verify_chain(2026) == []


def test_drift_flag_is_not_hashed(svc, repo):
    plain = post(svc, key="a")
    flagged_lines = sale()
    flagged_lines[2] = replace(flagged_lines[2], drift=True)
    flagged = post(svc, key="b", lines=flagged_lines)
    assert plain.warnings == [] and len(flagged.warnings) == 1
    assert svc.verify_chain(2026) == []


def test_manual_entry_with_vat_date_in_a_locked_month_is_422(svc, repo):
    lock(repo, 2026, 1)
    manual = JournalSource(event="manual", origin="manual", created_by="u1x000")
    with pytest.raises(ServiceError) as err:
        svc.post("manual:1", date(2026, 2, 3), "m", manual, sale())  # vat_date 2026-01-15
    assert err.value.status == 422 and repo.list("journal_entries") == []
    ok = [replace(ln, vat_date=date(2026, 2, 3)) if ln.vat_date else ln for ln in sale()]
    assert svc.post("manual:2", date(2026, 2, 3), "m", manual, ok).booking_date == "2026-02-03"
    drifting = [replace(ln, drift=True) if ln.vat_date else ln for ln in ok]
    with pytest.raises(ServiceError) as err:
        svc.post("manual:3", date(2026, 2, 3), "m", manual, drifting)
    assert err.value.status == 422


def test_check_reversible_refuses_a_locked_booking_date(svc, repo):
    early = post(svc, key="e", day=date(2026, 1, 15))
    late = post(svc, key="l", day=date(2026, 2, 15))
    svc.check_reversible(early)  # nothing locked
    lock(repo, 2026, 1)
    with pytest.raises(ServiceError) as err:
        svc.check_reversible(early)
    assert err.value.status == 409
    svc.check_reversible(late)
    svc.check_reversible({"booking_date": "2026-02-15"})
    with pytest.raises(ServiceError) as err:
        svc.check_reversible({"booking_date": "2026-01-31"})
    assert err.value.status == 409


def test_closing_date_entry_is_reversible_until_december_is_locked(svc, repo):
    repo.save("fiscal_years", fiscal_year(2027))
    for m in range(1, 12):
        lock(repo, 2026, m)
    e = post(svc, key="c", day=date(2026, 12, 20))
    svc.check_reversible(e)
    lock(repo, 2026, 12)
    with pytest.raises(ServiceError) as err:
        svc.check_reversible(e)  # the closing-date exception does not make a locked month reversible
    assert err.value.status == 409
