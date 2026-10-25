"""Model-based random testing of the HTTP API.

A hypothesis RuleBasedStateMachine plays boss, members and strangers at random. Every response is compared
with an independent reference model (see oracle.py) and after each step invariants cross-check the whole
state through the API.

Replay a failure: re-run with the printed `@reproduce_failure(...)` blob, or `pytest --hypothesis-seed=N`.
"""

import copy
import shutil
import tempfile
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, initialize, invariant, precondition, rule

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.service import auth as auth_module
from permita38.core.service import throttle as throttle_module
from permita38.core.service import tokens as tokens_module

from . import oracle
from .conftest import FakeClock

TODAY = date.today()
YEAR = TODAY.year
TTL = 12 * 3600
SECRET_WORDS = ("password_hash", "pw_changed", "pbkdf2_sha256")
BOSS_ONLY = ("customers", "contracts", "expenses", "templates", "offers", "invoices")
MEMBER_OWN = ("entries", "absences")
CRUD = ("customers", "projects", "tasks", "entries", "contracts", "absences", "expenses", "templates")
DEAD_ID = "deadbeef0000"  # valid id format, never exists
SESSION_ONLY = "This route cannot be used with an API token"
TOKEN_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
# name -> (method, path, safe method, session-only route); {tok} = the token's own id
TOKEN_CALLS = {
    "me": ("GET", "/auth/me", True, False),
    "get_customers": ("GET", "/customers", True, False),
    "get_entries": ("GET", "/entries", True, False),
    "get_projects": ("GET", "/projects", True, False),
    "get_settings": ("GET", "/settings", True, False),
    "get_users": ("GET", "/users", True, True),
    "list_tokens": ("GET", "/tokens", True, True),
    "post_customer": ("POST", "/customers", False, False),
    "delete_customer": ("DELETE", "/customers/{cid}", False, False),
    "put_settings": ("PUT", "/settings", False, True),
    "create_token": ("POST", "/tokens", False, True),
    "revoke_self": ("POST", "/tokens/{tok}/revoke", False, True),
}

# top-level keys that are absent (None) unless set; defaults apply otherwise
OPTIONAL = {
    "customers": ("customer_no", "street", "zip", "city", "country", "tax_number", "vat_id", "contact", "email",
                  "phone", "notes", "rate"),
    "projects": ("customer_id", "rate", "budget_hours", "color", "notes", "offer_id"),
    "tasks": ("rate",),
    "entries": ("user_id", "project_id", "task_id", "note", "start", "end", "invoice_id", "rate"),
    "contracts": ("customer_id", "project_id", "hours_per_week", "total_hours", "rate", "offer_id"),
    "absences": ("user_id", "end", "note"),
    "expenses": ("vat_rate", "vendor", "description", "category", "project_id", "attachment"),
    "templates": ("intro_offer", "outro_offer", "intro_invoice", "outro_invoice", "closing"),
    "offers": ("number", "customer_id", "project_id", "due_date", "valid_until", "paid_date", "service_from",
               "service_to", "title", "reference", "language", "intro", "outro", "notes", "vat_rate", "plan",
               "offer_id"),
}  # fmt: skip
OPTIONAL["invoices"] = OPTIONAL["offers"]
DEFAULTS = {
    "customers": {}, "tasks": {}, "absences": {"kind": "vacation"},
    "projects": {"personal": False, "status": "active", "member_ids": []},
    "entries": {"billable": True}, "contracts": {"status": "confirmed", "user_ids": []},
    "expenses": {"vat": 0.0, "deductible": 100.0},
    "templates": {"layout": "classic", "accent": "#1f4e79", "language": "de", "show_logo": True},
    "offers": {"status": "draft", "lines": []}, "invoices": {"status": "draft", "lines": []},
}  # fmt: skip


@dataclass
class Session:
    token: str
    user_id: str | None
    iat: int


def approx(a, b) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= 0.011
    return a == b


def subset_eq(exp, act, path="") -> None:
    if isinstance(exp, dict):
        assert isinstance(act, dict), f"{path}: expected object, got {act!r}"
        for k, v in exp.items():
            if k in ("planning_warnings", "sanity_warnings"):
                continue  # response-only hints on write responses, never stored
            assert k in act, f"{path}.{k} missing in {act}"
            subset_eq(v, act[k], f"{path}.{k}")
    elif isinstance(exp, list):
        assert isinstance(act, list) and len(exp) == len(act), f"{path}: {exp!r} != {act!r}"
        for i, (x, y) in enumerate(zip(exp, act)):
            subset_eq(x, y, f"{path}[{i}]")
    else:
        assert approx(exp, act), f"{path}: expected {exp!r}, got {act!r}"


# ---------------------------------------------------------------------------- strategies
def iso_days(lo=-20, hi=7):
    return st.integers(lo, hi).map(lambda n: (TODAY + timedelta(days=n)).isoformat())


DATES = st.one_of(iso_days(), iso_days(), iso_days(), st.sampled_from([f"{YEAR - 1}-12-31", f"{YEAR}-01-01"]))
TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs", "Cc")), min_size=1, max_size=18)
RATE = st.sampled_from([0, 25, 60, 80, 95, 130, 42.5])
QTY = st.integers(1, 160).map(lambda n: n / 4)  # quarter units: products are exact to the cent
PRICE = st.integers(0, 300).map(float)
VATS = st.sampled_from([0, 7, 19])
_PW_CHARS = st.characters(blacklist_categories=("Cs", "Cc"))
PASSWORDS = st.one_of(st.text(_PW_CHARS, min_size=8, max_size=16), st.text(_PW_CHARS, max_size=7))  # long (valid) first
BASE_USERNAME = st.from_regex(r"[a-z0-9][a-z0-9._-]{1,12}", fullmatch=True)
USERNAMES = st.one_of(
    BASE_USERNAME, BASE_USERNAME, BASE_USERNAME.map(str.upper), BASE_USERNAME.map(lambda s: f"  {s} "),
    st.sampled_from(["a", "x" * 33, "has space", "ümlaut", "-dash", "", "   ", "a/b", "a@b.de"]),
)  # fmt: skip


def maybe_none(strategy):
    """An optional field that may also be sent explicitly as null."""
    return st.one_of(st.none(), strategy)


class ApiMachine(RuleBasedStateMachine):
    def __init__(self):
        super().__init__()
        self.counters: dict[tuple[str, int], int] = {}  # (prefix, year of the document date) -> last number given
        self.tmp = Path(tempfile.mkdtemp(prefix="p38-stateful-"))
        self.clock = FakeClock(time.time())
        self._real_time = auth_module.time
        auth_module.time = self.clock.as_module()
        self._real_throttle_time = throttle_module.time
        throttle_module.time = self.clock.as_module()
        self._real_tokens_time = tokens_module.time
        tokens_module.time = self.clock.as_module()
        # trust_proxy: invariant probes come from their own (fake) client address so they never feed the throttle
        # that the token rules model for the default address
        cfg = Config(data_dir=self.tmp / "data", secret_key="stateful-secret-key", web_dir=None, trust_proxy=True)
        self.app = get_app(cfg)
        self.http = TestClient(self.app)
        self.http.__enter__()  # one event-loop portal for the whole run (much faster than one per request)
        self.sessions: list[Session] = []
        self.fails: dict[str, list[float]] = {}
        self.tokens: list[dict] = []  # API tokens: secret, id, user_id, read_only, expires_at, revoked
        self.tok_fails: list[float] = []  # failed token requests from the default address (invalid-token throttle)
        self.probe_n = 0
        self.dirty: set[str] = set()
        self.steps = 0
        self.timers: dict[str, dict | None] = {}
        self.allocs: dict[str, dict] = {}  # id -> {user_id, project_id, start, end, hours_per_week}
        self.m: dict[str, dict] = {c: {} for c in (*CRUD, "offers", "invoices", "users")}
        self.bodies: dict[str, dict] = {}  # last submitted body per document id
        self.settings = {"kleinunternehmer": False, "accounting": "eur"}
        self.graveyard: list[str] = []
        self.pw_of_boss = "boss-password-1"
        r = self.call("POST", "/auth/setup", json={"username": "boss", "name": "Bea Boss", "password": self.pw_of_boss})
        assert r.status_code == 200, r.text
        u = r.json()
        self.m["users"][u["id"]] = dict(
            id=u["id"], username="boss", name="Bea Boss", role="boss", active=True, weekly_hours=40.0,
            workdays=[0, 1, 2, 3, 4], password=self.pw_of_boss, pw_changed=self.now,
        )  # fmt: skip
        self.boss_id = u["id"]
        assert (
            self.call("POST", "/auth/setup", json={"username": "x1", "name": "x", "password": "password1"}).status_code
            == 400
        )
        self.do_login("boss", self.pw_of_boss)

    def teardown(self):
        auth_module.time = self._real_time
        throttle_module.time = self._real_throttle_time
        tokens_module.time = self._real_tokens_time
        self.http.__exit__(None, None, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ------------------------------------------------------------------ plumbing
    @property
    def now(self) -> int:
        return int(self.clock.now)

    def tick(self, seconds=1):
        self.clock.advance(seconds)
        self.steps += 1
        self.refresh_sessions()

    def refresh_sessions(self):
        """Long time jumps expire every token; make sure a boss (and a member) can still act afterwards."""
        for role in ("boss", "employee"):
            users = [u for u in self.m["users"].values() if u["role"] == role and u["active"]]
            if users and not any(self.valid(s) and self.role_of(s) == role for s in self.sessions):
                if self.do_login(users[0]["username"], users[0]["password"]) is None:  # throttled: mint one
                    self.sessions.append(self.mint(users[0]["id"]))

    def call(self, method, path, session: Session | None = None, **kw):
        headers = dict(kw.pop("headers", {}))
        if session is not None:
            headers["Authorization"] = "Bearer " + session.token
        self.http.cookies.clear()  # the login cookie must not turn anonymous calls into authenticated ones
        r = self.http.request(method, "/api" + path, headers=headers, **kw)
        self.http.cookies.clear()
        assert r.status_code < 500, f"{method} {path} -> {r.status_code}: {r.text[:300]}"
        sent = repr(kw)  # a value the client itself sent (e.g. a note that reads "password_hash") may be echoed back
        for word in SECRET_WORDS:
            assert word in sent or word not in r.text, f"secret field {word!r} leaked by {method} {path}"
        if r.status_code >= 400:
            assert r.headers["content-type"].startswith("application/json"), (path, r.status_code, r.text[:100])
            assert "detail" in r.json(), (path, r.status_code, r.text[:100])
        return r

    def valid(self, s: Session | None) -> bool:
        if s is None or s.user_id is None:
            return False
        u = self.m["users"].get(s.user_id)
        return bool(u and u["active"] and s.iat + TTL >= self.clock.now and s.iat >= u["pw_changed"])

    def role_of(self, s: Session) -> str:
        return self.m["users"][s.user_id]["role"]

    def gate(self, s: Session | None, boss_only: bool = False) -> int | None:
        """Expected status if authentication/authorisation already decides the outcome."""
        if not self.valid(s):
            return 401
        if boss_only and self.role_of(s) != "boss":
            return 403
        return None

    def mint(self, user_id: str) -> Session:
        """A fresh valid session for invariant checks, independent of the login flow."""
        svc = self.app.state.services
        return Session(svc.auth.create_token(svc.store.get("users", user_id)), user_id, self.now)

    def do_login(self, username, password) -> Session | None:
        r = self.call("POST", "/auth/login", json={"username": username, "password": password})
        key = username.strip().lower()
        recent = [t for t in self.fails.get(key, []) if self.clock.now - t < 300]
        self.fails[key] = recent
        user = next((u for u in self.m["users"].values() if u["username"] == key), None)
        if len(recent) >= 5:
            assert r.status_code == 429, r.text
            return None
        if user and user["active"] and user["password"] == password:
            assert r.status_code == 200, r.text
            self.fails.pop(key, None)
            body = r.json()
            assert body["user"]["username"] == key and body["user"]["role"] == user["role"]
            assert body["user"]["has_password"] is True
            s = Session(body["token"], user["id"], self.now)
            self.sessions.append(s)
            me = self.call("GET", "/auth/me", s)
            assert me.status_code == 200 and me.json()["id"] == user["id"]
            return s
        assert r.status_code == 401, r.text
        self.fails.setdefault(key, []).append(self.clock.now)
        return None

    # ------------------------------------------------------------------ strategies bound to the model
    def actor(self):
        boss = [s for s in self.sessions if self.valid(s) and self.role_of(s) == "boss"]
        member = [s for s in self.sessions if self.valid(s) and self.role_of(s) == "employee"]
        stale = [s for s in self.sessions if not self.valid(s)]
        junk = [None, Session("not.a.token", None, 0), Session("a.b", None, 0)]
        opts = []  # most likely (and, for hypothesis' simplest examples, first) choices are valid sessions
        if boss:
            opts += [st.sampled_from(boss)] * 8
        if member:
            opts += [st.sampled_from(member)] * 4
        if stale:
            opts.append(st.sampled_from(stale))
        opts.append(st.sampled_from(junk))
        return st.one_of(*opts)

    def boss_actor(self):
        bosses = [s for s in self.sessions if self.valid(s) and self.role_of(s) == "boss"]
        return st.sampled_from(bosses) if bosses else self.actor()

    def ref(self, coll, none=True, dangling=True):
        opts = []
        if self.m[coll]:
            opts += [st.sampled_from(list(self.m[coll]))] * 3
        if none:
            opts.append(st.none())
        if dangling:
            opts.append(st.just(DEAD_ID))
        return st.one_of(*opts) if opts else st.none()

    def user_ref(self):
        return st.sampled_from(list(self.m["users"]))

    def body_st(self, coll, new=True):
        opt = {}
        if coll == "customers":
            req = {"name": TEXT}
            opt = {k: maybe_none(TEXT) for k in OPTIONAL[coll] if k != "rate"} | {"rate": maybe_none(RATE)}
        elif coll == "projects":
            req = {"name": TEXT}
            opt = dict(customer_id=self.ref("customers"), rate=maybe_none(RATE), budget_hours=maybe_none(RATE),
                       color=maybe_none(st.just("#2563eb")), notes=maybe_none(TEXT), personal=st.sampled_from([False, False, False, True]),
                       status=st.sampled_from(["active", "paused", "done", "archived"]),
                       member_ids=st.lists(self.user_ref(), max_size=2, unique=True))  # fmt: skip
        elif coll == "tasks":
            req = {"name": TEXT, "project_id": self.ref("projects", none=False)}
            opt = {"rate": maybe_none(RATE)}
        elif coll == "entries":
            req = {"date": DATES, "minutes": st.integers(1, 600), "user_id": self.user_ref()}
            opt = dict(note=maybe_none(TEXT), billable=st.sampled_from([True, True, True, False]),
                       rate=st.one_of(st.none(), st.none(), st.none(), RATE),
                       start=maybe_none(st.just("09:00")), end=maybe_none(st.just("10:00")))  # fmt: skip
            pairs = [(t["project_id"], tid) for tid, t in self.m["tasks"].items()]
            pairs += [(pid, None) for pid in self.m["projects"]] + [(None, None), (DEAD_ID, DEAD_ID)]
            return st.tuples(st.fixed_dictionaries(req, optional=opt), st.sampled_from(pairs)).map(
                lambda t: {**t[0], "project_id": t[1][0], "task_id": t[1][1]}
            )
        elif coll == "contracts":
            req = {"title": TEXT.filter(lambda t: t.strip()), "start": iso_days(-10, 5), "end": iso_days(6, 90)}
            opt = dict(hours_per_week=maybe_none(RATE), total_hours=maybe_none(RATE), rate=maybe_none(RATE),
                       customer_id=self.ref("customers"), project_id=self.ref("projects"),
                       status=st.sampled_from(["confirmed", "done"]),
                       user_ids=st.lists(self.user_ref(), max_size=2, unique=True))  # fmt: skip
        elif coll == "absences":
            req = {"start": iso_days(-10, 10), "user_id": self.user_ref()}
            opt = dict(end=maybe_none(iso_days(11, 20)), note=maybe_none(TEXT),
                       kind=st.sampled_from(["vacation", "holiday", "sick", "other"]))  # fmt: skip
        elif coll == "expenses":
            req = {"date": DATES, "net": st.integers(0, 2000).map(lambda n: n / 4)}
            opt = dict(vat=st.integers(0, 400).map(lambda n: n / 4), vat_rate=maybe_none(VATS),
                       vendor=maybe_none(TEXT), category=maybe_none(TEXT), project_id=self.ref("projects"),
                       deductible=st.sampled_from([100, 70, 50, 0]))  # fmt: skip
        elif coll == "templates":
            req = {"name": TEXT}
            opt = dict(layout=st.sampled_from(["classic", "modern", "minimal"]), language=st.sampled_from(["de", "en"]),
                       closing=maybe_none(TEXT), show_logo=st.booleans())  # fmt: skip
        else:
            raise AssertionError(coll)
        return st.fixed_dictionaries(req, optional=opt)

    def doc_st(self, coll):
        line = st.fixed_dictionaries(
            {"description": TEXT, "qty": QTY, "price": PRICE},
            optional={"unit": st.sampled_from(["h", "pcs"]), "vat": maybe_none(VATS)},
        )
        opt = dict(
            project_id=self.ref("projects"), title=maybe_none(TEXT), date=DATES, vat_rate=maybe_none(VATS),
            reference=maybe_none(TEXT), notes=maybe_none(TEXT), service_from=maybe_none(iso_days()),
        )  # fmt: skip
        if coll == "offers":
            plan = st.fixed_dictionaries(
                {"start": iso_days(0, 10), "end": iso_days(20, 120)},
                optional=dict(hours_per_week=maybe_none(RATE), total_hours=maybe_none(RATE), rate=maybe_none(RATE),
                              user_ids=st.lists(self.user_ref(), max_size=2, unique=True)),
            )  # fmt: skip
            opt["plan"] = plan
            opt["valid_until"] = maybe_none(iso_days(5, 40))
        else:
            opt["due_date"] = maybe_none(iso_days(5, 40))
        return st.fixed_dictionaries(
            {"customer_id": self.ref("customers", dangling=False, none=False) if self.m["customers"] else st.none(),
             "lines": st.lists(line, min_size=0, max_size=4)},
            optional=opt,
        )  # fmt: skip

    # ------------------------------------------------------------------ expectations
    def expected(self, coll, body, old, actor_user):
        exp = {k: v for k, v in body.items() if v is not None}
        for k, v in DEFAULTS[coll].items():
            exp.setdefault(k, v)
        if coll in ("projects",):
            exp["member_ids"] = list(body.get("member_ids") or [])
        if coll in ("entries", "absences") and actor_user["role"] != "boss":
            exp["user_id"] = actor_user["id"]
        if coll == "entries" and actor_user["role"] != "boss":
            # billing fields are boss/system-only: a member can neither set nor change them
            for k in ("rate", "invoice_id"):
                exp.pop(k, None)
                if old and k in old:
                    exp[k] = old[k]
        if coll == "entries" and old and old.get("invoice_id") and "invoice_id" not in exp:
            exp["invoice_id"] = old["invoice_id"]
        return exp

    def check_record(self, coll, exp, act):
        assert act["id"] == exp["id"]
        subset_eq({k: v for k, v in exp.items() if k not in ("id", "created", "updated")}, act)
        for k in OPTIONAL[coll]:
            if k not in exp:
                assert k not in act, f"{coll}.{k} should be absent, got {act[k]!r}"

    def doc_expected(self, coll, body, old):
        exp = {k: v for k, v in body.items() if v is not None}
        exp.setdefault("status", "draft")
        exp.setdefault("lines", [])
        if old is None:
            exp["status"] = "draft"
            exp["date"] = exp.get("date") or date.today().isoformat()
        else:
            exp.pop("number", None)
            if old.get("number"):
                exp["number"] = old["number"]
            exp["date"] = exp.get("date") or old.get("date") or date.today().isoformat()  # a PUT cannot drop the date
        exp.setdefault("template_id", "classic")
        lines = []
        for ln in exp["lines"]:
            nl = {k: v for k, v in ln.items() if v is not None}
            nl["amount"] = oracle.line_amount(nl)
            lines.append(nl)
        exp["lines"] = lines
        if exp.get("plan") is not None:
            exp["plan"] = {k: v for k, v in exp["plan"].items() if v is not None}
            exp["plan"].setdefault("user_ids", [])
        return exp

    # ------------------------------------------------------------------ seed
    @initialize(data=st.data())
    def seed_world(self, data):
        """Start from a populated workspace so every rule is reachable from step one."""
        boss = self.sessions[0]
        for i in range(2):
            self.op_create_user(boss, f"member{i}", "member-pass-1", "employee", 40.0, [0, 1, 2, 3, 4], None)
        self.refresh_sessions()
        for coll, n in (("customers", 2), ("projects", 3), ("tasks", 2), ("entries", 5), ("absences", 1), ("expenses", 2),
                        ("contracts", 1), ("templates", 1)):  # fmt: skip
            for _ in range(n):
                self.op_create(boss, coll, data.draw(self.body_st(coll)))
        for coll in ("offers", "invoices", "invoices"):
            self.op_create_doc(boss, coll, data.draw(self.doc_st(coll)))
        self.dirty |= set(self.m)

    # ------------------------------------------------------------------ rules: time & auth
    @rule(secs=st.sampled_from([1, 5, 60, 900, 3600]))
    def time_passes(self, secs):
        self.tick(secs)

    @precondition(lambda self: self.steps % 9 == 0)
    @rule(secs=st.sampled_from([TTL - 5, TTL + 5, 2 * TTL]))
    def tokens_expire(self, secs):
        """Tokens are valid for exactly 12 h: just inside the window they still work, just outside they do not."""
        self.tick(secs)
        for sess in self.sessions[-3:]:
            r = self.call("GET", "/auth/me", sess)
            assert r.status_code == (200 if self.valid(sess) else 401), (secs, sess.iat, self.now)

    @rule(data=st.data())
    def login(self, data):
        self.tick()
        users = [u for u in self.m["users"].values()]
        pick = data.draw(st.sampled_from(users)) if users else None
        kind = data.draw(st.sampled_from(["right", "right", "wrong", "unknown", "case"]))
        if kind == "unknown":
            name, pw = data.draw(USERNAMES), data.draw(PASSWORDS)
        elif kind == "wrong":
            name, pw = pick["username"], pick["password"] + "x"
        elif kind == "case":
            name, pw = f" {pick['username'].upper()} ", pick["password"]
        else:
            name, pw = pick["username"], pick["password"]
        self.do_login(name, pw)

    @rule(data=st.data())
    def logout(self, data):
        self.tick()
        r = self.call("POST", "/auth/logout", data.draw(self.actor()))
        assert r.status_code == 204

    @rule(data=st.data(), new=PASSWORDS)
    def change_own_password(self, data, new):
        self.tick()
        s = data.draw(self.actor())
        wrong = data.draw(st.booleans()) and self.valid(s)
        old_pw = self.m["users"][s.user_id]["password"] if self.valid(s) else "whatever1"
        r = self.call(
            "POST",
            "/auth/change-password",
            s,
            json={"old_password": old_pw + ("!" if wrong else ""), "new_password": new},
        )
        if not self.valid(s):
            assert r.status_code == 401
        elif wrong or len(new) < 8:
            assert r.status_code == 400, r.text
        else:
            assert r.status_code == 200, r.text
            u = self.m["users"][s.user_id]
            u["password"], u["pw_changed"] = new, self.now
            self.sessions.append(Session(r.json()["token"], s.user_id, self.now))

    @rule(data=st.data())
    def anonymous_and_forged_probes(self, data):
        """Protected endpoints must answer 401 without a valid token - whatever else is in the request."""
        self.tick()
        s = data.draw(st.sampled_from([None, Session("x.y.z", None, 0), Session("", None, 0)]))
        method, path, kw = data.draw(st.sampled_from([
            ("GET", "/customers", {}), ("GET", "/users", {}), ("GET", "/bootstrap", {}), ("GET", "/settings", {}),
            ("GET", "/finance", {}), ("GET", "/dashboard", {}), ("GET", "/timer", {}), ("GET", "/forecast", {}),
            ("GET", "/auth/me", {}), ("POST", "/customers", {"json": {"name": "x"}}),
            ("DELETE", f"/invoices/{DEAD_ID}", {}), ("PUT", f"/entries/{DEAD_ID}", {"json": {"date": "2026-01-01", "minutes": 5}}),
            ("POST", "/users", {"json": {"username": "evil", "name": "e", "password": "password1", "role": "boss"}}),
            ("POST", "/timer/start", {"json": {}}), ("GET", f"/attachments/{DEAD_ID}.pdf", {}),
        ]))  # fmt: skip
        r = self.call(method, path, s, **kw)
        assert r.status_code == 401, (method, path, r.status_code, r.text)

    @rule(data=st.data())
    def member_probes(self, data):
        """Members never see boss-only data."""
        self.tick()
        members = [s for s in self.sessions if self.valid(s) and self.role_of(s) == "employee"]
        if not members:
            return
        s = data.draw(st.sampled_from(members))
        path = data.draw(st.sampled_from(
            ["/customers", "/contracts", "/expenses", "/templates", "/offers", "/invoices", "/settings", "/finance",
             "/project-report", "/forecast", f"/attachments/{DEAD_ID}.pdf"]))  # fmt: skip
        assert self.call("GET", path, s).status_code == 403, path

    @rule(data=st.data(), attempts=st.integers(5, 8))
    def brute_force(self, data, attempts):
        """Hammer one account with wrong passwords: from the 6th try within 5 minutes the server must say 429,
        even for the right password, and recover once the window has passed."""
        self.tick()
        u = data.draw(st.sampled_from(list(self.m["users"].values())))
        for _ in range(attempts):
            self.do_login(u["username"], u["password"] + "?")
        self.do_login(u["username"], u["password"])
        self.tick(301)
        self.do_login(u["username"], u["password"])

    @rule(data=st.data())
    def member_tampering(self, data):
        """A member books time and plans absences for somebody else, or edits/deletes other people's records."""
        self.tick()
        members = [x for x in self.sessions if self.valid(x) and self.role_of(x) == "employee"]
        if not members:
            return
        s = data.draw(st.sampled_from(members))
        other = data.draw(st.sampled_from([u for u in self.m["users"] if u != s.user_id]))
        coll = data.draw(st.sampled_from(["entries", "absences"]))
        body = data.draw(self.body_st(coll)) | {"user_id": other}
        self.op_create(s, coll, body)  # must be stored for the member, not for `other`
        foreign = [i for i, rec in self.m[coll].items() if rec.get("user_id") not in (None, s.user_id)]
        if foreign:
            rid = data.draw(st.sampled_from(foreign))
            assert self.call("PUT", f"/{coll}/{rid}", s, json=body).status_code == 403
            assert self.call("DELETE", f"/{coll}/{rid}", s).status_code == 403
            assert self.call("GET", f"/{coll}/{rid}", s).status_code == 403
        self.dirty.add(coll)

    # ------------------------------------------------------------------ rules: timer
    @rule(data=st.data())
    def timer_start(self, data):
        self.tick()
        s = data.draw(self.actor())
        body = {
            "project_id": data.draw(self.ref("projects")),
            "task_id": data.draw(self.ref("tasks")),
            "note": data.draw(TEXT),
        }
        r = self.call("POST", "/timer/start", s, json=body)
        if (g := self.gate(s)) is not None:
            assert r.status_code == g
            return
        assert r.status_code == 200 and r.json()["running"] is True and r.json()["note"] == body["note"]
        uid = s.user_id
        if self.timers.get(uid):  # starting while running stops the old one and books it
            self._booked_entry(uid, self.timers[uid], None)
        self.timers[uid] = dict(body)
        self.dirty |= {"entries"}

    @rule(data=st.data(), note=maybe_none(TEXT))
    def timer_stop(self, data, note):
        self.tick()
        s = data.draw(self.actor())
        before = {e["id"] for e in self.call("GET", "/entries", self.mint(self.boss_id)).json()}
        days = {date.today().isoformat()}  # the server stamps the entry date; accept the day before/after the call
        r = self.call("POST", "/timer/stop", s, json={"note": note})
        days.add(date.today().isoformat())
        if (g := self.gate(s)) is not None:
            assert r.status_code == g
            return
        assert r.status_code == 200
        t = self.timers.get(s.user_id)
        if not t:
            assert r.json() == {"running": False}
            return
        e = r.json()["entry"]
        assert e["id"] not in before and e["user_id"] == s.user_id and e["billable"] is True
        assert e["date"] in days and 1 <= e["minutes"] <= 2
        assert e.get("project_id") == t["project_id"] and e.get("task_id") == t["task_id"]
        assert e["note"] == (note if note is not None else t["note"])
        self.m["entries"][e["id"]] = {k: v for k, v in e.items() if v is not None and k not in ("created", "updated")}
        self.timers[s.user_id] = None
        self.dirty |= {"entries"}
        assert self.call("GET", "/timer", s).json()["running"] is False

    def _booked_entry(self, uid, t, note):
        boss = self.mint(self.boss_id)
        mine = [e for e in self.call("GET", "/entries", boss).json() if e.get("user_id") == uid]
        known = {i for i in self.m["entries"]}
        new = [e for e in mine if e["id"] not in known]
        assert len(new) == 1, new
        e = new[0]
        self.m["entries"][e["id"]] = {k: v for k, v in e.items() if k not in ("created", "updated")}

    # ------------------------------------------------------------------ rules: generic CRUD
    @precondition(lambda self: sum(len(v) for v in self.m.values()) < 70)
    @rule(data=st.data(), coll=st.sampled_from(CRUD))
    def create(self, data, coll):
        self.tick()
        self.op_create(data.draw(self.actor()), coll, data.draw(self.body_st(coll)))

    def op_create(self, s, coll, body):
        r = self.call("POST", f"/{coll}", s, json=body)
        if (g := self.gate(s, boss_only=coll not in MEMBER_OWN)) is not None:
            assert r.status_code == g, (coll, r.status_code, r.text)
            return
        assert r.status_code == 200, r.text
        exp = self.expected(coll, body, None, self.m["users"][s.user_id]) | {"id": r.json()["id"]}
        self.check_record(coll, exp, r.json())
        self.m[coll][exp["id"]] = exp
        self.dirty.add(coll)

    @precondition(lambda self: any(self.m[c] for c in CRUD))
    @rule(data=st.data(), coll=st.sampled_from(CRUD))
    def update(self, data, coll):
        self.tick()
        s = data.draw(self.actor())
        ids = list(self.m[coll]) + [DEAD_ID]
        rid = data.draw(st.sampled_from(ids))
        body = data.draw(self.body_st(coll))
        r = self.call("PUT", f"/{coll}/{rid}", s, json=body)
        if (g := self.gate(s)) is not None:
            assert r.status_code == g
            return
        old = self.m[coll].get(rid)
        me = self.m["users"][s.user_id]
        if me["role"] != "boss" and coll not in MEMBER_OWN:
            assert r.status_code == 403  # the area check comes before the lookup: nothing is revealed
            return
        if old is None:
            assert r.status_code == 404
            return
        if me["role"] != "boss" and (coll not in MEMBER_OWN or old.get("user_id") != me["id"]):
            assert r.status_code == 403
            return
        if coll == "entries" and old.get("invoice_id") and me["role"] != "boss":
            assert r.status_code == 403
            return
        assert r.status_code == 200, r.text
        exp = self.expected(coll, body, old, me) | {"id": rid}
        self.check_record(coll, exp, r.json())
        self.m[coll][rid] = exp
        self.dirty.add(coll)

    @precondition(lambda self: any(self.m[c] for c in (*CRUD, "offers", "invoices")))
    @rule(data=st.data(), coll=st.sampled_from((*CRUD, "offers", "invoices")))
    def delete(self, data, coll):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m[coll]) + [DEAD_ID]))
        r = self.call("DELETE", f"/{coll}/{rid}", s)
        if (g := self.gate(s)) is not None:
            assert r.status_code == g
            return
        old = self.m[coll].get(rid)
        if coll == "invoices" and old is not None and old.get("number"):
            assert r.status_code == 409  # numbered invoices are cancelled, never deleted (gapless numbering)
            return
        me = self.m["users"][s.user_id]
        if me["role"] != "boss" and coll not in MEMBER_OWN:
            assert r.status_code == 403
            return
        if old is not None and me["role"] != "boss":
            own = coll in MEMBER_OWN and old.get("user_id") == me["id"] and not old.get("invoice_id")
            if not own:
                assert r.status_code == 403
                return
        if coll == "projects" and old is not None and any(a["project_id"] == rid for a in self.allocs.values()):
            assert r.status_code == 409  # a project with allocations cannot be deleted
            return
        assert r.status_code == 204
        if old is None:
            return
        del self.m[coll][rid]
        self.graveyard.append(rid)
        if coll == "invoices":
            for e in self.m["entries"].values():
                if e.get("invoice_id") == rid:
                    del e["invoice_id"]
            self.dirty.add("entries")
        self.dirty.add(coll)

    @precondition(lambda self: any(self.m[c] for c in (*CRUD, "offers", "invoices")))
    @rule(data=st.data(), coll=st.sampled_from((*CRUD, "offers", "invoices")))
    def get_one(self, data, coll):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m[coll]) + [DEAD_ID]))
        r = self.call("GET", f"/{coll}/{rid}", s)
        if (g := self.gate(s)) is not None:
            assert r.status_code == g
            return
        old, me = self.m[coll].get(rid), self.m["users"][s.user_id]
        if me["role"] != "boss" and coll not in ("projects", "tasks", "entries", "absences"):
            assert r.status_code == 403  # the area check comes before the lookup
        elif old is None:
            assert r.status_code == 404
        elif me["role"] != "boss" and coll in ("projects", "tasks"):
            pid = rid if coll == "projects" else old.get("project_id")
            proj = self.m["projects"].get(pid)
            if proj is None or (proj["member_ids"] and me["id"] not in proj["member_ids"]):
                assert r.status_code == 404  # existence of unassigned projects/tasks is not revealed
            else:
                assert r.status_code == 200
                body = r.json()
                assert body["id"] == rid
                assert not ({"rate", "notes", "budget_hours"} & set(body)), body
        elif me["role"] != "boss" and (
            coll not in ("projects", "tasks", "entries", "absences")
            or (coll in MEMBER_OWN and old.get("user_id") != me["id"])
        ):
            assert r.status_code == 403
        else:
            assert r.status_code == 200
            self.check_record(coll, old, r.json()) if coll not in ("offers", "invoices") else None

    # ------------------------------------------------------------------ rules: offers & invoices
    @precondition(lambda self: len(self.m["offers"]) + len(self.m["invoices"]) < 14)
    @rule(data=st.data(), coll=st.sampled_from(["offers", "invoices"]))
    def create_doc(self, data, coll):
        self.tick()
        self.op_create_doc(data.draw(self.actor()), coll, data.draw(self.doc_st(coll)))

    def op_create_doc(self, s, coll, body):
        r = self.call("POST", f"/{coll}", s, json=body)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        assert r.status_code == 200, r.text
        exp = self.doc_expected(coll, body, None) | {"id": r.json()["id"]}
        self.check_record(coll, exp, r.json())
        self.m[coll][exp["id"]] = exp
        self.bodies[exp["id"]] = body
        self.dirty.add(coll)

    @precondition(lambda self: self.m["offers"] or self.m["invoices"])
    @rule(
        data=st.data(),
        coll=st.sampled_from(["offers", "invoices"]),
        status=st.sampled_from(["draft", "sent", "paid", "cancelled", "accepted", "rejected"]),
        paid=DATES,
    )
    def update_doc(self, data, coll, status, paid):
        """PUT replaces the document; this also covers 'mark as paid' and every status change."""
        self.tick()
        if not self.m[coll]:
            return
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m[coll])))
        body = (
            copy.deepcopy(data.draw(self.doc_st(coll))) if data.draw(st.booleans()) else copy.deepcopy(self.bodies[rid])
        )
        body["status"] = status
        if status == "paid":
            body["paid_date"] = paid
        r = self.call("PUT", f"/{coll}/{rid}", s, json=body)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        assert r.status_code == 200, r.text
        exp = self.doc_expected(coll, body, self.m[coll][rid]) | {"id": rid}
        self.check_record(coll, exp, r.json())
        self.m[coll][rid], self.bodies[rid] = exp, body
        self.dirty.add(coll)

    def next_number(self, coll, year):
        """Sequences are per document type and per year of the document date; a counter never moves back."""
        prefix = "RE" if coll == "invoices" else "AN"
        n = self.counters.get((prefix, year), 0) + 1
        self.counters[(prefix, year)] = n
        return f"{prefix}-{year}-{n:04d}"

    @precondition(lambda self: self.m["offers"] or self.m["invoices"])
    @rule(data=st.data(), coll=st.sampled_from(["offers", "invoices"]))
    def finalize(self, data, coll):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m[coll]) + [DEAD_ID]))
        r = self.call("POST", f"/{coll}/{rid}/finalize", s)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        doc = self.m[coll].get(rid)
        if doc is None:
            assert r.status_code == 404
            return
        if not doc.get("customer_id") or not doc["lines"]:
            assert r.status_code == 400
            return
        assert r.status_code == 200, r.text
        new = dict(doc)
        base = date.fromisoformat(new["date"]) if new.get("date") else TODAY
        if not new.get("number"):
            new["number"] = self.next_number(coll, base.year)
        if new["status"] == "draft":
            new["status"] = "sent"
        if coll == "invoices":
            new.setdefault("due_date", (base + timedelta(days=14)).isoformat())
        else:
            new.setdefault("valid_until", (base + timedelta(days=30)).isoformat())
        self.check_record(coll, new, r.json())
        self.m[coll][rid] = new
        self.bodies[rid] = {**self.bodies[rid], "status": new["status"]}
        self.dirty.add(coll)

    @precondition(lambda self: self.m["offers"] or self.m["invoices"])
    @rule(data=st.data(), coll=st.sampled_from(["offers", "invoices"]))
    def fetch_pdf(self, data, coll):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m[coll]) + [DEAD_ID]))
        r = self.call("GET", f"/{coll}/{rid}/pdf", s)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
        elif rid not in self.m[coll]:
            assert r.status_code == 404
        else:
            assert r.status_code == 200 and r.content.startswith(b"%PDF-") and r.content.rstrip().endswith(b"%%EOF")
            assert r.headers["content-type"] == "application/pdf"

    def entry_rate(self, e):
        return oracle.resolve_rate(e, self.m["tasks"], self.m["projects"], self.m["customers"], self.m["users"], 80)

    @rule(
        data=st.data(),
        by_customer=st.booleans(),
        by_project=st.booleans(),
        lo=maybe_none(iso_days(-25, 0)),
        hi=maybe_none(iso_days(-3, 8)),
    )
    def invoice_from_time(self, data, by_customer, by_project, lo, hi):
        self.tick()
        s = data.draw(self.actor())
        cust = data.draw(self.ref("customers", none=False)) if by_customer else None
        proj = data.draw(self.ref("projects", none=False)) if by_project else None
        days = {date.today().isoformat()}  # server-stamped invoice date: accept the day before/after the call
        r = self.call(
            "POST", "/invoices/from-time", s, json={"customer_id": cust, "project_id": proj, "from": lo, "to": hi}
        )
        days.add(date.today().isoformat())
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        picked, groups = [], {}
        for e in self.m["entries"].values():
            p = self.m["projects"].get(e.get("project_id"))
            if e.get("invoice_id") or not e["billable"] or not p or p["personal"]:
                continue
            if (lo and e["date"] < lo) or (hi and e["date"] > hi):
                continue
            if (proj and p["id"] != proj) or (cust and p.get("customer_id") != cust):
                continue
            t = self.m["tasks"].get(e.get("task_id"))
            key = (p["id"], t["id"] if t else None, self.entry_rate(e))
            groups.setdefault(key, [0, p, t])[0] += e["minutes"]
            picked.append(e)
        if not picked:
            assert r.status_code == 400, r.text
            return
        assert r.status_code == 200, r.text
        inv = r.json()
        want = sorted((p["name"] + (" – " + t["name"] if t else ""), round(mins / 60, 2), rate, "h")
                      for (_, _, rate), (mins, p, t) in groups.items())  # fmt: skip
        got = sorted((ln["description"], ln["qty"], ln["price"], ln["unit"]) for ln in inv["lines"])
        assert len(want) == len(got) and all(a[0] == b[0] and approx(a[1], b[1]) and approx(a[2], b[2]) and a[3] == b[3]
                                             for a, b in zip(want, got)), (want, got)  # fmt: skip
        assert approx(sum(ln["amount"] for ln in inv["lines"]), sum(oracle.line_amount(ln) for ln in inv["lines"]))
        dates = sorted(e["date"] for e in picked)
        assert inv["status"] == "draft" and inv["service_from"] == dates[0] and inv["service_to"] == dates[-1]
        assert inv["vat_rate"] == 19 and inv["date"] in days and ("number" not in inv)
        candidates = {self.m["projects"][k[0]].get("customer_id") for k in groups}
        assert inv.get("customer_id") == cust if cust else inv.get("customer_id") in candidates | {None}
        assert inv.get("project_id") == proj if proj else "project_id" not in inv
        for e in picked:
            e["invoice_id"] = inv["id"]
        body = {k: v for k, v in inv.items() if k not in ("id", "created", "updated", "number")}
        exp = self.doc_expected("invoices", {**body, "status": "draft"}, None) | {"id": inv["id"]}
        self.m["invoices"][inv["id"]] = exp
        self.bodies[inv["id"]] = body
        self.dirty |= {"entries", "invoices"}

    @precondition(lambda self: any(o["status"] != "accepted" for o in self.m["offers"].values()))
    @rule(data=st.data())
    def accept_offer(self, data):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from([k for k, o in list(self.m["offers"].items()) if o["status"] != "accepted"]))
        r = self.call("POST", f"/offers/{rid}/accept", s)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        assert r.status_code == 200, r.text
        out, offer = r.json(), self.m["offers"][rid]
        assert out["offer"]["status"] == "accepted"
        cust = self.m["customers"].get(offer.get("customer_id"))
        proj = out["project"]
        existing = self.m["projects"].get(offer.get("project_id"))
        if existing:  # the offer belongs to a project: it is widened, not replaced
            assert proj["id"] == offer["project_id"] and proj["name"] == existing["name"]
        else:
            assert proj["name"] == (offer.get("title") or "Project for " + (cust or {}).get("name", ""))
            assert (
                proj["status"] == "active"
                and proj["offer_id"] == rid
                and proj.get("customer_id") == offer.get("customer_id")
            )
        offer["status"] = "accepted"
        self.bodies[rid] = {**self.bodies[rid], "status": "accepted"}
        self.m["projects"][proj["id"]] = DEFAULTS["projects"] | {
            k: v for k, v in proj.items() if v is not None and k not in ("created", "updated")
        }
        plan = offer.get("plan")
        if plan and plan.get("start") and plan.get("end"):
            c = out["contract"]
            assert c is not None and c["start"] == plan["start"] and c["end"] == plan["end"]
            assert c["offer_id"] == rid and c["project_id"] == proj["id"] and c["status"] == "confirmed"
            assert c["user_ids"] == plan["user_ids"]
            for k in ("hours_per_week", "total_hours", "rate"):
                assert (c.get(k) == plan.get(k)) or approx(c.get(k), plan.get(k)), (k, c, plan)
            self.m["contracts"][c["id"]] = DEFAULTS["contracts"] | {
                k: v for k, v in c.items() if v is not None and k not in ("created", "updated")
            }
            self.dirty.add("contracts")
        else:
            assert out["contract"] is None
        self.dirty |= {"offers", "projects"}

    @precondition(lambda self: self.m["offers"])
    @rule(data=st.data())
    def offer_to_invoice(self, data):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m["offers"])))
        r = self.call("POST", f"/offers/{rid}/to-invoice", s)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        assert r.status_code == 200, r.text
        o = self.m["offers"][rid]
        body = {
            k: o[k]
            for k in ("customer_id", "project_id", "lines", "template_id", "vat_rate", "language", "title", "reference")
            if k in o
        }
        body.update(date=TODAY.isoformat(), offer_id=rid)
        exp = self.doc_expected("invoices", body, None) | {"id": r.json()["id"]}
        self.check_record("invoices", exp, r.json())
        self.m["invoices"][exp["id"]] = exp
        self.bodies[exp["id"]] = body
        self.dirty.add("invoices")

    @rule(data=st.data(), key=st.sampled_from(["kleinunternehmer", "accounting"]), flag=st.booleans())
    def change_settings(self, data, key, flag):
        self.tick()
        s = data.draw(self.actor())
        val = flag if key == "kleinunternehmer" else ("accrual" if flag else "eur")
        r = self.call("PUT", "/settings", s, json={key: val})
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        assert r.status_code == 200 and r.json()[key] == val
        assert r.json()["company_name"] == ""  # untouched fields are merged, not reset
        self.settings[key] = val
        self.dirty |= {"invoices", "expenses", "settings"}

    # ------------------------------------------------------------------ rules: users
    @rule(
        data=st.data(),
        username=USERNAMES,
        password=PASSWORDS,
        role=st.sampled_from(["employee", "employee", "boss", "root"]),
        hours=st.sampled_from([20.0, 32.5, 40.0]),
        workdays=st.sampled_from([[0, 1, 2, 3, 4], [0, 1, 2], [1, 3, 5]]),
        rate=maybe_none(RATE),
    )
    def create_user(self, data, username, password, role, hours, workdays, rate):
        self.tick()
        s = data.draw(self.actor())
        self.op_create_user(s, username, password, role, hours, workdays, rate)

    def op_create_user(self, s, username, password, role, hours, workdays, rate):
        body = {"username": username, "name": "N " + username.strip()[:8], "role": role, "password": password,
                "weekly_hours": hours, "workdays": workdays, "default_rate": rate}  # fmt: skip
        r = self.call("POST", "/users", s, json=body)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        norm = oracle.normalize_username(username)
        taken = any(u["username"] == norm for u in self.m["users"].values())
        if role not in ("boss", "employee"):
            assert r.status_code == 422, (body, r.text)  # rejected by the request model before any service rule
            return
        if not norm or taken or len(password) < 8:
            assert r.status_code == 400, (body, r.text)
            return
        assert r.status_code == 200, r.text
        u = r.json()
        assert u["username"] == norm and u["role"] == role and u["has_password"] and u["active"] is True
        self.m["users"][u["id"]] = dict(
            id=u["id"], username=norm, name=body["name"], role=role, active=True, weekly_hours=hours,
            workdays=workdays, password=password, pw_changed=self.now,
            **({"default_rate": rate} if rate is not None else {}),
        )  # fmt: skip
        self.dirty.add("users")

    @precondition(lambda self: len(self.m["users"]) > 1)
    @rule(
        data=st.data(),
        username=USERNAMES,
        password=maybe_none(PASSWORDS),
        role=st.sampled_from(["employee", "boss"]),
        active=st.booleans(),
        hours=st.sampled_from([20.0, 40.0]),
    )
    def update_user(self, data, username, password, role, active, hours):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m["users"]) + [DEAD_ID]))
        old = self.m["users"].get(rid)
        keep = data.draw(st.booleans()) and old is not None
        if keep:
            username = old["username"]
        body = {
            "username": username,
            "name": "Renamed",
            "role": role,
            "active": active,
            "weekly_hours": hours,
            "password": password,
        }
        r = self.call("PUT", f"/users/{rid}", s, json=body)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        if old is None:
            assert r.status_code == 404
            return
        norm = oracle.normalize_username(username)
        others = [u for u in self.m["users"].values() if u["id"] != rid]
        bad = (not norm) or any(u["username"] == norm for u in others) or (password and len(password) < 8)
        if old["role"] == "boss" and (role != "boss" or not active):
            bad = bad or not any(u["role"] == "boss" and u["active"] for u in others)
        if bad:
            assert r.status_code == 400, (body, r.text)
            return
        assert r.status_code == 200, r.text
        if old["active"] and not active:
            self.revoke_tokens_of(rid)  # deactivating revokes; reactivating must not revive them
        old.update(
            username=norm, name="Renamed", role=role, active=active, weekly_hours=hours, workdays=[0, 1, 2, 3, 4]
        )
        if password:
            old["password"], old["pw_changed"] = password, self.now
        got = r.json()
        assert got["username"] == norm and got["role"] == role and got["active"] == active and got["has_password"]
        self.dirty |= {"users"}

    @precondition(lambda self: len(self.m["users"]) > 1)
    @rule(data=st.data())
    def delete_user(self, data):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.m["users"]) + [DEAD_ID]))
        r = self.call("DELETE", f"/users/{rid}", s)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        old = self.m["users"].get(rid)
        if old is None:
            assert r.status_code == 204
        elif rid == s.user_id:
            assert r.status_code == 400
        elif any(a["user_id"] == rid for a in self.allocs.values()):
            assert r.status_code == 409  # a person with allocations is deactivated, not deleted
        else:
            assert r.status_code == 204, r.text
            del self.m["users"][rid]
            self.timers.pop(rid, None)
            self.dirty.add("users")

    # ------------------------------------------------------------------ rules: allocations (planner per project)
    @precondition(lambda self: len(self.allocs) < 12)
    @rule(
        data=st.data(),
        hpw=st.sampled_from([0.25, 10.0, 30.0, 80.0]),
        span=st.integers(-3, 40),
        project_ok=st.booleans(),
    )
    def allocation_create(self, data, hpw, span, project_ok):
        self.tick()
        s = data.draw(self.actor())
        uid = data.draw(st.sampled_from(list(self.m["users"]) + [DEAD_ID]))
        pid = data.draw(st.sampled_from(list(self.m["projects"]))) if project_ok and self.m["projects"] else DEAD_ID
        start = TODAY + timedelta(days=data.draw(st.integers(0, 20)))
        body = dict(user_id=uid, project_id=pid, start=start.isoformat(),
                    end=(start + timedelta(days=span)).isoformat(), hours_per_week=hpw)  # fmt: skip
        r = self.call("POST", "/allocations", s, json=body)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        user, project = self.m["users"].get(uid), self.m["projects"].get(pid)
        if user is None:
            assert r.status_code == 404 and r.json()["detail"] == "User not found"
        elif project is None:
            assert r.status_code == 404 and r.json()["detail"] == "Project not found"
        elif span < 0:
            assert r.status_code == 409
        elif project["status"] in ("done", "archived"):
            assert r.status_code == 409 and r.json()["detail"] == "The project is closed"
        elif not user["active"]:
            assert r.status_code == 409 and r.json()["detail"] == "The user is not active"
        else:
            assert r.status_code == 201, r.text
            got = r.json()
            assert got["kind"] == "employee" and got["status"] == "active" and got["hours_per_week"] == hpw
            assert got["planned_hours"] >= 0 and isinstance(got["planning_warnings"], list)
            self.allocs[got["id"]] = {k: body[k] for k in body}
            return
        assert r.status_code in (404, 409)

    @precondition(lambda self: self.allocs)
    @rule(data=st.data())
    def allocation_delete(self, data):
        self.tick()
        s = data.draw(self.actor())
        rid = data.draw(st.sampled_from(list(self.allocs) + [DEAD_ID]))
        r = self.call("DELETE", f"/allocations/{rid}", s)
        if (g := self.gate(s, boss_only=True)) is not None:
            assert r.status_code == g
            return
        if rid in self.allocs:
            assert r.status_code == 204
            del self.allocs[rid]
        else:
            assert r.status_code == 404

    def allocations_match_model(self):
        rows = self.call("GET", "/allocations", self._boss()).json()
        assert {r["id"] for r in rows} == set(self.allocs)
        for row in rows:
            subset_eq(self.allocs[row["id"]], row)
        # ring fence: whatever the allocations, the scopes of a week add up to the team row and the gaps to its gap
        out = self.call("GET", "/forecast", self._boss(), params={"weeks": 6}).json()
        for w in out["weeks"]:
            assert abs(sum(p["capacity"] for p in w["projects"]) - w["capacity"]) < 0.01
            assert abs(sum(p["gap"] for p in w["projects"]) - w["gap"]) < 0.01
            assert (w["gap"] > 0.001) <= (w["status"] == "over")

    # ------------------------------------------------------------------ rules: API tokens
    def revoke_tokens_of(self, user_id):
        for t in self.tokens:
            if t["user_id"] == user_id:
                t["revoked"] = True

    def tok_dead(self, t) -> bool:
        owner = self.m["users"].get(t["user_id"])
        return bool(t["revoked"] or t["expires_at"] <= self.now or owner is None or not owner["active"])

    def throttled(self) -> bool:
        self.tok_fails = [x for x in self.tok_fails if self.clock.now - x < 300]
        return len(self.tok_fails) >= 5

    def snapshot(self) -> dict:
        """What a token must not change when it is refused: the data as the boss sees it."""
        boss = self._boss()
        snap = {c: self.call("GET", f"/{c}", boss).json() for c in ("customers", "entries", "settings", "users")}
        rows = self.call("GET", "/tokens", boss, params={"all": "true"}).json()
        snap["tokens"] = {t["id"]: t["status"] for t in rows}
        return snap

    @rule(data=st.data(), days=st.sampled_from([1, 1, 2, 30]), read_only=st.booleans())
    def create_token(self, data, days, read_only):
        self.tick()
        s = data.draw(self.actor())
        body = {"label": f"tok{len(self.tokens)}", "expires_in_days": days, "read_only": read_only}
        r = self.call("POST", "/tokens", s, json=body)
        if (g := self.gate(s)) is not None:
            assert r.status_code == g, r.text
            return
        mine = [t for t in self.tokens if t["user_id"] == s.user_id and not t["revoked"] and t["expires_at"] > self.now]
        if len(mine) >= 20:
            assert r.status_code == 409, r.text
            return
        assert r.status_code == 201, r.text
        got = r.json()
        item = got["item"]
        assert len(got["token"]) == 47 and got["token"].startswith("pat_") and "token" not in item
        assert item["read_only"] is read_only and item["user_id"] == s.user_id and item["status"] == "active"
        assert item["expires_at"] == self.now + days * 86400, (item, self.now)
        assert item["hint"] == got["token"][:8]
        self.tokens.append(dict(secret=got["token"], id=item["id"], user_id=s.user_id, read_only=read_only,
                                expires_at=item["expires_at"], revoked=False))  # fmt: skip

    @precondition(lambda self: self.tokens)
    @rule(data=st.data(), as_owner=st.booleans())
    def revoke_token(self, data, as_owner):
        self.tick()
        tok = data.draw(st.sampled_from(self.tokens))
        owner = self.m["users"].get(tok["user_id"])
        s = self.mint(owner["id"]) if as_owner and owner and owner["active"] else data.draw(self.actor())
        r = self.call("POST", f"/tokens/{tok['id']}/revoke", s)
        if (g := self.gate(s)) is not None:
            assert r.status_code == g, r.text
            return
        if owner is None or (s.user_id != tok["user_id"] and self.role_of(s) != "boss"):
            assert r.status_code == 404, r.text  # deleted owner, or a token the caller may not see
            return
        assert r.status_code == 200 and r.json()["status"] == "revoked", r.text
        tok["revoked"] = True

    @rule(secs=st.sampled_from([86399, 86400, 86401]))
    def day_passes(self, secs):
        """Tokens live in whole days; the boundary `expires_at == now` is already expired."""
        self.tick(secs)

    @precondition(lambda self: self.tokens)
    @rule(data=st.data(), call=st.sampled_from(sorted(TOKEN_CALLS)))
    def use_token(self, data, call):
        self.tick()
        tok = data.draw(st.sampled_from(self.tokens))
        method, path, safe, session_only = TOKEN_CALLS[call]
        kw = {}
        if call == "post_customer":
            kw["json"] = data.draw(self.body_st("customers"))
        elif call == "delete_customer":
            path = path.format(cid=data.draw(st.sampled_from([*self.m["customers"], DEAD_ID])))
        elif call == "put_settings":
            kw["json"] = {"kleinunternehmer": not self.settings["kleinunternehmer"]}
        elif call == "create_token":
            kw["json"] = {"label": "by a token", "read_only": False}
        path = path.format(tok=tok["id"])
        guest = Session(tok["secret"], None, 0)
        before = None if safe else self.snapshot()
        throttled = self.throttled()
        r = self.call(method, path, guest, **kw)
        owner = self.m["users"].get(tok["user_id"])
        changed = False
        if self.tok_dead(tok):  # an invalid token is refused (429 while the address is locked out); always counted
            if throttled:
                assert r.status_code == 429 and int(r.headers["Retry-After"]) >= 1, (r.status_code, r.text)
            else:
                assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}, (call, r.status_code)
            self.tok_fails.append(self.clock.now)
        elif session_only:
            assert r.status_code == 403 and r.json() == {"detail": SESSION_ONLY}, (call, r.status_code, r.text)
        elif not safe and tok["read_only"]:
            assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}, (call, r.status_code, r.text)
        elif call == "me":
            me = r.json()
            assert r.status_code == 200 and me["id"] == owner["id"] and "_auth" not in me, r.text
            assert me["auth"]["kind"] == "token" and me["auth"]["token_id"] == tok["id"]
            assert me["auth"]["read_only"] is tok["read_only"] and "set-cookie" not in r.headers
        elif safe:
            same = self.call(method, path, self.mint(owner["id"]))  # exactly what the owner's session gets
            assert (r.status_code, r.json()) == (same.status_code, same.json()), (call, r.status_code)
        elif owner["role"] != "boss":
            assert r.status_code == 403, (call, r.status_code, r.text)
        elif call == "post_customer":
            assert r.status_code == 200, r.text
            exp = self.expected("customers", kw["json"], None, owner) | {"id": r.json()["id"]}
            self.check_record("customers", exp, r.json())
            self.m["customers"][exp["id"]] = exp
            self.dirty.add("customers")
            changed = True
        else:  # delete_customer by a read-write boss token
            assert r.status_code == 204, r.text
            cid = path.rsplit("/", 1)[1]
            if cid in self.m["customers"]:
                del self.m["customers"][cid]
                self.graveyard.append(cid)
                self.dirty.add("customers")
                changed = True
        if before is not None and not changed:
            assert self.snapshot() == before, f"{call} with a refused token changed data"

    @rule(data=st.data(), attempts=st.integers(5, 7), unlock=st.booleans())
    def token_brute_force(self, data, attempts, unlock):
        """Guessed tokens from one address lock out further guesses (429) until the window passes; a valid token is
        not affected by the lockout (an attacker must not be able to lock the owner out)."""
        self.tick()
        for _ in range(attempts):
            guess = "pat_" + data.draw(st.text(TOKEN_CHARS, min_size=43, max_size=43))
            throttled = self.throttled()
            r = self.call("GET", "/customers", Session(guess, None, 0))
            if throttled:
                assert r.status_code == 429 and "Retry-After" in r.headers
            else:
                assert r.status_code == 401, r.text
            self.tok_fails.append(self.clock.now)  # counted even while locked out
        live = [t for t in self.tokens if not self.tok_dead(t)]
        if live:
            r = self.call("GET", "/auth/me", Session(live[0]["secret"], None, 0))
            assert r.status_code == 200, (self.throttled(), r.status_code)
        if unlock:
            self.tick(301)

    # ------------------------------------------------------------------ invariants
    def probe(self, tok, path):
        """A token request from a fresh client address (never throttled, never feeds the modelled throttle)."""
        self.probe_n += 1
        ip = f"10.9.{self.probe_n // 250}.{self.probe_n % 250 + 1}"
        return self.call("GET", path, Session(tok["secret"], None, 0), headers={"X-Forwarded-For": ip})

    def tokens_match_model(self):
        """(1) a token never gets more than its owner's session, (2) revoked, expired and deactivated-owner tokens
        always answer 401, (3) the settings are never changed through a token (read-only and session-only rules are
        checked against before/after snapshots in `use_token`)."""
        live = [t for t in self.tokens if not self.tok_dead(t)]
        dead = [t for t in self.tokens if self.tok_dead(t)]
        if live:
            tok = live[self.steps % len(live)]
            ownersess = self.mint(tok["user_id"])
            for path in ("/customers", "/entries", "/settings", "/users"):
                got, want = self.probe(tok, path), self.call("GET", path, ownersess)
                if path == "/users":  # session-only: the owner may list users, the token never
                    assert got.status_code == 403 and got.json() == {"detail": SESSION_ONLY}, path
                else:
                    assert (got.status_code, got.json()) == (want.status_code, want.json()), path
            for write in ("/settings", "/tokens"):
                r = self.call("PUT" if write == "/settings" else "POST", write, Session(tok["secret"], None, 0),
                              headers={"X-Forwarded-For": "10.8.0.1"}, json={"kleinunternehmer": True} if write == "/settings" else {"label": "x"})  # fmt: skip
                assert r.status_code == 403, (write, r.status_code, tok["read_only"])
            settings = self.call("GET", "/settings", self._boss()).json()
            assert all(settings[k] == v for k, v in self.settings.items()), "a token changed the settings"
        for tok in dead[-2:] if len(dead) < 3 else [dead[self.steps % len(dead)], dead[-1]]:
            r = self.probe(tok, "/auth/me")
            assert r.status_code == 401 and r.json() == {"detail": "Not authenticated"}, (tok["id"], r.status_code)

    def _boss(self) -> Session:
        bosses = [u["id"] for u in self.m["users"].values() if u["role"] == "boss" and u["active"]]
        assert bosses, "model lost its last active boss - the API must never allow that"
        return self.mint(bosses[0])

    def sessions_match_model(self):
        """Every token we ever got is accepted or rejected exactly as the model says (expiry, password change,
        deactivation, deletion)."""
        for sess in self.sessions[-4:] + [x for x in self.sessions if not self.valid(x)][-2:]:
            r = self.call("GET", "/auth/me", sess)
            assert r.status_code == (200 if self.valid(sess) else 401), (r.status_code, sess.iat, self.now)

    def lists_match_model(self):
        boss = self._boss()
        todo = self.dirty & (set(CRUD) | {"offers", "invoices"}) or set()
        for coll in sorted(todo):
            rows = self.call("GET", f"/{coll}", boss).json()
            assert {r["id"] for r in rows} == set(self.m[coll]), coll
            for row in rows:
                if coll in ("offers", "invoices"):
                    self.check_record(coll, self.m[coll][row["id"]], row)
                elif coll in (
                    "customers",
                    "projects",
                    "tasks",
                    "contracts",
                    "absences",
                    "expenses",
                    "templates",
                    "entries",
                ):
                    exp = self.m[coll][row["id"]]
                    self.check_record(coll, exp, row)
        if "users" in self.dirty:
            rows = {u["id"]: u for u in self.call("GET", "/users", boss).json()}
            assert set(rows) == set(self.m["users"])
            for uid, u in self.m["users"].items():
                subset_eq({k: u[k] for k in ("username", "name", "role", "active", "weekly_hours")}, rows[uid])
        # a member only ever sees their own entries/absences and stripped projects
        members = [u["id"] for u in self.m["users"].values() if u["role"] == "employee" and u["active"]]
        if members and self.dirty & {"entries", "absences", "projects", "users", "tasks"}:
            uid = members[self.steps % len(members)]
            ms = self.mint(uid)
            assert {e["id"] for e in self.call("GET", "/entries", ms).json()} == {
                i for i, e in self.m["entries"].items() if e.get("user_id") == uid
            }
            assert {e["id"] for e in self.call("GET", "/absences", ms).json()} == {
                i for i, e in self.m["absences"].items() if e.get("user_id") == uid
            }
            visible = {i for i, p in self.m["projects"].items() if not p["member_ids"] or uid in p["member_ids"]}
            plist = self.call("GET", "/projects", ms).json()
            assert {p["id"] for p in plist} == visible
            assert all(not ({"rate", "notes", "budget_hours"} & set(p)) for p in plist)
            tlist = self.call("GET", "/tasks", ms).json()
            assert {t["id"] for t in tlist} == {i for i, t in self.m["tasks"].items() if t.get("project_id") in visible}
            assert all("rate" not in t for t in tlist)
            assert all(set(u) <= {"id", "name", "role"} for u in self.call("GET", "/users", ms).json())

    def dashboard_matches_model(self):
        if not self.dirty & {"entries", "absences", "users"}:
            return
        boss = self._boss()
        entries, absences = list(self.m["entries"].values()), list(self.m["absences"].values())
        for uid, u in list(self.m["users"].items())[:4]:
            days = {date.today()}  # the server computes "this week" on its own clock: accept either side of the call
            r = self.call("GET", f"/dashboard?user_id={uid}", boss)
            days.add(date.today())
            assert r.status_code == 200, r.text
            d = r.json()
            candidates = [oracle.week_target_and_worked(u, entries, absences, day) for day in sorted(days)]
            matches = [(t, w) for t, w in candidates if approx(d["target"], t) and approx(d["worked"], w)]
            assert matches, (u["username"], d, candidates)
            target, worked = matches[0]
            assert approx(d["remaining"], max(target - worked, 0)), (d, target, worked)
        assert self.call("GET", f"/dashboard?user_id={DEAD_ID}", boss).status_code == 404

    def finance_matches_model(self):
        if not self.dirty & {"invoices", "expenses", "settings"}:
            return
        boss = self._boss()
        klein, accrual = self.settings["kleinunternehmer"], self.settings["accounting"] == "accrual"
        for year in (YEAR, YEAR - 1):
            rev = vat_out = recv = exp_cost = vat_in = 0.0
            for inv in self.m["invoices"].values():
                if inv["status"] in ("draft", "cancelled"):
                    continue
                t = oracle.doc_totals(inv, klein)
                if inv["status"] != "paid":
                    recv += t["gross"]
                    if not accrual:
                        continue
                when = inv["date"] if accrual else inv.get("paid_date")
                if when and when.startswith(f"{year}-"):
                    rev += t["net"]
                    vat_out += t["vat"]
            for x in self.m["expenses"].values():
                if x["date"].startswith(f"{year}-"):
                    pct = x["deductible"] / 100
                    exp_cost += (x["net"] + x["vat"]) * pct if klein else x["net"] * pct
                    vat_in += 0 if klein else x["vat"] * pct
            f = self.call("GET", f"/finance?year={year}", boss).json()
            tot = f["totals"]
            tol = 0.02 + 0.011 * (len(self.m["invoices"]) + len(self.m["expenses"]))
            for key, want in dict(revenue=rev, vat_out=vat_out, expenses=exp_cost, vat_in=vat_in, profit=rev - exp_cost,
                                  vat_due=0 if klein else vat_out - vat_in).items():  # fmt: skip
                assert abs(tot[key] - want) <= tol, (year, key, tot[key], want, klein, accrual)
            assert abs(f["open_receivables"] - recv) <= tol, (year, f["open_receivables"], recv)
            assert f["year"] == year and f["kleinunternehmer"] == klein

    @invariant()
    def everything_matches_the_model(self):
        """One invariant (hypothesis runs separate ones in alphabetical order, which would clear `dirty` too early)."""
        self.sessions_match_model()
        self.tokens_match_model()
        self.lists_match_model()
        self.allocations_match_model()
        self.dashboard_matches_model()
        self.finance_matches_model()
        self.dirty = set()


# The run is split into SHARDS independent state machines with 1/SHARDS of the examples each, so that pytest-xdist can
# run them on different workers. Each shard is its own class and therefore draws its own (deterministic) examples.
SHARDS = 3


def _shard(n: int):
    machine = type(f"ApiMachineShard{n}", (ApiMachine,), {})
    base = settings.default
    machine.TestCase.settings = settings(base, max_examples=-(-base.max_examples // SHARDS))
    return type(f"TestApiStatefulShard{n}", (machine.TestCase,), {})


TestApiStatefulShard1, TestApiStatefulShard2, TestApiStatefulShard3 = (_shard(i) for i in range(1, SHARDS + 1))


pytestmark = pytest.mark.randomized
