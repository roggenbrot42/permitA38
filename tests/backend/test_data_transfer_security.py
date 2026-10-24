"""Zip safety, resource limits, rights and the single-flight lock of the data export/import."""

import io
import json
import threading
import tracemalloc
import zipfile
from pathlib import Path

import pytest

from permita38.core.service.transfer import limits
from permita38.core.service.transfer.archive import ArchiveError, ArchiveReader
from permita38.core.service.transfer.service import _LOCK

from .conftest import PASSWORD, _login
from .transfer_helpers import build_zip, export_archive, make_instance, seed, zip_files


@pytest.fixture
def pair(tmp_path):
    src_app, src_client = make_instance(tmp_path, "src")
    seed(src_app.state.services.store)
    dst_app, dst_client = make_instance(tmp_path, "dst")
    return src_client, dst_app, dst_client, tmp_path


def _codes(r) -> list[str]:
    return [e["code"] for e in r.json()["errors"]]


def _run(client, path: Path):
    return client.post("/api/data/import/run", files={"file": ("a.zip", path.read_bytes())}, data={"confirm": "true"})


def _zip_with(path: Path, entries: list[tuple[str | zipfile.ZipInfo, bytes]], method=zipfile.ZIP_DEFLATED) -> Path:
    with zipfile.ZipFile(path, "w", method) as zf:
        for name, data in entries:
            zf.writestr(name, data)
    return path


# ------------------------------------------------------------------ zip-slip and friends
@pytest.mark.parametrize(
    "name",
    [
        "../x",
        "/abs/x",
        "a\\..\\b",
        "C:evil",
        "data/../manifest.json",
        "data/customers.json/../../x",
        "attachments/../../x.pdf",
        "attachments/sub/abcdef12.pdf",
        "data/unknown_table.json",
        "dir/",
        "data/",
        "Manifest.json",
        "data/customers.json.bak",
    ],
)
def test_unsafe_entry_names_reject_the_whole_archive(name, pair):
    src, _, dst, tmp = pair
    files = zip_files(export_archive(src))
    files[name] = b"x"
    path = build_zip(files, tmp / "z.zip", rehash=False)
    r = _run(dst, path)
    assert r.status_code == 422 and _codes(r) == ["unsafe_entry"]


def test_nul_in_a_name_is_not_a_way_in(pair):
    src, dst_app, dst, tmp = pair
    files = zip_files(export_archive(src))
    files["data/customers.json\x00.evil"] = b"x"  # zipfile cuts the name at the NUL: a duplicate entry
    path = build_zip(files, tmp / "z.zip", rehash=False)
    r = _run(dst, path)
    assert r.status_code == 422 and "unsafe_entry" in _codes(r)
    assert dst_app.state.services.store.list("customers") == []


def test_symlink_entry_is_rejected(pair):
    src, _, dst, tmp = pair
    files = zip_files(export_archive(src))
    path = build_zip(files, tmp / "z.zip")
    with zipfile.ZipFile(path, "a") as zf:
        info = zipfile.ZipInfo("attachments/abcdef12.png")
        info.external_attr = (0o120777) << 16
        zf.writestr(info, "../../etc/passwd")
    r = _run(dst, path)
    assert r.status_code == 422 and "unsafe_entry" in _codes(r)


def test_duplicate_and_casefold_duplicate_entries(pair):
    src, _, dst, tmp = pair
    files = zip_files(export_archive(src))
    path = build_zip(files, tmp / "z.zip")
    with zipfile.ZipFile(path, "a") as zf, pytest.warns(UserWarning):
        zf.writestr("README.txt", "again")
    assert _codes(_run(dst, path)) == ["unsafe_entry"]
    path2 = build_zip(files, tmp / "z2.zip")
    with zipfile.ZipFile(path2, "a") as zf:
        zf.writestr("attachments/ABCDEF12.PNG", "x")  # not even a valid attachment name
    assert _codes(_run(dst, path2)) == ["unsafe_entry"]
    path3 = build_zip({**files, "attachments/abcdef12.png": b"a"}, tmp / "z3.zip")
    with zipfile.ZipFile(path3, "a") as zf:
        zf.writestr("attachments/abcdef12.PNG", "b")
    assert _codes(_run(dst, path3)) == ["unsafe_entry"]


def test_encrypted_entry_and_unsupported_method_are_rejected(pair):
    src, _, dst, tmp = pair
    path = build_zip(zip_files(export_archive(src)), tmp / "z.zip")
    data = bytearray(path.read_bytes())
    # set the "encrypted" bit in the central directory header of the first entry
    cd = data.rfind(b"PK\x01\x02")
    data[cd + 8] |= 0x01
    path.write_bytes(bytes(data))
    r = _run(dst, path)
    assert r.status_code == 422 and "unsafe_entry" in _codes(r)
    path2 = build_zip(zip_files(export_archive(src)), tmp / "z2.zip")
    with zipfile.ZipFile(path2, "a", zipfile.ZIP_BZIP2) as zf:
        zf.writestr("attachments/abcdef12.png", "x")
    assert "unsafe_entry" in _codes(_run(dst, path2))


# ------------------------------------------------------------------ limits
def test_too_many_entries(pair):
    _, _, dst, tmp = pair
    path = tmp / "many.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for i in range(limits.MAX_ENTRIES + 1):
            zf.writestr(f"attachments/{i:08x}.png", b"")
    r = _run(dst, path)
    assert r.status_code == 422 and _codes(r) == ["limit_exceeded"]


def test_zeros_bomb_is_rejected_quickly_with_bounded_memory(pair):
    _, _, dst, tmp = pair
    path = tmp / "bomb.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        with zf.open("data/customers.json", "w", force_zip64=True) as f:
            zeros = bytes(limits.MIB)
            for _i in range(1024):
                f.write(zeros)
    assert path.stat().st_size < 5 * limits.MIB
    tracemalloc.start()
    try:
        r = _run(dst, path)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert r.status_code == 422 and _codes(r) == ["limit_exceeded"]
    assert peak < 40 * limits.MIB


def test_a_lying_declared_size_aborts_at_the_limit(tmp_path, monkeypatch):
    """The directory says "small", the data is large: the reader counts what it really reads."""
    path = tmp_path / "lie.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("attachments/abcdef12.png", b"y" * 2000)
    monkeypatch.setattr(limits, "MAX_ATTACHMENT", 1000)
    # the declared size is checked at open time ...
    with pytest.raises(ArchiveError) as e:
        ArchiveReader(path)
    assert e.value.code == "limit_exceeded"
    # ... and a directory that lies is caught while reading
    monkeypatch.setattr(limits, "MAX_ATTACHMENT", 5000)
    reader = ArchiveReader(path)
    reader.entries["attachments/abcdef12.png"].file_size = 10
    monkeypatch.setattr(limits, "MAX_ATTACHMENT", 1000)
    with pytest.raises(ArchiveError) as e2:
        reader.read("attachments/abcdef12.png")
    assert e2.value.code in {
        "limit_exceeded",
        "not_a_zip",
    }  # never reads past the declared data, and a CRC mismatch fails
    reader.close()


def test_json_depth_bomb(pair):
    src, _, dst, tmp = pair
    files = zip_files(export_archive(src))
    deep = b"[" * 200_000 + b"]" * 200_000
    files["data/customers.json"] = deep
    r = _run(dst, build_zip(files, tmp / "deep.zip"))
    assert r.status_code == 422 and _codes(r) == ["limit_exceeded"]
    nested = {"collection": "customers", "count": 1, "records": [{"id": "abcdef12", "name": "x", "x_deep": {}}]}
    cur = nested["records"][0]["x_deep"]
    for _i in range(40):
        cur["d"] = {}
        cur = cur["d"]
    files["data/customers.json"] = json.dumps(nested).encode()
    r2 = _run(dst, build_zip(files, tmp / "deep2.zip"))
    assert r2.status_code == 422 and _codes(r2) == ["limit_exceeded"]


def test_too_many_records_in_a_collection(pair, monkeypatch):
    src, _, dst, tmp = pair
    monkeypatch.setattr(limits, "MAX_RECORDS", 2)
    files = zip_files(export_archive(src))
    recs = [{"id": f"abcdef{i:02d}", "name": "n"} for i in range(3)]
    files["data/customers.json"] = json.dumps({"collection": "customers", "count": 3, "records": recs}).encode()
    r = _run(dst, build_zip(files, tmp / "n.zip"))
    assert r.status_code == 422 and "limit_exceeded" in _codes(r)


def test_upload_over_the_limit_is_413(pair, monkeypatch):
    _, _, dst, _ = pair
    monkeypatch.setattr(limits, "MAX_UPLOAD", 1000)
    r = dst.post("/api/data/import/preview", files={"file": ("a.zip", b"x" * 2000)})
    assert r.status_code == 413 and r.json()["detail"] == "The file is larger than 512 MiB."


def test_wrong_content_types_in_records(pair):
    src, _, dst, tmp = pair
    files = zip_files(export_archive(src))
    for body in ([], "text", 5, {"collection": "customers", "count": 0, "records": "no"}):
        files["data/customers.json"] = json.dumps(body).encode()
        r = _run(dst, build_zip(files, tmp / "t.zip"))
        assert r.status_code == 422 and _codes(r) == ["json_invalid"]
    files["data/customers.json"] = json.dumps({"collection": "customers", "count": 1, "records": ["x"]}).encode()
    assert _codes(_run(dst, build_zip(files, tmp / "t.zip"))) == ["record_invalid"]


def test_error_list_is_capped(pair):
    src, _, dst, tmp = pair
    files = zip_files(export_archive(src))
    recs = [{"id": "nope"} for _i in range(200)]
    files["data/customers.json"] = json.dumps({"collection": "customers", "count": 200, "records": recs}).encode()
    r = dst.post("/api/data/import/preview", files={"file": ("a.zip", build_zip(files, tmp / "e.zip").read_bytes())})
    assert len(r.json()["errors"]) <= limits.MAX_ERRORS


# ------------------------------------------------------------------ rights and HTTP shape
ROUTES = [
    ("GET", "/api/data/export/info"),
    ("POST", "/api/data/export"),
    ("POST", "/api/data/import/preview"),
    ("POST", "/api/data/import/run"),
    ("GET", "/api/data/transfers"),
]


@pytest.mark.parametrize("method,path", ROUTES)
def test_only_the_boss_may_use_the_routes(method, path, app, boss_client, member_client, client):
    kwargs = {"files": {"file": ("a.zip", b"x")}} if "import" in path else {}
    assert client.request(method, path, **kwargs).status_code == 401
    assert member_client.request(method, path, **kwargs).status_code == 403
    for role in ("employee", "accountant"):
        r = boss_client.post(
            "/api/users", json={"username": role[:3] + "x", "name": role, "role": role, "password": PASSWORD}
        )
        assert r.status_code == 200
        c = _login(app, role[:3] + "x")
        assert c.request(method, path, **kwargs).status_code == 403, (role, path)
    assert boss_client.request(method, path, **kwargs).status_code != 403


@pytest.mark.skip(reason="TODO(api-tokens): add the five routes to the session-only list and assert 403 for a token")
def test_routes_are_session_only_for_api_tokens():
    raise AssertionError("pending PR #16 (SessionOnly)")


def test_export_response_headers_and_body_validation(boss_client):
    r = boss_client.post("/api/data/export", json={})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
    assert int(r.headers["content-length"]) == len(r.content)
    assert r.headers["content-disposition"].startswith('attachment; filename="permita38-export-')
    assert boss_client.post("/api/data/export").status_code == 200  # no body at all is the default
    assert boss_client.post("/api/data/export", json={"scope": "everything"}).status_code == 422
    r2 = boss_client.post("/api/data/export", json={"bogus": 1})
    assert r2.status_code == 422 and r2.json()["detail"][0]["type"] == "extra_forbidden"
    assert boss_client.post("/api/data/export", json={"include_accounts": "maybe"}).status_code == 422
    assert boss_client.post("/api/data/export", json={"scope": "full\x00"}).status_code == 422


def test_temp_files_are_removed_after_every_operation(pair):
    src, dst_app, dst, tmp = pair
    blob = export_archive(src)
    dst.post("/api/data/import/preview", files={"file": ("a.zip", blob)})
    dst.post("/api/data/import/run", files={"file": ("a.zip", b"junk")}, data={"confirm": "true"})
    dst.post("/api/data/import/run", files={"file": ("a.zip", blob)}, data={"confirm": "true"})
    for app_dir in ("src", "dst"):
        assert not list((tmp / app_dir / "tmp").glob("*"))


def test_a_second_request_while_one_runs_gets_409(pair):
    src, _, dst, _ = pair
    assert _LOCK.acquire(blocking=False)
    try:
        for r in (
            src.post("/api/data/export", json={}),
            dst.post("/api/data/import/preview", files={"file": ("a.zip", b"x")}),
            dst.post("/api/data/import/run", files={"file": ("a.zip", b"x")}, data={"confirm": "true"}),
        ):
            assert r.status_code == 409 and r.json() == {"detail": "Another export or import is running."}
    finally:
        _LOCK.release()
    assert src.post("/api/data/export", json={}).status_code == 200


def test_the_lock_is_released_after_a_failure(pair, monkeypatch):
    src, dst_app, _, _ = pair
    import permita38.core.service.transfer.export as exp

    def boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(exp, "build", boom)
    with pytest.raises(RuntimeError):
        src.app.state.services.transfer.export(None, exp.ExportOptions())
    monkeypatch.undo()
    assert threading.Lock().acquire(blocking=False) and _LOCK.acquire(blocking=False)
    _LOCK.release()


def test_export_memory_stays_bounded_with_a_large_attachment(pair):
    src, _, _, _ = pair
    store = src.app.state.services.store
    store.save_attachment("big.bin", bytes(range(256)) * (24 * limits.MIB // 256))
    tracemalloc.start()
    try:
        blob_len = 0
        with src.stream("POST", "/api/data/export", json={}) as r:
            for chunk in r.iter_bytes(limits.CHUNK):
                blob_len += len(chunk)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert blob_len > 24 * limits.MIB
    assert peak < 20 * limits.MIB  # the archive is streamed from a temp file, never held whole


def test_archive_survives_a_zip_reader_roundtrip(pair):
    src, _, _, _ = pair
    with zipfile.ZipFile(io.BytesIO(export_archive(src))) as zf:
        assert zf.testzip() is None
