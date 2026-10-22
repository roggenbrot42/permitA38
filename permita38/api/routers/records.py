# mypy: disable-error-code="valid-type,attr-defined"
"""Typed CRUD routers generated per collection."""

from fastapi import APIRouter, Response
from pydantic import BaseModel

from permita38.api.deps import CurrentUser, ServicesDep
from permita38.api.responses import StoredJSON
from permita38.core import models


def crud_router(coll: str, model: type[models.Record], in_model: type[BaseModel]) -> APIRouter:
    router = APIRouter(prefix=f"/{coll}", tags=[coll])

    @router.get("", response_model=list[model], response_model_exclude_none=True, name=f"list_{coll}")
    def list_records(user: CurrentUser, services: ServicesDep):
        return StoredJSON(services.records.list_records(user, coll))

    @router.post("", response_model=model, response_model_exclude_none=True, name=f"create_{coll}")
    def create_record(body: in_model, user: CurrentUser, services: ServicesDep):  # type: ignore[valid-type]
        return StoredJSON(services.records.create(user, coll, body.model_dump(exclude_none=True)))

    @router.get("/{rid}", response_model=model, response_model_exclude_none=True, name=f"get_{coll}")
    def get_record(rid: str, user: CurrentUser, services: ServicesDep):
        return StoredJSON(services.records.get(user, coll, rid))

    @router.put("/{rid}", response_model=model, response_model_exclude_none=True, name=f"update_{coll}")
    def update_record(rid: str, body: in_model, user: CurrentUser, services: ServicesDep):  # type: ignore[valid-type]
        return StoredJSON(services.records.update(user, coll, rid, body.model_dump(exclude_none=True)))

    @router.delete("/{rid}", status_code=204, name=f"delete_{coll}")
    def delete_record(rid: str, user: CurrentUser, services: ServicesDep):
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
