"""Users, passwords and signed session tokens (HS256, stdlib only)."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time

from permita38.core.config import SessionsConfig, ThrottleConfig
from permita38.core.errors import ServiceError
from permita38.core.netinfo import ClientInfo
from permita38.core.repository import Repository
from permita38.core.settings import DEFAULT_SETTINGS

from .clientctx import get_client
from .sessions import TOKEN_PREFIX, AuthContext, SessionService
from .throttle import AttemptThrottle, RateLimiter

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
    def __init__(
        self,
        store: Repository,
        secret_key: str,
        token_ttl_hours: int = 12,
        sessions_config: SessionsConfig | None = None,
        throttle_config: ThrottleConfig | None = None,
    ):
        self.store = store
        self.secret = secret_key.encode()
        self.ttl = token_ttl_hours * 3600
        tc = throttle_config or ThrottleConfig()
        self.throttle = AttemptThrottle(tc.account_max_failures, tc.account_window_seconds)  # per account
        self.ip_throttle = AttemptThrottle(  # per client address (IPv6: /64), exponential back-off on repeated bursts
            tc.ip_max_failures, tc.ip_window_seconds, backoff=True, max_lock=tc.ip_backoff_max_seconds
        )
        self.unauth_limiter = RateLimiter(tc.unauth_requests_per_minute)  # request rate on the sign-in endpoints
        self.token_limiter = RateLimiter(tc.api_token_requests_per_minute)  # request rate per API token
        self.sessions = SessionService(store, sessions_config, self.ttl)

    # ---------- throttling (account keys plus the client address of the current request)
    def guard(self, *keys: str) -> None:
        """Refuse (429 + Retry-After) while any account key or the caller's address is locked out."""
        self.throttle.check(*keys)
        client = get_client()
        if client:
            self.ip_throttle.check(client.ip_key)

    def record_failure(self, *keys: str) -> None:
        self.throttle.fail(*keys)
        client = get_client()
        if client:
            self.ip_throttle.fail(client.ip_key)

    def record_success(self, *keys: str) -> None:
        """Forgive the account keys. The address keeps its failures (an attacker must not reset them by logging in)."""
        self.throttle.clear(*keys)

    # ---------- tokens ----------
    def _sign(self, signing_input: str) -> str:
        return _b64(hmac.new(self.secret, signing_input.encode(), hashlib.sha256).digest())

    def create_token(self, user: dict, client: ClientInfo | None = None) -> str:
        """A new session for ``user``: a row in the sessions table plus the signed token that points at it."""
        session = self.sessions.create(user, client or get_client())
        now = int(session["created"])
        head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        claims = {
            "sub": user["id"],
            "iat": now,
            "exp": int(session["expires"]),
            "use": "session",
            "pwv": int(user.get("pw_changed") or 0),
            "jti": session["id"],
        }
        body = _b64(json.dumps(claims).encode())
        return f"{head}.{body}.{self._sign(head + '.' + body)}"

    def _claims(self, token: str) -> dict | None:
        """Signature-checked claims of a session token (expiry NOT checked), or None."""
        try:
            head, body, sig = token.split(".")
            if not hmac.compare_digest(sig, self._sign(f"{head}.{body}")):
                return None
            claims = json.loads(_unb64(body))
            return claims if isinstance(claims, dict) else None
        except (ValueError, TypeError):
            return None

    def authenticate(self, token: str | None, *, allow_api: bool = True) -> AuthContext | None:
        """Who is behind this credential? A session token (signed, not revoked, not expired) or an API token."""
        if not token:
            return None
        if token.startswith(TOKEN_PREFIX):
            if not allow_api:
                return None
            rec = self.sessions.authenticate_api_token(token)
            user = self.store.get("users", rec["user_id"]) if rec else None
            if not rec or not user or not user.get("active", True):
                return None
            return AuthContext(user, "api", rec["id"], list(rec.get("scopes") or []), rec.get("name"))
        claims = self._claims(token)
        try:
            if not claims or claims["exp"] < time.time() or claims.get("use", "session") != "session":
                return None  # expired, or not a session token (e.g. the two-factor challenge)
            user = self.store.get("users", claims["sub"])
            jti = claims.get("jti")
            legacy = jti is None
            if legacy:  # issued by alpha.1, before server-side sessions existed
                if not self.sessions.legacy_token_ok(int(claims["iat"])):
                    return None
            else:
                row = self.sessions.get_live(jti) if isinstance(jti, str) else None
                if row is None or row.get("user_id") != claims["sub"]:
                    return None  # unknown, revoked, expired or idle
        except (KeyError, TypeError, ValueError):
            return None
        if not user or not user.get("active", True):
            return None
        if "pwv" in claims:
            if claims["pwv"] != int(user.get("pw_changed") or 0):
                return None  # password / 2FA changed after this token was issued
        elif claims["iat"] < int(user.get("pw_changed") or 0):
            return None  # token without a version claim (older release)
        return AuthContext(user, "session", None if legacy else jti, legacy=legacy)

    def user_from_token(self, token: str | None) -> dict | None:
        ctx = self.authenticate(token)
        return ctx.user if ctx else None

    def logout(self, token: str | None) -> None:
        """Revoke the session behind ``token`` (best effort: unknown or foreign tokens are ignored)."""
        claims = self._claims(token or "")
        jti = claims.get("jti") if claims else None
        if isinstance(jti, str) and claims and claims.get("use", "session") == "session":
            self.sessions.revoke(jti)

    # ---------- login ----------
    def needs_setup(self) -> bool:
        return not self.store.list("users")

    def check_credentials(self, username: str, password: str) -> dict:
        """First factor: username + password, throttled. Returns the user record or raises 401/429."""
        key = (username or "").strip().lower()
        self.guard(key)
        user = next((u for u in self.store.list("users") if u.get("username") == key), None)
        stored = user.get("password_hash") if user else None
        ok = verify_password(password, stored or hash_password("x", "00" * 16))  # constant-ish time
        if not (user and stored and ok and user.get("active", True)):
            self.record_failure(key)
            raise ServiceError(401, "Wrong username or password")
        self.record_success(key)
        return user

    def login(self, username: str, password: str) -> tuple[str, dict]:
        """Session for a user WITHOUT two-factor authentication. Users with 2FA must go through TotpService."""
        user = self.check_credentials(username, password)
        if user.get("totp_enabled"):
            raise ServiceError(401, "Two-factor code required")
        return self.create_token(user), public_user(user)

    def setup(self, username: str, name: str, password: str, company_name: str = "", weekly_hours: float = 40):
        if not self.needs_setup():
            raise ServiceError(400, "Already set up")
        if not name.strip():
            raise ServiceError(400, "Name is required")
        username = normalize_username(username)
        validate_password(password)
        self.store.save_settings(DEFAULT_SETTINGS | {"company_name": company_name, "owner_name": name.strip()})
        return public_user(
            self.store.save(
                "users",
                dict(
                    username=username,
                    name=name.strip(),
                    role="boss",
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
        return [public_user(u) for u in self.store.list("users")]

    def _boss_count(self, excluding: str | None = None) -> int:
        return len(
            [
                u
                for u in self.store.list("users")
                if u["role"] == "boss" and u["id"] != excluding and u.get("active", True)
            ]
        )

    def save_user(self, data: dict) -> dict:
        old = self.store.get("users", data["id"]) if data.get("id") else None
        if data.get("id") and not old:
            raise ServiceError(404, "User not found")
        password = data.pop("password", None)
        data["username"] = normalize_username(data["username"])
        if data.get("role") not in ("boss", "member"):
            raise ServiceError(400, "Role must be boss or member")
        if any(u.get("username") == data["username"] and u["id"] != data.get("id") for u in self.store.list("users")):
            raise ServiceError(400, "This username is already taken")
        rec = {**(old or {}), **data}
        if password:
            validate_password(password)
            rec["password_hash"], rec["pw_changed"] = hash_password(password), next_session_version(old)
        elif not old:
            raise ServiceError(400, "A password is required for new users")
        if old and old["role"] == "boss" and (rec["role"] != "boss" or not rec.get("active", True)):
            if self._boss_count(excluding=old["id"]) == 0:
                raise ServiceError(400, "There must be at least one active boss")
        saved = self.store.save("users", rec)
        if old:  # access changed: end the sessions that were issued under the old settings
            deactivated = old.get("active", True) and not rec.get("active", True)
            if password or deactivated or old.get("role") != rec.get("role"):
                self.sessions.revoke_user(saved["id"], include_api=deactivated)
        return public_user(saved)

    def delete_user(self, user_id: str, actor: dict):
        old = self.store.get("users", user_id)
        if not old:
            return
        if user_id == actor["id"]:
            raise ServiceError(400, "You cannot delete yourself")
        if old["role"] == "boss" and self._boss_count(excluding=user_id) == 0:
            raise ServiceError(400, "Cannot delete the only boss")
        self.sessions.revoke_user(user_id, include_api=True)
        self.store.delete("users", user_id)

    def change_password(self, user: dict, old_password: str, new_password: str) -> str:
        key = f"pw:{user['id']}"
        self.guard(key)  # the current password is guessable with a stolen session: throttle it like a login
        if not verify_password(old_password, user.get("password_hash")):
            self.record_failure(key)
            raise ServiceError(400, "Current password is wrong")
        self.record_success(key)
        validate_password(new_password)
        rec = {**user, "password_hash": hash_password(new_password), "pw_changed": next_session_version(user)}
        saved = self.store.save("users", rec)
        self.sessions.revoke_user(saved["id"])  # every sign-in ends (API tokens are kept) ...
        return self.create_token(saved)  # ... and this one continues with a fresh session

    def set_password(self, username: str, new_password: str):
        """Recovery path used by the CLI (`permita38 set-password`)."""
        validate_password(new_password)
        user = next((u for u in self.store.list("users") if u.get("username") == username.strip().lower()), None)
        if not user:
            raise ServiceError(404, "No such user")
        self.store.save(
            "users", {**user, "password_hash": hash_password(new_password), "pw_changed": next_session_version(user)}
        )
        self.sessions.revoke_user(user["id"])
