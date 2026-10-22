from fastapi import APIRouter

from permita38.api.deps import BossUser, CurrentUser, ServicesDep
from permita38.core.models import Settings

router = APIRouter(tags=["settings"])
routers = [router]


@router.get("/bootstrap", summary="everything the web app needs after sign-in")
def bootstrap(user: CurrentUser, services: ServicesDep) -> dict:
    return services.settings.bootstrap(user, services.time.get_timer(user))


@router.get("/settings", response_model=Settings)
def get_settings(_: BossUser, services: ServicesDep):
    return services.settings.get()


@router.put("/settings", response_model=Settings, summary="merge the given values into the settings")
def update_settings(body: Settings, _: BossUser, services: ServicesDep):
    return services.settings.update(body.model_dump(exclude_unset=True))
