"""Safe serving and upload of attachments (docs/specs/itemised-expenses.md, "Serving attachments safely").

Decisions where the spec is silent (the implementation must follow them):
* the magic-number check decides on the *file extension*: .pdf -> ``%PDF-``, .png, .jpg/.jpeg, .webp (RIFF....WEBP),
  .gif (GIF8). Mismatch -> 415. The declared Content-Type of the multipart part is ignored (a valid PDF sent as
  text/html is accepted; HTML sent as application/pdf is not).
* any other extension (.html, .svg, .txt, .json, none) is still accepted on upload (the bytes are not checked) but is
  only ever served as ``application/octet-stream`` with ``Content-Disposition: attachment``.
* too large (> 15 MB) -> 413 as today; exactly 15 MB is accepted.
* attachment responses carry ``X-Frame-Options: SAMEORIGIN`` (framing by the same origin only; the CSP given in the
  spec has no ``frame-ancestors``).
* inline types are served with no ``Content-Disposition`` header or one starting with ``inline``.
"""

import pytest
from fastapi.testclient import TestClient

from permita38.api.server import get_app
from permita38.core.config import Config, default_web_dir

ATTACHMENT_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"
MAX = 15 * 1024 * 1024

PDF = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + b"\x00" * 20
JPEG = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 20
WEBP = b"RIFF\x1a\x00\x00\x00WEBPVP8 " + b"\x00" * 20
GIF = b"GIF89a\x01\x00\x01\x00" + b"\x00" * 20
HTML = b"<!doctype html><script>alert(document.domain)</script>"
SVG = b'<svg xmlns="http://www.w3.org/2000/svg" onload="alert(1)"></svg>'
EXE = b"MZ\x90\x00\x03\x00\x00\x00" + b"\x00" * 50


def ids(v):
    return None if isinstance(v, str) else "bytes"


def upload(c, name, blob, ctype="application/octet-stream"):
    return c.post("/api/attachments", files={"file": (name, blob, ctype)})


def stored(c, name, blob, ctype="application/octet-stream"):
    r = upload(c, name, blob, ctype)
    assert r.status_code == 200, r.text
    return r.json()["name"]


# ------------------------------------------------------------------ allow-list of served types
@pytest.mark.parametrize(
    "filename,blob,mime",
    [
        ("bill.pdf", PDF, "application/pdf"),
        ("photo.png", PNG, "image/png"),
        ("photo.jpg", JPEG, "image/jpeg"),
        ("photo.jpeg", JPEG, "image/jpeg"),
        ("photo.webp", WEBP, "image/webp"),
        ("photo.gif", GIF, "image/gif"),
        ("BILL.PDF", PDF, "application/pdf"),
    ],
    ids=ids,
)
def test_allow_listed_types_are_served_inline_with_their_type(boss_client, filename, blob, mime):
    r = boss_client.get(f"/api/attachments/{stored(boss_client, filename, blob)}")
    assert r.status_code == 200 and r.content == blob
    assert r.headers["content-type"].split(";")[0] == mime
    assert not r.headers.get("content-disposition", "inline").lower().startswith("attachment")


@pytest.mark.parametrize(
    "filename,blob",
    [
        ("page.html", HTML),
        ("page.htm", HTML),
        ("image.svg", SVG),
        ("data.json", b'{"a": 1}'),
        ("notes.txt", b"hello"),
        ("script.js", b"alert(1)"),
        ("feed.xml", b"<a/>"),
        ("noextension", b"hello"),
        ("archive.zip", b"PK\x03\x04"),
    ],
    ids=ids,
)
def test_everything_else_is_served_as_a_download(boss_client, filename, blob):
    r = boss_client.get(f"/api/attachments/{stored(boss_client, filename, blob, 'text/html')}")
    assert r.status_code == 200 and r.content == blob
    assert r.headers["content-type"].split(";")[0] == "application/octet-stream"
    assert r.headers["content-disposition"].lower().startswith("attachment")


def test_legacy_html_already_on_disk_is_not_executable_either(boss_client, config):
    folder = config.data_dir / "attachments"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "legacy01.html").write_bytes(HTML)
    (folder / "legacy02.svg").write_bytes(SVG)
    for name in ("legacy01.html", "legacy02.svg"):
        r = boss_client.get(f"/api/attachments/{name}")
        assert r.status_code == 200
        assert r.headers["content-type"].split(";")[0] == "application/octet-stream"
        assert r.headers["content-disposition"].lower().startswith("attachment")


# ------------------------------------------------------------------ headers
@pytest.mark.parametrize(
    "filename,blob",
    [("a.pdf", PDF), ("a.png", PNG), ("a.html", HTML), ("a.svg", SVG)],
    ids=lambda v: None if isinstance(v, str) else "b",
)
def test_attachment_responses_carry_the_hardening_headers(boss_client, filename, blob):
    r = boss_client.get(f"/api/attachments/{stored(boss_client, filename, blob)}")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["content-security-policy"] == ATTACHMENT_CSP
    assert "sandbox" not in r.headers["content-security-policy"]  # a sandbox would disable the PDF viewer
    assert r.headers["x-frame-options"].upper() == "SAMEORIGIN"


def test_other_api_responses_keep_having_no_csp(boss_client):
    r = boss_client.get("/api/expenses")
    assert "content-security-policy" not in r.headers and r.headers["x-content-type-options"] == "nosniff"


# ------------------------------------------------------------------ upload: magic numbers
@pytest.mark.parametrize(
    "filename,blob,ctype",
    [
        ("invoice.pdf", HTML, "application/pdf"),  # HTML disguised as a PDF
        ("invoice.pdf", SVG, "application/pdf"),
        ("invoice.pdf", EXE, "application/pdf"),
        ("invoice.pdf", EXE, "text/html"),
        ("invoice.pdf", PNG, "application/pdf"),  # a real image, wrong extension
        ("photo.png", HTML, "image/png"),
        ("photo.png", JPEG, "image/png"),
        ("photo.jpg", PNG, "image/jpeg"),
        ("photo.jpeg", GIF, "image/jpeg"),
        ("photo.webp", PNG, "image/webp"),
        ("photo.webp", b"RIFF\x1a\x00\x00\x00WAVEfmt " + b"\x00" * 20, "image/webp"),  # RIFF but not WEBP
        ("photo.gif", HTML, "image/gif"),
        ("empty.pdf", b"", "application/pdf"),
        ("short.png", b"\x89PNG", "image/png"),
    ],
    ids=ids,
)
def test_upload_with_wrong_magic_number_is_415_and_stores_nothing(boss_client, config, filename, blob, ctype):
    r = upload(boss_client, filename, blob, ctype)
    assert r.status_code == 415, (r.status_code, r.text[:200])
    folder = config.data_dir / "attachments"
    assert not folder.exists() or [p for p in folder.iterdir() if p.suffix != ".tmp"] == []


def test_the_declared_content_type_does_not_matter_only_extension_and_bytes(boss_client):
    name = stored(boss_client, "scan.pdf", PDF, "text/html")
    r = boss_client.get(f"/api/attachments/{name}")
    assert r.headers["content-type"].split(";")[0] == "application/pdf"


def test_html_and_svg_uploads_are_accepted_but_never_run(boss_client):
    for filename, blob in (("x.html", HTML), ("x.svg", SVG)):
        name = stored(boss_client, filename, blob, "text/html")
        r = boss_client.get(f"/api/attachments/{name}")
        assert r.headers["content-type"].startswith("application/octet-stream")
        assert r.headers["content-disposition"].lower().startswith("attachment")


# ------------------------------------------------------------------ size and names
def test_size_limit_is_15_mb(boss_client):
    ok = upload(boss_client, "big.pdf", PDF + b"0" * (MAX - len(PDF)), "application/pdf")
    assert ok.status_code == 200, ok.text[:200]
    too_big = upload(boss_client, "big.pdf", PDF + b"0" * (MAX - len(PDF) + 1), "application/pdf")
    assert too_big.status_code == 413


@pytest.mark.parametrize(
    "filename", ["../../evil.pdf", "..\\..\\evil.pdf", "/etc/passwd.pdf", "a/b/c.pdf", "x\x00.pdf"]
)
def test_hostile_upload_names_never_reach_the_stored_name_or_leave_the_folder(boss_client, config, filename):
    r = upload(boss_client, filename, PDF, "application/pdf")
    if r.status_code != 200:  # rejecting the name is fine as well
        assert r.status_code in (400, 415, 422)
        return
    name = r.json()["name"]
    assert "/" not in name and "\\" not in name and ".." not in name
    assert boss_client.get(f"/api/attachments/{name}").content == PDF
    folder = config.data_dir / "attachments"
    assert {p.name for p in folder.iterdir()} == {name}
    assert not (config.data_dir.parent / "evil.pdf").exists() and not (config.data_dir / "evil.pdf").exists()


@pytest.mark.parametrize(
    "name",
    ["..%2F..%2Fsecret.pdf", "%2e%2e/config.yaml", "..", "nosuch12.pdf", "a%2Fb.pdf", "%00.pdf", "..%5Csecret.pdf"],
)
def test_path_traversal_and_unknown_names_are_404(boss_client, config, name):
    (config.data_dir / "secret.pdf").write_bytes(PDF)
    r = boss_client.get(f"/api/attachments/{name}")
    assert r.status_code in (404, 405) and r.content != PDF


def test_attachments_need_a_login(client, boss_client):
    name = stored(boss_client, "a.pdf", PDF)
    assert client.get(f"/api/attachments/{name}").status_code in (401, 403)
    assert client.post("/api/attachments", files={"file": ("a.pdf", PDF, "application/pdf")}).status_code in (401, 403)


# ------------------------------------------------------------------ CSP of the web pages (blob: preview)
def csp_directives(value: str) -> dict[str, set[str]]:
    out = {}
    for part in value.split(";"):
        tokens = part.split()
        if tokens:
            out[tokens[0]] = set(tokens[1:])
    return out


def test_page_csp_allows_blob_images_and_frames_and_nothing_else_changed(tmp_path):
    web = default_web_dir()
    app = get_app(Config(data_dir=tmp_path / "d", secret_key="test-secret-key", web_dir=web))
    with TestClient(app) as c:
        r = c.get("/")
    assert r.status_code == 200
    assert csp_directives(r.headers["content-security-policy"]) == {
        "default-src": {"'self'"},
        "style-src": {"'self'", "'unsafe-inline'"},
        "img-src": {"'self'", "data:", "blob:"},
        "object-src": {"'none'"},
        "frame-ancestors": {"'none'"},
        "frame-src": {"'self'", "blob:"},
    }
    assert "script-src" not in r.headers["content-security-policy"]  # scripts stay on default-src 'self'


# ------------------------------------------------------------------ anchored names, upper-case extensions
def test_a_stored_name_with_a_trailing_newline_is_404(boss_client, config):
    name = stored(boss_client, "a.pdf", PDF, "application/pdf")
    assert boss_client.get(f"/api/attachments/{name}").status_code == 200
    assert boss_client.get(f"/api/attachments/{name}%0A").status_code == 404
    assert boss_client.get(f"/api/attachments/{name}%0D%0A").status_code == 404


def test_upper_case_extension_with_html_bytes_is_415_and_nothing_is_stored(boss_client, config):
    r = upload(boss_client, "x.PDF", HTML, "application/pdf")
    assert r.status_code == 415
    folder = config.data_dir / "attachments"
    assert not folder.exists() or [p for p in folder.iterdir() if p.suffix != ".tmp"] == []


@pytest.mark.parametrize(
    "filename,blob,mime",
    [("x.Png", PNG, "image/png"), ("x.JPG", JPEG, "image/jpeg"), ("x.PDF", PDF, "application/pdf")],
)
def test_upper_case_extensions_with_correct_magic_numbers_are_accepted_and_served_inline(
    boss_client, filename, blob, mime
):
    name = stored(boss_client, filename, blob, mime)
    assert name.endswith(name.rsplit(".", 1)[1].lower()) and name == name.lower()
    r = boss_client.get(f"/api/attachments/{name}")
    assert r.status_code == 200 and r.content == blob
    assert r.headers["content-type"].split(";")[0] == mime


def test_download_filename_is_a_quoted_string(boss_client):
    name = stored(boss_client, "x.html", HTML, "text/html")
    assert (
        boss_client.get(f"/api/attachments/{name}").headers["content-disposition"] == f'attachment; filename="{name}"'
    )
