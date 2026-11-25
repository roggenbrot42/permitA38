"""Export / replace-restore with the accounting tables (docs/specs/accounting.md 7.2, 22 row E26).
Synthetic chart and invented data; two throwaway repositories play source and target."""

import hashlib
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pytest

from permita38.core import charts
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.repository import create_repository
from permita38.core.service.journal import JournalService, JournalSource, PostLine
from permita38.core.service.transfer import manifest
from permita38.core.service.transfer.export import ExportOptions
from permita38.core.service.transfer.service import ImportRefused, TransferService

FIXTURE = Path(__file__).parent / "fixtures" / "chart_synthetic.json"
SRC = JournalSource(event="issue", created_by="u1x000", source_coll="invoices", source_id="inv1abc")
JOURNAL_TABLES = ("fiscal_years", "journal_entries", "journal_lines", "payments", "period_locks", "vat_returns")


def fiscal_year(year: int):
    return {
        "year": year, "chart": "skr03", "income_basis": "eur", "vat_basis": "soll", "vat_frequency": "month",
        "dauerfrist": False, "small_business": False, "rules_version": "r1", "opened_at": 1, "opened_by": "u1x000",
    }  # fmt: skip


def lines(net: int) -> list[PostLine]:
    vat = net * 19 // 100
    return [
        PostLine(role="bank", amount_cents=net + vat),
        PostLine(role="revenue_s19", amount_cents=-net, income_date=date(2026, 1, 15)),
        PostLine(
            role="vat_out_s19", amount_cents=-vat, tax_key="S19", tax_kind="tax", tax_rate="19",
            tax_base_cents=net, vat_date=date(2026, 1, 15),
        ),
    ]  # fmt: skip


@pytest.fixture(autouse=True)
def chart(tmp_path, monkeypatch):
    charts_dir = tmp_path / "charts"
    charts_dir.mkdir()
    shutil.copy(FIXTURE, charts_dir / "skr03-2026.json")
    monkeypatch.setattr(charts, "_DIR", charts_dir)


def make_repo(tmp_path, name: str):
    r = create_repository(Config(data_dir=tmp_path / name, secret_key="x" * 16))
    r.startup()
    return r


def fill(repo, per_year: dict[int, int]) -> None:
    """Fiscal years, ``per_year[year]`` entries each, one period lock and one filed VAT return (invented)."""
    svc = JournalService(repo, clock=lambda: 1_800_000_000)
    repo.save("invoices", {"id": SRC.source_id, "number": "R-1"})  # the entries' source exists (checked on restore)
    for year, n in per_year.items():
        repo.save("fiscal_years", fiscal_year(year))
        for i in range(1, n + 1):
            svc.post(f"issue:{year}:{i}", date(year, 1, 15), f"Invoice {year}-{i}", SRC, lines(10000 * i))
    repo.save("period_locks", {"year": 2025, "month": 12, "locked_at": 5, "locked_by": "u1x000", "reason": "manual"})
    repo.save(
        "vat_returns",
        {
            "year": 2025, "period_kind": "month", "period_no": 12, "from_date": "2025-12-01", "to_date": "2025-12-31",
            "due_date": "2026-01-10", "status": "final", "amends_key": "", "filed_on": "2026-01-09",
            "finalised_at": 7, "finalised_by": "u1x000",
        },
    )  # fmt: skip


def export(repo, tmp_path) -> Path:
    res = TransferService(repo, tmp_path / "tmp-out", allow_no_users=True).export(None, ExportOptions())
    return res.path


def snapshot(repo) -> dict:
    return {t: sorted(repo.list(t), key=lambda r: r["id"]) for t in JOURNAL_TABLES + ("number_sequences",)}


def service(repo, tmp_path) -> TransferService:
    return TransferService(repo, tmp_path / "tmp-in", allow_no_users=True)


def clean(repo) -> None:
    svc = JournalService(repo)
    for year in {int(f["year"]) for f in repo.list("fiscal_years")}:
        assert svc.verify_chain(year) == []


@pytest.fixture
def source(tmp_path):
    repo = make_repo(tmp_path, "source")
    fill(repo, {2025: 2, 2026: 3})
    yield repo
    repo.close()


def test_manifest_has_the_accounting_block_and_all_tables(source, tmp_path):
    path = export(source, tmp_path)
    with zipfile.ZipFile(path) as zf:
        man = json.loads(zf.read("manifest.json"))
        names = set(zf.namelist())
    assert man["format_version"] == manifest.FORMAT_VERSION == 2
    for t in JOURNAL_TABLES + ("number_sequences",):
        assert f"data/{t}.json" in names
    assert man["counts"]["journal_entries"] == 5 and man["counts"]["journal_lines"] == 15
    assert man["counts"]["fiscal_years"] == 2 and man["counts"]["period_locks"] == 1
    assert man["counts"]["vat_returns"] == 1
    tails = man["accounting"]["journal"]
    assert set(tails) == {"2025", "2026"}
    assert tails["2025"] == {"last_entry_no": 2, "last_hash": source.last_journal_hash(2025)}
    assert tails["2026"] == {"last_entry_no": 3, "last_hash": source.last_journal_hash(2026)}
    seqs = {(r["doc_type"], r["period"]): r["last_value"] for r in source.list("number_sequences")}
    assert seqs[("journal", "2025")] == 2 and seqs[("journal", "2026")] == 3


def test_restore_into_an_empty_instance_is_identical(source, tmp_path):
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    out = service(target, tmp_path).run(None, path)
    assert out["imported"] is True
    assert snapshot(target) == snapshot(source)
    assert target.last_journal_hash(2026) == source.last_journal_hash(2026)
    clean(target)
    target.close()


def test_replace_restore_over_existing_accounting_data(source, tmp_path):
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    fill(target, {2025: 1, 2026: 1})  # a shorter, different journal (other ids, same keys)
    assert snapshot(target) != snapshot(source)
    out = service(target, tmp_path).run(None, path, replace=True)
    assert out["imported"] is True
    assert not [w for w in out["warnings"] if w["code"] == "journal_shortened" or "source_id" in w["message"]]
    assert snapshot(target) == snapshot(source)
    clean(target)
    target.close()


def test_guards_are_back_after_the_restore(source, tmp_path):
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    fill(target, {2025: 1})
    service(target, tmp_path).run(None, path, replace=True)
    for sql in (
        "UPDATE journal_lines SET amount_cents = 1",
        "DELETE FROM journal_entries",
        "UPDATE period_locks SET reason = 'x'",
        "UPDATE vat_returns SET filed_on = '2026-02-01'",  # set_once
        "UPDATE vat_returns SET due_date = '2026-03-01'",  # frozen_when (final)
    ):
        with pytest.raises(Exception, match="state\\.|ref\\.in_use"):
            with target.transaction():
                target._exec(sql)
    target.close()


def test_guards_are_back_after_a_failed_restore(source, tmp_path):
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    fill(target, {2026: 1})
    before = snapshot(target)
    # a manifest that disagrees with the restored journal rolls everything back
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(bad, "w") as zout:
        for info in zin.infolist():
            data = zin.read(info.filename)
            if info.filename == "manifest.json":
                man = json.loads(data)
                man["accounting"]["journal"]["2026"]["last_entry_no"] = 99
                data = json.dumps(man).encode()
            zout.writestr(info.filename, data)
    with pytest.raises(ServiceError) as err:
        service(target, tmp_path).run(None, bad, replace=True, confirm_shorten=True)
    assert err.value.status == 422
    assert snapshot(target) == before
    with pytest.raises(Exception, match="state\\.append_only"):
        with target.transaction():
            target._exec("UPDATE journal_lines SET amount_cents = 1")
    target.close()


def test_import_without_replace_never_merges_a_journal(source, tmp_path):
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    fill(target, {2026: 1})
    before = snapshot(target)
    with pytest.raises(ServiceError) as err:
        service(target, tmp_path).run(None, path)
    assert err.value.status == 409 and "journal_entries" in err.value.message
    assert "never merged" in err.value.message
    assert snapshot(target) == before
    target.close()


def test_append_only_cannot_be_suspended_outside_the_restore(source):
    with pytest.raises(ValueError, match="append_only"):
        with source.transaction(), source.guards_suspended({"append_only"}):
            pass


def test_older_archive_warns_and_needs_confirmation(tmp_path):
    older = make_repo(tmp_path, "older")
    fill(older, {2026: 1})
    path = export(older, tmp_path)
    target = make_repo(tmp_path, "target")
    fill(target, {2026: 3})  # the current journal is longer than the archive's
    before = snapshot(target)
    svc = service(target, tmp_path)
    preview = svc.preview(None, path, replace=True)
    shortened = [w for w in preview["warnings"] if w["code"] == "journal_shortened"]
    assert len(shortened) == 1 and "shortened" in shortened[0]["message"]
    assert "year 2026 has 3 entries now but 1 in the archive" in shortened[0]["message"]
    with pytest.raises(ServiceError) as err:
        svc.run(None, path, replace=True)
    assert err.value.status == 409 and "shortened" in err.value.message
    assert snapshot(target) == before
    out = svc.run(None, path, replace=True, confirm_shorten=True)
    assert "journal_shortened" in [w["code"] for w in out["warnings"]]
    assert len(target.list("journal_entries")) == 1
    clean(target)
    older.close()
    target.close()


def test_a_longer_archive_is_no_warning(source, tmp_path):
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    fill(target, {2025: 1, 2026: 1})
    preview = service(target, tmp_path).preview(None, path, replace=True)
    assert preview["ok"] and not [w for w in preview["warnings"] if w["code"] == "journal_shortened"]
    target.close()


def test_cli_replace_needs_confirm_shorten_for_a_shorter_journal(tmp_path, capsys):
    """The replace-restore is reachable from the command line only (``import --replace [--confirm-shorten]``)."""
    from permita38.cli.main import main

    older = make_repo(tmp_path, "older")
    fill(older, {2026: 1})
    path = export(older, tmp_path)
    older.close()
    target = make_repo(tmp_path, "target")
    fill(target, {2026: 3})
    target.close()
    cli = ["-c", str(tmp_path / "target.yaml"), "--data", str(tmp_path / "target")]
    with pytest.raises(SystemExit):  # without --replace the instance is not empty
        main([*cli, "import", str(path), "--yes"])
    with pytest.raises(SystemExit, match="confirm_shorten"):
        main([*cli, "import", str(path), "--replace", "--yes"])
    main([*cli, "import", str(path), "--replace", "--confirm-shorten", "--yes"])
    assert "Imported." in capsys.readouterr().out
    reopened = make_repo(tmp_path, "target")
    assert len(reopened.list("journal_entries")) == 1
    reopened.close()


def rewrite(path: Path, out: Path, change) -> Path:
    with zipfile.ZipFile(path) as zin, zipfile.ZipFile(out, "w") as zout:
        for info in sorted(zin.infolist(), key=lambda i: i.filename == "manifest.json"):  # manifest last
            zout.writestr(info.filename, change(info.filename, zin.read(info.filename)))
    return out


def test_entry_whose_source_was_deleted_restores_without_a_warning(source, tmp_path):
    source.delete("invoices", SRC.source_id)  # the journal outlives its source (like E25)
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    out = service(target, tmp_path).run(None, path)
    assert not [w for w in out["warnings"] if ".source_id" in w["message"]]
    assert snapshot(target) == snapshot(source)
    target.close()


def test_replace_is_refused_when_the_dialect_cannot_suspend_guards(source, tmp_path, monkeypatch):
    path = export(source, tmp_path)
    target = make_repo(tmp_path, "target")
    monkeypatch.setattr(type(target), "can_suspend_guards", property(lambda self: False))
    with pytest.raises(ServiceError) as err:
        service(target, tmp_path).run(None, path, replace=True)
    assert err.value.status == 409 and "SQLite" in err.value.message
    target.close()


def test_tampered_middle_entry_with_a_correct_tail_is_refused(source, tmp_path):
    path = export(source, tmp_path)

    new: dict[str, bytes] = {}

    def tamper(name, data):
        if name == "manifest.json":  # the file hashes are re-sealed: only the chain check can catch this
            man = json.loads(data)
            meta = man["files"]["data/journal_entries.json"]
            meta["sha256"], meta["bytes"] = hashlib.sha256(new["j"]).hexdigest(), len(new["j"])
            return json.dumps(man).encode()
        if name != "data/journal_entries.json":
            return data
        body = json.loads(data)
        mid = next(r for r in body["records"] if r["fiscal_year"] == 2026 and r["entry_no"] == 2)
        mid["text"] = "changed outside the app"
        new["j"] = json.dumps(body).encode()
        return new["j"]

    bad = rewrite(path, tmp_path / "tampered.zip", tamper)
    target = make_repo(tmp_path, "target")
    with pytest.raises(ServiceError) as err:
        service(target, tmp_path).run(None, bad)
    assert err.value.status == 422 and "entry 2" in err.value.message
    assert target.list("journal_entries") == []
    target.close()


def test_replace_with_an_archive_without_accounting_needs_confirmation(tmp_path):
    plain = make_repo(tmp_path, "plain")
    path = export(plain, tmp_path)  # no journal at all
    plain.close()

    def strip(name, data):
        if name == "manifest.json":
            man = json.loads(data)
            man.pop("accounting", None)
            return json.dumps(man).encode()
        return data

    old = rewrite(path, tmp_path / "old.zip", strip)
    target = make_repo(tmp_path, "target")
    fill(target, {2026: 2})
    before = snapshot(target)
    svc = service(target, tmp_path)
    with pytest.raises(ServiceError) as err:
        svc.run(None, old, replace=True)
    assert err.value.status == 409 and "shortened" in err.value.message
    assert snapshot(target) == before
    svc.run(None, old, replace=True, confirm_shorten=True)
    assert target.list("journal_entries") == []
    target.close()


def test_replace_refuses_an_attachment_with_other_content_before_writing(tmp_path):
    src = make_repo(tmp_path, "src")
    name = src.save_attachment("receipt.pdf", b"%PDF-1.4 one")
    src.save("expenses", {"id": "e1x000", "attachment": name})
    path = TransferService(src, tmp_path / "o", allow_no_users=True).export(None, ExportOptions()).path
    target = make_repo(tmp_path, "target")
    target.restore_attachment(name, b"%PDF-1.4 other")
    target.save("invoices", {"id": "i1x000", "number": "R-9"})
    before = target.list("invoices")
    with pytest.raises(ImportRefused) as err:
        service(target, tmp_path).run(None, path, replace=True)
    assert "attachment_conflict" in [e["code"] for e in err.value.body()["errors"]]
    assert target.list("invoices") == before
    src.close()
    target.close()
