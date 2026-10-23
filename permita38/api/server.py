"""FastAPI application factory."""

from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from permita38 import __version__
from permita38.core.config import Config
from permita38.core.errors import ServiceError

from .deps import Services, startup_services
from .routers import (
    auth,
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
    time,
    totp,
    users,
)

CSP = "default-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; frame-ancestors 'none'"


def _clean_msg(msg: str) -> str:
    msg = msg.removeprefix("Value error, ")
    return msg[:300]


def get_app(config: Config) -> FastAPI:
    services = Services.create(config)

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
        return JSONResponse({"detail": exc.message}, status_code=exc.status)

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

    for module in (
        health,
        auth,
        totp,
        users,
        time,
        planning,
        finance,
        settings,
        files,
        sales,
        numbering,
        imports,
        impressum,
        records,
    ):
        for router in module.routers:
            app.include_router(router, prefix="/api")

    if config.serve_web and config.web_dir and config.web_dir.is_dir():
        app.mount("/", StaticFiles(directory=config.web_dir, html=True), name="web")
    return app
