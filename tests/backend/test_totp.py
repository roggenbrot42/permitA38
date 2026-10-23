"""Two-factor authentication (TOTP, RFC 6238): primitives, enrolment, two-step login, recovery, reset, secrecy."""

import base64
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from permita38.cli.main import main
from permita38.core.database import head_id
from permita38.core.repository import TABLES
from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service import auth as auth_mod
from permita38.core.service import totp as totp_mod
from permita38.core.service.throttle import AttemptThrottle

from .conftest import PASSWORD

RFC_SECRET = base64.b32encode(b"12345678901234567890").decode()  # the RFC 4226/6238 test key


# ====================================================================== fixtures / helpers
class Clock:
    """Controllable replacement for the `time` module inside the auth/totp services."""

    def __init__(self):
        self.now = float((int(time.time()) // 30) * 30 + 10)  # inside a time step, never at its edge

    def time(self) -> float:
        return self.now


@pytest.fixture(autouse=True)
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(auth_mod, "time", c)
    monkeypatch.setattr(totp_mod, "time", c)
    return c


def code_at(secret: str, clock: Clock, offset: int = 0) -> str:
    return totp_mod.hotp(secret, totp_mod.time_step(clock.now) + offset)


def enroll(client, clock):
    """Enrol the client's user. Returns (secret, recovery codes). The client keeps working (fresh token)."""
    r = client.post("/api/auth/totp/setup", json={"password": PASSWORD})
    assert r.status_code == 200, r.text
    secret = r.json()["secret"]
    r = client.post("/api/auth/totp/enable", json={"code": code_at(secret, clock)})
    assert r.status_code == 200, r.text
    client.headers["Authorization"] = "Bearer " + r.json()["token"]
    clock.now += 30  # the enrolment code is spent; continue in the next step
    return secret, r.json()["recovery_codes"]


def first_factor(app, username="boss", password=PASSWORD):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    return c, r


def sign_in(app, secret, clock, username="boss", **second):
    """Full two-step login. Returns (client, response of the second step)."""
    c, r = first_factor(app, username)
    assert r.status_code == 200 and r.json()["mfa_required"] is True, r.text
    body = {"mfa_token": r.json()["mfa_token"]} | (second or {"code": code_at(secret, clock)})
    r2 = c.post("/api/auth/login/totp", json=body)
    return c, r2


@pytest.fixture
def services(app):
    return app.state.services


# ====================================================================== RFC primitives
def test_hotp_rfc4226_vectors():
    expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]
    assert [totp_mod.hotp(RFC_SECRET, i) for i in range(10)] == expected


@pytest.mark.parametrize(
    "when,eight_digits",
    [
        (59, "94287082"),
        (1111111109, "07081804"),
        (1111111111, "14050471"),
        (1234567890, "89005924"),
        (2000000000, "69279037"),
        (20000000000, "65353130"),
    ],
)
def test_totp_rfc6238_sha1_vectors(when, eight_digits):
    assert totp_mod.hotp(RFC_SECRET, when // 30, digits=8) == eight_digits
    assert totp_mod.totp(RFC_SECRET, when) == eight_digits[-6:]  # our 6-digit code is the same truncation


def test_generated_secrets_are_160_bit_base32_and_unique():
    secrets_ = {totp_mod.generate_secret() for _ in range(50)}
    assert len(secrets_) == 50
    for s in secrets_:
        assert len(s) == 32 and "=" not in s
        assert len(base64.b32decode(s)) == 20


def test_window_accepts_one_step_either_side_and_rejects_two():
    now = 1_700_000_015.0
    cur = totp_mod.time_step(now)
    for off in (-1, 0, 1):
        assert totp_mod.match_step(RFC_SECRET, totp_mod.hotp(RFC_SECRET, cur + off), now) == cur + off
    for off in (-2, 2, 10):
        assert totp_mod.match_step(RFC_SECRET, totp_mod.hotp(RFC_SECRET, cur + off), now) is None


def test_replay_protection_requires_a_newer_step():
    now = 1_700_000_015.0
    cur = totp_mod.time_step(now)
    code = totp_mod.hotp(RFC_SECRET, cur)
    assert totp_mod.match_step(RFC_SECRET, code, now, last_step=cur - 1) == cur
    assert totp_mod.match_step(RFC_SECRET, code, now, last_step=cur) is None  # same step again
    assert totp_mod.match_step(RFC_SECRET, totp_mod.hotp(RFC_SECRET, cur - 1), now, last_step=cur) is None  # older
    assert totp_mod.match_step(RFC_SECRET, totp_mod.hotp(RFC_SECRET, cur + 1), now, last_step=cur) == cur + 1


@pytest.mark.parametrize("bad", ["", " ", "12345", "1234567", "abcdef", "١٢٣٤٥٦", "12 34", "000000\x00", "💥" * 6])
def test_garbage_codes_never_raise(bad):
    assert totp_mod.match_step(RFC_SECRET, bad, 1_700_000_015.0) is None


def test_code_with_spaces_is_accepted():
    now = 1_700_000_015.0
    code = totp_mod.totp(RFC_SECRET, now)
    assert totp_mod.match_step(RFC_SECRET, f"{code[:3]} {code[3:]}", now) is not None


def test_otpauth_uri_and_qr():
    uri = totp_mod.otpauth_uri("ABCDEFGH", "a.b@x")
    assert uri.startswith("otpauth://totp/permitA38%3Aa.b%40x?secret=ABCDEFGH&issuer=permitA38")
    assert "algorithm=SHA1" in uri and "digits=6" in uri and "period=30" in uri
    qr = totp_mod.qr_data_uri(uri)
    assert qr.startswith("data:image/svg+xml") and "<script" not in qr.lower()


def test_recovery_codes_format_hash_and_match():
    codes = [totp_mod.new_recovery_code() for _ in range(20)]
    assert len(set(codes)) == 20
    assert all(len(c) == 19 and c.count("-") == 3 for c in codes)
    hashes = [totp_mod.hash_recovery_code(c) for c in codes[:5]]
    assert all(codes[i] not in h for i, h in enumerate(hashes))  # only hashes are kept
    assert hashes[0] != totp_mod.hash_recovery_code(codes[0])  # salted
    assert totp_mod.match_recovery_code(codes[3], hashes) == 3
    assert totp_mod.match_recovery_code(codes[3].upper().replace("-", " "), hashes) == 3  # forgiving input
    assert totp_mod.match_recovery_code(codes[10], hashes) is None
    assert totp_mod.match_recovery_code("", hashes) is None


# ====================================================================== throttle unit
def test_throttle_any_key_locks_and_failures_count_against_all(monkeypatch):
    from permita38.core.service import throttle as th

    now = [1000.0]
    monkeypatch.setattr(th.time, "time", lambda: now[0])
    t = AttemptThrottle(max_failures=3, window=60)
    for _ in range(3):
        t.fail("user:a", "ip:1.2.3.4")
    with pytest.raises(Exception) as e:
        t.check("user:b", "ip:1.2.3.4")  # a different user from the same IP is locked too
    assert getattr(e.value, "status", None) == 429
    t.check("user:b", "ip:9.9.9.9")  # unrelated keys are fine
    now[0] += 61
    t.check("user:a", "ip:1.2.3.4")  # window passed
    t.fail("user:a")
    t.clear("user:a")
    t.check("user:a")


# ====================================================================== enrolment
def test_status_before_and_after(boss_client, clock):
    assert boss_client.get("/api/auth/totp/status").json() == {
        "enabled": False,
        "pending_setup": False,
        "recovery_codes_left": 0,
    }
    boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD})
    assert boss_client.get("/api/auth/totp/status").json()["pending_setup"] is True
    enroll(boss_client, clock)
    assert boss_client.get("/api/auth/totp/status").json() == {
        "enabled": True,
        "pending_setup": False,
        "recovery_codes_left": 10,
    }
    assert boss_client.get("/api/auth/me").json()["totp_enabled"] is True


def test_endpoints_require_authentication(client):
    for method, path, body in [
        ("get", "/api/auth/totp/status", None),
        ("post", "/api/auth/totp/setup", {"password": "x"}),
        ("post", "/api/auth/totp/enable", {"code": "123456"}),
        ("post", "/api/auth/totp/disable", {"password": "x"}),
        ("post", "/api/auth/totp/recovery-codes/regenerate", {"password": "x", "code": "1"}),
        ("post", "/api/users/abcdef123456/totp/reset", {"password": "x"}),
    ]:
        r = getattr(client, method)(path, **({"json": body} if body else {}))
        assert r.status_code == 401, path


def test_setup_needs_the_current_password(boss_client):
    r = boss_client.post("/api/auth/totp/setup", json={"password": "wrong password"})
    assert r.status_code == 401
    r = boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD})
    assert r.status_code == 200
    body = r.json()
    assert len(body["secret"]) == 32 and body["otpauth_uri"].startswith("otpauth://totp/")
    assert body["qr_svg"].startswith("data:image/svg+xml") and body["account"] == "boss"
    assert body["secret"] in body["otpauth_uri"]


def test_setup_does_not_change_login_until_enabled(app, boss_client):
    boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD})
    _, r = first_factor(app)
    assert r.status_code == 200 and r.json()["mfa_required"] is False and r.json()["token"]


def test_setup_can_be_restarted_until_enabled_but_not_afterwards(boss_client, clock):
    a = boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD}).json()["secret"]
    b = boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD}).json()["secret"]
    assert a != b
    assert boss_client.post("/api/auth/totp/enable", json={"code": code_at(a, clock)}).status_code == 400  # old secret
    enroll(boss_client, clock)
    assert boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD}).status_code == 400


def test_enable_rejects_wrong_code_and_requires_setup(boss_client, clock):
    assert boss_client.post("/api/auth/totp/enable", json={"code": "123456"}).status_code == 400  # no setup yet
    secret = boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD}).json()["secret"]
    wrong = code_at(secret, clock, 5)
    assert boss_client.post("/api/auth/totp/enable", json={"code": wrong}).status_code == 400
    assert boss_client.get("/api/auth/totp/status").json()["enabled"] is False


def test_enable_returns_ten_unique_recovery_codes_and_keeps_the_session(boss_client, clock):
    old_auth = boss_client.headers["Authorization"]
    secret = boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD}).json()["secret"]
    r = boss_client.post("/api/auth/totp/enable", json={"code": code_at(secret, clock)})
    codes = r.json()["recovery_codes"]
    assert len(codes) == 10 and len(set(codes)) == 10
    clock.now += 1
    # sessions from before the enrolment end (they never proved a second factor) ...
    assert boss_client.get("/api/auth/me").status_code == 401
    # ... the fresh token returned by /enable continues the session
    boss_client.headers["Authorization"] = "Bearer " + r.json()["token"]
    assert boss_client.get("/api/auth/me").status_code == 200
    assert old_auth != boss_client.headers["Authorization"]


# ====================================================================== two-step login
def test_login_with_2fa_returns_a_challenge_not_a_session(app, boss_client, clock):
    enroll(boss_client, clock)
    c, r = first_factor(app)
    body = r.json()
    assert r.status_code == 200 and body["mfa_required"] is True and body["mfa_token"]
    assert not body.get("token") and not body.get("user")
    assert "authorization" not in c.cookies  # no session cookie yet
    assert c.get("/api/auth/me").status_code == 401


def test_wrong_password_looks_the_same_for_2fa_and_plain_accounts(app, boss_client, member_client, clock):
    enroll(boss_client, clock)  # boss has 2FA, mia has not
    _, with_2fa = first_factor(app, "boss", "wrong password")
    _, without = first_factor(app, "mia", "wrong password")
    _, unknown = first_factor(app, "nobody", "wrong password")
    assert with_2fa.status_code == without.status_code == unknown.status_code == 401
    assert with_2fa.json() == without.json() == unknown.json()


def test_full_two_step_login_sets_cookie_and_returns_user(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    c, r = sign_in(app, secret, clock)
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["username"] == "boss" and body["user"]["totp_enabled"] is True and body["token"]
    assert "authorization" in c.cookies and "HttpOnly" in r.headers["set-cookie"]
    assert "SameSite=strict" in r.headers["set-cookie"] or "samesite=strict" in r.headers["set-cookie"].lower()
    assert c.get("/api/auth/me").status_code == 200  # the cookie alone carries the session
    me = TestClient(app)
    me.headers["Authorization"] = "Bearer " + body["token"]
    assert me.get("/api/auth/me").json()["username"] == "boss"


def test_code_window_at_login(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    assert sign_in(app, secret, clock, code=code_at(secret, clock, +1))[1].status_code == 200
    clock.now += 90
    assert sign_in(app, secret, clock, code=code_at(secret, clock, -1))[1].status_code == 200  # newer than the last
    clock.now += 30 * 5
    assert sign_in(app, secret, clock, code=code_at(secret, clock, -2))[1].status_code == 401
    assert sign_in(app, secret, clock, code=code_at(secret, clock, +2))[1].status_code == 401
    assert sign_in(app, secret, clock, code=code_at(secret, clock, 0))[1].status_code == 200


def test_a_code_cannot_be_used_twice(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    code = code_at(secret, clock)
    assert sign_in(app, secret, clock, code=code)[1].status_code == 200
    r = sign_in(app, secret, clock, code=code)[1]  # brand-new challenge, same code
    assert r.status_code == 401
    # an older code from a step before the last accepted one is refused as well
    assert sign_in(app, secret, clock, code=code_at(secret, clock, -1))[1].status_code == 401
    clock.now += 30
    assert sign_in(app, secret, clock)[1].status_code == 200


def test_enrolment_code_cannot_be_replayed_at_login(app, boss_client, clock):
    secret = boss_client.post("/api/auth/totp/setup", json={"password": PASSWORD}).json()["secret"]
    spent = code_at(secret, clock)
    boss_client.post("/api/auth/totp/enable", json={"code": spent})
    assert sign_in(app, secret, clock, code=spent)[1].status_code == 401


def test_wrong_codes_are_throttled_even_for_the_right_code_afterwards(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    _, r = first_factor(app)
    token = r.json()["mfa_token"]
    c = TestClient(app)
    for _ in range(5):
        assert c.post("/api/auth/login/totp", json={"mfa_token": token, "code": "000000"}).status_code == 401
    r = c.post("/api/auth/login/totp", json={"mfa_token": token, "code": code_at(secret, clock)})
    assert r.status_code == 429  # locked out: the correct code does not help during the lock
    assert sign_in(app, secret, clock)[1].status_code == 429  # also with a new challenge


def test_challenge_token_is_not_a_session_token(app, boss_client, clock):
    enroll(boss_client, clock)
    _, r = first_factor(app)
    mfa = r.json()["mfa_token"]
    c = TestClient(app)
    assert c.get("/api/auth/me", headers={"Authorization": "Bearer " + mfa}).status_code == 401
    c.cookies.set("authorization", mfa)
    assert c.get("/api/auth/me").status_code == 401
    assert c.get("/api/customers", headers={"Authorization": "Bearer " + mfa}).status_code == 401


def test_session_token_cannot_complete_the_second_step(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    session = boss_client.headers["Authorization"].split()[1]
    c = TestClient(app)
    r = c.post("/api/auth/login/totp", json={"mfa_token": session, "code": code_at(secret, clock)})
    assert r.status_code == 401


@pytest.mark.parametrize("junk", ["", "a.b.c", "not-a-token", "x" * 500, "..", "💥"])
def test_garbage_challenge_tokens_are_rejected(app, boss_client, clock, junk):
    secret, _ = enroll(boss_client, clock)
    c = TestClient(app)
    r = c.post("/api/auth/login/totp", json={"mfa_token": junk, "code": code_at(secret, clock)})
    assert r.status_code == 401


def test_tampered_challenge_is_rejected(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    _, r = first_factor(app)
    head, body, sig = r.json()["mfa_token"].split(".")
    forged_body = base64.urlsafe_b64encode(base64.urlsafe_b64decode(body + "==").replace(b'"mfa"', b'"xxx"')).rstrip(
        b"="
    )
    c = TestClient(app)
    for bad in (f"{head}.{forged_body.decode()}.{sig}", f"{head}.{body}.{sig[:-2]}AA"):
        r2 = c.post("/api/auth/login/totp", json={"mfa_token": bad, "code": code_at(secret, clock)})
        assert r2.status_code == 401


def test_challenge_expires_after_five_minutes(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    _, r = first_factor(app)
    token = r.json()["mfa_token"]
    clock.now += 301
    c = TestClient(app)
    assert c.post("/api/auth/login/totp", json={"mfa_token": token, "code": code_at(secret, clock)}).status_code == 401


def test_challenge_is_single_use(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    _, r = first_factor(app)
    token = r.json()["mfa_token"]
    c = TestClient(app)
    assert c.post("/api/auth/login/totp", json={"mfa_token": token, "code": code_at(secret, clock)}).status_code == 200
    clock.now += 30
    assert c.post("/api/auth/login/totp", json={"mfa_token": token, "code": code_at(secret, clock)}).status_code == 401


def test_challenge_dies_when_the_password_changes_or_user_is_deactivated(app, boss_client, member_client, clock):
    secret, _ = enroll(boss_client, clock)
    _, r = first_factor(app)
    token = r.json()["mfa_token"]
    clock.now += 2
    r2 = boss_client.post(
        "/api/auth/change-password", json={"old_password": PASSWORD, "new_password": "another long pw"}
    )
    assert r2.status_code == 200
    c = TestClient(app)
    assert c.post("/api/auth/login/totp", json={"mfa_token": token, "code": code_at(secret, clock)}).status_code == 401


def test_deactivated_user_cannot_finish_login(app, boss_client, member_client, services, clock):
    msecret, _ = enroll(member_client, clock)
    _, r = first_factor(app, "mia")
    token = r.json()["mfa_token"]
    boss_client.put(f"/api/users/{member_client.user_id}", json={"username": "mia", "name": "Mia", "active": False})
    c = TestClient(app)
    code = code_at(msecret, clock)
    assert c.post("/api/auth/login/totp", json={"mfa_token": token, "code": code}).status_code == 401


def test_service_login_never_returns_a_session_for_a_2fa_user(app, boss_client, services, clock):
    enroll(boss_client, clock)
    with pytest.raises(Exception) as e:
        services.auth.login("boss", PASSWORD)
    assert getattr(e.value, "status", None) == 401


def test_login_second_step_requires_one_of_the_codes(app, boss_client, clock):
    enroll(boss_client, clock)
    _, r = first_factor(app)
    c = TestClient(app)
    assert c.post("/api/auth/login/totp", json={"mfa_token": r.json()["mfa_token"]}).status_code == 401
    assert c.post("/api/auth/login/totp", json={}).status_code == 422


# ====================================================================== recovery codes
def test_recovery_code_logs_in_once(app, boss_client, clock):
    secret, codes = enroll(boss_client, clock)
    c, r = sign_in(app, secret, clock, recovery_code=codes[0])
    assert r.status_code == 200 and r.json()["recovery_codes_left"] == 9
    assert c.get("/api/auth/me").status_code == 200
    assert sign_in(app, secret, clock, recovery_code=codes[0])[1].status_code == 401  # single use
    assert sign_in(app, secret, clock, recovery_code=codes[1].upper())[1].status_code == 200  # case-insensitive
    assert boss_client.get("/api/auth/totp/status").json()["recovery_codes_left"] == 8


def test_recovery_login_does_not_burn_the_totp_step_and_vice_versa(app, boss_client, clock):
    secret, codes = enroll(boss_client, clock)
    assert sign_in(app, secret, clock, recovery_code=codes[2])[1].status_code == 200
    assert sign_in(app, secret, clock)[1].status_code == 200


def test_regenerate_recovery_codes_replaces_all_old_ones(app, boss_client, clock):
    secret, old = enroll(boss_client, clock)
    r = boss_client.post(
        "/api/auth/totp/recovery-codes/regenerate", json={"password": PASSWORD, "code": code_at(secret, clock)}
    )
    assert r.status_code == 200
    new = r.json()["recovery_codes"]
    assert len(new) == 10 and not set(new) & set(old)
    assert sign_in(app, secret, clock, recovery_code=old[0])[1].status_code == 401
    assert sign_in(app, secret, clock, recovery_code=new[0])[1].status_code == 200


def test_regenerate_needs_password_and_a_totp_code_not_a_recovery_code(boss_client, clock):
    secret, old = enroll(boss_client, clock)
    url = "/api/auth/totp/recovery-codes/regenerate"
    assert boss_client.post(url, json={"password": "nope nope nope", "code": code_at(secret, clock)}).status_code == 401
    assert boss_client.post(url, json={"password": PASSWORD, "code": "000000"}).status_code == 401
    assert boss_client.post(url, json={"password": PASSWORD, "code": old[0]}).status_code == 401
    assert boss_client.get("/api/auth/totp/status").json()["recovery_codes_left"] == 10


def test_only_hashes_of_recovery_codes_are_stored(boss_client, services, clock):
    _, codes = enroll(boss_client, clock)
    user = services.store.list("users")[0]
    stored = user["totp_recovery"]
    assert len(stored) == 10
    blob = repr(user)
    assert not any(c in blob or c.replace("-", "") in blob for c in codes)


# ====================================================================== disable / reset
def test_disable_requires_password_and_a_valid_code(app, boss_client, clock):
    secret, codes = enroll(boss_client, clock)
    url = "/api/auth/totp/disable"
    assert boss_client.post(url, json={"password": PASSWORD}).status_code == 401  # no code at all
    assert boss_client.post(url, json={"password": "wrong wrong", "code": code_at(secret, clock)}).status_code == 401
    assert boss_client.post(url, json={"password": PASSWORD, "code": "000000"}).status_code == 401
    assert boss_client.get("/api/auth/totp/status").json()["enabled"] is True
    r = boss_client.post(url, json={"password": PASSWORD, "code": code_at(secret, clock)})
    assert r.status_code == 200
    clock.now += 1
    assert boss_client.get("/api/auth/me").status_code == 401  # old session ended
    boss_client.headers["Authorization"] = "Bearer " + r.json()["token"]
    assert boss_client.get("/api/auth/me").json()["totp_enabled"] is False
    _, login = first_factor(app)
    assert login.json()["mfa_required"] is False and login.json()["token"]


def test_disable_with_a_recovery_code_and_state_is_cleared(app, boss_client, services, clock):
    secret, codes = enroll(boss_client, clock)
    r = boss_client.post("/api/auth/totp/disable", json={"password": PASSWORD, "recovery_code": codes[0]})
    assert r.status_code == 200
    boss_client.headers["Authorization"] = "Bearer " + r.json()["token"]
    u = services.store.list("users")[0]
    assert not u.get("totp_secret") and not u.get("totp_enabled") and not u.get("totp_recovery")
    assert boss_client.post("/api/auth/totp/disable", json={"password": PASSWORD}).status_code == 400  # already off


def test_boss_can_reset_a_users_2fa(app, boss_client, member_client, clock):
    msecret, _ = enroll(member_client, clock)
    clock.now += 1
    url = f"/api/users/{member_client.user_id}/totp/reset"
    assert boss_client.post(url, json={"password": "wrong wrong"}).status_code == 401
    r = boss_client.post(url, json={"password": PASSWORD})
    assert r.status_code == 200 and r.json()["totp_enabled"] is False
    assert member_client.get("/api/auth/me").status_code == 401  # their sessions ended
    _, login = first_factor(app, "mia")
    assert login.json()["mfa_required"] is False and login.json()["token"]
    assert boss_client.post("/api/users/doesnotexist1/totp/reset", json={"password": PASSWORD}).status_code == 404


def test_members_cannot_reset_anyones_2fa(boss_client, member_client, clock):
    enroll(boss_client, clock)
    boss_id = boss_client.get("/api/auth/me").json()["id"]
    r = member_client.post(f"/api/users/{boss_id}/totp/reset", json={"password": PASSWORD})
    assert r.status_code == 403
    assert boss_client.get("/api/auth/totp/status").json()["enabled"] is True


def test_password_change_ends_other_sessions_and_keeps_2fa(app, boss_client, clock):
    secret, _ = enroll(boss_client, clock)
    other, _ = sign_in(app, secret, clock)
    clock.now += 31
    r = boss_client.post(
        "/api/auth/change-password", json={"old_password": PASSWORD, "new_password": "brand new passw0rd"}
    )
    assert r.status_code == 200
    assert other.get("/api/auth/me").status_code == 401
    _, login = first_factor(app, password="brand new passw0rd")
    assert login.json()["mfa_required"] is True


# ====================================================================== secrecy
def test_secrets_never_appear_in_any_response(app, boss_client, member_client, services, clock):
    secret, codes = enroll(boss_client, clock)
    boss_id = boss_client.get("/api/auth/me").json()["id"]
    stored = services.store.get("users", boss_id)
    forbidden = [
        secret,
        *codes,
        *(c.replace("-", "") for c in codes),
        *stored["totp_recovery"],
        stored["password_hash"],
    ]
    keys = ["totp_secret", "totp_last_step", "totp_recovery", "password_hash", "pw_changed"]

    responses = [
        boss_client.get("/api/auth/me"),
        boss_client.get("/api/auth/totp/status"),
        boss_client.get("/api/users"),
        boss_client.get("/api/bootstrap"),
        boss_client.put(f"/api/users/{boss_id}", json={"username": "boss", "name": "Test Boss", "role": "boss"}),
        member_client.get("/api/users"),
        member_client.get("/api/bootstrap"),
        member_client.get("/api/auth/me"),
        sign_in(app, secret, clock)[1],
    ]
    clock.now += 30
    responses.append(sign_in(app, secret, clock, recovery_code=codes[0])[1])
    responses.append(boss_client.post(f"/api/users/{member_client.user_id}/totp/reset", json={"password": PASSWORD}))
    for r in responses:
        assert r.status_code == 200, r.text
        text = r.text.lower()
        for secret_value in forbidden:
            assert str(secret_value).lower() not in text, (r.request.url, secret_value)
        for k in keys:
            assert k not in text, (r.request.url, k)


def test_users_cannot_write_totp_state_through_the_user_api(boss_client, member_client, services, clock):
    uid = member_client.user_id
    r = boss_client.put(
        f"/api/users/{uid}",
        json={
            "username": "mia", "name": "Mia", "role": "member", "totp_enabled": True, "totp_secret": "AAAAAAAA",
            "totp_recovery": ["x$y"], "totp_last_step": 99999999999,
        },
    )  # fmt: skip
    assert r.status_code == 200
    u = services.store.get("users", uid)
    assert not u.get("totp_enabled") and not u.get("totp_secret") and not u.get("totp_recovery")
    assert not u.get("totp_last_step")


def test_editing_a_user_keeps_their_2fa(boss_client, member_client, services, clock):
    enroll(member_client, clock)
    before = services.store.get("users", member_client.user_id)
    r = boss_client.put(
        f"/api/users/{member_client.user_id}", json={"username": "mia", "name": "Mia R.", "role": "member"}
    )
    assert r.status_code == 200 and r.json()["totp_enabled"] is True
    after = services.store.get("users", member_client.user_id)
    for k in ("totp_secret", "totp_enabled", "totp_recovery", "totp_last_step"):
        assert after[k] == before[k]


# ====================================================================== command line
def test_cli_reset_totp(app, boss_client, config, tmp_path, clock, capsys):
    enroll(boss_client, clock)
    cfg = tmp_path / "cfg.yaml"
    main(["-c", str(cfg), "--data", str(config.data_dir), "reset-totp", "BOSS"])
    assert "is off" in capsys.readouterr().out
    _, login = first_factor(app)
    assert login.json()["mfa_required"] is False and login.json()["token"]
    with pytest.raises(SystemExit):
        main(["-c", str(cfg), "--data", str(config.data_dir), "reset-totp", "nobody"])


# ====================================================================== schema / migration 0002
def test_revision_two_adds_the_totp_columns_to_every_dialect():
    from permita38.core.database import DIALECT_NAMES, list_migrations

    for d in DIALECT_NAMES:
        m = next(x for x in list_migrations(d) if x.id == "00000002")
        assert m.name == "totp"
        for col in ("totp_secret", "totp_enabled", "totp_last_step", "totp_recovery"):
            assert col in m.sql(), (d, col)


def test_migrated_sqlite_database_matches_the_schema_description(tmp_path):
    repo = SQLiteRepository(tmp_path)
    repo.startup()
    for name, table in TABLES.items():
        cols = [r[1] for r in repo.conn.execute(f'PRAGMA table_info("{name}")')]
        expected = table.column_names + ([table.extra_column] if table.extra_column else [])
        assert sorted(cols) == sorted(expected), name
    repo.close()


def test_upgrade_from_revision_one_keeps_users(tmp_path):
    from permita38.core.database import MIGRATIONS_DIR

    data = tmp_path / "data"
    data.mkdir()
    raw = sqlite3.connect(data / DB_FILENAME)
    raw.executescript((MIGRATIONS_DIR / "sqlite" / "0001-initial.sql").read_text())
    raw.execute("PRAGMA user_version = 1")
    raw.execute(
        "INSERT INTO users (id, username, name, role, active, password_hash, pw_changed) "
        "VALUES ('abcdef123456', 'old', 'Old User', 'boss', 1, 'pbkdf2_sha256$1$00$00', 5)"
    )
    raw.commit()
    raw.close()
    repo = SQLiteRepository(data)
    repo.startup()
    assert repo.get_revision() == head_id()
    u = repo.get("users", "abcdef123456")
    assert u["username"] == "old" and not u.get("totp_enabled") and not u.get("totp_secret")
    assert list((data / "backups").glob(f"pre-migration-*-before-{head_id()}.sqlite3"))
    repo.save("users", {**u, "totp_enabled": True, "totp_secret": "ABC", "totp_last_step": 7, "totp_recovery": ["a$b"]})
    again = repo.get("users", "abcdef123456")
    assert again["totp_enabled"] is True and again["totp_last_step"] == 7 and again["totp_recovery"] == ["a$b"]
    repo.close()
