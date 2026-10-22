"""Responses for data that was read back from the database."""

import copy
import json
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel


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


def with_defaults(model: type[BaseModel], data: Any) -> Any:
    """Fill the model's non-null default values into records that lack them (e.g. ``personal: false``), the way a
    validated response model would, but without validating anything else."""
    defaults = {
        name: f.get_default(call_default_factory=True)
        for name, f in model.model_fields.items()
        if not f.is_required() and f.get_default(call_default_factory=True) is not None
    }

    def fill(rec: Any) -> Any:
        if not isinstance(rec, dict):
            return rec
        return {**{k: copy.deepcopy(v) for k, v in defaults.items() if k not in rec}, **rec}

    return [fill(r) for r in data] if isinstance(data, list) else fill(data)


def stored(model: type[BaseModel], data: Any) -> StoredJSON:
    return StoredJSON(with_defaults(model, data))
