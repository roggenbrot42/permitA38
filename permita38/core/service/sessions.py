"""Server-side sessions and personal API tokens.

Every sign-in creates a row in the ``sessions`` table; its random id is the ``jti`` claim of the signed session token.
A request is only accepted while the row exists, is not revoked, has not expired and (optionally) has not been idle for
too long. That makes "sign out", "sign out everywhere" and "revoke this device" real: they take effect on the very next
request, even though the token itself is stateless.

API tokens (``p38_<id>_<secret>``) live in the same table with ``kind='api'``. Only the SHA-256 of the secret is
stored; the secret is shown once at creation and has 192 bits of entropy, so a plain hash is sufficient.

Alpha.1 tokens (no ``jti``) keep working for one token lifetime after the first start of this version (the *legacy
window*), then never again. A marker row records when sessions were first enforced.

The table is part of the (possibly synced) database, so sessions created on one device are visible on the others. Each
device signs tokens with its own secret key, so a token only works on the device that issued it; revoking a session on
any device marks the row revoked for all of them.
"""

import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass

from permita38.core.config import SessionsConfig
from permita38.core.netinfo import ClientInfo
from permita38.core.repository import Repository

SESSION, API, MARKER = "session", "api", "marker"
MARKER_ID = "sessionsmarker"
TOKEN_PREFIX = "p38_"
CACHE_TTL = 5.0  # seconds a "this session is fine" answer is reused (revocations in this process drop it at once)
LAST_SEEN_INTERVAL = 300  # seconds between last_seen writes (every 60 s when an idle timeout is configured)
PURGE_INTERVAL = 3600
KEEP_REVOKED_SESSIONS = 7 * 86400
KEEP_EXPIRED_SESSIONS = 86400
KEEP_FINISHED_TOKENS = 30 * 86400


@dataclass
class AuthContext:
    """Who is calling and how they authenticated."""

    user: dict
    kind: str  # 'session' | 'api'
    session_id: str | None = None
    scopes: list[str] | None = None  # API tokens only
    token_name: str | None = None
    legacy: bool = False  # alpha.1 token without a session row


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def parse_api_token(token: str | None) -> tuple[str, str] | None:
    """'p38_<id>_<secret>' -> (id, secret), or None when the shape is wrong."""
    if not token or not token.startswith(TOKEN_PREFIX) or len(token) > 200:
        return None
    parts = token[len(TOKEN_PREFIX) :].split("_")
    if len(parts) != 2 or not parts[0].isalnum() or not parts[1].isalnum():
        return None
    return parts[0], parts[1]


class SessionService:
    def __init__(self, store: Repository, config: SessionsConfig | None = None, ttl_seconds: int = 12 * 3600):
        self.store = store
        self.cfg = config or SessionsConfig()
        self.ttl = ttl_seconds
        self._lock = threading.RLock()
        self._cache: dict[str, tuple[dict, float]] = {}
        self._last_purge = 0.0

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def now() -> float:
        return time.time()

    def _drop_cache(self, sid: str | None = None) -> None:
        with self._lock:
            if sid is None:
                self._cache.clear()
            else:
                self._cache.pop(sid, None)

    def _save(self, rec: dict) -> dict:
        saved = self.store.save("sessions", rec, touch=False)
        self._drop_cache(saved["id"])
        return saved

    # ------------------------------------------------------------------ legacy window
    def ensure_marker(self) -> dict:
        rec = self.store.get("sessions", MARKER_ID)
        if rec is None:
            rec = self.store.save("sessions", {"id": MARKER_ID, "kind": MARKER, "created": int(self.now())})
        return rec

    def legacy_token_ok(self, issued_at: int) -> bool:
        """An alpha.1 token (no jti) is accepted only if it predates sessions and the first-start window is open."""
        marker = self.ensure_marker()
        started = int(marker.get("created") or 0)
        return issued_at <= started and self.now() < started + self.ttl

    # ------------------------------------------------------------------ sessions
    def create(self, user: dict, client: ClientInfo | None = None) -> dict:
        now = int(self.now())
        rec = {
            "id": secrets.token_hex(16),
            "created": now,
            "user_id": user["id"],
            "kind": SESSION,
            "user_agent": (client.user_agent if client else "")[:200] or None,
            "ip_hint": client.ip_hint if client else None,
            "expires": now + self.ttl,
            "last_seen": now,
        }
        saved = self.store.save("sessions", {k: v for k, v in rec.items() if v is not None}, touch=False)
        self._enforce_limit(user["id"])
        self._maybe_purge()
        return saved

    def _enforce_limit(self, user_id: str) -> None:
        live = sorted(
            (r for r in self._user_rows(user_id, SESSION) if self._is_live(r)), key=lambda r: r.get("created", 0)
        )
        for r in live[: max(0, len(live) - self.cfg.max_per_user)]:
            self.revoke(r["id"])

    def _user_rows(self, user_id: str, kind: str | None = None) -> list[dict]:
        return [
            r
            for r in self.store.list("sessions")
            if r.get("user_id") == user_id and r.get("kind") != MARKER and (kind is None or r.get("kind") == kind)
        ]

    def _is_live(self, rec: dict, now: float | None = None) -> bool:
        now = self.now() if now is None else now
        if rec.get("revoked_at") or rec.get("expires", 0) <= now:
            return False
        idle = self.cfg.idle_timeout_minutes
        return not (idle and rec.get("kind") == SESSION and rec.get("last_seen", 0) + idle * 60 < now)

    def get_live(self, sid: str, kind: str = SESSION) -> dict | None:
        """The active session row for ``sid`` (cached for a few seconds), else None. Records activity."""
        now = self.now()
        with self._lock:
            hit = self._cache.get(sid)
            if hit and now - hit[1] < CACHE_TTL and self._is_live(hit[0], now):
                return hit[0] if hit[0].get("kind") == kind else None
        rec = self.store.get("sessions", sid)
        if rec is None or rec.get("kind") != kind or not self._is_live(rec, now):
            self._drop_cache(sid)
            return None
        rec = self._touch(rec, now)
        with self._lock:
            self._cache[sid] = (rec, now)
        return rec

    def _touch(self, rec: dict, now: float) -> dict:
        interval = 60 if self.cfg.idle_timeout_minutes else LAST_SEEN_INTERVAL
        if now - rec.get("last_seen", 0) < interval:
            return rec
        return self.store.save("sessions", {**rec, "last_seen": int(now)}, touch=False)

    def revoke(self, sid: str) -> bool:
        rec = self.store.get("sessions", sid)
        if rec is None or rec.get("kind") == MARKER or rec.get("revoked_at"):
            self._drop_cache(sid)
            return False
        self._save({**rec, "revoked_at": int(self.now())})
        return True

    def revoke_user(self, user_id: str, keep: str | None = None, include_api: bool = False) -> int:
        """Revoke the user's sessions (and optionally API tokens); ``keep`` spares one session id."""
        n = 0
        for r in self._user_rows(user_id):
            if r["id"] == keep or r.get("revoked_at"):
                continue
            if r.get("kind") == API and not include_api:
                continue
            n += self.revoke(r["id"])
        return n

    def list_for_user(self, user_id: str, kind: str = SESSION, active_only: bool = True) -> list[dict]:
        now = self.now()
        rows = [r for r in self._user_rows(user_id, kind) if not active_only or self._is_live(r, now)]
        return sorted(rows, key=lambda r: (r.get("last_seen", 0), r.get("created", 0)), reverse=True)

    # ------------------------------------------------------------------ API tokens
    def create_api_token(
        self, user: dict, name: str, scopes: list[str], days: int, client: ClientInfo | None = None
    ) -> tuple[dict, str]:
        now = int(self.now())
        tid, secret = secrets.token_hex(8), secrets.token_hex(24)
        rec = {
            "id": tid,
            "created": now,
            "user_id": user["id"],
            "kind": API,
            "name": name,
            "ip_hint": client.ip_hint if client else None,
            "expires": now + days * 86400,
            "token_hash": hash_secret(secret),
            "scopes": scopes,
        }
        saved = self.store.save("sessions", {k: v for k, v in rec.items() if v is not None}, touch=False)
        self._maybe_purge()
        return saved, f"{TOKEN_PREFIX}{tid}_{secret}"

    def authenticate_api_token(self, token: str) -> dict | None:
        parsed = parse_api_token(token)
        if parsed is None:
            return None
        tid, secret = parsed
        rec = self.store.get("sessions", tid)
        # compare against a dummy hash when the id is unknown so the timing does not reveal valid ids
        stored = (rec or {}).get("token_hash") or hash_secret("x")
        ok = hmac.compare_digest(hash_secret(secret), stored)
        now = self.now()
        if not (rec and ok and rec.get("kind") == API and self._is_live(rec, now)):
            return None
        return self._touch_token(rec, now)

    def _touch_token(self, rec: dict, now: float) -> dict:
        if now - rec.get("last_seen", 0) < LAST_SEEN_INTERVAL:
            return rec
        return self.store.save("sessions", {**rec, "last_seen": int(now)}, touch=False)

    # ------------------------------------------------------------------ housekeeping
    def _maybe_purge(self) -> None:
        now = self.now()
        if now - self._last_purge < PURGE_INTERVAL:
            return
        self._last_purge = now
        self.purge(now)

    def purge(self, now: float | None = None) -> int:
        now = self.now() if now is None else now
        removed = 0
        for r in self.store.list("sessions"):
            if r.get("kind") == MARKER:
                continue
            finished = r.get("revoked_at") or r.get("expires", 0)
            keep = KEEP_FINISHED_TOKENS if r.get("kind") == API else None
            if r.get("revoked_at"):
                keep = keep or KEEP_REVOKED_SESSIONS
            else:
                keep = keep or KEEP_EXPIRED_SESSIONS
            if finished and finished < now and now - finished > keep and not self._is_live(r, now):
                removed += self.store.delete("sessions", r["id"])
        return removed
