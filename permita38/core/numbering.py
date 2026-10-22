"""Document number patterns and sequence periods (pure functions, no storage).

A pattern is literal text plus tokens::

    {year}   four-digit year          2026
    {yy}     two-digit year           26
    {month}  two-digit month          03
    {n}      the sequence number      43
    {n:05d}  the sequence, zero-padded to 5 digits

Examples: ``RE-{year}-{n:04d}`` -> ``RE-2026-0043``, ``{yy}{month}-{n:03d}`` -> ``2603-043``.

Literal text may contain letters (including umlauts), digits, space and ``- _ / . #``. Exactly one number token is
required. Patterns with an unpadded number or the year after the number do not sort chronologically; see
:func:`sorting_warnings`.

The sequence of a document type restarts according to its *reset scope*: ``never``, ``yearly`` or ``monthly``
(see :func:`period_key`). Storage of the counters lives in :mod:`permita38.core.service.numbering`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

RESET_SCOPES = ("never", "yearly", "monthly")
MAX_PATTERN_LENGTH = 100
MAX_LITERAL_LENGTH = 40
MAX_NUMBER = 1_000_000_000
_LITERAL_OK = re.compile(r"^[0-9A-Za-zÄÖÜäöüß \-_/.#]*$")
_TOKEN_RE = re.compile(r"\{([^{}]*)\}")
_TOKEN_BODY = re.compile(r"^(?:year|yy|month|n(?::0([1-9])d)?)$")


class PatternError(ValueError):
    """The pattern is not allowed; the message is meant for the user."""


@dataclass(frozen=True)
class Token:
    kind: str  # "year", "yy", "month" or "n"
    width: int = 0  # zero-padding for "n"


@dataclass(frozen=True)
class Pattern:
    text: str
    parts: tuple  # literal strings and Token objects

    @property
    def number_token(self) -> Token:
        return next(p for p in self.parts if isinstance(p, Token) and p.kind == "n")

    def has(self, kind: str) -> bool:
        return any(isinstance(p, Token) and p.kind == kind for p in self.parts)

    def format(self, day: date, n: int) -> str:
        out = []
        for p in self.parts:
            if isinstance(p, str):
                out.append(p)
            elif p.kind == "year":
                out.append(f"{day.year:04d}")
            elif p.kind == "yy":
                out.append(f"{day.year % 100:02d}")
            elif p.kind == "month":
                out.append(f"{day.month:02d}")
            else:
                out.append(f"{n:0{p.width}d}" if p.width else str(n))
        return "".join(out)

    def match(self, text: str) -> int | None:
        """The sequence number of ``text`` if it was produced by this pattern (any date), else None."""
        rx = self._regex()
        m = rx.fullmatch(text)
        if not m:
            return None
        value = int(m.group("n"))
        return value if 0 < value <= MAX_NUMBER else None

    def _regex(self) -> re.Pattern[str]:
        pieces = []
        for p in self.parts:
            if isinstance(p, str):
                pieces.append(re.escape(p))
            elif p.kind == "year":
                pieces.append(r"\d{4}")
            elif p.kind in ("yy", "month"):
                pieces.append(r"\d{2}")
            else:
                pieces.append(r"(?P<n>\d+)")
        return re.compile("".join(pieces))


def parse_pattern(text: str) -> Pattern:
    """Validate and parse a pattern; raises :class:`PatternError` with a user-facing message."""
    if not isinstance(text, str) or not text:
        raise PatternError("The numbering pattern must not be empty.")
    if len(text) > MAX_PATTERN_LENGTH:
        raise PatternError(f"The numbering pattern may be at most {MAX_PATTERN_LENGTH} characters long.")
    parts: list = []
    pos = 0
    for m in _TOKEN_RE.finditer(text):
        literal = text[pos : m.start()]
        _check_literal(literal)
        if literal:
            parts.append(literal)
        body = m.group(1)
        t = _TOKEN_BODY.match(body)
        if not t:
            raise PatternError(f"Unknown token {{{body}}}. Use {{year}}, {{yy}}, {{month}}, {{n}} or {{n:05d}}.")
        if body.startswith("n"):
            width = int(t.group(1)) if t.group(1) else 0
            parts.append(Token("n", width))
        else:
            parts.append(Token(body))
        pos = m.end()
    tail = text[pos:]
    if "{" in tail or "}" in tail:
        raise PatternError("Braces must belong to a token such as {n} or {year}.")
    _check_literal(tail)
    if tail:
        parts.append(tail)
    numbers = [p for p in parts if isinstance(p, Token) and p.kind == "n"]
    if len(numbers) != 1:
        raise PatternError("The pattern must contain the number token {n} (or {n:05d}) exactly once.")
    return Pattern(text, tuple(parts))


def _check_literal(literal: str) -> None:
    if literal and not _LITERAL_OK.match(literal):
        raise PatternError("Only letters, digits, spaces and the characters - _ / . # may appear outside the tokens.")
    if len(literal) > MAX_LITERAL_LENGTH:
        raise PatternError(f"Text next to a token may be at most {MAX_LITERAL_LENGTH} characters long.")


def period_key(reset: str, day: date) -> str:
    """The counter period a document dated ``day`` belongs to for the given reset scope."""
    if reset == "never":
        return "all"
    if reset == "yearly":
        return f"{day.year:04d}"
    if reset == "monthly":
        return f"{day.year:04d}-{day.month:02d}"
    raise ValueError(f"unknown reset scope {reset!r}")


def sorting_warnings(pattern: Pattern, reset: str) -> list[str]:
    """Human-readable warnings about patterns that break chronological sorting or the reset."""
    out = []
    num = pattern.number_token
    if num.width == 0:
        out.append("The number is not zero-padded (e.g. {n:04d}); 10 would sort before 9.")
    year_tokens = [i for i, p in enumerate(pattern.parts) if isinstance(p, Token) and p.kind in ("year", "yy", "month")]
    num_index = pattern.parts.index(num)
    if year_tokens and max(year_tokens) > num_index:
        out.append("A date token comes after the number; sorting by number would not be chronological.")
    if reset != "never" and not pattern.has("year") and not pattern.has("yy"):
        out.append("The number restarts yearly or monthly but the pattern has no year: numbers would repeat.")
    return out


def normalize_text(text: str) -> str:
    """Canonical form used when comparing numbers typed by people (whitespace collapsed, case kept)."""
    return re.sub(r"\s+", " ", text or "").strip()
