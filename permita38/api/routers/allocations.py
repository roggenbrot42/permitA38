"""Assignments of employees and subcontractors. Thin: the logic is in core/service/allocations.py."""

from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Path, Query, Response

from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import StoredJSON, stored
from permita38.core import models, rights
from permita38.core import validation as v

router = APIRouter(prefix="/allocations", tags=["planning"])
routers = [router]


def _need_write(user: dict, supplier: bool) -> None:
    """Subcontractor rows need `procurement` write, employee rows `planning` write."""
    if not rights.allowed_for(user, rights.allocation_area(supplier), "write"):
        raise HTTPException(403, "Forbidden")


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
    "",
    response_model=models.Allocation,
    response_model_exclude_none=True,
    status_code=201,
    name="create_allocation",
    summary="give hours of a person or subcontractor to a project",
)
def create_allocation(
    body: models.AllocationIn, user: Annotated[dict, Needs("planning", "write")], services: ServicesDep
):
    _need_write(user, bool(body.supplier_id))
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
    user: Annotated[dict, Needs("planning", "write")],
    services: ServicesDep,
):
    _need_write(user, services.allocations.kind_of(allocation_id) == "subcontractor")
    _need_write(user, bool(body.supplier_id))
    return stored(models.Allocation, services.allocations.update(allocation_id, body.model_dump()))


@router.delete("/{allocation_id}", status_code=204, name="delete_allocation")
def delete_allocation(
    allocation_id: AllocationId, user: Annotated[dict, Needs("planning", "write")], services: ServicesDep
):
    _need_write(user, services.allocations.kind_of(allocation_id) == "subcontractor")
    services.allocations.delete(allocation_id)
    return Response(status_code=204)


@router.get(
    "/{allocation_id}",
    response_model=models.Allocation,
    response_model_exclude_none=True,
    name="get_allocation",
    summary="one allocation; external rows with their day list, employee rows only for planners",
)
def get_allocation(
    allocation_id: AllocationId, user: Annotated[dict, Needs("procurement", "read")], services: ServicesDep
):
    return stored(models.Allocation, services.procurement.allocation(allocation_id, user))


@router.post(
    "/{allocation_id}/cancel",
    response_model=models.Allocation,
    response_model_exclude_none=True,
    name="cancel_allocation",
    summary="end an external allocation from a day on; the days before it keep their hours and cost",
)
def cancel_allocation(
    allocation_id: AllocationId,
    body: models.CancelAllocationIn,
    _: Annotated[dict, Needs("procurement", "write")],
    services: ServicesDep,
):
    return stored(models.Allocation, services.procurement.cancel_allocation(allocation_id, body.model_dump()))
