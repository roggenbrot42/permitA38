"""API tokens (docs/contracts/api-tokens.md): storage, lifecycle, rights, session-only routes, throttle, audit log."""

import hashlib
import logging
import re
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from permita38.api.deps import declared, is_session_only
from permita38.core import rights
from permita38.core.service.audit import RETENTION_DAYS, AuditService
from permita38.core.service.tokens import TOKEN_RE

from .conftest import PASSWORD, _login
from .test_rbac import SESSION_ONLY, VALID_QUERY, _routes, _url

SESSION_ONLY_BODY = {"detail": "This route cannot be used with an API token"}
NOT_AUTH = {"detail": "Not authenticated"}
LOCKED = {"detail": "Too many failed attempts. Please wait a few minutes and try again."}
FAKE_SECRET = "pat_" + "A" * 43  # well-formed, unknown


# ------------------------------------------------------------------ helpers
def bearer(app, token: str, ip: str | None = None) -> TestClient:
    c = TestClient(app, client=(ip, 50000)) if ip else TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def new_token(session: TestClient, **body) -> dict:
    r = session.post("/api/tokens", json={"label": "Backup script", **body})
    assert r.status_code == 201, r.text
    return r.json()


def add_user(boss: TestClient, name: str, role: str) -> dict:
    r = boss.post("/api/users", json={"username": name, "name": name.title(), "role": role, "password": PASSWORD})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture
def store(app):
    return app.state.services.store


@pytest.fixture
def sessions(app, boss_client):
    """Signed-in sessions of one user per role."""
    out = {"boss": boss_client}
    for role in ("employee", "accountant"):
        user = add_user(boss_client, role[:3] + "x", role)
        out[role] = _login(app, role[:3] + "x")
        out[role].user_id = user["id"]
    return out


@pytest.fixture
def clock(monkeypatch):
    """Frozen clock that can be moved: ``clock.now`` (float seconds)."""

    class Clock:
        now = float(int(time.time())) + 0.5

    monkeypatch.setattr(time, "time", lambda: Clock.now)
    return Clock


def all_file_bytes(root: Path) -> bytes:
    return b"".join(p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file())


# ------------------------------------------------------------------ create and storage
def test_create_returns_the_secret_once_in_the_documented_shape(boss_client):
    r = boss_client.post("/api/tokens", json={"label": "  Backup script  "})
    assert r.status_code == 201
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    assert set(body) == {"token", "item"}
    token, item = body["token"], body["item"]
    assert TOKEN_RE.fullmatch(token) and len(token) == 47
    assert set(item) == {
        "id", "user_id", "label", "hint", "read_only", "created", "expires_at", "last_used_at", "revoked_at", "status",
    }  # fmt: skip
    assert item["label"] == "Backup script"  # trimmed
    assert item["hint"] == token[:8] and item["hint"].startswith("pat_")
    assert item["read_only"] is True  # default
    assert abs(item["expires_at"] - item["created"] - 90 * 86400) <= 2  # default 90 days
    assert item["last_used_at"] is None and item["revoked_at"] is None and item["status"] == "active"
    assert re.fullmatch(r"[0-9a-f]{8}", item["id"])


def test_the_secret_is_shown_once_and_only_its_hash_is_stored(app, boss_client, store, config):
    made = new_token(boss_client)
    secret, item = made["token"], made["item"]
    listing = boss_client.get("/api/tokens")
    assert listing.status_code == 200
    assert secret not in listing.text and "token_hash" not in listing.text
    assert all("token" not in row for row in listing.json())
    assert secret not in boss_client.post(f"/api/tokens/{item['id']}/revoke").text
    row = store.get("api_tokens", item["id"])
    assert row["token_hash"] == hashlib.sha256(secret.encode()).hexdigest()
    assert secret not in "".join(str(v) for v in row.values())
    assert secret.encode() not in all_file_bytes(config.data_dir)  # nowhere in the database files


def test_hashes_are_unique_and_secrets_differ(boss_client, store):
    secrets_ = {new_token(boss_client)["token"] for _ in range(5)}
    assert len(secrets_) == 5
    hashes = [r["token_hash"] for r in store.list("api_tokens")]
    assert len(set(hashes)) == 5


@pytest.mark.parametrize(
    "body, field",
    [
        ({}, "label"),
        ({"label": ""}, "label"),
        ({"label": "   "}, "label"),
        ({"label": "x" * 81}, "label"),
        ({"label": "tab\there"}, "label"),
        ({"label": "nul\x00"}, "body"),
        ({"label": "ok", "expires_in_days": 0}, "expires_in_days"),
        ({"label": "ok", "expires_in_days": 366}, "expires_in_days"),
        ({"label": "ok", "expires_in_days": "30"}, "expires_in_days"),
        ({"label": "ok", "expires_in_days": 1.5}, "expires_in_days"),
        ({"label": "ok", "expires_in_days": True}, "expires_in_days"),
        ({"label": "ok", "read_only": "yes"}, "read_only"),
        ({"label": "ok", "read_only": 1}, "read_only"),
        ({"label": "ok", "surprise": 1}, "surprise"),
    ],
)
def test_create_validation_is_a_422_list(boss_client, store, body, field):
    r = boss_client.post("/api/tokens", json=body)
    assert r.status_code == 422, r.text
    detail = r.json()["detail"]
    assert isinstance(detail, list)
    assert detail[0]["loc"] == ["body"] if field == "body" else detail[0]["loc"][:2] == ["body", field]
    assert store.list("api_tokens") == []


def test_create_bounds_are_accepted_at_the_edge(boss_client):
    assert new_token(boss_client, label="x" * 80, expires_in_days=1, read_only=False)["item"]["read_only"] is False
    item = new_token(boss_client, expires_in_days=365)["item"]
    assert abs(item["expires_at"] - item["created"] - 365 * 86400) <= 2


@pytest.mark.parametrize("role", ["boss", "employee", "accountant"])
def test_every_role_creates_tokens_for_itself_only(sessions, role):
    made = new_token(sessions[role])
    assert made["item"]["user_id"] == (sessions[role].user_id if role != "boss" else made["item"]["user_id"])
    assert sessions[role].get("/api/tokens").json()[0]["id"] == made["item"]["id"]
    assert "user_id" not in sessions[role].post("/api/tokens", json={"label": "a", "user_id": "x"}).json()  # 422


def test_unauthenticated_token_routes_answer_401(client):
    assert client.get("/api/tokens").status_code == 401
    assert client.post("/api/tokens", json={"label": "a"}).status_code == 401
    assert client.post("/api/tokens/revoke-all").status_code == 401
    assert client.post("/api/tokens/abcdef12/revoke").status_code == 401
    assert client.get("/api/tokens/abcdef12/audit").status_code == 401


def test_twenty_active_tokens_are_allowed_the_21st_is_a_409(boss_client, clock):
    items = [new_token(boss_client, label=f"t{i}")["item"] for i in range(20)]
    r = boss_client.post("/api/tokens", json={"label": "one too many"})
    assert r.status_code == 409 and r.json() == {"detail": "Too many active tokens (maximum 20)"}
    boss_client.post(f"/api/tokens/{items[0]['id']}/revoke")  # revoked tokens do not count
    assert boss_client.post("/api/tokens", json={"label": "fits again"}).status_code == 201
    assert boss_client.post("/api/tokens", json={"label": "full again"}).status_code == 409
    clock.now += 100 * 86400  # all expired; expired tokens do not count either (session left valid by re-login)


def test_the_limit_is_per_user_and_expired_tokens_do_not_count(app, sessions, store, clock):
    for i in range(20):
        new_token(sessions["employee"], label=f"t{i}", expires_in_days=1)
    assert sessions["employee"].post("/api/tokens", json={"label": "x"}).status_code == 409
    assert sessions["accountant"].post("/api/tokens", json={"label": "x"}).status_code == 201  # other user
    for row in store.list("api_tokens"):
        store.save("api_tokens", {**row, "expires_at": int(clock.now) - 1})  # all of them expired just now
    assert sessions["employee"].post("/api/tokens", json={"label": "x"}).status_code == 201


def test_the_validation_error_beats_the_conflict(boss_client):
    for i in range(20):
        new_token(boss_client, label=f"t{i}")
    assert boss_client.post("/api/tokens", json={"label": ""}).status_code == 422  # order: 401, 403, 422, 409


# ------------------------------------------------------------------ using a token
def test_a_read_only_token_reads_but_never_writes(app, boss_client):
    secret = new_token(boss_client)["token"]  # read_only by default
    t = bearer(app, secret)
    assert t.get("/api/customers").status_code == 200
    r = t.post("/api/customers", json={"name": "Demo"})
    assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}
    assert t.put("/api/customers/abc123def456", json={"name": "x"}).status_code == 403
    assert t.delete("/api/customers/abc123def456").status_code == 403
    assert boss_client.get("/api/customers").json() == []


def test_a_read_write_token_has_the_owners_rights(app, boss_client):
    t = bearer(app, new_token(boss_client, read_only=False)["token"])
    r = t.post("/api/customers", json={"name": "Demo GmbH"})
    assert r.status_code == 200, r.text
    assert [c["name"] for c in boss_client.get("/api/customers").json()] == ["Demo GmbH"]


def test_the_read_only_guard_covers_every_unsafe_method_even_for_posts_that_only_compute(app, boss_client):
    t = bearer(app, new_token(boss_client)["token"])
    for method, path in (("POST", "/api/plan/simulate"), ("POST", "/api/timer/start"), ("POST", "/api/anything")):
        assert t.request(method, path, json={}).status_code in (403, 404), path
    assert t.post("/api/plan/simulate", json={}).json() == {"detail": "Forbidden"}


def test_me_with_a_token_has_auth_and_no_private_key(app, boss_client):
    made = new_token(boss_client, label="Reader", expires_in_days=30)
    me = bearer(app, made["token"]).get("/api/auth/me")
    assert me.status_code == 200
    body = me.json()
    assert "_auth" not in me.text and "password_hash" not in body
    assert body["auth"] == {
        "kind": "token",
        "token_id": made["item"]["id"],
        "label": "Reader",
        "read_only": True,
        "expires_at": made["item"]["expires_at"],
    }
    assert all(p["write"] == "none" for p in body["permissions"].values())  # read-only: no write entry
    assert body["permissions"]["customers"]["read"] == "all"
    assert boss_client.get("/api/auth/me").json()["auth"] == {"kind": "session"}
    assert "_auth" not in bearer(app, made["token"]).get("/api/bootstrap").text


def test_a_read_write_token_shows_the_owners_full_table(app, boss_client):
    me = bearer(app, new_token(boss_client, read_only=False)["token"]).get("/api/auth/me").json()
    assert me["permissions"] == boss_client.get("/api/auth/me").json()["permissions"]


def test_last_used_is_written_at_most_once_a_minute(app, boss_client, clock):
    made = new_token(boss_client)
    t = bearer(app, made["token"])
    assert boss_client.get("/api/tokens").json()[0]["last_used_at"] is None
    t.get("/api/customers")
    first = boss_client.get("/api/tokens").json()[0]["last_used_at"]
    assert first == int(clock.now)
    clock.now += 30
    t.get("/api/customers")
    assert boss_client.get("/api/tokens").json()[0]["last_used_at"] == first
    clock.now += 31
    t.get("/api/customers")
    assert boss_client.get("/api/tokens").json()[0]["last_used_at"] == int(clock.now)


# ------------------------------------------------------------------ a token never exceeds its owner
@pytest.mark.parametrize("role", ["boss", "employee", "accountant"])
def test_a_token_gets_what_its_owners_session_gets_on_every_route(app, sessions, role):
    session = sessions[role]
    rw = bearer(app, new_token(session, read_only=False)["token"])
    ro = bearer(app, new_token(session)["token"])
    checked = 0
    for method, route, path in _routes(app):
        if not declared(route) or is_session_only(route):
            continue
        url = _url(path) + VALID_QUERY.get(path, "")
        kw = {"json": {}} if method in ("POST", "PUT", "PATCH") else {}
        expected = session.request(method, url, **kw)
        got_rw = rw.request(method, url, **kw)
        got_ro = ro.request(method, url, **kw)
        assert got_rw.status_code != 401, (method, path)
        assert (got_rw.status_code == 403) == (expected.status_code == 403), (role, method, path)
        needs_write = any(op == "write" for _, op, _ in declared(route))  # e.g. the boss-only numbering views
        if method in ("GET", "HEAD") and needs_write:
            assert got_rw.status_code == expected.status_code, (role, method, path)
            assert got_ro.status_code == 403, (role, method, path)  # read-only never holds a write right
        elif method in ("GET", "HEAD"):
            assert got_rw.status_code == expected.status_code, (role, method, path)
            assert got_ro.status_code == expected.status_code, (role, method, path)
        else:
            assert got_ro.status_code == 403 and got_ro.json() == {"detail": "Forbidden"}, (role, method, path)
        checked += 1
    assert checked > 100


@pytest.mark.parametrize("role", ["boss", "employee", "accountant"])
@pytest.mark.parametrize("read_only", [True, False])
def test_scope_for_is_the_owners_scope_clamped_to_read_for_read_only_tokens(role, read_only):
    principal = {"role": role, "_auth": {"kind": "token", "token_id": "x", "read_only": read_only}}
    for area in rights.AREAS:
        for op in ("read", "write"):
            owner = rights.scope(role, area, op)
            want = rights.Scope.NONE if (read_only and op == "write") else owner
            assert rights.scope_for(principal, area, op) == want, (role, area, op, read_only)
            assert rights.scope_for({"role": role}, area, op) == owner  # a session is unchanged
    assert rights.allowed_for(principal, "customers", "read") == (role != "employee")
    assert rights.allowed_for(principal, "customers", "write") == (role == "boss" and not read_only)
    assert rights.scope_for({"role": "root", "_auth": {"kind": "token", "read_only": False}}, "time") == "none"


def test_rights_are_literal_for_the_three_roles():
    token = {"kind": "token", "token_id": "x", "read_only": False}
    ro = {**token, "read_only": True}
    assert rights.scope_for({"role": "employee", "_auth": token}, "time", "write") == rights.Scope.LIMITED
    assert rights.scope_for({"role": "employee", "_auth": ro}, "time", "write") == rights.Scope.NONE
    assert rights.scope_for({"role": "employee", "_auth": ro}, "time", "read") == rights.Scope.LIMITED
    assert rights.scope_for({"role": "accountant", "_auth": token}, "expenses", "write") == rights.Scope.ALL
    assert rights.scope_for({"role": "accountant", "_auth": ro}, "expenses", "write") == rights.Scope.NONE
    assert rights.scope_for({"role": "accountant", "_auth": token}, "invoices", "write") == rights.Scope.NONE
    assert rights.scope_for({"role": "boss", "_auth": token}, "settings", "write") == rights.Scope.ALL


def test_a_limited_scope_returns_only_the_owners_own_records_through_a_token(app, sessions):
    boss, emp = sessions["boss"], sessions["employee"]
    assert boss.post("/api/entries", json={"date": "2026-01-05", "minutes": 60}).status_code == 200
    assert emp.post("/api/entries", json={"date": "2026-01-06", "minutes": 30}).status_code == 200
    via_token = bearer(app, new_token(emp)["token"]).get("/api/entries").json()
    assert [e["minutes"] for e in via_token] == [30]
    assert all(e["user_id"] == emp.user_id for e in via_token)
    assert len(bearer(app, new_token(boss)["token"]).get("/api/entries").json()) == 2


def test_a_demotion_clamps_the_very_next_request_and_a_promotion_widens_it(app, sessions):
    boss = sessions["boss"]
    other = add_user(boss, "bob", "boss")
    t = bearer(app, new_token(_login(app, "bob"), read_only=False)["token"])
    assert t.get("/api/customers").status_code == 200
    body = {"username": "bob", "name": "Bob", "role": "employee"}
    assert boss.put(f"/api/users/{other['id']}", json=body).status_code == 200
    assert t.get("/api/customers").status_code == 403  # the employee has no customers
    assert t.get("/api/entries").status_code == 200
    assert boss.put(f"/api/users/{other['id']}", json={**body, "role": "boss"}).status_code == 200
    assert t.get("/api/customers").status_code == 200


def test_deactivating_the_owner_revokes_the_tokens_and_reactivating_does_not_revive_them(app, sessions):
    boss = sessions["boss"]
    emp = sessions["employee"]
    made = new_token(emp)
    t = bearer(app, made["token"])
    assert t.get("/api/entries").status_code == 200
    body = {"username": "empx", "name": "Employee", "role": "employee", "active": False}
    assert boss.put(f"/api/users/{emp.user_id}", json=body).status_code == 200
    assert t.get("/api/entries").status_code == 401
    assert boss.get("/api/tokens?all=true").json()[0]["status"] == "revoked"
    assert boss.put(f"/api/users/{emp.user_id}", json={**body, "active": True}).status_code == 200
    assert t.get("/api/entries").status_code == 401  # a revoked token stays revoked
    assert _login(app, "empx").get("/api/entries").status_code == 200
    assert (
        bearer(app, new_token(_login(app, "empx"))["token"]).get("/api/entries").status_code == 200
    )  # a new one works


def test_an_inactive_owner_found_in_the_database_is_a_401(app, sessions, store):
    emp = sessions["employee"]
    t = bearer(app, new_token(emp)["token"])
    store.save("users", {**store.get("users", emp.user_id), "active": False})  # e.g. synced from another device
    assert t.get("/api/entries").status_code == 401


def test_deleting_the_owner_deletes_tokens_and_audit_rows(app, sessions, store):
    boss, emp = sessions["boss"], sessions["employee"]
    mine = new_token(boss)
    theirs = new_token(emp)
    bearer(app, theirs["token"]).get("/api/entries")
    bearer(app, mine["token"]).get("/api/customers")
    assert len(store.list("audit_log")) == 2
    assert boss.delete(f"/api/users/{emp.user_id}").status_code == 204
    assert [r["id"] for r in store.list("api_tokens")] == [mine["item"]["id"]]
    assert [r["token_id"] for r in store.list("audit_log")] == [mine["item"]["id"]]
    assert bearer(app, theirs["token"]).get("/api/entries").status_code == 401


# ------------------------------------------------------------------ session-only routes
def _fill(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "abcdef12", path)


@pytest.mark.parametrize("read_only", [True, False])
def test_session_only_routes_refuse_every_token_with_the_exact_body(app, boss_client, read_only):
    t = bearer(app, new_token(boss_client, read_only=read_only)["token"])
    assert len(SESSION_ONLY) == 18
    for method, path in sorted(SESSION_ONLY):
        r = t.request(method, _fill(path), json={} if method in ("POST", "PUT") else None)
        assert r.status_code == 403 and r.json() == SESSION_ONLY_BODY, (method, path, r.status_code, r.text)


def test_session_only_refuses_a_token_whatever_the_role(app, sessions):
    for role in ("employee", "accountant"):
        t = bearer(app, new_token(sessions[role], read_only=False)["token"])
        for method, path in sorted(SESSION_ONLY):
            r = t.request(method, _fill(path), json={})
            assert r.status_code == 403 and r.json() == SESSION_ONLY_BODY, (role, method, path)


def test_a_token_cannot_manage_tokens_or_passwords(app, boss_client, store):
    made = new_token(boss_client, read_only=False)
    t = bearer(app, made["token"])
    assert t.post("/api/tokens", json={"label": "child"}).json() == SESSION_ONLY_BODY
    assert t.get("/api/tokens").status_code == 403
    assert t.post(f"/api/tokens/{made['item']['id']}/revoke").status_code == 403
    assert len(store.list("api_tokens")) == 1
    before = store.list("users")
    r = t.post("/api/auth/change-password", json={"old_password": PASSWORD, "new_password": "another password"})
    assert r.status_code == 403 and store.list("users") == before


def test_a_read_write_boss_token_cannot_write_settings_but_reads_them(app, boss_client, store):
    t = bearer(app, new_token(boss_client, read_only=False)["token"])
    before = store.settings()
    r = t.put("/api/settings", json={"company_name": "Hijacked Ltd"})
    assert r.status_code == 403 and r.json() == SESSION_ONLY_BODY
    for method in ("POST", "DELETE", "PATCH"):  # no such routes: nothing to write
        assert t.request(method, "/api/settings", json={}).status_code in (403, 404, 405)
    assert store.settings() == before
    got = t.get("/api/settings")
    assert got.status_code == 200 and got.json()["company_name"] == before["company_name"]
    assert boss_client.put("/api/settings", json={"company_name": "Renamed"}).status_code == 200  # the session can


def test_public_routes_ignore_a_bearer_token_and_do_not_audit(app, boss_client, store):
    t = bearer(app, new_token(boss_client)["token"])
    assert t.get("/api/auth/status").status_code == 200
    assert t.get("/api/health").status_code == 200
    assert t.post("/api/auth/login", json={"username": "boss", "password": "wrong password"}).status_code == 401
    assert store.list("audit_log") == []
    assert store.list("api_tokens")[0].get("last_used_at") is None


# ------------------------------------------------------------------ lifecycle
def test_revoke_is_immediate_and_idempotent(app, boss_client, clock):
    made = new_token(boss_client)
    t = bearer(app, made["token"])
    assert t.get("/api/customers").status_code == 200
    r = boss_client.post(f"/api/tokens/{made['item']['id']}/revoke")
    assert r.status_code == 200
    item = r.json()
    assert item["status"] == "revoked" and item["revoked_at"] == int(clock.now)
    assert t.get("/api/customers").status_code == 401  # the very next request
    clock.now += 10
    again = boss_client.post(f"/api/tokens/{made['item']['id']}/revoke")
    assert again.status_code == 200 and again.json() == item  # unchanged, the first time stays


def test_expiry_boundary(app, boss_client, clock):
    made = new_token(boss_client, expires_in_days=1)
    expires = made["item"]["expires_at"]
    assert expires == int(clock.now) + 86400
    t = bearer(app, made["token"])
    assert boss_client.get("/api/tokens").json()[0]["status"] == "active"
    clock.now = expires - 1 + 0.5
    assert t.get("/api/customers").status_code == 200
    clock.now = expires + 0.0  # expires_at == now is expired
    assert t.get("/api/customers").status_code == 401


def test_status_of_listed_tokens(app, boss_client, store, clock):
    active = new_token(boss_client, label="a")["item"]
    expired = new_token(boss_client, label="e")["item"]
    both = new_token(boss_client, label="r")["item"]
    for tid in (expired["id"], both["id"]):
        store.save("api_tokens", {**store.get("api_tokens", tid), "expires_at": int(clock.now) - 5})
    boss_client.post(f"/api/tokens/{both['id']}/revoke")
    status = {row["label"]: row["status"] for row in boss_client.get("/api/tokens").json()}
    assert status == {"a": "active", "e": "expired", "r": "revoked"}  # revoked wins over expired
    assert active["id"]


def test_listing_is_own_tokens_newest_first_and_all_is_for_the_boss(app, sessions, clock):
    boss, emp = sessions["boss"], sessions["employee"]
    a = new_token(boss, label="first")["item"]
    clock.now += 5
    b = new_token(boss, label="second")["item"]
    c = new_token(emp, label="theirs")["item"]
    assert [x["id"] for x in boss.get("/api/tokens").json()] == [b["id"], a["id"]]
    assert [x["id"] for x in emp.get("/api/tokens").json()] == [c["id"]]
    everything = boss.get("/api/tokens?all=true").json()
    assert {x["id"] for x in everything} == {a["id"], b["id"], c["id"]}
    assert {x["label"]: x["owner_name"] for x in everything}["theirs"] == "Empx"
    assert "owner_name" not in boss.get("/api/tokens").json()[0]
    r = emp.get("/api/tokens?all=true")
    assert r.status_code == 403 and r.json() == {"detail": "Forbidden"}
    assert sessions["accountant"].get("/api/tokens?all=true").status_code == 403


def test_other_users_tokens_are_404_for_revoke_and_audit_but_the_boss_may(app, sessions):
    boss, emp, acc = sessions["boss"], sessions["employee"], sessions["accountant"]
    theirs = new_token(emp)["item"]["id"]
    for call in (
        lambda c, i: c.post(f"/api/tokens/{i}/revoke"),
        lambda c, i: c.get(f"/api/tokens/{i}/audit"),
    ):
        r = call(acc, theirs)
        assert r.status_code == 404 and r.json() == {"detail": "Token not found"}
        assert call(acc, "00000000").json() == r.json()  # an unknown id answers the same: no existence leak
    assert acc.post(f"/api/tokens/{theirs}/revoke").status_code == 404
    assert boss.get(f"/api/tokens/{theirs}/audit").status_code == 200
    assert boss.post(f"/api/tokens/{theirs}/revoke").json()["status"] == "revoked"


@pytest.mark.parametrize("bad", ["abc", "ABCDEF12", "abcdef1", "abcdef123", "zzzzzzzz"])
def test_the_token_id_must_be_eight_hex_characters(boss_client, bad):
    assert boss_client.post(f"/api/tokens/{bad}/revoke").status_code == 422
    assert boss_client.get(f"/api/tokens/{bad}/audit").status_code == 422


def test_revoke_all(app, sessions):
    boss, emp, acc = sessions["boss"], sessions["employee"], sessions["accountant"]
    mine = [new_token(emp, label=f"t{i}") for i in range(3)]
    new_token(boss)
    new_token(acc)
    r = emp.post("/api/tokens/revoke-all")
    assert r.status_code == 200 and r.json() == {"revoked": 3}
    assert emp.post("/api/tokens/revoke-all").json() == {"revoked": 0}
    assert all(bearer(app, m["token"]).get("/api/entries").status_code == 401 for m in mine)
    assert emp.post(f"/api/tokens/revoke-all?user_id={acc.user_id}").status_code == 403
    assert emp.post(f"/api/tokens/revoke-all?user_id={emp.user_id}").json() == {"revoked": 0}  # own id is fine
    assert boss.post(f"/api/tokens/revoke-all?user_id={acc.user_id}").json() == {"revoked": 1}
    assert [x["status"] for x in boss.get("/api/tokens").json()] == ["active"]
    assert boss.post("/api/tokens/revoke-all?user_id=!").status_code == 422


def test_there_is_no_patch_route(boss_client):
    made = new_token(boss_client)["item"]
    r = boss_client.patch(f"/api/tokens/{made['id']}", json={"label": "new"})
    assert r.status_code == 405
    assert boss_client.get("/api/tokens").json()[0]["label"] == "Backup script"
    assert boss_client.put(f"/api/tokens/{made['id']}", json={"label": "new"}).status_code in (404, 405)
    assert boss_client.delete(f"/api/tokens/{made['id']}").status_code in (404, 405)


# ------------------------------------------------------------------ transport
def test_a_pat_in_the_cookie_is_refused_and_a_bearer_wins_over_a_cookie(app, sessions):
    boss, emp = sessions["boss"], sessions["employee"]
    secret = new_token(emp)["token"]
    c = TestClient(app)
    c.cookies.set("authorization", secret)
    assert c.get("/api/entries").status_code == 401
    # token in the Authorization header + the boss's session cookie: the token (the employee) wins
    c2 = TestClient(app)
    login = c2.post("/api/auth/login", json={"username": "boss", "password": PASSWORD})
    assert login.status_code == 200 and c2.cookies.get("authorization")
    me = c2.get("/api/auth/me", headers={"Authorization": f"Bearer {secret}"}).json()
    assert me["username"] == "empx" and me["auth"]["kind"] == "token"
    assert boss.get("/api/auth/me").json()["username"] == "boss"


def test_a_bad_bearer_does_not_fall_back_to_a_valid_cookie(app, boss_client):
    c = TestClient(app)
    assert c.post("/api/auth/login", json={"username": "boss", "password": PASSWORD}).status_code == 200
    assert c.get("/api/auth/me").status_code == 200  # the cookie alone works
    assert c.get("/api/auth/me", headers={"Authorization": f"Bearer {FAKE_SECRET}"}).status_code == 401
    assert c.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"}).status_code == 401


def test_a_token_in_the_query_string_is_ignored(app, boss_client):
    secret = new_token(boss_client)["token"]
    c = TestClient(app)
    for key in ("token", "access_token", "authorization", "api_key"):
        assert c.get(f"/api/customers?{key}={secret}").status_code == 401


def test_a_session_jwt_works_as_a_bearer_and_is_never_tried_as_a_token(app, boss_client):
    jwt = boss_client.headers["Authorization"].split()[1]
    assert not jwt.startswith("pat_")
    c = bearer(app, jwt)
    me = c.get("/api/auth/me").json()
    assert me["auth"] == {"kind": "session"}
    assert c.post("/api/tokens", json={"label": "a"}).status_code == 201  # a real session may create tokens


def test_malformed_pat_values_are_401_without_a_lookup(app, boss_client, monkeypatch):
    calls = []
    real = app.state.services.store.find
    monkeypatch.setattr(app.state.services.store, "find", lambda coll, **kw: (calls.append(coll), real(coll, **kw))[1])
    for i, bad in enumerate(("pat_x", "pat_", "pat_" + "A" * 42, "pat_" + "A" * 44, "pat_" + "A" * 42 + "!")):
        r = bearer(app, bad, ip=f"10.1.0.{i}").get("/api/customers")
        assert r.status_code == 401 and r.json() == NOT_AUTH, bad
    assert "api_tokens" not in calls


def test_all_failures_look_the_same_and_no_response_sets_a_cookie(app, sessions, store):
    boss, emp = sessions["boss"], sessions["employee"]
    revoked = new_token(boss)
    boss.post(f"/api/tokens/{revoked['item']['id']}/revoke")
    expired = new_token(boss)
    store.save("api_tokens", {**store.get("api_tokens", expired["item"]["id"]), "expires_at": int(time.time()) - 1})
    ghost = new_token(emp)
    store.save("users", {**store.get("users", emp.user_id), "active": False})
    answers = []
    for i, secret in enumerate((FAKE_SECRET, revoked["token"], expired["token"], ghost["token"])):
        r = bearer(app, secret, ip=f"10.2.0.{i}").get("/api/customers")
        answers.append((r.status_code, r.json(), r.headers.get("www-authenticate"), r.headers.get("content-length")))
        assert "set-cookie" not in r.headers
    assert len(set(map(str, answers))) == 1, answers
    assert answers[0][:3] == (401, NOT_AUTH, "Bearer")
    ok = bearer(app, new_token(boss)["token"]).get("/api/customers")
    assert ok.status_code == 200 and "set-cookie" not in ok.headers


# ------------------------------------------------------------------ throttle of invalid tokens
def test_invalid_tokens_lock_the_client_ip_out_with_retry_after(app, boss_client):
    good = new_token(boss_client)["token"]
    for _ in range(5):
        assert bearer(app, FAKE_SECRET).get("/api/customers").status_code == 401
    r = bearer(app, good).get("/api/customers")  # a VALID token from the locked-out address
    assert r.status_code == 429 and r.json() == LOCKED
    assert 1 <= int(r.headers["retry-after"]) <= 300
    # another address is not affected
    assert bearer(app, good, ip="198.51.100.7").get("/api/customers").status_code == 200
    # the lock-out also holds for the session-only routes and for malformed values
    assert bearer(app, good).post("/api/tokens", json={"label": "a"}).status_code == 429
    assert bearer(app, "pat_x").get("/api/customers").status_code == 429


def test_the_lockout_check_runs_before_any_lookup(app, boss_client, monkeypatch):
    good = new_token(boss_client)["token"]
    for _ in range(5):
        bearer(app, FAKE_SECRET).get("/api/customers")

    def boom(*_a, **_k):
        raise AssertionError("looked up while locked out")

    monkeypatch.setattr(app.state.services.tokens, "authenticate", boom)
    assert bearer(app, good).get("/api/customers").status_code == 429


def test_malformed_values_count_and_valid_requests_do_not(app, boss_client):
    good = new_token(boss_client)["token"]
    t = bearer(app, good)
    for _ in range(10):
        assert t.get("/api/customers").status_code == 200  # valid requests never count
    for i in range(5):
        assert bearer(app, f"pat_bad{i}").get("/api/customers").status_code == 401
    assert t.get("/api/customers").status_code == 429


def test_retry_after_shrinks_as_the_window_passes(app, boss_client, clock):
    good = new_token(boss_client)["token"]
    for _ in range(5):
        bearer(app, FAKE_SECRET).get("/api/customers")
    first = int(bearer(app, good).get("/api/customers").headers["retry-after"])
    assert first == 300
    clock.now += 100
    assert int(bearer(app, good).get("/api/customers").headers["retry-after"]) == 200
    clock.now += 201
    assert bearer(app, good).get("/api/customers").status_code == 200


def test_the_login_route_sends_retry_after_too(app, client):
    for _ in range(5):
        assert client.post("/api/auth/login", json={"username": "nobody", "password": "x"}).status_code == 401
    r = client.post("/api/auth/login", json={"username": "nobody", "password": "x"})
    assert r.status_code == 429 and r.json() == LOCKED and 1 <= int(r.headers["retry-after"]) <= 300


# ------------------------------------------------------------------ audit log
def audit(session: TestClient, token_id: str, **query) -> dict:
    r = session.get(f"/api/tokens/{token_id}/audit", params=query)
    assert r.status_code == 200, r.text
    return r.json()


def test_exactly_one_audit_row_per_token_request_including_errors(app, boss_client, store):
    made = new_token(boss_client)  # read-only
    rw = new_token(boss_client, read_only=False)
    t, w = bearer(app, made["token"]), bearer(app, rw["token"])
    assert t.get("/api/customers").status_code == 200
    assert t.post("/api/customers", json={"name": "x"}).status_code == 403  # read-only guard
    assert t.get("/api/customers/abc123def456").status_code == 404
    assert t.post("/api/tokens", json={"label": "x"}).status_code == 403  # session-only
    assert t.get("/api/team/timeline").status_code == 422  # missing query
    assert w.post("/api/customers", json={}).status_code == 422
    rows = audit(boss_client, made["item"]["id"])["items"]
    # (several requests in one second have no stable order, so the rows are compared as a set)
    assert sorted((r["method"], r["route"], r["status"]) for r in rows) == sorted([
        ("GET", "/api/team/timeline", 422),
        ("POST", "/api/tokens", 403),
        ("GET", "/api/customers/{rid}", 404),
        ("POST", "/api/customers", 403),
        ("GET", "/api/customers", 200),
    ])  # fmt: skip
    assert len(rows) == 5
    assert all(r["token_id"] == made["item"]["id"] and r["user_id"] == made["item"]["user_id"] for r in rows)
    assert set(rows[0]) == {"id", "at", "token_id", "user_id", "method", "route", "status"}
    rw_rows = audit(boss_client, rw["item"]["id"])["items"]
    assert [(r["method"], r["route"], r["status"]) for r in rw_rows] == [("POST", "/api/customers", 422)]


def test_session_requests_and_invalid_tokens_leave_no_audit_rows(app, boss_client, store):
    boss_client.get("/api/customers")
    boss_client.post("/api/customers", json={"name": "x"})
    new_token(boss_client)
    assert store.list("audit_log") == []
    bearer(app, FAKE_SECRET).get("/api/customers")
    assert store.list("audit_log") == []


def test_the_audit_row_holds_no_ids_or_query_strings(app, boss_client, store):
    made = new_token(boss_client)
    t = bearer(app, made["token"])
    t.get("/api/customers/abc123def456?canary=zq-unique-9f3&x=1")
    (row,) = store.list("audit_log")
    assert row["route"] == "/api/customers/{rid}"
    assert "abc123def456" not in str(row) and "zq-unique" not in str(row)


def test_audit_log_paging(app, boss_client, clock):
    made = new_token(boss_client)
    t = bearer(app, made["token"])
    for _ in range(5):
        clock.now += 10
        t.get("/api/customers")
    first = audit(boss_client, made["item"]["id"], limit=2)
    assert len(first["items"]) == 2 and first["next"] == first["items"][-1]["at"]
    ats = [r["at"] for r in first["items"]]
    assert ats == sorted(ats, reverse=True)
    second = audit(boss_client, made["item"]["id"], limit=2, before=first["next"])
    assert all(r["at"] < first["next"] for r in second["items"]) and len(second["items"]) == 2
    last = audit(boss_client, made["item"]["id"], limit=2, before=second["next"])
    assert len(last["items"]) == 1 and last["next"] is None
    assert audit(boss_client, made["item"]["id"])["next"] is None  # default 50, fewer rows
    for bad in ({"limit": 0}, {"limit": 201}, {"before": -1}, {"before": 4102444801}, {"limit": "x"}):
        assert boss_client.get(f"/api/tokens/{made['item']['id']}/audit", params=bad).status_code == 422


def test_audit_rows_exist_for_revoked_and_expired_tokens_and_only_the_owner_or_boss_reads_them(app, sessions):
    boss, emp, acc = sessions["boss"], sessions["employee"], sessions["accountant"]
    made = new_token(emp)
    bearer(app, made["token"]).get("/api/entries")
    emp.post(f"/api/tokens/{made['item']['id']}/revoke")
    assert len(audit(emp, made["item"]["id"])["items"]) == 1
    assert len(audit(boss, made["item"]["id"])["items"]) == 1
    assert acc.get(f"/api/tokens/{made['item']['id']}/audit").status_code == 404


def test_the_audit_service_is_append_only(store):
    public = {n for n in dir(AuditService) if not n.startswith("_")}
    assert public == {"append", "list", "prune"}  # no update, no delete by id or by token
    svc = AuditService(store)
    svc.append("abcdef12", "u1", "GET", "/api/x", 200)
    row = svc.list("abcdef12")[0]
    assert row["status"] == 200
    assert not any(hasattr(svc, n) for n in ("update", "save", "delete", "remove", "clear"))


def test_prune_removes_rows_older_than_365_days_at_startup(config, store, app):
    now = int(time.time())
    svc = AuditService(store)
    day = 86400
    for age_days in (RETENTION_DAYS + 1, RETENTION_DAYS - 1, 1000, 0):
        store.save(
            "audit_log",
            {"at": now - age_days * day, "token_id": "abcdef12", "user_id": "u1", "method": "GET", "route": "/r",
             "status": 200},
        )  # fmt: skip
    assert svc.prune() == 2
    assert sorted((now - r["at"]) // day for r in store.list("audit_log")) == [0, RETENTION_DAYS - 1]
    # and it really runs when the app starts
    store.save("audit_log", {"at": now - 400 * day, "token_id": "abcdef12", "user_id": "u1", "method": "GET",
                             "route": "/r", "status": 200})  # fmt: skip
    from permita38.api.deps import startup_services

    startup_services(app.state.services)
    assert all(r["at"] > now - RETENTION_DAYS * day for r in store.list("audit_log"))


def test_a_failing_audit_write_is_logged_and_the_response_is_unchanged(app, boss_client, monkeypatch, caplog):
    made = new_token(boss_client)
    t = bearer(app, made["token"])
    expected = t.get("/api/customers")

    def boom(*_a, **_k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(app.state.services.audit, "append", boom)
    with caplog.at_level(logging.ERROR):
        r = t.get("/api/customers")
    assert r.status_code == 200 and r.json() == expected.json()
    assert "could not write the audit log" in caplog.text
    assert made["token"] not in caplog.text


def test_canary_the_secret_and_the_query_string_appear_nowhere(app, boss_client, config, caplog):
    secret = new_token(boss_client, read_only=False)["token"]
    canary = "zq-canary-query-7731"
    with caplog.at_level(logging.DEBUG):
        t = bearer(app, secret)
        t.get(f"/api/customers?search={canary}")
        t.post(f"/api/customers?search={canary}", json={})
        t.get(f"/api/team/timeline?start={canary}")
        bearer(app, "pat_" + "Z" * 43).get(f"/api/customers?search={canary}")
        t.get("/api/tokens")
    blob = all_file_bytes(config.data_dir)
    assert secret.encode() not in blob and canary.encode() not in blob
    assert hashlib.sha256(secret.encode()).hexdigest().encode() in blob  # only the hash is there
    # (the test client's own httpx log line shows the URL it was asked for; the server's logs are what counts)
    server_log = "\n".join(r.getMessage() for r in caplog.records if not r.name.startswith(("httpx", "httpcore")))
    assert secret not in server_log and canary not in server_log


def test_credentials_are_not_part_of_exports(app, boss_client):
    made = new_token(boss_client)
    for path in ("/api/bootstrap", "/api/auth/me", "/api/tokens"):
        assert made["token"] not in boss_client.get(path).text


# ------------------------------------------------------------------ source scan
def test_request_paths_never_ask_the_role_based_check_for_the_current_principal():
    """`rights.scope(role, ...)` is for roles of OTHER users and for the table itself; the signed-in principal is
    always checked with `scope_for` / `allowed_for`, otherwise a read-only token would slip through."""
    root = Path(__file__).parents[2] / "permita38"
    pattern = re.compile(r"rights\.(scope|allowed)\(")
    offenders = []
    for folder in ("api", "core/service"):
        for path in sorted((root / folder).rglob("*.py")):
            for n, line in enumerate(path.read_text().splitlines(), 1):
                if pattern.search(line):
                    offenders.append(f"{path.relative_to(root)}:{n}: {line.strip()}")
    assert offenders == []
