"""Audit of request bodies: every JSON body the routers accept runs the hard limits (NUL, lone surrogate, length,
types). A lone surrogate or NUL in any string field must be a clean 422, never a 500 and never a stored value.

The table lists each body with a valid example and the paths of its string fields (plus the fields that used to be
unchecked plain strings). Adding a body means adding a row here."""

import copy
import json

import pytest

from tests.backend.conftest import PASSWORD

SURROGATE = "\ud83d"
NUL = "a\x00b"
LONG = "x" * 5001
ID = "abcdef12"

# (method, url, valid body, string field paths)
BODIES = [
    (
        "POST",
        "/api/timer/start",
        {"project_id": None, "task_id": None, "note": "n"},
        [("project_id",), ("task_id",), ("note",)],
    ),
    ("POST", "/api/timer/stop", {"note": "n"}, [("note",)]),
    ("POST", "/api/customers/lookup-impressum", {"url": "https://example.com"}, [("url",)]),
    (
        "POST",
        "/api/auth/setup",
        {"username": "admin2", "name": "Ada", "password": PASSWORD, "company_name": "Demo"},
        [("username",), ("name",), ("password",), ("company_name",)],
    ),
    ("POST", "/api/auth/login", {"username": "boss", "password": PASSWORD}, [("username",), ("password",)]),
    (
        "POST",
        "/api/auth/change-password",
        {"old_password": PASSWORD, "new_password": PASSWORD},
        [("old_password",), ("new_password",)],
    ),
    (
        "POST",
        "/api/auth/login/totp",
        {"mfa_token": "t", "code": "123456", "recovery_code": None},
        [("mfa_token",), ("code",), ("recovery_code",)],
    ),
    ("POST", "/api/auth/totp/setup", {"password": PASSWORD}, [("password",)]),
    ("POST", "/api/auth/totp/enable", {"code": "123456"}, [("code",)]),
    (
        "POST",
        "/api/auth/totp/disable",
        {"password": PASSWORD, "code": None, "recovery_code": None},
        [("password",), ("code",), ("recovery_code",)],
    ),
    (
        "POST",
        "/api/auth/totp/recovery-codes/regenerate",
        {"password": PASSWORD, "code": "123456"},
        [("password",), ("code",)],
    ),
    ("POST", f"/api/users/{ID}/totp/reset", {"password": PASSWORD}, [("password",)]),
    (
        "POST",
        "/api/users",
        {
            "username": "zed",
            "name": "Zed",
            "password": PASSWORD,
            "role": "employee",
            "color": None,
            "timezone": None,
            "work_start": None,
            "work_end": None,
            "tracking_start": None,
        },
        [
            ("username",),
            ("name",),
            ("password",),
            ("role",),
            ("color",),
            ("timezone",),
            ("work_start",),
            ("work_end",),
            ("tracking_start",),
        ],
    ),
    (
        "POST",
        "/api/plan/simulate",
        {"changes": {"absences": [], "commitments": []}, "weeks": 4, "first": None, "user_id": None},
        [("first",), ("user_id",)],
    ),
    (
        "POST",
        "/api/plan/what-must-move",
        {"plan": {"start": "2026-01-01", "end": "2026-12-31", "user_ids": [ID], "rate": None}},
        [("plan", "start"), ("plan", "end"), ("plan", "user_ids", 0)],
    ),
    (
        "POST",
        "/api/plan/suggest-vacation",
        {"user_id": ID, "days": 3, "window_from": "2026-01-01", "window_to": "2026-02-01"},
        [("user_id",), ("window_from",), ("window_to",)],
    ),
    (
        "POST",
        "/api/plan/suggest-start",
        {"hours": 10, "deadline": "2026-12-31", "earliest": None, "user_ids": [ID]},
        [("deadline",), ("earliest",), ("user_ids", 0)],
    ),
    (
        "POST",
        "/api/plan/apply",
        {
            "changes": {
                "absences": [
                    {"id": None, "user_id": ID, "kind": "vacation", "start": "2026-01-02", "end": "2026-01-03"}
                ],
                "commitments": [],
            },
            "confirm": False,
        },
        [
            ("changes", "absences", 0, "id"),
            ("changes", "absences", 0, "user_id"),
            ("changes", "absences", 0, "kind"),
            ("changes", "absences", 0, "start"),
            ("changes", "absences", 0, "end"),
        ],
    ),
    ("POST", "/api/plan/scenarios", {"name": "Plan A", "changes": {"absences": [], "commitments": []}}, [("name",)]),
    ("POST", f"/api/projects/{ID}/extend", {"new_end": "2026-12-31", "reason": "r"}, [("new_end",), ("reason",)]),
    (
        "POST",
        "/api/numbering/check",
        {"type": "invoice", "pattern": "RE-{year}-{n}", "reset": "yearly", "next": None},
        [("type",), ("pattern",), ("reset",)],
    ),
    (
        "PUT",
        "/api/numbering",
        {"type": "invoice", "pattern": "RE-{n}", "reset": None, "next": None, "reason": "r", "confirm": False},
        [("type",), ("pattern",), ("reset",), ("reason",)],
    ),
    (
        "POST",
        "/api/invoices/from-time",
        {"customer_id": None, "project_id": None, "from": "2026-01-01", "to": None},
        [("customer_id",), ("project_id",), ("from",), ("to",)],
    ),
    (
        "POST",
        "/api/calendars",
        {"name": "Kalender", "url": "https://example.com/a.ics", "include_keywords": ["x"], "exclude_keywords": ["y"]},
        [("name",), ("url",), ("include_keywords", 0), ("exclude_keywords", 0)],
    ),
    (
        "PUT",
        f"/api/calendars/{ID}",
        {"name": "Kalender", "include_keywords": ["x"], "exclude_keywords": ["y"]},
        [("name",), ("include_keywords", 0), ("exclude_keywords", 0)],
    ),
]


def _with(body, path, value):
    body = copy.deepcopy(body)
    node = body
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return body


def _send(client, method, url, body):
    # json.dumps escapes a lone surrogate as \ud83d; the httpx json= argument cannot encode it
    return client.request(method, url, content=json.dumps(body).encode(), headers={"Content-Type": "application/json"})


_CASES = [
    pytest.param(method, url, body, field, id=f"{method} {url}:{'.'.join(map(str, field))}")
    for method, url, body, fields in BODIES
    for field in fields
]


@pytest.mark.parametrize("method,url,body,field", _CASES)
def test_lone_surrogate_in_every_string_field_is_422(boss_client, method, url, body, field):
    r = _send(boss_client, method, url, _with(body, field, SURROGATE))
    assert r.status_code == 422, (url, field, r.status_code, r.text[:300])
    assert "\\ud83d" not in r.text and SURROGATE not in r.text  # the input is not echoed back


@pytest.mark.parametrize("method,url,body,field", _CASES)
def test_nul_in_every_string_field_is_422(boss_client, method, url, body, field):
    r = _send(boss_client, method, url, _with(body, field, NUL))
    assert r.status_code == 422, (url, field, r.status_code, r.text[:300])


@pytest.mark.parametrize("method,url,body,field", _CASES)
def test_over_long_text_is_422(boss_client, method, url, body, field):
    r = _send(boss_client, method, url, _with(body, field, LONG))
    assert r.status_code == 422, (url, field, r.status_code, r.text[:300])


@pytest.mark.parametrize("method,url,body,field", _CASES)
def test_wrong_type_is_422(boss_client, method, url, body, field):
    r = _send(boss_client, method, url, _with(body, field, ["not", "a", "string"]))
    assert r.status_code == 422, (url, field, r.status_code, r.text[:300])


def test_the_surrogate_is_refused_before_anything_is_stored(boss_client):
    r = _send(boss_client, "POST", "/api/users", _with(BODIES[12][2], ("name",), SURROGATE))
    assert r.status_code == 422
    assert [u["username"] for u in boss_client.get("/api/users").json()] == ["boss"]


# valid bodies: 200. The last two need data that this test does not create (a known user, unbilled time), so for them
# the test only shows that validation passed: any status but 422 (a business answer such as 404 or 400 is fine)
GOOD = [
    ("POST", "/api/timer/start", {"project_id": None, "task_id": None, "note": ""}, 200),
    ("POST", "/api/auth/login", {"username": "boss", "password": PASSWORD}, 200),
    ("POST", "/api/auth/totp/setup", {"password": PASSWORD}, 200),
    ("POST", "/api/users", {"username": "zed", "name": "Zed Zeller", "password": PASSWORD, "role": "employee"}, 200),
    ("POST", "/api/plan/simulate", {"changes": {"absences": [], "commitments": []}, "weeks": 4}, 200),
    ("POST", "/api/numbering/check", {"type": "invoice", "pattern": "RE-{year}-{n}"}, 200),
    (
        "POST",
        "/api/plan/suggest-vacation",
        {"user_id": ID, "days": 3, "window_from": "2026-01-01", "window_to": "2026-02-01"},
        None,
    ),
    ("POST", "/api/invoices/from-time", {"from": "2026-01-01", "to": "2026-12-31"}, None),
]


@pytest.mark.parametrize("method,url,body,expected", GOOD)
def test_valid_bodies_are_still_accepted(boss_client, method, url, body, expected):
    r = _send(boss_client, method, url, body)
    if expected is None:
        assert r.status_code not in (422, 500), (url, r.status_code, r.text[:300])
    else:
        assert r.status_code == expected, (url, r.status_code, r.text[:300])


# ------------------------------------------------------------------ multipart form fields (no JSON model)
ICS = b"BEGIN:VCALENDAR\r\nEND:VCALENDAR\r\n"
FORM_ROUTES = [
    (
        "/api/calendars/upload",
        {"name": "Kalender", "all_day_blocks": "false", "busy_default": "true"},
        {"file": ("a.ics", ICS, "text/calendar")},
    ),
    ("/api/import/preview", {"type": "customers", "sheet": "Blatt1"}, {"file": ("a.csv", b"name\nAcme\n", "text/csv")}),
    ("/api/import/run", {"type": "customers", "sheet": "Blatt1"}, {"file": ("a.csv", b"name\nAcme\n", "text/csv")}),
]
FORM_FIELDS = [
    ("/api/calendars/upload", "name"),
    ("/api/calendars/upload", "all_day_blocks"),
    ("/api/calendars/upload", "busy_default"),
    ("/api/calendars/upload", "include_keywords"),
    ("/api/calendars/upload", "exclude_keywords"),
    ("/api/import/preview", "sheet"),
    ("/api/import/preview", "mapping"),
    ("/api/import/run", "sheet"),
    ("/api/import/run", "mapping"),
]


def _form_base(url):
    return next(d for u, d, _f in FORM_ROUTES if u == url), next(f for u, _d, f in FORM_ROUTES if u == url)


@pytest.mark.parametrize("url,field", FORM_FIELDS)
def test_nul_in_a_form_field_is_422(boss_client, url, field):
    data, files = _form_base(url)
    data = {**data, field: NUL}
    r = boss_client.post(url, data=data, files=files)
    assert r.status_code == 422, (url, field, r.status_code, r.text[:300])


@pytest.mark.parametrize("url,field", FORM_FIELDS)
def test_lone_surrogate_bytes_in_a_form_field_are_never_a_500(boss_client, url, field):
    """A lone surrogate cannot be sent as UTF-8 at all. Its raw bytes (\\xed\\xa0\\xbd) reach the form parser, which
    decodes them without error (latin-1 fallback), so the request is either refused or stored as text. The only
    requirement here is that the server never answers 500."""
    data, files = _form_base(url)
    parts = [
        b'--XX\r\nContent-Disposition: form-data; name="'
        + k.encode()
        + b'"\r\n\r\n'
        + (b"\xed\xa0\xbd" if k == field else v.encode())
        + b"\r\n"
        for k, v in data.items()
    ]
    name, content, ctype = files["file"]
    parts.append(
        b'--XX\r\nContent-Disposition: form-data; name="file"; filename="' + name.encode() + b'"\r\n'
        b"Content-Type: " + ctype.encode() + b"\r\n\r\n" + content + b"\r\n--XX--\r\n"
    )
    r = boss_client.post(url, content=b"".join(parts), headers={"Content-Type": "multipart/form-data; boundary=XX"})
    assert r.status_code < 500, (url, field, r.status_code, r.text[:300])
