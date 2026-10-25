"""Validate an export archive completely, then write it into an empty instance in one transaction."""

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, cast

from permita38.core.database import list_migrations
from permita38.core.errors import ServiceError
from permita38.core.filetypes import INLINE_TYPES, magic_ok
from permita38.core.numbering import PatternError, parse_pattern, period_key
from permita38.core.repository import Repository
from permita38.core.repository.base import ID_RE
from permita38.core.repository.schema import BOOL, FLOAT, ID, INT, JSON, TABLES
from permita38.core.rights import ROLES
from permita38.core.service.numbering import NumberingService
from permita38.core.settings import effective_settings

from . import limits, manifest
from .archive import ArchiveError, ArchiveReader, classify, parse_json
from .upgrades import upgrade

log = logging.getLogger("permita38.audit")

#: reference column -> collection it points to. A guard test fails when schema.py gets an id column missing here.
REFERENCES: dict[str, str] = {
    "customer_id": "customers",
    "project_id": "projects",
    "task_id": "tasks",
    "user_id": "users",
    "invoice_id": "invoices",
    "offer_id": "offers",
    "trip_id": "trips",
    "source_id": "calendar_sources",
    "contract_id": "contracts",
    # allocations of subcontractors (their collections arrive with that feature; until then the values are dangling)
    "supplier_id": "suppliers",
    "incoming_offer_id": "incoming_offers",
}
LIST_REFERENCES: dict[str, tuple[str, str]] = {  # (collection, column) -> target collection
    "projects.member_ids": ("projects", "users"),
    "contracts.user_ids": ("contracts", "users"),
}
#: attachment names referenced by a record
ATTACHMENT_COLUMNS = (("expenses", "attachment"), ("invoices", "original_pdf"), ("offers", "original_pdf"))
SHA_RE = re.compile(r"[0-9a-f]{64}")
#: what core/service/auth.hash_password produces
PASSWORD_HASH_RE = re.compile(r"pbkdf2_sha256\$[0-9]{1,8}\$(?:[0-9a-f]{2}){1,64}\$(?:[0-9a-f]{2}){16,128}")
MAX_ITERATIONS = 10_000_000


@dataclass
class Item:
    code: str
    message: str
    file: str | None = None

    def as_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "file": self.file}


@dataclass
class Plan:
    errors: list[Item] = field(default_factory=list)
    warnings: list[Item] = field(default_factory=list)
    manifest: dict | None = None
    compatibility: dict = field(default_factory=lambda: {"status": "unknown", "message": None})
    records: dict[str, list[dict]] = field(default_factory=dict)
    settings: dict = field(default_factory=dict)
    attachments: dict[str, int] = field(default_factory=dict)  # name -> bytes
    attachment_sha: dict[str, str] = field(default_factory=dict)  # name -> sha256
    allow_no_users: bool = False
    users: dict | None = None
    numbering: dict | None = None
    empty_reason: str | None = None

    def error(self, code: str, message: str, file: str | None = None) -> None:
        self.errors.append(Item(code, message, file))

    def warn(self, code: str, message: str, file: str | None = None) -> None:
        self.warnings.append(Item(code, message, file))

    @property
    def ok(self) -> bool:
        return not self.errors

    def counts(self) -> dict[str, int]:
        return {c: len(self.records.get(c, [])) for c in manifest.exported_collections()} if self.records else {}

    def attachment_summary(self) -> dict:
        return {"files": len(self.attachments), "bytes": sum(self.attachments.values())}

    def preview(self) -> dict:
        errors = [e.as_dict() for e in self.errors[: limits.MAX_ERRORS]]
        warnings = [w.as_dict() for w in self.warnings[: limits.MAX_WARNINGS]]
        out = {
            "ok": self.ok,
            "manifest": _manifest_summary(self.manifest) if self.manifest else None,
            "compatibility": self.compatibility,
            "counts": self.counts() if self.ok else {},
            "attachments": self.attachment_summary() if self.ok else {"files": 0, "bytes": 0},
            "users": self.users if self.ok else None,
            "numbering": self.numbering if self.ok else None,
            "warnings": warnings,
            "errors": errors,
        }
        if len(self.errors) > limits.MAX_ERRORS or len(self.warnings) > limits.MAX_WARNINGS:
            out["truncated"] = True
        return out


def _manifest_summary(m: dict) -> dict:
    keys = (
        "format",
        "format_version",
        "scope",
        "app_version",
        "schema_head",
        "created",
        "created_epoch",
        "include_accounts",
        "include_attachments",
    )
    return {k: m.get(k) for k in keys if k in m}


# ------------------------------------------------------------------ empty-instance precondition
def instance_state(store: Repository, *, allow_no_users: bool) -> str | None:
    """None when the instance is empty in the sense of spec decision 8, else the reason (409 text)."""
    found: list[str] = []
    for name in TABLES:
        if name in ("users", "settings"):
            continue
        n = len(store.list(name))
        if n:
            found.append(f"{name}: {n}")
    users = store.list("users")
    if len(users) > 1:
        found.append(f"users: {len(users)}")
    elif len(users) == 1 and users[0].get("role") != "boss":
        found.append("users: 1 (not a boss)")
    if not users and not allow_no_users:
        found.append("users: 0 (run the first-run setup first)")
    files = len(store.list_attachments())
    if files:
        found.append(f"attachments: {files}")
    return f"This instance already contains data ({', '.join(found)})." if found else None


# ------------------------------------------------------------------ validation
def _check_type(col_type: str, value: Any) -> bool:
    if col_type == BOOL:
        return isinstance(value, bool)
    if col_type == INT:
        return isinstance(value, int) and not isinstance(value, bool) and abs(value) < 2**63
    if col_type == FLOAT:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
            and abs(value) <= limits.MAX_FLOAT
        )
    if col_type == JSON:
        return True
    return isinstance(value, str)


def _validate_records(plan: Plan, coll: str, name: str, body: Any) -> list[dict]:
    if not isinstance(body, dict) or not isinstance(body.get("records"), list):
        plan.error("json_invalid", f"{name} must be an object with a list 'records'.", name)
        return []
    records = body["records"]
    if body.get("collection") != coll or body.get("count") != len(records):
        plan.error("json_invalid", f"{name}: 'collection' or 'count' does not match the content.", name)
    if len(records) > limits.MAX_RECORDS:
        plan.error("limit_exceeded", f"{name} has more than {limits.MAX_RECORDS} records.", name)
        return []
    table = TABLES[coll]
    seen: set[str] = set()
    out: list[dict] = []
    for i, rec in enumerate(records):
        where = f"{name} record {i}"
        if len(plan.errors) >= limits.MAX_ERRORS:
            break
        if not isinstance(rec, dict):
            plan.error("record_invalid", f"{where} is not an object.", name)
            continue
        rid = rec.get("id")
        if not isinstance(rid, str) or not ID_RE.match(rid):
            plan.error("record_invalid", f"{where} has no valid id.", name)
            continue
        if rid in seen:
            plan.error("duplicate_id", f"{where}: id {rid} appears twice.", name)
            continue
        seen.add(rid)
        if len(json.dumps(rec, ensure_ascii=False)) > limits.MAX_RECORD_BYTES:
            plan.error("limit_exceeded", f"{where} is larger than 1 MiB.", name)
            continue
        bad = next((c.name for c in table.columns if c.name in rec and not _check_type(c.type, rec[c.name])), None)
        if bad is None and any(k == "extra" for k in rec):
            bad = "extra"
        if bad is not None:
            plan.error("record_invalid", f"{where}: field {bad!r} has the wrong type.", name)
            continue
        out.append(rec)
    return out


def _check_semantics(plan: Plan) -> None:
    """Rules across a whole collection, after the upgrade hooks ran: unique indexes, roles, counters, settings."""
    for coll, recs in plan.records.items():
        name = f"data/{coll}.json"
        for index in (ix for ix in TABLES[coll].indexes if ix.unique):
            seen: dict[tuple, int] = {}
            for i, rec in enumerate(recs):
                values = tuple(rec.get(c) for c in index.columns)
                if any(v is None for v in values):
                    continue
                key = tuple(v.casefold() if isinstance(v, str) else v for v in values)
                if key in seen:
                    plan.error(
                        "duplicate_id",
                        f"{name} record {i}: {', '.join(index.columns)} is the same as in record {seen[key]}.",
                        name,
                    )
                else:
                    seen[key] = i
    for i, u in enumerate(plan.records.get("users", [])):
        if not isinstance(u.get("username"), str) or not u["username"].strip():
            plan.error("record_invalid", f"data/users.json record {i}: username is missing.", "data/users.json")
        if u.get("role") not in ROLES:
            plan.error("record_invalid", f"data/users.json record {i}: unknown role.", "data/users.json")
    for i, r in enumerate(plan.records.get("number_sequences", [])):
        last = r.get("last_value", 0)
        if not isinstance(last, int) or not 0 <= last <= limits.MAX_SEQUENCE:
            plan.error("record_invalid", f"data/number_sequences.json record {i}: last_value is out of range.")
    if not _settings_ok(plan.settings):
        plan.error("record_invalid", "data/settings.json contains settings that are not valid.", "data/settings.json")


def _check_references(plan: Plan) -> None:
    ids = {c: {r["id"] for r in recs} for c, recs in plan.records.items()}
    shown = 0
    total = 0
    for coll, recs in plan.records.items():
        table = TABLES[coll]
        for i, rec in enumerate(recs):
            for col in table.columns:
                if col.type != ID or col.name == "id":
                    continue
                target = REFERENCES.get(col.name)
                value = rec.get(col.name)
                if target is None or not value or value in ids.get(target, set()):
                    continue
                total += 1
                if shown < 20:
                    shown += 1
                    plan.warn(
                        "dangling_reference",
                        f"{coll}[{i}].{col.name} points to a missing {target[:-1]} ({value}).",
                        f"data/{coll}.json",
                    )
            for key, (c, target) in LIST_REFERENCES.items():
                if c != coll:
                    continue
                values = rec.get(key.split(".")[1])
                if isinstance(values, list):
                    for value in values:
                        if isinstance(value, str) and value not in ids.get(target, set()):
                            total += 1
                            if shown < 20:
                                shown += 1
                                plan.warn(
                                    "dangling_reference",
                                    f"{coll}[{i}].{key.split('.')[1]} points to a missing user ({value}).",
                                    f"data/{coll}.json",
                                )
    if total > shown:
        plan.warn("dangling_reference", f"{total - shown} more dangling references.", None)
    missing_files = 0
    for coll, attach_col in ATTACHMENT_COLUMNS:
        for rec in plan.records.get(coll, []):
            name = rec.get(attach_col)
            if isinstance(name, str) and name and name not in plan.attachments:
                missing_files += 1
    logo = plan.settings.get("logo")
    if isinstance(logo, str) and logo and logo not in plan.attachments:
        missing_files += 1
    if missing_files and plan.manifest and not plan.manifest.get("include_attachments", True):
        plan.warn(
            "attachments_not_included",
            f"The archive has no attachments; {missing_files} files are referenced and will be missing.",
        )
    elif missing_files:
        plan.warn("dangling_reference", f"{missing_files} referenced attachment files are not in the archive.")


def _hash_ok(value: str) -> bool:
    if not PASSWORD_HASH_RE.fullmatch(value):
        return False
    return 1 <= int(value.split("$")[1]) <= MAX_ITERATIONS


def _prepare_users(plan: Plan) -> None:
    """Archive users as they will be written: no two-factor data ever; no login unless the archive says it carries
    accounts (``include_accounts`` is true). A hand-made archive cannot smuggle hashes into an export without them."""
    accounts = bool(plan.manifest and plan.manifest.get("include_accounts") is True)
    users = []
    for i, u in enumerate(plan.records.get("users", [])):
        rec = {k: v for k, v in u.items() if not k.startswith("totp")}
        if not accounts:
            rec.pop("password_hash", None)
            rec.pop("pw_changed", None)
        elif rec.get("password_hash") and not _hash_ok(rec["password_hash"]):
            plan.error(
                "record_invalid", f"data/users.json record {i}: password_hash has an unknown format.", "data/users.json"
            )
        users.append(rec)
    plan.records["users"] = users


def _user_plan(plan: Plan, local_users: list[dict]) -> None:
    archive = plan.records.get("users", [])
    local = local_users[0] if local_users else None
    replaces = kept = None
    without: list[str] = []
    match = None
    if local is not None:
        folded = str(local.get("username", "")).casefold()
        match = next((u for u in archive if str(u.get("username", "")).casefold() == folded), None)
        if match is not None:
            replaces = local.get("username")
        else:
            kept = local.get("username")
            plan.warn(
                "setup_user_kept",
                f"No user in the archive is called {kept!r}; the setup account is kept next to the imported users.",
            )
        clash = next((u for u in archive if u["id"] == local["id"] and u is not match), None)
        if clash is not None:
            plan.error(
                "duplicate_id",
                "A user in the archive has the id of the setup account but another username.",
                "data/users.json",
            )
    with_login: list[str] = [str(local.get("username"))] if local is not None else []
    bosses: list[str] = [str(local.get("username"))] if local is not None and match is None else []
    if match is not None:
        bosses.append(str(match.get("username")))  # the matched account becomes the setup account: a boss
    for u in archive:
        if u is match:
            continue
        if u.get("password_hash"):
            with_login.append(str(u.get("username")))
            if u.get("role") == "boss":
                bosses.append(str(u.get("username")))
        else:
            without.append(str(u.get("username")))
    if without:
        plan.warn(
            "users_without_password",
            f"{len(without)} users have no password and are inactive until you set one: {', '.join(without)}.",
        )
    imported_logins = len(with_login) - (1 if local is not None else 0)
    if imported_logins > 0:
        plan.warn(
            "accounts_can_login",
            f"{len(with_login)} accounts incl. {len(bosses)} bosses can log in after the import.",
        )
    plan.users = {
        "replaces_setup_user": replaces,
        "kept_setup_user": kept,
        "without_password": without,
        "with_login": with_login,
        "bosses": bosses,
    }


class _PlanStore:
    """Read-only view of the archive data with the interface NumberingService needs, so the numbers shown in the
    preview and the result are computed by the same code that hands out numbers after the import."""

    def __init__(self, plan: Plan):
        self.plan = plan

    def settings(self) -> dict:
        return self.plan.settings if _settings_ok(self.plan.settings) else {}

    def list(self, coll: str) -> list[dict]:
        return list(self.plan.records.get(coll, []))

    def get(self, coll: str, rid: str) -> dict | None:
        return next((r for r in self.plan.records.get(coll, []) if r.get("id") == rid), None)


def _numbering(plan: Plan, today: date) -> None:
    service = NumberingService(cast(Repository, _PlanStore(plan)), clock=lambda: today)
    sequences = plan.records.get("number_sequences", [])
    out: dict = {}
    for key, short in (("invoices", "invoice"), ("offers", "offer")):
        try:
            cfg = service.config(short)
            pattern = parse_pattern(cfg.pattern, strict=False)
            nxt = service.preview(short, 1)[0]
        except (PatternError, ServiceError):
            continue
        group = "shared" if cfg.shared else cfg.coll
        colls = ("invoices", "offers") if cfg.shared else (cfg.coll,)
        best: tuple[int, str] | None = None
        by_period: dict[str, int] = {}
        for c in colls:
            for d in plan.records.get(c, []):
                number, day = d.get("number"), _day(d.get("date"))
                if not number or day is None:
                    continue
                n = pattern.match(number)
                if n is None or not pattern.date_consistent(number, day):
                    continue
                if best is None or n > best[0]:
                    best = (n, number)
                p = period_key(cfg.reset, day)
                by_period[p] = max(by_period.get(p, 0), n)
        rows = sorted((r for r in sequences if r.get("doc_type") == group), key=lambda r: str(r.get("period")))
        row = rows[-1] if rows else None
        for r in rows:
            p = str(r.get("period"))
            if by_period.get(p, 0) > int(r.get("last_value") or 0):
                plan.warn(
                    "numbering_counter_low",
                    f"The {short} counter for {p} ({r.get('last_value')}) is lower than the highest imported number "
                    f"({by_period[p]}); numbers already in use are skipped.",
                )
            if r.get("id") != service._row_id(group, p):
                plan.warn(
                    "numbering_counter_unreachable",
                    f"The {short} counter row for {p} has an unexpected id and is not used; the counter is "
                    "recomputed from the documents.",
                )
        out[key] = {
            "highest_imported": best[1] if best else None,
            "counter_period": row.get("period") if row else None,
            "counter": int(row.get("last_value") or 0) if row else None,
            "next": nxt,
        }
    plan.numbering = out


def _settings_ok(raw: dict) -> bool:
    try:
        effective_settings(raw)
        return True
    except Exception:
        return False


def _day(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def validate(path: Path, store: Repository, *, allow_no_users: bool = False, today: date | None = None) -> Plan:
    """Read the whole archive; nothing is written. Errors and warnings are collected in the returned plan."""
    plan = Plan(allow_no_users=allow_no_users)
    try:
        with ArchiveReader(path) as reader:
            _validate(plan, reader, store, today or date.today())
    except ArchiveError as e:
        plan.error(e.code, e.message, e.file)
    plan.empty_reason = instance_state(store, allow_no_users=allow_no_users)
    if plan.empty_reason:
        plan.error("not_empty", plan.empty_reason)
    return plan


def _validate(plan: Plan, reader: ArchiveReader, store: Repository, today: date) -> None:
    if "manifest.json" not in reader.entries:
        raise ArchiveError("manifest_missing", "The archive has no manifest.json.", "manifest.json")
    man = parse_json(reader.read("manifest.json"), "manifest.json")
    if not isinstance(man, dict) or not isinstance(man.get("files"), dict):
        raise ArchiveError("manifest_invalid", "manifest.json is not a valid manifest.", "manifest.json")
    plan.manifest = man
    if man.get("format") != manifest.FORMAT:
        raise ArchiveError("format_unknown", "This is not a permitA38 export archive.", "manifest.json")
    version = man.get("format_version")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise ArchiveError("manifest_invalid", "manifest.json has no valid format_version.", "manifest.json")
    if version > manifest.FORMAT_VERSION:
        raise ArchiveError(
            "format_too_new", "The archive was made by a newer permitA38. Update first.", "manifest.json"
        )
    chain = [m.id for m in list_migrations("sqlite")]
    head = man.get("schema_head")
    if not isinstance(head, str) or head not in chain:
        msg = f"The archive was made by a newer permitA38 (schema {str(head)[:20]}). Update first."
        plan.compatibility = {"status": "unknown", "message": msg}
        raise ArchiveError("schema_unknown", msg, "manifest.json")
    if head == chain[-1]:
        plan.compatibility = {"status": "same", "message": None}
    else:
        msg = "The archive was made by an older permitA38; fields added since are empty after the import."
        plan.compatibility = {"status": "older", "message": msg}
        plan.warn("schema_older", msg, "manifest.json")
    if man.get("scope") != "full":
        raise ArchiveError(
            "scope_not_importable",
            "This archive contains master data only; use Import documents with type customers.",
            "manifest.json",
        )
    # every file must be listed and match; the manifest itself is not listed
    listed = man["files"]
    for name in reader.entries:
        if name != "manifest.json" and name not in listed:
            plan.error("file_unlisted", f"{name} is in the archive but not in the manifest.", name)
    for name, meta in listed.items():
        if not isinstance(name, str) or not isinstance(meta, dict) or classify(name) is None:
            plan.error("manifest_invalid", "The manifest lists a file name that is not allowed.", "manifest.json")
            continue
        if name not in reader.entries:
            plan.error("file_missing", f"{name} is listed in the manifest but missing.", name)
    if plan.errors:
        return
    for name, meta in listed.items():
        kind, arg = reader.kinds[name]
        if kind in ("data", "settings"):
            blob = reader.read(name)
            size, sha, first = len(blob), hashlib.sha256(blob).hexdigest(), b""
        else:
            blob = b""
            size, sha, first = reader.digest(name)
        if sha != meta.get("sha256") or size != meta.get("bytes"):
            plan.error("hash_mismatch", f"{name} does not match its hash in the manifest.", name)
            continue
        if kind == "attachment":
            ext = "." + arg.rsplit(".", 1)[1]
            if ext in INLINE_TYPES and not magic_ok(ext, first):
                plan.error("attachment_invalid", f"{arg} does not look like its {ext} extension.", name)
                continue
            plan.attachments[arg] = size
            plan.attachment_sha[arg] = sha
        elif kind in ("data", "settings"):
            body = parse_json(blob, name)  # each file is parsed once; its bytes are dropped right away
            del blob
            if kind == "settings":
                if not isinstance(body, dict) or not isinstance(body.get("settings"), dict):
                    plan.error("json_invalid", "data/settings.json must be an object with 'settings'.", name)
                else:
                    plan.settings = body["settings"]
            else:
                plan.records[arg] = _validate_records(plan, arg, name, body)
    if plan.errors:
        return
    for coll in manifest.exported_collections():
        plan.records.setdefault(coll, [])
    upgrade(plan.records, head, chain)
    _prepare_users(plan)
    if any(manifest.is_secret_setting(k) for k in plan.settings):
        plan.settings = manifest.exportable_settings(plan.settings)
    if plan.records.get("calendar_sources"):
        n = len(plan.records["calendar_sources"])
        plan.warn(
            "calendar_url_removed", f"Calendar links are not exported; reconnect {n} calendar sources after the import."
        )
    _check_semantics(plan)
    if plan.errors:
        return
    _check_references(plan)
    _user_plan(plan, store.list("users"))
    _numbering(plan, today)


# ------------------------------------------------------------------ writing
def _stage_one(reader: ArchiveReader, store: Repository, stage: str, name: str, plan: Plan) -> None:
    sha = hashlib.sha256()

    def chunks():
        for chunk in reader.stream(f"attachments/{name}"):
            sha.update(chunk)
            yield chunk

    store.stage_attachment(stage, name, chunks())
    if sha.hexdigest() != plan.attachment_sha.get(name):
        raise ServiceError(422, f"The attachment {name} changed while it was read; nothing was imported.")


def apply(path: Path, store: Repository, plan: Plan) -> dict:
    """Write a validated plan. Attachments are staged first, the records go in one transaction, and the staged files
    are moved into place only after the commit. On any failure (also an interrupt) nothing is left behind."""
    if not plan.ok:
        raise ServiceError(422, "The archive was not imported.")
    stage = store.new_stage()
    try:
        with ArchiveReader(path) as reader:
            for name in sorted(plan.attachments):
                _stage_one(reader, store, stage, name, plan)
        with store.transaction():
            reason = instance_state(store, allow_no_users=plan.allow_no_users)  # re-checked: it may have changed
            if reason:
                raise ServiceError(409, reason)
            local = store.list("users")
            local_user = local[0] if local else None
            for coll in manifest.exported_collections():
                if coll == "users":
                    continue
                for rec in plan.records.get(coll, []):
                    store.save(coll, rec, touch=False)
            relogin = _write_users(store, plan, local_user)
            keep = {k: v for k, v in store.settings().items() if manifest.is_secret_setting(k)}
            store.save_settings({**plan.settings, **keep})
    except BaseException:
        store.discard_stage(stage)
        raise
    try:
        store.commit_stage(stage)
    except (OSError, ValueError) as e:  # the records are committed; the staged files stay for a manual recovery
        log.exception("could not move the imported attachments into place (staging area %s)", stage)
        raise ServiceError(500, "The data was imported but the attachment files could not be moved into place.") from e
    return {"relogin": relogin}


def _write_users(store: Repository, plan: Plan, local: dict | None) -> bool:
    relogin = False
    folded = str(local.get("username", "")).casefold() if local is not None else None
    for u in plan.records.get("users", []):
        rec = {k: v for k, v in u.items() if not k.startswith("totp")}
        if local is not None and str(rec.get("username", "")).casefold() == folded:
            store.delete("users", local["id"])
            rec["password_hash"] = local.get("password_hash")
            rec["pw_changed"] = local.get("pw_changed", 0)
            rec["role"] = "boss"
            if rec.get("active") is False:
                rec["active"] = True
            relogin = rec["id"] != local["id"]
        elif not rec.get("password_hash"):
            rec.pop("password_hash", None)
            rec.pop("pw_changed", None)
            rec["active"] = False
        rec["totp_enabled"] = False
        store.save("users", rec, touch=False)
    return relogin
