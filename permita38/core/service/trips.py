"""Business trips (docs/specs/business-trips.md): saving, figures, finalising and the trip statement.

Rights: the area ``trips`` (``core/rights.py``). A LIMITED scope sees and changes only its own trips; anything else
is 404 (a trip of another person is not revealed). Figures are computed here on every save and stored with the trip,
so the statement, the finance report and the preview use the same numbers. A final trip is locked until the boss
reopens it.
"""

from typing import Any

from permita38.core import rights, trip_statement, trips_calc
from permita38.core.errors import ServiceError
from permita38.core.repository import ID_RE, Repository
from permita38.core.rights import Scope
from permita38.core.trip_rates import RateTableMissing, load_checklist, load_rates

#: stored figures: the request keys that are never taken from the client
_COMPUTED = ("allowance_total", "mileage_total", "rules_version", "days")
_RESPONSE_ONLY = ("mileage_km", "mileage_rate")


def _valid_id(rid: str) -> bool:
    return isinstance(rid, str) and bool(ID_RE.match(rid))


class TripService:
    def __init__(self, store: Repository):
        self.store = store

    # -- visibility -------------------------------------------------------------------------------------------
    @staticmethod
    def _scope(me: dict, op: rights.Op) -> Scope:
        return rights.scope(me.get("role"), "trips", op)

    def _visible(self, me: dict, trip: dict, op: rights.Op = "read") -> bool:
        sc = self._scope(me, op)
        if sc == Scope.ALL:
            return True
        return sc == Scope.LIMITED and trip.get("user_id") == me["id"]

    def _get(self, me: dict, rid: str, op: rights.Op = "read") -> dict:
        trip = self.store.get("trips", rid) if _valid_id(rid) else None
        if not trip or not self._visible(me, trip, op):
            raise ServiceError(404, "Not found")
        return trip

    # -- reads ----------------------------------------------------------------------------------------------------
    def list(self, me: dict) -> list[dict]:
        if self._scope(me, "read") == Scope.NONE:
            raise ServiceError(403, "Forbidden")
        return [t for t in self.store.list("trips") if self._visible(me, t)]

    def get(self, me: dict, rid: str) -> dict:
        if self._scope(me, "read") == Scope.NONE:
            raise ServiceError(403, "Forbidden")
        return self._get(me, rid)

    # -- figures ------------------------------------------------------------------------------------------------------
    @staticmethod
    def _figures(body: dict) -> dict[str, Any]:
        try:
            out = trips_calc.compute(body, load_rates)
        except RateTableMissing as exc:
            raise ServiceError(422, str(exc)) from None
        except ValueError as exc:
            raise ServiceError(422, str(exc)) from None
        return out

    def _check_refs(self, body: dict) -> None:
        if body.get("customer_id") and not self.store.get("customers", body["customer_id"]):
            raise ServiceError(422, "Unknown customer")
        if body.get("project_id") and not self.store.get("projects", body["project_id"]):
            raise ServiceError(422, "Unknown project")
        known = {item["id"] for item in load_checklist()}
        for item in body.get("not_needed") or []:
            if item not in known:
                raise ServiceError(422, f"Unknown checklist item: {item}")

    def preview(self, me: dict, body: dict) -> dict:
        """Figures for a trip body, nothing is saved (the same computation a save uses)."""
        if self._scope(me, "read") == Scope.NONE:
            raise ServiceError(403, "Forbidden")
        self._check_refs(body)
        return self._figures(body)

    def _prepare(self, body: dict, owner: str, old: dict | None) -> dict:
        data = {k: v for k, v in body.items() if k not in ("id", "created", "updated", *_COMPUTED, *_RESPONSE_ONLY)}
        data["user_id"] = owner
        self._check_refs(data)
        figures = self._figures(data)
        data.update(
            allowance_total=figures["allowance_total"],
            mileage_total=figures["mileage_total"],
            rules_version=figures["rules_version"],
            days=figures["days"],
        )
        data.setdefault("status", "draft")
        if old is not None:
            data["created"] = old.get("created")
        return {k: v for k, v in data.items() if v is not None}

    # -- writes ---------------------------------------------------------------------------------------------------------
    def create(self, me: dict, body: dict) -> dict:
        if self._scope(me, "write") == Scope.NONE:
            raise ServiceError(403, "Forbidden")
        return self.store.save("trips", self._prepare(body, me["id"], None))

    def update(self, me: dict, rid: str, body: dict) -> dict:
        old = self._get(me, rid, "write")
        if old.get("status") == "final":
            raise ServiceError(409, "This trip is final; the boss can reopen it")
        data = self._prepare(body, old.get("user_id") or me["id"], old)
        return self.store.save("trips", {**data, "id": rid})

    def delete(self, me: dict, rid: str) -> None:
        old = self._get(me, rid, "write")
        if old.get("status") == "final":
            raise ServiceError(409, "This trip is final; reopen it before deleting")
        with self.store.transaction():
            for bill in self.store.find("expenses", trip_id=rid):  # the bills stay, unlinked from the trip
                bill.pop("trip_id", None)
                self.store.save("expenses", bill)
            self.store.delete("trips", rid)

    def reopen(self, me: dict, rid: str) -> dict:
        """Boss only (the route requires the full write scope): a final trip becomes a draft again."""
        old = self._get(me, rid, "write")
        if old.get("status") != "final":
            raise ServiceError(409, "Only a final trip can be reopened")
        return self.store.save("trips", {**old, "status": "draft"})

    # -- statement ------------------------------------------------------------------------------------------------------
    def statement(self, me: dict, rid: str) -> tuple[bytes, str]:
        trip = self._get(me, rid)
        owner = self.store.get("users", trip.get("user_id") or "") or {}
        settings = self.store.settings()
        bills = self.store.find("expenses", trip_id=rid)
        figures = {
            "days": trip.get("days") or [],
            "allowance_total": trip.get("allowance_total") or 0,
            "mileage_total": trip.get("mileage_total") or 0,
            "mileage_km": sum(float(m.get("km") or 0) for m in trip.get("mileage") or []),
            "rules_version": trip.get("rules_version"),
        }
        data = trip_statement.render(
            company=settings.get("company_name") or "",
            person=owner.get("name") or "",
            trip=trip,
            figures=figures,
            bills=bills,
        )
        name = f"trip-{rid}.pdf"
        return data, name
