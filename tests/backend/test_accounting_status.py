"""GET /api/accounting/status (accounting.md 16, 22 W9)."""

from permita38.core.service import accounting_status

URL = "/api/accounting/status"


def test_before_initialisation(boss_client):
    r = boss_client.get(URL)
    assert r.status_code == 200
    body = r.json()
    assert body["initialised"] is False and body["start"] is None and body["years"] == []
    assert body["banner_text"].startswith("Tax rules not confirmed by a tax adviser")


def test_after_initialisation_lists_years_and_flags_unconfirmed_rules(app, boss_client):
    store = app.state.services.store
    settings = store.settings()
    settings["accounting.start"] = "2026-01-01"
    store.save_settings(settings)
    r = boss_client.post("/api/accounting/years", json={"year": 2026, "chart": "skr03", "income_basis": "eur"})
    assert r.status_code == 201, r.text
    body = boss_client.get(URL).json()
    assert body["initialised"] is True and body["start"] == "2026-01-01"
    assert body["years"] == [{"year": 2026, "income_basis": "eur", "vat_basis": r.json()["vat_basis"], "closed": False}]
    assert isinstance(body["rules_unconfirmed"], bool)


def test_rules_confirmed_when_every_file_is_verified(boss_client, monkeypatch):
    monkeypatch.setattr(accounting_status, "vat_rules_unconfirmed", lambda year: False)
    monkeypatch.setattr(accounting_status, "chart_unconfirmed", lambda chart, year: False)
    monkeypatch.setattr(accounting_status, "datev_unconfirmed", lambda year: False)
    assert boss_client.get(URL).json()["rules_unconfirmed"] is False
    monkeypatch.setattr(accounting_status, "datev_unconfirmed", lambda year: True)
    assert boss_client.get(URL).json()["rules_unconfirmed"] is True
