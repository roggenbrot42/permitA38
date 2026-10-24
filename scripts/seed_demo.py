#!/usr/bin/env python3
"""Fill an empty permitA38 data folder with FAKE demo data (local preview only, see docs/preview.md).

Reads the same PERMITA38_* environment variables as the app (scripts/preview.sh sets them) and talks to the public
HTTP API in-process (FastAPI TestClient). Nothing here is real: names, addresses, IBAN and tax ids are invented.

The boss user is ``demo`` with the password ``demo``. That is shorter than the production password policy allows,
so the setup call uses a long throw-away password and the stored hash is then replaced through the auth service's
own hashing function. This bypass lives only in this script; the production policy is unchanged.
"""

import secrets
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi.testclient import TestClient  # noqa: E402

from permita38.api.server import get_app  # noqa: E402
from permita38.core.config import load_config  # noqa: E402
from permita38.core.service.auth import hash_password, next_session_version  # noqa: E402

USER, PASSWORD = "demo", "demo"


def monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def main() -> None:
    app = get_app(load_config())
    with TestClient(app) as c:
        if c.get("/api/auth/status").json().get("needs_setup") is False:
            sys.exit("the data folder already has users; refusing to seed it")
        tmp_pw = secrets.token_urlsafe(24)
        r = c.post(
            "/api/auth/setup",
            json={"username": USER, "name": "Dana Demo", "password": tmp_pw, "company_name": "Demo Studio"},
        )
        r.raise_for_status()
        # preview-only: the short password cannot pass validate_password, so set the hash through the store
        services = app.state.services
        user = next(u for u in services.store.list("users") if u["username"] == USER)
        services.store.save(
            "users",
            {**user, "password_hash": hash_password(PASSWORD), "pw_changed": next_session_version(user)},
        )
        tok = c.post("/api/auth/login", json={"username": USER, "password": PASSWORD})
        tok.raise_for_status()
        c.headers["Authorization"] = "Bearer " + tok.json()["token"]
        me = c.get("/api/auth/me").json()

        def post(path: str, body: dict) -> dict:
            resp = c.post(path, json=body)
            assert resp.status_code in (200, 201), (path, resp.status_code, resp.text)
            return resp.json()

        def put(path: str, body: dict) -> dict:
            resp = c.put(path, json=body)
            assert resp.status_code == 200, (path, resp.status_code, resp.text)
            return resp.json()

        put(
            "/api/settings",
            {
                "company_name": "Demo Studio",
                "owner_name": "Dana Demo",
                "street": "Beispielweg 1",
                "zip": "10115",
                "city": "Berlin",
                "email": "dana@example.invalid",
                "state": "BE",
                "bank_name": "Demo Bank",
                "iban": "DE89370400440532013000",
                "default_rate": 90,
                "company_type": "freiberufler",
            },
        )
        post(
            "/api/users",
            {"username": "erin", "name": "Erin Employee", "role": "employee", "password": "preview-password-erin"},
        )
        post(
            "/api/users",
            {"username": "alex", "name": "Alex Accountant", "role": "accountant", "password": "preview-password-alex"},
        )

        names = [
            ("Muster Maschinenbau GmbH", "Industriestrasse 5", "70173", "Stuttgart"),
            ("Beispiel Verlag", "Lesegasse 2", "50667", "Koeln"),
            ("Fiktiv Handel KG", "Marktplatz 9", "20095", "Hamburg"),
        ]
        customers = [
            post(
                "/api/customers",
                {
                    "name": n,
                    "street": s,
                    "zip": z,
                    "city": ct,
                    "country": "Deutschland",
                    "email": "info@example.invalid",
                },
            )
            for n, s, z, ct in names
        ]
        today = date.today()
        mon = monday(today)
        projects = []
        for i, (pname, cust) in enumerate(
            [("Website relaunch", 0), ("Katalog Satz", 1), ("Datenmigration", 2), ("Interne Weiterbildung", None)]
        ):
            body = {
                "name": pname,
                "rate": 90,
                "start": (mon - timedelta(weeks=6)).isoformat(),
                "end": (mon + timedelta(weeks=8)).isoformat(),
            }
            if cust is not None:
                body["customer_id"] = customers[cust]["id"]
            else:
                body = {"name": pname, "personal": True}
            projects.append(post("/api/projects", body))

        # six weeks of time entries for the boss and the employee
        users = {u["username"]: u for u in c.get("/api/users").json()}
        for w in range(6, 0, -1):
            for d in range(5):
                day = mon - timedelta(weeks=w) + timedelta(days=d)
                post(
                    "/api/entries",
                    {
                        "date": day.isoformat(),
                        "minutes": 300 + 30 * ((w + d) % 3),
                        "project_id": projects[(w + d) % 3]["id"],
                        "user_id": me["id"],
                        "note": "Demo work",
                    },
                )
                if d < 3:
                    post(
                        "/api/entries",
                        {
                            "date": day.isoformat(),
                            "minutes": 240,
                            "project_id": projects[d % 2]["id"],
                            "user_id": users["erin"]["id"],
                        },
                    )

        # a finished invoice from time, a draft offer that becomes a contract, an open offer
        inv = post(
            "/api/invoices/from-time",
            {"project_id": projects[0]["id"], "date_to": (mon - timedelta(weeks=3)).isoformat()},
        )
        post(f"/api/invoices/{inv['id']}/finalize", {})
        offer = post(
            "/api/offers",
            {
                "customer_id": customers[1]["id"],
                "project_id": projects[1]["id"],
                "title": "Katalog Satz Phase 2",
                "date": today.isoformat(),
                "lines": [{"description": "Satz und Layout", "qty": 40, "unit": "h", "price": 90}],
                "plan": {
                    "start": (mon + timedelta(weeks=2)).isoformat(),
                    "end": (mon + timedelta(weeks=6)).isoformat(),
                    "hours_per_week": 10,
                    "total_hours": 40,
                },
            },
        )
        post(f"/api/offers/{offer['id']}/finalize", {})
        post(
            "/api/offers",
            {
                "customer_id": customers[2]["id"],
                "title": "Schnittstellen Wartung (Entwurf)",
                "lines": [{"description": "Wartung", "qty": 12, "unit": "h", "price": 90}],
            },
        )
        post(
            "/api/invoices",
            {
                "customer_id": customers[0]["id"],
                "title": "Entwurf Hosting",
                "lines": [{"description": "Hosting Quartal", "qty": 1, "unit": "pauschal", "price": 240}],
            },
        )
        for k, (vendor, net, cat) in enumerate(
            [("Beispiel Bueromarkt", 48.0, "Buero"), ("Fiktiv Cloud", 29.9, "Software"), ("Demo Bahn", 86.5, "Reise")]
        ):
            post(
                "/api/expenses",
                {
                    "date": (today - timedelta(days=9 * (k + 1))).isoformat(),
                    "net": net,
                    "vat": round(net * 0.19, 2),
                    "vat_rate": 19,
                    "vendor": vendor,
                    "category": cat,
                },
            )
        # a domestic business trip (fake) with two meals provided on day two and one linked hotel bill
        trip = post(
            "/api/trips",
            {
                "title": "Demo-Kundenbesuch",
                "purpose": "Workshop beim Demo-Kunden",
                "start": "2026-09-14T08:00",
                "end": "2026-09-16T18:00",
                "places": [{"from_date": "2026-09-14", "country": "DE", "city": "Musterstadt"}],
                "meals_provided": [{"date": "2026-09-15", "breakfast": True, "lunch": True}],
                "cost_centre": "general",
            },
        )
        post(
            "/api/expenses",
            {
                "date": "2026-09-14",
                "net": 95.0,
                "vat": round(95.0 * 0.07, 2),
                "vat_rate": 7,
                "vendor": "Beispiel Hotel",
                "category": "Reise",
                "trip_id": trip["id"],
            },
        )
        post(
            "/api/absences",
            {
                "kind": "vacation",
                "start": (mon + timedelta(weeks=4)).isoformat(),
                "end": (mon + timedelta(weeks=4, days=4)).isoformat(),
                "user_id": me["id"],
            },
        )
        post(
            "/api/absences",
            {"kind": "sick", "start": (mon - timedelta(weeks=2)).isoformat(), "user_id": users["erin"]["id"]},
        )
    print(f"Demo data written. Login: {USER} / {PASSWORD} (local preview only).")


if __name__ == "__main__":
    main()
