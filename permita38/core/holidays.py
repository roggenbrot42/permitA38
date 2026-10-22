"""German public holidays per federal state (Bundesland)."""

from datetime import date, timedelta

STATES = {
    "": "Nationwide only",
    "BW": "Baden-Württemberg",
    "BY": "Bayern",
    "BE": "Berlin",
    "BB": "Brandenburg",
    "HB": "Bremen",
    "HH": "Hamburg",
    "HE": "Hessen",
    "MV": "Mecklenburg-Vorpommern",
    "NI": "Niedersachsen",
    "NW": "Nordrhein-Westfalen",
    "RP": "Rheinland-Pfalz",
    "SL": "Saarland",
    "SN": "Sachsen",
    "ST": "Sachsen-Anhalt",
    "SH": "Schleswig-Holstein",
    "TH": "Thüringen",
}


def easter(y):
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    lz = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * lz) // 451
    month = (h + lz - 7 * m + 114) // 31
    day = (h + lz - 7 * m + 114) % 31 + 1
    return date(y, month, day)


def holidays(year, state=""):
    e = easter(year)
    h = {
        date(year, 1, 1): "Neujahr",
        e - timedelta(days=2): "Karfreitag",
        e + timedelta(days=1): "Ostermontag",
        date(year, 5, 1): "Tag der Arbeit",
        e + timedelta(days=39): "Christi Himmelfahrt",
        e + timedelta(days=50): "Pfingstmontag",
        date(year, 10, 3): "Tag der Deutschen Einheit",
        date(year, 12, 25): "1. Weihnachtstag",
        date(year, 12, 26): "2. Weihnachtstag",
    }
    if state in ("BW", "BY", "ST"):
        h[date(year, 1, 6)] = "Heilige Drei Könige"
    if state == "BE" or (state == "MV" and year >= 2023):
        h[date(year, 3, 8)] = "Internationaler Frauentag"
    if state in ("BW", "BY", "HE", "NW", "RP", "SL"):
        h[e + timedelta(days=60)] = "Fronleichnam"
    if state == "SL":
        h[date(year, 8, 15)] = "Mariä Himmelfahrt"
    if state == "TH":
        h[date(year, 9, 20)] = "Weltkindertag"
    if state in ("BB", "HB", "HH", "MV", "NI", "SN", "ST", "SH", "TH"):
        h[date(year, 10, 31)] = "Reformationstag"
    if state in ("BW", "BY", "NW", "RP", "SL"):
        h[date(year, 11, 1)] = "Allerheiligen"
    if state == "SN":
        d = date(year, 11, 22)  # Wednesday before Nov 23
        while d.weekday() != 2:
            d -= timedelta(days=1)
        h[d] = "Buß- und Bettag"
    return h
