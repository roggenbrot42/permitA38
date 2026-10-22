"""Offers and invoices: numbering, finalizing, conversion, billing tracked time, PDF."""

import re
from datetime import date, timedelta

from permita38.core import calc, documents
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.settings import effective_settings

from .records import RecordService


class SalesService:
    def __init__(self, store: Repository, records: RecordService):
        self.store = store
        self.records = records

    def settings(self) -> dict:
        return effective_settings(self.store.settings())

    def next_number(self, coll: str) -> str:
        pattern = self.settings()["invoice_pattern" if coll == "invoices" else "offer_pattern"]
        year = date.today().year
        prefix_re = re.escape(pattern.split("{")[0])
        nums = []
        for x in self.store.list(coll):
            num = x.get("number") or ""
            m = re.match(prefix_re + r".*?(\d+)$", num)
            if m and str(year) in num:
                nums.append(int(m.group(1)))
        return pattern.format(year=year, n=max(nums, default=0) + 1)

    def _get(self, coll: str, rid: str) -> dict:
        doc = self.store.get(coll, rid)
        if not doc:
            raise ServiceError(404, "Not found")
        return doc

    def finalize(self, coll: str, rid: str) -> dict:
        doc = self._get(coll, rid)
        st = self.settings()
        if not doc.get("customer_id"):
            raise ServiceError(400, "Choose a customer first")
        if not doc.get("lines"):
            raise ServiceError(400, "Add at least one line")
        if not doc.get("number"):
            doc["number"] = self.next_number(coll)
        if doc.get("status", "draft") == "draft":
            doc["status"] = "sent"
        base = calc.d(doc.get("date")) or date.today()
        if coll == "invoices" and not doc.get("due_date"):
            doc["due_date"] = (base + timedelta(days=int(st["payment_days"]))).isoformat()
        if coll == "offers" and not doc.get("valid_until"):
            doc["valid_until"] = (base + timedelta(days=int(st["offer_valid_days"]))).isoformat()
        return self.store.save(coll, doc)

    def pdf(self, coll: str, rid: str) -> tuple[bytes, str]:
        doc = self._get(coll, rid)
        data = documents.render(self.store, coll[:-1], doc)
        return data, (doc.get("number") or "draft") + ".pdf"

    def accept_offer(self, rid: str) -> dict:
        with self.store.transaction():
            return self._accept_offer(rid)

    def _accept_offer(self, rid: str) -> dict:
        o = self._get("offers", rid)
        o["status"] = "accepted"
        o = self.store.save("offers", o)
        cust = self.store.get("customers", o["customer_id"]) if o.get("customer_id") else None
        proj = self.store.save(
            "projects",
            dict(
                name=o.get("title") or "Project for " + (cust or {}).get("name", ""),
                customer_id=o.get("customer_id"),
                status="active",
                color="#2563eb",
                offer_id=rid,
            ),
        )
        out = {"offer": o, "project": proj, "contract": None}
        plan = o.get("plan")
        if plan and plan.get("start") and plan.get("end"):
            c = dict(
                title=o.get("title") or "Contract " + (o.get("number") or ""),
                customer_id=o.get("customer_id"),
                project_id=proj["id"],
                start=plan["start"],
                end=plan["end"],
                offer_id=rid,
                status="confirmed",
                user_ids=plan.get("user_ids") or [],
            )
            for k in ("hours_per_week", "total_hours", "rate"):
                if plan.get(k) is not None:
                    c[k] = plan[k]
            out["contract"] = self.store.save("contracts", c)
        return out

    def offer_to_invoice(self, rid: str) -> dict:
        o = self._get("offers", rid)
        keys = ("customer_id", "project_id", "lines", "template_id", "vat_rate", "language", "title", "reference")
        inv = {k: o[k] for k in keys if o.get(k) is not None}
        inv.update(date=date.today().isoformat(), offer_id=rid)
        return self.store.save("invoices", self.records.prepare_document(inv, None))

    def invoice_from_time(
        self, customer_id: str | None, project_id: str | None, date_from: str | None, date_to: str | None
    ) -> dict:
        d0, d1 = date_from or "0000-00-00", date_to or "9999-99-99"
        ctx = calc.context(self.store)
        groups: dict = {}
        picked = []
        for e in self.store.list("entries"):
            if e.get("invoice_id") or not e.get("billable", True) or not (d0 <= e["date"] <= d1):
                continue
            p = ctx["projects"].get(e.get("project_id"))
            if not p or p.get("personal"):
                continue
            if project_id and p["id"] != project_id:
                continue
            if customer_id and p.get("customer_id") != customer_id:
                continue
            rate = calc.rate_for(e, ctx)
            t = ctx["tasks"].get(e.get("task_id"))
            g = groups.setdefault((p["id"], (t or {}).get("id"), rate), dict(minutes=0, p=p, t=t, rate=rate))
            g["minutes"] += e["minutes"]
            picked.append(e)
        if not picked:
            raise ServiceError(400, "No unbilled time found for this selection")
        lines = [
            dict(
                description=g["p"]["name"] + (" – " + g["t"]["name"] if g["t"] else ""),
                qty=round(g["minutes"] / 60, 2),
                unit="h",
                price=g["rate"],
            )
            for g in groups.values()
        ]
        dates = sorted(e["date"] for e in picked)
        first = next(iter(groups.values()))
        inv = dict(
            customer_id=customer_id or first["p"].get("customer_id"),
            lines=lines,
            date=date.today().isoformat(),
            service_from=dates[0],
            service_to=dates[-1],
            vat_rate=self.settings()["vat_rate"],
        )
        if project_id:
            inv["project_id"] = project_id
        inv = {k: v for k, v in inv.items() if v is not None}
        with self.store.transaction():  # the invoice and the "billed" marks on the entries succeed or fail together
            inv = self.store.save("invoices", self.records.prepare_document(inv, None))
            for e in picked:
                e["invoice_id"] = inv["id"]
                self.store.save("entries", e)
        return inv
