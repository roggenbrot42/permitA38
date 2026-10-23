"""Progressive Web App support: the service worker must be served from the site root with the app version
injected (a new release changes the bytes, which is what makes browsers install the updated worker)."""

import mimetypes

from fastapi import FastAPI, Response

from permita38 import __version__
from permita38.core.config import Config

VERSION_PLACEHOLDER = "__APP_VERSION__"


def mount_pwa(app: FastAPI, config: Config) -> None:
    mimetypes.add_type("application/manifest+json", ".webmanifest")
    if not (config.serve_web and config.web_dir and (config.web_dir / "sw.js").is_file()):
        return
    worker = config.web_dir / "sw.js"

    @app.get("/sw.js", include_in_schema=False)
    def service_worker() -> Response:
        body = worker.read_text(encoding="utf-8").replace(VERSION_PLACEHOLDER, __version__)
        return Response(
            body,
            media_type="application/javascript",
            headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"},
        )
