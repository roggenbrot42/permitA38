import pytest
from pydantic import ValidationError

from permita38.core.models import PersonalTaxIn
from permita38.core.repository.sqlite import SQLiteRepository
from permita38.core.service.personal_tax import PersonalTaxService
from permita38.core.settings import effective_personal_tax
from permita38.core.validation import MAX_MONEY


@pytest.fixture
def store(tmp_path):
    repo = SQLiteRepository(tmp_path / "data")
    repo.startup()
    yield repo
    repo.close()


def ok(**kw):
    return PersonalTaxIn(**kw)


# (path in the body, minimum, maximum)
BOUNDS = [
    (("church_tax_percent",), 0, 15),
    (("other_income_year",), 0, MAX_MONEY),
    (("other_deductions_year",), 0, MAX_MONEY),
    (("other_insurance_year",), 0, MAX_MONEY),
    (("children", "count"), 0, 15),
    (("children", "pv_children_under_25"), 0, 15),
    (("health", "zusatzbeitrag_percent"), 0, 10),
    (("health", "pkv_monthly"), 0, 100_000),
    (("health", "pkv_basis_monthly"), 0, 100_000),
    (("health", "pkv_care_monthly"), 0, 100_000),
    (("health", "pkv_employer_subsidy_monthly"), 0, 100_000),
    (("pension", "fixed_monthly"), 0, 100_000),
    (("employment", "gross_year"), 0, MAX_MONEY),
    (("employment", "steuerklasse"), 1, 6),
    (("employment", "bav_employee_year"), 0, 100_000),
    (("employment", "bav_employer_year"), 0, 100_000),
    (("employment", "vwl_employer_year"), 0, 10_000),
    (("employment", "werbungskosten_year"), 0, MAX_MONEY),
    (("employment", "withheld_year"), 0, MAX_MONEY),
]


def body(path, value):
    d = {"employment": {"gross_year": 1}} if path[0] == "employment" else {}
    if path == ("health", "pkv_basis_monthly"):
        d = {"health": {"pkv_monthly": 100_000}}
    node = d
    for key in path[:-1]:
        node = node.setdefault(key, {})
    node[path[-1]] = value
    return d


@pytest.mark.parametrize("path,lo,hi", BOUNDS)
def test_bounds(path, lo, hi):
    ok(**body(path, lo))
    ok(**body(path, hi))
    step = 1 if isinstance(lo, int) and hi < 100 else 0.01
    with pytest.raises(ValidationError):
        ok(**body(path, lo - step))
    with pytest.raises(ValidationError):
        ok(**body(path, hi + step))


@pytest.mark.parametrize("path", [b[0] for b in BOUNDS])
def test_non_finite_refused(path):
    for bad in (float("nan"), float("inf")):
        with pytest.raises(ValidationError):
            ok(**body(path, bad))


@pytest.mark.parametrize(
    "body_",
    [
        {"assessment": "x"},
        {"health": {"mode": "x"}},
        {"pension": {"mode": "x"}},
        {"unemployment": {"mode": "x"}},
        {"employment": {}},
    ],
)
def test_enums_and_required(body_):
    with pytest.raises(ValidationError):
        ok(**body_)


def test_pkv_basis_not_above_premium():
    ok(health={"pkv_monthly": 400, "pkv_basis_monthly": 400})
    with pytest.raises(ValidationError):
        ok(health={"pkv_monthly": 400, "pkv_basis_monthly": 400.01})


def test_defaults_without_stored_profile(store):
    got = PersonalTaxService(store).get()
    assert got["stored"] is False
    assert got["assessment"] == "single"
    assert got["health"]["mode"] == "gkv" and got["health"]["krankengeld"] is True
    assert got["health"]["zusatzbeitrag_percent"] is None
    assert got["children"]["count"] == 0
    assert got["employment"] is None
    assert got["warnings"] is None


def test_update_stores_and_replaces(store):
    svc = PersonalTaxService(store)
    out = svc.update(ok(assessment="joint", other_income_year=500, health={"mode": "pkv"}).model_dump())
    assert out["stored"] is True and out["assessment"] == "joint"
    out = svc.update(ok(church_tax_percent=9).model_dump())  # replaces: nothing of the first survives
    assert out["assessment"] == "single" and out["other_income_year"] == 0 and out["health"]["mode"] == "gkv"
    got = svc.get()
    assert got["stored"] is True and got["church_tax_percent"] == 9
    assert got["warnings"] is None  # warnings are not stored


def test_update_keeps_other_settings(store):
    store.save_settings({"company_name": "Demo GmbH"})
    PersonalTaxService(store).update(ok().model_dump())
    assert store.settings()["company_name"] == "Demo GmbH"


def test_unknown_fields_round_trip(store):
    svc = PersonalTaxService(store)
    data = ok(**{"future": {"a": 1}, "children": {"count": 1, "later": True}}).model_dump()
    assert data["future"] == {"a": 1} and data["children"]["later"] is True
    out = svc.update(data)
    assert out["future"] == {"a": 1} and out["children"]["later"] is True
    got = svc.get()
    assert got["future"] == {"a": 1} and got["children"]["later"] is True


def test_damaged_object_falls_back_per_field():
    raw = {
        "personal_tax": {
            "assessment": "joint",
            "church_tax_percent": 99,
            "other_income_year": "abc",
            "children": {"count": 2, "pv_parent": "maybe", "full_allowance": True},
            "health": {"mode": "pkv", "pkv_monthly": 100, "pkv_basis_monthly": 500},
            "pension": "garbage",
            "unemployment": None,
            "employment": {"gross_year": "x"},
            "keep": 1,
        }
    }
    got = effective_personal_tax(raw)
    assert got["assessment"] == "joint"
    assert got["church_tax_percent"] == 0
    assert got["other_income_year"] == 0
    assert got["children"]["count"] == 2 and got["children"]["full_allowance"] is True
    assert got["children"]["pv_parent"] is False
    assert got["health"]["mode"] == "pkv" and got["health"]["pkv_monthly"] == 100  # only the basis is clamped
    assert got["health"]["pkv_basis_monthly"] == 100
    assert got["pension"]["mode"] == "none"
    assert got["unemployment"]["mode"] == "none"
    assert got["employment"] is None
    assert got["keep"] == 1


@pytest.mark.parametrize("stored", [None, "x", 5, [], {}])
def test_damaged_whole_profile(stored):
    raw = {} if stored is None else {"personal_tax": stored}
    assert effective_personal_tax(raw) == PersonalTaxIn().model_dump()


def test_valid_employment_kept():
    got = effective_personal_tax({"personal_tax": {"employment": {"gross_year": 50000, "steuerklasse": 4}}})
    assert got["employment"]["gross_year"] == 50000 and got["employment"]["steuerklasse"] == 4


def warnings_of(store, **kw):
    return PersonalTaxService(store).update(ok(**kw).model_dump())["warnings"]


@pytest.mark.parametrize("assessment", ["single", "joint"])
@pytest.mark.parametrize("klasse", [1, 2, 3, 4, 5, 6])
def test_warning_steuerklasse(store, assessment, klasse):
    msg = "Steuerklasse III/IV/V means married; check the assessment."
    got = warnings_of(store, assessment=assessment, employment={"gross_year": 1, "steuerklasse": klasse})
    fires = assessment == "single" and klasse in (3, 4, 5)
    assert (got == [msg]) if fires else not got


@pytest.mark.parametrize("mode", ["voluntary", "voluntary_half"])
def test_warning_unemployment(store, mode):
    msg = "Voluntary unemployment insurance is usually not possible next to an employment that is insured."
    assert warnings_of(store, employment={"gross_year": 1}, unemployment={"mode": mode}) == [msg]
    assert not warnings_of(store, employment={"gross_year": 1, "sv_liable": False}, unemployment={"mode": mode})
    assert not warnings_of(store, unemployment={"mode": mode})
    assert not warnings_of(store, employment={"gross_year": 1})


def test_warning_pkv_basis(store):
    msg = (
        "Enter the basic-cover share from your insurer's annual statement; "
        "without it nothing of the premium counts as basic cover."
    )
    assert warnings_of(store, health={"mode": "pkv", "pkv_monthly": 300}) == [msg]
    assert not warnings_of(store, health={"mode": "pkv", "pkv_monthly": 300, "pkv_basis_monthly": 250})
    assert not warnings_of(store, health={"mode": "pkv"})
    assert not warnings_of(store, health={"mode": "gkv", "pkv_monthly": 300})


def test_get_survives_reserved_keys(store):
    svc = PersonalTaxService(store)
    for stored in ({"stored": 1}, {"warnings": "x"}, {"stored": 1, "warnings": "x", "assessment": "joint"}):
        store.save_settings({"personal_tax": stored})
        got = svc.get()
        assert got["stored"] is True and got["warnings"] is None
    assert got["assessment"] == "joint"
    assert "stored" not in effective_personal_tax({"personal_tax": {"stored": 1}})


def test_bool_not_accepted_as_number():
    got = effective_personal_tax(
        {
            "personal_tax": {
                "children": {"count": True, "pv_children_under_25": True, "full_allowance": True},
                "church_tax_percent": True,
                "employment": {"gross_year": 5, "bav_employee_year": True},
            }
        }
    )
    assert got["children"]["count"] == 0 and got["children"]["pv_children_under_25"] == 0
    assert got["children"]["full_allowance"] is True
    assert got["church_tax_percent"] == 0
    assert got["employment"]["gross_year"] == 5 and got["employment"]["bav_employee_year"] == 0
    assert effective_personal_tax({"personal_tax": {"employment": {"gross_year": True}}})["employment"] is None


def test_health_inconsistent_keeps_mode_and_premium():
    got = effective_personal_tax(
        {
            "personal_tax": {
                "health": {"mode": "pkv", "pkv_monthly": 100, "pkv_basis_monthly": 500, "krankengeld": False}
            }
        }
    )
    assert got["health"]["mode"] == "pkv" and got["health"]["pkv_monthly"] == 100
    assert got["health"]["pkv_basis_monthly"] == 100 and got["health"]["krankengeld"] is False


def test_employment_one_bad_optional_field():
    got = effective_personal_tax({"personal_tax": {"employment": {"gross_year": 40000, "bav_employee_year": -1}}})
    assert got["employment"]["gross_year"] == 40000 and got["employment"]["bav_employee_year"] == 0


def test_unknown_fields_in_damaged_object_survive():
    got = effective_personal_tax({"personal_tax": {"children": {"count": 99, "later": [1]}, "x": {"y": 2}}})
    assert got["children"]["count"] == 0 and got["children"]["later"] == [1]
    assert got["x"] == {"y": 2}


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 16, -1, 2**70])
def test_non_finite_and_out_of_range_fall_back(bad):
    got = effective_personal_tax(
        {"personal_tax": {"children": {"count": bad}, "church_tax_percent": bad, "other_income_year": bad}}
    )
    assert got["children"]["count"] == 0
    assert got["church_tax_percent"] == 0
    assert got["other_income_year"] == (16 if bad == 16 else 0)


@pytest.mark.parametrize("field", ["children", "health", "pension", "unemployment"])
def test_list_instead_of_nested_object(field):
    got = effective_personal_tax({"personal_tax": {field: [1, 2]}})
    assert got[field] == PersonalTaxIn().model_dump()[field]
    assert effective_personal_tax({"personal_tax": {"employment": [1]}})["employment"] is None


@pytest.mark.parametrize("stored", [{}, "x"])
def test_stored_empty_or_string_reads_as_stored(store, stored):
    store.save_settings({"personal_tax": stored})
    got = PersonalTaxService(store).get()
    assert got["stored"] is True
    assert got["assessment"] == "single" and got["children"]["count"] == 0 and got["employment"] is None
