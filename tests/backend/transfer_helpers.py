"""Shared helpers of the data export/import tests: build instances, seed data, read archives."""

import hashlib
import io
import json
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config

from .conftest import PASSWORD, _login

_OPEN: list[tuple[TestClient, bool]] = []  # (client, entered with __enter__)


def track(client: TestClient, entered: bool = False) -> TestClient:
    """Remember a client so the autouse fixture in conftest.py closes it (open clients keep files open on Windows)."""
    _OPEN.append((client, entered))
    return client


def close_all() -> None:
    while _OPEN:
        client, entered = _OPEN.pop()
        if entered:
            client.__exit__(None, None, None)
        else:
            client.close()


def login(app, username: str):
    return track(_login(app, username))


def make_instance(tmp_path: Path, name: str, *, setup: bool = True):
    """(app, boss client or None) of a fresh instance in ``tmp_path/name``."""
    cfg = Config(data_dir=tmp_path / name, secret_key="test-secret-key-" + name, web_dir=None)
    app = get_app(cfg)
    client = track(TestClient(app), entered=True)
    client.__enter__()
    if not setup:
        return app, client
    r = client.post(
        "/api/auth/setup",
        json={"username": "boss", "name": "Test Boss", "password": PASSWORD, "company_name": "Demo Studio"},
    )
    assert r.status_code == 200, r.text
    return app, login(app, "boss")


def seed(store, *, extras: bool = True) -> dict:
    """A small but connected data set with fake demo data. Returns the ids."""
    x = {"x_future_field": {"tags": ["a", "b"], "rank": 3}} if extras else {}
    cust = store.save("customers", {"name": "Beispiel GmbH", "city": "Köln", "rate": 95.0, **x})
    proj = store.save("projects", {"name": "Relaunch", "customer_id": cust["id"], "status": "active"})
    task = store.save("tasks", {"project_id": proj["id"], "name": "Design"})
    boss = store.list("users")[0]
    store.save(
        "entries",
        {"user_id": boss["id"], "date": "2026-03-02", "minutes": 90, "project_id": proj["id"], "task_id": task["id"]},
    )
    att = store.save_attachment("bill.pdf", b"%PDF-1.4 demo bill")
    store.save("expenses", {"date": "2026-03-03", "net": 10.0, "vat": 1.9, "vendor": '=Shop;"x"', "attachment": att})
    store.save(
        "invoices",
        {
            "number": "RE-2026-0007",
            "customer_id": cust["id"],
            "date": "2026-03-05",
            "status": "sent",
            "lines": [{"description": "Work", "quantity": 1, "unit_price": 100}],
        },
    )
    store.save(
        "number_sequences",
        {
            "id": "n" + hashlib.sha256(b"invoices|2026").hexdigest()[:20],
            "doc_type": "invoices",
            "period": "2026",
            "last_value": 7,
        },
    )
    store.save(
        "allocations",
        {"user_id": boss["id"], "project_id": proj["id"], "start": "2026-03-02", "end": "2026-03-27",
         "hours_per_week": 25.0, "status": "active", **x},
    )
    store.save(
        "calendar_sources",
        {"user_id": boss["id"], "name": "Cal", "kind": "ics", "url": "https://example.invalid/secret-feed-token"},
    )
    return {"customer": cust["id"], "project": proj["id"], "attachment": att}


def zip_files(blob: bytes) -> dict[str, bytes]:
    with zipfile.ZipFile(io.BytesIO(blob)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def build_zip(files: dict[str, bytes], path: Path, manifest_patch: dict | None = None, rehash: bool = True) -> Path:
    """Write an archive from ``files``; fixes the manifest hashes unless ``rehash`` is False."""
    from hashlib import sha256

    files = dict(files)
    man = json.loads(files.get("manifest.json", b"{}") or b"{}")
    if rehash and man:
        man["files"] = {
            n: {"bytes": len(b), "sha256": sha256(b).hexdigest()} for n, b in files.items() if n != "manifest.json"
        }
    if manifest_patch:
        man.update(manifest_patch)
    if man:
        files["manifest.json"] = json.dumps(man).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for n, b in files.items():
            zf.writestr(n, b)
    return path


def export_archive(client, **body) -> bytes:
    r = client.post("/api/data/export", json=body)
    assert r.status_code == 200, r.text
    return r.content
