"""The accounting oracle fixture covers every worked example of docs/specs/accounting.md section 19."""

import json
import re
from pathlib import Path

import pytest

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "accounting_oracle.json").read_text("utf-8"))
SPEC = Path(__file__).parents[2] / "docs" / "specs" / "accounting.md"


def _numbers(cases: list[dict], prefix: str) -> set[int]:
    return {int(m.group(1)) for c in cases if (m := re.match(rf"^{prefix}(\d+)(?!\d)", c["id"]))}


def test_every_worked_example_of_the_spec_has_a_journal_case():
    if not SPEC.exists():
        pytest.skip("docs/specs/accounting.md is not on this branch")
    named = {int(n) for n in re.findall(r"^\*\*A(\d+)\.", SPEC.read_text("utf-8"), flags=re.MULTILINE)}
    assert named == set(range(1, 19)), "section 19 should name A1..A18"
    have = _numbers(FIXTURE["journal_cases"], "A")
    assert named <= have, f"journal cases missing for {sorted(named - have)}"


def test_named_examples_without_the_spec_file():
    assert set(range(1, 19)) <= _numbers(FIXTURE["journal_cases"], "A")


def test_ustva_zm_payment_and_issue_cases_are_complete():
    assert set(range(1, 18)) <= _numbers(FIXTURE["ustva_cases"], "U")
    assert set(range(1, 5)) <= _numbers(FIXTURE["zm_cases"], "Z")
    assert set(range(1, 7)) <= _numbers(FIXTURE["payment_cases"], "P")
    assert set(range(1, 7)) <= _numbers(FIXTURE["journal_cases"], "I")
