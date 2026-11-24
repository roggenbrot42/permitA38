"""DATEV rows from journal entries and the DATEV simulation (docs/specs/datev-export.md, "From journal entries to
rows" and "Self-check"; docs/specs/accounting.md sections 5, 6, 7a).

Pure functions, integer cents, no files and no HTTP. :func:`entry_rows` turns one journal entry into DATEV rows
(gross rows with VAT folded in where DATEV's own split reproduces the booked VAT, otherwise the unfolded form, and as
the last resort the interim account ``datev_transfer``); :func:`simulate` re-splits rows the way DATEV does and sums
per account, so the result can be compared with :func:`journal_balances`.

Row orientation: ``konto`` is the debited account and the flag is ``S`` (the spec examples), amounts are > 0.
"""

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from typing import Literal

from permita38.core.accounting.types import AccountNo, Cents, Entry, Line
from permita38.core.charts import Chart, Role
from permita38.core.datev import DatevRules

__all__ = [
    "DatevBatchError",
    "DatevRow",
    "KnownDifference",
    "batch_rows",
    "entry_rows",
    "journal_balances",
    "known_differences",
    "simulate",
]

Note = Literal["fallback", "transfer"]
DifferenceKind = Literal["eur_clearing", "statistical", "fallback", "transfer"]

_VAT_KEYS = frozenset({"S19", "S7", "SX", "VST", "RC_EU_SVC", "RC_3C_SVC"})
_OUT_ROLE = {"S19": "vat_out_s19", "S7": "vat_out_s7", "SX": "vat_out_sx"}
_RC_ROLES = {"RC_EU_SVC": ("rc_vat_out_eu", "rc_input_vat"), "RC_3C_SVC": ("rc_vat_out_3c", "rc_input_vat")}
_ROLE_RATE = {"s19": Decimal(19), "s7": Decimal(7)}
_TAX_ROLE_PREFIXES = ("vat_", "input_vat", "rc_")
_STRICT_PREFIXES = ("vat_out_", "input_vat_", "rc_vat_out_")
_CLEARING_OF = {
    "vat_out_s19": ("vat_not_due_s19", "vat_soll_clearing_s19"),
    "vat_out_s7": ("vat_not_due_s7", "vat_soll_clearing_s7"),
    "vat_out_sx": ("vat_not_due_sx", "vat_soll_clearing_sx"),
    "input_vat_s19": ("input_vat_clearing",),
    "input_vat_s7": ("input_vat_clearing",),
    "input_vat_sx": ("input_vat_clearing",),
    "rc_vat_out_eu": ("rc_vat_nondeductible",),
    "rc_vat_out_3c": ("rc_vat_nondeductible",),
}
_TRANSFER_ROLE = Role("datev_transfer")


class DatevBatchError(ValueError):
    """A batch of entries cannot be exported as one file (it crosses a fiscal year)."""


@dataclass(frozen=True)
class DatevRow:
    """One booking row. ``side`` is the flag from the view of ``konto``; ``note`` marks fallback rows."""

    amount_cents: Cents
    side: Literal["S", "H"]
    konto: AccountNo
    gegenkonto: AccountNo
    bu_key: str | None
    belegdatum: str  # DDMM
    belegfeld1: str  # at most 36 characters
    text: str  # at most 60 characters
    festschreibung: bool = False
    event_key: str = ""
    note: Note | None = None


@dataclass(frozen=True)
class KnownDifference:
    kind: DifferenceKind
    event_key: str
    detail: str


@dataclass(frozen=True)
class _Pair:
    debit: AccountNo
    credit: AccountNo
    amount: int
    bu: str | None = None
    plain: bool = True  # plain rows get the cancel key when they touch an automatic account


@dataclass
class _Work:
    line: Line
    role: str
    account: AccountNo
    rem: int
    vat: bool
    pl: bool
    bu: str | None = None
    folded: bool = False


@dataclass(frozen=True)
class _Build:
    pairs: list[_Pair] | None
    attempted: bool  # a VAT line had a partner row to fold into


@dataclass(frozen=True)
class _Ctx:
    account_of: dict[str, AccountNo]
    cls: dict[AccountNo, str]
    auto: dict[AccountNo, str]  # automatic key of non-tax accounts
    strict: frozenset[AccountNo]  # accounts whose VAT must never be reclassified
    bu_to_tax: dict[str, tuple[str, Decimal | None]]
    cancel: str
    rules: DatevRules
    rates: Mapping[str, Decimal]
    transfer: AccountNo


def _context(chart: Chart, rules: DatevRules, rates: Mapping[str, Decimal]) -> _Ctx:
    account_of: dict[str, AccountNo] = {}
    cls: dict[AccountNo, str] = {}
    auto: dict[AccountNo, str] = {}
    tax_accounts: set[AccountNo] = set()
    strict: set[AccountNo] = set()
    for role, acc in chart.roles:
        no = AccountNo(acc.account)
        account_of[role] = no
        if role.startswith(_TAX_ROLE_PREFIXES):
            tax_accounts.add(no)
        if role.startswith(_STRICT_PREFIXES):
            strict.add(no)
    for _, acc in chart.roles:
        cls[AccountNo(acc.account)] = acc.account_class
    for acc in chart.accounts:
        cls.setdefault(AccountNo(acc.account), acc.account_class)
    for source in ([a for _, a in chart.roles], list(chart.accounts)):
        for acc in source:
            no = AccountNo(acc.account)
            if acc.automatic is not None and no not in tax_accounts:
                auto[no] = acc.automatic
    bu_to_tax: dict[str, tuple[str, Decimal | None]] = {}
    for tax_key, value in rules.bu_keys.items():
        if isinstance(value, dict):
            for rate, key in value.items():
                bu_to_tax.setdefault(key, (tax_key, Decimal(rate)))
        elif value is not None:
            bu_to_tax.setdefault(value, (tax_key, None))
    return _Ctx(
        account_of=account_of,
        cls=cls,
        auto=auto,
        strict=frozenset(strict),
        bu_to_tax=bu_to_tax,
        cancel=rules.cancel_automatic_key,
        rules=rules,
        rates=rates,
        transfer=account_of[_TRANSFER_ROLE],
    )


def _half_up(value: Fraction) -> int:
    """Non-negative exact rational -> whole cents, ties up."""
    return (2 * value.numerator + value.denominator) // (2 * value.denominator)


def _vat_account(ctx: _Ctx, key: str, rate: Decimal) -> AccountNo | None:
    if key in _OUT_ROLE:
        return ctx.account_of.get(_OUT_ROLE[key])
    if key == "VST":
        suffix = "s19" if rate == 19 else "s7" if rate == 7 else "sx"
        return ctx.account_of.get(f"input_vat_{suffix}")
    return None


def _taxed_account(ctx: _Ctx, konto: AccountNo, gegen: AccountNo) -> AccountNo:
    konto_pl = ctx.cls.get(konto) == "pl"
    gegen_pl = ctx.cls.get(gegen) == "pl"
    return gegen if gegen_pl and not konto_pl else konto


def _deltas(
    ctx: _Ctx, amount: int, side: str, konto: AccountNo, gegen: AccountNo, bu: str | None
) -> list[tuple[AccountNo, int]]:
    """The signed (debit positive) effect of one row on the accounts, after DATEV's own tax split."""
    s = 1 if side == "S" else -1
    out = [(konto, s * amount), (gegen, -s * amount)]
    taxed: AccountNo | None = None
    key: str | None = None
    rate: Decimal | None = None
    if bu is not None:
        hit = ctx.bu_to_tax.get(bu)
        if bu == ctx.cancel or hit is None:
            return out
        key, rate = hit
        taxed = _taxed_account(ctx, konto, gegen)
    else:
        for acc in (konto, gegen):
            if acc in ctx.auto:
                taxed, key = acc, ctx.auto[acc]
                break
    if taxed is None or key is None:
        return out
    if rate is None:
        rate = ctx.rates.get(key)
    if rate is None:
        return out
    st = s if taxed == konto else -s
    if key in _RC_ROLES:
        tax = _half_up(Fraction(amount) * Fraction(rate) / 100)
        out_no = ctx.account_of.get(_RC_ROLES[key][0])
        in_no = ctx.account_of.get(_RC_ROLES[key][1])
        if out_no is None or in_no is None:
            return out
        return [*out, (out_no, -st * tax), (in_no, st * tax)]
    tax = _half_up(Fraction(amount) * Fraction(rate) / (100 + Fraction(rate)))
    vat_no = _vat_account(ctx, key, rate)
    if vat_no is None:
        return out
    return [*out, (taxed, -st * tax), (vat_no, st * tax)]


def _sum(deltas: Sequence[tuple[AccountNo, int]]) -> dict[AccountNo, Cents]:
    total: dict[AccountNo, int] = {}
    for no, amount in deltas:
        total[no] = total.get(no, 0) + amount
    return {no: Cents(v) for no, v in total.items() if v != 0}


def simulate(
    rows: Sequence[DatevRow], chart: Chart, datev_rules: DatevRules, vat_rates: Mapping[str, Decimal]
) -> dict[AccountNo, Cents]:
    """Re-split every row by its automatic account or BU key (half-up per row) and sum per account."""
    ctx = _context(chart, datev_rules, vat_rates)
    return _simulate(ctx, rows)


def _simulate(ctx: _Ctx, rows: Sequence[DatevRow]) -> dict[AccountNo, Cents]:
    deltas: list[tuple[AccountNo, int]] = []
    for r in rows:
        deltas.extend(_deltas(ctx, r.amount_cents, r.side, r.konto, r.gegenkonto, r.bu_key))
    return _sum(deltas)


def _line_balances(ctx: _Ctx, lines: Sequence[Line]) -> dict[AccountNo, Cents]:
    return _sum([(ctx.account_of[ln.role], ln.amount_cents) for ln in lines])


def journal_balances(entries: Sequence[Entry], chart: Chart) -> dict[AccountNo, Cents]:
    """The journal's balance (debit positive) per account for ``entries``; zero balances are left out."""
    account_of = {role: AccountNo(acc.account) for role, acc in chart.roles}
    return _sum([(account_of[Role(ln.role)], ln.amount_cents) for e in entries for ln in e.lines])


# ---------------------------------------------------------------------------------------------------------------
# anchors and VAT lines

_MONEY = frozenset({"bank", "cash"})
_OPEN_ITEMS = frozenset({"receivables", "payables", "payables_employee", "customer_credit"})
_PRIVATE = frozenset({"private_withdrawal", "private_deposit"})


def _rank(role: str) -> int:
    if role in _MONEY:
        return 5
    if role in _OPEN_ITEMS:
        return 4
    if role.startswith(("vat_soll_clearing_", "vat_not_due_")) or role in (
        "input_vat_clearing",
        "rc_vat_nondeductible",
    ):
        return 3
    if role == "opening_balance":
        return 2
    if role in _PRIVATE:
        return 1
    return 0


def _is_vat(line: Line) -> bool:
    return line.tax_kind in ("tax", "input") and line.tax_key in _VAT_KEYS


def _rate_of(ctx: _Ctx, w: _Work) -> Decimal | None:
    if w.line.tax_rate is not None:
        return Decimal(w.line.tax_rate)
    if w.role.startswith("rc_"):
        rate = ctx.rates.get(w.line.tax_key or "")
        if rate is None:
            raise DatevBatchError(f"No VAT rate for reverse-charge key {w.line.tax_key!r}")
        return rate
    return _ROLE_RATE.get(w.role.rsplit("_", 1)[-1])


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _take(debit_side: _Work, credit_side: _Work, pairs: list[_Pair]) -> None:
    amount = min(abs(debit_side.rem), abs(credit_side.rem))
    if amount == 0:
        return
    pairs.append(_Pair(debit_side.account, credit_side.account, amount))
    debit_side.rem -= amount
    credit_side.rem += amount


def _pair_off(works: list[_Work], fold: bool, pairs: list[_Pair]) -> None:
    """VAT lines balanced by a clearing line, by the other half of a § 13b pair or (unfolded) by the cost line."""
    for v in works:
        if not v.vat:
            continue
        roles: list[str] = list(_CLEARING_OF.get(v.role, ()))
        if not fold and v.role.startswith("rc_vat_out"):
            roles.append("rc_input_vat")
        if not fold and v.role == "rc_input_vat":
            roles += ["rc_vat_out_eu", "rc_vat_out_3c"]
        candidates = [w for r in roles for w in works if w.role == r]
        if not fold and v.role.startswith("rc_vat_out"):
            candidates += [w for w in works if w.pl]
        for c in candidates:
            if v.rem == 0:
                break
            if c.rem == 0 or _sign(c.rem) == _sign(v.rem):
                continue
            debit, credit = (v, c) if v.rem > 0 else (c, v)
            _take(debit, credit, pairs)


def _group_key(ctx: _Ctx, w: _Work) -> tuple[str, Decimal | None]:
    return (w.line.tax_key or "", _rate_of(ctx, w))


def _fold(ctx: _Ctx, works: list[_Work]) -> tuple[bool, bool]:
    """Fold the VAT lines into their partner rows. Returns (possible, attempted)."""
    carriers = [w for w in works if w.pl and w.rem != 0]
    groups: dict[int, list[_Work]] = {}
    chosen: set[int] = set()
    for v in works:
        if not v.vat or v.rem == 0 or not carriers:
            continue
        target = next(
            (c for c in carriers if v.role.startswith("vat_out_") and c.role == "revenue_" + v.role[8:]), None
        )
        if target is None:
            target = max(carriers, key=lambda c: abs(c.rem))
            chosen.add(id(target))
        groups.setdefault(id(target), []).append(v)
    if not groups:
        return True, False
    by_id = {id(c): c for c in carriers}
    if len(chosen) == 1:
        # the VAT belongs to the whole cost: its row carries the full net, the shares follow as reclassifications
        main = by_id[next(iter(chosen))]
        for c in carriers:
            if c is not main and id(c) not in groups and _sign(c.rem) == _sign(main.rem):
                main.rem += c.rem
                c.rem = 0
    for ident, vats in groups.items():
        target = by_id[ident]
        keys = {_group_key(ctx, v) for v in vats}
        if len(keys) != 1:
            return False, True
        tax_key, rate = next(iter(keys))
        bu = ctx.rules.bu_key(tax_key, rate)
        auto = ctx.auto.get(target.account)
        if auto == tax_key and tax_key in ctx.rates:
            bu = None  # the automatic account splits the tax itself
        elif bu is None:
            return False, True
        target.bu = bu
        target.folded = True
        for v in vats:
            target.rem += v.rem
            v.rem = 0
    return True, True


def _build(ctx: _Ctx, lines: Sequence[Line], fold: bool) -> _Build:
    works = [
        _Work(
            line=ln,
            role=ln.role,
            account=ctx.account_of[ln.role],
            rem=ln.amount_cents,
            vat=_is_vat(ln),
            pl=ctx.cls.get(ctx.account_of[ln.role]) == "pl" and not _is_vat(ln),
        )
        for ln in lines
    ]
    pairs_off: list[_Pair] = []
    _pair_off(works, fold, pairs_off)
    attempted = False
    if fold:
        possible, attempted = _fold(ctx, works)
        if not possible:
            return _Build(None, attempted)
    rest = [w for w in works if w.rem != 0]
    hub: list[_Pair] = []
    if rest:
        ranked = [w for w in rest if _rank(w.role) > 0]
        if not ranked:
            return _Build(None, attempted)
        anchor = max(ranked, key=lambda w: (_rank(w.role), abs(w.rem)))
        for w in rest:
            if w is anchor:
                continue
            if _sign(w.rem) == _sign(anchor.rem):
                return _Build(None, attempted)
            if w.rem > 0:
                hub.append(_Pair(w.account, anchor.account, w.rem, w.bu, plain=not w.folded))
            else:
                hub.append(_Pair(anchor.account, w.account, -w.rem, w.bu, plain=not w.folded))
    return _Build([*hub, *pairs_off], attempted)


# ---------------------------------------------------------------------------------------------------------------
# rows


def _belegfeld1(event_key: str) -> str:
    """Belegfeld 1 (36 characters): long keys keep a 27 character prefix plus a hash so entries stay distinct."""
    if len(event_key) <= 36:
        return event_key
    return event_key[:27] + "~" + hashlib.sha256(event_key.encode()).hexdigest()[:8]


def _to_rows(
    ctx: _Ctx, entry: Entry, pairs: Sequence[_Pair], festschreibung: bool, note: Note | None
) -> list[DatevRow]:
    rows: list[DatevRow] = []
    for p in pairs:
        bu = p.bu
        if bu is None and p.plain and (p.debit in ctx.auto or p.credit in ctx.auto):
            bu = ctx.cancel
        rows.append(
            DatevRow(
                amount_cents=Cents(p.amount),
                side="S",
                konto=p.debit,
                gegenkonto=p.credit,
                bu_key=bu,
                belegdatum=entry.booking_date.strftime("%d%m"),
                belegfeld1=_belegfeld1(entry.event_key),
                text=entry.event_key[:60],
                festschreibung=festschreibung,
                event_key=entry.event_key,
                note=note,
            )
        )
    return rows


def _residual_pairs(residual: dict[AccountNo, Cents]) -> list[_Pair]:
    debits = [(no, int(v)) for no, v in sorted(residual.items()) if v > 0]
    credits = [(no, -int(v)) for no, v in sorted(residual.items()) if v < 0]
    pairs: list[_Pair] = []
    while debits and credits:
        (d_no, d_amount), (c_no, c_amount) = debits[0], credits[0]
        amount = min(d_amount, c_amount)
        pairs.append(_Pair(d_no, c_no, amount))
        debits[0], credits[0] = (d_no, d_amount - amount), (c_no, c_amount - amount)
        if debits[0][1] == 0:
            debits.pop(0)
        if credits[0][1] == 0:
            credits.pop(0)
    return pairs


def _folded_rows(
    ctx: _Ctx, entry: Entry, pairs: list[_Pair], target: dict[AccountNo, Cents], festschreibung: bool
) -> list[DatevRow] | None:
    """The folded candidate plus reclassification rows; None when DATEV's split would change a booked VAT amount."""
    rows = _to_rows(ctx, entry, pairs, festschreibung, None)
    sim = _simulate(ctx, rows)
    residual = _sum([*((no, v) for no, v in target.items()), *((no, -v) for no, v in sim.items())])
    if not residual:
        return rows
    if any(no in ctx.strict for no in residual):
        return None
    rows += _to_rows(ctx, entry, _residual_pairs(residual), festschreibung, None)
    return rows if _simulate(ctx, rows) == target else None


def entry_rows(
    entry: Entry,
    chart: Chart,
    datev_rules: DatevRules,
    vat_rates: Mapping[str, Decimal],
    *,
    festschreibung: bool = False,
) -> list[DatevRow]:
    """DATEV rows of one journal entry (rules 1 to 6). The result always simulates to the entry's balances."""
    ctx = _context(chart, datev_rules, vat_rates)
    lines = [ln for ln in entry.lines if ln.amount_cents != 0]
    if not lines:
        return []
    target = _line_balances(ctx, lines)
    folded = _build(ctx, lines, fold=True)
    if folded.pairs is not None:
        rows = _folded_rows(ctx, entry, folded.pairs, target, festschreibung)
        if rows is not None:
            return rows
    note: Note | None = "fallback" if folded.attempted else None
    plain = _build(ctx, lines, fold=False)
    if plain.pairs is not None:
        rows = _to_rows(ctx, entry, plain.pairs, festschreibung, note)
        if _simulate(ctx, rows) == target:
            return rows
    return _to_rows(ctx, entry, _transfer_pairs(ctx, lines), festschreibung, "transfer")


def _transfer_pairs(ctx: _Ctx, lines: Sequence[Line]) -> list[_Pair]:
    pairs: list[_Pair] = []
    for ln in lines:
        no = ctx.account_of[ln.role]
        if ln.amount_cents > 0:
            pairs.append(_Pair(no, ctx.transfer, ln.amount_cents))
        else:
            pairs.append(_Pair(ctx.transfer, no, -ln.amount_cents))
    return pairs


def batch_rows(
    entries: Sequence[Entry],
    chart: Chart,
    datev_rules: DatevRules,
    vat_rates: Mapping[str, Decimal],
    *,
    festschreibung: bool = False,
) -> list[DatevRow]:
    """Rows of one batch ordered by booking date (then the given entry order); one fiscal year only (rule 7)."""
    years = {e.booking_date.year for e in entries}
    if len(years) > 1:
        raise DatevBatchError(f"A DATEV batch covers one fiscal year, not {sorted(years)}")
    ordered: list[Entry] = sorted(entries, key=lambda e: e.booking_date)
    rows: list[DatevRow] = []
    for e in ordered:
        rows += entry_rows(e, chart, datev_rules, vat_rates, festschreibung=festschreibung)
    return rows


def known_differences(entries: Sequence[Entry], rows: Sequence[DatevRow] = ()) -> list[KnownDifference]:
    """EÜR clearing entries, statistical lines and (from ``rows``) the fallback and transfer rows of a batch."""
    found: list[KnownDifference] = []
    for e in entries:
        if any(
            ln.amount_cents != 0 and (ln.role.startswith("vat_soll_clearing_") or ln.role == "input_vat_clearing")
            for ln in e.lines
        ):
            found.append(KnownDifference("eur_clearing", e.event_key, "EUeR clearing account is ignored by DATEV"))
        if any(ln.amount_cents == 0 and ln.tax_kind == "base_only" for ln in e.lines):
            found.append(
                KnownDifference("statistical", e.event_key, "statistical line not exported (Kz 21 follows the payment)")
            )
    seen: set[tuple[str, str]] = set()
    for r in rows:
        if r.note is not None and (r.note, r.event_key) not in seen:
            seen.add((r.note, r.event_key))
            detail = "unfolded VAT rows" if r.note == "fallback" else "booked through datev_transfer"
            found.append(KnownDifference(r.note, r.event_key, detail))
    return found
