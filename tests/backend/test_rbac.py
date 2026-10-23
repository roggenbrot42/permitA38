"""Roles and rights: boss / employee / accountant, enforced on the server for every route."""

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from permita38.api.deps import declared
from permita38.api.server import all_routers
from permita38.core import rights
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository

from .conftest import PASSWORD, _login

DUMMY_ID = "abc123def456"

# routes that need a signed-in user but no area: the user's own account, public status, static info
OWN_ACCOUNT_OR_PUBLIC = {
    ("GET", "/api/health"),
    ("GET", "/api/auth/status"),
    ("POST", "/api/auth/setup"),
    ("POST", "/api/auth/login"),
    ("POST", "/api/auth/logout"),
    ("POST", "/api/auth/change-password"),
    ("GET", "/api/auth/me"),
    ("POST", "/api/auth/login/totp"),
    ("GET", "/api/auth/totp/status"),
    ("POST", "/api/auth/totp/setup"),
    ("POST", "/api/auth/totp/enable"),
    ("POST", "/api/auth/totp/disable"),
    ("POST", "/api/auth/totp/recovery-codes/regenerate"),
    ("GET", "/api/bootstrap"),
    ("GET", "/api/holidays"),
}


def _routes(app=None):
    for router in all_routers():
        for r in router.routes:
            assert isinstance(r, APIRoute)
            for m in sorted(r.methods - {"HEAD", "OPTIONS"}):
                yield m, r, "/api" + r.path


def _url(path: str) -> str:
    return re.sub(r"\{[^}]+\}", DUMMY_ID, path)


@pytest.fixture
def clients(app, boss_client):
    out = {"boss": boss_client}
    for role in ("employee", "accountant"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role.title(), "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200, r.text
        out[role] = _login(app, role[:3] + "x")
        out[role].user_id = r.json()["id"]
    return out


# ------------------------------------------------------------------ the table itself
def test_matrix_is_complete_and_boss_can_do_everything():
    assert set(rights.MATRIX) == set(rights.ROLES)
    for role, table in rights.MATRIX.items():
        assert set(table) == set(rights.AREAS), role
    assert all(sc == (rights.Scope.ALL,) * 2 for sc in rights.MATRIX["boss"].values())
    for role in rights.ROLES:  # you cannot write what you cannot read
        for area, (rd, wr) in rights.MATRIX[role].items():
            assert not (rd == rights.Scope.NONE and wr != rights.Scope.NONE), (role, area)


def test_legacy_and_unknown_roles():
    assert rights.normalize_role("member") == "employee"
    assert rights.scope("member", "time", "write") == rights.Scope.LIMITED
    for bogus in ("root", "", None, 7, "BOSS"):
        assert not any(rights.allowed(bogus, a, op) for a in rights.AREAS for op in ("read", "write")), bogus


def test_docs_list_every_area_and_role():
    text = (Path(__file__).parents[2] / "docs" / "roles.md").read_text()
    for area in rights.AREAS:
        assert f"`{area}`" in text, area
    for role in rights.ROLES:
        assert role in text
    # the table in the docs is generated from MATRIX: every cell must be present and correct
    for area in rights.AREAS:
        row = next(line for line in text.splitlines() if line.startswith(f"| `{area}`"))
        cells = [c.strip() for c in row.strip("|").split("|")][2:]
        expected = [
            f"{rights.MATRIX[r][area][0].value} / {rights.MATRIX[r][area][1].value}"
            for r in ("boss", "employee", "accountant")
        ]
        assert cells == expected, area


# ------------------------------------------------------------------ every route is guarded
def test_every_route_declares_its_area_or_is_explicitly_account_level(app):
    unguarded = {(m, path) for m, r, path in _routes(app) if not declared(r)}
    assert unguarded == OWN_ACCOUNT_OR_PUBLIC


def test_declared_areas_exist(app):
    for _, r, _path in _routes(app):
        for area, op, _full in declared(r):
            assert area in rights.AREAS and op in ("read", "write")


def test_every_route_is_denied_exactly_where_the_matrix_says(app, clients):
    """All routes x all roles: 403 if and only if the role lacks the declared access; otherwise the request gets
    past the permission check (whatever the handler then says about the dummy ids)."""
    checked = 0
    for method, route, path in _routes(app):
        reqs = declared(route)
        if not reqs:
            continue
        for role, c in clients.items():
            should_pass = all(
                (s := rights.scope(role, a, op)) != rights.Scope.NONE and (not full or s == rights.Scope.ALL)
                for a, op, full in reqs
            )
            r = c.request(method, _url(path), json={} if method in ("POST", "PUT", "PATCH") else None)
            if method == "POST" and "attachments" in path:
                r = c.post(_url(path), files={"file": ("a.txt", b"x")})
            assert (r.status_code != 403) == should_pass, (method, path, role, r.status_code)
            checked += 1
    assert checked > 150


def test_anonymous_gets_401_not_403_everywhere(app, client):
    for method, route, path in _routes(app):
        if declared(route):
            r = client.request(method, _url(path), json={} if method in ("POST", "PUT") else None)
            assert r.status_code == 401, (method, path)


# ------------------------------------------------------------------ roles in practice
def test_accountant_reads_money_and_books_expenses_but_changes_nothing_else(boss_client, clients):
    acc = clients["accountant"]
    cust = boss_client.post("/api/customers", json={"name": "Demo GmbH"}).json()
    proj = boss_client.post("/api/projects", json={"name": "Site", "customer_id": cust["id"], "rate": 90}).json()
    boss_client.post("/api/entries", json={"date": "2026-01-05", "minutes": 60, "project_id": proj["id"]})
    inv = boss_client.post(
        "/api/invoices", json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 1, "price": 100}]}
    )
    assert inv.status_code == 200, inv.text
    for path in ("/api/customers", "/api/invoices", "/api/offers", "/api/finance", "/api/project-report",
                 "/api/settings", "/api/templates", "/api/entries", "/api/contracts", "/api/expenses"):  # fmt: skip
        assert acc.get(path).status_code == 200, path
    # full project records for the accountant (rates), not the restricted employee view
    assert acc.get(f"/api/projects/{proj['id']}").json()["rate"] == 90
    # expenses and bills: yes
    assert acc.post("/api/expenses", json={"date": "2026-01-06", "description": "Paper", "net": 10}).status_code == 200
    assert acc.post("/api/attachments", files={"file": ("bill.txt", b"x")}).status_code == 200
    # everything else: no
    assert acc.post("/api/customers", json={"name": "X"}).status_code == 403
    assert acc.put(f"/api/invoices/{inv.json()['id']}", json={"customer_id": cust["id"]}).status_code == 403
    assert acc.post(f"/api/invoices/{inv.json()['id']}/finalize").status_code == 403
    assert acc.delete(f"/api/invoices/{inv.json()['id']}").status_code == 403
    assert acc.post("/api/entries", json={"date": "2026-01-05", "minutes": 5}).status_code == 403
    assert acc.post("/api/timer/start", json={}).status_code == 403
    assert acc.get("/api/dashboard").status_code == 403
    assert acc.get("/api/absences").status_code == 403
    assert acc.get("/api/forecast").status_code == 403
    assert acc.put("/api/settings", json={"company_name": "Hacked"}).status_code == 403
    assert (
        acc.post("/api/users", json={"username": "ev", "name": "E", "role": "boss", "password": PASSWORD}).status_code
        == 403
    )
    # pdf download is a read
    assert acc.get(f"/api/invoices/{inv.json()['id']}/pdf").status_code != 403


def test_accountant_sees_names_only_in_the_team_list_and_no_server_path(clients):
    acc = clients["accountant"]
    users = acc.get("/api/users").json()
    assert users and all(set(u) == {"id", "name", "role"} for u in users)
    b = acc.get("/api/bootstrap").json()
    assert "data_dir" not in b and "vat_rate" in b["settings"]  # full settings, no server folder
    emp = clients["employee"].get("/api/bootstrap").json()
    assert set(emp["settings"]) == {"company_name", "state"}


def test_employee_is_unchanged(boss_client, clients):
    emp = clients["employee"]
    for path in ("/api/invoices", "/api/finance", "/api/customers", "/api/settings", "/api/forecast", "/api/templates"):
        assert emp.get(path).status_code == 403, path
    assert emp.get("/api/projects").status_code == 200
    assert emp.get("/api/dashboard").status_code == 200
    assert emp.post("/api/timer/start", json={}).status_code == 200
    assert (
        emp.post("/api/absences", json={"kind": "vacation", "start": "2026-02-02", "end": "2026-02-03"}).status_code
        == 200
    )
    assert emp.post("/api/projects", json={"name": "P"}).status_code == 403
    assert emp.post("/api/attachments", files={"file": ("a.txt", b"x")}).status_code == 403
    assert emp.get(f"/api/dashboard?user_id={clients['boss'].get('/api/auth/me').json()['id']}").status_code == 403


def test_me_and_login_carry_the_permissions(clients, app):
    for role, c in clients.items():
        me = c.get("/api/auth/me").json()
        assert me["role"] == role
        assert me["permissions"] == rights.permissions(role)
    r = TestClient(app).post("/api/auth/login", json={"username": "accx", "password": PASSWORD}).json()
    assert r["user"]["permissions"]["expenses"] == {"read": "all", "write": "all"}


def test_only_the_boss_manages_users_and_roles_are_validated(boss_client, clients):
    ok = boss_client.post(
        "/api/users", json={"username": "zed", "name": "Zed", "role": "accountant", "password": PASSWORD}
    )
    assert ok.status_code == 200 and ok.json()["role"] == "accountant"
    bad = boss_client.post("/api/users", json={"username": "zee", "name": "Z", "role": "root", "password": PASSWORD})
    assert bad.status_code == 422
    for role in ("employee", "accountant"):
        assert (
            clients[role]
            .put(f"/api/users/{clients[role].user_id}", json={"username": "x" + role[:3], "name": "N", "role": "boss"})
            .status_code
            == 403
        )  # no self-promotion


def test_legacy_member_role_is_accepted_and_means_employee(boss_client):
    r = boss_client.post("/api/users", json={"username": "old", "name": "Old", "role": "member", "password": PASSWORD})
    assert r.status_code == 200 and r.json()["role"] == "employee"


def test_a_user_stored_as_member_still_works(app, boss_client):
    services = app.state.services
    """Another device on an older version may still write `role: member` into the synced database."""
    services.store.save(
        "users",
        {"username": "legacy", "name": "Legacy", "role": "member", "active": True, "workdays": [0, 1, 2, 3, 4],
         "password_hash": __import__("permita38.core.service.auth", fromlist=["x"]).hash_password(PASSWORD),
         "pw_changed": 1},
    )  # fmt: skip
    c = _login(app, "legacy")
    assert c.get("/api/auth/me").json()["role"] == "employee"
    assert c.get("/api/entries").status_code == 200
    assert c.get("/api/invoices").status_code == 403
    listed = boss_client.get("/api/users").json()
    assert next(u for u in listed if u["username"] == "legacy")["role"] == "employee"


def test_an_unknown_stored_role_has_no_rights(app, boss_client):
    services = app.state.services
    from permita38.core.service.auth import hash_password

    services.store.save(
        "users",
        {"username": "odd", "name": "Odd", "role": "wizard", "active": True, "workdays": [0],
         "password_hash": hash_password(PASSWORD), "pw_changed": 1},
    )  # fmt: skip
    c = _login(app, "odd")
    for path in ("/api/projects", "/api/entries", "/api/timer", "/api/users", "/api/customers"):
        assert c.get(path).status_code == 403, path


def test_demoting_to_accountant_and_last_boss_rule(boss_client, clients):
    me = boss_client.get("/api/auth/me").json()
    body = {"username": "boss", "name": "Test Boss", "role": "accountant"}
    assert boss_client.put(f"/api/users/{me['id']}", json=body).status_code == 400  # last boss stays boss


def test_role_change_takes_effect_on_the_next_request(boss_client, clients):
    emp = clients["employee"]
    assert emp.get("/api/finance").status_code == 403
    uid = emp.user_id
    boss_client.put(f"/api/users/{uid}", json={"username": "empx", "name": "Employee", "role": "accountant"})
    assert emp.get("/api/finance").status_code == 200
    assert emp.get("/api/timer").status_code == 403


# ------------------------------------------------------------------ migration 0004
def test_migration_renames_member_to_employee_and_keeps_boss(tmp_path):
    data = tmp_path / "data"
    repo = SQLiteRepository(data)
    repo.startup()
    repo.save("users", {"id": "user00000001", "username": "b", "name": "B", "role": "boss"})
    repo.save("users", {"id": "user00000002", "username": "m", "name": "M", "role": "member", "future": 1})
    repo.save("users", {"id": "user00000003", "username": "a", "name": "A", "role": "accountant"})
    repo.close()
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.execute("PRAGMA user_version = 3")  # as if this database had not been migrated yet
    raw.commit()
    raw.close()
    new = SQLiteRepository(data)
    new.startup()
    roles = {u["username"]: u["role"] for u in new.list("users")}
    assert roles == {"b": "boss", "m": "employee", "a": "accountant"}
    assert new.get("users", "user00000002")["future"] == 1  # unknown fields survive
    new.close()
