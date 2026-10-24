"""Browser tests of the data export and import page. The API is intercepted with the browser context's routes; the
fixtures are the examples of docs/contracts/data-export.md, so these tests run without the backend. Demo data only."""

import copy
import json
import mimetypes
import pathlib
from urllib.parse import urlparse

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

WEB = pathlib.Path(__file__).resolve().parents[2] / "web"
ORIGIN = "http://permita38.test"

COUNTS = {"customers": 12, "projects": 9, "entries": 3041, "invoices": 87, "expenses": 140, "users": 2}
ATTACHMENTS = {"files": 211, "bytes": 48213377}
INFO_FULL = {
    "counts": COUNTS,
    "attachments": ATTACHMENTS,
    "estimated_bytes": 61000000,
    "app_version": "0.4.0-alpha.2",
    "schema_head": "d8a69cde",
    "import": {"possible": False, "reason": "This instance already contains data (customers: 12, invoices: 87)."},
}
INFO_EMPTY = {
    **INFO_FULL,
    "counts": {k: 0 for k in COUNTS},
    "attachments": {"files": 0, "bytes": 0},
    "import": {"possible": True, "reason": None},
}
USERS = {"replaces_setup_user": "anna", "kept_setup_user": None, "without_password": ["lena", "tom"]}
NUMBERING = {
    "invoices": {"highest_imported": "RE-2026-0087", "counter_period": "2026", "counter": 87, "next": "RE-2026-0088"},
    "offers": {"highest_imported": "AN-2026-0031", "counter_period": "2026", "counter": 31, "next": "AN-2026-0032"},
}
WARNINGS = [
    {
        "code": "dangling_reference",
        "message": "entries[41].project_id points to a missing project (a1b2c3d4).",
        "file": "data/entries.json",
    },
    {
        "code": "calendar_url_removed",
        "message": "Calendar links are not exported; reconnect 2 calendar sources after the import.",
        "file": None,
    },
]
PREVIEW_OK = {
    "ok": True,
    "manifest": {
        "format": "permita38-export",
        "format_version": 1,
        "scope": "full",
        "app_version": "0.4.0-alpha.2",
        "schema_head": "d8a69cde",
        "created": "2026-10-24T09:30:15Z",
        "created_epoch": 1792834215,
        "include_accounts": False,
        "include_attachments": True,
    },
    "compatibility": {"status": "same", "message": None},
    "counts": COUNTS,
    "attachments": ATTACHMENTS,
    "users": USERS,
    "numbering": NUMBERING,
    "warnings": WARNINGS,
    "errors": [],
}
NEWER = "The archive was made by a newer permitA38 (schema 7ecb5a30). Update first."
PREVIEW_BAD = {
    "ok": False,
    "manifest": None,
    "compatibility": {"status": "unknown", "message": NEWER},
    "counts": {},
    "attachments": {"files": 0, "bytes": 0},
    "users": None,
    "numbering": None,
    "warnings": [],
    "errors": [{"code": "schema_unknown", "message": NEWER, "file": "manifest.json"}],
}
RUN_OK = {
    "imported": True,
    "counts": COUNTS,
    "attachments": ATTACHMENTS,
    "relogin": True,
    "users": USERS,
    "numbering": NUMBERING,
    "warnings": [],
}
TRANSFERS = [
    {
        "at": 1792834215,
        "user_id": "u1a2b3c4d5",
        "action": "export",
        "ok": True,
        "include_accounts": False,
        "counts": {"customers": 12},
        "archive_sha256": "9f2c",
    },
    {
        "at": 1792830000,
        "user_id": "u1a2b3c4d5",
        "action": "import_preview",
        "ok": False,
        "include_accounts": None,
        "counts": {},
        "archive_sha256": None,
    },
]
BOSS = {
    "id": "u1a2b3c4d5",
    "name": "Demo Boss",
    "role": "boss",
    "permissions": {a: {"read": "all", "write": "all"} for a in ("settings", "customers", "projects", "trips")},
}
EMPLOYEE = {
    "id": "u2b3c4d5e6",
    "name": "Demo Employee",
    "role": "employee",
    "permissions": {"trips": {"read": "limited", "write": "limited"}},
}
ZIP_BYTES = b"PK\x05\x06" + b"\x00" * 18
ZIP_HEADERS = {
    "Content-Type": "application/zip",
    "Content-Disposition": 'attachment; filename="permita38-export-20261024-093015.zip"',
}
REVOKE_SPY = (
    "window.__revoked = 0; const _rv = URL.revokeObjectURL.bind(URL);"
    " URL.revokeObjectURL = u => { window.__revoked++; return _rv(u); };"
)
# every File called big.zip reports 513 MiB, so the browser-side size check can be tested without a real file
BIG_FILE_SPY = (
    "const _size = Object.getOwnPropertyDescriptor(Blob.prototype, 'size').get;"
    " Object.defineProperty(File.prototype, 'size', { configurable: true, get() {"
    " return this.name === 'big.zip' ? 513 * 1024 * 1024 : _size.call(this); } });"
)


def _json(route, status, payload, headers=None):
    return route.fulfill(
        status=status, content_type="application/json", body=json.dumps(payload), headers=headers or None
    )


def _static(route):
    path = urlparse(route.request.url).path
    name = "index.html" if path in ("", "/") else path.lstrip("/")
    f = WEB / name
    if not f.is_file():
        return route.fulfill(status=404, body="not found")
    return route.fulfill(
        status=200, content_type=mimetypes.guess_type(f.name)[0] or "application/octet-stream", body=f.read_bytes()
    )


class FakeExportApi:
    """The routes of docs/contracts/data-export.md; every answer can be replaced by the test."""

    def __init__(self, me=BOSS, info=INFO_FULL):
        self.me = me
        self.info = copy.deepcopy(info)
        self.requests: list[tuple[str, str, dict | None, bytes]] = []
        self.export_answer = None  # (status, payload) instead of the ZIP
        self.preview_answer = (200, PREVIEW_OK)
        self.run_answer = (200, RUN_OK)
        self.slow_export = False  # the export waits until release_export() is called
        self.export_headers = None  # extra response headers for an export error, e.g. Retry-After
        self.held = None

    def release_export(self):
        route, self.held = self.held, None
        route.fulfill(status=200, body=ZIP_BYTES, headers=ZIP_HEADERS)

    def calls(self, method, path):
        return [r for r in self.requests if r[0] == method and r[1] == path]

    def handle(self, route):
        req = route.request
        path = urlparse(req.url).path.split("/api/", 1)[1]
        raw = req.post_data_buffer or b""
        is_json = "application/json" in (req.headers.get("content-type") or "")
        self.requests.append((req.method, path, json.loads(raw) if raw and is_json else None, raw))
        if path == "bootstrap":
            boot = {"me": self.me, "settings": {}, "timer": None, "states": {}, "conflicts": []}
            return _json(route, 200, {**boot, "templates": [], "data_dir": "", "features": {}})
        if path == "data/export/info":
            return _json(route, 200, self.info)
        if path == "data/transfers":
            return _json(route, 200, TRANSFERS)
        if path == "data/export":
            if self.export_answer:
                return _json(route, *self.export_answer, headers=self.export_headers)
            if self.slow_export:
                self.held = route
                return None
            return route.fulfill(status=200, body=ZIP_BYTES, headers=ZIP_HEADERS)
        if path == "data/import/preview":
            return _json(route, *self.preview_answer)
        if path == "data/import/run":
            return _json(route, *self.run_answer)
        if path in ("auth/logout",):
            return _json(route, 200, {})
        if req.method == "GET":
            return _json(route, 200, [])
        return _json(route, 404, {"detail": "Not found"})


def open_app(browser, api, page="dataexport", init=""):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900}, accept_downloads=True)
    ctx.add_init_script("localStorage.setItem('permita38.token', 'test-token')")
    if init:
        ctx.add_init_script(init)
    ctx.route("**/*", _static)
    ctx.route("**/api/**", api.handle)
    pg = ctx.new_page()
    problems: list[str] = []
    pg.on("pageerror", lambda e: problems.append("pageerror: " + str(e)))
    pg.on("dialog", lambda d: d.accept())
    pg.goto(f"{ORIGIN}/#/{page}")
    pg.wait_for_selector("nav.side a.nav")
    return ctx, pg, problems


def upload(pg, name="export.zip"):
    pg.set_input_files("#dx_file", {"name": name, "mimeType": "application/zip", "buffer": ZIP_BYTES})


def test_export_info_notice_and_transfers(browser):
    api = FakeExportApi()
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("h1")).to_have_text("Data export and import")
    expect(pg.locator("#dx_export")).to_contain_text("3041")
    expect(pg.locator("#dx_export")).to_contain_text("211 attachment file(s)")
    expect(pg.locator("#dx_export")).to_contain_text("What is never exported")
    expect(pg.locator("main")).to_contain_text("It is not a legally compliant archive (GoBD)")
    expect(pg.locator("main")).to_contain_text("Two-factor secrets and API tokens are never included.")
    expect(pg.locator("#dx_export")).not_to_contain_text("This archive is large")
    expect(pg.locator("#dx_transfers")).to_contain_text("Export")
    expect(pg.locator("#dx_transfers")).to_contain_text("Import check")
    expect(pg.locator("#dx_transfers")).to_contain_text("Failed")
    assert problems == []
    ctx.close()


def test_full_download_sends_the_options_and_saves_the_zip(browser):
    api = FakeExportApi()
    ctx, pg, problems = open_app(browser, api)
    expect(pg.get_by_label("Include attachments (invoice PDFs, receipts)")).to_be_checked()
    expect(pg.get_by_label("Include user accounts and password hashes")).not_to_be_checked()
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Download full archive").click()
    assert dl.value.suggested_filename == "permita38-export-20261024-093015.zip"
    body = api.calls("POST", "data/export")[0][2]
    assert body == {"scope": "full", "include_accounts": False, "include_attachments": True}
    expect(pg.locator("#dx_progress")).to_contain_text("Saved permita38-export-20261024-093015.zip")
    expect(pg.get_by_role("button", name="Download full archive")).to_be_enabled()
    pg.get_by_label("Include attachments (invoice PDFs, receipts)").uncheck()
    pg.get_by_label("Include user accounts and password hashes").check()
    with pg.expect_download():
        pg.get_by_role("button", name="Download full archive").click()
    body = api.calls("POST", "data/export")[1][2]
    assert body == {"scope": "full", "include_accounts": True, "include_attachments": False}
    assert problems == []
    ctx.close()


def test_master_data_download(browser):
    api = FakeExportApi()
    ctx, pg, problems = open_app(browser, api)
    with pg.expect_download():
        pg.get_by_role("button", name="Download customers and suppliers only").click()
    assert api.calls("POST", "data/export")[0][2] == {"scope": "master_data"}
    assert problems == []
    ctx.close()


def test_large_archive_warns_and_points_to_the_cli(browser):
    api = FakeExportApi(info={**INFO_FULL, "estimated_bytes": 600_000_000})
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("#dx_export [role=alert]")).to_contain_text("permita38 export")
    assert problems == []
    ctx.close()


@pytest.mark.parametrize(
    ("status", "detail", "shown"),
    [
        (409, "Another export or import is running.", "Another export or import is running."),
        (413, "The export would be larger than the limit (2 GiB uncompressed).", "larger than the limit"),
        (403, "Forbidden", "Only the boss may export or import data"),
        (429, "Too many requests", "Too many requests. Wait a minute"),
    ],
)
def test_export_errors_are_shown_and_the_page_stays_usable(browser, status, detail, shown):
    api = FakeExportApi()
    api.export_answer = (status, {"detail": detail})
    ctx, pg, problems = open_app(browser, api)
    pg.get_by_role("button", name="Download full archive").click()
    expect(pg.locator("#dx_export [role=alert]")).to_contain_text(shown)
    expect(pg.get_by_role("button", name="Download full archive")).to_be_enabled()
    assert problems == []
    ctx.close()


def test_instance_with_data_disables_the_import(browser):
    api = FakeExportApi()
    ctx, pg, problems = open_app(browser, api)
    expect(pg.locator("#dx_import [role=alert]")).to_contain_text("This instance already contains data")
    expect(pg.locator("#dx_import [role=alert]")).to_contain_text("Import works only on an empty instance")
    expect(pg.locator("#dx_file")).to_be_disabled()
    expect(pg.get_by_role("button", name="Check file")).to_be_disabled()
    assert api.calls("POST", "data/import/preview") == []
    assert problems == []
    ctx.close()


def test_preview_then_confirmed_run_and_relogin(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    ctx, pg, problems = open_app(browser, api)
    expect(pg.get_by_role("button", name="Check file")).to_be_disabled()
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    panel = pg.locator("#dx_preview")
    expect(panel).to_contain_text("The archive can be imported")
    expect(panel).to_contain_text("permitA38 0.4.0-alpha.2")
    expect(panel).to_contain_text("Same version")
    expect(panel.locator("table.dx-counts")).to_contain_text("3041")
    expect(panel).to_contain_text("211 attachment file(s)")
    expect(panel).to_contain_text("next number will be RE-2026-0088")
    expect(panel.get_by_role("link", name="numbering page")).to_have_attribute("href", "#/numbering")
    expect(panel).to_contain_text("anna")
    expect(panel).to_contain_text("lena, tom")
    expect(panel).to_contain_text("entries[41].project_id points to a missing project")
    # the upload is multipart with the file part only; nothing was imported yet
    assert b'name="file"' in api.calls("POST", "data/import/preview")[0][3]
    assert api.calls("POST", "data/import/run") == []
    run = pg.get_by_role("button", name="Import now")
    expect(run).to_be_disabled()
    pg.get_by_label("I understand that this instance will be filled").check()
    expect(run).to_be_enabled()
    run.click()
    expect(pg.locator("#modal-root")).to_contain_text("Import now?")
    pg.locator("#dx-cancel").click()
    assert api.calls("POST", "data/import/run") == []
    run.click()
    pg.locator("#dx-go").click()
    result = pg.locator("#dx_import [role=status]").first
    expect(result).to_contain_text("The archive was imported")
    expect(result).to_contain_text("RE-2026-0088")
    expect(result).to_contain_text("lena, tom")
    raw = api.calls("POST", "data/import/run")[0][3]
    assert b'name="confirm"' in raw
    assert b"true" in raw
    pg.get_by_role("button", name="Log in again").click()
    expect(pg.locator("form, .auth-card").first).to_be_visible()
    assert pg.evaluate("localStorage.getItem('permita38.token')") is None
    assert problems == []
    ctx.close()


def test_many_warnings_are_collapsed(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    many = [{"code": "dangling_reference", "message": f"warning number {i}", "file": None} for i in range(8)]
    api.preview_answer = (200, {**PREVIEW_OK, "warnings": many})
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    details = pg.locator("#dx_preview details.dx-warn")
    expect(details).to_contain_text("Warnings (8)")
    assert details.get_attribute("open") is None
    assert problems == []
    ctx.close()


def test_unacceptable_archive_shows_errors_and_blocks_import(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    api.preview_answer = (200, PREVIEW_BAD)
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    expect(pg.locator("#dx_preview")).to_contain_text("The archive cannot be imported")
    expect(pg.locator("#dx_preview [role=alert]")).to_contain_text("made by a newer permitA38")
    expect(pg.locator("#dx_preview")).to_contain_text("Unknown version")
    expect(pg.get_by_role("button", name="Import now")).to_be_disabled()
    expect(pg.get_by_label("I understand that this instance will be filled")).to_be_disabled()
    assert problems == []
    ctx.close()


def test_master_data_archive_cannot_be_imported(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    manifest = {**PREVIEW_OK["manifest"], "scope": "master_data"}
    api.preview_answer = (200, {**PREVIEW_OK, "manifest": manifest})
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    expect(pg.locator("#dx_preview")).to_contain_text("customers and suppliers only")
    expect(pg.get_by_label("I understand that this instance will be filled")).to_be_disabled()
    expect(pg.get_by_role("button", name="Import now")).to_be_disabled()
    assert problems == []
    ctx.close()


@pytest.mark.parametrize(
    ("status", "payload", "shown"),
    [
        (413, {"detail": "The file is larger than 512 MiB."}, "larger than 512 MiB"),
        (409, INFO_FULL["import"] and {"detail": INFO_FULL["import"]["reason"]}, "already contains data"),
        (429, {"detail": "Too many requests"}, "Too many requests. Wait a minute"),
        (403, {"detail": "Forbidden"}, "Only the boss may"),
    ],
)
def test_preview_errors(browser, status, payload, shown):
    api = FakeExportApi(info=INFO_EMPTY)
    api.preview_answer = (status, payload)
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    expect(pg.locator("#dx_import [role=alert]")).to_contain_text(shown)
    expect(pg.get_by_role("button", name="Check file")).to_be_enabled()
    assert problems == []
    ctx.close()


def test_run_refused_with_errors_422(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    err = {"code": "record_invalid", "message": "customers[3] is invalid: name is required.", "file": "data/customers.json"}
    api.run_answer = (422, {"detail": "The archive was not imported: 1 errors.", "errors": [err]})
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    pg.get_by_label("I understand that this instance will be filled").check()
    pg.get_by_role("button", name="Import now").click()
    pg.locator("#dx-go").click()
    expect(pg.locator("#dx_import [role=alert]").first).to_contain_text("The archive was not imported")
    expect(pg.locator("#dx_import")).to_contain_text("customers[3] is invalid")
    expect(pg.get_by_role("button", name="Import now")).to_be_disabled()  # nothing was imported; the page stays usable
    expect(pg.get_by_role("button", name="Check file")).to_be_enabled()
    assert problems == []
    ctx.close()


def test_run_409_instance_not_empty(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    reason = "This instance already contains data (customers: 1)."
    api.run_answer = (409, {"detail": reason})
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    pg.get_by_label("I understand that this instance will be filled").check()
    pg.get_by_role("button", name="Import now").click()
    pg.locator("#dx-go").click()
    expect(pg.locator("#dx_import [role=alert]").first).to_contain_text(reason)
    expect(pg.locator("#dx_file")).to_be_disabled()
    assert problems == []
    ctx.close()


def test_import_without_relogin_has_no_login_button(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    api.run_answer = (200, {**RUN_OK, "relogin": False})
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    pg.get_by_label("I understand that this instance will be filled").check()
    pg.get_by_role("button", name="Import now").click()
    pg.locator("#dx-go").click()
    expect(pg.locator("#dx_import")).to_contain_text("The archive was imported")
    expect(pg.get_by_role("button", name="Log in again")).to_have_count(0)
    assert problems == []
    ctx.close()


def test_boss_reaches_it_from_settings_and_keeps_nothing_in_storage(browser):
    api = FakeExportApi()
    ctx, pg, problems = open_app(browser, api, page="settings")
    pg.get_by_role("link", name="Data export and import").click()
    expect(pg.locator("h1")).to_have_text("Data export and import")
    with pg.expect_download():
        pg.get_by_role("button", name="Download full archive").click()
    keys = pg.evaluate("Object.keys(localStorage).concat(Object.keys(sessionStorage))")
    assert keys == ["permita38.token"]
    assert problems == []
    ctx.close()


def test_employee_has_no_access(browser):
    api = FakeExportApi(me=EMPLOYEE)
    ctx, pg, problems = open_app(browser, api, page="trips")
    expect(pg.locator("nav.side")).not_to_contain_text("Data export")
    pg.goto(f"{ORIGIN}/#/dataexport")
    pg.wait_for_selector("#toast.on")
    assert "dataexport" not in pg.url
    assert api.calls("GET", "data/export/info") == []
    assert problems == []
    ctx.close()


def test_download_revokes_the_object_url_right_after_the_click(browser):
    api = FakeExportApi()
    ctx, pg, problems = open_app(browser, api, init=REVOKE_SPY)
    with pg.expect_download() as dl:
        pg.get_by_role("button", name="Download full archive").click()
    assert dl.value.failure() is None
    pg.wait_for_function("window.__revoked === 1", timeout=5000)
    assert problems == []
    ctx.close()


def test_rate_limit_says_when_to_retry(browser):
    api = FakeExportApi()
    api.export_answer = (429, {"detail": "Too many requests"})
    api.export_headers = {"Retry-After": "30"}
    ctx, pg, problems = open_app(browser, api)
    pg.get_by_role("button", name="Download full archive").click()
    expect(pg.locator("#dx_export [role=alert]")).to_contain_text("Too many requests. Try again in 30 seconds.")
    assert problems == []
    ctx.close()


def test_archive_strings_are_shown_as_text_not_markup(browser):
    evil = "<img src=x onerror=window.__x=1>"
    api = FakeExportApi(info=INFO_EMPTY)
    api.preview_answer = (
        200,
        {
            **PREVIEW_OK,
            "manifest": {**PREVIEW_OK["manifest"], "app_version": evil},
            "users": {**USERS, "without_password": [evil]},
            "counts": {evil: 1},
            "warnings": [{"code": "x", "message": evil, "file": None}],
        },
    )
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    panel = pg.locator("#dx_preview")
    expect(panel).to_contain_text("permitA38 " + evil)
    # record keys show their underscores as spaces; the markup stays text
    expect(panel.locator("table.dx-counts")).to_contain_text("<img src=x onerror=window.")
    expect(panel).to_contain_text(f"Without a password (set one with permita38 set-password): {evil}")
    expect(panel).to_contain_text("Warnings (1)")
    assert panel.locator("img").count() == 0
    assert pg.evaluate("typeof window.__x") == "undefined"
    assert problems == []
    ctx.close()


def test_file_over_512_mib_is_refused_in_the_browser(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    ctx, pg, problems = open_app(browser, api, init=BIG_FILE_SPY)
    upload(pg, name="big.zip")
    pg.get_by_role("button", name="Check file").click()
    expect(pg.locator("#dx_import [role=alert]")).to_contain_text("The file is larger than 512 MiB.")
    assert api.calls("POST", "data/import/preview") == []
    assert problems == []
    ctx.close()


def test_focus_stays_on_the_control_the_user_used(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    expect(pg.locator("#dx_preview")).to_contain_text("The archive can be imported")
    expect(pg.get_by_role("button", name="Check file")).to_be_focused()
    pg.get_by_label("I understand that this instance will be filled").check()
    run = pg.get_by_role("button", name="Import now")
    run.click()
    expect(pg.locator("#dx-cancel")).to_be_focused()
    pg.locator("#dx-cancel").click()
    expect(run).to_be_focused()
    assert problems == []
    ctx.close()


def test_import_is_locked_while_an_export_runs(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    api.slow_export = True
    ctx, pg, problems = open_app(browser, api)
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    expect(pg.locator("#dx_preview")).to_contain_text("The archive can be imported")
    pg.get_by_label("I understand that this instance will be filled").check()
    expect(pg.get_by_role("button", name="Import now")).to_be_enabled()
    pg.get_by_role("button", name="Download full archive").click()
    expect(pg.locator("#dx_progress")).to_contain_text("Building the archive")
    expect(pg.locator("#dx_file")).to_be_disabled()
    expect(pg.get_by_role("button", name="Check file")).to_be_disabled()
    expect(pg.get_by_label("I understand that this instance will be filled")).to_be_disabled()
    expect(pg.get_by_role("button", name="Import now")).to_be_disabled()
    with pg.expect_download():
        api.release_export()
    expect(pg.locator("#dx_file")).to_be_enabled()
    expect(pg.get_by_role("button", name="Import now")).to_be_enabled()
    assert problems == []
    ctx.close()


def test_nothing_of_the_archive_stays_in_the_browser_after_preview_and_run(browser):
    api = FakeExportApi(info=INFO_EMPTY)
    ctx, pg, problems = open_app(browser, api)
    with pg.expect_download():
        pg.get_by_role("button", name="Download full archive").click()
    expect(pg.locator("#dx_progress")).to_contain_text("Saved permita38-export")
    upload(pg)
    pg.get_by_role("button", name="Check file").click()
    expect(pg.locator("#dx_preview")).to_contain_text("The archive can be imported")
    keys = pg.evaluate("Object.keys(localStorage).concat(Object.keys(sessionStorage))")
    assert keys == ["permita38.token"]
    pg.get_by_label("I understand that this instance will be filled").check()
    pg.get_by_role("button", name="Import now").click()
    pg.locator("#dx-go").click()
    expect(pg.locator("#dx_import")).to_contain_text("The archive was imported")
    keys = pg.evaluate("Object.keys(localStorage).concat(Object.keys(sessionStorage))")
    assert keys == ["permita38.token"]
    assert pg.evaluate("indexedDB.databases().then(d => d.map(x => x.name))") == []
    assert problems == []
    ctx.close()
