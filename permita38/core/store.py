"""File-based storage: one JSON file per record (the repository layer).

One file per record means two devices rarely touch the same file, which keeps
Syncthing / Nextcloud / Dropbox conflicts to a minimum. Writes are atomic.
"""

import json
import os
import re
import threading
import time
import uuid

COLLECTIONS = [
    "customers",
    "projects",
    "tasks",
    "entries",
    "contracts",
    "absences",
    "offers",
    "invoices",
    "expenses",
    "users",
    "templates",
]
ID_RE = re.compile(r"^[0-9a-z]{6,32}$")
FILE_RE = re.compile(r"^[0-9a-z]{6,32}\.json$")  # ignores *.sync-conflict-* files


def new_id():
    return uuid.uuid4().hex[:12]


class Store:
    def __init__(self, root):
        self.root = os.path.abspath(root)
        self.lock = threading.RLock()
        self._cache = {}
        for c in COLLECTIONS + ["timers", "attachments"]:
            os.makedirs(os.path.join(self.root, c), exist_ok=True)

    # -- generic records -------------------------------------------------
    def _path(self, coll, rid):
        if coll not in COLLECTIONS and coll != "timers":
            raise KeyError(coll)
        if not ID_RE.match(rid):
            raise ValueError("bad id")
        return os.path.join(self.root, coll, rid + ".json")

    def _read(self, path):
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size)
        hit = self._cache.get(path)
        if hit and hit[0] == key:
            return hit[1]
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self._cache[path] = (key, data)
        return data

    def list(self, coll):
        d = os.path.join(self.root, coll)
        out = []
        for name in sorted(os.listdir(d)):
            if FILE_RE.match(name):
                try:
                    out.append(dict(self._read(os.path.join(d, name))))
                except (OSError, ValueError):
                    continue  # half-synced file; picked up next time
        return out

    def get(self, coll, rid):
        try:
            return dict(self._read(self._path(coll, rid)))
        except (OSError, ValueError):
            return None

    def save(self, coll, obj):
        with self.lock:
            obj = dict(obj)
            if not obj.get("id"):
                obj["id"] = new_id()
                obj["created"] = int(time.time())
            obj["updated"] = int(time.time())
            self._write_json(self._path(coll, obj["id"]), obj)
            return obj

    def delete(self, coll, rid):
        with self.lock:
            try:
                os.remove(self._path(coll, rid))
                return True
            except OSError:
                return False

    def _write_json(self, path, data):
        tmp = path + ".tmp-%d" % os.getpid()
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp, path)

    # -- settings --------------------------------------------------------
    def settings(self):
        p = os.path.join(self.root, "settings.json")
        try:
            return self._read(p)
        except (OSError, ValueError):
            return {}

    def save_settings(self, data):
        with self.lock:
            self._write_json(os.path.join(self.root, "settings.json"), data)

    # -- attachments -----------------------------------------------------
    def save_attachment(self, filename, blob):
        ext = os.path.splitext(filename)[1].lower()
        if not re.match(r"^\.[a-z0-9]{1,5}$", ext):
            ext = ".bin"
        name = new_id() + ext
        with open(os.path.join(self.root, "attachments", name), "wb") as f:
            f.write(blob)
        return name

    def attachment_path(self, name):
        if not re.match(r"^[0-9a-z]{6,32}\.[a-z0-9]{1,5}$", name):
            return None
        p = os.path.join(self.root, "attachments", name)
        return p if os.path.isfile(p) else None

    def conflicts(self):
        found = []
        for dp, _, files in os.walk(self.root):
            found += [os.path.relpath(os.path.join(dp, f), self.root) for f in files if ".sync-conflict-" in f]
        return found
