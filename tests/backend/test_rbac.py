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
from permita38.core.database import list_migrations
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service import team_timeline

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
    boss = {a: sc for a, sc in rights.MATRIX["boss"].items() if a != "team_timeline"}
    assert all(sc == (rights.Scope.ALL,) * 2 for sc in boss.values())
    # the timeline is read-only for everybody, the boss included (its level is always "full")
    assert rights.MATRIX["boss"]["team_timeline"] == (rights.Scope.ALL, rights.Scope.NONE)
    assert all(rights.scope(r, "team_timeline", "write") == rights.Scope.NONE for r in rights.ROLES)
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


# Hand-written, independent of the router declarations: (method, path) -> (area, operation, needs full scope).
# Reviewed line by line against docs/roles.md. A new guarded route must be added here.
# Notes: read-only planning calls are POSTs but need only `planning` read; the Impressum lookup is part of
# `customers` (write, it creates data); converting an offer into an invoice creates an invoice (`invoices` write);
# GET /settings needs full scope because employees get their limited view through /bootstrap.
EXPECTED: dict[tuple[str, str], tuple[str, str, bool]] = {
    ("POST", "/api/users/{user_id}/totp/reset"): ("team", "write", False),
    ("GET", "/api/users"): ("team", "read", False),
    ("POST", "/api/users"): ("team", "write", False),
    ("PUT", "/api/users/{user_id}"): ("team", "write", False),
    ("DELETE", "/api/users/{user_id}"): ("team", "write", False),
    # calendars are boss-only (planning write, full scope); each boss manages only their own sources (service check)
    ("GET", "/api/calendars"): ("planning", "write", True),
    ("POST", "/api/calendars"): ("planning", "write", True),
    ("POST", "/api/calendars/upload"): ("planning", "write", True),
    ("PUT", "/api/calendars/{source_id}"): ("planning", "write", True),
    ("DELETE", "/api/calendars/{source_id}"): ("planning", "write", True),
    ("POST", "/api/calendars/{source_id}/refresh"): ("planning", "write", True),
    ("GET", "/api/calendars/busy"): ("planning", "write", True),
    ("GET", "/api/calendars/suggestions"): ("planning", "write", True),
    ("GET", "/api/calendars/{source_id}/preview"): ("planning", "write", True),
    ("GET", "/api/timer"): ("timer", "read", False),
    ("POST", "/api/timer/start"): ("timer", "write", False),
    ("POST", "/api/timer/stop"): ("timer", "write", False),
    ("GET", "/api/dashboard"): ("timer", "read", False),
    ("GET", "/api/forecast"): ("planning", "read", False),
    ("POST", "/api/plan/simulate"): ("planning", "read", False),
    ("POST", "/api/plan/check-offer"): ("planning", "read", False),
    ("POST", "/api/plan/what-must-move"): ("planning", "read", False),
    ("POST", "/api/plan/suggest-vacation"): ("planning", "read", False),
    ("POST", "/api/plan/suggest-start"): ("planning", "read", False),
    ("GET", "/api/plan/vacation"): ("planning", "read", False),
    ("POST", "/api/plan/apply"): ("planning", "write", False),
    ("GET", "/api/plan/scenarios"): ("planning", "read", False),
    ("POST", "/api/plan/scenarios"): ("planning", "write", False),
    ("PUT", "/api/plan/scenarios/{rid}"): ("planning", "write", False),
    ("DELETE", "/api/plan/scenarios/{rid}"): ("planning", "write", False),
    ("POST", "/api/plan/scenarios/{rid}/apply"): ("planning", "write", False),
    ("POST", "/api/projects/{rid}/extend"): ("planning", "write", False),
    ("GET", "/api/projects/{rid}/planning"): ("planning", "read", False),
    ("GET", "/api/finance"): ("finance", "read", False),
    ("GET", "/api/project-report"): ("finance", "read", False),
    ("GET", "/api/settings"): ("settings", "read", True),
    # the team timeline: the matrix only decides access; the setting decides the level (service, 403 at `off`)
    ("GET", "/api/team/timeline"): ("team_timeline", "read", False),
    ("PUT", "/api/settings"): ("settings", "write", False),
    # numbering and import management is boss-only: settings write at full scope
    ("GET", "/api/numbering/preview"): ("settings", "write", True),
    ("GET", "/api/numbering/status"): ("settings", "write", True),
    ("POST", "/api/numbering/check"): ("settings", "write", True),
    ("PUT", "/api/numbering"): ("settings", "write", True),
    ("GET", "/api/numbering/log"): ("settings", "write", True),
    ("POST", "/api/import/preview"): ("settings", "write", True),
    ("POST", "/api/import/run"): ("settings", "write", True),
    ("GET", "/api/import/batches"): ("settings", "write", True),
    ("POST", "/api/import/batches/{rid}/undo"): ("settings", "write", True),
    ("POST", "/api/attachments"): ("attachments", "write", False),
    ("GET", "/api/attachments/{name}"): ("attachments", "read", False),
    ("POST", "/api/invoices/from-time"): ("invoices", "write", False),
    ("POST", "/api/invoices/{rid}/finalize"): ("invoices", "write", False),
    ("GET", "/api/invoices/{rid}/pdf"): ("invoices", "read", False),
    ("POST", "/api/offers/{rid}/finalize"): ("offers", "write", False),
    ("GET", "/api/offers/{rid}/pdf"): ("offers", "read", False),
    ("POST", "/api/offers/{rid}/accept"): ("offers", "write", False),
    ("POST", "/api/offers/{rid}/to-invoice"): ("invoices", "write", False),
    ("POST", "/api/customers/lookup-impressum"): ("customers", "write", False),
    ("GET", "/api/customers"): ("customers", "read", False),
    ("POST", "/api/customers"): ("customers", "write", False),
    ("GET", "/api/customers/{rid}"): ("customers", "read", False),
    ("PUT", "/api/customers/{rid}"): ("customers", "write", False),
    ("DELETE", "/api/customers/{rid}"): ("customers", "write", False),
    ("GET", "/api/projects"): ("projects", "read", False),
    ("POST", "/api/projects"): ("projects", "write", False),
    ("GET", "/api/projects/{rid}"): ("projects", "read", False),
    ("PUT", "/api/projects/{rid}"): ("projects", "write", False),
    ("DELETE", "/api/projects/{rid}"): ("projects", "write", False),
    ("GET", "/api/tasks"): ("projects", "read", False),
    ("POST", "/api/tasks"): ("projects", "write", False),
    ("GET", "/api/tasks/{rid}"): ("projects", "read", False),
    ("PUT", "/api/tasks/{rid}"): ("projects", "write", False),
    ("DELETE", "/api/tasks/{rid}"): ("projects", "write", False),
    ("GET", "/api/entries"): ("time", "read", False),
    ("POST", "/api/entries"): ("time", "write", False),
    ("GET", "/api/entries/{rid}"): ("time", "read", False),
    ("PUT", "/api/entries/{rid}"): ("time", "write", False),
    ("DELETE", "/api/entries/{rid}"): ("time", "write", False),
    ("GET", "/api/contracts"): ("contracts", "read", False),
    ("POST", "/api/contracts"): ("contracts", "write", False),
    ("GET", "/api/contracts/{rid}"): ("contracts", "read", False),
    ("PUT", "/api/contracts/{rid}"): ("contracts", "write", False),
    ("DELETE", "/api/contracts/{rid}"): ("contracts", "write", False),
    ("GET", "/api/absences"): ("absences", "read", False),
    ("POST", "/api/absences"): ("absences", "write", False),
    ("GET", "/api/absences/{rid}"): ("absences", "read", False),
    ("PUT", "/api/absences/{rid}"): ("absences", "write", False),
    ("DELETE", "/api/absences/{rid}"): ("absences", "write", False),
    ("GET", "/api/offers"): ("offers", "read", False),
    ("POST", "/api/offers"): ("offers", "write", False),
    ("GET", "/api/offers/{rid}"): ("offers", "read", False),
    ("PUT", "/api/offers/{rid}"): ("offers", "write", False),
    ("DELETE", "/api/offers/{rid}"): ("offers", "write", False),
    ("GET", "/api/invoices"): ("invoices", "read", False),
    ("POST", "/api/invoices"): ("invoices", "write", False),
    ("GET", "/api/invoices/{rid}"): ("invoices", "read", False),
    ("PUT", "/api/invoices/{rid}"): ("invoices", "write", False),
    ("DELETE", "/api/invoices/{rid}"): ("invoices", "write", False),
    ("GET", "/api/expenses"): ("expenses", "read", False),
    ("POST", "/api/expenses"): ("expenses", "write", False),
    ("GET", "/api/expenses/{rid}"): ("expenses", "read", False),
    ("PUT", "/api/expenses/{rid}"): ("expenses", "write", False),
    ("DELETE", "/api/expenses/{rid}"): ("expenses", "write", False),
    ("GET", "/api/templates"): ("templates", "read", False),
    ("POST", "/api/templates"): ("templates", "write", False),
    ("GET", "/api/templates/{rid}"): ("templates", "read", False),
    ("PUT", "/api/templates/{rid}"): ("templates", "write", False),
    ("DELETE", "/api/templates/{rid}"): ("templates", "write", False),
}


def test_declared_rights_equal_the_hand_written_table(app):
    actual = {}
    for method, route, path in _routes(app):
        reqs = declared(route)
        if reqs:
            assert len(reqs) == 1, (method, path, reqs)
            actual[(method, path)] = reqs[0]
    assert actual == EXPECTED


# routes whose 403 also depends on data (the team timeline is 403 at level `off`): a valid query so that the
# validation of the parameters does not answer first
VALID_QUERY = {"/api/team/timeline": "?start=2026-11-02&end=2026-11-03"}


def test_every_route_is_denied_exactly_where_the_matrix_says(app, clients):
    """All routes x all roles: 403 if and only if the role lacks the declared access; otherwise the request gets
    past the permission check (whatever the handler then says about the dummy ids). The timeline also answers 403
    for a role whose level is `off` (docs/contracts/team-timeline.md)."""
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
            if path == "/api/team/timeline":
                should_pass = (
                    should_pass and team_timeline.level_for(role, app.state.services.store.settings()) != "off"
                )
            r = c.request(
                method, _url(path) + VALID_QUERY.get(path, ""), json={} if method in ("POST", "PUT", "PATCH") else None
            )
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


# ------------------------------------------------------------------ migration roles
def test_migration_renames_member_to_employee_and_keeps_boss(tmp_path):
    data = tmp_path / "data"
    repo = SQLiteRepository(data)
    repo.startup()
    repo.save("users", {"id": "user00000001", "username": "b", "name": "B", "role": "boss"})
    repo.save("users", {"id": "user00000002", "username": "m", "name": "M", "role": "member", "future": 1})
    repo.save("users", {"id": "user00000003", "username": "a", "name": "A", "role": "accountant"})
    repo.close()
    raw = sqlite3.connect(data / DB_FILENAME)
    chain = list_migrations("sqlite")
    roles_mig = next(m for m in chain if m.name == "roles")
    # as if this database had not applied the roles migration yet (and nothing after it)
    later = [m.id for m in chain[chain.index(roles_mig) :]]
    raw.executemany("DELETE FROM schema_migrations WHERE id = ?", [(i,) for i in later])
    raw.execute(f"PRAGMA user_version = {len(chain) - len(later)}")
    # undo the schema of the later migrations too, so that the runner can apply them again
    if "calendars" in [m.name for m in chain[chain.index(roles_mig) + 1 :]]:
        raw.executescript(
            'DROP TABLE "calendar_events"; DROP TABLE "calendar_sources";'
            ' ALTER TABLE "users" DROP COLUMN "work_start"; ALTER TABLE "users" DROP COLUMN "work_end";'
            ' ALTER TABLE "users" DROP COLUMN "timezone";'
        )
    if "expense_lines" in [m.name for m in chain[chain.index(roles_mig) + 1 :]]:
        raw.execute('ALTER TABLE "expenses" DROP COLUMN "lines"')
    raw.commit()
    raw.close()
    new = SQLiteRepository(data)
    new.startup()
    roles = {u["username"]: u["role"] for u in new.list("users")}
    assert roles == {"b": "boss", "m": "employee", "a": "accountant"}
    assert new.get("users", "user00000002")["future"] == 1  # unknown fields survive
    new.close()


# ------------------------------------------------------------------ review follow-ups
def test_bootstrap_only_contains_what_the_role_may_see(app, boss_client, clients):
    store = app.state.services.store
    store.save("templates", {"name": "Mine", "kind": "offer", "body": "x"})
    (store.data_dir / "users.sync-conflict-20260101.json").write_text("{}")
    boot = {role: c.get("/api/bootstrap").json() for role, c in clients.items()}
    assert boot["boss"]["conflicts"] and any(t.get("name") == "Mine" for t in boot["boss"]["templates"])
    assert boot["employee"]["templates"] == [] and boot["employee"]["conflicts"] == []
    assert boot["accountant"]["templates"] and boot["accountant"]["conflicts"] == []


def test_boss_count_normalises_legacy_and_ignores_unknown_roles(app, boss_client):
    auth = app.state.services.auth
    store = app.state.services.store
    boss_id = boss_client.get("/api/auth/me").json()["id"]
    assert auth._boss_count() == 1
    store.save("users", {"username": "old", "name": "Old", "role": "member", "active": True})
    store.save("users", {"username": "odd", "name": "Odd", "role": "BOSS", "active": True})  # no rights at all
    store.save("users", {"username": "gone", "name": "Gone", "role": "boss", "active": False})
    assert auth._boss_count() == 1
    assert auth._boss_count(excluding=boss_id) == 0
    r = boss_client.put(
        f"/api/users/{boss_id}", json={"username": "boss", "name": "Test Boss", "role": "employee", "active": True}
    )
    assert r.status_code == 400  # the odd-cased and the inactive "boss" do not count


def test_last_boss_check_and_save_share_one_transaction(app, boss_client, monkeypatch):
    store = app.state.services.store
    boss_id = boss_client.get("/api/auth/me").json()["id"]
    depth = {"now": 0, "at_count": None, "at_save": None}
    real_tx, real_count, real_save = store.transaction, app.state.services.auth._boss_count, store.save
    from contextlib import contextmanager

    @contextmanager
    def tx():
        with real_tx():
            depth["now"] += 1
            try:
                yield
            finally:
                depth["now"] -= 1

    def count(*a, **k):
        depth["at_count"] = depth["now"]
        return real_count(*a, **k)

    def save(*a, **k):
        depth["at_save"] = depth["now"]
        return real_save(*a, **k)

    monkeypatch.setattr(store, "transaction", tx)
    monkeypatch.setattr(app.state.services.auth, "_boss_count", count)
    monkeypatch.setattr(store, "save", save)
    boss_client.post("/api/users", json={"username": "b2", "name": "B2", "role": "boss", "password": PASSWORD})
    r = boss_client.put(
        f"/api/users/{boss_id}", json={"username": "boss", "name": "Test Boss", "role": "employee", "active": True}
    )
    assert r.status_code == 200, r.text
    assert depth["at_count"] == 1 and depth["at_save"] == 1


def test_planning_warnings_only_for_roles_with_planning_access():
    from permita38.api.routers.records import _with_warnings

    class Stub:
        class planning:  # noqa: N801
            @staticmethod
            def warnings_for(coll, rec):
                return [{"kind": "x"}]

    rec = {"id": "p1"}
    for role in ("employee", "accountant"):
        assert "planning_warnings" not in _with_warnings(Stub, "projects", rec, {"role": role})
    assert _with_warnings(Stub, "projects", rec, {"role": "boss"})["planning_warnings"] == [{"kind": "x"}]


def test_employee_gets_404_for_unassigned_project_and_does_not_see_its_tasks(boss_client, clients):
    emp = clients["employee"]
    mine = boss_client.post("/api/projects", json={"name": "Mine", "member_ids": [emp.user_id]}).json()
    other = boss_client.post(
        "/api/projects", json={"name": "Other", "member_ids": [boss_client.get("/api/auth/me").json()["id"]]}
    ).json()
    t_mine = boss_client.post("/api/tasks", json={"name": "T1", "project_id": mine["id"]}).json()
    t_other = boss_client.post("/api/tasks", json={"name": "T2", "project_id": other["id"]}).json()
    assert emp.get(f"/api/projects/{other['id']}").status_code == 404
    assert emp.get(f"/api/projects/{mine['id']}").status_code == 200
    assert [p["id"] for p in emp.get("/api/projects").json()] == [mine["id"]]
    assert emp.get(f"/api/tasks/{t_other['id']}").status_code == 404
    assert [t["id"] for t in emp.get("/api/tasks").json()] == [t_mine["id"]]
