"""API tokens: creation, lookup, revocation (docs/contracts/api-tokens.md).

Only ``sha256(token)`` is stored. The plaintext leaves this module once, in the return value of ``create``; it is never
logged and never kept. A token is a credential of its owner: the principal it produces is the owner's user record plus
``_auth`` (see ``permita38/core/rights.py::scope_for``), and the owner's role and ``active`` flag are read from the
database on every request."""

import base64
import hashlib
import hmac
import re
import secrets
import time

from permita38.core import rights
from permita38.core.errors import ServiceError
from permita38.core.models import API_TOKEN_MAX_ACTIVE
from permita38.core.repository import Repository

PREFIX = "pat_"
TOKEN_RE = re.compile(r"pat_[A-Za-z0-9_-]{43}")
LAST_USED_INTERVAL = 60  # seconds: `last_used_at` is written at most this often per token
NOT_FOUND = "Token not found"


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_secret() -> str:
    return PREFIX + base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def status_of(row: dict, now: int) -> str:
    if row.get("revoked_at"):
        return "revoked"  # revoked wins over expired
    return "expired" if int(row.get("expires_at") or 0) <= now else "active"


def view(row: dict, now: int) -> dict:
    """The public shape of a token: never the hash, never the secret."""
    return {
        "id": row["id"],
        "user_id": row.get("user_id"),
        "label": row.get("label"),
        "hint": row.get("hint"),
        "read_only": bool(row.get("read_only", True)),
        "created": row.get("created"),
        "expires_at": row.get("expires_at"),
        "last_used_at": row.get("last_used_at"),
        "revoked_at": row.get("revoked_at"),
        "status": status_of(row, now),
    }


def revoke_user_tokens(store: Repository, user_id: str, now: int | None = None) -> int:
    """Revoke every active token of a user (deactivation, revoke-all); returns how many were active. Expired tokens
    are left alone: they stay ``expired``."""
    now = int(now if now is not None else time.time())
    count = 0
    with store.transaction():
        for row in store.find("api_tokens", user_id=user_id):
            if status_of(row, now) == "active":
                store.save("api_tokens", {**row, "revoked_at": now})
                count += 1
    return count


class TokenService:
    def __init__(self, store: Repository):
        self.store = store

    # ---------- authentication ----------
    def authenticate(self, bearer: str) -> dict | None:
        """The principal for a bearer value starting with ``pat_``, or None for any kind of failure (malformed,
        unknown, revoked, expired, owner missing or inactive). Malformed values are rejected without a lookup."""
        if not TOKEN_RE.fullmatch(bearer):
            return None
        digest = hash_token(bearer)
        rows = self.store.find("api_tokens", token_hash=digest)
        row = rows[0] if rows else None
        if row is None or not hmac.compare_digest(str(row.get("token_hash", "")), digest):
            return None
        now = int(time.time())
        if status_of(row, now) != "active":
            return None
        owner = self.store.get("users", row.get("user_id") or "")
        if not owner or not owner.get("active", True):
            return None
        self._touch(row["id"], now)
        return {
            **owner,
            "role": rights.normalize_role(owner.get("role")),
            "_auth": {"kind": "token", "token_id": row["id"], "read_only": bool(row.get("read_only", True))},
        }

    def _touch(self, token_id: str, now: int) -> None:
        with self.store.transaction():  # re-read inside the lock so a concurrent revoke is never overwritten
            row = self.store.get("api_tokens", token_id)
            if row and not row.get("revoked_at") and now - int(row.get("last_used_at") or 0) >= LAST_USED_INTERVAL:
                self.store.save("api_tokens", {**row, "last_used_at": now}, touch=False)

    def details(self, token_id: str) -> dict | None:
        row = self.store.get("api_tokens", token_id)
        return view(row, int(time.time())) if row else None

    # ---------- management ----------
    def create(self, user: dict, label: str, expires_in_days: int, read_only: bool) -> tuple[str, dict]:
        now = int(time.time())
        secret = new_secret()
        with self.store.transaction():
            mine = self.store.find("api_tokens", user_id=user["id"])
            if sum(1 for r in mine if status_of(r, now) == "active") >= API_TOKEN_MAX_ACTIVE:
                raise ServiceError(409, f"Too many active tokens (maximum {API_TOKEN_MAX_ACTIVE})")
            token_id = secrets.token_hex(4)  # 8 hex characters, as the contract says (the generic generator has 12)
            while self.store.get("api_tokens", token_id):
                token_id = secrets.token_hex(4)
            row = self.store.save(
                "api_tokens",
                {
                    "id": token_id,
                    "user_id": user["id"],
                    "label": label,
                    "token_hash": hash_token(secret),
                    "hint": secret[: len(PREFIX) + 4],
                    "read_only": read_only,
                    "expires_at": now + expires_in_days * 86400,
                },
            )
        return secret, view(row, now)

    def _can_see(self, me: dict, row: dict) -> bool:
        return row.get("user_id") == me["id"] or rights.normalize_role(me.get("role")) == rights.BOSS

    def _visible(self, me: dict, token_id: str) -> dict:
        row = self.store.get("api_tokens", token_id)
        if not row or not self._can_see(me, row):
            raise ServiceError(404, NOT_FOUND)  # the same answer for "unknown" and "not yours": no existence leak
        return row

    def list(self, me: dict, all_users: bool = False) -> list[dict]:
        now = int(time.time())
        if all_users:
            if rights.normalize_role(me.get("role")) != rights.BOSS:
                raise ServiceError(403, "Forbidden")
            rows = self.store.list("api_tokens")
            names = {u["id"]: u.get("name") or u.get("username") for u in self.store.list("users")}
        else:
            rows = self.store.find("api_tokens", user_id=me["id"])
            names = {}
        rows.sort(key=lambda r: (int(r.get("created") or 0), r["id"]), reverse=True)
        out = []
        for r in rows:
            item = view(r, now)
            if all_users:
                item["owner_name"] = names.get(r.get("user_id"))
            out.append(item)
        return out

    def revoke(self, me: dict, token_id: str) -> dict:
        with self.store.transaction():
            row = self._visible(me, token_id)
            if not row.get("revoked_at"):  # idempotent: the first revocation time stays
                row = self.store.save("api_tokens", {**row, "revoked_at": int(time.time())})
        return view(row, int(time.time()))

    def revoke_all(self, me: dict, user_id: str | None = None) -> int:
        target = user_id or me["id"]
        if target != me["id"] and rights.normalize_role(me.get("role")) != rights.BOSS:
            raise ServiceError(403, "Forbidden")
        return revoke_user_tokens(self.store, target)

    def audit_target(self, me: dict, token_id: str) -> str:
        """Id of a token whose audit log ``me`` may read (owner or boss), else 404."""
        return self._visible(me, token_id)["id"]
