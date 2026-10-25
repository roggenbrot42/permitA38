"""Suppliers and subcontractors: master data, the limited view for employees, vendor suggestions and adoption
(docs/contracts/subcontractors.md, docs/specs/subcontractors.md).

Capacity, offers, allocations and commitments live in :mod:`procurement`; this module only knows the supplier rows and
their links to expenses.
"""

from __future__ import annotations

import unicodedata
from collections import defaultdict

from permita38.core import rights
from permita38.core.errors import ServiceError
from permita38.core.money import dec, q2
from permita38.core.repository import ID_RE, Repository
from permita38.core.rights import Scope

from . import sanity_rules as rules
from .planning_warnings import warning
from .records import RESPONSE_ONLY

EU_COUNTRIES = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE".split()
)  # member states other than Germany
LEGAL_FORMS = frozenset("gmbh mbh ug ek ohg kg gbr ag se ltd llc inc co".split())
DEFAULT_WORKDAYS = [0, 1, 2, 3, 4]
LIMITED_KEYS = ("id", "name", "kind", "tags", "active")
CAPACITY_KIND = "person-capacity"
IN_USE = "Supplier is used by incoming offers, allocations or expenses; archive it instead"


def default_vat_mode(country: str | None) -> str:
    if country in (None, "", "DE"):
        return "domestic"
    return "eu_reverse_charge" if country in EU_COUNTRIES else "third_country_reverse_charge"


def vendor_key(name: str) -> str:
    """The normalised key of a vendor text: NFKC, case-fold, only letters, digits and spaces, trailing legal forms
    (GmbH, UG, e.K., Ltd ...) removed. Empty when nothing is left."""
    text = unicodedata.normalize("NFKC", name).casefold()
    text = "".join(ch for ch in text if ch.isalnum() or ch.isspace())
    tokens = text.split()
    while tokens and tokens[-1] in LEGAL_FORMS:
        tokens.pop()
    return " ".join(tokens)


def limited(rec: dict) -> dict:
    out = {k: rec[k] for k in LIMITED_KEYS if k in rec}
    out.setdefault("kind", "company")
    out.setdefault("tags", [])
    out.setdefault("active", True)
    return out


def _sort_key(rec: dict) -> tuple[str, str]:
    return (str(rec.get("name") or "").casefold(), str(rec.get("id") or ""))


class SupplierService:
    def __init__(self, store: Repository):
        self.store = store

    # ------------------------------------------------------------------ reads
    def _view(self, me: dict, rec: dict) -> dict:
        return limited(rec) if rights.scope_for(me, "suppliers") != Scope.ALL else rec

    def list(
        self, me: dict, kind: str | None = None, active: bool | None = None, tag: str | None = None, q: str | None = None
    ) -> list[dict]:
        full = rights.scope_for(me, "suppliers") == Scope.ALL
        needle = (q or "").strip().casefold()
        rows = []
        for r in self.store.list("suppliers"):
            if kind and r.get("kind", "company") != kind:
                continue
            if active is not None and bool(r.get("active", True)) != active:
                continue
            if tag and tag.casefold() not in {str(t).casefold() for t in r.get("tags") or []}:
                continue
            if needle:
                fields = [r.get("name")] + ([r.get("supplier_no"), r.get("contact")] if full else [])
                if not any(needle in str(f or "").casefold() for f in fields):
                    continue
            rows.append(r)
        rows.sort(key=_sort_key)
        return [r if full else limited(r) for r in rows]

    def get(self, me: dict, rid: str) -> dict:
        rec = self.store.get("suppliers", rid) if ID_RE.match(rid) else None
        if not rec:
            raise ServiceError(404, "Not found")
        return self._view(me, rec)

    # ------------------------------------------------------------------ writes
    @staticmethod
    def prepare(data: dict, old: dict | None = None) -> dict:
        """The record as it is stored: response-only keys dropped, the VAT mode and the weekdays defaulted."""
        rec = {k: v for k, v in data.items() if k not in RESPONSE_ONLY and k not in ("id", "created", "updated")}
        rec.setdefault("kind", "company")
        rec.setdefault("active", True)
        rec.setdefault("country", "DE")
        if not rec.get("vat_mode"):
            rec["vat_mode"] = default_vat_mode(rec.get("country"))
        if rec["kind"] == CAPACITY_KIND:
            rec["workdays"] = sorted(rec.get("workdays") or DEFAULT_WORKDAYS)
        rec.setdefault("tags", [])
        if old:
            for k in ("external_id", "import_batch"):  # import bookkeeping is not a client field
                if k in old:
                    rec[k] = old[k]
                else:
                    rec.pop(k, None)
        else:
            rec.pop("external_id", None)
            rec.pop("import_batch", None)
        return {k: v for k, v in rec.items() if v is not None}

    def warnings(self, rec: dict) -> list[dict]:
        out: list[dict] = []
        number = str(rec.get("supplier_no") or "").strip().casefold()
        if number:
            for o in self.store.list("suppliers"):
                if o.get("id") != rec.get("id") and str(o.get("supplier_no") or "").strip().casefold() == number:
                    out.append(
                        warning(
                            "supplier.duplicate_number",
                            "warning",
                            "supplier_no",
                            f"The supplier number {rec.get('supplier_no')} is already used by {o.get('name')}.",
                            {"other_id": o.get("id")},
                            confirmable=False,
                        )
                    )
                    break
        return out + rules.check_identifiers(rec, owner="supplier")

    def create(self, data: dict) -> dict:
        saved = self.store.save("suppliers", self.prepare(data))
        return {**saved, "sanity_warnings": self.warnings(saved)}

    def update(self, rid: str, data: dict) -> dict:
        old = self.store.get("suppliers", rid) if ID_RE.match(rid) else None
        if not old:
            raise ServiceError(404, "Not found")
        rec = self.prepare(data, old)
        if old.get("kind") == CAPACITY_KIND and rec["kind"] != CAPACITY_KIND and self._capacity_in_use(rid):
            raise ServiceError(
                409, "The kind cannot change while an incoming offer with a plan or an allocation uses this supplier"
            )
        saved = self.store.save("suppliers", {**rec, "id": rid, "created": old.get("created")})
        return {**saved, "sanity_warnings": self.warnings(saved)}

    def _capacity_in_use(self, rid: str) -> bool:
        if self.store.find("allocations", supplier_id=rid):
            return True
        return any(o.get("plan") for o in self.store.find("incoming_offers", supplier_id=rid))

    def in_use(self, rid: str) -> bool:
        return bool(
            self.store.find("incoming_offers", supplier_id=rid)
            or self.store.find("allocations", supplier_id=rid)
            or self.store.find("expenses", supplier_id=rid)
        )

    def delete(self, rid: str) -> None:
        if not (ID_RE.match(rid) and self.store.get("suppliers", rid)):
            raise ServiceError(404, "Not found")
        with self.store.transaction():
            if self.in_use(rid):
                raise ServiceError(409, IN_USE)
            self.store.delete("suppliers", rid)

    # ------------------------------------------------------------------ vendor adoption
    def _unlinked(self) -> list[dict]:
        return [
            e
            for e in self.store.list("expenses")
            if not e.get("supplier_id") and isinstance(e.get("vendor"), str) and e["vendor"].strip()
        ]

    def vendor_suggestions(self) -> dict:
        expenses = self._unlinked()
        by_key: dict[str, list[dict]] = defaultdict(list)
        for e in expenses:
            key = vendor_key(e["vendor"])
            if key:
                by_key[key].append(e)
        suppliers: dict[str, list[dict]] = defaultdict(list)
        for s in sorted(self.store.list("suppliers"), key=_sort_key):
            suppliers[vendor_key(str(s.get("name") or ""))].append(s)
        groups = []
        for key, rows in by_key.items():
            counts: dict[str, int] = defaultdict(int)
            for e in rows:
                counts[e["vendor"]] += 1
            variants = sorted(counts, key=lambda t: (-counts[t], t))
            best = (suppliers.get(key) or [None])[0]
            groups.append(
                dict(
                    key=key,
                    variants=variants,
                    count=len(rows),
                    net_total=float(q2(sum((dec(e.get("net")) for e in rows), dec(0)))),
                    supplier={"id": best["id"], "name": best.get("name")} if best else None,
                )
            )
        groups.sort(key=lambda g: (-g["count"], g["key"]))
        return dict(groups=groups, unlinked_expenses=len(expenses))

    def adopt_vendors(self, items: list[dict], dry_run: bool) -> dict:
        """Link expenses (``supplier_id`` null, ``vendor`` equal to one of the texts) to a supplier, creating it on
        request. ``vendor`` itself is never touched. A real run is one transaction."""
        for item in items:
            sid = item.get("supplier_id")
            if sid and not (ID_RE.match(sid) and self.store.get("suppliers", sid)):
                raise ServiceError(404, "Supplier not found")
        claimed: set[str] = set()
        plan = []
        for item in items:
            texts = set(item["vendors"])
            ids = [e["id"] for e in self._unlinked() if e["vendor"] in texts and e["id"] not in claimed]
            claimed.update(ids)
            plan.append((item, ids))
        out_items = []
        changed = 0

        def run() -> None:
            nonlocal changed
            for item, ids in plan:
                sid = item.get("supplier_id")
                creating = item.get("create") is not None
                created = False
                if ids and creating and not dry_run:
                    sid = self.store.save("suppliers", self.prepare(item["create"]))["id"]
                    created = True
                if ids and not dry_run:
                    for eid in ids:
                        exp = self.store.get("expenses", eid)
                        if exp:
                            self.store.save("expenses", {**exp, "supplier_id": sid})
                changed += len(ids)
                out_items.append(
                    dict(
                        vendors=item["vendors"],
                        supplier_id=sid if (sid or not creating) else None,
                        supplier_created=created,
                        would_create=bool(creating and ids),
                        expense_ids=ids,
                        count=len(ids),
                    )
                )

        if dry_run:
            run()
        else:
            with self.store.transaction():
                run()
        return dict(dry_run=dry_run, items=out_items, expenses_changed=changed)
