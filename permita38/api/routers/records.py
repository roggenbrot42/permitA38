# mypy: disable-error-code="valid-type,attr-defined"
"""Typed CRUD routers generated per collection."""

from typing import Annotated

from fastapi import APIRouter, Response
from pydantic import BaseModel

from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import stored
from permita38.core import models, rights
from permita38.core.service.sanity import CHECKED

# collections whose saved records also return non-blocking planning warnings (never stored)
WARNED = ("projects", "contracts", "offers", "invoices")


def _result(model: type[models.Record]) -> type[models.Record]:
    """The lenient response model plus ``planning_warnings`` / ``sanity_warnings`` (response-only, not columns)."""

    class Result(model):  # type: ignore[valid-type,misc]
        planning_warnings: list[dict] | None = None
        sanity_warnings: list[dict] | None = None

    Result.__name__ = model.__name__ + "Result"
    return Result


def _with_warnings(services, coll: str, rec: dict, user: dict, old: dict | None = None) -> dict:
    out = rec
    if coll in WARNED and rights.scope(user["role"], "planning") == rights.Scope.ALL:
        out = {**out, "planning_warnings": services.planning.warnings_for(coll, rec)}
    if coll in CHECKED:
        out = {**out, "sanity_warnings": services.sanity.check(coll, rec, old)}
    return out


def crud_router(coll: str, model: type[models.Record], in_model: type[BaseModel]) -> APIRouter:
    router = APIRouter(prefix=f"/{coll}", tags=[coll])
    out_model = _result(model) if coll in WARNED or coll in CHECKED else model
    area = rights.COLLECTION_AREA[coll]
    reader = Annotated[dict, Needs(area, "read")]
    writer = Annotated[dict, Needs(area, "write")]

    @router.get("", response_model=list[model], response_model_exclude_none=True, name=f"list_{coll}")
    def list_records(user: reader, services: ServicesDep):
        return stored(model, services.records.list_records(user, coll))

    @router.post("", response_model=out_model, response_model_exclude_none=True, name=f"create_{coll}")
    def create_record(body: in_model, user: writer, services: ServicesDep):  # type: ignore[valid-type]
        rec = services.records.create(user, coll, body.model_dump(exclude_none=True))
        return stored(out_model, _with_warnings(services, coll, rec, user))

    @router.get("/{rid}", response_model=model, response_model_exclude_none=True, name=f"get_{coll}")
    def get_record(rid: str, user: reader, services: ServicesDep):
        return stored(model, services.records.get(user, coll, rid))

    @router.put("/{rid}", response_model=out_model, response_model_exclude_none=True, name=f"update_{coll}")
    def update_record(rid: str, body: in_model, user: writer, services: ServicesDep):  # type: ignore[valid-type]
        old = services.sanity.previous(coll, rid)
        rec = services.records.update(user, coll, rid, body.model_dump(exclude_none=True))
        return stored(out_model, _with_warnings(services, coll, rec, user, old))

    @router.delete("/{rid}", status_code=204, name=f"delete_{coll}")
    def delete_record(rid: str, user: writer, services: ServicesDep):
        services.records.delete(user, coll, rid)
        return Response(status_code=204)

    return router


# collection -> (response model: lenient, request model: strict)
COLLECTIONS: dict[str, tuple[type[models.Record], type[BaseModel]]] = {
    "customers": (models.Customer, models.CustomerIn),
    "projects": (models.Project, models.ProjectIn),
    "tasks": (models.Task, models.TaskIn),
    "entries": (models.Entry, models.EntryIn),
    "contracts": (models.Contract, models.ContractIn),
    "absences": (models.Absence, models.AbsenceIn),
    "offers": (models.Document, models.DocumentIn),
    "invoices": (models.Document, models.DocumentIn),
    "expenses": (models.Expense, models.ExpenseIn),
    "templates": (models.Template, models.TemplateIn),
}

routers = [crud_router(c, out, inp) for c, (out, inp) in COLLECTIONS.items()]
