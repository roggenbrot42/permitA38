#!/usr/bin/env python3
"""Smoke test for a built permitA38 binary or any command line that starts the server.

    python packaging/smoke_test.py dist/permita38/permita38[.exe]

Starts the app on a free port with a throw-away data folder and config (the real user config is never touched),
then checks: health, public status, the bundled web frontend, first-run setup + login, a database write, and PDF
rendering (which needs the bundled templates and the migrations). Stdlib only, so it runs against any build.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

TIMEOUT = 60


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def call(base: str, method: str, path: str, body=None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=15) as r:
        raw = r.read()
        return r.status, r.headers.get("Content-Type", ""), raw


def expect(cond: bool, what: str) -> None:
    if not cond:
        raise AssertionError(what)
    print("  ok:", what)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    command = argv[1:]
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="p38-smoke-") as tmp:
        tmp_path = Path(tmp)
        env = dict(os.environ, HOME=tmp, USERPROFILE=tmp, APPDATA=tmp, XDG_CONFIG_HOME=tmp, PYTHONUTF8="1")
        env.pop("PERMITA38_DATA", None)
        log = open(tmp_path / "server.log", "w+", encoding="utf-8")
        proc = subprocess.Popen(
            [*command, "-c", str(tmp_path / "config.yaml"), "--data", str(tmp_path / "data"), "--host", "127.0.0.1",
             "--port", str(port), "--no-browser", "serve"],
            stdout=log, stderr=subprocess.STDOUT, env=env,
        )  # fmt: skip
        try:
            deadline = time.time() + TIMEOUT
            while True:
                if proc.poll() is not None:
                    raise AssertionError(f"server exited early with code {proc.returncode}")
                try:
                    status, _, raw = call(base, "GET", "/api/health")
                    if status == 200:
                        break
                except (urllib.error.URLError, ConnectionError, OSError):
                    pass
                if time.time() > deadline:
                    raise AssertionError(f"server did not become healthy within {TIMEOUT}s")
                time.sleep(0.5)
            print(f"server is up on {base}")
            expect(json.loads(raw) == {"status": "ok"}, "GET /api/health")

            _, _, raw = call(base, "GET", "/api/auth/status")
            st = json.loads(raw)
            expect(st["setup_needed"] is True and st["app"] == "permitA38", "GET /api/auth/status (fresh install)")
            expect("data_dir" not in st, "public status does not leak the data folder")

            status, ctype, raw = call(base, "GET", "/")
            expect(status == 200 and "text/html" in ctype and b"permitA38" in raw, "bundled web frontend is served")
            status, _, raw = call(base, "GET", "/api.js")
            expect(status == 200 and len(raw) > 100, "frontend scripts are bundled")

            pw = "smoke-test-password-1"
            call(base, "POST", "/api/auth/setup", {"username": "smoke", "name": "Smoke Test", "password": pw})
            _, _, raw = call(base, "POST", "/api/auth/login", {"username": "smoke", "password": pw})
            token = json.loads(raw)["token"]
            expect(bool(token), "first-run setup and login")

            _, _, raw = call(base, "POST", "/api/customers", {"name": "ACME GmbH"}, token)
            cust = json.loads(raw)
            _, _, raw = call(
                base, "POST", "/api/invoices",
                {"customer_id": cust["id"], "lines": [{"description": "Consulting", "qty": 2, "unit": "h", "price": 100}]},
                token,
            )  # fmt: skip
            inv = json.loads(raw)
            _, _, raw = call(base, "POST", f"/api/invoices/{inv['id']}/finalize", None, token)
            expect(json.loads(raw)["number"].startswith("RE-"), "database write + invoice numbering")
            status, ctype, raw = call(base, "GET", f"/api/invoices/{inv['id']}/pdf", None, token)
            expect(status == 200 and raw.startswith(b"%PDF") and "pdf" in ctype, "invoice PDF is rendered")
        except Exception:
            print("\nSMOKE TEST FAILED", file=sys.stderr)
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            log.seek(0)
            tail = log.read()[-3000:]
            log.close()
            if "Traceback" in tail:
                print("--- server log (tail) ---\n" + tail, file=sys.stderr)
    print("SMOKE TEST PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
