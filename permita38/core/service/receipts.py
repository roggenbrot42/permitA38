"""Receipt scan: upload pipeline, duplicates, confirm and discard (docs/specs/receipt-scan.md).

A clean bill is booked as an expense at once; any flag keeps it as a ``receipt_scans`` row (status ``flagged``) that
reaches the figures only when the user saves it. Nothing read from a receipt is logged.
"""

import copy
import hashlib
import re
import threading
import time
from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any

from pydantic import ValidationError

from permita38.core.errors import ServiceError
from permita38.core.models import ExpenseIn, ReceiptConfirmIn
from permita38.core.money import dec, q2, split_gross, to_float
from permita38.core.receipts import ocr, ocr_tools, photopdf, qrcodes, rules, runner, textrules
from permita38.core.receipts.extraction import Extraction, ReadResult, Word, cap_words
from permita38.core.receipts.flags import ordered
from permita38.core.receipts.rules import Context
from permita38.core.repository import ID_RE, Repository
from permita38.core.repository.base import new_id
from permita38.core.uploads import check_upload

from .records import RecordService
from .sanity import SanityService

ACCEPTED = [".pdf", ".png", ".jpg", ".jpeg", ".webp", ".xml"]
NO_NUMBER_LIMIT = Decimal("250.00")
INTERNAL_KEYS = ("sha256", "invoice_key")  # never sent to a client
_OCR_CACHE_SECONDS = 60.0


def _text_of(value: Any) -> str:
    return str(value or "").strip()


def _key(text: Any) -> str:
    return str(text or "").strip().casefold()


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _german_date(iso: Any) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else None


def _german_amount(value: Decimal) -> str:
    return f"{q2(value):,.2f}".replace(",", "\0").replace(".", ",").replace("\0", ".")


def public_scan(scan: dict) -> dict:
    """The scan as sent to a client: without the hash and the duplicate key."""
    return {k: v for k, v in scan.items() if k not in INTERNAL_KEYS}


def _gross_of(data: dict) -> Decimal:
    """Gross of an expense body: the sum of the printed line grosses, or net + VAT."""
    if data.get("lines"):
        return sum((dec(ln.get("gross")) for ln in data["lines"]), Decimal(0))
    return dec(data.get("net")) + dec(data.get("vat"))


def expense_body(draft: dict, attachment: str) -> dict | None:
    """The draft (``gross`` and ``vat_rate``, or ``lines``) as a validated ``ExpenseIn`` body, or ``None`` if the
    draft cannot be an expense. Exact decimals: the printed gross is split by ``money.split_gross``."""
    try:
        body: dict[str, Any] = {
            k: draft[k]
            for k in ("date", "paid_on", "invoice_no", "vendor", "category", "deductible", "vat_deductible")
            if draft.get(k) is not None
        }
        body["attachment"] = attachment
        if draft.get("lines"):
            body["lines"] = [{k: v for k, v in ln.items() if v is not None} for ln in draft["lines"]]
        else:
            if draft.get("gross") is None:
                return None
            gross = Decimal(str(draft["gross"]))
            rate = None if draft.get("vat_rate") is None else Decimal(str(draft["vat_rate"]))
            if rate is None:
                net, vat = gross, Decimal(0)
            else:
                ((net, vat),) = split_gross([(gross, rate)])
                body["vat_rate"] = float(rate)
            body["net"], body["vat"] = to_float(net), to_float(vat)
        ExpenseIn.model_validate(body)
        return body
    except (ValidationError, ArithmeticError, ValueError, TypeError, KeyError):
        return None


def _better(a: Any, b: Any) -> bool:
    """``a`` reads the bill better than ``b``: a reading beats an unreadable one, then fewer flags win."""
    key = lambda e: (any(f["code"] == "unreadable" for f in e.flags), len(e.flags))  # noqa: E731
    return bool(key(a) < key(b))


class ReceiptService:
    def __init__(
        self, store: Repository, records: RecordService, config: Any, sanity: SanityService, *, today: Any = date.today
    ):
        self.store = store
        self.records = records
        self.config = config
        self.sanity = sanity
        self._today = today
        self._backfill_lock = threading.Lock()
        self._hashed: set[Any] = set()  # expense attachments whose hash is known already
        self._ocr_cache: tuple[float, bool] | None = None
        self._install_lock = threading.Lock()

    # ------------------------------------------------------------------ capabilities
    def capabilities(self) -> dict:
        scan = self.config.scan
        try:
            import pypdfium2  # noqa: F401

            pdf = True
        except ImportError:  # pragma: no cover - a required dependency
            pdf = False
        ocr_ok = scan.enabled and self._ocr_available()
        offer = scan.enabled and not ocr_ok and ocr_tools.offered()
        pkg = ocr_tools.package() if offer else None
        extra = {"ocr_download_size": pkg.size, "ocr_download_host": pkg.host} if pkg else {}
        return {
            **extra,
            "enabled": scan.enabled,
            "pdf": pdf and scan.enabled,
            "einvoice": scan.enabled,
            "ocr": ocr_ok,
            "ocr_install": "available" if pkg else "none",
            "vision": "off",
            "accepted": ACCEPTED,
        }

    def _ocr_available(self) -> bool:
        now = time.monotonic()
        if self._ocr_cache and now - self._ocr_cache[0] < _OCR_CACHE_SECONDS:
            return self._ocr_cache[1]
        scan = self.config.scan
        found = ocr.available(scan.tesseract_path, scan.languages) is not None
        self._ocr_cache = (now, found)
        return found

    def install_ocr(self) -> dict:
        """Download and install the pinned OCR package (bundled Windows build only; asked for by a boss)."""
        pkg = ocr_tools.package() if ocr_tools.offered() else None
        if pkg is None or not self.config.scan.enabled:
            raise ServiceError(404, "OCR download is not offered on this build")
        if not self._install_lock.acquire(blocking=False):
            raise ServiceError(409, "An OCR download is already running")
        try:
            self._ocr_cache = None
            if self._ocr_available():
                raise ServiceError(409, "OCR is already installed")
            ocr_tools.install(pkg)
            self._ocr_cache = None
            return {"ocr": self._ocr_available(), "version": pkg.version}
        finally:
            self._install_lock.release()

    # ------------------------------------------------------------------ reading
    def list_flagged(self) -> list[dict]:
        rows = [s for s in self.store.find("receipt_scans", status="flagged")]
        rows.sort(key=lambda s: s.get("created") or 0, reverse=True)
        return [public_scan(s) for s in rows]

    def get(self, scan_id: str) -> dict:
        return public_scan(self._scan(scan_id))

    def _scan(self, scan_id: str) -> dict:
        scan = self.store.get("receipt_scans", scan_id) if ID_RE.match(scan_id) else None
        if not scan:
            raise ServiceError(404, "Not found")
        return scan

    def page_png(self, scan_id: str, page: int) -> bytes:
        scan = self._scan(scan_id)
        name = scan.get("attachment") or ""
        is_photo = name.lower().endswith((".jpg", ".jpeg"))
        path = self.store.attachment_path(name) if name.lower().endswith(".pdf") or is_photo else None
        if not path or page < 0:
            raise ServiceError(404, "Not found")
        rotation = (scan.get("doc") or {}).get("rotation")
        if is_photo and rotation not in (0, 90, 180, 270):
            raise ServiceError(404, "Not found")  # a photo has a page image only if it was read at an angle
        png = runner.render_page(path, page, self.config, rotation if is_photo else None)
        if png is None:
            raise ServiceError(404, "Not found")
        return png

    # ------------------------------------------------------------------ upload
    def upload(self, me: dict, filename: str | None, blob: bytes) -> dict:
        if not self.config.scan.enabled:
            raise ServiceError(503, "Receipt scanning is switched off on this device")
        filename, _ext = check_upload(filename, blob, ACCEPTED)
        digest = hashlib.sha256(blob).hexdigest()
        self._backfill()
        self._refuse_known_file(digest)
        name = self.store.save_attachment(filename, blob)
        try:
            return self._process(me, filename, name, digest)
        except Exception as exc:
            self.store.delete_attachment(name)  # a duplicate or a failure leaves no file behind
            if isinstance(exc, ServiceError):
                raise
            existing = self._known_scan(digest)  # a parallel upload won the unique index on sha256
            if existing is not None:
                raise self._duplicate(existing, "file") from None
            raise

    def _process(self, me: dict, filename: str, name: str, digest: str) -> dict:
        path = self.store.attachment_path(name) or ""
        read = runner.read_file(path, self.config)
        scan_id = new_id()
        ev = self._evaluate(read)  # the slow part runs outside the transaction
        ev.doc["pages"] = read.pages_total
        if read.engine == "ocr" and name.lower().endswith((".jpg", ".jpeg")):
            ev.doc["rotation"] = read.rotation
        with self.store.transaction():  # one writer at a time: the re-checks and the inserts cannot interleave
            self._refuse_known_file(digest)
            self._refuse_known_bill(
                ev.doc, ev.draft.get("vendor"), ev.draft.get("invoice_no"), None, ev.draft.get("date")
            )
            self._flag_late_duplicate(ev, None)
            row: dict[str, Any] = {
                "id": scan_id,
                "sha256": digest,
                "invoice_key": ev.invoice_key,
                "attachment": name,
                "filename": (textrules.clean_text(re.split(r"[\\/]", filename)[-1], 200) or "receipt"),
                "engine": read.engine,
                "draft": ev.draft,
                "doc": ev.doc,
                "created_by": me.get("id"),
            }
            flags = ev.flags
            expense = None
            if not flags:
                body = expense_body(ev.draft, name)
                if body is not None:
                    try:
                        expense = self.records.create_scanned(me, body, scan_id)
                    except ServiceError as err:
                        if err.status != 422:
                            raise
                if expense is None:
                    flags = ordered({"invalid_data"})
            if expense is not None:
                row.update(status="booked", flags=[], expense_id=expense["id"])
            else:
                row.update(status="flagged", flags=flags)
            saved = self.store.save("receipt_scans", row)
        return {"outcome": "booked" if expense else "flagged", "scan": public_scan(saved), "expense": expense}

    def _evaluate(self, read: ReadResult, *, photos: bool = False, own_id: str | None = None) -> rules.Evaluation:
        """The best reading of ``read`` over its OCR page modes. ``photos``: the pages are overlapping photos."""
        read = cap_words(replace(read))  # defensive: the worker already bounds the words
        extraction = Extraction()
        # the page modes of the OCR: the first is the main reading, the others are tried if it is not clean
        candidates: list[tuple[ReadResult, Extraction]] = []
        if not read.error and read.engine != "none":
            words = textrules.drop_overlap(read.words) if photos else read.words
            extraction = copy.deepcopy(read.extraction) if read.extraction else self._parse(read, words)
            if read.extraction and read.words:
                self._einvoice_sources(extraction, read.words)
            candidates.append((read, extraction))
            if not read.extraction:
                for alt in read.alternatives:
                    alt = textrules.drop_overlap(alt) if photos else alt
                    candidates.append((replace(read, words=alt, alternatives=[]), self._parse(read, alt)))
        ctx = self._context(own_id)
        ev = None
        for cand_read, cand in candidates:
            tried = rules.evaluate(cand_read, cand, ctx)
            if ev is None or _better(tried, ev):
                ev = tried
            if tried.clean:
                break
        return ev if ev is not None else rules.evaluate(read, extraction, ctx)

    def _parse(self, read: ReadResult, words: list[Word]) -> Extraction:
        """Text rules on ``words``, then the QR codes found on the pages (a TSE code beats what the text says)."""
        x = textrules.parse(words, exclude=self._own_names())
        if read.codes:
            qrcodes.apply_qr(x, read.codes, ocr=read.engine == "ocr")
        return x

    def _own_names(self) -> tuple[str, ...]:
        """The company name in Settings: the customer on a bill, never the vendor."""
        try:
            settings = self.store.settings() or {}
        except Exception:
            return ()
        return tuple(str(settings.get(k)) for k in ("company_name",) if settings.get(k))

    # ------------------------------------------------------------------ more photos of one receipt
    def add_part(self, me: dict, scan_id: str, filename: str | None, blob: bytes) -> dict:
        """Add a photo to a flagged scan: the photos become the pages of one generated PDF, read again as a whole."""
        if not self.config.scan.enabled:
            raise ServiceError(503, "Receipt scanning is switched off on this device")
        filename, _ext = check_upload(filename, blob, [".jpg", ".jpeg"])
        digest = hashlib.sha256(blob).hexdigest()
        self._backfill()
        scan = self._scan(scan_id)
        if scan.get("status") != "flagged":
            raise ServiceError(409, "This receipt has already been saved", body={"code": "not_flagged"})
        self._refuse_known_file(digest)
        old_name = scan.get("attachment") or ""
        old_doc = scan.get("doc") or {}
        old_path = self.store.attachment_path(old_name) or ""
        try:
            data = open(old_path, "rb").read()
            existing = (
                photopdf.extract(data)
                if old_name.lower().endswith(".pdf")
                else [(data, int(old_doc.get("rotation") or 0))]
            )
            photopdf.jpeg_size(blob)
            for photo, _turn in existing:
                photopdf.jpeg_size(photo)
        except (photopdf.PhotoError, OSError):
            raise ServiceError(409, "Only photos can be added to a photo", body={"code": "not_photo"}) from None
        if len(existing) >= photopdf.MAX_PHOTOS:
            raise ServiceError(409, "At most 5 photos per receipt", body={"code": "too_many_parts"})
        hashes = list(old_doc.get("part_hashes") or [scan.get("sha256")])
        probe = self.store.save_attachment(filename, blob)  # the angle of the new photo is found on its own
        try:
            turned = runner.read_file(self.store.attachment_path(probe) or "", self.config)
        finally:
            self.store.delete_attachment(probe)
        pdf_name = self.store.save_attachment("receipt.pdf", photopdf.build([*existing, (blob, turned.rotation)]))
        try:
            read = runner.read_file(self.store.attachment_path(pdf_name) or "", self.config)
            ev = self._evaluate(read, photos=True, own_id=scan_id)  # the slow part runs outside the transaction
            ev.doc["pages"] = read.pages_total
            ev.doc["part_hashes"] = [*hashes, digest]
            with self.store.transaction():
                scan = self._scan(scan_id)
                if scan.get("status") != "flagged":
                    raise ServiceError(409, "This receipt has already been saved", body={"code": "not_flagged"})
                if (
                    scan.get("attachment") != old_name
                    or list((scan.get("doc") or {}).get("part_hashes") or [scan.get("sha256")]) != hashes
                ):
                    raise ServiceError(
                        409, "This receipt was changed meanwhile, please try again", body={"code": "changed"}
                    )
                self._refuse_known_file(digest)
                self._refuse_known_bill(
                    ev.doc, ev.draft.get("vendor"), ev.draft.get("invoice_no"), scan_id, ev.draft.get("date")
                )
                self._flag_late_duplicate(ev, scan_id)
                row = {**scan, "attachment": pdf_name, "engine": read.engine, "draft": ev.draft, "doc": ev.doc}
                row["invoice_key"] = ev.invoice_key
                expense = None
                flags = ev.flags
                if not flags:
                    body = expense_body(ev.draft, pdf_name)
                    if body is not None:
                        try:
                            expense = self.records.create_scanned(me, body, scan_id)
                        except ServiceError as err:
                            if err.status != 422:
                                raise
                    if expense is None:
                        flags = ordered({"invalid_data"})
                if expense is not None:
                    row.update(status="booked", flags=[], expense_id=expense["id"])
                    self._remember_parts(row, ev.doc["part_hashes"])
                else:
                    row.update(status="flagged", flags=flags)
                saved = self.store.save("receipt_scans", row)
        except Exception:
            self.store.delete_attachment(pdf_name)
            raise
        self.store.delete_attachment(old_name)  # the originals are gone: the generated PDF holds the photos
        return {"outcome": "booked" if expense else "flagged", "scan": public_scan(saved), "expense": expense}

    def _remember_parts(self, scan: dict, hashes: list[str]) -> None:
        """One ``known`` row per photo of a booked scan, so that each photo alone is a duplicate (the first is the scan)."""
        for digest in hashes:
            if digest == scan.get("sha256") or self.store.find("receipt_scans", sha256=digest):
                continue
            self.store.save(
                "receipt_scans",
                {
                    "status": "known",
                    "sha256": digest,
                    "attachment": scan.get("attachment"),
                    "filename": scan.get("filename"),
                    "engine": "none",
                    "expense_id": scan.get("expense_id"),
                },
            )

    # ------------------------------------------------------------------ duplicates
    def _backfill(self) -> None:
        """Hash expense attachments that have no scan row yet and store them as ``known`` (hand-attached bills)."""
        with self._backfill_lock:
            scans = self.store.list("receipt_scans")
            attached = {s.get("attachment") for s in scans} | self._hashed
            hashes = {s.get("sha256") for s in scans}
            for e in self.store.list("expenses"):
                att = e.get("attachment")
                path = self.store.attachment_path(att) if att and att not in attached else None
                if not path:
                    continue
                try:
                    digest = _sha256_file(path)
                    self._hashed.add(att)  # hashed once: the next upload does not read it again
                    if digest in hashes:
                        continue
                    self.store.save(
                        "receipt_scans",
                        {
                            "status": "known",
                            "sha256": digest,
                            "attachment": att,
                            "filename": att,
                            "engine": "none",
                            "expense_id": e["id"],
                        },
                    )
                    hashes.add(digest)
                    attached.add(att)
                except Exception:  # an unreadable file or a parallel insert: it is simply not known
                    continue

    def _counts(self, scan: dict) -> bool:
        """A scan counts as a duplicate only if it is flagged, or its expense still exists. A stale row is removed."""
        if scan.get("status") == "flagged":
            return True
        expense = self.store.get("expenses", scan["expense_id"]) if scan.get("expense_id") else None
        if expense is not None and (
            scan.get("status") != "known" or expense.get("attachment") == scan.get("attachment")
        ):
            return True
        self.store.delete("receipt_scans", scan["id"])
        return False

    def _known_scan(self, digest: str) -> dict | None:
        for scan in self.store.find("receipt_scans", sha256=digest):
            if self._counts(scan):
                return scan
        for scan in self.store.find(
            "receipt_scans", status="flagged"
        ):  # a photo added to a scan that still needs a check
            if digest in ((scan.get("doc") or {}).get("part_hashes") or []):
                return scan
        return None

    def _refuse_known_file(self, digest: str) -> None:
        scan = self._known_scan(digest)
        if scan is not None:
            raise self._duplicate(scan, "file")

    def _refuse_known_bill(
        self, doc: dict, vendor: Any, invoice_no: Any, own_id: str | None = None, bill_date: Any = None
    ) -> None:
        """409 if the bill is already known: by TSE id (the strongest identity), or by invoice number when
        ``_clash`` is hard. A weaker match of the number only raises ``possible_duplicate`` in ``rules.evaluate``."""
        probe = {**doc, "bill_date": bill_date or doc.get("bill_date")}
        tse_id = doc.get("tse_id")
        if tse_id:
            for scan in self.store.list("receipt_scans"):
                if scan["id"] != own_id and (scan.get("doc") or {}).get("tse_id") == tse_id and self._counts(scan):
                    raise self._duplicate(scan, "tse")
        found = self._clash(probe, own_id)
        if found is not None and found[0] == "hard":
            raise self._duplicate(found[1], "invoice")

    def _flag_late_duplicate(self, ev: rules.Evaluation, own_id: str | None) -> None:
        """Inside the transaction: the checks of ``rules.evaluate`` ran before it, so a parallel upload of the same bill
        (other bytes) may have been stored meanwhile. A clean result that now meets a known bill is flagged."""
        if any(f["code"] == "possible_duplicate" for f in ev.flags):
            return
        doc, draft = ev.doc, ev.draft
        ctx = self._context(own_id)
        gross = doc.get("printed_total")
        hit = bool(
            draft.get("vendor")
            and draft.get("date")
            and gross is not None
            and ctx.possible_duplicate(
                {
                    "vendor": draft["vendor"],
                    "date": draft["date"],
                    "gross": gross,
                    "invoice_no": draft.get("invoice_no"),
                    "invoice_ocr": bool(doc.get("number_ocr")),
                }
            )
        )
        hit = hit or ctx.number_clash(doc) is not None
        if hit:
            ev.flags = ordered({f["code"] for f in ev.flags} | {"possible_duplicate"})
            ev.clean = False

    def _clash(self, probe: dict, own_id: str | None = None) -> tuple[str, dict] | None:
        """How the invoice number of ``probe`` meets the known bills: ``("hard", row)``, ``("soft", row)`` or ``None``.

        hard: the number came from OCR on neither side and the bills share the date, or share the VAT id while the
        number is an invoice number (not a till or receipt number, ``number_weak``).
        soft: the same number otherwise (a till number that restarts every day is not a duplicate; the user decides).
        Never a clash with a credit note, or when both sides have a TSE id and the ids differ. The keys of a booked
        bill are taken from the live expense, so an edit or a delete is followed."""
        mine = set(probe.get("invoice_keys") or [])
        if not mine or probe.get("credit_note"):
            return None
        my_vat = set(probe.get("vat_keys") or [])
        scans = [s for s in self.store.list("receipt_scans") if s["id"] != own_id]
        by_expense = {s["expense_id"]: s for s in scans if s.get("expense_id") and s.get("status") == "booked"}
        candidates: list[tuple[dict, dict, Any, set[str], set[str]]] = []  # row, doc, date, keys, VAT keys
        for e in self.store.list("expenses"):
            row = by_expense.get(e["id"])
            doc = (row or {}).get("doc") or {}
            keys = set(rules.invoice_keys(doc.get("vat_id"), e.get("vendor"), e.get("invoice_no")))
            vat = set(rules.vat_keys(doc.get("vat_id"), e.get("invoice_no")))
            candidates.append((row or {"expense_id": e["id"]}, doc, e.get("date"), keys, vat))
        for s in scans:
            if s.get("status") == "flagged":
                doc = s.get("doc") or {}
                keys, vat = set(doc.get("invoice_keys") or []), set(doc.get("vat_keys") or [])
                candidates.append((s, doc, (s.get("draft") or {}).get("date"), keys, vat))
        best: tuple[str, dict] | None = None
        for row, doc, date_, keys, their_vat in candidates:
            if not mine & keys or doc.get("credit_note"):
                continue
            if probe.get("tse_id") and doc.get("tse_id") and probe["tse_id"] != doc["tse_id"]:
                continue
            ocr = bool(probe.get("number_ocr")) or bool(doc.get("number_ocr"))
            same_vat = bool(my_vat & their_vat)
            same_day = bool(probe.get("bill_date")) and probe.get("bill_date") == date_
            weak = bool(probe.get("number_weak")) or bool(doc.get("number_weak"))  # till or receipt label
            if not ocr and (same_day or (same_vat and not weak)):
                return "hard", row
            best = best or ("soft", row)
        return best

    def _duplicate(self, scan: dict, match: str) -> ServiceError:
        expense = self.store.get("expenses", scan["expense_id"]) if scan.get("expense_id") else None
        gross: Decimal | None
        if expense is not None:
            vendor, iso, gross = expense.get("vendor"), expense.get("date"), _gross_of(expense)
        else:
            draft = scan.get("draft") or {}
            vendor, iso = draft.get("vendor"), draft.get("date")
            gross = None if draft.get("gross") is None else dec(draft["gross"])
        parts = [vendor, _german_date(iso), None if gross is None else _german_amount(gross) + " €"]
        what = ", ".join(str(p) for p in parts if p)
        message = "This receipt is already in the list" + (f": {what}." if what else ".")
        return ServiceError(
            409,
            message,
            body={
                "code": "duplicate",
                "match": match,
                "expense_id": expense["id"] if expense is not None else None,
                "scan_id": scan.get("id"),
            },
        )

    # ------------------------------------------------------------------ rules context
    def _context(self, own_id: str | None = None) -> Context:
        expenses = self.store.list("expenses")
        scans = [s for s in self.store.list("receipt_scans") if s["id"] != own_id]
        ocr_number = {  # expense id -> whether its number was read by OCR
            s["expense_id"]: bool((s.get("doc") or {}).get("number_ocr")) for s in scans if s.get("expense_id")
        }
        # what a new bill is compared with: the expenses, and the bills that still wait for a check
        others: list[tuple[str, str, Decimal, str, bool]] = [
            (
                rules.norm_name(e.get("vendor")),
                str(e.get("date")),
                q2(_gross_of(e)),
                _key(e.get("invoice_no")),
                ocr_number.get(e["id"], False),
            )
            for e in expenses
        ]
        for s in scans:
            d = s.get("draft") or {}
            if s.get("status") == "flagged" and d.get("vendor") and d.get("date") and d.get("gross") is not None:
                others.append(
                    (
                        rules.norm_name(d["vendor"]),
                        str(d["date"]),
                        q2(dec(d["gross"])),
                        _key(d.get("invoice_no")),
                        bool((s.get("doc") or {}).get("number_ocr")),
                    )
                )

        def category_of_vendor(vendor: str) -> str | None:
            same = [e for e in expenses if e.get("category") and _key(e.get("vendor")) == _key(vendor)]
            same.sort(key=lambda e: e.get("updated") or e.get("created") or 0)  # stable: ties keep list order
            return same[-1]["category"] if same else None

        def possible_duplicate(probe: dict) -> bool:
            if probe.get("gross") is None:
                return False
            vendor, gross, number = (
                rules.norm_name(probe["vendor"]),
                q2(dec(probe["gross"])),
                _key(probe.get("invoice_no")),
            )
            for o_vendor, o_date, o_gross, o_number, o_ocr in others:
                if o_vendor != vendor or o_date != probe["date"] or o_gross != gross:
                    continue  # the gross, so that a tip does not hide a match
                if number and o_number and number != o_number and not (o_ocr or probe.get("invoice_ocr")):
                    continue  # two real bills with different numbers, none of them read by OCR
                return True
            return False

        def number_clash(probe: dict) -> str | None:
            found = self._clash(probe, own_id)
            return found[0] if found else None

        return Context(
            today=self._today(),
            category_of_vendor=category_of_vendor,
            possible_duplicate=possible_duplicate,
            number_clash=number_clash,
        )

    # ------------------------------------------------------------------ boxes of an e-invoice in its PDF
    @staticmethod
    def _einvoice_sources(x: Extraction, words: list[Word]) -> None:
        """Mark a value only where it is found exactly once in the text layer of the PDF."""

        def runs(tokens: list[str]) -> list[list[Word]]:
            n = len(tokens)
            return [
                words[i : i + n]
                for i in range(len(words) - n + 1)
                if n and [w.text.strip() for w in words[i : i + n]] == tokens
            ]

        def mark(field: str, candidates: list[list[str]], same: Any = None) -> None:
            found: list[list[Word]] = []
            for tokens in candidates:
                found += runs(tokens)
            if same is not None:
                found += [[w] for w in words if (v := textrules.parse_amount(w.text)) is not None and v == same]
            if len(found) == 1:
                x.sources[field] = found[0]

        if x.vendor:
            mark("vendor", [x.vendor.split()])
        if x.invoice_no:
            mark("invoice_no", [x.invoice_no.split()])
        if x.vat_id:
            mark("vat_id", [[x.vat_id], [x.vat_id[:2], x.vat_id[2:]]])
        for field, iso in (("date", x.bill_date), ("due_date", x.due_date)):
            m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
            if m:
                y, mo, d = m.groups()
                mark(field, [[f"{d}.{mo}.{y}"], [f"{d}.{mo}.{y[2:]}"], [iso or ""]])
        if x.total is not None:
            mark("total", [], same=x.total)

    # ------------------------------------------------------------------ confirm and discard
    def confirm(self, me: dict, scan_id: str, body: ReceiptConfirmIn) -> dict:
        self._scan(scan_id)
        with self.store.transaction():  # a second click waits here and then finds the scan booked
            scan = self._scan(scan_id)
            if scan.get("status") != "flagged":
                raise ServiceError(409, "This receipt has already been saved", body={"code": "not_flagged"})
            data = body.model_dump(exclude_none=True)
            accepted = set(data.pop("accepted", []))
            for k in ("external_id", "attachment"):
                data.pop(k, None)
            data["attachment"] = scan.get("attachment")
            gross = _gross_of(data)
            missing: list[str] = []
            invoice_no = (data.get("invoice_no") or "").strip()
            if not invoice_no and gross > NO_NUMBER_LIMIT and "no_invoice_number" not in accepted:
                missing.append("no_invoice_number")
            printed = (scan.get("doc") or {}).get("printed_total")
            if printed is not None and q2(gross) != dec(printed) and "total_differs" not in accepted:
                missing.append("total_differs")
            if missing:
                raise ServiceError(
                    409,
                    "Please confirm before saving: " + ", ".join(missing),
                    body={"code": "needs_acceptance", "missing": missing},
                )
            key = None
            doc = dict(scan.get("doc") or {})
            if invoice_no:
                ident = doc.get("vat_id")
                key = rules._invoice_key(Extraction(vendor=data.get("vendor"), vat_id=ident, invoice_no=invoice_no))
                doc["invoice_keys"] = rules.invoice_keys(ident, data.get("vendor"), invoice_no)
                doc["vat_keys"] = rules.vat_keys(ident, invoice_no)
                if invoice_no != _text_of((scan.get("draft") or {}).get("invoice_no")):
                    doc["number_ocr"] = False  # typed by the user
            else:
                doc["invoice_keys"] = []
                doc["vat_keys"] = []
            self._refuse_known_bill(doc, data.get("vendor"), invoice_no, scan_id, data.get("date"))
            expense = self.records.create_scanned(me, data, scan_id)
            saved = self.store.save(
                "receipt_scans",
                {**scan, "status": "booked", "expense_id": expense["id"], "invoice_key": key, "doc": doc},
            )
            self._remember_parts(saved, list((scan.get("doc") or {}).get("part_hashes") or []))
        return {
            "expense": {**expense, "sanity_warnings": self.sanity.check("expenses", expense, None)},
            "scan": public_scan(saved),
        }

    def discard(self, scan_id: str) -> None:
        with self.store.transaction():
            scan = self._scan(scan_id)
            if scan.get("status") != "flagged":
                raise ServiceError(409, "Only a receipt that still needs a check can be discarded")
            self.store.delete("receipt_scans", scan_id)
        if scan.get("attachment"):
            self.store.delete_attachment(scan["attachment"])
