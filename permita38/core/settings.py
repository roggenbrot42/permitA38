import logging
import types
import typing
from typing import Annotated, Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from .models import LEGACY_TAX_KEYS, PersonalTaxIn, Settings

__all__ = ["DEFAULT_SETTINGS", "LEGACY_TAX_KEYS", "effective_personal_tax", "effective_settings"]

log = logging.getLogger(__name__)

DEFAULT_SETTINGS: dict = Settings().model_dump()


def effective_settings(raw: dict) -> dict:
    """Stored settings merged over the defaults."""
    return Settings(**raw).model_dump()


def _nested_model(annotation: Any) -> tuple[type[BaseModel] | None, bool]:
    """The pydantic model behind a field annotation (also ``Model | None``) and whether it may be None."""
    args = typing.get_args(annotation) if typing.get_origin(annotation) in (typing.Union, types.UnionType) else ()
    nullable = type(None) in args
    candidates = [a for a in args if a is not type(None)] or [annotation]
    for c in candidates:
        if isinstance(c, type) and issubclass(c, BaseModel):
            return c, nullable
    return None, nullable


def _field_adapter(field: Any) -> TypeAdapter:
    tp: Any = Annotated[(field.annotation, *field.metadata)] if field.metadata else field.annotation
    return TypeAdapter(tp)


def _defensive(model: type[BaseModel], raw: Any, where: str) -> dict | None:
    """Validate ``raw`` field by field: an invalid value falls back to its default (logged), never an error.
    Unknown fields are kept. Returns None when the object cannot be built at all."""
    if not isinstance(raw, dict):
        log.warning("personal tax profile: %s is not an object, using the default", where)
        return None
    clean: dict[str, Any] = {}
    for name, value in raw.items():
        field = model.model_fields.get(name)
        if field is None:
            clean[name] = value
            continue
        nested, nullable = _nested_model(field.annotation)
        if nested is not None:
            if value is None and nullable:
                clean[name] = None
                continue
            sub = _defensive(nested, value, f"{where}.{name}")
            if sub is not None:
                clean[name] = sub
            elif nullable:
                clean[name] = None
            continue
        try:
            _field_adapter(field).validate_python(value)
            clean[name] = value
        except ValidationError:
            log.warning("personal tax profile: invalid %s.%s, using the default", where, name)
    try:
        return model(**clean).model_dump()
    except ValidationError:
        log.warning("personal tax profile: %s is inconsistent, using the default", where)
        try:
            return model().model_dump()
        except ValidationError:  # a required field is missing
            return None


def effective_personal_tax(raw_settings: dict) -> dict:
    """The stored personal tax profile (``personal_tax`` in the raw settings) over the defaults. A damaged or missing
    object, or field, falls back to its default; this never raises."""
    stored = raw_settings.get("personal_tax") if isinstance(raw_settings, dict) else None
    out = _defensive(PersonalTaxIn, stored, "personal_tax") if stored is not None else None
    return out if out is not None else PersonalTaxIn().model_dump()
