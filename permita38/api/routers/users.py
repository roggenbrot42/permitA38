from typing import Annotated

from fastapi import APIRouter, Response

from permita38.api.deps import CurrentUser, ServicesDep, needs
from permita38.api.responses import stored
from permita38.core.models import UserIn, UserOut

router = APIRouter(prefix="/users", tags=["users"])
routers = [router]
TeamManager = Annotated[dict, needs("team.manage")]


def _dump(body: UserIn) -> dict:
    return body.model_dump(exclude_none=True)


@router.get("", response_model=list[UserOut], response_model_exclude_none=True)
def list_users(user: CurrentUser, services: ServicesDep):
    """With 'See the team': full records. Everybody else: id and name only."""
    return stored(UserOut, services.records.list_users(user, services.auth.public))


@router.post("", response_model=UserOut, response_model_exclude_none=True)
def create_user(body: UserIn, actor: TeamManager, services: ServicesDep):
    data = _dump(body)
    data.pop("id", None)
    return stored(UserOut, services.auth.save_user(data, actor))


@router.put("/{user_id}", response_model=UserOut, response_model_exclude_none=True)
def update_user(user_id: str, body: UserIn, actor: TeamManager, services: ServicesDep):
    return stored(UserOut, services.auth.save_user({**_dump(body), "id": user_id}, actor))


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, actor: TeamManager, services: ServicesDep):
    services.auth.delete_user(user_id, actor)
    return Response(status_code=204)
