"""Look up customer master data from the Impressum (legal notice) of a company website.

Read-only: nothing is stored. The caller (web UI) shows the result and the user decides what to keep.
"""

import threading
import time
from collections import defaultdict, deque
from collections.abc import Callable
from urllib.parse import urlsplit

from permita38.core.errors import ServiceError
from permita38.core.impressum_parser import (
    Candidate,
    Extracted,
    Page,
    clean,
    decode_html,
    extract_fields,
    find_impressum_links,
    is_impressum_page,
    parse_html,
)
from permita38.core.netsafe import FetchedPage, Fetcher, FetchError, RequestBudget, same_site

MAX_REQUESTS = 5
MAX_FOLLOW_UPS = 3
RATE_LIMIT = (10, 60.0)  # lookups per user per window (seconds)
FALLBACK_PATHS = ("/impressum", "/imprint", "/impressum.html")
FIELDS = (
    "name", "legal_form", "street", "zip", "city", "country", "email", "phone", "vat_id", "tax_number",
    "register_court", "register_number", "managing_directors", "contact",
)  # fmt: skip

_SKIPPABLE = frozenset({"http_error", "bad_content_type", "dns_error", "network_error", "timeout"})
_ERROR_STATUS = {
    "invalid_url": 422,
    "blocked_address": 400,
    "dns_error": 502,
    "timeout": 504,
    "network_error": 502,
    "tls_error": 502,
    "too_large": 502,
    "bad_content_type": 422,
    "http_error": 502,
    "redirect_loop": 502,
    "too_many_redirects": 502,
    "budget_exceeded": 502,
}


class ImpressumService:
    def __init__(
        self,
        fetcher: Fetcher,
        *,
        enabled: bool = True,
        rate_limit: tuple[int, float] = RATE_LIMIT,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.fetcher = fetcher
        self.enabled = enabled
        self.rate_limit = rate_limit
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    # ---------------------------------------------------------------------------------------------
    def lookup(self, user_id: str, url: str) -> dict:
        if not self.enabled:
            raise ServiceError(403, "The Impressum lookup is disabled in the server configuration.")
        self._throttle(user_id)
        url = (url or "").strip()
        if url and "://" not in url:
            url = "https://" + url
        budget = RequestBudget(MAX_REQUESTS)
        warnings: list[str] = []
        first = self._fetch(url, budget)
        pages: list[tuple[str, Page]] = []
        first_page = self._parse(first)
        base_site = first.url
        if urlsplit(first.url).hostname != urlsplit(url).hostname and not same_site(first.url, url):
            warnings.append(f"The address redirects to a different website: {urlsplit(first.url).hostname}.")
        pages.append((first.url, first_page))

        impressum_url = first.url if is_impressum_page(first_page, first.url) else None
        extracted = [extract_fields(first_page)] if impressum_url or _has_signal(first_page) else []
        if first_page.script_only:
            warnings.append(
                "The page is built with JavaScript and has no readable text. Enter the Impressum page address."
            )

        tried = {first.url}
        queue: list[str] = []

        def enqueue(from_page: Page, from_url: str) -> None:
            for link, _score in find_impressum_links(from_page, from_url):
                if link not in tried and link not in queue and same_site(link, base_site):
                    queue.append(link)

        if impressum_url is not None or extracted:
            enqueue(first_page, first.url)
        else:
            enqueue(first_page, first.url)
            if not queue:
                origin = f"{urlsplit(first.url).scheme}://{urlsplit(first.url).netloc}"
                queue = [origin + p for p in FALLBACK_PATHS]
                warnings.append("No Impressum link was found on the page; tried the usual addresses.")
        follow = 0
        while queue and follow < MAX_FOLLOW_UPS and budget.used < MAX_REQUESTS and not self._complete(extracted):
            cand = queue.pop(0)
            if cand in tried:
                continue
            tried.add(cand)
            follow += 1
            page = self._try_fetch(cand, budget)
            if page is None:
                continue
            tried.add(page.url)
            parsed = self._parse(page)
            pages.append((page.url, parsed))
            if is_impressum_page(parsed, page.url) or _has_signal(parsed, FOLLOW_UP_SIGNALS):
                impressum_url = impressum_url or page.url
                extracted.append(extract_fields(parsed))
                enqueue(parsed, page.url)
        if not extracted or not any(e.candidates for e in extracted):
            if first_page.script_only:
                raise ServiceError(
                    404,
                    "The page is built with JavaScript and has no readable text. "
                    "Paste the address of the Impressum page itself.",
                )
            raise ServiceError(
                404,
                "No Impressum was found on this website. Open the Impressum page in your browser and paste its address.",
            )
        return self._merge(first.url, impressum_url, extracted, warnings)

    # ---------------------------------------------------------------------------------------------
    def _throttle(self, user_id: str) -> None:
        limit, window = self.rate_limit
        now = self._clock()
        with self._lock:
            hits = self._hits[user_id]
            while hits and now - hits[0] > window:
                hits.popleft()
            if len(hits) >= limit:
                raise ServiceError(429, "Too many lookups. Please wait a minute and try again.")
            hits.append(now)

    def _fetch(self, url: str, budget: RequestBudget) -> FetchedPage:
        try:
            return self.fetcher.fetch(url, budget)
        except FetchError as e:
            raise ServiceError(_ERROR_STATUS.get(e.code, 502), e.message) from None

    def _try_fetch(self, url: str, budget: RequestBudget) -> FetchedPage | None:
        """A follow-up page that is missing, slow or not HTML is not an error – it is simply skipped."""
        try:
            return self.fetcher.fetch(url, budget)
        except FetchError as e:
            if e.code in _SKIPPABLE:
                return None
            raise ServiceError(_ERROR_STATUS.get(e.code, 502), e.message) from None

    @staticmethod
    def _parse(page: FetchedPage) -> Page:
        return parse_html(decode_html(page.body, page.charset))

    @staticmethod
    def _complete(extracted: list[Extracted]) -> bool:
        have = {k for e in extracted for k, v in e.candidates.items() if v}
        return {"name", "street", "zip", "city"} <= have and bool({"email", "phone"} & have)

    @staticmethod
    def _merge(source_url: str, impressum_url: str | None, extracted: list[Extracted], warnings: list[str]) -> dict:
        by_field: dict[str, list[Candidate]] = {}
        for ex in extracted:
            for w in ex.warnings:
                if w not in warnings:
                    warnings.append(w)
            for name, cands in ex.candidates.items():
                lst = by_field.setdefault(name, [])
                for c in cands:
                    existing = next((x for x in lst if x.value == c.value), None)
                    if existing is None:
                        lst.append(c)
                    elif c.confidence > existing.confidence:
                        existing.confidence, existing.evidence, existing.source = c.confidence, c.evidence, c.source
        fields: dict = {}
        confidence: dict[str, float] = {}
        evidence: dict[str, str] = {}
        candidates: dict[str, list[dict]] = {}
        for name in FIELDS:
            lst = sorted(by_field.get(name, []), key=lambda c: -c.confidence)
            if not lst:
                continue
            best = lst[0]
            fields[name] = best.value
            confidence[name] = best.confidence
            evidence[name] = best.evidence
            if len(lst) > 1:
                candidates[name] = [
                    {"value": c.value, "confidence": c.confidence, "evidence": c.evidence} for c in lst[1:6]
                ]
        return {
            "source_url": clean(source_url, 500),
            "impressum_url": clean(impressum_url, 500) if impressum_url else None,
            "fields": fields,
            "confidence": confidence,
            "evidence": evidence,
            "candidates": candidates,
            "warnings": warnings,
        }


FIRST_PAGE_SIGNALS = frozenset({"zip", "vat_id"})
FOLLOW_UP_SIGNALS = frozenset({"zip", "vat_id", "email", "phone"})  # pages the Impressum links to (e.g. Kontakt)


def _has_signal(page: Page, wanted: frozenset[str] = FIRST_PAGE_SIGNALS) -> bool:
    return bool(wanted & set(extract_fields(page).candidates))
