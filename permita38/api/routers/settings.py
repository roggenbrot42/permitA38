from typing import Annotated

from fastapi import APIRouter

from permita38.api.deps import CurrentUser, Needs, ServicesDep, SessionOnly
from permita38.core import rights
from permita38.core.models import Settings, SettingsIn

router = APIRouter(tags=["settings"])
routers = [router]


@router.get("/bootstrap", summary="everything the web app needs after sign-in")
def bootstrap(user: CurrentUser, services: ServicesDep) -> dict:
    out = services.settings.bootstrap(user, services.time.get_timer(user))
    out["features"] = {"impressum_lookup": services.config.features.impressum_lookup}
    if rights.scope_for(user, "settings", "write") == rights.Scope.ALL:  # the server's folder path is not for everyone
        out["data_dir"] = str(services.config.data_dir)
    return out


@router.get("/settings", response_model=Settings)
def get_settings(user: Annotated[dict, Needs("settings", full=True)], services: ServicesDep):
    # the team timeline visibility is the boss's setting: other roles do not get the field
    return services.settings.get(team=rights.normalize_role(user.get("role")) == rights.BOSS)


class SettingsResult(Settings):
    """Saved settings plus advisory warnings about the tax ids and bank data (never stored)."""

    sanity_warnings: list[dict] | None = None


@router.put(
    "/settings",
    response_model=SettingsResult,
    summary="merge the given values into the settings",
    dependencies=[SessionOnly],
)
def update_settings(body: SettingsIn, _: Annotated[dict, Needs("settings", "write")], services: ServicesDep):
    saved = services.settings.update(body.model_dump(exclude_unset=True))
    return {**saved, "sanity_warnings": services.sanity.settings_warnings(saved)}
