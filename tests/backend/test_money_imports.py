"""FX-B2: the money of the import report (``rows[].totals`` net, vat, gross) is a decimal string in
``POST /import/preview`` and ``POST /import/run`` (docs/specs/decimal-money-api.md, "Dict endpoints"). Fake data only."""

import re

DECIMAL = re.compile(r"^-?[0-9]+\.[0-9]{2,6}$")

INVOICE_CSV = (
    "Rechnungsnummer;Rechnungsdatum;Kunde;Ust-IdNr;Netto;USt;Brutto;Fällig;bezahlt am;Status\n"
    "RE-2025-0001;03.04.2025;Mueller GmbH;DE 123 456 789;1.000,00;190,00;1.190,00;03.05.2025;10.04.2025;bezahlt\n"
    "RE-2025-0002;05.04.2025;Mueller GmbH;DE123456789;100,50;19,10;119,60;;;offen\n"
)
EXPENSE_CSV = "Datum;Lieferant;Netto;USt;Brutto\n03.04.2025;Demo Shop;50,00;9,50;59,50\n"
INVOICE_TOTALS = [
    {"net": "1000.00", "vat": "190.00", "gross": "1190.00"},
    {"net": "100.50", "vat": "19.10", "gross": "119.60"},
]


def _post(client, route, name, body, kind):
    return client.post(f"/api/import/{route}", data={"type": kind}, files={"file": (name, body.encode(), "text/csv")})


def _check(report, expected):
    assert [r["totals"] for r in report["rows"]] == expected
    for r in report["rows"]:
        for k in ("net", "vat", "gross"):
            assert isinstance(r["totals"][k], str) and DECIMAL.match(r["totals"][k])


def test_preview_totals_are_decimal_strings(boss_client):
    r = _post(boss_client, "preview", "inv.csv", INVOICE_CSV, "invoices")
    assert r.status_code == 200, r.text
    _check(r.json(), INVOICE_TOTALS)


def test_run_totals_are_decimal_strings(boss_client):
    r = _post(boss_client, "run", "inv.csv", INVOICE_CSV, "invoices")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["applied"] is True
    _check(body, INVOICE_TOTALS)


def test_expense_rows_have_no_totals_block(boss_client):
    r = _post(boss_client, "preview", "exp.csv", EXPENSE_CSV, "expenses")
    assert r.status_code == 200, r.text
    assert [x["totals"] for x in r.json()["rows"]] == [None]


def test_rejected_run_report_totals_are_decimal_strings(boss_client):
    bad = INVOICE_CSV + "RE-2025-0003;07.04.2025;Mueller GmbH;;100,00;19,00;999,00;;;offen\n"
    r = _post(boss_client, "run", "inv.csv", bad, "invoices")
    assert r.status_code == 422, r.text
    report = r.json()["detail"]
    assert report["applied"] is False
    assert report["rows"][0]["totals"] == INVOICE_TOTALS[0]
