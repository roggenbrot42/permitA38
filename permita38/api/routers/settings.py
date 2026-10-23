from typing import Annotated

from fastapi import APIRouter

from permita38.api.deps import CurrentUser, ServicesDep, needs
from permita38.core.models import Settings, SettingsIn
from permita38.core.service.roles import can

SettingsUser = Annotated[dict, needs("settings.manage")]
router = APIRouter(tags=["settings"])
routers = [router]


@router.get("/bootstrap", summary="everything the web app needs after sign-in")
def bootstrap(user: CurrentUser, services: ServicesDep) -> dict:
    out = services.settings.bootstrap(user, services.time.get_timer(user))
    out["features"] = {"impressum_lookup": services.config.features.impressum_lookup}
    if can(user, "settings.manage"):  # the server's folder path is not for everyone
        out["data_dir"] = str(services.config.data_dir)
    return out


@router.get("/settings", response_model=Settings)
def get_settings(_: SettingsUser, services: ServicesDep):
    return services.settings.get()


@router.put("/settings", response_model=Settings, summary="merge the given values into the settings")
def update_settings(body: SettingsIn, _: SettingsUser, services: ServicesDep):
    return services.settings.update(body.model_dump(exclude_unset=True))
