from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from permita38 import __version__
from permita38.api.deps import COOKIE_NAME, CurrentUser, ServicesDep, SessionOnly
from permita38.core import validation as v
from permita38.core.config import Config
from permita38.core.models import UserOut
from permita38.core.service.auth import public_user

router = APIRouter(prefix="/auth", tags=["auth"])
routers = [router]


class AuthStatus(BaseModel):
    setup_needed: bool
    app: str = "permitA38"
    version: str


class SetupPayload(v.CheckedBody):
    username: str = Field(max_length=64)
    name: v.Name
    password: str = Field(max_length=1000)
    company_name: v.Short = ""
    weekly_hours: v.Hours = 40


class LoginPayload(v.CheckedBody):
    username: str = Field(max_length=64)
    password: str = Field(max_length=1000)


class LoginResult(BaseModel):
    """Either a session (`token` + `user`) or, for accounts with two-factor authentication, a challenge
    (`mfa_required` + `mfa_token`) that POST /auth/login/totp turns into a session."""

    token: str | None = None
    user: UserOut | None = None
    mfa_required: bool = False
    mfa_token: str | None = None
    recovery_codes_left: int | None = None


class ChangePasswordPayload(v.CheckedBody):
    old_password: str = Field(max_length=1000)
    new_password: str = Field(max_length=1000)


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
    if request.headers.get("authorization", "").lower().startswith("bearer "):
        return  # public route: a request that carries a bearer credential (an API token) never touches the cookie
    response.delete_cookie(COOKIE_NAME, path="/", secure=cookie_secure(request, services.config))


@router.post("/change-password", response_model=TokenResult, summary="change own password", dependencies=[SessionOnly])
def change_password(
    payload: ChangePasswordPayload, request: Request, user: CurrentUser, response: Response, services: ServicesDep
):
    token = services.auth.change_password(user, payload.old_password, payload.new_password)
    _set_cookie(response, token, services.auth.ttl, cookie_secure(request, services.config))
    return {"token": token}


@router.get("/me", response_model=UserOut, summary="the signed-in user (also usable with an API token)")
def me(user: CurrentUser, services: ServicesDep):
    out = public_user(user)
    token = user.get("_auth")
    if token and token.get("kind") == "token":
        details = services.tokens.details(token["token_id"]) or {}
        out["auth"] = {
            "kind": "token",
            "token_id": token["token_id"],
            "label": details.get("label"),
            "read_only": token["read_only"],
            "expires_at": details.get("expires_at"),
        }
    else:
        out["auth"] = {"kind": "session"}
    return out
