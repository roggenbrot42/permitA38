"""Browser test: the project picker next to a customer lists only that customer's projects (and general projects
without a customer) in the document editor and the trip dialog; changing the customer clears a project that no longer
fits. A legacy project of another customer stays visible, marked, when an existing record is opened. Demo data only."""

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

from tests.web.test_impressum_prefill import Server, chromium  # noqa: E402,F401  (shared harness)


@pytest.fixture
def srv(tmp_path):
    s = Server(tmp_path)
    yield s
    s.stop()


@pytest.fixture
def world(srv):
    h = {"Authorization": "Bearer " + srv.token}
    post = lambda path, body: httpx.post(srv.base + "/api/" + path, headers=h, json=body).json()  # noqa: E731
    put = lambda path, body: httpx.put(srv.base + "/api/" + path, headers=h, json=body)  # noqa: E731
    acme = post("customers", {"name": "Acme Demo GmbH"})
    other = post("customers", {"name": "Globex Demo Ltd"})
    dated = {"start": "2026-01-01", "end": "2026-12-31"}
    p_acme = post("projects", {"name": "Acme Website", "customer_id": acme["id"], **dated})
    p_other = post("projects", {"name": "Globex Shop", "customer_id": other["id"], **dated})
    p_general = post("projects", {"name": "General Admin", **dated})
    return {
        "h": h,
        "post": post,
        "put": put,
        "acme": acme["id"],
        "other": other["id"],
        "p_acme": p_acme["id"],
        "p_other": p_other["id"],
        "p_general": p_general["id"],
    }


def _ctx(browser, srv):
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    ctx.add_init_script(f"localStorage.setItem('permita38.token', '{srv.token}')")
    return ctx


def _options(pg, sel):
    return pg.eval_on_selector_all(f"{sel} option", "els => els.map(e => e.value)")


def test_document_editor_lists_only_the_chosen_customers_projects(browser, srv, world):
    ctx = _ctx(browser, srv)
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/doc/offers/new")
    pg.wait_for_selector("#d_totals")
    # no customer chosen: only the general project (no customer) is offered
    assert set(_options(pg, "#d_proj")) == {"", world["p_general"]}
    pg.select_option("#d_cust", world["acme"])
    assert set(_options(pg, "#d_proj")) == {"", world["p_acme"], world["p_general"]}
    pg.select_option("#d_proj", world["p_acme"])
    # the customer changes: the project of the other customer is not offered, and the chosen one is cleared
    pg.select_option("#d_cust", world["other"])
    assert set(_options(pg, "#d_proj")) == {"", world["p_other"], world["p_general"]}
    assert pg.input_value("#d_proj") == ""
    # a project that still fits is kept when the customer changes
    pg.select_option("#d_proj", world["p_general"])
    pg.select_option("#d_cust", world["acme"])
    assert pg.input_value("#d_proj") == world["p_general"]
    ctx.close()


def test_legacy_project_of_another_customer_is_shown_marked(browser, srv, world):
    offer = world["post"]("offers", {"customer_id": world["acme"], "project_id": world["p_acme"], "lines": []})
    # the project is moved to another customer after the offer points at it
    world["put"](
        f"projects/{world['p_acme']}",
        {"name": "Acme Website", "customer_id": world["other"], "start": "2026-01-01", "end": "2026-12-31"},
    )
    ctx = _ctx(browser, srv)
    pg = ctx.new_page()
    pg.goto(srv.base + f"/#/doc/offers/{offer['id']}")
    pg.wait_for_selector("#d_totals")
    assert pg.input_value("#d_proj") == world["p_acme"]
    assert "(other customer)" in pg.inner_text("#d_proj option:checked")
    # the other mismatching projects are not offered for this customer
    assert set(_options(pg, "#d_proj")) == {"", world["p_acme"], world["p_general"]}
    ctx.close()


def test_trip_dialog_lists_only_the_chosen_customers_projects(browser, srv, world):
    ctx = _ctx(browser, srv)
    pg = ctx.new_page()
    pg.goto(srv.base + "/#/trips")
    pg.click("[data-a=newTrip]")
    pg.wait_for_selector("#t_project")
    assert set(_options(pg, "#t_project")) == {"", world["p_general"]}
    pg.select_option("#t_customer", world["acme"])
    assert set(_options(pg, "#t_project")) == {"", world["p_acme"], world["p_general"]}
    pg.select_option("#t_project", world["p_acme"])
    pg.select_option("#t_customer", world["other"])
    assert set(_options(pg, "#t_project")) == {"", world["p_other"], world["p_general"]}
    assert pg.input_value("#t_project") == ""
    ctx.close()
