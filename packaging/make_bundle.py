#!/usr/bin/env python3
"""Turn PyInstaller's dist/permita38/ folder into a reproducible release archive.

    python packaging/make_bundle.py --platform linux-x86_64 --version 0.4.0-alpha.1 [--dist dist/permita38] [--out dist]

Windows -> permita38-<version>-windows-x86_64.zip, Linux -> permita38-<version>-linux-x86_64.tar.gz. The archive holds
one top-level folder with the program, launcher script, LICENSE, THIRD_PARTY_NOTICES.md and a short README. File
order, owners and timestamps are normalised (SOURCE_DATE_EPOCH) so the same input yields the same archive.
"""

from __future__ import annotations

import argparse
import gzip
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

README = """permitA38 {version} ({platform})
================================

Time tracking, planning, offers, invoices and tax reserves for freelancers and small businesses in Germany.
Licensed under the GNU GPL v3 or later - see LICENSE. Third-party licenses: THIRD_PARTY_NOTICES.md.

Start
-----
  {launcher}                 starts permitA38 and opens your browser (http://localhost:8765)
  {launcher} --data <folder> keeps your data in <folder> (put it inside your Syncthing share to sync devices)

The first start asks you to create the boss account. Data lives in <data folder>/permita38.sqlite3.
Documentation: https://github.com/roggenbrot42/permitA38

Verify this download
--------------------
  sha256sum -c SHA256SUMS                                      (Windows: Get-FileHash <file> -Algorithm SHA256)
  gh attestation verify <file> --repo roggenbrot42/permitA38   (build provenance)

This is a pre-release if the version contains "alpha", "beta" or "rc": expect rough edges and keep backups.
"""


def source_date_epoch() -> int:
    return int(os.environ.get("SOURCE_DATE_EPOCH", "1577836800"))  # 2020-01-01 if not set


def stage(dist: Path, platform: str, version: str, staging: Path) -> Path:
    top = staging / f"permita38-{version}-{platform}"
    shutil.copytree(dist, top, symlinks=True)
    windows = platform.startswith("windows")
    launcher = "start-permita38.bat" if windows else "start-permita38.sh"
    src = ROOT / "packaging" / ("windows" if windows else "linux") / launcher
    shutil.copy2(src, top / launcher)
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        shutil.copy2(ROOT / name, top / name)
    text = README.format(version=version, platform=platform, launcher=launcher)
    (top / "README.txt").write_text(text.replace("\n", "\r\n") if windows else text, encoding="utf-8", newline="")
    return top


def _entries(top: Path):
    for path in sorted(top.rglob("*"), key=lambda p: p.relative_to(top).as_posix()):
        yield path, Path(top.name) / path.relative_to(top)


def _mode(path: Path) -> int:
    return 0o755 if (path.is_dir() or path.stat().st_mode & stat.S_IXUSR) else 0o644


def write_tar_gz(top: Path, target: Path) -> None:
    mtime = source_date_epoch()
    with open(target, "wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=mtime) as gz:
        with tarfile.open(fileobj=gz, mode="w", format=tarfile.PAX_FORMAT) as tar:
            for path, arcname in _entries(top):
                info = tar.gettarinfo(str(path), arcname.as_posix())
                info.uid = info.gid = 0
                info.uname = info.gname = "root"
                info.mtime = mtime
                info.mode = _mode(path) if not path.is_symlink() else info.mode
                if info.isfile():
                    with open(path, "rb") as f:
                        tar.addfile(info, f)
                else:
                    tar.addfile(info)


def write_zip(top: Path, target: Path) -> None:
    mtime = max(source_date_epoch(), 315532800)  # zip cannot store dates before 1980
    date_time = tuple(__import__("time").gmtime(mtime))[:6]
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path, arcname in _entries(top):
            if path.is_dir():
                continue
            info = zipfile.ZipInfo(arcname.as_posix(), date_time)  # type: ignore[arg-type]
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (stat.S_IFREG | _mode(path)) << 16
            z.writestr(info, path.read_bytes())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--platform", required=True, choices=["windows-x86_64", "linux-x86_64"])
    ap.add_argument("--version", required=True, help="release version, e.g. 0.4.0-alpha.1")
    ap.add_argument("--dist", type=Path, default=ROOT / "dist" / "permita38")
    ap.add_argument("--out", type=Path, default=ROOT / "dist")
    args = ap.parse_args(argv)
    if not args.dist.is_dir():
        print(f"error: {args.dist} not found (run PyInstaller first)", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as staging:
        top = stage(args.dist, args.platform, args.version, Path(staging))
        if args.platform.startswith("windows"):
            target = args.out / f"{top.name}.zip"
            write_zip(top, target)
        else:
            target = args.out / f"{top.name}.tar.gz"
            write_tar_gz(top, target)
    print(target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
