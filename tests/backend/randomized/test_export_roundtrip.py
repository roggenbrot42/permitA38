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


@st.composite
def datasets(draw) -> dict:
    n = lambda: draw(st.integers(0, 3))  # noqa: E731
    data: dict = {"customers": [], "projects": [], "entries": [], "expenses": [], "invoices": [], "attachments": []}
    for _ in range(n()):
        data["customers"].append(
            with_extras({"name": draw(TEXT), "rate": draw(FLOAT), "notes": draw(TEXT)}, draw(EXTRAS))
        )
    for _ in range(n()):
        data["projects"].append(with_extras({"name": draw(TEXT), "member_ids": []}, draw(EXTRAS)))
    for _ in range(n()):
        data["entries"].append(
            with_extras({"date": "2026-02-03", "minutes": draw(st.integers(1, 600)), "note": draw(TEXT)}, draw(EXTRAS))
        )
    for _ in range(draw(st.integers(0, 4))):
        data["attachments"].append(draw(st.binary(max_size=2000)))
    for i in range(n()):
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
    for _ in range(n()):
        data["expenses"].append(
            with_extras({"date": "2026-02-01", "net": draw(FLOAT), "vendor": draw(TEXT)}, draw(EXTRAS))
        )
    data["counter"] = draw(st.integers(0, 50))
    data["settings"] = draw(st.dictionaries(st.from_regex(r"s_[a-z]{1,6}", fullmatch=True), JSON_VALUE, max_size=3))
    return data


def boss_row(store, hash_value: str) -> dict:
    return store.save(
        "users", {"username": "boss", "name": "Boss", "role": "boss", "password_hash": hash_value, "pw_changed": 1}
    )


def populate(store, data: dict) -> None:
    boss = store.list("users")[0]
    cust = [store.save("customers", c) for c in data["customers"]]
    projs = [
        store.save("projects", {**p, **({"customer_id": cust[0]["id"]} if cust else {})}) for p in data["projects"]
    ]
    for e in data["entries"]:
        store.save("entries", {**e, "user_id": boss["id"], **({"project_id": projs[0]["id"]} if projs else {})})
    names = [store.save_attachment("f.pdf", blob) for blob in data["attachments"]]
    for i, e in enumerate(data["expenses"]):
        store.save("expenses", {**e, **({"attachment": names[i % len(names)]} if names else {})})
    for inv in data["invoices"]:
        store.save("invoices", {**inv, **({"customer_id": cust[0]["id"]} if cust else {})})
    if data["counter"]:
        sid = NumberingService(store)._row_id("invoices", "2026")
        store.save(
            "number_sequences", {"id": sid, "doc_type": "invoices", "period": "2026", "last_value": data["counter"]}
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


def roundtrip(base: Path, data: dict, tag: str) -> None:
    src = SQLiteRepository(base / f"src-{tag}")
    src.startup()
    boss_row(src, "hash-of-source-boss")
    populate(src, data)
    dst = SQLiteRepository(base / f"dst-{tag}")
    dst.startup()
    boss_row(dst, "hash-of-local-boss")
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
            assert dst.list("users")[0]["password_hash"] == "hash-of-local-boss"
            assert src.list("users")[0]["password_hash"] == "hash-of-source-boss"
            clock = lambda: date(2026, 4, 1)  # noqa: E731
            assert NumberingService(src, clock).preview("invoice", 2) == NumberingService(dst, clock).preview(
                "invoice", 2
            )
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


def test_zero_records_everywhere(tmp_path):
    empty = {
        "customers": [],
        "projects": [],
        "entries": [],
        "expenses": [],
        "invoices": [],
        "attachments": [],
        "counter": 0,
        "settings": {},
    }
    roundtrip(tmp_path, empty, "zero")


def test_a_24_mib_attachment_survives(tmp_path):
    big = (b"permitA38" * 1000 + bytes(range(256))) * 2600
    data = {
        "customers": [],
        "projects": [],
        "entries": [],
        "expenses": [{"date": "2026-02-01", "net": 1.0}],
        "invoices": [],
        "attachments": [big[: 24 * 1024 * 1024]],
        "counter": 1,
        "settings": {},
    }
    roundtrip(tmp_path, data, "big")
