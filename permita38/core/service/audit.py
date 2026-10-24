"""Append-only audit log of requests authenticated by an API token (docs/contracts/api-tokens.md).

Only ``append`` and ``list`` exist for callers. Rows are removed in exactly two places: the startup prune of rows older
than ``RETENTION_DAYS`` and the cascade when the owner is deleted (``AuthService.delete_user``). A row holds the token
id, the owner, the method, the route *template* and the status: no body, no query string, no token, no record id."""

import time

from permita38.core.repository import Repository

RETENTION_DAYS = 365
UNMATCHED_ROUTE = "(unmatched)"
PUBLIC_FIELDS = ("id", "at", "token_id", "user_id", "method", "route", "status")


class AuditService:
    def __init__(self, store: Repository):
        self.store = store

    def append(self, token_id: str, user_id: str, method: str, route: str, status: int) -> None:
        self.store.save(
            "audit_log",
            {
                "at": int(time.time()),
                "token_id": token_id,
                "user_id": user_id,
                "method": method[:16],
                "route": route[:300],
                "status": int(status),
            },
        )

    def list(self, token_id: str, limit: int = 50, before: int | None = None) -> list[dict]:
        """Newest first; ``before`` keeps rows with ``at < before``."""
        rows = self.store.find("audit_log", token_id=token_id)
        if before is not None:
            rows = [r for r in rows if int(r.get("at") or 0) < before]
        rows.sort(key=lambda r: (int(r.get("at") or 0), int(r.get("created") or 0), r["id"]), reverse=True)
        return [{k: r.get(k) for k in PUBLIC_FIELDS} for r in rows[:limit]]

    def prune(self, now: int | None = None) -> int:
        """Delete rows older than the retention period (called at startup); returns how many."""
        cutoff = int(now if now is not None else time.time()) - RETENTION_DAYS * 86400
        with self.store.transaction():
            old = [r["id"] for r in self.store.list("audit_log") if int(r.get("at") or 0) < cutoff]
            for rid in old:
                self.store.delete("audit_log", rid)
        return len(old)
