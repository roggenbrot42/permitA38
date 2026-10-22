"""Rough German tax estimates for freelancers and small businesses.

These are ESTIMATES for planning how much to set aside. They are not tax
advice and do not replace a Steuerberater. Rates live in TARIFFS / constants
so they are easy to update each year.
"""

import math

# Einkommensteuer tariff §32a EStG: (grundfreibetrag, zone2_end, zone3_end, a2, a3, c3, k4, k5)
TARIFFS = {
    2025: dict(gf=12096, z2=17443, z3=68480, a2=932.30, a3=176.64, c3=1015.13, k4=10911.92, k5=19246.67),
    2026: dict(gf=12348, z2=17799, z3=69878, a2=914.51, a3=173.10, c3=1034.87, k4=11135.63, k5=19470.38),
}
SOLI_FREIGRENZE = {2025: 19950, 2026: 20350}  # single assessment
KLEIN_LIMIT_PREV, KLEIN_LIMIT_CUR = 25000, 100000


def _tariff(year):
    return TARIFFS.get(year) or TARIFFS[max(TARIFFS)]


def est_single(zve, year):
    t = _tariff(year)
    x = math.floor(max(zve, 0))
    if x <= t["gf"]:
        return 0
    if x <= t["z2"]:
        y = (x - t["gf"]) / 10000
        return math.floor((t["a2"] * y + 1400) * y)
    if x <= t["z3"]:
        z = (x - t["z2"]) / 10000
        return math.floor((t["a3"] * z + 2397) * z + t["c3"])
    if x <= 277825:
        return math.floor(0.42 * x - t["k4"])
    return math.floor(0.45 * x - t["k5"])


def est(zve, year, splitting=False):
    if splitting:
        return 2 * est_single(zve / 2, year)
    return est_single(zve, year)


def soli(est_amount, year, splitting=False):
    free = SOLI_FREIGRENZE.get(year, 20350) * (2 if splitting else 1)
    if est_amount <= free:
        return 0.0
    return min(0.055 * est_amount, 0.119 * (est_amount - free))


def gewerbesteuer(profit, hebesatz, freibetrag):
    base = max(math.floor(profit / 100) * 100 - freibetrag, 0)
    messbetrag = base * 0.035
    return messbetrag, messbetrag * hebesatz / 100


def estimate(profit, year, s):
    """Annual tax estimate for a given annual profit (Gewinn).

    s = settings dict. Returns a breakdown incl. total tax and effective rate.
    """
    ctype = s.get("company_type", "freiberufler")
    hebesatz = float(s.get("hebesatz") or 400)
    social = float(s.get("social_monthly") or 0) * 12
    other_ded = float(s.get("other_deductions") or 0)
    other_inc = float(s.get("other_income") or 0)
    church = float(s.get("church_tax") or 0) / 100
    splitting = bool(s.get("splitting"))
    profit = max(profit, 0.0)
    out = dict(
        profit=profit,
        social=social,
        est=0,
        soli=0,
        church=0,
        gewst=0,
        kst=0,
        gewst_credit=0,
        income_tax_total=0,
        total=0,
        rate=0,
        zve=0,
    )

    if ctype == "gmbh":
        out["kst"] = round(profit * 0.15, 2)
        out["soli"] = round(out["kst"] * 0.055, 2)
        _, out["gewst"] = gewerbesteuer(profit, hebesatz, 0)
        out["total"] = round(out["kst"] + out["soli"] + out["gewst"], 2)
        out["social"] = 0
    else:
        freib = 24500 if ctype == "gewerbe" else 0
        base_zve = max(other_inc - other_ded, 0)
        zve = max(profit + other_inc - social - other_ded, 0)
        out["zve"] = zve
        e_with, e_without = est(zve, year, splitting), est(base_zve, year, splitting)
        e = e_with - e_without  # attributable to the business income
        if ctype == "gewerbe":
            mess, gew = gewerbesteuer(profit, hebesatz, freib)
            credit = min(4.0 * mess, e)  # §35 EStG
            out["gewst"], out["gewst_credit"] = round(gew, 2), round(credit, 2)
            e -= credit
            e_with -= credit
        out["est"] = max(e, 0)
        out["soli"] = round(soli(e_with, year, splitting) - soli(e_without, year, splitting), 2)
        out["church"] = round(max(e, 0) * church, 2)
        out["total"] = round(out["est"] + out["soli"] + out["church"] + out["gewst"], 2)
    out["rate"] = out["total"] / profit if profit > 0 else 0
    return out


def rules_metadata() -> dict:
    """Validity/review metadata of the tables above (``core/taxrules/data/income_tax.json``).

    The numbers stay in this module; the data file records which years they cover, when a maintainer last reviewed them
    and which of them are still unverified, so they take part in the quarterly review and the staleness check.
    """
    from .taxrules import load

    return load("income_tax").data
