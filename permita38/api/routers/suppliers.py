"""Suppliers and subcontractors (docs/contracts/subcontractors.md). Thin: rights are declared here, the logic is in
core/service/suppliers.py (master data) and core/service/procurement.py (the dependency hint)."""

from typing import Annotated, Any

from fastapi import APIRouter, Path, Query, Response

from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import StoredJSON, stored
from permita38.core import models
from permita38.core import validation as v

router = APIRouter(prefix="/suppliers", tags=["suppliers"])
routers = [router]

SupplierId = Annotated[str, Path(pattern=v.ID_PATTERN)]
Reader = Annotated[dict, Needs("suppliers", "read")]
Writer = Annotated[dict, Needs("suppliers", "write")]


def _body(body: models.SupplierIn) -> dict:
    return body.model_dump(by_alias=True, exclude_none=True)


@router.get(
    "",
    response_model=list[models.Supplier | models.SupplierLimited],
    response_model_exclude_none=True,
    name="list_suppliers",
    summary="suppliers, sorted by name; employees get the limited view",
)
def list_suppliers(
    user: Reader,
    services: ServicesDep,
    kind: models.SupplierKind | None = None,
    active: bool | None = None,
    tag: Annotated[str | None, Query(max_length=50)] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
):
    return stored(models.Supplier, services.suppliers.list(user, kind, active, tag, q))


@router.post(
    "",
    response_model=models.SupplierResult,
    response_model_exclude_none=True,
    status_code=201,
    name="create_supplier",
)
def create_supplier(body: models.SupplierIn, _: Writer, services: ServicesDep):
    return StoredJSON(services.suppliers.create(_body(body)), status_code=201)


@router.get(
    "/vendor-suggestions",
    response_model=dict,
    name="vendor_suggestions",
    summary="expense vendors without a supplier, grouped by a normalised name",
)
def vendor_suggestions(_: Writer, services: ServicesDep) -> Any:
    return services.suppliers.vendor_suggestions()


@router.post(
    "/adopt-vendors",
    response_model=dict,
    name="adopt_vendors",
    summary="link expenses to suppliers by vendor text (dry_run is on by default)",
)
def adopt_vendors(body: models.AdoptVendorsIn, _: Writer, services: ServicesDep) -> Any:
    items = [
        {
            "vendors": i.vendors,
            "supplier_id": i.supplier_id,
            "create": i.create.model_dump(by_alias=True, exclude_none=True) if i.create else None,
        }
        for i in body.items
    ]
    return services.suppliers.adopt_vendors(items, body.dry_run)


@router.get(
    "/{supplier_id}",
    response_model=models.Supplier | models.SupplierLimited,
    response_model_exclude_none=True,
    name="get_supplier",
)
def get_supplier(supplier_id: SupplierId, user: Reader, services: ServicesDep):
    return stored(models.Supplier, services.suppliers.get(user, supplier_id))


@router.put(
    "/{supplier_id}", response_model=models.SupplierResult, response_model_exclude_none=True, name="update_supplier"
)
def update_supplier(supplier_id: SupplierId, body: models.SupplierIn, _: Writer, services: ServicesDep):
    return stored(models.SupplierResult, services.suppliers.update(supplier_id, _body(body)))


@router.delete("/{supplier_id}", status_code=204, name="delete_supplier")
def delete_supplier(supplier_id: SupplierId, _: Writer, services: ServicesDep):
    services.suppliers.delete(supplier_id)
    return Response(status_code=204)



@router.get(
    "/{supplier_id}/dependency",
    response_model=dict,
    name="supplier_dependency",
    summary="the numbers behind the dependency hint of a subcontractor",
)
def supplier_dependency(supplier_id: SupplierId, _: Annotated[dict, Needs("procurement", "read")], services: ServicesDep):
    return services.procurement.dependency_route(supplier_id)
