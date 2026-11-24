"""Every data file inside the package must be listed as package data, or the wheel and the bundle miss it."""

import re
import tomllib
from pathlib import Path

import permita38

ROOT = Path(permita38.__file__).parent
DATA_SUFFIXES = {".json", ".sql", ".yaml", ".csv", ".txt"}


def _declared() -> dict[str, list[str]]:
    pyproject = tomllib.loads((ROOT.parent / "pyproject.toml").read_text(encoding="utf-8"))
    return pyproject["tool"]["setuptools"]["package-data"]


def test_data_files_are_declared_as_package_data():
    declared = _declared()
    missing = []
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in DATA_SUFFIXES or "__pycache__" in path.parts:
            continue
        rel = path.relative_to(ROOT)
        # the package is the nearest ancestor directory with an __init__.py
        pkg_dir = path.parent
        while not (pkg_dir / "__init__.py").exists():
            pkg_dir = pkg_dir.parent
        package = ".".join(("permita38", *pkg_dir.relative_to(ROOT).parts))
        pattern_path = path.relative_to(pkg_dir)
        if not any(pattern_path.match(p) for p in declared.get(package, [])):
            missing.append(str(rel))
    assert not missing, f"not declared in [tool.setuptools.package-data]: {missing}"


def test_bundle_spec_lists_every_data_file():
    """PyInstaller does not read package-data: each data directory needs its own entry in the spec's datas."""
    spec = (ROOT.parent / "packaging" / "permita38.spec").read_text(encoding="utf-8")
    targets = re.findall(r'"(permita38/[^"]+)"', spec)
    missing = []
    for path in sorted(ROOT.rglob("*")):
        if path.suffix not in DATA_SUFFIXES or "__pycache__" in path.parts:
            continue
        rel = "permita38/" + path.relative_to(ROOT).as_posix()
        if not any(rel.startswith(t.rstrip("/") + "/") for t in targets):
            missing.append(rel)
    assert not missing, f"not bundled by packaging/permita38.spec: {missing}"
