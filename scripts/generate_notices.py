#!/usr/bin/env python3
"""Generate THIRD_PARTY_NOTICES.md and verify that every runtime dependency is GPLv3-compatible.

    generate_notices.py                      # write THIRD_PARTY_NOTICES.md from the *installed* dependency tree
    generate_notices.py --check-licenses     # only check compatibility (CI); exit 1 on unknown/incompatible licenses

The dependency closure is resolved from the installed package metadata starting at the ``dependencies`` of
pyproject.toml, so it reflects exactly what is shipped (dev/test tools are excluded). Unknown or incompatible
licenses fail the run: nothing is guessed.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# SPDX identifiers (lower-cased) whose terms allow inclusion in a GPL-3.0-or-later combined work.
COMPATIBLE = {
    "mit", "mit-0", "bsd-2-clause", "bsd-3-clause", "bsd-3-clause-clear", "0bsd", "isc", "zlib", "unlicense", "cc0-1.0",
    "apache-2.0", "psf-2.0", "python-2.0", "mpl-2.0", "bsl-1.0", "hpnd", "wtfpl", "public-domain",
    "lgpl-2.1-only", "lgpl-2.1-or-later", "lgpl-3.0-only", "lgpl-3.0-or-later",
    "gpl-3.0-only", "gpl-3.0-or-later", "agpl-3.0-only", "agpl-3.0-or-later",
}  # fmt: skip
# Known to be incompatible (or not permitted) – reported with a specific message.
INCOMPATIBLE = {"gpl-2.0-only", "gpl-2.0", "proprietary", "commercial", "sspl-1.0", "busl-1.1", "cc-by-nc-4.0"}

# free-text / classifier spellings -> SPDX
ALIASES = [
    (r"^mit( license)?$", "MIT"),
    (r"^mit[- ]0$", "MIT-0"),
    (r"^bsd[- ]?2[- ]clause.*|^simplified bsd.*|^freebsd.*", "BSD-2-Clause"),
    (r"^bsd[- ]?3[- ]clause.*|^new bsd.*|^modified bsd.*|^revised bsd.*", "BSD-3-Clause"),
    (r"^bsd( license)?$", "BSD-3-Clause"),  # trove "BSD License" is generic; every BSD variant is compatible
    (r"^isc( license.*)?$", "ISC"),
    (r"^apache.*(2\.0|2).*|^apache software license$|^apache license$", "Apache-2.0"),
    (r"^(psf|python software foundation).*|^python-2\.0$", "PSF-2.0"),
    (r"^mozilla public license 2\.0.*|^mpl[- ]2\.0.*", "MPL-2.0"),
    (r"^the unlicense.*|^unlicense$", "Unlicense"),
    (r"^zlib.*", "Zlib"),
    (r"^0bsd$", "0BSD"),
    (r"^(public domain|cc0.*)$", "CC0-1.0"),
    (r"^gnu lesser general public license v3.*(or later)?.*|^lgpl-?3.*\+?$", "LGPL-3.0-or-later"),
    (r"^gnu lesser general public license v2.*|^lgpl-?2.*", "LGPL-2.1-or-later"),
    (r"^gnu general public license v3.*(or later)?.*|^gpl-?3.*", "GPL-3.0-or-later"),
    (r"^gnu affero general public license v3.*|^agpl-?3.*", "AGPL-3.0-or-later"),
    (r"^gnu general public license v2.*|^gpl-?2.*", "GPL-2.0-only"),
]
LICENSE_FILE_RE = re.compile(r"(^|/)(licen[sc]e|copying|notice|copyright|authors)[^/]*$", re.I)


@dataclass
class Package:
    name: str
    version: str
    license_expr: str
    homepage: str = ""
    texts: list[tuple[str, str]] = field(default_factory=list)  # (file name, text)


# ----------------------------------------------------------------------------- license logic
def normalize_license(raw: str) -> str:
    """Map a free-text license string or trove classifier to an SPDX identifier (or return it unchanged)."""
    value = " ".join(raw.replace("License ::", "").replace("OSI Approved ::", "").split()).strip()
    low = value.lower()
    for pattern, spdx in ALIASES:
        if re.match(pattern, low):
            return spdx
    return value


def _tokens(expr: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", expr)


def is_compatible(expr: str) -> bool:
    """Evaluate an SPDX expression: ``A OR B`` needs one compatible side, ``A AND B`` needs both."""
    tokens = _tokens(expr)
    pos = 0

    def parse_or() -> bool:
        nonlocal pos
        result = parse_and()
        while pos < len(tokens) and tokens[pos].upper() == "OR":
            pos += 1
            right = parse_and()
            result = result or right
        return result

    def parse_and() -> bool:
        nonlocal pos
        result = parse_atom()
        while pos < len(tokens) and tokens[pos].upper() == "AND":
            pos += 1
            right = parse_atom()
            result = result and right
        return result

    def parse_atom() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            raise ValueError("incomplete license expression")
        tok = tokens[pos]
        if tok == "(":
            pos += 1
            value = parse_or()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return value
        pos += 1
        if pos < len(tokens) and tokens[pos].upper() == "WITH":  # license exception: judged by the base license
            pos += 2
        ident = tok.lower().removesuffix("+")
        if tok.endswith("+") and ident in {"gpl-3.0", "lgpl-2.1", "lgpl-3.0", "agpl-3.0"}:
            ident += "-or-later"
        return ident in COMPATIBLE and ident not in INCOMPATIBLE

    if not tokens:
        return False
    try:
        value = parse_or()
    except ValueError:
        return False
    return value and pos == len(tokens)


def license_of(dist: metadata.Distribution) -> str:
    """Best available license statement of an installed distribution, as an SPDX-ish expression."""
    md = dist.metadata
    expr = (md.get("License-Expression") or "").strip()
    if expr:
        return expr
    classifiers = [c for c in (md.get_all("Classifier") or []) if c.startswith("License ::")]
    mapped = [normalize_license(c) for c in classifiers if not c.endswith("OSI Approved")]
    mapped = [m for m in mapped if not m.startswith("License")]  # drop bare "License :: OSI Approved"
    raw = (md.get("License") or "").strip()
    if raw and "\n" not in raw and len(raw) < 80 and raw.upper() != "UNKNOWN":
        norm = normalize_license(raw)
        if is_compatible(norm) or not mapped:
            return norm
    if mapped:
        return " OR ".join(dict.fromkeys(mapped)) if len(set(mapped)) > 1 else mapped[0]
    if raw and raw.upper() != "UNKNOWN":
        first = raw.strip().splitlines()[0]
        return normalize_license(first)
    return "UNKNOWN"


# ----------------------------------------------------------------------------- dependency closure
def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def runtime_requirements(root: Path = ROOT) -> list[str]:
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    block = re.search(r"^dependencies\s*=\s*\[(.*?)^\]", text, re.M | re.S)
    if not block:
        raise SystemExit("could not find [project] dependencies in pyproject.toml")
    return [m.group(1) for m in re.finditer(r'"([A-Za-z0-9_.\-]+)', block.group(1))]


def _marker_applies(marker: str) -> bool:
    if "extra" in marker:
        return False
    try:
        from packaging.markers import Marker

        return Marker(marker).evaluate({"extra": ""})
    except Exception:  # packaging missing or marker unparsable: be conservative and include the dependency
        return True


def dependency_closure(start: list[str]) -> list[metadata.Distribution]:
    seen: dict[str, metadata.Distribution] = {}
    queue = list(start)
    while queue:
        name = canonical(queue.pop())
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            raise SystemExit(f"dependency {name!r} is not installed: run `pip install -e .` first") from None
        seen[name] = dist
        for req in dist.requires or []:
            body, _, marker = req.partition(";")
            if marker and not _marker_applies(marker.strip()):
                continue
            m = re.match(r"\s*([A-Za-z0-9_.\-]+)", body)
            if m:
                queue.append(m.group(1))
    return [seen[k] for k in sorted(seen)]


def collect(root: Path = ROOT) -> list[Package]:
    packages = []
    for dist in dependency_closure(runtime_requirements(root)):
        md = dist.metadata
        homepage = md.get("Home-page") or ""
        for url in md.get_all("Project-URL") or []:
            label, _, target = url.partition(",")
            if label.strip().lower() in {"homepage", "home", "source", "repository", "source code"} and not homepage:
                homepage = target.strip()
        pkg = Package(md["Name"], md["Version"], license_of(dist), homepage)
        for f in dist.files or []:
            if LICENSE_FILE_RE.search(str(f)) and "/tests/" not in str(f):
                try:
                    text = Path(str(dist.locate_file(f))).read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    continue
                if text and all(text != t for _, t in pkg.texts):
                    pkg.texts.append((Path(str(f)).name, text))
        packages.append(pkg)
    return packages


def check(packages: list[Package]) -> list[str]:
    problems = []
    for p in packages:
        if p.license_expr.upper() == "UNKNOWN":
            problems.append(f"{p.name} {p.version}: license could not be determined")
        elif not is_compatible(p.license_expr):
            problems.append(f"{p.name} {p.version}: license {p.license_expr!r} is not known to be GPLv3-compatible")
    return problems


# ----------------------------------------------------------------------------- output
def render(packages: list[Package]) -> str:
    out = [
        "# Third-party notices",
        "",
        "permitA38 is licensed under the GNU General Public License v3.0 or later (see `LICENSE`). It includes and",
        "depends on the third-party software listed below, each under its own license. This file is generated by",
        "`scripts/generate_notices.py` from the installed runtime dependency tree; do not edit it by hand.",
        "",
        "The standalone downloads additionally bundle the CPython interpreter and its standard library",
        "(Python Software Foundation License 2.0, https://docs.python.org/3/license.html).",
        "",
        "## Summary",
        "",
        "| Package | Version | License |",
        "|---|---|---|",
    ]
    for p in packages:
        out.append(f"| {p.name} | {p.version} | {p.license_expr} |")
    out += ["", "## License texts", ""]
    for p in packages:
        out.append(f"### {p.name} {p.version} — {p.license_expr}")
        if p.homepage:
            out.append(f"\n<{p.homepage}>")
        if not p.texts:
            out.append("\n_The package ships no separate license file; see its homepage._")
        for name, text in p.texts:
            out += ["", f"`{name}`", "", "```text", text.replace("```", "'''"), "```"]
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--output", type=Path, default=ROOT / "THIRD_PARTY_NOTICES.md")
    ap.add_argument("--check-licenses", action="store_true", help="only verify compatibility, write nothing")
    args = ap.parse_args(argv)

    packages = collect()
    problems = check(packages)
    for p in packages:
        print(f"{p.name:28} {p.version:12} {p.license_expr}")
    if problems:
        print("\nLicense problems:", *problems, sep="\n  - ", file=sys.stderr)
        return 1
    if args.check_licenses:
        print(f"\nOK: all {len(packages)} runtime dependencies are GPLv3-compatible")
        return 0
    args.output.write_text(render(packages), encoding="utf-8")
    print(f"\nWrote {args.output} ({len(packages)} packages)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
