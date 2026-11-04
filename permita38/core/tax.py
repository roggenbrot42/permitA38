"""German tax estimate for freelancers and small businesses (docs/specs/tax-burden-and-settings.md, Part B).

These are ESTIMATES for planning how much to set aside, not tax advice. All money is computed with exact
:class:`~decimal.Decimal` arithmetic (``core/money.py``); the rule tables live in ``core/tax_rules/<year>.json``.

Implements algorithm steps 1-12: employment, contributions, Vorsorgeaufwendungen, tariff, children (Guenstigerpruefung),
Gewerbesteuer with the §35 credit, the GmbH, Soli and church tax.
"""

from decimal import ROUND_DOWN, ROUND_FLOOR, Decimal
from typing import Any

from .money import ZERO, dec, q2
from .tax_rules import TaxRules, rules_for

KLEIN_LIMIT_PREV, KLEIN_LIMIT_CUR = 25000, 100000

_HUNDRED = Decimal(100)
_TWELVE = Decimal(12)


def _floor0(x: Decimal) -> Decimal:
    return x.to_integral_value(rounding=ROUND_FLOOR)


def _trunc2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_DOWN)


def _pos(x: Decimal) -> Decimal:
    return x if x > 0 else ZERO


def _clamp(x: Decimal, lo: Decimal, hi: Decimal) -> Decimal:
    return max(lo, min(x, hi))


def _tariff(x: Decimal, r: TaxRules) -> int:
    """§32a EStG for a single person: ``x`` is rounded down to whole euros, the result too."""
    x = _floor0(_pos(x))
    if x <= r.est_gf:
        return 0
    if x <= r.est_z2:
        y = (x - r.est_gf) / 10000
        t = (r.est_a2 * y + 1400) * y
    elif x <= r.est_z3:
        z = (x - r.est_z2) / 10000
        t = (r.est_a3 * z + 2397) * z + r.est_c3
    elif x <= r.est_z4:
        t = Decimal("0.42") * x - r.est_k4
    else:
        t = Decimal("0.45") * x - r.est_k5
    return int(_floor0(t))


def est_single(zve: Any, year: int) -> int:
    """Einkommensteuer (§32a) of a single person on the taxable income ``zve``, whole euros."""
    return _tariff(dec(zve), rules_for(year))


def est(zve: Any, year: int, splitting: bool = False) -> int:
    """Einkommensteuer; with ``splitting`` twice the tariff on half of the (floored) income."""
    r = rules_for(year)
    x = dec(zve)
    if splitting:
        return 2 * _tariff(_floor0(_pos(x)) / 2, r)
    return _tariff(x, r)


def _get(d: Any, key: str, default: Any = None) -> Any:
    """Defensive read of a nested profile value."""
    if isinstance(d, dict):
        v = d.get(key)
        return default if v is None else v
    return default


def _soli(base: Decimal, r: TaxRules, joint: bool) -> Decimal:
    free = r.soli_freigrenze * (2 if joint else 1)
    if base <= free:
        return ZERO
    return _trunc2(min(base * r.soli_rate / _HUNDRED, (base - free) * r.soli_cap_rate / _HUNDRED))


def estimate(profit: Decimal, year: int, settings: dict, profile: dict) -> dict:
    """Annual tax and social-insurance estimate for a business profit.

    ``profit`` may be negative; ``settings`` supplies ``company_type``, ``state`` and ``hebesatz``; ``profile`` is the
    effective personal tax profile (``PersonalTaxIn`` shape, required). Returns Decimals, see the spec for the keys.
    """
    r = rules_for(year)
    settings = settings or {}
    ctype = settings.get("company_type") or "freiberufler"
    joint = _get(profile, "assessment", "single") == "joint"
    warnings: list[str] = [r.warning] if r.warning else []

    health = _get(profile, "health", {})
    pension = _get(profile, "pension", {})
    unemployment = _get(profile, "unemployment", {})
    children = _get(profile, "children", {})
    emp = profile.get("employment") if isinstance(profile.get("employment"), dict) else None
    other_income = dec(_get(profile, "other_income_year", 0))
    other_ded = dec(_get(profile, "other_deductions_year", 0))
    other_ins = dec(_get(profile, "other_insurance_year", 0))
    church_pct = dec(_get(profile, "church_tax_percent", 0))

    biz = ZERO if ctype == "gmbh" else profit  # the GmbH profit is taxed in the company

    # 1. Employment wage
    tax_wage = sv_wage = emp_income = ZERO
    sv_liable = False
    withheld = None
    if emp is not None:
        gross = dec(emp.get("gross_year"))
        bav_ee, bav_er = dec(emp.get("bav_employee_year")), dec(emp.get("bav_employer_year"))
        vwl = dec(emp.get("vwl_employer_year"))
        tax_free = min(bav_ee + bav_er, r.bav_tax_free_percent * r.bbg_rv_year / _HUNDRED)
        sv_free = min(bav_ee + bav_er, r.bav_sv_free_percent * r.bbg_rv_year / _HUNDRED)
        tax_wage = gross + vwl + bav_er - tax_free
        sv_wage = gross + vwl + bav_er - sv_free
        wk = max(dec(emp.get("werbungskosten_year")), r.an_pauschbetrag)
        emp_income = _pos(tax_wage - wk)
        sv_liable = bool(_get(emp, "sv_liable", True))
        if emp.get("withheld_year") is not None:
            withheld = dec(emp.get("withheld_year"))

    # 2. Employee contributions and shared parameters
    mode = _get(health, "mode", "gkv")
    krankengeld = bool(_get(health, "krankengeld", True))
    zusatz = dec(_get(health, "zusatzbeitrag_percent", r.kv_zusatz_average))
    n_children = max(0, int(dec(_get(children, "pv_children_under_25", 0))))
    pv_total = r.pv_rate
    if not _get(children, "pv_parent", False):
        pv_total += r.pv_childless_surcharge
    if n_children >= 2:
        pv_total -= r.pv_child_reduction * (min(n_children, 5) - 1)
    ee_kv = ee_pv = ee_rv = ee_alv = ZERO
    if sv_liable:
        kv_m = min(q2(sv_wage / _TWELVE), r.bbg_kv_month)
        rv_m = min(q2(sv_wage / _TWELVE), r.bbg_rv_month)
        employer_pv = r.pv_employer_share_sn if settings.get("state") == "SN" else r.pv_employer_share
        if mode == "gkv":
            ee_kv = _TWELVE * q2(kv_m * (r.kv_rate_general / 2 + zusatz / 2) / _HUNDRED)
            ee_pv = _TWELVE * q2(kv_m * (pv_total - employer_pv) / _HUNDRED)
        ee_rv = _TWELVE * q2(rv_m * (r.rv_rate / 2) / _HUNDRED)
        ee_alv = _TWELVE * q2(rv_m * (r.alv_rate / 2) / _HUNDRED)
    er_rv = ee_rv

    # 3. Self-paid health and care
    kv = pv = subsidy = ZERO
    if mode == "pkv":
        kv = _TWELVE * dec(_get(health, "pkv_monthly", 0))
        pv = _TWELVE * dec(_get(health, "pkv_care_monthly", 0))
        subsidy = _TWELVE * dec(_get(health, "pkv_employer_subsidy_monthly", 0))
    elif mode == "gkv" and not sv_liable:
        w = tax_wage if emp is not None else ZERO
        base_m = _clamp(q2((_pos(biz) + _pos(other_income) + w) / _TWELVE), r.kv_min_base_month, r.bbg_kv_month)
        kv_rate = r.kv_rate_general if krankengeld else r.kv_rate_reduced
        kv = _TWELVE * q2(base_m * (kv_rate + zusatz) / _HUNDRED)
        pv = _TWELVE * q2(base_m * pv_total / _HUNDRED)

    # 4. Pension and unemployment (self-paid)
    pmode = _get(pension, "mode", "none")
    self_rv = ZERO
    if pmode == "statutory_income":
        rv_base = _clamp(q2(_pos(biz) / _TWELVE), r.rv_min_base_month, r.bbg_rv_month)
        self_rv = _TWELVE * q2(rv_base * r.rv_rate / _HUNDRED)
    elif pmode == "statutory_regular":
        self_rv = _TWELVE * q2(r.bezugsgroesse_month * r.rv_rate / _HUNDRED)
    elif pmode == "fixed":
        self_rv = _TWELVE * dec(_get(pension, "fixed_monthly", 0))
    umode = _get(unemployment, "mode", "none")
    alv_self = ZERO
    if umode == "voluntary":
        alv_self = _TWELVE * r.alv_voluntary_month
    elif umode == "voluntary_half":
        alv_self = _TWELVE * r.alv_voluntary_half_month

    # 5. Vorsorgeaufwendungen
    a = self_rv + ee_rv + er_rv
    h = r.vorsorge_hoechst_altersvorsorge * (2 if joint else 1)
    d_a = _pos(min(a, h) - er_rv)
    cut = ZERO
    pkv_extra = ZERO
    if mode == "pkv":
        b = _pos(_TWELVE * dec(_get(health, "pkv_basis_monthly", 0)) + pv - subsidy)
        pkv_extra = _TWELVE * (dec(_get(health, "pkv_monthly", 0)) - dec(_get(health, "pkv_basis_monthly", 0)))
    elif mode == "gkv" and sv_liable:
        cut = q2(ee_kv * r.kv_basis_cut_percent / _HUNDRED)
        b = ee_kv - cut + ee_pv
    elif mode == "gkv":
        if krankengeld:
            cut = q2(kv * r.kv_basis_cut_percent / _HUNDRED)
        b = kv - cut + pv
    else:
        b = ZERO
    o = alv_self + ee_alv + cut + pkv_extra + other_ins
    cap = r.vorsorge_cap_employee if (sv_liable or subsidy > 0) else r.vorsorge_cap_self
    d_bo = max(b, min(b + o, cap))

    # 6. Income and zvE
    sum_income = biz + emp_income + other_income
    sa = max(other_ded, r.sa_pauschbetrag * (2 if joint else 1))
    zve = _pos(sum_income - d_a - d_bo - sa)

    # 7./8. Tariff and children (Guenstigerpruefung, §31 EStG)
    n_kids = max(0, int(dec(_get(children, "count", 0))))
    f = 2 if (joint or bool(_get(children, "full_allowance", False))) else 1
    kfb = (r.kinderfreibetrag + r.bea_freibetrag) * f
    kg = _TWELVE * r.kindergeld_month * f / 2
    z = zve
    kg_added = ZERO
    kfb_used = 0
    for _ in range(n_kids):
        reduced = _pos(z - kfb)
        if est(z, year, joint) - est(reduced, year, joint) > kg:
            z = reduced
            kg_added += kg
            kfb_used += 1
    est_tarif = Decimal(est(z, year, joint))
    z_base = Decimal(est(_pos(zve - n_kids * kfb), year, joint))

    # 9. Gewerbesteuer and §35 (gewerbe), 11. GmbH
    hebesatz = dec(_get(settings, "hebesatz", 400), Decimal(400))  # null / "" / invalid: default 400
    messzahl = r.gew_messzahl / _HUNDRED
    gewst = kst = kst_soli = credit = credit_z = ZERO
    if ctype == "gewerbe":
        ertrag = _pos(_floor0(profit / _HUNDRED) * _HUNDRED - r.gew_freibetrag)
        mess = ertrag * messzahl
        gewst = q2(mess * hebesatz / _HUNDRED)
        positives = _pos(biz) + _pos(emp_income) + _pos(other_income)
        share = _pos(biz) / positives if positives > 0 else ZERO
        limit = min(r.gew_credit_factor * mess, gewst)
        credit = min(limit, q2(est_tarif * share))
        credit_z = min(limit, q2(z_base * share))
    elif ctype == "gmbh":
        kst = _trunc2(_floor0(_pos(profit)) * r.kst_rate / _HUNDRED)
        kst_soli = _trunc2(kst * r.soli_rate / _HUNDRED)
        gewst = q2(_floor0(_pos(profit) / _HUNDRED) * _HUNDRED * messzahl * hebesatz / _HUNDRED)

    # 10. Taxes
    income_est = est_tarif - credit + kg_added
    soli_base = _pos(z_base - credit_z)
    soli = _soli(soli_base, r, joint)
    church = _trunc2(z_base * church_pct / _HUNDRED)
    income_taxes = income_est + soli + church

    # 12. Result
    social = kv + pv + self_rv + alv_self + ee_kv + ee_pv + ee_rv + ee_alv - subsidy
    tax_due = income_taxes + gewst + kst + kst_soli
    total = tax_due + social
    return dict(
        profit=profit,
        social=social,
        est=income_est,
        soli=soli,
        church=church,
        gewst=gewst,
        kst=kst,
        gewst_credit=credit,
        income_tax_total=income_taxes,
        total=total,
        rate=tax_due / profit if profit > 0 else ZERO,
        zve=zve,
        kst_soli=kst_soli,
        tax_due=tax_due,
        withheld=withheld if withheld is not None else ZERO,
        est_tarif=est_tarif,
        kg_added=kg_added,
        kfb_used=kfb_used,
        zuschlag_base=z_base,
        soli_base=soli_base,
        vorsorge=dict(A=a, H=h, dA=d_a, B=b, O=o, C=cap, dBO=d_bo),
        insurance=dict(
            kv=kv,
            pv=pv,
            rv=self_rv,
            alv=alv_self,
            ee_kv=ee_kv,
            ee_pv=ee_pv,
            ee_rv=ee_rv,
            ee_alv=ee_alv,
            subsidy=subsidy,
        ),
        emp_income=emp_income,
        tax_wage=tax_wage,
        sv_wage=sv_wage,
        balance=income_taxes - withheld if withheld is not None else None,
        warnings=warnings,
    )
