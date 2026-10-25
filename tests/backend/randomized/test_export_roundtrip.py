"""Property test: export -> import into a fresh instance -> export again gives semantically identical archives.

Generated data covers unknown extra fields (nested), unicode, characters that are special in CSV, strings that look
like spreadsheet formulas, zero records and many small attachments. One fixed test adds a 24 MiB attachment."""

import json
import sqlite3
import zipfile
from datetime import date
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from permita38.core.repository.sqlite import DB_FILENAME, SQLiteRepository
from permita38.core.service.numbering import NumberingService
from permita38.core.service.transfer import manifest
from permita38.core.service.transfer.export import ExportOptions
from permita38.core.service.transfer.service import TransferService

pytestmark = pytest.mark.randomized

TEXT = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    max_size=30,
) | st.sampled_from(
    ["=1+1", "+x", "-5", "@SUM(A1)", "\tTab", "a;b", 'q"uote', "line\nbreak", "Köln", "😀", "مرحبا", "é"]
)
JSON_LEAF = (
    st.none() | st.booleans() | st.integers(-(2**53), 2**53) | st.floats(allow_nan=False, allow_infinity=False) | TEXT
)
JSON_VALUE = st.recursive(
    JSON_LEAF, lambda c: st.lists(c, max_size=3) | st.dictionaries(TEXT, c, max_size=3), max_leaves=8
)
EXTRAS = st.dictionaries(st.from_regex(r"x_[a-z]{1,8}", fullmatch=True), JSON_VALUE, max_size=3)
FLOAT = st.floats(min_value=0, max_value=1e6, allow_nan=False)


def with_extras(base: dict, extras: dict) -> dict:
    return {**extras, **base}


HASH = "pbkdf2_sha256$1000$" + "ab" * 16 + "$" + "cd" * 32
LOCAL_HASH = "pbkdf2_sha256$1000$" + "12" * 16 + "$" + "34" * 32
#: collections filled generically (a record with a name and unknown extra fields); the rest has dedicated code
SPECIAL = ("users", "number_sequences", "invoices", "settings")
GENERIC = [c for c in manifest.exported_collections() if c not in SPECIAL]


@st.composite
def datasets(draw) -> dict:
    """Records with unknown extra fields in EVERY exported collection, at least three attachments, a counter."""
    data: dict = {"records": {}, "invoices": [], "users": [], "attachments": []}
    for coll in GENERIC:
        data["records"][coll] = [
            with_extras({"name": draw(TEXT)}, draw(EXTRAS)) for _ in range(draw(st.integers(0, 2)))
        ]
    for i in range(draw(st.integers(0, 3))):
        data["invoices"].append(
            with_extras(
                {
                    "number": f"RE-2026-{i + 1:04d}",
                    "date": "2026-03-01",
                    "lines": [{"description": draw(TEXT), "quantity": 1}],
                },
                draw(EXTRAS),
            )
        )
    for i in range(draw(st.integers(0, 2))):
        role = draw(st.sampled_from(["boss", "employee", "accountant"]))
        data["users"].append(
            with_extras(
                {"username": f"user{i}", "name": draw(TEXT), "role": role, "password_hash": HASH, "pw_changed": 1},
                draw(EXTRAS),
            )
        )
    data["attachments"] = draw(st.lists(st.binary(max_size=2000), min_size=3, max_size=5))
    data["counter"] = draw(st.integers(0, 50))
    data["sequence_extras"] = draw(EXTRAS)
    data["settings"] = draw(st.dictionaries(st.from_regex(r"s_[a-z]{1,6}", fullmatch=True), JSON_VALUE, max_size=3))
    return data


def boss_row(store, hash_value: str) -> dict:
    return store.save(
        "users", {"username": "boss", "name": "Boss", "role": "boss", "password_hash": hash_value, "pw_changed": 1}
    )


def populate(store, data: dict) -> None:
    for coll, recs in data["records"].items():
        for rec in recs:
            if coll == "allocations":  # exactly one owner: the instance's first user (no dangling reference)
                owner = {
                    "user_id": store.list("users")[0]["id"],
                    "project_id": store.save("projects", {"name": "p"})["id"],
                }
                rec = {**rec, **owner, "start": "2026-03-02", "end": "2026-03-27", "hours_per_week": 20.0}
            store.save(coll, rec)
    for u in data["users"]:
        store.save("users", u)
    names = [store.save_attachment("f.bin", blob) for blob in data["attachments"]]
    for i, name in enumerate(names):
        store.save("expenses", {"date": "2026-02-01", "net": 1.5, "vendor": "=cmd|' /C calc'!A0", "attachment": name})
    for inv in data["invoices"]:
        store.save("invoices", inv)
    if data["counter"]:
        sid = NumberingService(store)._row_id("invoices", "2026")
        store.save(
            "number_sequences",
            {
                "id": sid,
                "doc_type": "invoices",
                "period": "2026",
                "last_value": data["counter"],
                **data["sequence_extras"],
            },
        )
    store.save_settings({"company_name": "Demo", **data["settings"]})


def read_archive(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def canonical(files: dict[str, bytes]) -> dict:
    """The archive as comparable data: parsed JSON with sorted records, attachment bytes, CSV; no volatile fields."""
    out: dict = {}
    for name, blob in files.items():
        if name.startswith("data/") and name != manifest.SETTINGS_FILE:
            body = json.loads(blob)
            recs = sorted(body["records"], key=lambda r: r["id"])
            if name == "data/users.json":
                recs = [{k: v for k, v in r.items() if k not in ("password_hash", "pw_changed")} for r in recs]
            out[name] = recs
        elif name == manifest.SETTINGS_FILE:
            out[name] = json.loads(blob)
        elif name.startswith("attachments/") or name.startswith("csv/"):
            out[name] = blob if not name.startswith("csv/users") else None
        elif name == "manifest.json":
            m = json.loads(blob)
            out[name] = {
                k: m[k] for k in ("counts", "numbering", "schema_head", "scope", "include_attachments", "excluded")
            }
    return out


def assert_same_rows(src, dst) -> None:
    """The repository rows themselves (not the archive) are equal, including created/updated and unknown fields."""

    def rows(store, coll):
        recs = sorted(store.list(coll), key=lambda r: r["id"])
        if coll == "users":  # the setup account keeps its local password
            skip = ("password_hash", "pw_changed")
            recs = [{k: v for k, v in r.items() if k not in skip and not k.startswith("totp")} for r in recs]
        if coll == "calendar_sources":  # link URLs are never exported
            recs = [{**r, "url": ""} for r in recs]
        return recs

    for coll in manifest.exported_collections():
        assert rows(src, coll) == rows(dst, coll), coll
    assert dst.list_attachments() == src.list_attachments()


def roundtrip(base: Path, data: dict, tag: str) -> None:
    src = SQLiteRepository(base / f"src-{tag}")
    src.startup()
    boss_row(src, HASH)
    populate(src, data)
    dst = SQLiteRepository(base / f"dst-{tag}")
    dst.startup()
    boss_row(dst, LOCAL_HASH)
    ts, td = TransferService(src, base / f"src-{tag}" / "tmp"), TransferService(dst, base / f"dst-{tag}" / "tmp")
    a = ts.export("u", ExportOptions(include_accounts=True))
    try:
        result = td.run("u", a.path, today=date(2026, 4, 1))
        assert result["imported"] is True
        assert not [w for w in result["warnings"] if w["code"] == "dangling_reference"]
        b = td.export("u", ExportOptions(include_accounts=True))
        try:
            assert canonical(read_archive(a.path)) == canonical(read_archive(b.path))
            # the setup-user rule is the only allowed difference: the local password hash is kept
            assert next(u for u in dst.list("users") if u["username"] == "boss")["password_hash"] == LOCAL_HASH
            assert next(u for u in src.list("users") if u["username"] == "boss")["password_hash"] == HASH
            clock = lambda: date(2026, 4, 1)  # noqa: E731
            assert NumberingService(src, clock).preview("invoice", 2) == NumberingService(dst, clock).preview(
                "invoice", 2
            )
            assert result["numbering"]["invoices"]["next"] == NumberingService(src, clock).preview("invoice", 1)[0]
            assert_same_rows(src, dst)
        finally:
            b.path.unlink()
    finally:
        a.path.unlink()
    # independent oracle: the raw tables of the new database hold the same number of rows as the source database
    raw_src = sqlite3.connect(base / f"src-{tag}" / DB_FILENAME)
    raw_dst = sqlite3.connect(base / f"dst-{tag}" / DB_FILENAME)
    for table in manifest.exported_collections():
        q = f'SELECT COUNT(*) FROM "{table}"'
        assert raw_src.execute(q).fetchone() == raw_dst.execute(q).fetchone(), table
    raw_src.close()
    raw_dst.close()
    src.close()
    dst.close()


_counter = {"n": 0}


@settings(max_examples=25, deadline=None, suppress_health_check=list(HealthCheck))
@given(data=datasets())
def test_export_import_export_is_identical(data, tmp_path_factory):
    _counter["n"] += 1
    roundtrip(tmp_path_factory.mktemp("rt"), data, str(_counter["n"]))


def _empty() -> dict:
    return {
        "records": {},
        "invoices": [],
        "users": [],
        "attachments": [],
        "counter": 0,
        "sequence_extras": {},
        "settings": {},
    }


def test_zero_records_everywhere(tmp_path):
    roundtrip(tmp_path, _empty(), "zero")


def test_a_24_mib_attachment_survives(tmp_path):
    big = (b"permitA38" * 1000 + bytes(range(256))) * 2600
    data = _empty()
    data["attachments"] = [big[: 24 * 1024 * 1024]]
    data["counter"] = 1
    roundtrip(tmp_path, data, "big")
