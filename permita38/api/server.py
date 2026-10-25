"""FastAPI application factory."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from permita38 import __version__
from permita38.core.config import Config
from permita38.core.errors import ServiceError
from permita38.core.service.audit import UNMATCHED_ROUTE
from permita38.core.service.calendar.service import ResourceFetcher

from .deps import AUDIT_SCOPE_KEY, Services, is_session_only, startup_services
from .routers import (
    allocations,
    auth,
    calendars,
    data_transfer,
    files,
    finance,
    health,
    imports,
    impressum,
    numbering,
    planning,
    records,
    sales,
    settings,
    suppliers,
    team,
    time,
    tokens,
    totp,
    trips,
    users,
)

log = logging.getLogger(__name__)
API_PREFIX = "/api"

# blob: is only used by the expense dialog to preview a bill before it is uploaded (see docs/specs/itemised-expenses.md)
CSP = (
    "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; object-src 'none'; "
    "frame-ancestors 'none'; frame-src 'self' blob:"
)


def all_routers() -> list:
    """Every router of the API in registration order (tests enumerate the routes through this)."""
    modules = (
        health,
        auth,
        totp,
        users,
        time,
        planning,
        allocations,
        suppliers,
        finance,
        settings,
        team,
        files,
        sales,
        numbering,
        imports,
        impressum,
        records,
        calendars,
        trips,
        data_transfer,
        tokens,
    )
    return [router for module in modules for router in module.routers]


def _route_template(request: Request) -> str:
    """The matched route's path template (``/api/customers/{rid}``), never the concrete URL."""
    path = getattr(request.scope.get("route"), "path", None)
    if not path:
        return UNMATCHED_ROUTE
    return path if path.startswith(API_PREFIX + "/") else API_PREFIX + path  # included routers keep their own path


def _clean_msg(msg: str) -> str:
    msg = msg.removeprefix("Value error, ")
    return msg[:300]


def get_app(config: Config, *, calendar_fetcher: ResourceFetcher | None = None) -> FastAPI:
    """Build the app. ``calendar_fetcher`` is a test-only seam (never set by the CLI)."""
    services = Services.create(config, calendar_fetcher=calendar_fetcher)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        startup_services(services)
        yield
        services.store.close()

    app = FastAPI(
        lifespan=lifespan,
        title="permitA38 API",
        version=__version__,
        license_info={"name": "GPL-3.0-or-later", "url": "https://www.gnu.org/licenses/gpl-3.0.html"},
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.services = services

    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError):
        return JSONResponse({"detail": exc.message, **exc.body}, status_code=exc.status, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError):
        # Never echo the offending input back (it can be huge or contain unencodable characters); keep the
        # field path and a readable message, which is all a client needs.
        detail = [
            {
                "loc": [x if isinstance(x, int) else str(x) for x in e["loc"]],
                "msg": _clean_msg(e["msg"]),
                "type": e["type"],
            }
            for e in exc.errors()
        ]
        return JSONResponse({"detail": detail}, status_code=422)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if not request.url.path.startswith("/api"):
            response.headers["Content-Security-Policy"] = CSP
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.middleware("http")
    async def audit_token_requests(request: Request, call_next):
        """One audit row per request authenticated by an API token, written after the status is known. A failing
        write is logged (without any credential) and never changes the response."""
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            who = request.scope.get(AUDIT_SCOPE_KEY)
            if who:
                template = _route_template(request)
                try:
                    await run_in_threadpool(services.audit.append, who[0], who[1], request.method, template, status)
                except Exception:
                    log.exception("could not write the audit log")

    for router in all_routers():
        app.include_router(router, prefix=API_PREFIX)

    default_openapi = app.openapi

    def openapi() -> dict:
        """Adds the second credential, the API token, to every operation a token may use."""
        if app.openapi_schema:
            return app.openapi_schema
        schema = default_openapi()
        schema["components"]["securitySchemes"]["ApiToken"] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "pat_",
            "description": "API token (`Authorization: Bearer pat_...`), created in the settings. It has the rights "
            "of its owner, optionally read-only, and can never use the session-only routes.",
        }
        session_only = {
            (API_PREFIX + r.path, m.lower())
            for router in all_routers()
            for r in router.routes
            if is_session_only(r)
            for m in r.methods
        }
        for path, item in schema["paths"].items():
            for method, op in item.items():
                if op.get("security") and (path, method) not in session_only:
                    op["security"].append({"ApiToken": []})
        return schema

    app.openapi = openapi  # type: ignore[method-assign]

    if config.serve_web and config.web_dir and config.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=config.web_dir, html=True), name="web")
    return app
