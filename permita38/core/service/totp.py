"""TOTP (RFC 6238) two-factor authentication on top of the AuthService.

The cryptography is plain standard library: HMAC-SHA1 (RFC 4226 HOTP), 6 digits, 30 s steps, 160-bit base32 secrets.
Accepted window: +-1 step. Every accepted step is remembered per user (``totp_last_step``) so a code can never be used
twice (replay protection). Recovery codes are random 64-bit values, stored only as salted SHA-256 hashes, single use.

The secret is stored in the database. The database may be shared between devices (file sync), so it cannot be
encrypted with the per-device ``secret_key``; protecting the database file at rest is the operator's job.
"""

import base64
import hashlib
import hmac
import json
import logging
import secrets
import struct
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

import segno

from permita38.core.errors import ServiceError

from .auth import AuthService, _b64, _unb64, next_session_version, public_user, verify_password

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1  # accepted steps before/after the current one
SECRET_BYTES = 20  # 160 bit
RECOVERY_CODE_COUNT = 10
MFA_TOKEN_TTL = 300  # seconds
ISSUER = "permitA38"

audit = logging.getLogger("permita38.audit")


# ---------------------------------------------------------------------- RFC 4226 / 6238 primitives
def generate_secret() -> str:
    return base64.b32encode(secrets.token_bytes(SECRET_BYTES)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    secret = secret.replace(" ", "").upper()
    return base64.b32decode(secret + "=" * (-len(secret) % 8))


def hotp(secret: str, counter: int, digits: int = DIGITS) -> str:
    mac = hmac.new(_key(secret), struct.pack(">Q", counter), hashlib.sha1).digest()
    offset = mac[-1] & 0x0F
    value = struct.unpack(">I", mac[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def time_step(now: float, step: int = STEP_SECONDS) -> int:
    return int(now // step)


def totp(secret: str, now: float, digits: int = DIGITS) -> str:
    return hotp(secret, time_step(now), digits)


def match_step(secret: str, code: str, now: float, last_step: int | None = None, window: int = WINDOW) -> int | None:
    """Time step the code is valid for (within +-window and newer than ``last_step``), else None.

    All candidates are compared in constant time and without early exit.
    """
    code = "".join(code.split())
    current = time_step(now)
    found: int | None = None
    for step in range(current - window, current + window + 1):
        ok = hmac.compare_digest(hotp(secret, step).encode(), code.encode())
        if ok and step > (last_step if last_step is not None else -1):
            found = step if found is None else max(found, step)
    return found


def otpauth_uri(secret: str, account: str, issuer: str = ISSUER) -> str:
    label = quote(f"{issuer}:{account}", safe="")
    return f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer, safe='')}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


def qr_data_uri(text: str) -> str:
    """The QR code as an inline SVG data URI (generated locally, nothing leaves the server)."""
    return segno.make(text, error="m").svg_data_uri(scale=5, border=2, dark="#000000", light="#ffffff")


# ---------------------------------------------------------------------- recovery codes
def new_recovery_code() -> str:
    h = secrets.token_hex(8)  # 64 bit
    return "-".join(h[i : i + 4] for i in range(0, 16, 4))


def normalize_recovery_code(code: str) -> str:
    return "".join(c for c in code.lower() if c.isalnum())


def hash_recovery_code(code: str, salt: str | None = None) -> str:
    salt = salt or secrets.token_hex(8)
    digest = hashlib.sha256((salt + normalize_recovery_code(code)).encode()).hexdigest()
    return f"{salt}${digest}"


def match_recovery_code(code: str, hashes: list[str]) -> int | None:
    """Index of the hash matching the code (compares every hash, constant time per hash)."""
    found: int | None = None
    norm = normalize_recovery_code(code)
    for i, stored in enumerate(hashes):
        salt, _, digest = stored.partition("$")
        cand = hashlib.sha256((salt + norm).encode()).hexdigest()
        if hmac.compare_digest(cand.encode(), digest.encode()) and found is None:
            found = i
    return found


@dataclass
class LoginStep:
    """Outcome of the first login factor."""

    token: str | None = None
    user: dict | None = None
    mfa_token: str | None = None

    @property
    def mfa_required(self) -> bool:
        return self.mfa_token is not None


class TotpService:
    def __init__(self, auth: AuthService):
        self.auth = auth
        self.store = auth.store
        self._lock = threading.RLock()
        self._used_mfa: dict[str, float] = {}  # jti -> expiry, so a challenge completes only once

    # ---------- helpers
    def _user(self, user_id: str) -> dict:
        u = self.store.get("users", user_id)
        if not u:
            raise ServiceError(404, "User not found")
        return u

    def _ukey(self, user: dict) -> str:
        return f"totp:{user['id']}"

    def _pwkey(self, user: dict) -> str:
        return f"pw:{user['id']}"

    def _require_password(self, user: dict, password: str):
        keys = (self._pwkey(user),)
        self.auth.throttle.check(*keys)
        if not verify_password(password or "", user.get("password_hash")):
            self.auth.throttle.fail(*keys)
            raise ServiceError(401, "Wrong password")
        self.auth.throttle.clear(*keys)

    def _save(self, user: dict, **changes) -> dict:
        return self.store.save("users", {**user, **changes})

    def _bump(self, user: dict, **changes) -> dict:
        """Save and invalidate every session issued before now (like a password change)."""
        return self._save(user, pw_changed=next_session_version(user), **changes)

    def _check_code(self, user: dict, code: str | None, recovery_code: str | None, *, allow_recovery: bool) -> str:
        """Verify a TOTP or recovery code. Returns 'totp' or 'recovery'. Persists replay state. Throttled."""
        key = self._ukey(user)
        with self._lock:
            self.auth.throttle.check(key)
            fresh = self._user(user["id"])  # re-read inside the lock: replay state must be current
            if code and fresh.get("totp_secret"):
                step = match_step(fresh["totp_secret"], code, time.time(), fresh.get("totp_last_step"))
                if step is not None:
                    self._save(fresh, totp_last_step=step)
                    self.auth.throttle.clear(key)
                    return "totp"
            if allow_recovery and recovery_code:
                hashes = list(fresh.get("totp_recovery") or [])
                idx = match_recovery_code(recovery_code, hashes)
                if idx is not None:
                    del hashes[idx]
                    self._save(fresh, totp_recovery=hashes)
                    self.auth.throttle.clear(key)
                    audit.warning("recovery code used for user %s (%d left)", fresh["username"], len(hashes))
                    return "recovery"
            self.auth.throttle.fail(key)
        raise ServiceError(401, "Wrong or expired code")

    # ---------- status / enrolment
    def status(self, user: dict) -> dict:
        u = self._user(user["id"])
        return {
            "enabled": bool(u.get("totp_enabled")),
            "pending_setup": bool(u.get("totp_secret")) and not u.get("totp_enabled"),
            "recovery_codes_left": len(u.get("totp_recovery") or []) if u.get("totp_enabled") else 0,
        }

    def begin_setup(self, user: dict, password: str) -> dict:
        self._require_password(user, password)
        with self._lock:
            u = self._user(user["id"])
            if u.get("totp_enabled"):
                raise ServiceError(400, "Two-factor authentication is already enabled. Disable it first.")
            secret = generate_secret()
            self._save(u, totp_secret=secret, totp_enabled=False, totp_last_step=None, totp_recovery=[])
        uri = otpauth_uri(secret, u["username"])
        return {
            "secret": secret,
            "otpauth_uri": uri,
            "qr_svg": qr_data_uri(uri),
            "issuer": ISSUER,
            "account": u["username"],
        }

    def enable(self, user: dict, code: str) -> tuple[str, list[str]]:
        key = self._ukey(user)
        with self._lock:
            self.auth.throttle.check(key)
            u = self._user(user["id"])
            if u.get("totp_enabled"):
                raise ServiceError(400, "Two-factor authentication is already enabled")
            if not u.get("totp_secret"):
                raise ServiceError(400, "Start the setup first")
            step = match_step(u["totp_secret"], code or "", time.time(), None)
            if step is None:
                self.auth.throttle.fail(key)
                raise ServiceError(400, "That code is not correct. Check the clock of your phone and try again.")
            self.auth.throttle.clear(key)
            codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
            saved = self._bump(
                u, totp_enabled=True, totp_last_step=step, totp_recovery=[hash_recovery_code(c) for c in codes]
            )
        audit.info("two-factor authentication enabled for user %s", u["username"])
        return self.auth.create_token(saved), codes

    def disable(self, user: dict, password: str, code: str | None, recovery_code: str | None) -> str:
        self._require_password(user, password)
        u = self._user(user["id"])
        if not u.get("totp_enabled") and not u.get("totp_secret"):
            raise ServiceError(400, "Two-factor authentication is not enabled")
        if u.get("totp_enabled"):
            self._check_code(u, code, recovery_code, allow_recovery=True)
        with self._lock:
            saved = self._bump(
                self._user(u["id"]), totp_secret=None, totp_enabled=False, totp_last_step=None, totp_recovery=[]
            )
        audit.info("two-factor authentication disabled for user %s", u["username"])
        return self.auth.create_token(saved)

    def regenerate_recovery_codes(self, user: dict, password: str, code: str) -> list[str]:
        self._require_password(user, password)
        u = self._user(user["id"])
        if not u.get("totp_enabled"):
            raise ServiceError(400, "Two-factor authentication is not enabled")
        self._check_code(u, code, None, allow_recovery=False)
        codes = [new_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        with self._lock:
            self._save(self._user(u["id"]), totp_recovery=[hash_recovery_code(c) for c in codes])
        audit.info("recovery codes regenerated for user %s", u["username"])
        return codes

    # ---------- administrative reset
    def reset_for(self, actor: dict, target_id: str, actor_password: str) -> dict:
        self._require_password(actor, actor_password)
        target = self._user(target_id)
        with self._lock:
            saved = self._bump(
                self._user(target["id"]), totp_secret=None, totp_enabled=False, totp_last_step=None, totp_recovery=[]
            )
        audit.warning("two-factor authentication of user %s reset by %s", target["username"], actor["username"])
        return public_user(saved)

    def reset_by_username(self, username: str) -> None:
        """Lock-out recovery from the command line."""
        user = next((u for u in self.store.list("users") if u.get("username") == username.strip().lower()), None)
        if not user:
            raise ServiceError(404, "No such user")
        self._bump(user, totp_secret=None, totp_enabled=False, totp_last_step=None, totp_recovery=[])

    # ---------- two-step login
    def _mfa_token(self, user: dict) -> str:
        now = int(time.time())
        head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        claims = {
            "sub": user["id"],
            "iat": now,
            "exp": now + MFA_TOKEN_TTL,
            "use": "mfa",
            "jti": secrets.token_hex(8),
            "pwv": int(user.get("pw_changed") or 0),
        }
        body = _b64(json.dumps(claims).encode())
        return f"{head}.{body}.{self.auth._sign(head + '.' + body)}"

    def login(self, username: str, password: str) -> LoginStep:
        """First factor. A correct password of a 2FA user yields a short-lived challenge instead of a session."""
        user = self.auth.check_credentials(username, password)
        if user.get("totp_enabled"):
            return LoginStep(mfa_token=self._mfa_token(user))
        return LoginStep(token=self.auth.create_token(user), user=public_user(user))

    def complete_login(
        self, mfa_token: str, code: str | None, recovery_code: str | None
    ) -> tuple[str, dict, int | None]:
        """Second factor. Returns (session token, user, recovery codes left if a recovery code was used)."""
        claims = self._read_mfa_token(mfa_token)
        user = self.store.get("users", claims["sub"])
        if not user or not user.get("active", True) or claims["pwv"] != int(user.get("pw_changed") or 0):
            raise ServiceError(401, "The sign-in expired. Please start again.")
        if not user.get("totp_enabled"):
            raise ServiceError(401, "The sign-in expired. Please start again.")
        with self._lock:
            now = time.time()
            self._used_mfa = {j: e for j, e in self._used_mfa.items() if e > now}
            if claims["jti"] in self._used_mfa:
                raise ServiceError(401, "The sign-in expired. Please start again.")
        how = self._check_code(user, code, recovery_code, allow_recovery=True)
        with self._lock:
            self._used_mfa[claims["jti"]] = claims["exp"]
        fresh = self._user(user["id"])
        left = len(fresh.get("totp_recovery") or []) if how == "recovery" else None
        return self.auth.create_token(fresh), public_user(fresh), left

    def _read_mfa_token(self, token: str | None) -> dict:
        try:
            head, body, sig = (token or "").split(".")
            if not hmac.compare_digest(sig, self.auth._sign(f"{head}.{body}")):
                raise ValueError
            claims = json.loads(_unb64(body))
            if claims.get("use") != "mfa" or claims["exp"] < time.time():
                raise ValueError
            if not all(
                isinstance(claims.get(k), str if k in ("sub", "jti") else int) for k in ("sub", "iat", "jti", "pwv")
            ):
                raise ValueError
            return claims
        except (ValueError, KeyError, TypeError):
            raise ServiceError(401, "The sign-in expired. Please start again.") from None
