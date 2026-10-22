"""Render offers and invoices to PDF using a template (DIN 5008-style letter)."""

import json
import os
from datetime import datetime

from . import calc
from .pdf import MM, Pdf

HERE = os.path.dirname(__file__)

LABELS = {
    "de": dict(
        offer="Angebot",
        invoice="Rechnung",
        number="Nummer",
        date="Datum",
        customer_no="Kundennr.",
        valid="Gültig bis",
        due="Zahlbar bis",
        period="Leistungszeitraum",
        pos="Pos.",
        desc="Beschreibung",
        qty="Menge",
        unit="Einheit",
        price="Einzelpreis",
        amount="Betrag",
        net="Nettobetrag",
        vat="USt. %s %%",
        total="Gesamtbetrag",
        page="Seite",
        klein="Gemäß §19 UStG wird keine Umsatzsteuer berechnet.",
        taxno="Steuernr.",
        vatid="USt-IdNr.",
        bank="Bankverbindung",
        ref="Ihr Zeichen",
        draft="ENTWURF",
        dear="Sehr geehrte Damen und Herren,",
        contact="Ansprechpartner",
    ),
    "en": dict(
        offer="Offer",
        invoice="Invoice",
        number="Number",
        date="Date",
        customer_no="Customer no.",
        valid="Valid until",
        due="Due date",
        period="Service period",
        pos="Pos.",
        desc="Description",
        qty="Qty",
        unit="Unit",
        price="Unit price",
        amount="Amount",
        net="Net total",
        vat="VAT %s %%",
        total="Total",
        page="Page",
        klein="No VAT is charged according to §19 UStG (small business regulation).",
        taxno="Tax no.",
        vatid="VAT ID",
        bank="Bank details",
        ref="Your reference",
        draft="DRAFT",
        dear="Dear Sir or Madam,",
        contact="Contact",
    ),
}


def builtin_templates():
    out = {}
    d = os.path.join(HERE, "templates")
    for n in sorted(os.listdir(d)):
        if n.endswith(".json"):
            with open(os.path.join(d, n), encoding="utf-8") as f:
                t = json.load(f)
            out[t["id"]] = t
    return out


def get_template(store, tid):
    t = store.get("templates", tid) if tid and "_" not in tid else None
    return t or builtin_templates().get(tid) or builtin_templates()["classic"]


def money(x, lang="de"):
    s = "{:,.2f}".format(x)
    if lang == "de":
        s = s.replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " €"


def fdate(s, lang="de"):
    if not s:
        return ""
    dt = datetime.strptime(s, "%Y-%m-%d")
    return dt.strftime("%d.%m.%Y" if lang == "de" else "%Y-%m-%d")


def qty_fmt(q, lang):
    s = ("%.2f" % float(q)).rstrip("0").rstrip(".")
    return s.replace(".", ",") if lang == "de" else s


def render(store, kind, doc):
    """kind: 'offer' | 'invoice'. Returns PDF bytes."""
    s = store.settings()
    tpl = get_template(store, doc.get("template_id") or s.get("default_template") or "classic")
    lang = doc.get("language") or tpl.get("language") or "de"
    L = LABELS[lang]
    cust = store.get("customers", doc.get("customer_id")) or {}
    totals = calc.doc_totals(doc, s)
    accent = tpl.get("accent", "#1f4e79")
    layout = tpl.get("layout", "classic")
    pdf = Pdf()
    left, right = 20 * MM, Pdf.W - 20 * MM
    ctx = dict(
        customer_name=cust.get("name", ""),
        contact=cust.get("contact", ""),
        number=doc.get("number") or "",
        date=fdate(doc.get("date"), lang),
        valid_until=fdate(doc.get("valid_until"), lang),
        due_date=fdate(doc.get("due_date"), lang),
        company=s.get("company_name", ""),
        owner=s.get("owner_name", ""),
    )

    def fill(t):
        for k, v in ctx.items():
            t = (t or "").replace("{%s}" % k, str(v))
        return t

    def header(first):
        if layout == "modern":
            pdf.rect(0, 0, Pdf.W, 14 * MM, accent)
            pdf.text(left, 9 * MM, s.get("company_name", ""), 12, True, "#ffffff")
        elif layout == "classic":
            pdf.rect(0, 0, Pdf.W, 4 * MM, accent)
        logo = s.get("logo")
        if first and logo and tpl.get("show_logo", True):
            p = store.attachment_path(logo)
            if p:
                with open(p, "rb") as f:
                    pdf.jpeg(f.read(), right - 45 * MM, 18 * MM, 45 * MM, 18 * MM)
        if first and layout != "modern":
            pdf.text(left, 17 * MM, s.get("company_name", ""), 14, True, accent if layout == "classic" else "#000000")

    def footer(n):
        y = Pdf.H - 17 * MM
        pdf.line(left, y - 12, right, y - 12, "#bbbbbb", 0.4)
        c1 = [
            x
            for x in (s.get("company_name"), s.get("street"), (s.get("zip", "") + " " + s.get("city", "")).strip())
            if x
        ]
        c2 = [x for x in (s.get("email"), s.get("phone"), s.get("website")) if x]
        c3 = []
        if s.get("tax_number"):
            c3.append("%s %s" % (L["taxno"], s["tax_number"]))
        if s.get("vat_id"):
            c3.append("%s %s" % (L["vatid"], s["vat_id"]))
        c4 = [
            x
            for x in (
                s.get("bank_name"),
                "IBAN " + s["iban"] if s.get("iban") else "",
                "BIC " + s["bic"] if s.get("bic") else "",
            )
            if x
        ]
        for i, col in enumerate((c1, c2, c3, c4)):
            for j, line in enumerate(col[:3]):
                pdf.text(left + i * 43 * MM, y + j * 8, line, 7, False, "#555555")
        pdf.text(right, Pdf.H - 8 * MM, "%s %d" % (L["page"], n), 7, False, "#555555", "r")

    header(True)
    page_no = 1
    # sender line + address window (DIN 5008 form B: 45 mm from top)
    sender = " · ".join(
        x for x in (s.get("company_name"), s.get("street"), (s.get("zip", "") + " " + s.get("city", "")).strip()) if x
    )
    pdf.text(left, 47 * MM, sender, 6.5, False, "#666666")
    pdf.line(left, 48 * MM, left + 85 * MM, 48 * MM, "#dddddd", 0.3)
    addr = [cust.get("name", "")]
    if cust.get("contact"):
        addr.append(cust["contact"])
    addr += [
        x
        for x in (
            cust.get("street"),
            (cust.get("zip", "") + " " + cust.get("city", "")).strip(),
            cust.get("country") if cust.get("country") not in (None, "", "Germany", "Deutschland") else "",
        )
        if x
    ]
    for i, ln in enumerate(addr[:6]):
        pdf.text(left, 55 * MM + i * 12, ln, 10)
    # info block
    info = [(L["number"], doc.get("number") or "—"), (L["date"], fdate(doc.get("date"), lang))]
    if cust.get("customer_no"):
        info.append((L["customer_no"], cust["customer_no"]))
    if kind == "offer" and doc.get("valid_until"):
        info.append((L["valid"], fdate(doc["valid_until"], lang)))
    if kind == "invoice" and doc.get("due_date"):
        info.append((L["due"], fdate(doc["due_date"], lang)))
    if doc.get("service_from"):
        per = fdate(doc["service_from"], lang)
        if doc.get("service_to") and doc["service_to"] != doc["service_from"]:
            per += " – " + fdate(doc["service_to"], lang)
        info.append((L["period"], per))
    if doc.get("reference"):
        info.append((L["ref"], doc["reference"]))
    for i, (k, v) in enumerate(info):
        pdf.text(right - 60 * MM, 52 * MM + i * 12, k + ":", 8.5, False, "#555555")
        pdf.text(right, 52 * MM + i * 12, v, 8.5, True, "#000000", "r")

    y = 98 * MM
    title = "%s %s" % (L[kind], doc.get("number") or "")
    if not doc.get("number"):
        title = "%s (%s)" % (L[kind], L["draft"])
    pdf.text(left, y, title.strip(), 16, True, accent if layout != "minimal" else "#000000")
    if doc.get("title"):
        y += 16
        pdf.text(left, y, doc["title"], 10.5, True)
    y += 20
    intro = fill(doc.get("intro") if doc.get("intro") not in (None, "") else tpl.get("intro_%s" % kind, ""))
    for ln in pdf.wrap(intro, right - left, 9.5):
        pdf.text(left, y, ln, 9.5)
        y += 13
    y += 8

    cols = dict(
        pos=left, desc=left + 10 * MM, qty=left + 108 * MM, unit=left + 112 * MM, price=left + 148 * MM, amount=right
    )

    def table_head(y):
        pdf.rect(left, y - 10, right - left, 16, accent if layout != "minimal" else "#eeeeee")
        col = "#ffffff" if layout != "minimal" else "#000000"
        pdf.text(cols["pos"] + 2, y + 1, L["pos"], 8.5, True, col)
        pdf.text(cols["desc"], y + 1, L["desc"], 8.5, True, col)
        pdf.text(cols["qty"], y + 1, L["qty"], 8.5, True, col, "r")
        pdf.text(cols["unit"], y + 1, L["unit"], 8.5, True, col)
        pdf.text(cols["price"], y + 1, L["price"], 8.5, True, col, "r")
        pdf.text(cols["amount"] - 2, y + 1, L["amount"], 8.5, True, col, "r")
        return y + 18

    def ensure(y, need):
        nonlocal page_no
        if y + need > Pdf.H - 28 * MM:
            footer(page_no)
            pdf.new_page()
            page_no += 1
            header(False)
            return table_head(30 * MM)
        return y

    y = table_head(y)
    for i, ln in enumerate(doc.get("lines", []), 1):
        desc = pdf.wrap(ln.get("description", ""), 78 * MM, 9)
        y = ensure(y, 12 * len(desc) + 6)
        pdf.text(cols["pos"] + 2, y, str(i), 9)
        for j, dl in enumerate(desc):
            pdf.text(cols["desc"], y + j * 11.5, dl, 9)
        pdf.text(cols["qty"], y, qty_fmt(ln.get("qty", 0), lang), 9, False, "#000000", "r")
        pdf.text(cols["unit"], y, ln.get("unit", ""), 9)
        pdf.text(cols["price"], y, money(float(ln.get("price") or 0), lang), 9, False, "#000000", "r")
        pdf.text(cols["amount"] - 2, y, money(ln["amount"], lang), 9, False, "#000000", "r")
        y += 11.5 * len(desc) + 4
        pdf.line(left, y - 7, right, y - 7, "#e5e5e5", 0.3)
    y = ensure(y + 6, 70)
    tx = right - 70 * MM
    pdf.text(tx, y, L["net"], 9.5)
    pdf.text(right - 2, y, money(totals["net"], lang), 9.5, False, "#000000", "r")
    for rate, amt in sorted(totals["vat"].items()):
        if rate:
            y += 13
            pdf.text(tx, y, L["vat"] % ("%g" % rate), 9.5)
            pdf.text(right - 2, y, money(amt, lang), 9.5, False, "#000000", "r")
    y += 6
    pdf.line(tx, y, right, y, accent, 0.8)
    y += 14
    pdf.text(tx, y, L["total"], 11, True)
    pdf.text(right - 2, y, money(totals["gross"], lang), 11, True, "#000000", "r")
    y += 26
    if s.get("kleinunternehmer"):
        for ln in pdf.wrap(L["klein"], right - left, 8.5):
            pdf.text(left, y, ln, 8.5, False, "#444444")
            y += 11
        y += 4
    outro = fill(doc.get("outro") if doc.get("outro") not in (None, "") else tpl.get("outro_%s" % kind, ""))
    if kind == "invoice" and s.get("iban"):
        bank = ", ".join(
            x for x in (s.get("bank_name"), "IBAN " + s["iban"], "BIC " + s["bic"] if s.get("bic") else "") if x
        )
        outro += ("\n" if outro else "") + "%s: %s" % (L["bank"], bank)
    for ln in pdf.wrap(outro, right - left, 9.5):
        y = ensure(y, 14)
        pdf.text(left, y, ln, 9.5)
        y += 13
    if tpl.get("closing"):
        y += 8
        pdf.text(left, y, fill(tpl["closing"]), 9.5)
        y += 13
        pdf.text(left, y, s.get("owner_name", ""), 9.5)
    footer(page_no)
    return pdf.output()
