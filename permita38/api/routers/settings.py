from typing import Annotated

from fastapi import APIRouter

from permita38.api.deps import CurrentUser, Needs, ServicesDep
from permita38.core import rights
from permita38.core.models import Settings, SettingsIn

router = APIRouter(tags=["settings"])
routers = [router]


@router.get("/bootstrap", summary="everything the web app needs after sign-in")
def bootstrap(user: CurrentUser, services: ServicesDep) -> dict:
    out = services.settings.bootstrap(user, services.time.get_timer(user))
    out["features"] = {"impressum_lookup": services.config.features.impressum_lookup}
    if (
        rights.scope(user["role"], "settings", "write") == rights.Scope.ALL
    ):  # the server's folder path is not for everyone
        out["data_dir"] = str(services.config.data_dir)
    return out


@router.get("/settings", response_model=Settings)
def get_settings(_: Annotated[dict, Needs("settings", full=True)], services: ServicesDep):
    return services.settings.get()


@router.put("/settings", response_model=Settings, summary="merge the given values into the settings")
def update_settings(body: SettingsIn, _: Annotated[dict, Needs("settings", "write")], services: ServicesDep):
    return services.settings.update(body.model_dump(exclude_unset=True))
