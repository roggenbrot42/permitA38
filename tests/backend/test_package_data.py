"""Every data file inside the package must be listed as package data, or the wheel and the bundle miss it."""

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


def test_bundle_spec_lists_trip_rates():
    spec = (ROOT.parent / "packaging" / "permita38.spec").read_text(encoding="utf-8")
    assert '"trip_rates"' in spec
