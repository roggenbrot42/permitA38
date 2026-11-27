"""The shipped SKR03 / SKR04 chart files for 2026 against the table of docs/specs/charts-2026.md (sections 3-5).

The oracle is written from the spec as literals, not generated from the files. Account names are checked by the
reviewer against the DATEV PDFs, which are not in the repository.
"""

import json
import re
from pathlib import Path

import pytest

from permita38.core import charts
from permita38.core.charts import ENGINE_ROLES, chart_for, load_chart

# (role, skr03, skr04, automatic)
ROLES: list[tuple[str, str, str, str | None]] = [
    ("bank", "1200", "1800", None),
    ("cash", "1000", "1600", None),
    ("receivables", "1410", "1210", None),
    ("payables", "1610", "3310", None),
    ("customer_credit", "1700", "3500", None),
    ("payables_employee", "1740", "3720", None),
    ("revenue_s19", "8400", "4400", "S19"),
    ("revenue_s7", "8300", "4300", "S7"),
    ("revenue_sx", "8200", "4200", None),
    ("revenue_s0", "8290", "4290", None),
    ("revenue_ku", "8192", "4184", "KU"),
    ("revenue_eu_svc", "8336", "4336", "EU_SVC"),
    ("vat_out_s19", "1776", "3806", None),
    ("vat_out_s7", "1771", "3801", None),
    ("vat_out_sx", "1770", "3800", None),
    ("vat_not_due_s19", "1766", "3816", None),
    ("vat_not_due_s7", "1761", "3811", None),
    ("vat_not_due_sx", "1760", "3810", None),
    ("vat_soll_clearing_s19", "1371", "1486", None),
    ("vat_soll_clearing_s7", "1371", "1486", None),
    ("vat_soll_clearing_sx", "1371", "1486", None),
    ("input_vat_s19", "1576", "1406", None),
    ("input_vat_s7", "1571", "1401", None),
    ("input_vat_sx", "1570", "1400", None),
    ("rc_vat_out_eu", "1787", "3837", None),
    ("rc_vat_out_3c", "1787", "3837", None),
    ("rc_input_vat", "1577", "1407", None),
    ("vat_prepayments", "1780", "3820", None),
    ("vat_special_prepayment", "1781", "3830", None),
    ("vat_previous_year", "1790", "3841", None),
    ("input_vat_clearing", "1580", "1480", None),
    ("rc_vat_nondeductible", "1692", "3612", None),
    ("vat_paid_private", "1693", "3613", None),
    ("datev_transfer", "1792", "3630", None),
    ("expense_software", "4806", "6495", None),
    ("expense_hardware", "4855", "6260", None),
    ("expense_office", "4210", "6310", None),
    ("travel_costs", "4673", "6673", None),
    ("expense_lodging", "4676", "6680", None),
    ("expense_material", "3000", "5100", None),
    ("meals_deductible", "4650", "6640", None),
    ("meals_nondeductible", "4654", "6644", None),
    ("expense_gifts", "4630", "6610", None),
    ("expense_marketing", "4600", "6600", None),
    ("expense_insurance", "4360", "6400", None),
    ("expense_phone", "4920", "6805", None),
    ("expense_training", "4945", "6821", None),
    ("expense_subcontractors", "3100", "5900", None),
    ("expense_fees", "4970", "6855", None),
    ("expense_other", "4900", "6300", None),
    ("bad_debts", "2400", "6930", None),
    ("cash_discount_granted", "8700", "4700", None),
    ("trade_tax_expense", "4320", "7610", None),
    ("trade_tax_provision", "0956", "3035", None),
    ("corporate_tax_expense", "2200", "7600", None),
    ("corporate_tax_provision", "0963", "3040", None),
    ("tax_refunds", "2281", "7641", None),
    ("private_withdrawal", "1800", "2100", None),
    ("private_deposit", "1890", "2180", None),
    ("equity", "0880", "2010", None),
    ("profit_carryforward", "0880", "2010", None),
    ("opening_balance", "9000", "9000", None),
    ("closing_balance", "9000", "9000", None),
]

# Printed names, literal from spec section 3 (SKR03 name, SKR04 name)
NAMES: dict[str, tuple[str, str]] = {  # role -> (SKR03 name, SKR04 name)
    "bank": ("Bank", "Bank"),
    "cash": ("Kasse", "Kasse"),
    "receivables": ("Forderungen aus Lieferungen und Leistungen ohne Kontokorrent", "Forderungen aus Lieferungen und Leistungen ohne Kontokorrent"),
    "payables": ("Verbindlichkeiten aus Lieferungen und Leistungen ohne Kontokorrent", "Verbindlichkeiten aus Lieferungen und Leistungen ohne Kontokorrent"),
    "customer_credit": ("Sonstige Verbindlichkeiten", "Sonstige Verbindlichkeiten"),
    "payables_employee": ("Verbindlichkeiten aus Lohn und Gehalt", "Verbindlichkeiten aus Lohn und Gehalt"),
    "revenue_s19": ("Erlöse 19 % USt", "Erlöse 19 % USt"),
    "revenue_s7": ("Erlöse 7 % USt", "Erlöse 7 % USt"),
    "revenue_sx": ("Erlöse", "Erlöse"),
    "revenue_s0": ("Erlöse 0 % USt", "Erlöse 0 % USt"),
    "revenue_ku": ("Steuerfreie Erlöse Kleinunternehmer nach § 19 Abs. 1 UStG", "Steuerfreie Erlöse Kleinunternehmer nach § 19 Abs. 1 UStG"),
    "revenue_eu_svc": ("Erlöse aus im anderen EU-Land steuerpflichtigen sonstigen Leistungen, für die der Leistungsempfänger die Umsatzsteuer schuldet", "Erlöse aus im anderen EU-Land steuerpflichtigen sonstigen Leistungen, für die der Leistungsempfänger die Umsatzsteuer schuldet"),
    "vat_out_s19": ("Umsatzsteuer 19 %", "Umsatzsteuer 19 %"),
    "vat_out_s7": ("Umsatzsteuer 7 %", "Umsatzsteuer 7 %"),
    "vat_out_sx": ("Umsatzsteuer", "Umsatzsteuer"),
    "vat_not_due_s19": ("Umsatzsteuer nicht fällig 19 %", "Umsatzsteuer nicht fällig 19 %"),
    "vat_not_due_s7": ("Umsatzsteuer nicht fällig 7 %", "Umsatzsteuer nicht fällig 7 %"),
    "vat_not_due_sx": ("Umsatzsteuer nicht fällig", "Umsatzsteuer nicht fällig"),
    "vat_soll_clearing_s19": ("Verrechnungskonto Gewinnermittlung § 4 Abs. 3 EStG, nicht ergebniswirksam", "Verrechnungskonto für Gewinnermittlung § 4 Abs. 3 EStG, nicht ergebniswirksam"),
    "vat_soll_clearing_s7": ("Verrechnungskonto Gewinnermittlung § 4 Abs. 3 EStG, nicht ergebniswirksam", "Verrechnungskonto für Gewinnermittlung § 4 Abs. 3 EStG, nicht ergebniswirksam"),
    "vat_soll_clearing_sx": ("Verrechnungskonto Gewinnermittlung § 4 Abs. 3 EStG, nicht ergebniswirksam", "Verrechnungskonto für Gewinnermittlung § 4 Abs. 3 EStG, nicht ergebniswirksam"),
    "input_vat_s19": ("Abziehbare Vorsteuer 19 %", "Abziehbare Vorsteuer 19 %"),
    "input_vat_s7": ("Abziehbare Vorsteuer 7 %", "Abziehbare Vorsteuer 7 %"),
    "input_vat_sx": ("Abziehbare Vorsteuer", "Abziehbare Vorsteuer"),
    "rc_vat_out_eu": ("Umsatzsteuer nach § 13b UStG 19 %", "Umsatzsteuer nach § 13b UStG 19 %"),
    "rc_vat_out_3c": ("Umsatzsteuer nach § 13b UStG 19 %", "Umsatzsteuer nach § 13b UStG 19 %"),
    "rc_input_vat": ("Abziehbare Vorsteuer nach § 13b UStG 19 %", "Abziehbare Vorsteuer nach § 13b UStG 19 %"),
    "vat_prepayments": ("Umsatzsteuer-Vorauszahlungen", "Umsatzsteuer-Vorauszahlungen"),
    "vat_special_prepayment": ("Umsatzsteuer-Vorauszahlungen 1/11", "Umsatzsteuer-Vorauszahlungen 1/11"),
    "vat_previous_year": ("Umsatzsteuerverbindlichkeiten Vorjahr", "Umsatzsteuerverbindlichkeiten Vorjahr"),
    "input_vat_clearing": ("Gegenkonto Vorsteuer § 4 Abs. 3 EStG", "Gegenkonto Vorsteuer § 4 Abs. 3 EStG"),
    "rc_vat_nondeductible": ("Neutralisierung aufwandswirksamer Sachverhalte für § 4 Abs. 3 EStG", "Neutralisierung aufwandswirksamer Sachverhalte für § 4 Abs. 3 EStG"),
    "vat_paid_private": ("Ergebnisneutrale Sachverhalte für § 4 Abs. 3 EStG", "Ergebnisneutrale Sachverhalte für § 4 Abs. 3 EStG"),
    "datev_transfer": ("Sonstige Verrechnungskonten (Interimskonten)", "Sonstige Verrechnungskonten (Interimskonto)"),
    "expense_software": ("Wartungskosten für Hard- und Software", "Wartungskosten für Hard- und Software"),
    "expense_hardware": ("Sofortabschreibung geringwertiger Wirtschaftsgüter", "Sofortabschreibungen geringwertiger Wirtschaftsgüter"),
    "expense_office": ("Miete (unbewegliche Wirtschaftsgüter)", "Miete (unbewegliche Wirtschaftsgüter)"),
    "travel_costs": ("Reisekosten Unternehmer Fahrtkosten", "Reisekosten Unternehmer Fahrtkosten"),
    "expense_lodging": ("Reisekosten Unternehmer Übernachtungsaufwand und Reisenebenkosten", "Reisekosten Unternehmer Übernachtungsaufwand und Reisenebenkosten"),
    "expense_material": ("Einkauf Roh-, Hilfs- und Betriebsstoffe", "Einkauf Roh-, Hilfs- und Betriebsstoffe"),
    "meals_deductible": ("Bewirtungskosten", "Bewirtungskosten"),
    "meals_nondeductible": ("Nicht abzugsfähige Bewirtungskosten", "Nicht abzugsfähige Bewirtungskosten"),
    "expense_gifts": ("Geschenke abzugsfähig ohne § 37b EStG", "Geschenke abzugsfähig ohne § 37b EStG"),
    "expense_marketing": ("Werbekosten", "Werbekosten"),
    "expense_insurance": ("Versicherungen", "Versicherungen"),
    "expense_phone": ("Telefon", "Telefon"),
    "expense_training": ("Fortbildungskosten", "Fortbildungskosten"),
    "expense_subcontractors": ("Fremdleistungen", "Fremdleistungen"),
    "expense_fees": ("Nebenkosten des Geldverkehrs", "Nebenkosten des Geldverkehrs"),
    "expense_other": ("Sonstige betriebliche Aufwendungen", "Sonstige betriebliche Aufwendungen"),
    "bad_debts": ("Forderungsverluste (übliche Höhe)", "Forderungsverluste (übliche Höhe)"),
    "cash_discount_granted": ("Erlösschmälerungen", "Erlösschmälerungen"),
    "trade_tax_expense": ("Gewerbesteuer", "Gewerbesteuer"),
    "trade_tax_provision": ("Gewerbesteuerrückstellung nach § 4 Abs. 5b EStG", "Gewerbesteuerrückstellung nach § 4 Abs. 5b EStG"),
    "corporate_tax_expense": ("Körperschaftsteuer", "Körperschaftsteuer"),
    "corporate_tax_provision": ("Körperschaftsteuerrückstellung", "Körperschaftsteuerrückstellung"),
    "tax_refunds": ("Gewerbesteuernachzahlungen und Gewerbesteuererstattungen für Vorjahre nach § 4 Abs. 5b EStG", "Gewerbesteuernachzahlungen und Gewerbesteuererstattungen für Vorjahre nach § 4 Abs. 5b EStG"),
    "private_withdrawal": ("Privatentnahmen allgemein", "Privatentnahmen allgemein"),
    "private_deposit": ("Privateinlagen", "Privateinlagen"),
    "equity": ("Variables Kapital", "Variables Kapital"),
    "profit_carryforward": ("Variables Kapital", "Variables Kapital"),
    "opening_balance": ("Saldenvorträge, Sachkonten", "Saldenvorträge, Sachkonten"),
    "closing_balance": ("Saldenvorträge, Sachkonten", "Saldenvorträge, Sachkonten"),
}

PL_ROLES = {
    "revenue_s19", "revenue_s7", "revenue_sx", "revenue_s0", "revenue_ku", "revenue_eu_svc",
    "expense_software", "expense_hardware", "expense_office", "travel_costs", "expense_lodging", "expense_material",
    "meals_deductible", "meals_nondeductible", "expense_gifts", "expense_marketing", "expense_insurance",
    "expense_phone", "expense_training", "expense_subcontractors", "expense_fees", "expense_other", "bad_debts",
    "cash_discount_granted", "trade_tax_expense", "corporate_tax_expense", "tax_refunds",
}  # fmt: skip

CATEGORIES = {
    "Software & subscriptions": "expense_software",
    "Hardware & equipment": "expense_hardware",
    "Office & coworking": "expense_office",
    "Travel": "travel_costs",
    "Transportation": "travel_costs",
    "Accommodation": "expense_lodging",
    "Material": "expense_material",
    "Business meals": "meals_deductible",
    "Gifts": "expense_gifts",
    "Marketing": "expense_marketing",
    "Insurance": "expense_insurance",
    "Phone & internet": "expense_phone",
    "Training": "expense_training",
    "Subcontractors": "expense_subcontractors",
    "Fees & bank": "expense_fees",
    "Other": "expense_other",
}

_E61 = "Z. 61 Übrige unbeschränkt abziehbare Betriebsausgaben"
_E15 = "Z. 15 Umsatzsteuerpflichtige Betriebseinnahmen"
_E16 = "Z. 16 Umsatzsteuerfreie, nicht umsatzsteuerbare Betriebseinnahmen, 0 %, § 13b UStG"
_E68 = "Z. 68 Gewerbesteuer (nicht abziehbar)"
# role -> (line, deductible); every P&L role except corporate_tax_expense (None)
EUR: dict[str, tuple[str, bool]] = {
    "revenue_s19": (_E15, True),
    "revenue_s7": (_E15, True),
    "revenue_sx": (_E15, True),
    "cash_discount_granted": (_E15, True),
    "revenue_s0": (_E16, True),
    "revenue_eu_svc": (_E16, True),
    "revenue_ku": ("Z. 12 Betriebseinnahmen als Kleinunternehmer", True),
    "expense_material": ("Z. 29 Waren, Rohstoffe und Hilfsstoffe", True),
    "expense_subcontractors": ("Z. 30 Bezogene Fremdleistungen", True),
    "expense_hardware": ("Z. 37 Aufwendungen für geringwertige Wirtschaftsgüter", True),
    "expense_office": ("Z. 40 Miete/Pacht für Geschäftsräume", True),
    "expense_phone": ("Z. 44 Aufwendungen für Telekommunikation", True),
    "expense_lodging": ("Z. 45 Übernachtungs- und Reisenebenkosten bei Geschäftsreisen", True),
    "expense_training": ("Z. 46 Fortbildungskosten", True),
    "expense_insurance": ("Z. 50 Beiträge, Gebühren, Abgaben und Versicherungen", True),
    "expense_software": ("Z. 51 Laufende EDV-Kosten", True),
    "expense_marketing": ("Z. 55 Werbekosten", True),
    "expense_fees": (_E61, True),
    "expense_other": (_E61, True),
    "bad_debts": (_E61, True),
    "expense_gifts": ("Z. 63 Geschenke (abziehbar)", True),
    "meals_deductible": ("Z. 64 Bewirtungsaufwendungen (abziehbar)", True),
    "meals_nondeductible": ("Z. 64 Bewirtungsaufwendungen (nicht abziehbar)", False),
    "travel_costs": ("Z. 71 Sonstige tatsächliche Fahrtkosten", True),
    "trade_tax_expense": (_E68, False),
    "tax_refunds": (_E68, False),
}

SHARED = [
    ["vat_soll_clearing_s19", "vat_soll_clearing_s7", "vat_soll_clearing_sx"],
    ["rc_vat_out_eu", "rc_vat_out_3c"],
    ["equity", "profit_carryforward"],
    ["opening_balance", "closing_balance"],
]

SOURCES = {
    "skr03": "DATEV Kontenrahmen SKR 03 (Art.-Nr. 11174), gültig für 2026; role mapping: architect, to be confirmed by the tax adviser",
    "skr04": "DATEV Kontenrahmen SKR 04 (Art.-Nr. 11175), gültig für 2026; role mapping: architect, to be confirmed by the tax adviser",
}  # fmt: skip

CHARTS = ("skr03", "skr04")


def _path(chart: str) -> Path:
    return Path(charts.__file__).parent / f"{chart}-2026.json"


def _no(chart: str, row: tuple[str, str, str, str | None]) -> str:
    return row[1] if chart == "skr03" else row[2]


@pytest.mark.parametrize("chart", CHARTS)
def test_file_loads_through_chart_for_without_warning(chart):
    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    assert (c.chart, c.year, c.requested_year, c.warning) == (chart, 2026, 2026, None)
    assert c.status == "to_verify"
    assert c.source == SOURCES[chart]
    assert load_chart(_path(chart)).roles == c.roles


@pytest.mark.parametrize("chart", CHARTS)
def test_oracle_covers_every_engine_role_once(chart):
    names = [r[0] for r in ROLES]
    assert len(names) == len(set(names)) == len(chart_for(chart, 2026).roles)  # type: ignore[arg-type]
    assert set(ENGINE_ROLES) <= set(names)


@pytest.mark.parametrize("chart", CHARTS)
def test_role_accounts_and_automatic_match_the_spec_table(chart):
    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    got = {role: (acc.account, acc.automatic) for role, acc in c.roles}
    assert got == {row[0]: (_no(chart, row), row[3]) for row in ROLES}


@pytest.mark.parametrize("chart", CHARTS)
def test_class_and_eur_lines_match_the_spec(chart):
    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    for role, acc in c.roles:
        assert acc.account_class == ("pl" if role in PL_ROLES else "balance"), role
        if role in EUR:
            assert acc.eur_line is not None and (acc.eur_line.line, acc.eur_line.deductible) == EUR[role], role
        else:
            assert acc.eur_line is None, role
    assert set(EUR) | {"corporate_tax_expense"} == PL_ROLES


@pytest.mark.parametrize("chart", CHARTS)
def test_categories_match_the_spec(chart):
    assert dict(chart_for(chart, 2026).categories) == CATEGORIES  # type: ignore[arg-type]


@pytest.mark.parametrize("chart", CHARTS)
def test_shared_groups_match_the_spec(chart):
    raw = json.loads(_path(chart).read_text(encoding="utf-8"))
    assert raw["shared"] == SHARED


@pytest.mark.parametrize("chart", CHARTS)
def test_accounts_are_exactly_the_role_accounts_sorted_without_duplicates(chart):
    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    listed = [a.account for a in c.accounts]
    assert listed == sorted(set(_no(chart, row) for row in ROLES))
    assert all(re.fullmatch(r"[0-9]{4}", a) for a in listed)
    by_account = {a.account: a for a in c.accounts}
    for _, acc in c.roles:
        assert by_account[acc.account] == acc


@pytest.mark.parametrize("chart", CHARTS)
def test_account_names_are_clean(chart):
    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    for a in c.accounts:
        assert a.name == a.name.strip() and a.name
        assert not re.search(r"-$|\s\d+\)$|\s{2,}", a.name), a.name  # trailing hyphen, footnote marker, spaces


@pytest.mark.parametrize("chart", CHARTS)
def test_automatic_accounts_carry_only_matching_keys(chart):
    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    assert {a.account: a.automatic for a in c.accounts if a.automatic} == {_no(chart, r): r[3] for r in ROLES if r[3]}


@pytest.mark.parametrize("chart", CHARTS)
def test_role_names_match_the_spec_exactly(chart):
    idx = 0 if chart == "skr03" else 1
    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    assert {role: acc.name for role, acc in c.roles} == {role: names[idx] for role, names in NAMES.items()}


@pytest.mark.parametrize("chart", CHARTS)
def test_categories_equal_the_web_expense_categories(chart):
    js = (Path(__file__).resolve().parents[2] / "web" / "app2.js").read_text(encoding="utf-8")
    m = re.search(r"^const EXP_CATS = \[(.*?)\];", js, re.M)
    assert m
    exp_cats = re.findall(r"'([^']*)'", m.group(1))
    assert exp_cats
    assert [k for k, _ in chart_for(chart, 2026).categories] == exp_cats  # type: ignore[arg-type]


@pytest.mark.parametrize("chart", CHARTS)
def test_real_chart_datev_simulation_equals_the_journal(chart):
    from permita38.core.accounting.datev import batch_rows, entry_rows, journal_balances, simulate
    from permita38.core.datev import datev_rules_for
    from tests.backend import test_datev_rows as t

    c = chart_for(chart, 2026)  # type: ignore[arg-type]
    rules = datev_rules_for(2026)
    cases = t._journal_cases()
    assert len(cases) >= 30
    transfer: set[str] = set()
    for case in cases:
        entries = t.oracle_entries(case)
        rows = batch_rows(entries, c, rules, t.RATES)
        assert simulate(rows, c, rules, t.RATES) == journal_balances(entries, c), case["id"]
        for e in entries:
            if any(r.note == "transfer" for r in entry_rows(e, c, rules, t.RATES)):
                transfer.add(e.event_key)
    assert transfer == {"A18-credit-moves-cash-eur-soll:2"}
