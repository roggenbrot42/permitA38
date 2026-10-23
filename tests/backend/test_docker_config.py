"""Container / server hosting: env configuration, secret handling, bootstrap account, cookie flags, CLI."""

import stat
import sys

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.cli.main import build_log_config, main
from permita38.core.bootstrap import bootstrap_from_env
from permita38.core.config import Config, load_config

PASSWORD = "correct horse battery"
ENV_VARS = [
    "PERMITA38_DATA", "PERMITA38_HOST", "PERMITA38_PORT", "PERMITA38_SECRET_KEY", "PERMITA38_SECRET_KEY_FILE",
    "PERMITA38_CORS_ORIGINS", "PERMITA38_SERVE_WEB", "PERMITA38_TOKEN_TTL_HOURS", "PERMITA38_COOKIE_SECURE",
    "PERMITA38_TRUST_PROXY", "PERMITA38_FORWARDED_ALLOW_IPS", "PERMITA38_LOG_LEVEL", "PERMITA38_IN_CONTAINER",
    "PERMITA38_WEB_DIR", "PERMITA38_BOOTSTRAP_USERNAME", "PERMITA38_BOOTSTRAP_NAME", "PERMITA38_BOOTSTRAP_PASSWORD",
    "PERMITA38_BOOTSTRAP_PASSWORD_FILE", "PERMITA38_BOOTSTRAP_COMPANY",
]  # fmt: skip


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def cfg_path(tmp_path):
    return tmp_path / "cfg" / "config.yaml"


# ---------- environment overrides ----------
def test_env_overrides(tmp_path, monkeypatch):
    for k, v in {
        "PERMITA38_DATA": str(tmp_path / "d"), "PERMITA38_HOST": "0.0.0.0", "PERMITA38_PORT": "9000",
        "PERMITA38_CORS_ORIGINS": "https://a.example, https://b.example ,", "PERMITA38_SERVE_WEB": "false",
        "PERMITA38_TOKEN_TTL_HOURS": "3", "PERMITA38_COOKIE_SECURE": "yes", "PERMITA38_TRUST_PROXY": "1",
        "PERMITA38_FORWARDED_ALLOW_IPS": "10.0.0.1", "PERMITA38_LOG_LEVEL": "debug",
        "PERMITA38_SECRET_KEY": "x" * 32, "PERMITA38_WEB_DIR": str(tmp_path / "web"),
    }.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    c = load_config(cfg_path(tmp_path))
    assert c.data_dir == (tmp_path / "d").resolve()
    assert (c.host, c.port, c.token_ttl_hours) == ("0.0.0.0", 9000, 3)
    assert c.cors_origins == ["https://a.example", "https://b.example"]
    assert c.serve_web is False and c.cookie_secure is True and c.trust_proxy is True
    assert c.forwarded_allow_ips == "10.0.0.1" and c.log_level == "debug"
    assert c.secret_key == "x" * 32 and c.web_dir == tmp_path / "web"


def test_env_beats_yaml_and_cli_beats_env(tmp_path, monkeypatch):
    p = cfg_path(tmp_path)
    p.parent.mkdir()
    p.write_text("port: 1111\nsecret_key: " + "y" * 32 + "\n")
    assert load_config(p).port == 1111
    monkeypatch.setenv("PERMITA38_PORT", "2222")
    assert load_config(p).port == 2222
    assert load_config(p, port=3333).port == 3333


def test_env_values_are_never_persisted_to_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITA38_PORT", "2222")
    monkeypatch.setenv("PERMITA38_DATA", str(tmp_path / "d"))
    load_config(cfg_path(tmp_path))  # desktop mode: generates + writes a secret
    text = cfg_path(tmp_path).read_text()
    assert "secret_key" in text and "2222" not in text and str(tmp_path / "d") not in text


@pytest.mark.parametrize("name,value", [("PERMITA38_PORT", "abc"), ("PERMITA38_TRUST_PROXY", "maybe")])
def test_invalid_env_values_fail_loudly(tmp_path, monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=name):
        load_config(cfg_path(tmp_path))


# ---------- secret key ----------
def test_secret_from_file_beats_yaml_and_is_stripped(tmp_path, monkeypatch):
    f = tmp_path / "secret"
    f.write_text("s" * 40 + "\n")
    monkeypatch.setenv("PERMITA38_SECRET_KEY_FILE", str(f))
    assert load_config(cfg_path(tmp_path)).secret_key == "s" * 40
    assert not cfg_path(tmp_path).exists()  # nothing generated when the secret is supplied


def test_secret_env_beats_file(tmp_path, monkeypatch):
    f = tmp_path / "secret"
    f.write_text("f" * 40)
    monkeypatch.setenv("PERMITA38_SECRET_KEY_FILE", str(f))
    monkeypatch.setenv("PERMITA38_SECRET_KEY", "e" * 40)
    assert load_config(cfg_path(tmp_path)).secret_key == "e" * 40


def test_short_or_missing_secret_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITA38_SECRET_KEY", "short")
    with pytest.raises(ValueError, match="at least"):
        load_config(cfg_path(tmp_path))
    monkeypatch.delenv("PERMITA38_SECRET_KEY")
    monkeypatch.setenv("PERMITA38_SECRET_KEY_FILE", str(tmp_path / "missing"))
    with pytest.raises(ValueError, match="cannot read"):
        load_config(cfg_path(tmp_path))


def test_container_persists_secret_in_data_volume_and_writes_no_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITA38_IN_CONTAINER", "1")
    monkeypatch.setenv("PERMITA38_DATA", str(tmp_path / "data"))
    c1 = load_config(cfg_path(tmp_path))
    c2 = load_config(cfg_path(tmp_path))
    assert c1.secret_key == c2.secret_key and len(c1.secret_key) == 64 and c1.in_container
    f = tmp_path / "data" / ".secret_key"
    assert f.read_text().strip() == c1.secret_key
    if sys.platform != "win32":
        assert stat.S_IMODE(f.stat().st_mode) == 0o600
    assert not cfg_path(tmp_path).exists()  # no per-device YAML in a container


def test_container_cli_data_override_does_not_write_yaml(tmp_path, monkeypatch):
    monkeypatch.setenv("PERMITA38_IN_CONTAINER", "1")
    load_config(cfg_path(tmp_path), data_dir=tmp_path / "data")
    assert not cfg_path(tmp_path).exists()


def test_desktop_keeps_secret_out_of_the_data_folder(tmp_path):
    c = load_config(cfg_path(tmp_path), data_dir=tmp_path / "data")
    assert cfg_path(tmp_path).is_file() and not (tmp_path / "data" / ".secret_key").exists()
    assert load_config(cfg_path(tmp_path)).secret_key == c.secret_key
    assert not c.in_container


# ---------- bootstrap account ----------
def make_app(tmp_path, **kw):
    return get_app(Config(data_dir=tmp_path / "data", secret_key="k" * 32, web_dir=None, **kw))


def login(app, username, password):
    with TestClient(app) as c:
        return c.post("/api/auth/login", json={"username": username, "password": password})


def test_bootstrap_creates_boss_once_and_never_overwrites(tmp_path):
    app = make_app(tmp_path)
    env = {
        "PERMITA38_BOOTSTRAP_USERNAME": "Owner", "PERMITA38_BOOTSTRAP_NAME": "Olivia Owner",
        "PERMITA38_BOOTSTRAP_PASSWORD": PASSWORD, "PERMITA38_BOOTSTRAP_COMPANY": "Demo Studio",
    }  # fmt: skip
    assert bootstrap_from_env(app.state.services.auth, env) is True
    r = login(app, "owner", PASSWORD)
    assert r.status_code == 200 and r.json()["user"]["role"] == "boss" and r.json()["user"]["name"] == "Olivia Owner"
    with TestClient(app) as c:  # the public setup page is closed afterwards
        assert c.get("/api/auth/status").json()["setup_needed"] is False
        assert (
            c.post("/api/auth/setup", json={"username": "evil", "name": "E", "password": PASSWORD}).status_code == 400
        )
    # a second start with different values changes nothing
    env2 = {**env, "PERMITA38_BOOTSTRAP_USERNAME": "other", "PERMITA38_BOOTSTRAP_PASSWORD": "another password!"}
    assert bootstrap_from_env(app.state.services.auth, env2) is False
    app2 = make_app(tmp_path)
    assert login(app2, "owner", PASSWORD).status_code == 200
    assert login(app2, "other", "another password!").status_code == 401
    assert len(app2.state.services.store.list("users")) == 1
    assert app2.state.services.store.settings()["company_name"] == "Demo Studio"


def test_bootstrap_password_file(tmp_path):
    pw = tmp_path / "pw"
    pw.write_text(PASSWORD + "\n")
    app = make_app(tmp_path)
    env = {"PERMITA38_BOOTSTRAP_USERNAME": "owner", "PERMITA38_BOOTSTRAP_PASSWORD_FILE": str(pw)}
    assert bootstrap_from_env(app.state.services.auth, env) is True
    assert login(app, "owner", PASSWORD).status_code == 200


def test_bootstrap_needs_username_and_password(tmp_path):
    app = make_app(tmp_path)
    auth = app.state.services.auth
    assert bootstrap_from_env(auth, {}) is False
    assert bootstrap_from_env(auth, {"PERMITA38_BOOTSTRAP_USERNAME": "owner"}) is False
    assert bootstrap_from_env(auth, {"PERMITA38_BOOTSTRAP_PASSWORD": PASSWORD}) is False
    assert auth.needs_setup()


def test_bootstrap_rejects_weak_password_and_unreadable_file(tmp_path):
    auth = make_app(tmp_path).state.services.auth
    with pytest.raises(RuntimeError, match="at least 8"):
        bootstrap_from_env(auth, {"PERMITA38_BOOTSTRAP_USERNAME": "owner", "PERMITA38_BOOTSTRAP_PASSWORD": "short"})
    with pytest.raises(RuntimeError, match="cannot read"):
        bootstrap_from_env(
            auth,
            {"PERMITA38_BOOTSTRAP_USERNAME": "owner", "PERMITA38_BOOTSTRAP_PASSWORD_FILE": str(tmp_path / "nope")},
        )
    assert auth.needs_setup()


# ---------- cookie Secure flag ----------
def set_cookie_header(app, base_url="http://testserver", headers=None):
    with TestClient(app, base_url=base_url) as c:
        c.post("/api/auth/setup", json={"username": "boss", "name": "B", "password": PASSWORD})
        r = c.post("/api/auth/login", json={"username": "boss", "password": PASSWORD}, headers=headers or {})
    assert r.status_code == 200
    return r.headers["set-cookie"].lower()


def test_cookie_not_secure_on_plain_http(tmp_path):
    assert "secure" not in set_cookie_header(make_app(tmp_path))


def test_cookie_secure_on_https(tmp_path):
    assert "secure" in set_cookie_header(make_app(tmp_path), "https://testserver")


def test_cookie_secure_behind_trusted_proxy(tmp_path):
    app = make_app(tmp_path, trust_proxy=True)
    assert "secure" in set_cookie_header(app, headers={"X-Forwarded-Proto": "https"})


def test_forwarded_proto_ignored_without_trust_proxy(tmp_path):
    assert "secure" not in set_cookie_header(make_app(tmp_path), headers={"X-Forwarded-Proto": "https"})


def test_trusted_proxy_with_plain_http_forwarded_is_not_secure(tmp_path):
    app = make_app(tmp_path, trust_proxy=True)
    assert "secure" not in set_cookie_header(app, headers={"X-Forwarded-Proto": "http"})


def test_explicit_cookie_secure_overrides_detection(tmp_path):
    assert "secure" in set_cookie_header(make_app(tmp_path, cookie_secure=True))
    assert "secure" not in set_cookie_header(make_app(tmp_path, cookie_secure=False), "https://testserver")


def test_logout_cookie_matches_secure_setting(tmp_path):
    app = make_app(tmp_path, cookie_secure=True)
    with TestClient(app) as c:
        r = c.post("/api/auth/logout")
    assert r.status_code == 204 and "secure" in r.headers["set-cookie"].lower()


# ---------- CLI ----------
def run_cli(tmp_path, *args):
    return main(["-c", str(cfg_path(tmp_path)), "--data", str(tmp_path / "data"), *args])


def test_cli_create_user_boss_then_member(tmp_path, capsys):
    pw = tmp_path / "pw"
    pw.write_text(PASSWORD + "\n")
    run_cli(
        tmp_path,
        "create-user",
        "--username",
        "Boss",
        "--name",
        "The Boss",
        "--role",
        "boss",
        "--password-file",
        str(pw),
    )
    run_cli(tmp_path, "create-user", "--username", "mia", "--name", "Mia", "--password-file", str(pw))
    out = capsys.readouterr().out
    assert "Created boss 'boss'" in out and "Created employee 'mia'" in out
    app = get_app(load_config(cfg_path(tmp_path), data_dir=tmp_path / "data"))
    assert login(app, "boss", PASSWORD).json()["user"]["role"] == "boss"
    assert login(app, "mia", PASSWORD).json()["user"]["role"] == "employee"
    store = app.state.services.store
    assert store.settings()["owner_name"] == "The Boss"  # first boss initialised the settings


def test_cli_create_user_errors(tmp_path):
    pw = tmp_path / "pw"
    pw.write_text(PASSWORD)
    short = tmp_path / "short"
    short.write_text("abc")
    base = ["create-user", "--username", "bob", "--name", "Bob", "--role", "boss"]
    run_cli(tmp_path, *base, "--password-file", str(pw))
    with pytest.raises(SystemExit, match="already taken"):
        run_cli(tmp_path, *base, "--password-file", str(pw))
    with pytest.raises(SystemExit, match="at least 8"):
        run_cli(tmp_path, "create-user", "--username", "x1", "--name", "X", "--password-file", str(short))
    with pytest.raises(SystemExit, match="Cannot read"):
        run_cli(tmp_path, *base, "--password-file", str(tmp_path / "missing"))
    with pytest.raises(SystemExit):  # invalid role is an argparse error
        run_cli(tmp_path, "create-user", "--username", "y1", "--name", "Y", "--role", "root")


def test_cli_create_user_prompts_for_password(tmp_path, monkeypatch):
    answers = iter([PASSWORD, PASSWORD])
    monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
    run_cli(tmp_path, "create-user", "--username", "bob", "--name", "Bob", "--role", "boss")
    assert (
        login(get_app(load_config(cfg_path(tmp_path), data_dir=tmp_path / "data")), "bob", PASSWORD).status_code == 200
    )


# ---------- logging ----------
def test_log_config_logs_to_stdout_at_requested_level():
    cfg = build_log_config("warning")
    assert cfg["handlers"]["stdout"]["stream"] == "ext://sys.stdout"
    assert cfg["loggers"]["uvicorn.access"]["level"] == "WARNING"
