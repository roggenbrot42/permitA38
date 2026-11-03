from datetime import date
from decimal import Decimal

from permita38.core import tax
from permita38.core.holidays import easter, holidays


def test_tariff_2026_continuity():
    assert tax.est_single(12348, 2026) == 0
    assert abs(tax.est_single(17800, 2026) - 1035) <= 2
    assert abs(tax.est_single(69879, 2026) - 18214) <= 3
    assert tax.est_single(100000, 2026) == 30864
    assert tax.est(100000, 2026, True) == 2 * tax.est_single(50000, 2026)


def test_tariff_exact_values_2026():
    # hand-computed: zone 2 at 17 799 (y = 0.5451): y = 0.5451: (914.51 * y + 1400) * y = 1034.85
    assert tax.est_single(17799, 2026) == 1034
    assert tax.est_single(Decimal("12348.99"), 2026) == 0
    assert tax.est_single(-5, 2026) == 0
    assert tax.est_single(47364, 2026) == 9634  # oracle O1
    assert tax.est(Decimal(100000), 2026, True) == 2 * tax.est_single(50000, 2026)
    assert isinstance(tax.est_single(100000, 2026), int)
    assert tax.KLEIN_LIMIT_PREV == 25000 and tax.KLEIN_LIMIT_CUR == 100000


def test_holidays():
    assert easter(2026) == date(2026, 4, 5)
    by = holidays(2026, "BY")
    assert date(2026, 4, 3) in by  # Good Friday
    assert date(2026, 1, 6) in by
    assert date(2026, 1, 6) not in holidays(2026, "NW")
