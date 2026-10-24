"""Contract every Repository implementation must satisfy (see core/repository/base.py)."""

import threading
import time

import pytest

from permita38.core.repository import COLLECTIONS, INTERNAL_COLLECTIONS, TABLES, DatabaseRevisionError, Repository
from permita38.core.repository.schema import Table

from .repository_registry import REGISTRY, integrity_error, registry_params

ALL = [*COLLECTIONS, *INTERNAL_COLLECTIONS]


@pytest.fixture(params=registry_params())
def repo(request, tmp_path) -> Repository:
    r = REGISTRY[request.param](tmp_path)
    r.startup()
    yield r
    r.close()


def sample(table: Table, i: int = 0) -> dict:
    """A record using every column with a value of the right logical type."""
    values = {
        "id": f"ref{i}abc",
        "str": f"Müller & Söhne “{i}” €",
        "text": "line one\nline two 😀 ' \" \\",
        "int": 40 + i,
        "float": 12.5 + i + 0.1 + 0.2,
        "bool": bool(i % 2),
        "json": [1, "two", {"k": [True, None, 2.5]}],
    }
    return {c.name: values[c.type] for c in table.columns if c.name not in ("id", "created", "updated")}


# ------------------------------------------------------------------ CRUD and fidelity
@pytest.mark.parametrize("coll", ALL)
@pytest.mark.parametrize("i", [0, 1])  # i=0 -> bools False, i=1 -> True
def test_round_trip_every_collection(repo, coll, i):
    rec = sample(TABLES[coll], i)
    saved = repo.save(coll, dict(rec))
    assert saved["id"] and isinstance(saved["created"], int) and isinstance(saved["updated"], int)
    for k, v in rec.items():
        assert saved[k] == v, k
        assert type(saved[k]) is type(v), k  # bool stays bool, float stays float, int stays int
    assert repo.get(coll, saved["id"]) == saved
    assert saved in repo.list(coll)


def test_none_is_omitted_and_save_replaces(repo):
    a = repo.save("customers", {"name": "A", "city": "Berlin", "rate": None})
    assert "rate" not in a
    b = repo.save("customers", {"id": a["id"], "name": "A2"})  # full replace: city disappears
    assert b["name"] == "A2" and "city" not in b
    assert repo.get("customers", a["id"]) == b


def test_created_preserved_updated_refreshed(repo):
    a = repo.save("customers", {"name": "A", "created": 1000, "updated": 1000})
    assert a["created"] == 1000 and a["updated"] >= int(time.time()) - 5
    b = repo.save("customers", {"id": a["id"], "name": "B"})  # no created given -> kept
    assert b["created"] == 1000
    c = repo.save("customers", {"id": a["id"], "name": "C", "updated": 5}, touch=False)
    assert c["updated"] == 5


def test_zero_false_and_empty_values_are_kept(repo):
    p = repo.save("projects", {"name": "P", "personal": False, "rate": 0, "member_ids": [], "notes": ""})
    assert p["personal"] is False and p["rate"] == 0 and p["member_ids"] == [] and p["notes"] == ""


def test_unknown_fields_survive(repo):
    extra = {"future_field": {"nested": [1, 2, {"x": "ü"}]}, "flag": True, "n": 7}
    a = repo.save("customers", {"name": "A", **extra})
    got = repo.get("customers", a["id"])
    assert got is not None
    for k, v in extra.items():
        assert got[k] == v
    b = repo.save("customers", {**got, "name": "A2"})  # resave keeps them
    assert b["future_field"] == extra["future_field"]


def test_values_that_do_not_fit_the_column_type_are_preserved(repo):
    a = repo.save("entries", {"date": "2026-01-01", "minutes": "ninety", "billable": "yes", "rate": 2**70})
    got = repo.get("entries", a["id"])
    assert got is not None
    assert got["minutes"] == "ninety" and got["billable"] == "yes" and got["rate"] == 2**70


def test_nan_and_huge_ints_do_not_break_storage(repo):
    a = repo.save("expenses", {"date": "2026-01-01", "net": float("nan"), "vat": 2**80})
    got = repo.get("expenses", a["id"])
    assert got is not None and got["vat"] == 2**80 and got["net"] != got["net"]


def test_get_and_delete_edge_cases(repo):
    assert repo.get("customers", "nosuchid1") is None
    assert repo.get("customers", "../../etc/passwd") is None
    assert repo.get("customers", "") is None
    with pytest.raises(KeyError):
        repo.get("nonsense", "abcdef")
    with pytest.raises(KeyError):
        repo.list("nonsense")
    with pytest.raises(ValueError):
        repo.save("customers", {"id": "BAD ID!", "name": "x"})
    a = repo.save("customers", {"name": "A"})
    assert repo.delete("customers", a["id"]) is True
    assert repo.delete("customers", a["id"]) is False
    assert repo.get("customers", a["id"]) is None


def test_list_order_is_created_then_id(repo):
    ids = {}
    for name, created in (("c", 300), ("a", 100), ("b", 200), ("d", 200)):
        ids[name] = repo.save("customers", {"name": name, "created": created})["id"]
    got = [r["name"] for r in repo.list("customers")]
    tie = ["b", "d"] if ids["b"] < ids["d"] else ["d", "b"]
    assert got == ["a", *tie, "c"]


# ------------------------------------------------------------------ transactions
def test_transaction_commits(repo):
    with repo.transaction():
        repo.save("customers", {"name": "A"})
        repo.save("customers", {"name": "B"})
    assert len(repo.list("customers")) == 2


def test_transaction_rolls_back_everything_on_error(repo):
    keep = repo.save("customers", {"name": "keep"})
    with pytest.raises(RuntimeError):
        with repo.transaction():
            repo.save("customers", {"name": "new"})
            repo.save("customers", {"id": keep["id"], "name": "changed"})
            repo.delete("customers", keep["id"])
            repo.save_settings({"a": 1})
            raise RuntimeError("boom")
    assert [c["name"] for c in repo.list("customers")] == ["keep"]
    assert repo.settings() == {}


def test_nested_transactions_join_the_outer_one(repo):
    with pytest.raises(RuntimeError):
        with repo.transaction():
            with repo.transaction():
                repo.save("customers", {"name": "inner"})
            raise RuntimeError("outer fails")
    assert repo.list("customers") == []


def test_concurrent_writers_do_not_lose_records(repo):
    errors = []

    def work(n):
        try:
            for i in range(25):
                repo.save("customers", {"name": f"t{n}-{i}"})
        except Exception as e:  # pragma: no cover
            errors.append(e)

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors
    assert len(repo.list("customers")) == 200


def test_transactions_are_isolated_read_modify_write(repo):
    counter = repo.save("entries", {"date": "2026-01-01", "minutes": 0})

    def work():
        for _ in range(20):
            with repo.transaction():
                e = repo.get("entries", counter["id"])
                e["minutes"] += 1
                repo.save("entries", e)

    threads = [threading.Thread(target=work) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert repo.get("entries", counter["id"])["minutes"] == 160


# ------------------------------------------------------------------ settings, timers, attachments
def test_settings_round_trip_and_replace(repo):
    assert repo.settings() == {}
    data = {"company_name": "Müller GmbH", "vat_rate": 19.0, "kleinunternehmer": False, "n": 3, "nested": {"a": [1]}}
    repo.save_settings(data)
    assert repo.settings() == data
    repo.save_settings({"only": "this", "dropped": None})
    assert repo.settings() == {"only": "this"}


def test_timers_hold_running_state(repo):
    repo.save("timers", {"id": "user00000001", "running": True, "started": 1_700_000_000, "note": "x"})
    t = repo.get("timers", "user00000001")
    assert t is not None and t["running"] is True and t["started"] == 1_700_000_000
    repo.save("timers", {"id": "user00000001", "running": False})
    t2 = repo.get("timers", "user00000001")
    assert t2 is not None and t2["running"] is False and "started" not in t2


def test_attachments(repo):
    name = repo.save_attachment("Receipt März.PDF", b"%PDF-1.4 test")
    path = repo.attachment_path(name)
    assert path and name.endswith(".pdf")
    with open(path, "rb") as f:
        assert f.read() == b"%PDF-1.4 test"
    assert repo.save_attachment("noext", b"x").endswith(".bin")
    for bad in ("../x.pdf", "nosuch12.pdf", "..", "a/b.pdf", ""):
        assert repo.attachment_path(bad) is None


# ------------------------------------------------------------------ operations
def test_health_and_revision(repo):
    from permita38.core.database import head_id

    h = repo.health()
    assert h["ok"] is True and h["revision"] == head_id() == h["current_revision"]
    assert repo.get_revision() == head_id()
    assert h["applied"] == len(repo.applied_ids())
    assert repo.migrate() == (head_id(), head_id(), 0)  # nothing pending


def test_conflicts_is_a_list(repo):
    assert repo.conflicts() == []


def test_backup_if_supported(repo, tmp_path):
    if repo.backup_dir is None:
        pytest.skip("backend has no file backups")
    repo.save("customers", {"name": "A"})
    dest = repo.backup(tmp_path / "b" / "copy.bak")
    assert dest.is_file() and dest.stat().st_size > 0


def test_database_revision_error_is_a_runtime_error():
    assert issubclass(DatabaseRevisionError, RuntimeError)


def test_user_roles_round_trip_unchanged(repo):
    """The storage layer keeps whatever role string it is given (the meaning lives in core/rights.py)."""
    for role in ("boss", "employee", "accountant", "member"):
        saved = repo.save("users", {"username": f"u-{role}", "name": role, "role": role})
        assert repo.get("users", saved["id"])["role"] == role


# ------------------------------------------------------------------ equality queries (calendar storage)
def test_find_matches_plain_columns_and_orders_like_list(repo):
    repo.save("calendar_sources", {"id": "srcaaaaa1", "user_id": "userAAAA1", "name": "A"})
    repo.save("calendar_sources", {"id": "srcbbbbb2", "user_id": "userBBBB2", "name": "B"})
    for i in range(3):
        repo.save("calendar_events", {"id": f"evt{i}aaaa", "source_id": "srcaaaaa1", "start_utc": f"2030-01-0{i + 1}"})
    repo.save("calendar_events", {"id": "evtbbbbbb", "source_id": "srcbbbbb2", "start_utc": "2030-01-01"})
    found = repo.find("calendar_events", source_id="srcaaaaa1")
    assert [r["id"] for r in found] == [r["id"] for r in repo.list("calendar_events") if r["source_id"] == "srcaaaaa1"]
    assert len(found) == 3
    assert repo.find("calendar_events", source_id="nosuchsrc") == []
    assert [r["name"] for r in repo.find("calendar_sources", user_id="userBBBB2")] == ["B"]


def test_find_refuses_json_and_unknown_columns(repo):
    with pytest.raises(ValueError):
        repo.find("calendar_sources", include_keywords=["x"])
    with pytest.raises(ValueError):
        repo.find("calendar_sources", no_such_column="x")


def test_delete_where_removes_only_matching_rows(repo):
    for i in range(3):
        repo.save("calendar_events", {"id": f"del{i}aaaa", "source_id": "srcdel001"})
    repo.save("calendar_events", {"id": "keepkeep1", "source_id": "srckeep01"})
    assert repo.delete_where("calendar_events", source_id="srcdel001") == 3
    assert [r["id"] for r in repo.list("calendar_events")] == ["keepkeep1"]
    with pytest.raises(ValueError):
        repo.delete_where("calendar_events")  # an empty condition would wipe the table


def test_save_many_is_all_or_nothing(repo):
    repo.save("calendar_events", {"id": "survivor1", "source_id": "srcx0001"})
    rows = [{"id": "batchaa01", "source_id": "srcx0002"}, {"id": "bad id!!", "source_id": "srcx0002"}]
    with pytest.raises(ValueError):
        repo.save_many("calendar_events", rows)
    assert [r["id"] for r in repo.list("calendar_events")] == ["survivor1"]
    saved = repo.save_many("calendar_events", [{"id": "batchbb02", "source_id": "srcx0003"}])
    assert saved[0]["id"] == "batchbb02" and repo.get("calendar_events", "batchbb02")


def test_restore_list_and_delete_attachments(repo):
    assert repo.list_attachments() == []
    repo.restore_attachment("abc123def4.pdf", b"one")
    repo.restore_attachment("abc123def4.pdf", b"one")  # same content again is fine
    with pytest.raises(FileExistsError):
        repo.restore_attachment("abc123def4.pdf", b"other")
    for bad in ("../x.pdf", "A1B2C3D4.pdf", "nodot", "a/b.pdf", ".pdf", ""):
        with pytest.raises(ValueError):
            repo.restore_attachment(bad, b"x")
    saved = repo.save_attachment("r.png", b"png")
    assert repo.list_attachments() == sorted(["abc123def4.pdf", saved])
    path = repo.attachment_path("abc123def4.pdf")
    assert path is not None
    with open(path, "rb") as f:
        assert f.read() == b"one"
    assert repo.delete_attachment("abc123def4.pdf") is True
    assert repo.delete_attachment("abc123def4.pdf") is False
    assert repo.delete_attachment("../x.pdf") is False
    assert repo.list_attachments() == [saved]


def test_attachment_staging_is_invisible_until_committed(repo):
    stage = repo.new_stage()
    repo.stage_attachment(stage, "abc123def4.pdf", [b"on", b"e"])
    assert repo.list_attachments() == [] and repo.attachment_path("abc123def4.pdf") is None
    with pytest.raises(ValueError):
        repo.stage_attachment(stage, "../x.pdf", [b"x"])
    with pytest.raises(ValueError):
        repo.stage_attachment("../../etc", "abc123def4.pdf", [b"x"])
    assert repo.commit_stage(stage) == ["abc123def4.pdf"]
    assert repo.list_attachments() == ["abc123def4.pdf"]
    repo.discard_stage(stage)  # idempotent: the stage is gone already
    other = repo.new_stage()
    repo.stage_attachment(other, "zzz999aaa1.pdf", [b"x"])
    repo.discard_stage(other)
    assert repo.list_attachments() == ["abc123def4.pdf"]
    stale = repo.new_stage()
    assert repo.discard_stale_stages(3600) == 0
    assert repo.discard_stale_stages(-1) >= 1
    with pytest.raises(OSError):
        repo.stage_attachment(stale, "abc123def5.pdf", [b"x"])


# ------------------------------------------------------------------ API tokens and the audit log
def test_api_token_hash_is_unique_and_findable(repo):
    a = repo.save("api_tokens", {"user_id": "u1", "label": "a", "token_hash": "h" * 64, "expires_at": 10})
    assert repo.find("api_tokens", token_hash="h" * 64) == [a]
    assert repo.find("api_tokens", token_hash="x" * 64) == []
    with pytest.raises(integrity_error(repo)):  # unique index idx_api_tokens_hash
        repo.save("api_tokens", {"user_id": "u2", "label": "b", "token_hash": "h" * 64, "expires_at": 10})
    assert len(repo.list("api_tokens")) == 1


def test_api_tokens_keep_unknown_fields_and_false_booleans(repo):
    a = repo.save(
        "api_tokens",
        {"user_id": "u1", "token_hash": "a" * 64, "read_only": False, "revoked_at": None, "future": {"k": [1, 2]}},
    )
    got = repo.get("api_tokens", a["id"])
    assert got["read_only"] is False and got["future"] == {"k": [1, 2]} and "revoked_at" not in got


def test_audit_log_keeps_unknown_fields_and_filters_by_token_and_user(repo):
    base = {"at": 5, "method": "GET", "route": "/api/x", "status": 200}
    repo.save("audit_log", {**base, "token_id": "t1", "user_id": "u1", "later": "kept"})
    repo.save("audit_log", {**base, "token_id": "t2", "user_id": "u1"})
    repo.save("audit_log", {**base, "token_id": "t3", "user_id": "u2"})
    assert [r["later"] for r in repo.find("audit_log", token_id="t1")] == ["kept"]
    assert repo.delete_where("audit_log", user_id="u1") == 2
    assert [r["token_id"] for r in repo.list("audit_log")] == ["t3"]
    assert repo.delete_where("api_tokens", user_id="nobody") == 0
