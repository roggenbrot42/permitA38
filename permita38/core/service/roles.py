"""Roles and rights: resolving what a user may do, and defining roles."""

from collections.abc import Iterable, Mapping

from permita38.core import rights as R
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository

# the two rights that together make somebody able to administer access; at least one active user must keep them
ADMIN_RIGHTS = frozenset({"roles.manage", "team.manage"})


class Principal(dict):
    """The authenticated user: the stored user record plus the rights they hold right now.

    The rights live in an attribute, not in the dict, so copying the record (``{**me, ...}``) and saving it can never
    write them into the database.
    """

    rights: frozenset[str] = frozenset()


def rights_of_principal(me: Mapping) -> frozenset[str]:
    return getattr(me, "rights", frozenset())


def can(me: Mapping, right: str) -> bool:
    """True if the authenticated user (as produced by `AuthService.principal`) holds `right`."""
    return right in rights_of_principal(me)


def can_any(me: Mapping, rights: Iterable[str]) -> bool:
    held = rights_of_principal(me)
    return any(r in held for r in rights)


def require(me: Mapping, *rights: str) -> None:
    """Raise 403 unless the user holds at least one of `rights`."""
    if not can_any(me, rights):
        raise ServiceError(403, "Forbidden")


class RoleService:
    def __init__(self, store: Repository):
        self.store = store

    # ------------------------------------------------------------------ definitions
    def definitions(self) -> dict[str, dict]:
        """All roles by id. The built-in roles always come from the code (they are locked), whatever the database says."""
        defs = {r["id"]: r for r in self.store.list("roles")}
        for d in R.SYSTEM_ROLES:
            row = defs.get(d.id, {})
            defs[d.id] = {
                **row,
                "id": d.id,
                "name": d.name,
                "description": d.description,
                "rights": list(d.rights),
                "system": True,
            }
        return defs

    def list_roles(self) -> list[dict]:
        defs = self.definitions()
        return sorted(defs.values(), key=lambda r: (not r.get("system"), str(r.get("name", "")).lower()))

    def get(self, rid: str) -> dict:
        role = self.definitions().get(rid)
        if not role:
            raise ServiceError(404, "Role not found")
        return role

    # ------------------------------------------------------------------ resolution
    @staticmethod
    def role_ids_of(user: Mapping) -> list[str]:
        ids = user.get("role_ids")
        if isinstance(ids, list):
            return [i for i in ids if isinstance(i, str)]
        return [R.BOSS_ID] if user.get("role") == "boss" else [R.MEMBER_ID]  # users from before roles existed

    def rights_of(self, user: Mapping, defs: Mapping[str, dict] | None = None) -> frozenset[str]:
        defs = defs if defs is not None else self.definitions()
        held: set[str] = set()
        for rid in self.role_ids_of(user):
            held.update(defs.get(rid, {}).get("rights") or ())
        return R.expand(held)

    def role_refs(self, user: Mapping, defs: Mapping[str, dict] | None = None) -> list[dict]:
        defs = defs if defs is not None else self.definitions()
        return [{"id": rid, "name": defs[rid]["name"]} for rid in self.role_ids_of(user) if rid in defs]

    def compat_role(self, user: Mapping, defs: Mapping[str, dict] | None = None) -> str:
        """The old single `role` string: 'boss' for somebody who may do everything, else the first role's name."""
        defs = defs if defs is not None else self.definitions()
        if self.rights_of(user, defs) >= R.ALL_RIGHTS:
            return "boss"
        refs = self.role_refs(user, defs)
        return refs[0]["name"] if refs else "none"

    def decorate(self, public: dict, user: Mapping) -> dict:
        """Add the computed `roles`, `rights` and compatible `role` to a public user record."""
        defs = self.definitions()
        return {
            **public,
            "role_ids": self.role_ids_of(user),
            "roles": self.role_refs(user, defs),
            "rights": sorted(self.rights_of(user, defs)),
            "role": self.compat_role(user, defs),
        }

    def principal(self, user: dict) -> Principal:
        """The authenticated user plus the set of rights they hold right now (rights are evaluated on every request)."""
        p = Principal(user)
        p.rights = self.rights_of(user)
        return p

    # ------------------------------------------------------------------ the last administrator
    def ensure_admin_remains(
        self, users: Iterable[Mapping] | None = None, defs: Mapping[str, dict] | None = None
    ) -> None:
        users = list(users) if users is not None else self.store.list("users")
        defs = defs if defs is not None else self.definitions()
        for u in users:
            if u.get("active", True) and ADMIN_RIGHTS <= self.rights_of(u, defs):
                return
        raise ServiceError(400, "At least one active user must keep the rights to manage roles and the team")

    # ------------------------------------------------------------------ role administration
    def _validate(self, data: dict, own_id: str | None) -> dict:
        name = data["name"].strip()
        if not name:
            raise ServiceError(422, "A role needs a name")
        clash = [
            r for r in self.definitions().values() if r["id"] != own_id and r["name"].strip().lower() == name.lower()
        ]
        if clash:
            raise ServiceError(409, f"A role named '{clash[0]['name']}' already exists")
        unknown = sorted(set(data.get("rights") or ()) - R.ALL_RIGHTS)
        if unknown:
            raise ServiceError(422, "Unknown right(s): " + ", ".join(unknown))
        return {
            "name": name,
            "description": (data.get("description") or "").strip() or None,
            "rights": sorted(set(data.get("rights") or ())),
            "system": False,
        }

    def create(self, data: dict) -> dict:
        rec = self._validate(data, None)
        return self.store.save("roles", {k: v for k, v in rec.items() if v is not None})

    def update(self, rid: str, data: dict) -> dict:
        role = self.get(rid)
        if role.get("system"):
            raise ServiceError(403, "This role is built in and cannot be changed")
        rec = self._validate(data, rid)
        merged = {**(self.store.get("roles", rid) or {}), **rec}
        merged = {k: v for k, v in merged.items() if v is not None}
        defs = self.definitions()
        defs[rid] = {**merged, "id": rid}
        self.ensure_admin_remains(defs=defs)  # raises before anything is written
        if not rec["description"]:
            merged.pop("description", None)
        return self.store.save("roles", merged)

    def delete(self, rid: str) -> None:
        role = self.get(rid)
        if role.get("system"):
            raise ServiceError(403, "This role is built in and cannot be deleted")
        users = [u for u in self.store.list("users") if rid in self.role_ids_of(u)]
        if users:
            raise ServiceError(409, f"The role is still assigned to {len(users)} user(s); remove it from them first")
        self.store.delete("roles", rid)
