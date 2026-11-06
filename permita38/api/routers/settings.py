from typing import Annotated

from fastapi import APIRouter

from permita38.api.deps import CurrentUser, Needs, ServicesDep, SessionOnly
from permita38.core import rights
from permita38.core.models import PersonalTax, PersonalTaxIn, Settings, SettingsIn, WorkLimits, WorkLimitsIn

router = APIRouter(tags=["settings"])
routers = [router]


@router.get("/bootstrap", summary="everything the web app needs after sign-in")
def bootstrap(user: CurrentUser, services: ServicesDep) -> dict:
    out = services.settings.bootstrap(user, services.time.get_timer(user))
    out["features"] = {"impressum_lookup": services.config.features.impressum_lookup}
    # the server's folder path is for a boss signed in with a password, never for an API token
    if not rights.is_token(user) and rights.scope_for(user, "settings", "write") == rights.Scope.ALL:
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


@router.get(
    "/settings/personal-tax",
    response_model=PersonalTax,
    summary="the personal tax profile (boss, signed in with a password)",
    dependencies=[SessionOnly],
)
def get_personal_tax(_: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep):
    return services.personal_tax.get()


@router.put(
    "/settings/personal-tax",
    response_model=PersonalTax,
    summary="replace the personal tax profile (boss, signed in with a password)",
    dependencies=[SessionOnly],
)
def put_personal_tax(
    body: PersonalTaxIn, _: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep
):
    data = body.model_dump(exclude={"stored", "warnings"})
    return services.personal_tax.update(data)


@router.get(
    "/settings/work-limits",
    response_model=WorkLimits,
    summary="the signed-in boss's own planning limits (defaults when not stored)",
    dependencies=[SessionOnly],
)
def get_work_limits(user: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep):
    return services.settings.get_work_limits(user["id"])


@router.put(
    "/settings/work-limits",
    response_model=WorkLimits,
    summary="replace the signed-in boss's own planning limits",
    dependencies=[SessionOnly],
)
def put_work_limits(
    body: WorkLimitsIn, user: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep
):
    return services.settings.put_work_limits(user["id"], body.model_dump())
