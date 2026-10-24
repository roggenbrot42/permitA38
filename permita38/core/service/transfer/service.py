"""Export/import service: one operation at a time, transfer log, audit line. The router and the CLI call only this."""

import logging
import threading
import time
from datetime import date
from pathlib import Path

from permita38 import __version__
from permita38.core.database import head_id
from permita38.core.errors import ServiceError
from permita38.core.repository import Repository

from . import export, importer, limits, manifest

audit = logging.getLogger("permita38.audit")
_LOCK = threading.Lock()
BUSY = "Another export or import is running."


class _Single:
    def __enter__(self) -> None:
        if not _LOCK.acquire(blocking=False):
            raise ServiceError(409, BUSY)

    def __exit__(self, *exc: object) -> None:
        _LOCK.release()


def clean_stale_tmp(tmp_dir: Path, max_age: float = 3600) -> None:
    """Remove export/import temp files left behind by a crash."""
    if not tmp_dir.is_dir():
        return
    for p in tmp_dir.glob("permita38-*"):
        try:
            if p.is_file() and time.time() - p.stat().st_mtime > max_age:
                p.unlink()
        except OSError:
            pass


class TransferService:
    def __init__(self, store: Repository, tmp_dir: Path, *, allow_no_users: bool = False):
        self.store = store
        self.tmp_dir = tmp_dir
        self.allow_no_users = allow_no_users

    # ------------------------------------------------------------------ log
    def _log(self, user_id: str | None, action: str, ok: bool, include_accounts: bool | None, counts: dict, sha: str | None) -> None:
        entry = {
            "at": int(time.time()),
            "user_id": user_id,
            "action": action,
            "ok": ok,
            "include_accounts": include_accounts,
            "counts": counts,
            "archive_sha256": sha,
        }
        try:
            st = self.store.settings()
            st[manifest.LOG_KEY] = [entry, *list(st.get(manifest.LOG_KEY) or [])][: limits.MAX_LOG]
            self.store.save_settings(st)
        except Exception:  # the log must never break the operation
            audit.exception("could not write the transfer log")
        audit.info("data transfer: %s ok=%s user=%s", action, ok, user_id)

    def transfers(self) -> list[dict]:
        return list(self.store.settings().get(manifest.LOG_KEY) or [])[: limits.MAX_LOG]

    # ------------------------------------------------------------------ info
    def info(self) -> dict:
        counts = {c: len(self.store.list(c)) for c in manifest.exported_collections()}
        files, size = export.attachment_stats(self.store)
        reason = importer.instance_state(self.store, allow_no_users=self.allow_no_users)
        return {
            "counts": counts,
            "attachments": {"files": files, "bytes": size},
            "estimated_bytes": export.estimate(self.store, counts, size),
            "app_version": __version__,
            "schema_head": head_id(),
            "import": {"possible": reason is None, "reason": reason},
        }

    # ------------------------------------------------------------------ export
    def export(self, user_id: str | None, opts: export.ExportOptions) -> export.ExportResult:
        action = "export_master_data" if opts.scope == "master_data" else "export"
        with _Single():
            try:
                result = export.build(self.store, opts, self.tmp_dir)
            except BaseException:
                self._log(user_id, action, False, None, {}, None)
                raise
            self._log(
                user_id,
                action,
                True,
                bool(result.manifest.get("include_accounts")),
                result.counts,
                result.sha256,
            )
            return result

    # ------------------------------------------------------------------ import
    def preview(self, user_id: str | None, path: Path, today: date | None = None) -> dict:
        with _Single():
            plan = importer.validate(path, self.store, allow_no_users=self.allow_no_users, today=today)
            self._log(user_id, "import_preview", plan.ok, _accounts(plan), plan.counts(), None)
            return plan.preview()

    def run(self, user_id: str | None, path: Path, today: date | None = None) -> dict:
        with _Single():
            plan = importer.validate(path, self.store, allow_no_users=self.allow_no_users, today=today)
            if not plan.ok:
                self._log(user_id, "import", False, _accounts(plan), {}, None)
                if plan.empty_reason and len(plan.errors) == 1:
                    raise ServiceError(409, plan.empty_reason)
                raise ImportRefused(plan)
            try:
                res = importer.apply(path, self.store, plan)
            except BaseException:
                self._log(user_id, "import", False, _accounts(plan), {}, None)
                raise
            self._log(user_id, "import", True, _accounts(plan), plan.counts(), None)
            return {
                "imported": True,
                "counts": plan.counts(),
                "attachments": plan.attachment_summary(),
                "relogin": res["relogin"],
                "users": plan.users,
                "numbering": plan.numbering,
                "warnings": [w.as_dict() for w in plan.warnings[: limits.MAX_WARNINGS]],
            }


class ImportRefused(Exception):
    """The archive has errors; nothing was written. Carries the plan for the 422 answer."""

    def __init__(self, plan: importer.Plan):
        super().__init__("not imported")
        self.plan = plan

    def body(self) -> dict:
        errors = [e.as_dict() for e in self.plan.errors[: limits.MAX_ERRORS]]
        n = len(self.plan.errors)
        return {"detail": f"The archive was not imported: {n} error{'s' if n != 1 else ''}.", "errors": errors}


def _accounts(plan: importer.Plan) -> bool | None:
    return bool(plan.manifest.get("include_accounts")) if plan.manifest else None
