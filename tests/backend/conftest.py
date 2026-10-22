import os

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config

PASSWORD = "correct horse battery"


def pytest_ignore_collect(collection_path, config):
    """`pytest` / `make test` is the fast suite. The randomized suite (tests/backend/randomized) is only collected
    when it is asked for: by path, with `-m randomized`, or with PERMITA38_RANDOM=1."""
    if collection_path.name != "randomized" or not collection_path.is_dir():
        return None
    asked = (
        os.environ.get("PERMITA38_RANDOM") == "1"
        or "randomized" in (config.getoption("markexpr") or "")
        or any("randomized" in str(a) for a in config.invocation_params.args)
    )
    return None if asked else True


@pytest.fixture
def config(tmp_path):
    return Config(data_dir=tmp_path / "data", secret_key="test-secret-key", web_dir=None)


@pytest.fixture
def app(config):
    return get_app(config)


@pytest.fixture
def client(app):
    """Anonymous client."""
    with TestClient(app) as c:
        yield c


def _login(app, username, password=PASSWORD):
    c = TestClient(app)
    r = c.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    c.headers["Authorization"] = "Bearer " + r.json()["token"]
    return c


@pytest.fixture
def boss_client(app, client):
    r = client.post(
        "/api/auth/setup",
        json={"username": "boss", "name": "Test Boss", "password": PASSWORD, "company_name": "Demo Studio"},
    )
    assert r.status_code == 200, r.text
    c = _login(app, "boss")
    yield c
    c.close()


@pytest.fixture
def member_client(app, boss_client):
    r = boss_client.post(
        "/api/users", json={"username": "mia", "name": "Mia Member", "role": "member", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    c = _login(app, "mia")
    c.user_id = r.json()["id"]  # type: ignore[attr-defined]
    yield c
    c.close()
