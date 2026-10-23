import getpass
import time

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.cli.main import main
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.service.auth import MAX_PASSWORD_LENGTH

from .conftest import PASSWORD


def test_status_and_setup_only_once(client):
    assert client.get("/api/auth/status").json()["setup_needed"] is True
    body = {"username": "boss", "name": "Boss", "password": PASSWORD}
    assert client.post("/api/auth/setup", json=body).status_code == 200
    assert client.get("/api/auth/status").json()["setup_needed"] is False
    assert client.post("/api/auth/setup", json=body).status_code == 400


@pytest.mark.parametrize(
    "username,password",
    [("boss", "short"), ("Bad Name!", PASSWORD), ("b", PASSWORD), ("", PASSWORD)],
)
def test_setup_rejects_bad_input(client, username, password):
    r = client.post("/api/auth/setup", json={"username": username, "name": "Boss", "password": password})
    assert r.status_code in (400, 422)
    assert client.get("/api/auth/status").json()["setup_needed"] is True


def test_login_success_and_failure(client, boss_client):
    r = client.post("/api/auth/login", json={"username": "BOSS", "password": PASSWORD})  # case-insensitive
    assert r.status_code == 200
    assert r.json()["token"] and r.json()["user"]["username"] == "boss"
    assert "password_hash" not in r.json()["user"]
    assert client.post("/api/auth/login", json={"username": "boss", "password": "wrong password"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "nobody", "password": PASSWORD}).status_code == 401


def test_requires_auth(client):
    for path in ("/api/customers", "/api/bootstrap", "/api/finance", "/api/auth/me"):
        assert client.get(path).status_code == 401
    assert client.get("/api/health").status_code == 200


def test_me_and_bootstrap(boss_client):
    me = boss_client.get("/api/auth/me").json()
    assert me["username"] == "boss" and me["role"] == "boss"
    b = boss_client.get("/api/bootstrap").json()
    assert b["me"]["id"] == me["id"]
    assert b["settings"]["company_name"] == "Demo Studio"


def test_throttling_after_five_failures(client, boss_client):
    for _ in range(5):
        assert (
            client.post("/api/auth/login", json={"username": "boss", "password": "nope nope nope"}).status_code == 401
        )
    # even the right password is refused while throttled
    r = client.post("/api/auth/login", json={"username": "boss", "password": PASSWORD})
    assert r.status_code == 429


def test_tampered_token_rejected(client, boss_client):
    token = boss_client.headers["Authorization"].split()[1]
    head, body, sig = token.split(".")
    bad_sig = sig[:-2] + ("AA" if not sig.endswith("AA") else "BB")
    for bad in (f"{head}.{body}.{bad_sig}", f"{head}.{body}x.{sig}", "garbage", ""):
        r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {bad}"})
        assert r.status_code == 401


def test_expired_token_rejected(app, config, boss_client, monkeypatch):
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + config.token_ttl_hours * 3600 + 60)
    assert boss_client.get("/api/auth/me").status_code == 401


def test_cookie_auth_for_downloads(app, boss_client):
    cust = boss_client.post("/api/customers", json={"name": "ACME GmbH"}).json()
    inv = boss_client.post(
        "/api/invoices",
        json={"customer_id": cust["id"], "lines": [{"description": "Work", "qty": 2, "unit": "h", "price": 100}]},
    ).json()
    login = TestClient(app).post("/api/auth/login", json={"username": "boss", "password": PASSWORD})
    assert "authorization" in login.cookies
    cookie_only = TestClient(app, cookies={"authorization": login.cookies["authorization"]})
    r = cookie_only.get(f"/api/invoices/{inv['id']}/pdf")
    assert r.status_code == 200 and r.content.startswith(b"%PDF-1.4")
    assert TestClient(app).get(f"/api/invoices/{inv['id']}/pdf").status_code == 401


def test_logout_clears_cookie(app):
    c = TestClient(app)
    c.post("/api/auth/setup", json={"username": "boss", "name": "Boss", "password": PASSWORD})
    c.post("/api/auth/login", json={"username": "boss", "password": PASSWORD})
    assert c.get("/api/auth/me").status_code == 200  # cookie auth
    r = c.post("/api/auth/logout")
    assert r.status_code in (200, 204)
    assert "authorization" in r.headers.get("set-cookie", "").lower()
    assert c.get("/api/auth/me").status_code == 401


def test_change_password_invalidates_old_token(app, boss_client):
    old_token = boss_client.headers["Authorization"]
    time.sleep(1.1)  # token iat has one-second resolution
    r = boss_client.post(
        "/api/auth/change-password", json={"old_password": "wrong old one", "new_password": "a brand new password"}
    )
    assert r.status_code == 400
    r = boss_client.post(
        "/api/auth/change-password", json={"old_password": PASSWORD, "new_password": "a brand new password"}
    )
    assert r.status_code in (200, 204), r.text
    other = TestClient(app)
    assert other.get("/api/auth/me", headers={"Authorization": old_token}).status_code == 401
    assert other.post("/api/auth/login", json={"username": "boss", "password": PASSWORD}).status_code == 401
    ok = other.post("/api/auth/login", json={"username": "boss", "password": "a brand new password"})
    assert ok.status_code == 200
    short = other.post(
        "/api/auth/change-password",
        headers={"Authorization": "Bearer " + ok.json()["token"]},
        json={"old_password": "a brand new password", "new_password": "short"},
    )
    assert short.status_code in (400, 422)


def test_users_validation_and_last_boss(boss_client):
    boss = boss_client.get("/api/auth/me").json()
    ok = boss_client.post("/api/users", json={"username": "mia", "name": "Mia", "password": PASSWORD})
    assert ok.status_code == 200 and "password_hash" not in ok.json() and "password" not in ok.json()
    assert (
        boss_client.post("/api/users", json={"username": "MIA", "name": "X", "password": PASSWORD}).status_code == 400
    )
    assert (
        boss_client.post("/api/users", json={"username": "bad name", "name": "X", "password": PASSWORD}).status_code
        == 400
    )
    assert boss_client.post("/api/users", json={"username": "newbie", "name": "X"}).status_code in (400, 422)  # no pw
    assert (
        boss_client.post("/api/users", json={"username": "newbie", "name": "X", "password": "short"}).status_code == 400
    )
    # cannot demote, deactivate or delete the last boss
    demote = {"username": "boss", "name": "Test Boss", "role": "member"}
    assert boss_client.put(f"/api/users/{boss['id']}", json=demote).status_code == 400
    assert (
        boss_client.put(f"/api/users/{boss['id']}", json={**demote, "role": "boss", "active": False}).status_code == 400
    )
    assert boss_client.delete(f"/api/users/{boss['id']}").status_code == 400
    # list never exposes secrets
    for u in boss_client.get("/api/users").json():
        assert "password_hash" not in u and "password" not in u


def test_member_permissions(boss_client, member_client):
    for path in (
        "/api/invoices",
        "/api/offers",
        "/api/finance",
        "/api/customers",
        "/api/settings",
        "/api/forecast",
        "/api/contracts",
        "/api/expenses",
        "/api/project-report",
    ):
        assert member_client.get(path).status_code == 403, path
    assert member_client.post("/api/customers", json={"name": "X"}).status_code == 403
    assert member_client.get("/api/auth/me").json()["role"] == "employee"

    boss_id = boss_client.get("/api/auth/me").json()["id"]
    proj = boss_client.post("/api/projects", json={"name": "Site"}).json()
    # booking for someone else is forced to the member's own user_id
    r = member_client.post(
        "/api/entries", json={"user_id": boss_id, "date": "2026-01-05", "minutes": 60, "project_id": proj["id"]}
    )
    assert r.status_code == 200 and r.json()["user_id"] == member_client.user_id
    boss_client.post("/api/entries", json={"user_id": boss_id, "date": "2026-01-05", "minutes": 90})
    mine = member_client.get("/api/entries").json()
    assert [e["user_id"] for e in mine] == [member_client.user_id]
    # cannot edit or delete someone else's entry
    others = [e for e in boss_client.get("/api/entries").json() if e["user_id"] == boss_id][0]
    assert (
        member_client.put(f"/api/entries/{others['id']}", json={"date": "2026-01-05", "minutes": 1}).status_code == 403
    )
    assert member_client.delete(f"/api/entries/{others['id']}").status_code == 403
    # member can see the users' names but not secrets
    users = member_client.get("/api/users").json()
    assert all("password_hash" not in u for u in users)
    assert (
        member_client.post(
            "/api/users", json={"username": "evil", "name": "Evil", "role": "boss", "password": PASSWORD}
        ).status_code
        == 403
    )


def test_password_maximum_is_enforced_by_the_service_and_the_cli(tmp_path, monkeypatch, capsys):
    """A password the API could not log in with must not be settable by the recovery CLI either."""
    assert MAX_PASSWORD_LENGTH == 1000
    cfg_file = tmp_path / "cfg.yaml"
    data = tmp_path / "d"
    cfg = Config(data_dir=data, secret_key="s" * 16, web_dir=None)
    app = get_app(cfg)
    with TestClient(app) as c:
        assert (
            c.post("/api/auth/setup", json={"username": "boss", "name": "Boss", "password": PASSWORD}).status_code
            == 200
        )
        with pytest.raises(ServiceError) as too_long:
            app.state.services.auth.set_password("boss", "x" * 1001)
        assert too_long.value.status == 400 and "at most 1000" in too_long.value.message

    answers = iter(["x" * 1001, "x" * 1001])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    with pytest.raises(SystemExit) as refused:
        main(["-c", str(cfg_file), "--data", str(data), "set-password", "boss"])
    assert "at most 1000" in str(refused.value)

    long_ok = "y" * 1000
    answers = iter([long_ok, long_ok])
    monkeypatch.setattr(getpass, "getpass", lambda prompt="": next(answers))
    main(["-c", str(cfg_file), "--data", str(data), "set-password", "boss"])
    assert "Password changed." in capsys.readouterr().out
    with TestClient(get_app(cfg)) as c:
        assert c.post("/api/auth/login", json={"username": "boss", "password": long_ok}).status_code == 200
