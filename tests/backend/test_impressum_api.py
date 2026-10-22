"""HTTP API of the Impressum lookup: access control, contract, no side effects."""

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.repository import COLLECTIONS

from .conftest import PASSWORD
from .impressum_helpers import FakeFetcher, page

URL = "https://beispiel.example/impressum"
ENDPOINT = "/api/customers/lookup-impressum"


def use_fake(app, routes=None):
    fake = FakeFetcher(routes if routes is not None else {URL: page(URL, "gmbh_register")})
    app.state.services.impressum.fetcher = fake
    return fake


def snapshot(app) -> dict:
    store = app.state.services.store
    data = {c: store.list(c) for c in COLLECTIONS}
    data["settings"] = store.settings()
    return data


def test_requires_login(app, client):
    use_fake(app)
    assert client.post(ENDPOINT, json={"url": URL}).status_code == 401


def test_members_are_refused(app, member_client):
    fake = use_fake(app)
    assert member_client.post(ENDPOINT, json={"url": URL}).status_code == 403
    assert fake.calls == []


def test_boss_gets_fields_confidence_evidence_and_warnings(app, boss_client):
    use_fake(app)
    r = boss_client.post(ENDPOINT, json={"url": URL})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["fields"]["name"] == "Beispiel Software GmbH"
    assert body["fields"]["managing_directors"] == ["Erika Mustermann", "Max Beispiel"]
    assert body["fields"]["vat_id"] == "DE123456788" and body["fields"]["zip"] == "80331"
    assert set(body["confidence"]) == set(body["evidence"]) == set(body["fields"])
    assert body["warnings"] == [] and isinstance(body["candidates"], dict)


def test_lookup_never_writes_anything(app, boss_client):
    boss_client.post("/api/customers", json={"name": "Bestehender Kunde GmbH"})
    use_fake(app)
    before = snapshot(app)
    assert boss_client.post(ENDPOINT, json={"url": URL}).status_code == 200
    assert boss_client.post(ENDPOINT, json={"url": "https://nowhere.example/"}).status_code == 502
    assert snapshot(app) == before  # no customer, setting, attachment record or anything else was created or changed
    assert len(boss_client.get("/api/customers").json()) == 1


@pytest.mark.parametrize("payload", [{}, {"url": None}, {"url": "x" * 2100}, {"url": 5}, []])
def test_invalid_bodies_are_422(app, boss_client, payload):
    fake = use_fake(app)
    assert boss_client.post(ENDPOINT, json=payload).status_code == 422
    assert fake.calls == []


def test_errors_are_json_with_detail(app, boss_client):
    use_fake(app, {})
    r = boss_client.post(ENDPOINT, json={"url": "https://missing.example/"})
    assert r.status_code == 502 and "detail" in r.json()


def test_rate_limit_over_http(app, boss_client):
    use_fake(app)
    app.state.services.impressum.rate_limit = (3, 60.0)
    codes = [boss_client.post(ENDPOINT, json={"url": URL}).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


@pytest.mark.parametrize(
    "url,status",
    [
        ("http://127.0.0.1/", 400), ("http://localhost:8765/api/auth/status", 400), ("http://169.254.169.254/latest/", 400),
        ("http://[::1]/", 400), ("http://metadata.google.internal/", 400), ("http://10.0.0.1/", 400),
        ("http://2130706433/", 400), ("http://example.com:22/", 400), ("file:///etc/passwd", 422),
        ("ftp://example.com/", 422), ("http://user:pw@example.com/", 422), ("javascript:alert(1)", 422),
    ],
)  # fmt: skip
def test_ssrf_attempts_are_refused_with_the_real_fetcher(boss_client, url, status):
    """No fake here: the production SafeFetcher refuses these before any connection is made."""
    r = boss_client.post(ENDPOINT, json={"url": url})
    assert r.status_code == status, r.text


def test_feature_switch(tmp_path):
    cfg = Config(
        data_dir=tmp_path / "data", secret_key="test-secret-key", web_dir=None, features={"impressum_lookup": False}
    )
    app = get_app(cfg)
    with TestClient(app) as c:
        c.post("/api/auth/setup", json={"username": "boss", "name": "B", "password": PASSWORD})
        token = c.post("/api/auth/login", json={"username": "boss", "password": PASSWORD}).json()["token"]
        c.headers["Authorization"] = "Bearer " + token
        fake = use_fake(app)
        assert c.post(ENDPOINT, json={"url": URL}).status_code == 403
        assert fake.calls == []
        assert c.get("/api/bootstrap").json()["features"] == {"impressum_lookup": False}


def test_bootstrap_announces_the_feature(boss_client, member_client):
    assert boss_client.get("/api/bootstrap").json()["features"] == {"impressum_lookup": True}
    assert member_client.get("/api/bootstrap").json()["features"] == {"impressum_lookup": True}


def test_openapi_documents_the_endpoint(app):
    spec = app.openapi()
    op = spec["paths"][ENDPOINT]["post"]
    assert "lookup-impressum" in ENDPOINT and op["responses"]["200"]
    assert "ImpressumLookup" in spec["components"]["schemas"]


def test_feature_switch_from_yaml_and_environment(tmp_path, monkeypatch):
    from permita38.core.config import load_config

    monkeypatch.delenv("PERMITA38_FEATURE_IMPRESSUM_LOOKUP", raising=False)
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("secret_key: " + "k" * 32 + "\nfeatures:\n  impressum_lookup: false\n", encoding="utf-8")
    assert load_config(cfg_file, data_dir=tmp_path / "d").features.impressum_lookup is False
    monkeypatch.setenv("PERMITA38_FEATURE_IMPRESSUM_LOOKUP", "true")  # environment beats the file
    assert load_config(cfg_file, data_dir=tmp_path / "d").features.impressum_lookup is True
    monkeypatch.setenv("PERMITA38_FEATURE_IMPRESSUM_LOOKUP", "off")
    other = tmp_path / "other.yaml"
    other.write_text("secret_key: " + "k" * 32 + "\n", encoding="utf-8")
    assert load_config(other, data_dir=tmp_path / "d").features.impressum_lookup is False
    monkeypatch.delenv("PERMITA38_FEATURE_IMPRESSUM_LOOKUP")
    assert load_config(other, data_dir=tmp_path / "d").features.impressum_lookup is True  # default: on
