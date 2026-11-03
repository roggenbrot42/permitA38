"""QR codes on receipts (docs/specs/receipt-scan.md, "QR codes").

``find_codes`` reads a raster with zxing-cpp (worker process only). ``parse_tse`` and ``parse_epc`` turn the text of a
code into ``Qr``; ``apply_qr`` lets a TSE code replace what the text rules read for groups, total and payment kind.
All content is untrusted: amounts go through ``Decimal`` with bounds, nothing is followed, the signature is not verified.
"""

import re
from decimal import Decimal, InvalidOperation

from .extraction import Adjustment, Code, Extraction, Qr, VatGroup, Word

MAX_CODE_CHARS = 2000
MAX_CODES = 10
MAX_AMOUNT = Decimal("1000000")
TSE_RATES = (Decimal(19), Decimal(7), Decimal("10.7"), Decimal("5.5"), Decimal(0))
_AMOUNT = re.compile(r"-?\d{1,9}(?:\.\d{1,4})?")


def find_codes(width: int, height: int, gray: bytes, page: int = 0) -> list[Code]:
    """QR codes of a gray raster (one byte per pixel). Never raises; no library, no codes."""
    try:
        import zxingcpp

        view = zxingcpp.ImageView(gray, width, height, zxingcpp.ImageFormat.Lum)
        found = zxingcpp.read_barcodes(view, formats=zxingcpp.BarcodeFormat.QRCode)  # type: ignore[arg-type]
    except Exception:
        return []
    out: list[Code] = []
    for item in found[:MAX_CODES]:
        text = item.text
        if not text or len(text) > MAX_CODE_CHARS:
            continue
        try:
            pos = item.position
            xs = [pos.top_left.x, pos.top_right.x, pos.bottom_left.x, pos.bottom_right.x]
            ys = [pos.top_left.y, pos.top_right.y, pos.bottom_left.y, pos.bottom_right.y]
        except Exception:
            xs, ys = [0, 0], [0, 0]
        x0, y0 = max(min(xs), 0) / width, max(min(ys), 0) / height
        out.append(Code(text, page, x0, y0, min(max(xs) / width, 1.0) - x0, min(max(ys) / height, 1.0) - y0))
    return out


def _amount(text: str) -> Decimal | None:
    text = text.strip()
    if not _AMOUNT.fullmatch(text):
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if abs(value) <= MAX_AMOUNT else None


def parse_tse(text: str) -> Qr | None:
    """The DSFinV-K code of a till: ``V0;serial;Kassenbeleg-V1;Beleg^g19_g7_g10.7_g5.5_g0^pay:Bar_pay:Unbar;tx;..;start;..``."""
    if len(text) > MAX_CODE_CHARS or not text.startswith("V0;"):
        return None
    parts = text.split(";")
    if len(parts) < 7 or parts[2] != "Kassenbeleg-V1":
        return None  # other process types (Bestellung-V1, ...) are ignored
    data = parts[3].split("^")
    if len(data) != 3 or data[0] != "Beleg":
        return None
    gross = data[1].split("_")
    if len(gross) > len(TSE_RATES):
        return None
    groups: list[tuple[Decimal, Decimal]] = []
    for rate, raw in zip(TSE_RATES, gross):
        value = _amount(raw)
        if value is None:
            return None
        if value != 0:
            groups.append((rate, value))  # a 0 % gross is a tip or a free item: kept, only a zero amount is dropped
    payments: list[tuple[Decimal, str]] = []
    for raw in filter(None, data[2].split("_")):
        amount, _, kind = raw.partition(":")
        value = _amount(amount)
        if value is None or kind not in ("Bar", "Unbar"):
            return None
        payments.append((value, kind))
    m = re.match(r"(\d{4}-\d{2}-\d{2})", parts[6])
    serial = re.sub(r"[^A-Za-z0-9]", "", parts[1])[:8]
    tx = re.sub(r"\D", "", parts[4])[:12]
    number = f"{serial}-{tx}" if serial and tx else None
    full = re.sub(r"[^A-Za-z0-9]", "", parts[1]).casefold()[:128]
    start = re.sub(r"\D", "", parts[6])[:20]  # the start time keeps a generic serial from colliding
    tse_id = f"{full}-{tx}-{start}" if full and tx else None
    return Qr(
        kind="tse", groups=groups, payments=payments, date=m.group(1) if m else None, number=number, tse_id=tse_id
    )


def parse_epc(text: str) -> Qr | None:
    """A GiroCode (``BCD``): IBAN, recipient, ``EUR<amount>``."""
    lines = text.split("\n")
    if len(text) > MAX_CODE_CHARS or len(lines) < 8 or lines[0].strip() != "BCD":
        return None
    m = re.fullmatch(r"EUR(\d{1,9}(?:\.\d{1,2})?)", lines[7].strip())
    amount = _amount(m.group(1)) if m else None
    return Qr(kind="epc", epc_amount=amount) if amount is not None else None


def parse_code(text: str) -> Qr | None:
    return parse_tse(text) or parse_epc(text)


def apply_qr(x: Extraction, codes: list[Code], *, ocr: bool) -> None:
    """Merge the first TSE code (else the first EPC code) into ``x``. ``ocr``: the printed values came from OCR."""
    parsed = [(c, q) for c in codes if (q := parse_code(c.text)) is not None]
    tse = next(((c, q) for c, q in parsed if q.kind == "tse"), None)
    chosen = tse or next(((c, q) for c, q in parsed if q.kind == "epc"), None)
    if chosen is None:
        return
    code, qr = chosen
    qr.printed_total = x.total
    qr.printed_from_ocr = ocr
    x.qr = qr
    x.sources["qr"] = [Word("qr", code.page, code.x, code.y, code.w, code.h)]
    if qr.kind == "epc":
        if "open" not in x.payment_signals:
            x.payment_signals.append("open")
        return
    old = {g.rate: g for g in x.vat_groups}
    x.vat_groups = []
    for rate, gross in qr.groups:
        text_group = old.get(rate)
        group = VatGroup(rate=rate, gross=gross, code=text_group.code if text_group else None)
        if text_group is not None and text_group.vat is not None:
            group.vat = text_group.vat
        x.vat_groups.append(group)
    zero = next((g for r, g in qr.groups if r == 0), None)
    if zero is not None and any(a.kind == "tip" for a in x.adjustments):
        # the code holds the tip as a 0 % gross: it replaces the tip that was read, and is no group of its own
        x.adjustments = [a for a in x.adjustments if a.kind != "tip"] + [Adjustment("tip", zero)]
        x.vat_groups = [g for g in x.vat_groups if g.rate != 0]
    if qr.payments:
        x.total = sum((amount for amount, _k in qr.payments), Decimal(0))
        x.competing_totals = []
        x.sources.pop("total", None)
        x.card_amount = sum((a for a, k in qr.payments if k == "Unbar"), Decimal(0)) or x.card_amount
        for signal in ("cash" if any(k == "Bar" for _a, k in qr.payments) else "card",):
            if signal not in x.payment_signals:
                x.payment_signals.append(signal)
        x.payment_signals = [s for s in x.payment_signals if s != "open"]
    if qr.date:
        if x.bill_date is None and (not x.competing_dates or qr.date in x.competing_dates):
            x.bill_date, x.competing_dates = qr.date, []
    if not x.invoice_no and qr.number:
        x.invoice_no = qr.number
    x.doc_type = "receipt"
