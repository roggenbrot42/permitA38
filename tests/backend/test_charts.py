"""Chart-of-accounts loader and validation (docs/specs/accounting.md section 5) with a SYNTHETIC chart."""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from permita38.core import charts
from permita38.core.charts import (
    ENGINE_ROLES,
    AccountNo,
    ChartInvalid,
    ChartMissing,
    EurLine,
    Role,
    chart_for,
    load_chart,
)

FIXTURE = Path(__file__).parent / "fixtures" / "chart_synthetic.json"


def _raw() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return data


def _write(tmp_path: Path, mutate: Callable[[dict[str, Any]], None], name: str = "skr03-2026.json") -> Path:
    data = copy.deepcopy(_raw())
    mutate(data)
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_engine_roles_count_and_unique() -> None:
    assert len(ENGINE_ROLES) == 52
    assert len(set(ENGINE_ROLES)) == 52


def test_synthetic_chart_validates() -> None:
    c = load_chart(FIXTURE)
    assert c.chart == "skr03" and c.year == 2026 and c.status == "to_verify"
    assert {r for r, _ in c.roles} == set(ENGINE_ROLES)
    assert all(a.account.startswith("9") for a in c.accounts)


def test_account_lookup() -> None:
    c = load_chart(FIXTURE)
    assert c.account(Role("bank")) == "9001"
    assert c.account(Role("revenue_s19")) == "9007"
    with pytest.raises(KeyError):
        c.account(Role("nope"))


def test_category_lookup_default() -> None:
    c = load_chart(FIXTURE)
    assert c.role_for_category("software") == "expense_software"
    assert c.role_for_category("travel") == "travel_costs"
    assert c.role_for_category("unmapped_category") == "expense_other"


def test_eur_line_lookup() -> None:
    c = load_chart(FIXTURE)
    assert c.eur_line(c.account(Role("bank"))) is None
    assert c.eur_line(c.account(Role("expense_software"))) == EurLine("Line expense_software", True)
    assert c.eur_line(c.account(Role("meals_nondeductible"))) == EurLine("Line meals_nondeductible", False)
    assert c.eur_line(AccountNo("9900")) == EurLine("Line extra", True)
    with pytest.raises(KeyError):
        c.eur_line(AccountNo("1"))


def test_automatic_values_are_known_keys() -> None:
    c = load_chart(FIXTURE)
    autos = {a.automatic for a in c.accounts if a.automatic}
    assert autos == {"S19", "S7", "VST"}


def _drop_role(d: dict[str, Any]) -> None:
    del d["roles"]["bank"]


def _unknown_role(d: dict[str, Any]) -> None:
    d["roles"]["made_up"] = d["roles"]["bank"]


def _bad_category(d: dict[str, Any]) -> None:
    d["categories"]["x"] = "no_such_role"


def _duplicate(d: dict[str, Any]) -> None:
    d["roles"]["cash"]["account"] = d["roles"]["bank"]["account"]


def _bad_auto(d: dict[str, Any]) -> None:
    d["roles"]["vat_out_s19"]["automatic"] = "S99"


def _no_deductible(d: dict[str, Any]) -> None:
    d["roles"]["expense_other"]["eur_line"] = {"line": "Line"}


def _not_in_accounts(d: dict[str, Any]) -> None:
    d["accounts"] = [a for a in d["accounts"] if a["account"] != d["roles"]["cash"]["account"]]


def _wrong_year(d: dict[str, Any]) -> None:
    d["year"] = 2025


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (_drop_role, "engine role 'bank' is missing"),
        (_unknown_role, "unknown role 'made_up'"),
        (_bad_category, "unknown role 'no_such_role'"),
        (_duplicate, "used by both"),
        (_bad_auto, "'S99' is not a known tax key"),
        (_no_deductible, "'deductible'"),
        (_not_in_accounts, "missing in 'accounts'"),
        (_wrong_year, "differs from the file name year"),
    ],
)
def test_invalid_variants(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mutate: Any, message: str) -> None:
    path = _write(tmp_path, mutate)
    with pytest.raises(ChartInvalid, match=message):
        if mutate is _wrong_year:
            monkeypatch.setattr(charts, "_DIR", tmp_path)
            chart_for("skr03", 2026)
        else:
            load_chart(path)


def test_shared_account_allowed_when_declared(tmp_path: Path) -> None:
    def share(d: dict[str, Any]) -> None:
        d["roles"]["cash"]["account"] = d["roles"]["bank"]["account"]
        d["shared"] = [["bank", "cash"]]

    c = load_chart(_write(tmp_path, share))
    assert c.account(Role("cash")) == c.account(Role("bank"))


def _set_year(year: int) -> Callable[[dict[str, Any]], None]:
    def mutate(d: dict[str, Any]) -> None:
        d["year"] = year

    return mutate


def _install(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, years: tuple[int, ...]) -> None:
    for y in years:
        _write(tmp_path, _set_year(y), f"skr03-{y}.json")
    monkeypatch.setattr(charts, "_DIR", tmp_path)


def test_chart_for_exact_year_has_no_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, (2025, 2026))
    c = chart_for("skr03", 2026)
    assert c.year == 2026 and c.warning is None


def test_chart_for_falls_back_to_older_with_warning(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, (2024, 2025))
    c = chart_for("skr03", 2027)
    assert c.year == 2025 and c.requested_year == 2027
    assert c.warning == "No skr03 chart for 2027; using 2025."


def test_chart_for_older_than_all_uses_oldest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, (2025, 2026))
    c = chart_for("skr03", 2020)
    assert c.year == 2025 and c.warning is not None


def test_chart_for_missing_chart_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _install(tmp_path, monkeypatch, (2026,))
    with pytest.raises(ChartMissing):
        chart_for("skr04", 2026)
