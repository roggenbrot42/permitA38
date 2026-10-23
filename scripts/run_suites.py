#!/usr/bin/env python3
"""Run the test suites one after the other and print ONE line per suite (standard library only).

Why: the full output of the randomized and browser suites is long and cheap to avoid reading. Every suite runs with
terse pytest options, its complete output goes to test-artifacts/<suite>.log and the console only shows the verdict,
the counts, the duration and the first failing test ids. A lock file makes a second run (another terminal, an agent)
wait instead of loading the machine twice.

    python scripts/run_suites.py                 # backend, random, web
    python scripts/run_suites.py backend lint    # only these
    python scripts/run_suites.py web --last-failed
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "test-artifacts"
PYTEST = [sys.executable, "-m", "pytest", "-q", "--no-header", "--tb=line", "-rfE", "-p", "no:cacheprovider"]
SUITES: dict[str, list[str]] = {
    "lint": [],  # handled below
    "backend": [*PYTEST, "tests/backend", "tests/release", "--ignore=tests/backend/randomized"],
    "random": [*PYTEST, "tests/backend/randomized"],
    "web": [*PYTEST, "tests/web"],
}
DEFAULT = ["backend", "random", "web"]


def run_lint(log) -> int:
    code = 0
    for cmd in (
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "."],
        [sys.executable, "-m", "mypy", "permita38"],
    ):
        code |= subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
    return code


def summarize(text: str) -> str:
    last = [ln for ln in text.splitlines() if re.search(r"\b(passed|failed|error|errors|no tests ran)\b", ln)]
    failed = [
        ln.split(" - ")[0].removeprefix("FAILED ").removeprefix("ERROR ")
        for ln in text.splitlines()
        if ln.startswith(("FAILED ", "ERROR "))
    ]
    line = last[-1].strip(" =") if last else "no summary line"
    if failed:
        line += (
            "\n      first failures: "
            + "; ".join(failed[:5])
            + (f" (+{len(failed) - 5} more)" if len(failed) > 5 else "")
        )
    return line


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("suites", nargs="*", help=f"default: {' '.join(DEFAULT)}")
    ap.add_argument("--last-failed", action="store_true", help="pytest --lf (only what failed in the previous run)")
    args = ap.parse_args()
    names = args.suites or DEFAULT
    unknown = [n for n in names if n not in SUITES]
    if unknown:
        ap.error(f"unknown suite(s) {unknown}; choose from {list(SUITES)}")
    OUT.mkdir(exist_ok=True)
    with open(OUT / ".lock", "w") as lock:
        try:
            import fcntl  # POSIX only; on Windows runs are simply not serialised
        except ImportError:
            fcntl = None
        if fcntl:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("another test run is active - waiting for it to finish ...", flush=True)
                fcntl.flock(lock, fcntl.LOCK_EX)
        worst = 0
        for name in names:
            t0 = time.time()
            logfile = OUT / f"{name}.log"
            with open(logfile, "w") as log:
                if name == "lint":
                    code = run_lint(log)
                else:
                    cmd = SUITES[name] + (["--lf"] if args.last_failed else [])
                    code = subprocess.run(cmd, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT).returncode
            text = logfile.read_text(errors="replace")
            took = f"{time.time() - t0:.0f}s"
            verdict = "PASS" if code == 0 else "FAIL"
            detail = summarize(text) if name != "lint" else ("clean" if code == 0 else "see log")
            print(
                f"{name:8} {verdict}  {took:>5}  {detail}"
                + ("" if code == 0 else f"\n      log: test-artifacts/{name}.log"),
                flush=True,
            )
            worst = worst or int(code != 0)  # a signal gives a negative code, still a failure
        return 1 if worst else 0


if __name__ == "__main__":
    sys.exit(main())
