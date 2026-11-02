"""Parent side of the scan: the real worker end to end, and a fake worker that times out, crashes or floods."""

import subprocess
import sys
import threading
import time

import pytest

from permita38.core.config import ScanConfig
from permita38.core.receipts import runner
from tests.backend.receipt_helpers import UBL, receipt_pdf

SCAN = ScanConfig(timeout_s=5, tesseract_path="/nonexistent/tesseract")


def _fake(monkeypatch, code: str):
    monkeypatch.setattr(runner, "worker_command", lambda path, scan, page=None: [sys.executable, "-c", code])


def test_real_worker_reads_a_pdf(tmp_path):
    path = tmp_path / "r.pdf"
    path.write_bytes(receipt_pdf())
    res = runner.read_file(str(path), SCAN)
    assert (res.engine, res.error, res.pages_read, res.pages_total) == ("pdf_text", None, 1, 1)
    assert "Gesamt" in {w.text for w in res.words}


def test_real_worker_reads_an_einvoice(tmp_path):
    path = tmp_path / "r.xml"
    path.write_text(UBL, encoding="utf-8")
    res = runner.read_file(str(path), SCAN)
    assert res.engine == "einvoice" and res.extraction.invoice_no == "RE-2026-0042"


@pytest.mark.parametrize("data, error", [(b"", "empty"), (b"%PDF-1.4 broken", "unreadable")])
def test_real_worker_bad_files(tmp_path, data, error):
    path = tmp_path / "r.pdf"
    path.write_bytes(data)
    res = runner.read_file(str(path), SCAN)
    assert (res.engine, res.error) == ("none", error)


def test_missing_file_does_not_raise(tmp_path):
    res = runner.read_file(str(tmp_path / "nope.pdf"), SCAN)
    assert (res.engine, res.error) == ("none", "unreadable")


def test_accepts_the_whole_config_too(tmp_path):
    class Cfg:
        scan = SCAN

    path = tmp_path / "r.xml"
    path.write_text(UBL, encoding="utf-8")
    assert runner.read_file(str(path), Cfg()).engine == "einvoice"


def test_page_png_through_the_worker(tmp_path):
    path = tmp_path / "r.pdf"
    path.write_bytes(receipt_pdf())
    assert runner.render_page(str(path), 0, SCAN).startswith(b"\x89PNG")
    assert runner.render_page(str(path), 5, SCAN) is None
    assert runner.render_page(str(tmp_path / "missing.pdf"), 0, SCAN) is None


def test_timeout(monkeypatch, tmp_path):
    _fake(monkeypatch, "import time; time.sleep(60)")
    start = time.monotonic()
    res = runner.read_file(str(tmp_path / "x"), ScanConfig(timeout_s=5))
    assert (res.engine, res.error) == ("none", "timeout")
    assert time.monotonic() - start < 15


def test_crash(monkeypatch, tmp_path):
    _fake(
        monkeypatch,
        "import os, signal; os.kill(os.getpid(), getattr(signal, 'SIGKILL', signal.SIGTERM))",
    )
    assert runner.read_file(str(tmp_path / "x"), SCAN).error == "crash"
    _fake(monkeypatch, "import sys; sys.exit(3)")
    assert runner.read_file(str(tmp_path / "x"), SCAN).error == "crash"


def test_output_over_the_cap(monkeypatch, tmp_path):
    _fake(monkeypatch, "import sys; sys.stdout.write('x' * 5_000_000)")
    res = runner.read_file(str(tmp_path / "x"), SCAN)
    assert (res.engine, res.error) == ("none", "too_large")


@pytest.mark.parametrize("out", ["not json", "[1, 2]", '{"words": [{"nope": 1}]}', '{"engine": "ocr", "words": 5}'])
def test_bad_output(monkeypatch, tmp_path, out):
    _fake(monkeypatch, f"import sys; sys.stdout.write({out!r})")
    res = runner.read_file(str(tmp_path / "x"), SCAN)
    assert (res.engine, res.error) == ("none", "bad_output")


def test_worker_that_cannot_start(monkeypatch, tmp_path):
    monkeypatch.setattr(runner, "worker_command", lambda path, scan, page=None: ["/nonexistent/program"])
    assert runner.read_file(str(tmp_path / "x"), SCAN).error == "crash"


def test_concurrency_is_limited(monkeypatch, tmp_path):
    _fake(monkeypatch, "import time, sys; time.sleep(0.4); sys.stdout.write('{}')")
    cfg = ScanConfig(timeout_s=10, max_concurrent=1)
    start = time.monotonic()
    threads = [threading.Thread(target=runner.read_file, args=(str(tmp_path / "x"), cfg)) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert time.monotonic() - start >= 1.1  # three one-at-a-time runs of 0.4 s


def test_frozen_build_calls_itself(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    cmd = runner.worker_command("/tmp/a.pdf", SCAN)
    assert cmd[:2] == [sys.executable, "scan-worker"] and cmd[-2:] == ["--", "/tmp/a.pdf"]


def test_subcommand_is_hidden_but_works(tmp_path):
    helptext = subprocess.run([sys.executable, "-m", "permita38", "--help"], capture_output=True, text=True).stdout
    assert "==SUPPRESS==" not in helptext
    assert not any(line.strip().startswith("scan-worker") for line in helptext.splitlines()[5:])  # no help entry
    path = tmp_path / "e.xml"
    path.write_text(UBL, encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "permita38", "scan-worker", "--", str(path)], capture_output=True)
    assert out.returncode == 0 and b'"engine":"einvoice"' in out.stdout


def test_scan_config_bounds():
    for bad in ({"timeout_s": 0}, {"max_concurrent": 0}, {"languages": "deu eng"}, {"unknown": 1}):
        with pytest.raises(ValueError):
            ScanConfig(**bad)
    cfg = ScanConfig()
    assert (cfg.enabled, cfg.tesseract_path, cfg.languages, cfg.timeout_s, cfg.max_concurrent) == (
        True,
        None,
        "deu+eng",
        120,
        2,
    )


def test_magic_ok_for_xml():
    from permita38.core.filetypes import magic_ok

    assert magic_ok(".xml", UBL.encode()) and magic_ok(".xml", b"\xef\xbb\xbf<Invoice/>")
    assert magic_ok(".xml", UBL.encode("utf-16")) and magic_ok(".xml", b"  \n<a/>")
    assert not magic_ok(".xml", b"%PDF-1.4") and not magic_ok(".xml", b"")


# ------------------------------------------------------------------ OCR page modes
def _words(n: int, text: str = "Wort") -> list:
    from permita38.core.receipts.extraction import Word

    return [Word(text, 0, 0.1, 0.1, 0.1, 0.01) for _ in range(n)]


def test_modes_6_3_11_are_run_and_the_first_is_the_main_reading():
    from permita38.core.receipts import worker

    seen = []

    def run_mode(psm):
        seen.append(psm)
        return _words(psm, f"m{psm}")

    first, others = worker._ocr_modes(run_mode, lambda: 100.0)
    assert seen == [6, 3, 11]
    assert [w.text for w in first][:1] == ["m6"] and [w[0].text for w in others] == ["m3", "m11"]


def test_later_modes_are_skipped_when_time_is_short_or_they_fail():
    from permita38.core.receipts import worker

    seen = []
    first, others = worker._ocr_modes(lambda p: seen.append(p) or _words(2), lambda: 1.0)
    assert seen == [6] and others == []

    def flaky(psm):
        if psm == 3:
            raise RuntimeError("tesseract_failed")
        return _words(2)

    assert worker._ocr_modes(flaky, lambda: 100.0)[1] == []
    with pytest.raises(RuntimeError):  # a failure of the first mode is the failure of the scan
        worker._ocr_modes(lambda p: (_ for _ in ()).throw(RuntimeError("x")), lambda: 100.0)


def test_psm_is_passed_to_tesseract(monkeypatch, tmp_path):
    from permita38.core.receipts import ocr

    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        (tmp_path / "x").mkdir(exist_ok=True)
        with open(cmd[2] + ".tsv", "w") as out:
            out.write("level\n")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    ocr.run_tesseract("tess", "deu", tmp_path / "i.png", 0, 5, psm=11)
    assert seen["cmd"][seen["cmd"].index("--psm") + 1] == "11"


def test_alternatives_are_dropped_first_to_stay_under_the_cap():
    import json

    from permita38.core.receipts import worker
    from permita38.core.receipts.extraction import ReadResult

    res = ReadResult("ocr", _words(50), None, 1, 1, alternatives=[_words(50), _words(50)])
    full = worker.serialise(res, max_bytes=10_000_000)
    assert len(json.loads(full)["alternatives"]) == 2
    one_word_set = len(worker.serialise(ReadResult("ocr", _words(50), None, 1, 1), max_bytes=10_000_000))
    small = worker.serialise(
        ReadResult("ocr", _words(50), None, 1, 1, alternatives=[_words(50), _words(50)]), max_bytes=one_word_set + 10
    )
    data = json.loads(small)
    assert data["alternatives"] == [] and len(data["words"]) == 50 and data["pages_read"] == 1


@pytest.mark.skipif(sys.platform == "win32", reason="process groups are POSIX")
def test_timeout_ends_a_child_of_the_worker(monkeypatch, tmp_path):
    marker = tmp_path / "alive"
    child = tmp_path / "child.py"
    child.write_text(f"import time, pathlib\ntime.sleep(8)\npathlib.Path({str(marker)!r}).write_text('x')\n")
    code = f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, {str(child)!r}])\ntime.sleep(60)"
    _fake(monkeypatch, code)
    res = runner.read_file(str(tmp_path / "x"), ScanConfig(timeout_s=5))
    assert res.error == "timeout"
    time.sleep(5)
    assert not marker.exists()
