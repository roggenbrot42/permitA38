"""Employee allocations (docs/contracts/planner-per-project.md). Thin: the logic is in core/service/allocations.py."""

from typing import Annotated, Literal

from fastapi import APIRouter, Path, Query, Response

from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import StoredJSON, stored
from permita38.core import models, rights
from permita38.core import validation as v

router = APIRouter(prefix="/allocations", tags=["planning"])
routers = [router]

AllocationId = Annotated[str, Path(pattern=v.ID_PATTERN)]
IdQuery = Annotated[str | None, Query(pattern=v.ID_PATTERN)]


@router.get(
    "",
    response_model=list[models.Allocation],
    response_model_exclude_none=True,
    name="list_allocations",
    summary="allocations of people to projects, newest start first",
)
def list_allocations(
    user: Annotated[dict, Needs("planning")],
    services: ServicesDep,
    project_id: IdQuery = None,
    user_id: IdQuery = None,
    supplier_id: IdQuery = None,
    status: Literal["active", "cancelled"] | None = None,
):
    external = rights.scope_for(user, "procurement", "read") != rights.Scope.NONE  # area exists with the subcontractors
    rows = services.allocations.list(project_id, user_id, supplier_id, status, include_external=external)
    return stored(models.Allocation, rows)


@router.post(
    "/bootstrap",
    response_model=dict,
    name="bootstrap_allocations",
    summary="preview (confirm false) or create one allocation per active contract with a project and assignee",
)
def bootstrap(body: models.AllocationBootstrapIn, _: Annotated[dict, Needs("planning", "write")], services: ServicesDep):
    return services.allocations.bootstrap(body.confirm)


@router.post(
    "",
    response_model=models.Allocation,
    response_model_exclude_none=True,
    status_code=201,
    name="create_allocation",
    summary="give hours per week of a person to a project",
)
def create_allocation(
    body: models.AllocationIn, _: Annotated[dict, Needs("planning", "write")], services: ServicesDep
):
    return StoredJSON(services.allocations.create(body.model_dump()), status_code=201)


@router.put(
    "/{allocation_id}",
    response_model=models.Allocation,
    response_model_exclude_none=True,
    name="update_allocation",
)
def update_allocation(
    allocation_id: AllocationId,
    body: models.AllocationIn,
    _: Annotated[dict, Needs("planning", "write")],
    services: ServicesDep,
):
    return stored(models.Allocation, services.allocations.update(allocation_id, body.model_dump()))


@router.delete("/{allocation_id}", status_code=204, name="delete_allocation")
def delete_allocation(
    allocation_id: AllocationId, _: Annotated[dict, Needs("planning", "write")], services: ServicesDep
):
    services.allocations.delete(allocation_id)
    return Response(status_code=204)
