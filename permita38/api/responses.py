"""Responses for data that was read back from the database."""

import copy
import json
import types
import typing
from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel

from permita38.core import money
from permita38.core.validation import DecimalOutput


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


def _decimal_scalar(value: Any) -> Any:
    """A stored amount as a decimal string; ``None``, empty, bool and unparsable values stay as they are."""
    if value is None or value == "" or isinstance(value, bool):
        return value
    try:
        return money.to_decimal_str(value)
    except (ValueError, ArithmeticError):
        return value


def _convert(annotation: Any, value: Any, metadata: tuple[Any, ...] = ()) -> Any:
    """Convert the money in ``value`` according to its type ``annotation`` (recursively); else return it unchanged."""
    if any(isinstance(m, DecimalOutput) for m in metadata):
        return _decimal_scalar(value)
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Annotated:
        return _convert(args[0], value, tuple(args[1:]))
    if origin in (typing.Union, types.UnionType):
        for arg in args:
            out = _convert(arg, value)
            if out is not value:
                return out
        return value
    if origin is list and isinstance(value, list) and args:
        return [_convert(args[0], v) for v in value]
    if origin is dict and isinstance(value, dict) and len(args) == 2:
        return {k: _convert(args[1], v) for k, v in value.items()}
    if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
        return present(annotation, value)
    return value


def present(model: type[BaseModel], data: Any) -> Any:
    """Convert the ``Money`` / ``UnitPrice`` fields of stored record(s) to decimal strings, recursively into nested
    models. Unknown fields, ``None`` and unparsable values are left alone (never an error); the input is not changed."""
    if isinstance(data, list):
        return [present(model, r) for r in data]
    if not isinstance(data, dict):
        return data
    out = dict(data)
    for name, field in model.model_fields.items():
        if name in out:
            out[name] = _convert(field.annotation, out[name], tuple(field.metadata))
    return out


def stored(model: type[BaseModel], data: Any) -> StoredJSON:
    return StoredJSON(present(model, with_defaults(model, data)))
