"""The terse test runner and the short failure report of the randomized browser tests."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def load_runner():
    spec = importlib.util.spec_from_file_location("run_suites", ROOT / "scripts" / "run_suites.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_summary_keeps_the_verdict_and_the_first_failures_only():
    text = "\n".join(
        ["....F.", *[f"FAILED tests/x.py::test_{i} - boom" for i in range(7)], "7 failed, 30 passed in 2.00s"]
    )
    out = load_runner().summarize(text)
    assert out.splitlines()[0] == "7 failed, 30 passed in 2.00s"
    assert "tests/x.py::test_0" in out and "tests/x.py::test_4" in out and "tests/x.py::test_5" not in out
    assert "(+2 more)" in out


def test_summary_of_a_passing_run_is_one_line():
    assert load_runner().summarize("....\n12 passed, 3 skipped in 1.20s\n") == "12 passed, 3 skipped in 1.20s"


def test_failure_report_is_short_and_the_full_log_is_saved(tmp_path, monkeypatch):
    from tests.web.e2e_random import helpers

    monkeypatch.setattr(helpers, "ARTIFACTS", tmp_path / "test-artifacts")
    log = [f"{i:03d} step" for i in range(1, 101)]
    msg = helpers.report_failure("monkey", 7, log, tail=12)
    assert "PERMITA38_E2E_SEED=7" in msg and "100 step" in msg and "088 step" not in msg
    assert (tmp_path / "test-artifacts" / "e2e-monkey-seed7.log").read_text().count("\n") == 100
