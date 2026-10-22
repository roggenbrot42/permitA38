"""GPLv3 license-compatibility gate for the runtime dependencies (see scripts/generate_notices.py)."""

import re
from pathlib import Path

import generate_notices as gn
import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    "expr",
    [
        "MIT",
        "BSD-3-Clause",
        "Apache-2.0",
        "MIT OR Apache-2.0",
        "Apache-2.0 AND MIT",
        "(MIT OR GPL-2.0-only) AND ISC",
        "GPL-3.0-or-later",
        "GPL-3.0+",
        "LGPL-2.1-or-later",
        "MPL-2.0",
        "PSF-2.0",
        "Apache-2.0 WITH LLVM-exception",
    ],
)
def test_compatible_expressions(expr):
    assert gn.is_compatible(expr)


@pytest.mark.parametrize(
    "expr",
    ["GPL-2.0-only", "Proprietary", "UNKNOWN", "", "MIT AND GPL-2.0-only", "SSPL-1.0", "Some Custom License", "MIT OR"],
)
def test_incompatible_or_unknown_expressions_fail(expr):
    assert not gn.is_compatible(expr)


@pytest.mark.parametrize(
    "raw,spdx",
    [
        ("MIT License", "MIT"),
        ("BSD License", "BSD-3-Clause"),
        ("BSD-3-Clause", "BSD-3-Clause"),
        ("Apache Software License", "Apache-2.0"),
        ("Apache License 2.0", "Apache-2.0"),
        ("Python Software Foundation License", "PSF-2.0"),
        ("GNU General Public License v3 or later (GPLv3+)", "GPL-3.0-or-later"),
        ("GNU General Public License v2 (GPLv2)", "GPL-2.0-only"),
        ("Mozilla Public License 2.0 (MPL 2.0)", "MPL-2.0"),
    ],
)
def test_normalize_license(raw, spdx):
    assert gn.normalize_license(raw) == spdx


def test_every_runtime_dependency_is_gpl3_compatible():
    packages = gn.collect(ROOT)
    assert len(packages) >= 5, "dependency closure looks wrong"
    assert gn.check(packages) == []


def test_dependency_closure_includes_transitive_packages():
    names = {p.name.lower() for p in gn.collect(ROOT)}
    assert {"fastapi", "pydantic", "starlette", "uvicorn", "pyyaml"} <= names


def test_dev_tools_are_not_part_of_the_notices():
    names = {p.name.lower() for p in gn.collect(ROOT)}
    assert not names & {"pytest", "ruff", "mypy", "pyinstaller", "playwright"}


def test_committed_notices_file_lists_every_direct_dependency():
    text = (ROOT / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    for name in gn.runtime_requirements(ROOT):
        assert re.search(rf"^\| {re.escape(name)} \|", text, re.I | re.M), f"{name} missing from THIRD_PARTY_NOTICES.md"
    assert "scripts/generate_notices.py" in text
