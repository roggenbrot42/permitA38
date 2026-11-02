"""Parent side of the scan: starts the worker process, enforces timeout and output cap, never raises.

Errors are fixed codes in ``ReadResult.error`` (``timeout``, ``crash``, ``bad_output``, ``too_large`` ...). No file
content is logged (docs/specs/receipt-scan.md, "Worker process").
"""

import json
import os
import signal
import subprocess
import sys
import threading
from typing import Any

from permita38.core.config import ScanConfig
from permita38.core.receipts.extraction import ReadResult, read_result_from_json

MAX_STDOUT_BYTES = 1_000_000
MAX_PAGE_BYTES = 12_000_000  # one page image as PNG

_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
_semaphores: dict[int, threading.BoundedSemaphore] = {}
_lock = threading.Lock()


def _semaphore(n: int) -> threading.BoundedSemaphore:
    with _lock:
        return _semaphores.setdefault(n, threading.BoundedSemaphore(n))


def _scan(config: Any) -> ScanConfig:
    return getattr(config, "scan", config)


def worker_command(path: str, scan: ScanConfig, page: int | None = None, rotation: int | None = None) -> list[str]:
    """Argument list that starts the worker; the bundled build is its own interpreter (`sys.executable`)."""
    base = [sys.executable] if getattr(sys, "frozen", False) else [sys.executable, "-m", "permita38"]
    cmd = [*base, "scan-worker", "--timeout", str(scan.timeout_s), "--languages", scan.languages]
    if scan.tesseract_path:
        cmd += ["--tesseract", scan.tesseract_path]
    if page is not None:
        cmd += ["--page", str(page)]
    if rotation is not None:
        cmd += ["--rotation", str(rotation)]
    return [*cmd, "--", path]


def _kill(proc: "subprocess.Popen[bytes]") -> None:
    """End the worker; on POSIX the whole process group, so that a Tesseract child does not survive."""
    if os.name == "posix":
        try:
            os.killpg(proc.pid, signal.SIGKILL)
            return
        except OSError:
            pass
    proc.kill()


def _run(cmd: list[str], timeout_s: int, cap: int) -> tuple[bytes | None, str | None]:
    """(stdout, None) or (None, error code)."""
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=_NO_WINDOW,
            start_new_session=os.name == "posix",  # own process group, so that a Tesseract child can be ended too
        )
    except OSError:
        return None, "crash"
    chunks: list[bytes] = []
    state = {"size": 0, "over": False}

    def drain() -> None:
        assert proc.stdout is not None
        while True:
            block = proc.stdout.read(65536)
            if not block:
                return
            state["size"] += len(block)
            if state["size"] > cap:
                state["over"] = True
                _kill(proc)
                return
            chunks.append(block)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        _kill(proc)
        proc.wait()
        reader.join(5)
        return None, "timeout"
    reader.join(5)
    if state["over"]:
        return None, "too_large"
    if proc.returncode != 0:
        return None, "crash"
    return b"".join(chunks), None


def read_file(path: str, config: Any) -> ReadResult:
    """Read a stored receipt file in a worker process. Never raises."""
    scan = _scan(config)
    try:
        with _semaphore(scan.max_concurrent):
            out, error = _run(worker_command(str(path), scan), scan.timeout_s, MAX_STDOUT_BYTES)
        if out is None:
            return ReadResult(engine="none", error=error)
        return read_result_from_json(json.loads(out.decode("utf-8")))
    except Exception:  # bad JSON, wrong shape, anything: the file is simply unreadable
        return ReadResult(engine="none", error="bad_output")


def render_page(path: str, page: int, config: Any, rotation: int | None = None) -> bytes | None:
    """PNG of page ``page`` (0-based) of a PDF at 150 dpi, or ``None`` if it cannot be rendered. Never raises."""
    scan = _scan(config)
    try:
        with _semaphore(scan.max_concurrent):
            out, _error = _run(worker_command(str(path), scan, page, rotation), scan.timeout_s, MAX_PAGE_BYTES)
        return out if out and out.startswith(b"\x89PNG") else None
    except Exception:
        return None
