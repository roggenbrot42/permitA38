"""Schema-driven request fuzzing of the HTTP API.

Requests are generated from the app's own OpenAPI document (paths, methods, query parameters, JSON bodies),
then deliberately damaged: wrong types, huge numbers, unicode and emoji, path traversal, malformed JSON,
negative values, impossible dates. They are sent as boss, member and anonymous.

For every response we assert: never a 5xx, never a traceback, errors are JSON with `detail`, strangers get
401, path traversal never reaches files outside the data folder, strangers/members never write where they
must not, and afterwards the data folder is still consistent and the app restarts on it.
"""

import hashlib
import json
import os
import random
import shutil
import sqlite3
import tempfile
import uuid
from pathlib import Path
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st

from permita38.api.server import get_app
from permita38.core.config import Config

DEAD_ID = "deadbeef0000"
DATE_FIELDS = {"date", "start", "end", "due_date", "valid_until", "paid_date", "service_from", "service_to",
               "tracking_start", "from", "to"}  # fmt: skip
ID_FIELDS = {"customer_id", "project_id", "task_id", "user_id", "offer_id", "invoice_id"}
JUNK = [
    None, True, False, 0, -1, 1, 2**31, 2**63, -(2**63), 10**30, 1e308, -1e308, 0.1, -0.0, "", " ", "0", "null",
    "x" * 5000, "😀🔥" * 40, "\u0000", "../../etc/passwd", "..\\..\\windows\\win.ini", "<script>alert(1)</script>",
    "' OR 1=1 --", "%s%n%x", "2026-13-45", "9999-99-99", "0000-00-00", {"a": 1}, [], [1, "a", None], {"__proto__": {}},
    "‮", "ǅ", "ß", "İ", "\ud83d",  # lone surrogate (valid JSON escape, not valid UTF-8)
]  # fmt: skip
TRAVERSAL = ["../../etc/passwd", "..%2f..%2fsecret.txt", "..\\..\\secret.txt", "%2e%2e/%2e%2e/secret.txt",
             "....//....//secret.txt", "/etc/passwd", "secret.txt%00.png", "a/../../secret.txt"]  # fmt: skip
PUBLIC = {("get", "/api/health"), ("get", "/api/auth/status"), ("post", "/api/auth/setup"),
                 ("post", "/api/auth/login"), ("post", "/api/auth/logout")}  # fmt: skip
MEMBER_WRITE_TABLES = {"entries", "absences", "timers"}  # the only tables a member may write to


class World:
    """A fresh app + data folder per example, seeded straight through the store (fast)."""

    def __init__(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="p38-fuzz-"))
        self.data = self.tmp / "data"
        self.canary = f"CANARY-{uuid.uuid4().hex}"
        (self.tmp / "secret.txt").write_text(self.canary)
        (self.data.parent / "outside.txt").write_text(self.canary)
        self.cfg = Config(data_dir=self.data, secret_key="fuzz-secret-key-0123456789", web_dir=None)
        self.app = get_app(self.cfg)
        self.http = TestClient(self.app, raise_server_exceptions=os.environ.get("PERMITA38_FUZZ_RAISE") == "1")
        self.http.__enter__()
        self.svc = self.app.state.services
        self.db_file = self.svc.store.db_path
        a = self.svc.auth
        self.boss = a.setup("boss", "Bea Boss", "boss-password-1", "Fuzz Co", 40)
        self.member = a.save_user(dict(username="mia", name="Mia", role="employee", password="member-pass-1"))
        s = self.svc.store
        self.ids: list[str] = [self.boss["id"], self.member["id"]]
        c = s.save("customers", dict(name="ACME", rate=100.0, street="Main 1", zip="10115", city="Berlin"))
        p = s.save("projects", dict(name="Site", customer_id=c["id"], personal=False, status="active", member_ids=[]))
        t = s.save("tasks", dict(name="Dev", project_id=p["id"], rate=120.0))
        e = s.save("entries", dict(user_id=self.member["id"], date="2026-10-20", minutes=90, project_id=p["id"],
                                   task_id=t["id"], billable=True))  # fmt: skip
        s.save(
            "entries", dict(user_id=self.boss["id"], date="2026-10-21", minutes=30, project_id=p["id"], billable=True)
        )
        x = s.save("expenses", dict(date="2026-10-01", net=10.0, vat=1.9, deductible=100))
        ab = s.save("absences", dict(user_id=self.member["id"], kind="vacation", start="2026-12-24", end="2026-12-31"))
        k = s.save("contracts", dict(title="Retainer", customer_id=c["id"], start="2026-10-01", end="2027-03-01",
                                     hours_per_week=10, status="confirmed", user_ids=[self.boss["id"]]))  # fmt: skip
        line = dict(description="Work", qty=2.0, price=100.0, unit="h", amount=200.0)
        o = s.save("offers", dict(customer_id=c["id"], title="Offer", status="draft", date="2026-10-01", lines=[line]))
        i = s.save("invoices", dict(customer_id=c["id"], title="Inv", status="draft", date="2026-10-01", lines=[line]))
        self.attachment = s.save_attachment("bill.png", b"\x89PNG\r\n\x1a\nfake")
        self.ids += [c["id"], p["id"], t["id"], e["id"], x["id"], ab["id"], k["id"], o["id"], i["id"]]
        self.snapshot0 = None

    def token(self, who: str) -> str | None:
        user = {"boss": self.boss, "member": self.member}.get(who)
        if not user or not self.svc.store.get("users", user["id"]):
            return None
        return self.svc.auth.create_token(self.svc.store.get("users", user["id"]))

    def snapshot(self) -> dict[str, str]:
        """Digest of every table (read through a separate read-only connection) and of the attachments folder."""
        out: dict[str, str] = {}
        con = sqlite3.connect(f"file:{self.db_file}?mode=ro", uri=True)
        try:
            tables = [
                r[0]
                for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")
            ]
            for t in sorted(tables):
                rows = con.execute(f'SELECT * FROM "{t}" ORDER BY 1').fetchall()
                out[t] = hashlib.sha256(repr(rows).encode()).hexdigest()
        finally:
            con.close()
        files = sorted(
            (str(p.relative_to(self.data)), p.stat().st_size)
            for p in (self.data / "attachments").rglob("*")
            if p.is_file()
        )
        out["attachments"] = hashlib.sha256(repr(files).encode()).hexdigest()
        return out

    def close(self):
        self.http.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)


# ---------------------------------------------------------------------------- request generation
class Gen:
    def __init__(self, rnd, world: World, spec: dict):
        self.r, self.w, self.spec = rnd, world, spec
        self.ops = [(m, p, op) for p, item in spec["paths"].items() for m, op in item.items()
                    if m in ("get", "post", "put", "delete")]  # fmt: skip
        # every example concentrates on a few operations, so each gets hit repeatedly with all roles and bodies
        self.focus = [self.ops[self.r.randrange(len(self.ops))] for _ in range(5)]

    # -- values
    def junk(self):
        return self.r.choice(JUNK)

    def an_id(self):
        x = self.r.random()
        if x < 0.55:
            return self.r.choice(self.w.ids)
        if x < 0.7:
            return DEAD_ID
        if x < 0.8:
            return uuid.uuid4().hex[:12]
        return self.r.choice([*TRAVERSAL, "", "x" * 300, "😀", "0"])

    def a_date(self):
        return self.r.choice(["2026-10-19", "2026-10-22", "2026-12-24", "2027-01-01", "2025-12-31", "2026-02-28"])

    def schema_value(self, schema, name="", depth=0):
        schema = self.resolve(schema)
        if depth > 5:
            return None
        if self.r.random() < 0.08:
            return self.junk()
        for key in ("anyOf", "oneOf"):
            if key in schema:
                return self.schema_value(self.r.choice(schema[key]), name, depth + 1)
        if "enum" in schema:
            return self.r.choice(schema["enum"])
        t = schema.get("type")
        if t == "object" or "properties" in schema:
            props = schema.get("properties", {})
            out = {k: self.schema_value(v, k, depth + 1) for k, v in props.items()
                   if k in schema.get("required", []) or self.r.random() < 0.6}  # fmt: skip
            if self.r.random() < 0.1:
                out[self.r.choice(["__class__", "id", "created", "$where", "../x", "x" * 50])] = self.junk()
            return out
        if t == "array":
            return [self.schema_value(schema.get("items", {}), name, depth + 1) for _ in range(self.r.randint(0, 3))]
        if t == "boolean":
            return self.r.random() < 0.5
        if t == "integer":
            return self.r.choice([0, 1, 5, 60, 480, -5, 2**31, 12])
        if t == "number":
            return self.r.choice([0, 1.5, 42.5, 100, -1, 0.001, 19, 7])
        if name in DATE_FIELDS:
            return self.a_date()
        if name in ID_FIELDS or name == "id":
            return self.an_id()
        if name in ("username",):
            return self.r.choice(["boss", "mia", "new.user", "x"])
        if name in ("password", "old_password", "new_password"):
            return self.r.choice(["boss-password-1", "member-pass-1", "short", "a-new-long-password"])
        if name == "role":
            return self.r.choice(["boss", "employee", "root"])
        return self.r.choice(["text", "Ünïcödé ✓", "😀", "a" * 200, "", "line\nbreak", '"quotes"', "x"])

    def resolve(self, schema):
        while isinstance(schema, dict) and "$ref" in schema:
            node = self.spec
            for part in schema["$ref"].lstrip("#/").split("/"):
                node = node[part]
            schema = node
        return schema

    def pick_operation(self):
        """Two thirds of the traffic are writes: that is where permissions and consistency can break."""
        writes = [o for o in self.ops if o[0] != "get"]
        x = self.r.random()
        method, path, op = self.r.choice(self.focus if x < 0.6 else writes if x < 0.85 else self.ops)
        return method, path, op

    # -- one request
    def request(self):
        method, path, op = self.pick_operation()
        url = path
        has_traversal = False
        query = {}
        for prm in op.get("parameters", []):
            if prm["in"] == "path":
                v = self.an_id()
                if prm["name"] == "name" and self.r.random() < 0.4:
                    v = self.r.choice([self.w.attachment, *TRAVERSAL])
                has_traversal |= "/" in v or "%" in v or "\\" in v or v == ""
                url = url.replace("{" + prm["name"] + "}", quote(v, safe="") if self.r.random() < 0.85 else v)
            elif self.r.random() < 0.7:
                query[prm["name"]] = self.query_value(prm)
        kw: dict = {}
        if "requestBody" in op:
            content = op["requestBody"]["content"]
            if "multipart/form-data" in content:
                name = self.r.choice(
                    ["bill.pdf", "a.png", "../../x.png", "x" * 300 + ".png", "no_ext", "ü.JPG", ".", "a.b.c.d"]
                )
                blob = self.r.choice([b"", b"%PDF-1.4 fake", b"\xff\xd8\xff", os.urandom(200), b"A" * 100_000])
                if self.r.random() < 0.9:
                    kw["files"] = {"file": (name, blob, self.r.choice(["image/png", "application/pdf", "text/html"]))}
            else:
                schema = content["application/json"]["schema"]
                x = self.r.random()
                if x < 0.05:
                    kw["content"] = self.r.choice([b"{", b"[1,", b"\xff\xfe", b"null", b"", b"{'a':1}", b"1" * 5000])
                    kw["headers"] = {"Content-Type": "application/json"}
                elif x < 0.08:
                    kw["content"] = b"plain text"
                    kw["headers"] = {"Content-Type": "text/plain"}
                else:
                    kw["content"] = json.dumps(self.schema_value(schema), allow_nan=False).encode()
                    kw["headers"] = {"Content-Type": "application/json"}
        return method.upper(), url, query, kw, has_traversal

    def query_value(self, prm):
        name = prm["name"]
        t = self.resolve(prm.get("schema", {}))
        if "anyOf" in t:
            t = self.resolve(next(x for x in t["anyOf"] if x.get("type") != "null"))
        if name == "year":
            return self.r.choice([2025, 2026, 2027, 0, "abc", 10000, 99999, -5])
        if name == "user_id":
            return self.an_id()
        if t.get("type") == "integer":
            return self.r.choice([0, 1, 26, 156, 157, -1, 10**12, "abc", 1.5])
        return self.r.choice(["text", "", "😀", "../../x", "1", "-1"])


def check_response(r, method, url, who, has_traversal, world, sent=""):
    sent = sent if isinstance(sent, str) else sent.decode("utf-8", "replace")
    body = r.text
    assert r.status_code < 500, f"{who} {method} {url} -> {r.status_code}: {body[:300]}  sent={sent[:400]!r}"
    assert "Traceback (most recent call last)" not in body and 'File "/' not in body, body[:300]
    assert world.canary not in body, f"{who} {method} {url} leaked a file outside the data folder"
    if r.status_code >= 400:
        assert r.headers["content-type"].startswith("application/json"), (method, url, r.status_code, body[:200])
        assert "detail" in r.json(), (method, url, r.status_code, body[:200])
    for secret in ("password_hash", "pw_changed", "pbkdf2_sha256"):
        # records keep unknown fields, so a field the client sent itself is legitimately echoed back
        assert secret not in body or secret in sent, f"{secret} leaked by {method} {url}"


def run_session(seed: int, steps: int):
    world = World()
    try:
        spec = world.app.openapi()
        gen = Gen(random.Random(seed), world, spec)
        for _ in range(steps):
            method, url, query, kw, trav = gen.request()
            who = gen.r.choice(["boss", "boss", "member", "member", "anon", "forged", "cookie"])
            headers = dict(kw.pop("headers", {}))
            tok = world.token(who if who in ("boss", "member") else "boss")
            if who in ("boss", "member") and tok:
                headers["Authorization"] = "Bearer " + tok
            elif who == "forged":
                headers["Authorization"] = "Bearer " + gen.r.choice(
                    ["a.b.c", tok.rsplit(".", 1)[0] + ".AAAA" if tok else "x", "", "x" * 2000]
                )
            elif who == "cookie" and tok:
                world.http.cookies.set("authorization", tok)
            before = world.snapshot() if who != "boss" else None
            r = world.http.request(method, url, params=query, headers=headers, **kw)
            world.http.cookies.clear()
            check_response(r, method, url, who, trav, world, kw.get("content") or str(kw.get("files") or ""))
            if who in ("anon", "forged"):
                public = (method.lower(), url.split("?")[0].rstrip("/")) in PUBLIC
                if not public:
                    ok = {401, 404, 405, 422} if trav else {401, 405, 422}  # routing (405) happens before auth
                    if r.status_code == 400 and r.json().get("detail") == "There was an error parsing the body":
                        ok = {400}  # the body is read before the auth dependency runs (FastAPI order)
                    assert r.status_code in ok, f"stranger got {r.status_code} on {method} {url}: {r.text[:200]}"
                assert world.snapshot() == before, f"a stranger changed the database via {method} {url}"
            if who == "member":
                after = world.snapshot()
                changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
                # a member may change their own user row: password and two-factor enrolment (/auth/totp/*)
                own_row = url.endswith("/auth/change-password") or "/auth/totp/" in url
                own = {"users"} if own_row else set()
                bad = {k for k in changed if k not in MEMBER_WRITE_TABLES and k not in own}
                assert not bad, f"{who} {method} {url} wrote to {sorted(bad)}"
        verify_consistency(world)
    finally:
        world.close()


def verify_consistency(world: World):
    """The database passes SQLite's integrity check, no temp files are left behind, at least one active boss
    remains, and a restarted app still serves everything."""
    con = sqlite3.connect(f"file:{world.db_file}?mode=ro", uri=True)
    try:
        assert con.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        con.close()
    leftovers = [p.name for p in world.data.rglob("*") if ".tmp" in p.name]
    assert not leftovers, f"stray temp files after failed writes: {leftovers}"
    users = world.svc.store.list("users")
    boss = next((u for u in users if u["role"] == "boss" and u.get("active", True)), None)
    assert boss, "all active bosses are gone"
    app2 = get_app(world.cfg)  # "restart" on the same folder
    with TestClient(app2, raise_server_exceptions=False) as c:
        h = {"Authorization": "Bearer " + app2.state.services.auth.create_token(boss)}
        for coll in ("customers", "projects", "tasks", "entries", "contracts", "absences", "offers", "invoices",
                     "expenses", "users", "templates"):  # fmt: skip
            r = c.get(f"/api/{coll}", headers=h)
            assert r.status_code == 200, (coll, r.status_code, r.text[:200])
        for path in ("/api/bootstrap", "/api/dashboard", "/api/finance", "/api/forecast", "/api/project-report",
                     "/api/settings", "/api/timer"):  # fmt: skip
            r = c.get(path, headers=h)
            assert r.status_code == 200, (path, r.status_code, r.text[:200])


@pytest.mark.randomized
@settings(settings.default, max_examples=max(8, settings.default.max_examples * 2 // 3))
@given(seed=st.integers(0, 2**32 - 1))
def test_random_requests_never_break_the_api(seed):
    """`seed` drives a real PRNG (hypothesis' own random source is biased towards 'simplest' values, which
    would make every body junk). A failure prints the seed: replay with run_session(seed, steps=40)."""
    run_session(seed, steps=40)


@pytest.mark.randomized
def test_openapi_covers_every_route():
    """The fuzzer is only as good as the schema it reads: every route of every router module is documented."""
    import importlib
    import pkgutil

    import permita38.api.routers as pkg

    declared = set()
    for info in pkgutil.iter_modules(pkg.__path__):
        for router in getattr(importlib.import_module(f"{pkg.__name__}.{info.name}"), "routers", []):
            declared |= {(m.lower(), "/api" + r.path) for r in router.routes for m in r.methods if m != "HEAD"}
    world = World()
    try:
        documented = {(m, p) for p, item in world.app.openapi()["paths"].items() for m in item}
        assert declared and declared == documented, declared ^ documented
    finally:
        world.close()


@pytest.mark.randomized
@pytest.mark.parametrize("name", TRAVERSAL + ["../" * 8 + "etc/passwd", "a" * 5000, "😀.png", "x.png\x00", ""])
def test_attachment_paths_cannot_escape_the_data_folder(name):
    from permita38.core.repository import create_repository

    tmp = Path(tempfile.mkdtemp(prefix="p38-trav-"))
    try:
        (tmp / "secret.png").write_bytes(b"secret")
        repo = create_repository(Config(data_dir=tmp / "data", secret_key="trav-secret-key-0123456789", web_dir=None))
        repo.startup()
        assert repo.attachment_path(name) is None
        for coll in ("customers", "users", "timers"):
            try:
                assert repo.get(coll, name) is None
            except (ValueError, KeyError):
                pass  # rejecting the id outright is fine too
        repo.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@pytest.mark.randomized
@pytest.mark.parametrize(
    "extra",
    [
        {"password_hash": "pbkdf2_sha256$1$00$00", "pw_changed": 0},
        {"is_boss": True, "id": "othersomeone", "active": True},
    ],
)
def test_user_records_cannot_be_mass_assigned(extra):
    """Extra fields on /users are ignored: they can neither replace a password hash nor smuggle a role."""
    world = World()
    try:
        h = {"Authorization": "Bearer " + world.token("boss")}
        body = {"username": "mia", "name": "Mia", "role": "employee", "weekly_hours": 40, **extra}
        r = world.http.put(f"/api/users/{world.member['id']}", json=body, headers=h)
        assert r.status_code == 200, r.text
        stored = world.svc.store.get("users", world.member["id"])
        assert stored["role"] == "employee" and stored["id"] == world.member["id"] and "is_boss" not in stored
        assert stored["password_hash"].count("$") == 3 and stored["password_hash"] != extra.get("password_hash")
        login = world.http.post("/api/auth/login", json={"username": "mia", "password": "member-pass-1"})
        assert login.status_code == 200
    finally:
        world.close()
