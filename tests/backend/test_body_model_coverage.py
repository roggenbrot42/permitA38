"""Completeness: every request body of a state-changing route is a CheckedBody, nested models included.

A new route that takes a plain BaseModel (or a bare dict / str body) fails here, so the hard limits cannot be skipped
by accident. Exceptions go into ALLOWED_UNCHECKED with a reason; the list is empty today."""

import tempfile
import typing
from pathlib import Path

from fastapi import params
from fastapi.routing import APIRoute
from pydantic import BaseModel

from permita38.api.server import get_app
from permita38.core.config import Config
from permita38.core.validation import CheckedBody

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# (route path, model name) -> reason. Empty: every request body is checked.
ALLOWED_UNCHECKED: dict[tuple[str, str], str] = {}


def _app():
    return get_app(Config(data_dir=Path(tempfile.mkdtemp()) / "d", secret_key="s" * 16, web_dir=None))


def _routes(app):
    found = []
    for route in app.routes:
        if isinstance(route, APIRoute):
            found.append(route)
        original = getattr(route, "original_router", None)  # routers are included as wrappers
        if original is not None:
            found.extend(r for r in original.routes if isinstance(r, APIRoute))
    return found


def _models_in(annotation, seen):
    """Every pydantic model reachable from a type annotation (through unions, lists, dicts and model fields)."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        if annotation in seen:
            return
        seen.add(annotation)
        yield annotation
        for field in annotation.model_fields.values():
            yield from _models_in(field.annotation, seen)
        return
    for arg in typing.get_args(annotation):
        yield from _models_in(arg, seen)


def _body_models():
    """(route path, method, model) for every model in a request body of a state-changing route."""
    out = []
    for route in _routes(_app()):
        if not route.methods & WRITE_METHODS:
            continue
        for param in route.dependant.body_params:
            if isinstance(param.field_info, (params.Form, params.File)):
                continue  # multipart parts: text fields are checked by api/forms.check_form, files are not text
            annotation = param.field_info.annotation
            for model in _models_in(annotation, set()):
                out.append((route.path, sorted(route.methods & WRITE_METHODS)[0], model))
            if not list(_models_in(annotation, set())):
                out.append((route.path, sorted(route.methods & WRITE_METHODS)[0], annotation))  # a bare dict, str, ...
    return out


BODIES = _body_models()


def test_the_walk_finds_the_request_bodies():
    """Guards the test itself: an empty walk would pass for the wrong reason."""
    names = {m.__name__ for _, _, m in BODIES if isinstance(m, type)}
    assert {"TimerStartIn", "TimerStopIn", "UserIn", "CalendarIn", "SetupPayload", "LoginPayload"} <= names
    assert len(BODIES) >= 25


def test_every_request_body_model_is_a_checked_body():
    problems = []
    for path, method, model in BODIES:
        if not isinstance(model, type) or not issubclass(model, BaseModel):
            problems.append((method, path, repr(model), "not a model: bare value bodies are not allowed"))
        elif not issubclass(model, CheckedBody) and (path, model.__name__) not in ALLOWED_UNCHECKED:
            problems.append((method, path, model.__name__, "plain BaseModel: derive from CheckedBody"))
    assert not problems, problems


def test_the_allow_list_has_no_stale_entries():
    names = {(path, m.__name__) for path, _, m in BODIES if isinstance(m, type)}
    assert set(ALLOWED_UNCHECKED) <= names, set(ALLOWED_UNCHECKED) - names
