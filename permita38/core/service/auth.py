"""Users, passwords and signed session tokens (HS256, stdlib only)."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from permita38.core import rights as R
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository
from permita38.core.settings import DEFAULT_SETTINGS

from .roles import Principal, RoleService, can, rights_of_principal
from .throttle import AttemptThrottle

PBKDF2_ITERATIONS = 200_000
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
MIN_PASSWORD_LENGTH = 8
# never leave the server: credentials and second-factor state
_PRIVATE = ("password_hash", "pw_changed", "totp_secret", "totp_last_step", "totp_recovery")


def hash_password(password: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${dk.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    try:
        _, iters, salt, digest = (stored or "").split("$")
        dk = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(dk.hex(), digest)
    except ValueError:
        return False


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def public_user(u: dict) -> dict:
    out = {k: v for k, v in u.items() if k not in _PRIVATE}
    out["has_password"] = bool(u.get("password_hash"))
    out["totp_enabled"] = bool(u.get("totp_enabled"))
    return out


def next_session_version(user: dict | None = None) -> int:
    """New value of `pw_changed` (the session version): strictly greater than the old one, so every session issued
    before a password / 2FA change ends, even if the change happens in the same second as the login."""
    return max(int(time.time()), int((user or {}).get("pw_changed") or 0) + 1)


def validate_password(password: str):
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ServiceError(400, f"Password must be at least {MIN_PASSWORD_LENGTH} characters long")


def normalize_username(username: str) -> str:
    username = (username or "").strip().lower()
    if not USERNAME_RE.match(username):
        raise ServiceError(400, "Username: 2-32 characters, letters, digits, dot, dash or underscore")
    return username


class AuthService:
    def __init__(self, store: Repository, secret_key: str, token_ttl_hours: int = 12):
        self.store = store
        self.secret = secret_key.encode()
        self.ttl = token_ttl_hours * 3600
        self.throttle = AttemptThrottle()
        self.roles = RoleService(store)

    # ---------- users as seen by the API ----------
    def public(self, user: dict) -> dict:
        """The user record for responses: no secrets, plus the computed roles and rights."""
        return self.roles.decorate(public_user(user), user)

    def principal(self, user: dict) -> Principal:
        """The authenticated user together with the rights they hold now (see `RoleService.principal`)."""
        return self.roles.principal(user)

    # ---------- tokens ----------
    def _sign(self, signing_input: str) -> str:
        return _b64(hmac.new(self.secret, signing_input.encode(), hashlib.sha256).digest())

    def create_token(self, user: dict) -> str:
        now = int(time.time())
        head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        claims = {
            "sub": user["id"],
            "iat": now,
            "exp": now + self.ttl,
            "use": "session",
            "pwv": int(user.get("pw_changed") or 0),
        }
        body = _b64(json.dumps(claims).encode())
        return f"{head}.{body}.{self._sign(head + '.' + body)}"

    def user_from_token(self, token: str | None) -> dict | None:
        try:
            head, body, sig = (token or "").split(".")
            if not hmac.compare_digest(sig, self._sign(f"{head}.{body}")):
                return None
            claims = json.loads(_unb64(body))
            if claims["exp"] < time.time() or claims.get("use", "session") != "session":
                return None  # expired, or not a session token (e.g. the two-factor challenge)
            user = self.store.get("users", claims["sub"])
        except (ValueError, KeyError, TypeError):
            return None
        if not user or not user.get("active", True):
            return None
        if "pwv" in claims:
            if claims["pwv"] != int(user.get("pw_changed") or 0):
                return None  # password / 2FA changed after this token was issued
        elif claims["iat"] < int(user.get("pw_changed") or 0):
            return None  # token without a version claim (older release)
        return user

    # ---------- login ----------
    def needs_setup(self) -> bool:
        return not self.store.list("users")

    def check_credentials(self, username: str, password: str) -> dict:
        """First factor: username + password, throttled. Returns the user record or raises 401/429."""
        key = (username or "").strip().lower()
        self.throttle.check(key)
        user = next((u for u in self.store.list("users") if u.get("username") == key), None)
        stored = user.get("password_hash") if user else None
        ok = verify_password(password, stored or hash_password("x", "00" * 16))  # constant-ish time
        if not (user and stored and ok and user.get("active", True)):
            self.throttle.fail(key)
            raise ServiceError(401, "Wrong username or password")
        self.throttle.clear(key)
        return user

    def login(self, username: str, password: str) -> tuple[str, dict]:
        """Session for a user WITHOUT two-factor authentication. Users with 2FA must go through TotpService."""
        user = self.check_credentials(username, password)
        if user.get("totp_enabled"):
            raise ServiceError(401, "Two-factor code required")
        return self.create_token(user), self.public(user)

    def setup(self, username: str, name: str, password: str, company_name: str = "", weekly_hours: float = 40):
        if not self.needs_setup():
            raise ServiceError(400, "Already set up")
        if not name.strip():
            raise ServiceError(400, "Name is required")
        username = normalize_username(username)
        validate_password(password)
        self.store.save_settings(DEFAULT_SETTINGS | {"company_name": company_name, "owner_name": name.strip()})
        return self.public(
            self.store.save(
                "users",
                dict(
                    username=username,
                    name=name.strip(),
                    role="boss",
                    role_ids=[R.BOSS_ID],
                    weekly_hours=weekly_hours,
                    active=True,
                    workdays=[0, 1, 2, 3, 4],
                    color="#2563eb",
                    password_hash=hash_password(password),
                    pw_changed=next_session_version(),
                ),
            )
        )

    # ---------- user management ----------
    def list_users(self) -> list[dict]:
        return [self.public(u) for u in self.store.list("users")]

    def _target_role_ids(self, data: dict, old: dict | None) -> list[str]:
        ids = data.pop("role_ids", None)
        legacy = data.pop("role", None)
        if ids is None:
            if legacy is not None:
                ids = [R.BOSS_ID if legacy == "boss" else R.MEMBER_ID]
            elif old is not None:
                ids = self.roles.role_ids_of(old)
            else:
                ids = [R.MEMBER_ID]
        ids = list(dict.fromkeys(ids))
        defs = self.roles.definitions()
        unknown = [i for i in ids if i not in defs]
        if unknown:
            raise ServiceError(422, "Unknown role: " + ", ".join(unknown))
        return ids

    def _check_may_manage(self, actor: dict | None, target: dict, new_rights: frozenset[str] | None = None):
        """Nobody can use user administration to rise above their own rights.

        Unless the actor may define roles (administrator level: they could grant themselves anything anyway):
        * a user who holds rights the actor does not hold cannot be edited, deleted or have their 2FA reset, and
        * roles that would give the user (or anybody) rights the actor does not hold cannot be assigned.
        """
        if actor is None or can(actor, "roles.manage"):
            return
        mine = rights_of_principal(actor)
        if not self.roles.rights_of(target) <= mine:
            raise ServiceError(403, "You cannot manage a user who holds rights you do not have")
        if new_rights is not None and not new_rights <= mine:
            raise ServiceError(403, "You cannot grant rights you do not hold")

    def save_user(self, data: dict, actor: dict | None = None) -> dict:
        old = self.store.get("users", data["id"]) if data.get("id") else None
        if data.get("id") and not old:
            raise ServiceError(404, "User not found")
        password = data.pop("password", None)
        data["username"] = normalize_username(data["username"])
        role_ids = self._target_role_ids(data, old)
        if any(u.get("username") == data["username"] and u["id"] != data.get("id") for u in self.store.list("users")):
            raise ServiceError(400, "This username is already taken")
        defs = self.roles.definitions()
        new_rights = self.roles.rights_of({"role_ids": role_ids}, defs)
        self._check_may_manage(actor, old or {"role_ids": []}, new_rights)
        rec = {**(old or {}), **data, "role_ids": role_ids, "role": "boss" if R.BOSS_ID in role_ids else "member"}
        if password:
            validate_password(password)
            rec["password_hash"], rec["pw_changed"] = hash_password(password), next_session_version(old)
        elif not old:
            raise ServiceError(400, "A password is required for new users")
        if old:  # changing roles or deactivating must not remove the last administrator
            others = [u for u in self.store.list("users") if u["id"] != old["id"]]
            self.roles.ensure_admin_remains([*others, rec], defs)
        return self.public(self.store.save("users", rec))

    def delete_user(self, user_id: str, actor: dict):
        old = self.store.get("users", user_id)
        if not old:
            return
        if user_id == actor["id"]:
            raise ServiceError(400, "You cannot delete yourself")
        self._check_may_manage(actor, old)
        self.roles.ensure_admin_remains([u for u in self.store.list("users") if u["id"] != user_id])
        self.store.delete("users", user_id)

    def change_password(self, user: dict, old_password: str, new_password: str) -> str:
        if not verify_password(old_password, user.get("password_hash")):
            raise ServiceError(400, "Current password is wrong")
        validate_password(new_password)
        rec = {**user, "password_hash": hash_password(new_password), "pw_changed": next_session_version(user)}
        saved = self.store.save("users", rec)
        return self.create_token(saved)  # other sessions are invalidated, this one continues

    def set_password(self, username: str, new_password: str):
        """Recovery path used by the CLI (`permita38 set-password`)."""
        validate_password(new_password)
        user = next((u for u in self.store.list("users") if u.get("username") == username.strip().lower()), None)
        if not user:
            raise ServiceError(404, "No such user")
        self.store.save(
            "users", {**user, "password_hash": hash_password(new_password), "pw_changed": next_session_version(user)}
        )
