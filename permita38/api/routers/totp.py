"""Two-factor authentication (TOTP, RFC 6238): enrolment, second login step, recovery and administrative reset."""

from fastapi import APIRouter, Request, Response
from pydantic import BaseModel, Field

from permita38.api.deps import BossUser, CurrentUser, ServicesDep
from permita38.api.routers.auth import LoginResult, TokenResult, _set_cookie, cookie_secure
from permita38.core.models import UserOut

router = APIRouter(prefix="/auth", tags=["two-factor"])
admin_router = APIRouter(prefix="/users", tags=["two-factor"])
routers = [router, admin_router]


class TotpLoginPayload(BaseModel):
    mfa_token: str
    code: str | None = Field(default=None, description="6-digit code from the authenticator app")
    recovery_code: str | None = Field(default=None, description="one-time recovery code (instead of `code`)")


class PasswordPayload(BaseModel):
    password: str


class TotpSetupResult(BaseModel):
    secret: str = Field(description="base32 secret for manual entry")
    otpauth_uri: str
    qr_svg: str = Field(description="QR code as an SVG data URI")
    issuer: str
    account: str


class CodePayload(BaseModel):
    code: str


class EnableResult(BaseModel):
    token: str
    recovery_codes: list[str] = Field(description="shown exactly once; only hashes are stored")


class DisablePayload(BaseModel):
    password: str
    code: str | None = None
    recovery_code: str | None = None


class RegeneratePayload(BaseModel):
    password: str
    code: str


class RecoveryCodesResult(BaseModel):
    recovery_codes: list[str]


class TotpStatus(BaseModel):
    enabled: bool
    pending_setup: bool
    recovery_codes_left: int


class ResetPayload(BaseModel):
    password: str = Field(description="the boss's own password, as confirmation")


@router.post("/login/totp", response_model=LoginResult, summary="sign in, second step: authenticator or recovery code")
def login_totp(payload: TotpLoginPayload, request: Request, response: Response, services: ServicesDep):
    token, user, left = services.totp.complete_login(payload.mfa_token, payload.code, payload.recovery_code)
    _set_cookie(response, token, services.auth.ttl, cookie_secure(request, services.config))
    return {"token": token, "user": user, "recovery_codes_left": left}


@router.get("/totp/status", response_model=TotpStatus)
def totp_status(user: CurrentUser, services: ServicesDep):
    return services.totp.status(user)


@router.post("/totp/setup", response_model=TotpSetupResult, summary="start enrolment (needs the current password)")
def totp_setup(payload: PasswordPayload, user: CurrentUser, services: ServicesDep):
    return services.totp.begin_setup(user, payload.password)


@router.post("/totp/enable", response_model=EnableResult, summary="confirm enrolment with a first code")
def totp_enable(payload: CodePayload, request: Request, user: CurrentUser, response: Response, services: ServicesDep):
    token, codes = services.totp.enable(user, payload.code)
    _set_cookie(response, token, services.auth.ttl, cookie_secure(request, services.config))
    return {"token": token, "recovery_codes": codes}


@router.post("/totp/disable", response_model=TokenResult, summary="turn two-factor authentication off")
def totp_disable(
    payload: DisablePayload, request: Request, user: CurrentUser, response: Response, services: ServicesDep
):
    token = services.totp.disable(user, payload.password, payload.code, payload.recovery_code)
    _set_cookie(response, token, services.auth.ttl, cookie_secure(request, services.config))
    return {"token": token}


@router.post(
    "/totp/recovery-codes/regenerate",
    response_model=RecoveryCodesResult,
    summary="replace all recovery codes (old ones stop working)",
)
def totp_regenerate(payload: RegeneratePayload, user: CurrentUser, services: ServicesDep):
    return {"recovery_codes": services.totp.regenerate_recovery_codes(user, payload.password, payload.code)}


@admin_router.post(
    "/{user_id}/totp/reset",
    response_model=UserOut,
    summary="boss: remove a user's two-factor authentication (lock-out recovery)",
)
def totp_reset(user_id: str, payload: ResetPayload, boss: BossUser, services: ServicesDep):
    return services.totp.reset_for(boss, user_id, payload.password)
