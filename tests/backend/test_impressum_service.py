"""Orchestration of an Impressum lookup (fake fetcher, no network)."""

import pytest

from permita38.core.errors import ServiceError
from permita38.core.netsafe import BlockedAddress, FetchError
from permita38.core.service.impressum import ImpressumService

from .impressum_helpers import FakeFetcher, page

SITE = "https://www.teilbar.example"


def service(routes, **kw):
    fetcher = FakeFetcher(routes)
    return ImpressumService(fetcher, **kw), fetcher


def test_direct_impressum_url_needs_one_request():
    svc, fetcher = service(
        {"https://beispiel.example/impressum": page("https://beispiel.example/impressum", "gmbh_register")}
    )
    result = svc.lookup("u1", "https://beispiel.example/impressum")
    assert fetcher.calls == ["https://beispiel.example/impressum"]
    assert result["fields"]["name"] == "Beispiel Software GmbH" and result["fields"]["vat_id"] == "DE123456788"
    assert result["impressum_url"] == result["source_url"] == "https://beispiel.example/impressum"
    assert set(result) == {"source_url", "impressum_url", "fields", "confidence", "evidence", "candidates", "warnings"}


def test_scheme_is_added_when_missing():
    svc, fetcher = service({"https://beispiel.example": page("https://beispiel.example", "gmbh_register")})
    svc.lookup("u1", "  beispiel.example ")
    assert fetcher.calls == ["https://beispiel.example"]


def test_home_page_links_to_impressum_which_links_to_contact_page():
    routes = {
        SITE + "/": page(SITE + "/", "split_home"),
        SITE + "/rechtliches/impressum.html": page(SITE + "/rechtliches/impressum.html", "split_impressum"),
        SITE + "/kontakt": page(SITE + "/kontakt", "split_kontakt"),
    }
    svc, fetcher = service(routes)
    result = svc.lookup("u1", SITE + "/")
    assert fetcher.calls == [SITE + "/", SITE + "/rechtliches/impressum.html", SITE + "/kontakt"]
    f = result["fields"]
    assert (f["name"], f["street"], f["zip"], f["city"]) == ("Teilbar GmbH", "Musterring 1", "99084", "Erfurt")
    assert (f["email"], f["phone"]) == ("team@example.com", "03615550000")  # merged from the second page
    assert result["impressum_url"] == SITE + "/rechtliches/impressum.html"


def test_usual_addresses_are_tried_when_the_page_has_no_link():
    routes = {
        "https://x.example/": page("https://x.example/", "no_impressum"),
        "https://x.example/impressum": page("https://x.example/impressum", "gmbh_register"),
    }
    svc, fetcher = service(routes)
    result = svc.lookup("u1", "https://x.example/")
    assert fetcher.calls == ["https://x.example/", "https://x.example/impressum"]
    assert result["fields"]["city"] == "München"
    assert any("usual addresses" in w for w in result["warnings"])


def test_nothing_found_gives_a_friendly_404():
    svc, _ = service({"https://x.example/": page("https://x.example/", "no_impressum")})
    with pytest.raises(ServiceError) as exc:
        svc.lookup("u1", "https://x.example/")
    assert exc.value.status == 404 and "Impressum" in exc.value.message


def test_javascript_only_site_gets_a_specific_message():
    svc, _ = service({"https://app.example/": page("https://app.example/", "js_app")})
    with pytest.raises(ServiceError) as exc:
        svc.lookup("u1", "https://app.example/")
    assert exc.value.status == 404 and "JavaScript" in exc.value.message


def test_json_ld_only_page_is_not_reported_as_an_error():
    svc, _ = service({"https://app.example/": page("https://app.example/", "jsonld_only")})
    assert svc.lookup("u1", "https://app.example/")["fields"]["city"] == "Stuttgart"


def test_links_to_other_sites_are_never_followed():
    home = b'<html><a href="https://evil.example/impressum">Impressum</a><a href="http://169.254.169.254/impressum">Impressum</a></html>'
    svc, fetcher = service({"https://x.example/": page("https://x.example/", body=home)})
    with pytest.raises(ServiceError):
        svc.lookup("u1", "https://x.example/")
    assert all("evil.example" not in c and "169.254" not in c for c in fetcher.calls)


def test_subdomain_and_www_variants_count_as_the_same_site():
    home = b'<a href="https://x.example/impressum">Impressum</a>'
    routes = {
        "https://www.x.example/": page("https://www.x.example/", body=home),
        "https://x.example/impressum": page("https://x.example/impressum", "gmbh_register"),
    }
    svc, fetcher = service(routes)
    assert svc.lookup("u1", "https://www.x.example/")["fields"]["zip"] == "80331"
    assert fetcher.calls[-1] == "https://x.example/impressum"


def test_request_budget_is_never_exceeded():
    links = "".join(f'<a href="/impressum-{i}">Impressum</a>' for i in range(30)).encode()
    routes = {"https://x.example/": page("https://x.example/", body=links)}
    svc, fetcher = service(routes)
    with pytest.raises(ServiceError):
        svc.lookup("u1", "https://x.example/")
    assert len(fetcher.calls) <= 5


def test_failing_candidate_pages_are_skipped_but_not_the_first_page():
    home = b'<a href="/impressum">Impressum</a><a href="/legal-notice">Legal notice</a>'
    routes = {
        "https://x.example/": page("https://x.example/", body=home),
        "https://x.example/impressum": FetchError("timeout", "slow"),
        "https://x.example/legal-notice": page("https://x.example/legal-notice", "english_imprint"),
    }
    svc, _ = service(routes)
    assert svc.lookup("u1", "https://x.example/")["fields"]["name"] == "Example Consulting Ltd."


@pytest.mark.parametrize(
    "code,status",
    [
        ("invalid_url", 422), ("blocked_address", 400), ("dns_error", 502), ("timeout", 504), ("network_error", 502),
        ("tls_error", 502), ("too_large", 502), ("bad_content_type", 422), ("http_error", 502),
        ("redirect_loop", 502), ("too_many_redirects", 502), ("budget_exceeded", 502), ("something_new", 502),
    ],
)  # fmt: skip
def test_fetch_errors_map_to_friendly_http_errors(code, status):
    svc, _ = service({"https://x.example/": FetchError(code, "message for the user")})
    with pytest.raises(ServiceError) as exc:
        svc.lookup("u1", "https://x.example/")
    assert exc.value.status == status and exc.value.message == "message for the user"


def test_blocked_address_error_is_reported_as_400():
    svc, _ = service({"https://x.example/": BlockedAddress()})
    with pytest.raises(ServiceError) as exc:
        svc.lookup("u1", "https://x.example/")
    assert exc.value.status == 400


def test_redirect_to_another_website_is_flagged():
    routes = {"https://old.example/": page("https://new.example/impressum", "gmbh_register")}
    svc, _ = service(routes)
    result = svc.lookup("u1", "https://old.example/")
    assert any("different website" in w and "new.example" in w for w in result["warnings"])
    assert result["source_url"] == "https://new.example/impressum"


def test_disabled_feature_refuses_before_touching_the_network():
    svc, fetcher = service({}, enabled=False)
    with pytest.raises(ServiceError) as exc:
        svc.lookup("u1", "https://x.example/")
    assert exc.value.status == 403 and fetcher.calls == []


def test_rate_limit_is_per_user_and_expires():
    now = [1000.0]
    routes = {"https://x.example/": page("https://x.example/", "gmbh_register")}
    svc, fetcher = service(routes, rate_limit=(3, 60.0), clock=lambda: now[0])
    for _ in range(3):
        svc.lookup("u1", "https://x.example/")
    with pytest.raises(ServiceError) as exc:
        svc.lookup("u1", "https://x.example/")
    assert exc.value.status == 429
    calls_before = len(fetcher.calls)
    svc.lookup("u2", "https://x.example/")  # another user is not affected
    assert len(fetcher.calls) == calls_before + 1
    now[0] += 61
    svc.lookup("u1", "https://x.example/")  # window has passed


def test_invalid_urls_are_reported_before_any_request_is_counted_as_success():
    svc, fetcher = service({}, rate_limit=(100, 60.0))
    svc2 = ImpressumService(__import__("permita38.core.netsafe", fromlist=["SafeFetcher"]).SafeFetcher("t"))
    for bad in ("", "http://127.0.0.1/", "file:///etc/passwd", "http://localhost/", "ftp://example.com"):
        with pytest.raises(ServiceError) as exc:
            svc2.lookup("u1", bad)
        assert exc.value.status in (400, 422), bad
    assert fetcher.calls == []
