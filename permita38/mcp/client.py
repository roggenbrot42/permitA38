"""HTTP client of the permitA38 API for the MCP server (docs/specs/mcp-server.md, "MCP is an HTTP-API client").

Read-only by construction: the client has a ``get`` method and no other verb. The token travels only in the
``Authorization`` header; it is never logged, put in a URL or in an error message.
"""

import asyncio
import ipaddress
import logging
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("permita38.mcp")

TIMEOUT = 10.0
CONCURRENCY = 4
TOKEN_RE = re.compile(r"^pat_[A-Za-z0-9_-]{43}$")
DEFAULT_URL = "http://127.0.0.1:8765"


class ApiError(Exception):
    """An API call failed. ``message`` is safe to show to a model (no token, no response body)."""

    def __init__(self, status: int | None, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.retry_after = retry_after


class ApiClient(Protocol):
    """What the tools need: JSON GET against the API. Tests substitute a fake."""

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any: ...


def check_url(url: str) -> str:
    """The base URL without a trailing slash. Plain http is only accepted for loopback, everything else needs https."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError("PERMITA38_URL must be an http(s) URL such as http://127.0.0.1:8765")
    if parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("PERMITA38_URL must not contain credentials, a query or a fragment")
    if parts.scheme == "http" and not _is_loopback(parts.hostname):
        raise ValueError("PERMITA38_URL: plain http is only allowed for 127.0.0.1, ::1 and localhost; use https")
    return url.rstrip("/")


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def check_token(token: str) -> str:
    token = token.strip()
    if not TOKEN_RE.match(token):
        raise ValueError("the token is not a permitA38 API token (expected pat_ followed by 43 characters)")
    return token


class HttpApiClient:
    """``ApiClient`` over httpx: fixed base URL, no proxies from the environment, no redirects, 10 s timeout, at most
    4 requests at a time."""

    def __init__(self, base_url: str, token: str, transport: httpx.AsyncBaseTransport | None = None):
        self._http = httpx.AsyncClient(
            base_url=check_url(base_url),
            headers={"Authorization": f"Bearer {check_token(token)}", "Accept": "application/json"},
            timeout=TIMEOUT,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )
        self._slots = asyncio.Semaphore(CONCURRENCY)
        # httpx logs every request URL at INFO; the server logs tool name, status and duration itself
        logging.getLogger("httpx").setLevel(logging.WARNING)
        logging.getLogger("httpcore").setLevel(logging.WARNING)

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        try:
            async with self._slots:
                resp = await self._http.get(path, params=clean)
        except httpx.TimeoutException:
            raise ApiError(None, "The permitA38 server did not answer within 10 seconds") from None
        except httpx.HTTPError:
            raise ApiError(None, "The permitA38 server cannot be reached; is `permita38 serve` running?") from None
        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                raise ApiError(resp.status_code, "The permitA38 server sent an answer that is not JSON") from None
        raise _error(resp)


def _error(resp: httpx.Response) -> ApiError:
    status = resp.status_code
    if status == 401:
        return ApiError(status, "The API token is invalid, expired or revoked")
    if status == 403:
        return ApiError(status, "The token is not allowed to read this")
    if status == 404:
        return ApiError(status, "Not found")
    if status == 429:
        try:
            wait = int(resp.headers.get("Retry-After", ""))
        except ValueError:
            wait = None
        return ApiError(status, "Too many requests; try again later", retry_after=wait)
    if 300 <= status < 400:
        return ApiError(status, "The permitA38 server answered with a redirect, which is not followed")
    if status in (400, 422):
        return ApiError(status, "The server rejected the request")
    return ApiError(status, f"The permitA38 server answered with status {status}")


def load_token(token_file: Path | None, environ: Mapping[str, str], posix: bool | None = None) -> str:
    """The token from ``--token-file`` (preferred; on POSIX the file must not be accessible to group or others) or
    ``PERMITA38_TOKEN``. Never from a command-line argument. Raises ``ValueError`` with a message that does not
    contain the token."""
    posix = os.name == "posix" if posix is None else posix
    if token_file is not None:
        try:
            mode = token_file.stat().st_mode
            raw = token_file.read_text(encoding="utf-8")
        except OSError as e:
            raise ValueError(f"cannot read the token file: {e.strerror or 'error'}") from None
        if posix and mode & 0o077:
            raise ValueError(f"the token file {token_file} is accessible to other users; run: chmod 600 {token_file}")
        return check_token(raw)
    raw = environ.get("PERMITA38_TOKEN", "")
    if not raw.strip():
        raise ValueError("no token: set PERMITA38_TOKEN or use --token-file PATH (a token is created in the web app)")
    return check_token(raw)
