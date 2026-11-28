"""Independent model of the accounting target rule (docs/specs/accounting.md 9.1 - 9.3).

Written from the specification text alone, with exact ``fractions.Fraction`` arithmetic, its own half-up rounding and
its own largest-remainder allocation. It imports nothing from ``permita38``: the model and the engine must not share
code. Money is integer cents; dates are ``datetime.date``.

The model keeps the state of one invoice (section 9.2 "State") and computes, after every event, the **targets** of
the spec as cumulative role sums plus the VAT target per tax key. The posted difference between two target sets is
the delta of an event; its VAT part is booked into the month of the event's ``vat_date``.
"""

import math
from dataclasses import dataclass, field
from datetime import date
from fractions import Fraction

HALF = Fraction(1, 2)


def q2(x: Fraction) -> int:
    """Round to whole cents, half away from zero."""
    x = Fraction(x)
    if x >= 0:
        return math.floor(x + HALF)
    return -math.floor(-x + HALF)


def allocate(total: int, weights: dict[int, int]) -> dict[int, int]:
    """Split ``total`` over ``weights`` (rate -> weight) by largest remainder; ties go to the larger rate."""
    out = dict.fromkeys(weights, 0)
    if total == 0:
        return out
    wsum = sum(weights.values())
    if wsum == 0:
        raise ValueError("cannot allocate over zero weights")
    shares = {r: Fraction(total * w, wsum) for r, w in weights.items()}
    floors = {r: math.floor(s) for r, s in shares.items()}
    rest = total - sum(floors.values())
    order = sorted(shares, key=lambda r: (-(shares[r] - floors[r]), -r))
    for r in order[:rest]:
        floors[r] += 1
    return floors


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def suffix(rate: int) -> str:
    return {19: "s19", 7: "s7", 0: "s0"}.get(rate, "sx")


def tax_key_of(rate: int) -> str:
    return {19: "S19", 7: "S7"}.get(rate, "SX")


@dataclass
class Group:
    rate: int
    net: int
    vat: int
    # credited so far (cumulative, write-off reversals subtracted), by kind: kind -> [net, vat]
    credits: dict[str, list[int]] = field(default_factory=dict)
    wo_gross: int = 0  # written off and not yet reversed
    wo_vat: int = 0

    @property
    def gross(self) -> int:
        return self.net + self.vat

    def credited_net(self, *kinds: str) -> int:
        return sum(self.credits.get(k, [0, 0])[0] for k in (kinds or self.credits))

    @property
    def k_net(self) -> int:
        return sum(v[0] for v in self.credits.values())

    @property
    def k_vat(self) -> int:
        return sum(v[1] for v in self.credits.values())

    @property
    def k(self) -> int:
        return self.k_net + self.k_vat

    @property
    def c(self) -> int:
        return self.gross - self.k

    @property
    def cv(self) -> int:
        return self.vat - self.k_vat

    @property
    def cn(self) -> int:
        return self.c - self.cv


@dataclass
class Delta:
    roles: dict[str, int]  # role -> amount (debit positive), zeros removed
    vat: dict[tuple[str, str], list[int]]  # (tax_key, 'YYYY-MM') -> [base, tax] (VAT effect sign), zeros removed

    def merged(self, other: "Delta") -> "Delta":
        roles = dict(self.roles)
        for k, v in other.roles.items():
            roles[k] = roles.get(k, 0) + v
        vat = {k: list(v) for k, v in self.vat.items()}
        for k, (b, t) in other.vat.items():
            cur = vat.setdefault(k, [0, 0])
            cur[0] += b
            cur[1] += t
        return Delta({k: v for k, v in roles.items() if v}, {k: v for k, v in vat.items() if v != [0, 0]})


@dataclass
class GroupTarget:
    t: int
    tv: int
    tn: int
    d: int
    b: int


class InvoiceModel:
    """One issued invoice under one basis combination and treatment."""

    def __init__(
        self,
        income: str,
        vat: str,
        treatment: str,
        rates: dict[int, tuple[int, int]],
        invoice_date: date,
        perf: date | None = None,
    ):
        self.income = income
        self.vat = vat
        self.treatment = treatment
        self.groups = [Group(r, n, v) for r, (n, v) in sorted(rates.items(), reverse=True)]
        self.invoice_date = invoice_date
        self.perf = perf or invoice_date
        self.kept = 0  # P
        # cumulative VAT sums by (tax_key, month): [base, tax]
        self.vat_sums: dict[tuple[str, str], list[int]] = {}
        self.roles: dict[str, int] = {}  # cumulative = current targets
        self._vat_targets: dict[str, tuple[int, int]] = {}  # tax_key -> (D, B)

    # ---- state ---------------------------------------------------------------------------------------------
    @property
    def gross(self) -> int:
        return sum(g.gross for g in self.groups)

    @property
    def current(self) -> int:  # C
        return sum(g.c for g in self.groups)

    @property
    def settled(self) -> int:  # T
        return 0 if self.gross <= 0 else min(self.kept, self.gross)

    @property
    def overpayment(self) -> int:
        return self.kept - self.settled

    @property
    def written_off_gross(self) -> int:
        return sum(g.wo_gross for g in self.groups)

    # ---- targets (9.2) -------------------------------------------------------------------------------------
    def group_targets(self) -> dict[int, GroupTarget]:
        T, C = self.settled, self.current
        out: dict[int, GroupTarget] = {}
        legacy_negative = self.gross <= 0
        if legacy_negative:
            tr = dict.fromkeys((g.rate for g in self.groups), 0)
            tvr = dict(tr)
        elif T <= C:
            tr = allocate(T, {g.rate: g.c for g in self.groups})
            tvr = {g.rate: (q2(Fraction(g.cv * tr[g.rate], g.c)) if g.c else 0) for g in self.groups}
        else:
            extra = allocate(T - C, {g.rate: g.k for g in self.groups})
            tr = {g.rate: g.c + extra[g.rate] for g in self.groups}
            tvr = {g.rate: g.cv + (q2(Fraction(g.k_vat * (tr[g.rate] - g.c), g.k)) if g.k else 0) for g in self.groups}
        for g in self.groups:
            t, tv = tr[g.rate], tvr[g.rate]
            tn = t - tv
            if self.vat == "soll":
                if legacy_negative:
                    d, b = g.cv, g.cn
                elif g.cv >= tv:
                    d, b = max(g.cv, tv), g.cn
                else:
                    d, b = tv, tn
            else:
                d, b = tv, tn
            out[g.rate] = GroupTarget(t, tv, tn, d, b)
        return out

    def compute_targets(self) -> tuple[dict[str, int], dict[str, tuple[int, int]]]:
        """Cumulative role sums and the VAT target (D, B) per tax key for the current state."""
        gt = self.group_targets()
        P, T, C = self.kept, self.settled, self.current
        roles: dict[str, int] = {"bank": P, "customer_credit": -(P - T)}
        vat: dict[str, tuple[int, int]] = {}
        accrual = self.income == "accrual"
        if accrual:
            roles["receivables"] = C - T
            roles["cash_discount_granted"] = sum(g.credited_net("discount") for g in self.groups)
            roles["bad_debts"] = sum(g.credited_net("write_off") for g in self.groups)
        cn_total = sum(g.cn for g in self.groups)
        if self.treatment == "standard":
            for g in self.groups:
                x, s = gt[g.rate], suffix(g.rate)
                if accrual:
                    roles["revenue_" + s] = -(g.net - g.credited_net("storno", "partial"))
                else:
                    roles["revenue_" + s] = -x.tn
                if g.rate > 0:
                    roles["vat_out_" + s] = -x.d
                    if accrual:
                        roles["vat_not_due_" + s] = -(g.cv - x.d)
                    elif self.vat == "soll":
                        roles["vat_soll_clearing_" + s] = x.d - x.tv
                    vat[tax_key_of(g.rate)] = (x.d, x.b)
        elif self.treatment == "eu_b2b_service":
            roles["revenue_eu_svc"] = (
                -(sum(g.net for g in self.groups) - sum(g.credited_net("storno", "partial") for g in self.groups))
                if accrual
                else -T
            )
            vat["EU_SVC"] = (0, cn_total)
        elif self.treatment == "small_business":
            roles["revenue_ku"] = (
                -(sum(g.net for g in self.groups) - sum(g.credited_net("storno", "partial") for g in self.groups))
                if accrual
                else -T
            )
            vat["KU"] = (0, cn_total if accrual else T)
        else:
            raise ValueError(self.treatment)
        return {k: v for k, v in roles.items() if v}, vat

    # ---- events --------------------------------------------------------------------------------------------
    def _post(self, vat_date: date) -> Delta:
        roles, vat = self.compute_targets()
        droles = {k: roles.get(k, 0) - self.roles.get(k, 0) for k in set(roles) | set(self.roles)}
        droles = {k: v for k, v in droles.items() if v}
        dvat: dict[tuple[str, str], list[int]] = {}
        for key in set(vat) | set(self._vat_targets):
            d1, b1 = vat.get(key, (0, 0))
            d0, b0 = self._vat_targets.get(key, (0, 0))
            if (d1 - d0, b1 - b0) != (0, 0):
                dvat[(key, month_of(vat_date))] = [b1 - b0, d1 - d0]
        for k, (b, t) in dvat.items():
            cur = self.vat_sums.setdefault(k, [0, 0])
            cur[0] += b
            cur[1] += t
        self.roles, self._vat_targets = roles, vat
        assert sum(roles.values()) == 0, f"targets do not balance: {roles}"
        return Delta(droles, dvat)

    def issue(self) -> Delta:
        """The issue entry: the targets of the initial state, VAT dated at the performance date (invoice date for KU)."""
        vat_date = self.invoice_date if self.treatment == "small_business" else self.perf
        return self._post(vat_date)

    def credit(self, kind: str, rates: dict[int, tuple[int, int]], on: date) -> Delta:
        """A credit document with per-rate (net, vat); write_off_reversal carries positive amounts that are taken back."""
        sign = -1 if kind == "write_off_reversal" else 1
        for g in self.groups:
            net, vat = rates.get(g.rate, (0, 0))
            if sign > 0:
                assert net >= 0 and vat >= 0 and net + vat <= g.c and vat <= g.cv, ("credit too large", g.rate)
            ck = "write_off" if kind == "write_off_reversal" else kind
            cur = g.credits.setdefault(ck, [0, 0])
            cur[0] += sign * net
            cur[1] += sign * vat
            if kind == "write_off":
                g.wo_gross += net + vat
                g.wo_vat += vat
            elif kind == "write_off_reversal":
                g.wo_gross -= net + vat
                g.wo_vat -= vat
                assert g.wo_gross >= 0 and g.wo_vat >= 0
        return self._post(on)

    def reversal_amounts(self, amount: int) -> dict[int, tuple[int, int]]:
        """The write-off reversal a payment of ``amount`` triggers (positive net, vat per rate); empty if none."""
        if amount <= 0 or self.written_off_gross == 0 or self.gross <= 0:
            return {}
        t_before = self.settled
        t_after = min(self.kept + amount, self.gross)
        total = min(t_after - max(self.current, t_before), self.written_off_gross)
        if total <= 0:
            return {}
        share = allocate(total, {g.rate: g.wo_gross for g in self.groups})
        out = {}
        for g in self.groups:
            gross = share[g.rate]
            if gross == 0:
                continue
            vat = q2(Fraction(g.wo_vat * gross, g.wo_gross))
            out[g.rate] = (gross - vat, vat)
        return out

    def payment(self, amount: int, on: date) -> list[tuple[str, Delta, dict[int, tuple[int, int]]]]:
        """A payment (> 0) or refund (< 0). Returns [(kind, delta, rates)]: an optional reversal, then the payment."""
        out = []
        rev = self.reversal_amounts(amount)
        if rev:
            out.append(("write_off_reversal", self.credit("write_off_reversal", rev, on), rev))
        assert self.kept + amount >= 0, "P < 0 is refused"
        self.kept += amount
        out.append(("payment", self._post(on), {}))
        return out

    # ---- document amounts the spec derives (9.3) ------------------------------------------------------------
    def storno_amounts(self) -> dict[int, tuple[int, int]]:
        return {g.rate: (g.cn, g.cv) for g in self.groups if g.c > 0}

    def write_off_amounts(self) -> dict[int, tuple[int, int]]:
        """Open amount C_r - T_r per rate group; the VAT is the complement CV_r - TV_r."""
        if self.current <= self.settled:
            return {}
        gt = self.group_targets()
        out = {}
        for g in self.groups:
            gross = g.c - gt[g.rate].t
            vat = g.cv - gt[g.rate].tv
            if gross > 0 or vat > 0:
                out[g.rate] = (gross - vat, vat)
        return out

    def discount_amounts(self, paid: int) -> dict[int, tuple[int, int]]:
        """Settle with discount: C_r - T'_r where T' allocates the cash after paying ``paid`` over the current C_r."""
        t_after = self.kept + paid
        if not 0 < t_after < self.current:
            return {}
        tr = allocate(t_after, {g.rate: g.c for g in self.groups})
        out = {}
        for g in self.groups:
            tvr = q2(Fraction(g.cv * tr[g.rate], g.c)) if g.c else 0
            gross, vat = g.c - tr[g.rate], g.cv - tvr
            if gross > 0 or vat > 0:
                out[g.rate] = (gross - vat, vat)
        return out

    def partial_amounts(self, permille: list[int]) -> dict[int, tuple[int, int]]:
        """A partial credit of ``permille[i]`` / 1000 of the current net of group i (VAT at the rate, capped)."""
        out = {}
        for g, pm in zip(self.groups, permille, strict=False):
            net = g.cn * pm // 1000
            if pm >= 1000 or net == g.cn:
                net, vat = g.cn, g.cv
            else:
                vat = min(g.cv, q2(Fraction(net * g.rate, 100)))
            if net > 0 or vat > 0:
                out[g.rate] = (net, vat)
        return out

    # ---- derived numbers for the properties ------------------------------------------------------------------
    def due_vat_by_key(self) -> dict[str, int]:
        return {k: d for k, (d, _) in self._vat_targets.items() if d}

    def posted_vat(self) -> dict[str, tuple[int, int]]:
        """Sum over all months: tax_key -> (base, tax)."""
        out: dict[str, list[int]] = {}
        for (key, _), (b, t) in self.vat_sums.items():
            cur = out.setdefault(key, [0, 0])
            cur[0] += b
            cur[1] += t
        return {k: (v[0], v[1]) for k, v in out.items()}


def issue_posts_entry(income: str, vat: str, treatment: str) -> bool:
    """Spec 9.1: which (basis, treatment) combinations post an issue entry (for an invoice with non-zero amounts)."""
    if treatment == "standard":
        return not (income == "eur" and vat == "ist")
    if treatment == "small_business":
        return income == "accrual"
    return True  # eu_b2b_service posts a line (statistical under EÜR)


# ---- fixture replay -----------------------------------------------------------------------------------------------
def replay_fixture_case(case: dict, all_cases: dict[str, dict]) -> tuple[dict[int, dict[str, int]], dict]:
    """Replay the invoice events of a fixture journal case.

    Returns (role delta per fixture event index, final VAT sums {'YYYY-MM': {tax_key: {'base', 'tax'}}}).
    Only ``issue``, ``payment``, ``credit_note`` and ``lock`` events are supported; ``lock`` has no effect on the lines.
    """
    events = case["events"]
    if isinstance(events, str):
        events = all_cases[events.removeprefix("same as ")]["events"]
    bases = case["bases"]
    income, vat = bases["income_basis"], bases["vat_basis"]
    models: dict[str, InvoiceModel] = {}
    per_event: dict[int, dict[str, int]] = {}
    for i, ev in enumerate(events):
        kind = ev["type"]
        if kind == "lock":
            continue
        if kind == "issue":
            rates = {int(r): (v["net"], v["vat"]) for r, v in ev["rates"].items()}
            on = date.fromisoformat(ev["date"])
            perf = date.fromisoformat(ev.get("service_to") or ev.get("service_from") or ev["date"])
            m = InvoiceModel(income, vat, ev["treatment"], rates, on, perf)
            models[ev["id"]] = m
            d = m.issue()
        elif kind == "payment":
            m = models[ev["invoice"]]
            d = Delta({}, {})
            for _, part, _ in m.payment(ev["amount"], date.fromisoformat(ev["date"])):
                d = d.merged(part)
        elif kind == "credit_note":
            m = models[ev["invoice"]]
            rates = {int(r): (v["net"], v.get("vat", 0)) for r, v in ev["rates"].items()}
            d = m.credit(ev["credit_kind"], rates, date.fromisoformat(ev["date"]))
        else:
            raise ValueError(f"event type {kind} is not an invoice event")
        per_event[i] = d.roles
    sums: dict[str, dict[str, dict[str, int]]] = {}
    for m in models.values():
        for (key, month), (b, t) in m.vat_sums.items():
            if b or t:
                sums.setdefault(month, {}).setdefault(key, {"base": 0, "tax": 0})
                sums[month][key]["base"] += b
                sums[month][key]["tax"] += t
    return per_event, sums
