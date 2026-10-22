"""Shared helpers for the Impressum tests: fixture loading and a fake (network-free) fetcher."""

from pathlib import Path

from permita38.core.netsafe import FetchedPage, FetchError, RequestBudget

FIXTURES = Path(__file__).parent / "fixtures" / "impressum"


def fixture_bytes(name: str) -> bytes:
    return (FIXTURES / f"{name}.html").read_bytes()


def page(url: str, fixture: str | None = None, body: bytes | None = None) -> FetchedPage:
    data = body if body is not None else fixture_bytes(fixture or "")
    return FetchedPage(url=url, status=200, content_type="text/html", body=data)


class FakeFetcher:
    """Maps URLs to pages or errors; records every request and honours the shared request budget."""

    def __init__(self, routes: dict[str, "FetchedPage | FetchError"]):
        self.routes = routes
        self.calls: list[str] = []

    def fetch(self, url: str, budget: RequestBudget | None = None) -> FetchedPage:
        if budget is not None:
            budget.spend()
        self.calls.append(url)
        hit = self.routes.get(url)
        if hit is None:
            raise FetchError("http_error", "The website answered with HTTP status 404.")
        if isinstance(hit, FetchError):
            raise hit
        return hit
