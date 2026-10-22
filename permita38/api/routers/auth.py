from fastapi import APIRouter, Response
from pydantic import BaseModel

from permita38 import __version__
from permita38.api.deps import COOKIE_NAME, CurrentUser, ServicesDep
from permita38.core.models import UserOut
from permita38.core.service.auth import public_user

router = APIRouter(prefix="/auth", tags=["auth"])
routers = [router]


class AuthStatus(BaseModel):
    setup_needed: bool
    app: str = "permitA38"
    version: str
    data_dir: str


class SetupPayload(BaseModel):
    username: str
    name: str
    password: str
    company_name: str = ""
    weekly_hours: float = 40


class LoginPayload(BaseModel):
    username: str
    password: str


class LoginResult(BaseModel):
    token: str
    user: UserOut


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


class TokenResult(BaseModel):
    token: str


def _set_cookie(response: Response, token: str, max_age: int):
    response.set_cookie(COOKIE_NAME, token, max_age=max_age, httponly=True, samesite="strict", path="/")


@router.get("/status", response_model=AuthStatus, summary="public: is the first-run setup still needed?")
def status(services: ServicesDep):
    return AuthStatus(
        setup_needed=services.auth.needs_setup(), version=__version__, data_dir=str(services.config.data_dir)
    )


@router.post("/setup", response_model=UserOut, summary="create the first boss account")
def setup(payload: SetupPayload, services: ServicesDep):
    return services.auth.setup(
        payload.username, payload.name, payload.password, payload.company_name, payload.weekly_hours
    )


@router.post("/login", response_model=LoginResult, summary="sign in with username and password")
def login(payload: LoginPayload, response: Response, services: ServicesDep):
    token, user = services.auth.login(payload.username, payload.password)
    _set_cookie(response, token, services.auth.ttl)
    return {"token": token, "user": user}


@router.post("/logout", status_code=204, summary="sign out (clears the cookie)")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")


@router.post("/change-password", response_model=TokenResult, summary="change own password")
def change_password(payload: ChangePasswordPayload, user: CurrentUser, response: Response, services: ServicesDep):
    token = services.auth.change_password(user, payload.old_password, payload.new_password)
    _set_cookie(response, token, services.auth.ttl)
    return {"token": token}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser):
    return public_user(user)
