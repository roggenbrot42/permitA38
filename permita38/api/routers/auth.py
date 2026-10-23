from fastapi import APIRouter, Request, Response
from pydantic import BaseModel

from permita38 import __version__
from permita38.api.deps import COOKIE_NAME, CurrentUser, ServicesDep
from permita38.core.config import Config
from permita38.core.models import UserOut

router = APIRouter(prefix="/auth", tags=["auth"])
routers = [router]


class AuthStatus(BaseModel):
    setup_needed: bool
    app: str = "permitA38"
    version: str


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
    """Either a session (`token` + `user`) or, for accounts with two-factor authentication, a challenge
    (`mfa_required` + `mfa_token`) that POST /auth/login/totp turns into a session."""

    token: str | None = None
    user: UserOut | None = None
    mfa_required: bool = False
    mfa_token: str | None = None
    recovery_codes_left: int | None = None


class ChangePasswordPayload(BaseModel):
    old_password: str
    new_password: str


class TokenResult(BaseModel):
    token: str


def cookie_secure(request: Request, config: Config) -> bool:
    """Secure flag for the auth cookie: explicit setting, else https (directly or via a trusted proxy)."""
    if config.cookie_secure is not None:
        return config.cookie_secure
    if request.url.scheme == "https":
        return True
    if config.trust_proxy:
        return request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower() == "https"
    return False


def _set_cookie(response: Response, token: str, max_age: int, secure: bool = False):
    response.set_cookie(COOKIE_NAME, token, max_age=max_age, httponly=True, samesite="strict", path="/", secure=secure)


@router.get("/status", response_model=AuthStatus, summary="public: is the first-run setup still needed?")
def status(services: ServicesDep):
    return AuthStatus(setup_needed=services.auth.needs_setup(), version=__version__)


@router.post("/setup", response_model=UserOut, summary="create the first boss account")
def setup(payload: SetupPayload, services: ServicesDep):
    return services.auth.setup(
        payload.username, payload.name, payload.password, payload.company_name, payload.weekly_hours
    )


@router.post("/login", response_model=LoginResult, summary="sign in with username and password")
def login(payload: LoginPayload, request: Request, response: Response, services: ServicesDep):
    step = services.totp.login(payload.username, payload.password)
    if step.mfa_required:
        return {"mfa_required": True, "mfa_token": step.mfa_token}
    assert step.token is not None
    _set_cookie(response, step.token, services.auth.ttl, cookie_secure(request, services.config))
    return {"token": step.token, "user": step.user}


@router.post("/logout", status_code=204, summary="sign out (clears the cookie)")
def logout(request: Request, response: Response, services: ServicesDep):
    response.delete_cookie(COOKIE_NAME, path="/", secure=cookie_secure(request, services.config))


@router.post("/change-password", response_model=TokenResult, summary="change own password")
def change_password(
    payload: ChangePasswordPayload, request: Request, user: CurrentUser, response: Response, services: ServicesDep
):
    token = services.auth.change_password(user, payload.old_password, payload.new_password)
    _set_cookie(response, token, services.auth.ttl, cookie_secure(request, services.config))
    return {"token": token}


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser, services: ServicesDep):
    return services.auth.public(user)
