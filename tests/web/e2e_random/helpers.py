"""Small helpers shared by the random tests: number formatting as the UI shows it, an expected-value
calendar, and the seed handling."""

import os
import random
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from permita38.core.holidays import holidays

NBSP = "  "
DEFAULT_SEEDS = [11, 23, 42, 1337, 2026]


def seeds_from_env(default: list[int] | None = None) -> list[int]:
    raw = os.environ.get("PERMITA38_E2E_SEED")
    if raw:
        return [int(x) for x in raw.replace(",", " ").split()]
    return list(default or DEFAULT_SEEDS)


ARTIFACTS = Path(__file__).resolve().parents[3] / "test-artifacts"


def report_failure(kind: str, seed: int, log: list[str], tail: int = 12) -> str:
    """Keep failure output short: the full step log goes to test-artifacts/, the message carries the replay command
    and only the last few steps (a long dump costs a reader a lot and rarely helps more than the tail)."""
    ARTIFACTS.mkdir(exist_ok=True)
    path = ARTIFACTS / f"e2e-{kind}-seed{seed}.log"
    path.write_text("\n".join(log) + "\n", encoding="utf-8")
    return (
        f"replay: PERMITA38_E2E_SEED={seed} pytest tests/web/e2e_random -k {kind}\n"
        f"full log ({len(log)} steps): {path.relative_to(path.parents[1])}\nlast steps:\n" + "\n".join(log[-tail:])
    )


def env_int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def money(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def eur(x) -> str:
    """German euro formatting like `Intl.NumberFormat('de-DE', {style:'currency',currency:'EUR'})`."""
    s = f"{money(x):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return s + " €"


def norm(text: str) -> str:
    """Normalise whitespace (the browser uses NBSP before the euro sign)."""
    for ch in NBSP:
        text = text.replace(ch, " ")
    return " ".join(text.split())


def hm(minutes: float) -> str:
    """Hours as the UI shows them: H:MM."""
    m = round(abs(minutes))
    return ("-" if minutes < 0 else "") + f"{m // 60}:{m % 60:02d}"


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def parse_duration(text: str) -> int:
    """Independent spec of the duration formats the entry form accepts, in minutes."""
    t = text.strip().lower().replace(",", ".")
    if ":" in t:
        h, m = t.split(":")
        return int(h) * 60 + int(m)
    if t.endswith("m") and "h" not in t:
        return int(t[:-1])
    if "h" in t:
        h, rest = t.split("h")
        return round(float(h) * 60) + (int(rest.strip().rstrip("m")) if rest.strip() else 0)
    return round(float(t) * 60)


def week_target_minutes(day: date, weekly_hours: float, absent_days: set[date], state: str = "") -> int:
    """Expected weekly target: weekly hours spread over Mon-Fri minus public holidays and time off."""
    ws = week_start(day)
    hol = holidays(ws.year, state) | holidays((ws + timedelta(days=6)).year, state)
    working = 0
    for i in range(5):
        d = ws + timedelta(days=i)
        if d not in hol and d not in absent_days:
            working += 1
    return round(weekly_hours * 60 / 5 * working)


def pick_weighted(rng: random.Random, items: list[tuple[str, int]]) -> str:
    names, weights = zip(*items, strict=True)
    return rng.choices(names, weights=weights, k=1)[0]


WORDS = [
    "Nordlicht",
    "Brandt",
    "Müller",
    "Kaffee",
    "Atlas",
    "Fjord",
    "Lindner",
    "Hopfen",
    "Quelle",
    "Aurora",
    "Kiefer",
    "Öko",
    "Bärlauch",
    "Sonnen",
    "Wiesen",
    "Zeitgeist",
    "Pixel",
    "Handwerk",
    "Köhler",
    "Strauß",
]
SUFFIXES = ["GmbH", "UG", "& Söhne", "Studio", "AG", "e.K.", "Consulting"]


def company(rng: random.Random, n: int) -> str:
    return f"{rng.choice(WORDS)} {rng.choice(WORDS)} {rng.choice(SUFFIXES)} {n}"
