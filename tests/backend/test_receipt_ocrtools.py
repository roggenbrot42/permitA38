# ruff: noqa: F811  (fixtures imported from test_receipts_api are used as arguments)
"""WP H: pinned download, safe unpack, install and route for the Windows OCR package (fake demo data, local server).

The real package does not exist yet. The CI step on windows-latest that downloads it and reads the fake PNG is left out
until the constants in ``core/receipts/ocr_tools.py`` are filled in (see ``.github/workflows/ocr-tools.yaml``)."""

import hashlib
import io
import threading
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from permita38.core import netsafe
from permita38.core.errors import ServiceError
from permita38.core.netsafe import FetchError, SafeFetcher, check_ip
from permita38.core.receipts import ocr, ocr_tools

from .test_api_tokens import bearer, new_token
from .test_receipts_api import c, people, reader, services  # noqa: F401  (fixtures)

HOST = "dl.example"


def make_zip(files: dict[str, bytes] | None = None) -> bytes:
    files = {"tesseract.exe": b"MZ fake", "tessdata/deu.traineddata": b"d", "tessdata/eng.traineddata": b"e"} | (
        files or {}
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Site:
    def __init__(self):
        self.routes: dict = {}
        site = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_GET(self):
                status, headers, body = site.routes.get(self.path, (404, {}, b"no"))
                self.send_response(status)
                for k, v in headers.items():
                    self.send_header(k, v)
                if "Content-Length" not in headers and "no-length" not in headers:
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def url(self, path="/pkg.zip", host=HOST):
        return f"http://{host}:{self.port}{path}"


@pytest.fixture
def site():
    s = Site()
    yield s
    s.server.shutdown()
    s.server.server_close()


def loopback_ok(ip):
    if str(ip) != "127.0.0.1":
        check_ip(ip)


def fetcher(site, mapping=None):
    mapping = mapping or {}
    return SafeFetcher(
        "test",
        resolver=lambda host, port: [mapping.get(host, "127.0.0.1")],
        address_policy=loopback_ok,
        allowed_ports=(site.port,),
    )


def download(site, url, digest, limit, dest, **kw):
    f = fetcher(site, kw.pop("mapping", None))
    return f.download_pinned(url, digest, limit, dest, allowed_hosts=kw.pop("hosts", (HOST,)), https_only=False)


# ------------------------------------------------------------------ download_pinned
def test_download_ok_streams_to_file(site, tmp_path):
    body = make_zip()
    site.routes["/pkg.zip"] = (200, {"Content-Type": "application/octet-stream"}, body)
    dest = tmp_path / "p.zip"
    assert download(site, site.url(), sha(body), len(body), dest) == len(body)
    assert dest.read_bytes() == body


def test_download_follows_a_redirect_inside_the_allow_list(site, tmp_path):
    body = b"payload"
    site.routes["/a"] = (302, {"Location": site.url("/b", "cdn.example")}, b"")
    site.routes["/b"] = (200, {}, body)
    dest = tmp_path / "p.bin"
    download(site, site.url("/a"), sha(body), 100, dest, hosts=(HOST, "cdn.example"))
    assert dest.read_bytes() == body


def test_wrong_digest_leaves_nothing(site, tmp_path):
    site.routes["/pkg.zip"] = (200, {}, b"payload")
    dest = tmp_path / "p.zip"
    with pytest.raises(FetchError) as e:
        download(site, site.url(), sha(b"other"), 100, dest)
    assert e.value.code == "digest_mismatch" and not dest.exists()


def test_invalid_digest_is_refused(site, tmp_path):
    with pytest.raises(FetchError) as e:
        download(site, site.url(), "abc", 100, tmp_path / "p")
    assert e.value.code == "invalid_digest"


def test_oversize_by_header_and_without_length(site, tmp_path):
    body = b"x" * 50
    site.routes["/pkg.zip"] = (200, {}, body)
    with pytest.raises(FetchError) as e:
        download(site, site.url(), sha(body), 10, tmp_path / "a")
    assert e.value.code == "too_large" and not (tmp_path / "a").exists()
    site.routes["/nolen"] = (200, {"no-length": "1", "Connection": "close"}, body)
    with pytest.raises(FetchError) as e:
        download(site, site.url("/nolen"), sha(body), 10, tmp_path / "b")
    assert e.value.code == "too_large" and not (tmp_path / "b").exists()


def test_redirect_to_a_private_address_is_refused(site, tmp_path):
    site.routes["/a"] = (302, {"Location": site.url("/b", "cdn.example")}, b"")
    site.routes["/b"] = (200, {}, b"secret")
    with pytest.raises(FetchError) as e:
        download(site, site.url("/a"), sha(b"secret"), 100, tmp_path / "p", hosts=(HOST, "cdn.example"),
                 mapping={"cdn.example": "10.0.0.5"})  # fmt: skip
    assert e.value.code == "blocked_address" and not (tmp_path / "p").exists()


def test_redirect_to_another_host_is_refused(site, tmp_path):
    site.routes["/a"] = (302, {"Location": site.url("/b", "evil.example")}, b"")
    site.routes["/b"] = (200, {}, b"x")
    with pytest.raises(FetchError) as e:
        download(site, site.url("/a"), sha(b"x"), 100, tmp_path / "p")
    assert e.value.code == "host_not_allowed"


def test_first_host_must_be_allowed_and_https_is_required_by_default(site, tmp_path):
    site.routes["/pkg.zip"] = (200, {}, b"x")
    with pytest.raises(FetchError) as e:
        download(site, site.url(host="other.example"), sha(b"x"), 100, tmp_path / "p")
    assert e.value.code == "host_not_allowed"
    with pytest.raises(FetchError) as e:
        fetcher(site).download_pinned(site.url(), sha(b"x"), 100, tmp_path / "p", allowed_hosts=(HOST,))
    assert e.value.code == "insecure_url"
    assert "github.com" in set(netsafe.GITHUB_RELEASE_HOSTS)  # an exact host, not a substring


def test_http_error_and_redirect_loop(site, tmp_path):
    with pytest.raises(FetchError) as e:
        download(site, site.url("/missing"), sha(b"x"), 100, tmp_path / "p")
    assert e.value.code == "http_error"
    site.routes["/l"] = (302, {"Location": "/l"}, b"")
    with pytest.raises(FetchError) as e:
        download(site, site.url("/l"), sha(b"x"), 100, tmp_path / "p")
    assert e.value.code == "redirect_loop"


# ------------------------------------------------------------------ unpack
def test_unpack_ok(tmp_path):
    z = tmp_path / "p.zip"
    z.write_bytes(make_zip())
    out = tmp_path / "out"
    out.mkdir()
    ocr_tools.unpack(z, out)
    assert (out / "tesseract.exe").is_file() and (out / "tessdata" / "deu.traineddata").is_file()


@pytest.mark.parametrize(
    "name", ["../evil.txt", "/abs.txt", "a/../../evil.txt", "C:/evil.txt", "a\\..\\evil.txt", "tessdata/../../x"]
)
def test_unpack_refuses_path_traversal(tmp_path, name):
    z = tmp_path / "p.zip"
    z.write_bytes(make_zip({name: b"x"}))
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError):
        ocr_tools.unpack(z, out)
    assert not (tmp_path / "evil.txt").exists()


def test_unpack_limits_and_missing_program(tmp_path, monkeypatch):
    z = tmp_path / "p.zip"
    z.write_bytes(make_zip())
    monkeypatch.setattr(ocr_tools, "MAX_FILES", 2)
    out = tmp_path / "o1"
    out.mkdir()
    with pytest.raises(ValueError, match="too many"):
        ocr_tools.unpack(z, out)
    monkeypatch.setattr(ocr_tools, "MAX_FILES", 10)
    monkeypatch.setattr(ocr_tools, "MAX_UNPACKED_BYTES", 5)
    out = tmp_path / "o2"
    out.mkdir()
    with pytest.raises(ValueError, match="large"):
        ocr_tools.unpack(z, out)
    monkeypatch.undo()
    z.write_bytes(make_zip({"tesseract.exe": b""}))  # present but program removed below
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("readme.txt", "x")
    z.write_bytes(buf.getvalue())
    out = tmp_path / "o3"
    out.mkdir()
    with pytest.raises(ValueError, match="no tesseract"):
        ocr_tools.unpack(z, out)


def test_unpack_refuses_links(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("tesseract.exe")
        info.external_attr = 0o120777 << 16
        zf.writestr(info, "/etc/passwd")
    z = tmp_path / "p.zip"
    z.write_bytes(buf.getvalue())
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(ValueError, match="link"):
        ocr_tools.unpack(z, out)


# ------------------------------------------------------------------ install
def pkg_for(site, body, version="5.4.0-1", **kw):
    return ocr_tools.Package(version, site.url(), kw.get("size", len(body)), kw.get("sha", sha(body)))


@pytest.fixture
def allow_site(monkeypatch):
    """Let ``install`` talk to the local server: the allow-list and https rule are relaxed for the test only."""
    orig = netsafe.download_pinned

    def patched(url, digest, limit, dest, *, fetcher=None, **kw):
        return fetcher.download_pinned(url, digest, limit, dest, allowed_hosts=(HOST,), https_only=False)

    monkeypatch.setattr(ocr_tools.netsafe, "download_pinned", patched)
    return orig


def test_install_ok_replaces_old_versions_and_stale_temp_folders(site, tmp_path, allow_site):
    body = make_zip()
    site.routes["/pkg.zip"] = (200, {}, body)
    root = tmp_path / "ocr"
    (root / "5.3.0-1").mkdir(parents=True)
    (root / "5.3.0-1" / "tesseract.exe").write_bytes(b"old")
    (root / ".tmp-dead").mkdir()
    exe = ocr_tools.install(pkg_for(site, body), root, fetcher=fetcher(site))
    assert exe == root / "5.4.0-1" / "tesseract.exe" and exe.is_file()
    assert sorted(p.name for p in root.iterdir()) == ["5.4.0-1"]
    assert ocr_tools.find_installed(root) == exe
    assert ocr_tools.tessdata_dir(exe, root) == root / "5.4.0-1" / "tessdata"
    assert ocr_tools.tessdata_dir(tmp_path / "elsewhere" / "tesseract.exe", root) is None


def test_install_bad_digest_and_bad_package_leave_nothing(site, tmp_path, allow_site):
    body = make_zip()
    site.routes["/pkg.zip"] = (200, {}, body)
    root = tmp_path / "ocr"
    with pytest.raises(ServiceError) as e:
        ocr_tools.install(pkg_for(site, body, sha="0" * 64), root, fetcher=fetcher(site))
    assert e.value.status == 502 and list(root.iterdir()) == []
    evil = make_zip({"../evil": b"x"})
    site.routes["/pkg.zip"] = (200, {}, evil)
    with pytest.raises(ServiceError) as e:
        ocr_tools.install(pkg_for(site, evil), root, fetcher=fetcher(site))
    assert e.value.status == 502 and list(root.iterdir()) == []


def test_an_interrupted_install_is_ignored(tmp_path):
    root = tmp_path / "ocr"
    (root / ".tmp-abc").mkdir(parents=True)
    (root / ".tmp-abc" / "tesseract.exe").write_bytes(b"half")
    assert ocr_tools.find_installed(root) is None
    assert ocr_tools.find_installed(tmp_path / "missing") is None


# ------------------------------------------------------------------ lookup order
def test_lookup_order_config_path_downloaded(tmp_path, monkeypatch):
    root = tmp_path / "ocr"
    (root / "5.4.0-1" / "tessdata").mkdir(parents=True)
    (root / "5.4.0-1" / "tesseract.exe").write_bytes(b"x")
    monkeypatch.setattr(ocr_tools, "default_root", lambda: root)
    monkeypatch.setattr(ocr.shutil, "which", lambda name: None)
    assert ocr.find_tesseract() == str(root / "5.4.0-1" / "tesseract.exe")
    assert ocr._tessdata_args(ocr.find_tesseract()) == ["--tessdata-dir", str(root / "5.4.0-1" / "tessdata")]
    monkeypatch.setattr(ocr.shutil, "which", lambda name: "/usr/bin/tesseract")
    assert ocr.find_tesseract() == "/usr/bin/tesseract"
    assert ocr._tessdata_args("/usr/bin/tesseract") == []
    mine = tmp_path / "mine"
    mine.write_bytes(b"x")
    assert ocr.find_tesseract(str(mine)) == str(mine)
    assert ocr.find_tesseract(str(tmp_path / "nope")) is None


# ------------------------------------------------------------------ build gate, capabilities and route
def test_not_offered_while_the_constants_are_empty(monkeypatch):
    for name in ("VERSION", "URL", "SIZE", "SHA256"):
        monkeypatch.setattr(ocr_tools, name, None)
    assert ocr_tools.package() is None and not ocr_tools.offered()
    monkeypatch.setattr(ocr_tools, "supported_build", lambda: True)
    assert not ocr_tools.offered()  # still no package


PKG = ocr_tools.Package("5.4.0-1", "https://github.com/example/repo/releases/download/x/p.zip", 1234567, "a" * 64)


@pytest.fixture
def offered(monkeypatch):
    monkeypatch.setattr(ocr, "available", lambda path, languages: None)  # whatever this machine has installed
    monkeypatch.setattr(ocr_tools, "package", lambda: PKG)
    monkeypatch.setattr(ocr_tools, "supported_build", lambda: True)


def test_capabilities_carry_size_and_host_only_when_available(c, services, offered):
    got = c.get("/api/receipts/capabilities").json()
    assert got["ocr_install"] == "available"
    assert (got["ocr_download_size"], got["ocr_download_host"]) == (1234567, "github.com")


def test_capabilities_none_on_other_builds(c, services):
    got = c.get("/api/receipts/capabilities").json()
    assert got["ocr_install"] == "none" and "ocr_download_host" not in got


def test_route_404_on_other_builds(c):
    assert c.post("/api/receipts/ocr/install").status_code == 404


def test_route_installs_and_then_reports_409(c, services, offered, monkeypatch):
    calls = []

    state = {"ocr": False}

    def fake_install(pkg, root=None, fetcher=None):
        calls.append(pkg)
        state["ocr"] = True

    monkeypatch.setattr(ocr_tools, "install", fake_install)
    monkeypatch.setattr(services.receipts, "_ocr_available", lambda: state["ocr"])
    r = c.post("/api/receipts/ocr/install")
    assert r.status_code == 200 and r.json() == {"ocr": True, "version": "5.4.0-1"}
    assert c.post("/api/receipts/ocr/install").status_code == 409
    assert c.get("/api/receipts/capabilities").json()["ocr_install"] == "none"


def test_route_download_failure_is_502(c, services, offered, monkeypatch):
    def failing(pkg, root=None, fetcher=None):
        raise ServiceError(502, "The OCR package could not be downloaded or verified (digest_mismatch).")

    monkeypatch.setattr(ocr_tools, "install", failing)
    assert c.post("/api/receipts/ocr/install").status_code == 502


def test_route_one_at_a_time(c, services, offered, monkeypatch):
    started, release = threading.Event(), threading.Event()

    def slow(pkg, root=None, fetcher=None):
        started.set()
        release.wait(5)

    monkeypatch.setattr(ocr_tools, "install", slow)
    results = []
    t = threading.Thread(target=lambda: results.append(c.post("/api/receipts/ocr/install").status_code))
    t.start()
    assert started.wait(5)
    assert c.post("/api/receipts/ocr/install").status_code == 409
    release.set()
    t.join(5)
    assert results == [200]


def test_route_rights_and_tokens(app, c, services, people, offered):
    assert people["accountant"].post("/api/receipts/ocr/install").status_code == 403
    assert people["employee"].post("/api/receipts/ocr/install").status_code == 403
    assert c.__class__(c.app).post("/api/receipts/ocr/install").status_code == 401
    rw = bearer(app, new_token(c, read_only=False)["token"])
    assert rw.post("/api/receipts/ocr/install").status_code == 403


def test_newest_version_is_chosen_numerically(tmp_path):
    for v in ("5.9.0", "5.10.0", "5.2.0"):
        (tmp_path / v / "tessdata").mkdir(parents=True)
        (tmp_path / v / ocr_tools.EXE_NAME).write_bytes(b"x")
    assert ocr_tools.find_installed(tmp_path) == tmp_path / "5.10.0" / ocr_tools.EXE_NAME


def test_pinned_constants_are_complete():
    pkg = ocr_tools.package()
    assert pkg is not None and pkg.version == "5.4.0.20240606-1" and pkg.size == 58705851
    assert pkg.url.endswith(f"/ocr-tools-win64-{pkg.version}/ocr-tools-win64-{pkg.version}.zip")
    assert len(pkg.sha256) == 64 and pkg.host == "github.com"
