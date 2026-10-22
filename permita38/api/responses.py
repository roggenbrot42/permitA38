"""Responses for data that was read back from the database."""

import json
from typing import Any

from fastapi.responses import JSONResponse


class StoredJSON(JSONResponse):
    """JSON of stored records, sent **without** response-model validation.

    Request bodies are validated strictly, but a record in the database may come from an older version or from
    another device through file sync. Re-validating such a record on the way out would turn one damaged row into
    an HTTP 500 for *every* list request. The ``response_model`` on the route still documents the shape in the
    OpenAPI contract; this class only skips the re-validation. ASCII-only output keeps even damaged text (e.g. a
    lone surrogate) serialisable.
    """

    def render(self, content: Any) -> bytes:
        return json.dumps(content, ensure_ascii=True, allow_nan=False, separators=(",", ":"), default=str).encode()
