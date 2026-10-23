"""A simple cross-platform lock file: only one backup (or restore) may run on a data folder at a time."""

import json
import os
import socket
import time
from pathlib import Path

STALE_AFTER = 6 * 3600  # seconds: a lock older than this is considered abandoned


class LockBusy(Exception):
    pass


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:  # e.g. Windows: signal 0 is not supported for foreign processes
        return True
    return True


class FileLock:
    def __init__(self, path: Path, stale_after: float = STALE_AFTER, clock=time.time):
        self.path = Path(path)
        self.stale_after = stale_after
        self.clock = clock
        self._held = False

    def _stale(self) -> bool:
        try:
            info = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return True  # unreadable/half-written lock file: treat as abandoned after the age check below
        age = self.clock() - float(info.get("time", 0))
        if age > self.stale_after:
            return True
        # same machine and the process is gone -> abandoned
        return info.get("host") == socket.gethostname() and not _pid_alive(int(info.get("pid", 0)))

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                if self._stale():
                    self.path.unlink(missing_ok=True)
                    continue
                raise LockBusy(f"Another backup is already running (lock file {self.path})") from None
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"pid": os.getpid(), "host": socket.gethostname(), "time": self.clock()}, f)
            self._held = True
            return
        raise LockBusy(f"Could not take the backup lock {self.path}")

    def release(self) -> None:
        if self._held:
            self.path.unlink(missing_ok=True)
            self._held = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
