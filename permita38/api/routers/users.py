from typing import Annotated

from fastapi import APIRouter, Response

from permita38.api.deps import Needs, ServicesDep, SessionOnly
from permita38.api.responses import stored
from permita38.core.models import UserIn, UserOut
from permita38.core.service.records import user_warnings

router = APIRouter(prefix="/users", tags=["users"], dependencies=[SessionOnly])
routers = [router]


def _dump(body: UserIn) -> dict:
    return body.model_dump(exclude_none=True)


def _with_warnings(saved: dict) -> dict:
    return {**saved, "planning_warnings": user_warnings(saved)}


@router.get("", response_model=list[UserOut], response_model_exclude_none=True)
def list_users(user: Annotated[dict, Needs("team")], services: ServicesDep):
    """Full records with team access; everyone else: id, name and role only."""
    return stored(UserOut, services.records.list_users(user))


@router.post("", response_model=UserOut)
def create_user(body: UserIn, _: Annotated[dict, Needs("team", "write")], services: ServicesDep):
    data = _dump(body)
    data.pop("id", None)
    return _with_warnings(services.auth.save_user(data))


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: str, body: UserIn, _: Annotated[dict, Needs("team", "write")], services: ServicesDep):
    return _with_warnings(services.auth.save_user({**_dump(body), "id": user_id}))


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: str, user: Annotated[dict, Needs("team", "write")], services: ServicesDep):
    services.auth.delete_user(user_id, user)
    services.calendar.delete_all_for_user(user_id)  # their calendar links and events must not outlive them
    return Response(status_code=204)
