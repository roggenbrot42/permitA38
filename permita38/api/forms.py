"""Text fields of multipart requests (uploads with form fields). They have no pydantic model, so the checks run here and
report in the same 422 shape as every other request body: ``loc`` is ``["form", <field name>]``."""

from collections.abc import Mapping
from typing import Any

from fastapi.exceptions import RequestValidationError

from permita38.core import validation as v


def check_form(fields: Mapping[str, Any]) -> None:
    errors = v.form_value_errors(fields)
    if errors:
        raise RequestValidationError([{**e, "loc": ("form", *e["loc"])} for e in errors])
