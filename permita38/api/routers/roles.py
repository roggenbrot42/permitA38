from typing import Annotated, Any

from fastapi import APIRouter, Response

from permita38.api.deps import CurrentUser, ServicesDep, needs
from permita38.api.responses import stored
from permita38.core import rights as R
from permita38.core.models import Role, RoleIn

router = APIRouter(tags=["roles"])
routers = [router]
RoleViewer = Annotated[dict, needs(any_of=("team.view", "roles.manage"))]
RoleManager = Annotated[dict, needs("roles.manage")]


def _dump(body: RoleIn) -> dict:
    return body.model_dump(exclude_none=True)


@router.get("/rights", summary="the catalog of rights, grouped, with what each implies")
def rights_catalog(_: CurrentUser) -> dict[str, Any]:
    return {
        "groups": list(R.GROUPS),
        "rights": [
            {
                "key": r.key,
                "group": r.group,
                "label": r.label,
                "description": r.description,
                "planned": r.planned,
                "implies": sorted(R.IMPLIES.get(r.key, ())),
            }
            for r in R.CATALOG
        ],
    }


@router.get("/roles", response_model=list[Role], response_model_exclude_none=True)
def list_roles(_: RoleViewer, services: ServicesDep):
    return stored(Role, services.roles.list_roles())


@router.post("/roles", response_model=Role, response_model_exclude_none=True)
def create_role(body: RoleIn, _: RoleManager, services: ServicesDep):
    return stored(Role, services.roles.create(_dump(body)))


@router.get("/roles/{rid}", response_model=Role, response_model_exclude_none=True)
def get_role(rid: str, _: RoleViewer, services: ServicesDep):
    return stored(Role, services.roles.get(rid))


@router.put("/roles/{rid}", response_model=Role, response_model_exclude_none=True)
def update_role(rid: str, body: RoleIn, _: RoleManager, services: ServicesDep):
    return stored(Role, services.roles.update(rid, _dump(body)))


@router.delete("/roles/{rid}", status_code=204)
def delete_role(rid: str, _: RoleManager, services: ServicesDep):
    services.roles.delete(rid)
    return Response(status_code=204)
