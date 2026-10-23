"""The caller of the current request, made available to services without threading a parameter through every call.

The API middleware sets it before the endpoint runs; services read it (``None`` for the CLI and for tests that call
a service directly, in which case only the per-account limits apply)."""

from contextvars import ContextVar

from permita38.core.netinfo import ClientInfo

current_client: ContextVar[ClientInfo | None] = ContextVar("current_client", default=None)


def get_client() -> ClientInfo | None:
    return current_client.get()
