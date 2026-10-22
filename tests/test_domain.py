import os
import shutil
import tempfile
from datetime import date

from permita38.core import tax
from permita38.core.holidays import easter, holidays
from permita38.core.store import Store


def test_tariff_2026_continuity():
    assert tax.est_single(12348, 2026) == 0
    assert abs(tax.est_single(17800, 2026) - 1035) <= 2
    assert abs(tax.est_single(69879, 2026) - 18214) <= 3
    assert tax.est_single(100000, 2026) == 30864
    assert tax.est(100000, 2026, True) == 2 * tax.est_single(50000, 2026)


def test_freiberufler_vs_gmbh():
    fb = tax.estimate(60000, 2026, {"company_type": "freiberufler"})
    assert 0.15 < fb["rate"] < 0.35
    assert fb["gewst"] == 0
    gm = tax.estimate(60000, 2026, {"company_type": "gmbh", "hebesatz": 400})
    assert abs(gm["kst"] - 9000) < 0.01
    assert abs(gm["gewst"] - 60000 * 0.035 * 4) < 0.01


def test_gewerbe_credit():
    g = tax.estimate(80000, 2026, {"company_type": "gewerbe", "hebesatz": 400})
    assert g["gewst"] > 0 and g["gewst_credit"] > 0


def test_holidays():
    assert easter(2026) == date(2026, 4, 5)
    by = holidays(2026, "BY")
    assert date(2026, 4, 3) in by  # Good Friday
    assert date(2026, 1, 6) in by
    assert date(2026, 1, 6) not in holidays(2026, "NW")


def test_store_ignores_conflict_files():
    tmp = tempfile.mkdtemp()
    try:
        s = Store(tmp)
        r = s.save("customers", {"name": "A"})
        with open(os.path.join(tmp, "customers", r["id"] + ".sync-conflict-20260101-ABC.json"), "w") as f:
            f.write("{}")
        assert len(s.list("customers")) == 1
        assert len(s.conflicts()) == 1
    finally:
        shutil.rmtree(tmp)
