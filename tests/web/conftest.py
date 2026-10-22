"""Everything under tests/web drives the web UI (or the live HTTP server) and carries the `web` marker."""

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "/tests/web/" in str(item.fspath).replace("\\", "/"):
            item.add_marker(pytest.mark.web)
