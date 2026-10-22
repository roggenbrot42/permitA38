# mypy: disable-error-code="valid-type,attr-defined"
"""Typed CRUD routers generated per collection."""

from fastapi import APIRouter, Response

from permita38.api.deps import CurrentUser, ServicesDep
from permita38.core import models


def crud_router(coll: str, model: type[models.Record]) -> APIRouter:
    router = APIRouter(prefix=f"/{coll}", tags=[coll])

    @router.get("", response_model=list[model], response_model_exclude_none=True, name=f"list_{coll}")
    def list_records(user: CurrentUser, services: ServicesDep):
        return services.records.list_records(user, coll)

    @router.post("", response_model=model, response_model_exclude_none=True, name=f"create_{coll}")
    def create_record(body: model, user: CurrentUser, services: ServicesDep):  # type: ignore[valid-type]
        return services.records.create(user, coll, body.model_dump(exclude_none=True))

    @router.get("/{rid}", response_model=model, response_model_exclude_none=True, name=f"get_{coll}")
    def get_record(rid: str, user: CurrentUser, services: ServicesDep):
        return services.records.get(user, coll, rid)

    @router.put("/{rid}", response_model=model, response_model_exclude_none=True, name=f"update_{coll}")
    def update_record(rid: str, body: model, user: CurrentUser, services: ServicesDep):  # type: ignore[valid-type]
        return services.records.update(user, coll, rid, body.model_dump(exclude_none=True))

    @router.delete("/{rid}", status_code=204, name=f"delete_{coll}")
    def delete_record(rid: str, user: CurrentUser, services: ServicesDep):
        services.records.delete(user, coll, rid)
        return Response(status_code=204)

    return router


COLLECTIONS: dict[str, type[models.Record]] = {
    "customers": models.Customer,
    "projects": models.Project,
    "tasks": models.Task,
    "entries": models.Entry,
    "contracts": models.Contract,
    "absences": models.Absence,
    "offers": models.Document,
    "invoices": models.Document,
    "expenses": models.Expense,
    "templates": models.Template,
}

routers = [crud_router(c, m) for c, m in COLLECTIONS.items()]
