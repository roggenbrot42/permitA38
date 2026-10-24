"""API tokens: create, list, revoke, audit (docs/contracts/api-tokens.md). Session-only: a token can never manage tokens."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Query, Response

from permita38.api.deps import CurrentUser, ServicesDep, SessionOnly
from permita38.core import validation as v
from permita38.core.models import (
    EPOCH_MAX,
    ApiTokenCreated,
    ApiTokenCreateIn,
    ApiTokenOut,
    AuditPage,
    RevokeAllResult,
)

router = APIRouter(prefix="/tokens", tags=["tokens"], dependencies=[SessionOnly])
routers = [router]

TokenId = Annotated[str, Path(pattern=r"^[0-9a-f]{8}$", description="8 hex characters")]


@router.patch("/{token_id}", status_code=405, summary="not supported: tokens are immutable (always 405)")
def no_patch(token_id: TokenId, user: CurrentUser):
    """Label, expiry and access are immutable (docs/contracts/api-tokens.md, decision 17): revoke and create anew."""
    raise HTTPException(405, "Method Not Allowed", headers={"Allow": "GET, POST"})


@router.post(
    "",
    response_model=ApiTokenCreated,
    response_model_exclude_unset=True,
    status_code=201,
    summary="create a token (the secret is shown once)",
)
def create_token(body: ApiTokenCreateIn, user: CurrentUser, services: ServicesDep, response: Response):
    response.headers["Cache-Control"] = "no-store"
    secret, item = services.tokens.create(user, body.label, body.expires_in_days, body.read_only)
    return {"token": secret, "item": item}


@router.get(
    "",
    response_model=list[ApiTokenOut],
    response_model_exclude_unset=True,
    summary="own tokens (boss: all=true lists everyone's)",
)
def list_tokens(user: CurrentUser, services: ServicesDep, all: bool = False):
    return services.tokens.list(user, all_users=all)


@router.post("/revoke-all", response_model=RevokeAllResult, summary="revoke all active tokens of a user")
def revoke_all(
    user: CurrentUser,
    services: ServicesDep,
    user_id: Annotated[str | None, Query(pattern=v.ID_PATTERN, description="boss only")] = None,
):
    return {"revoked": services.tokens.revoke_all(user, user_id)}


@router.post(
    "/{token_id}/revoke",
    response_model=ApiTokenOut,
    response_model_exclude_unset=True,
    summary="revoke a token (idempotent)",
)
def revoke_token(token_id: TokenId, user: CurrentUser, services: ServicesDep):
    return services.tokens.revoke(user, token_id)


@router.get("/{token_id}/audit", response_model=AuditPage, summary="requests made with a token, newest first")
def token_audit(
    token_id: TokenId,
    user: CurrentUser,
    services: ServicesDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before: Annotated[int | None, Query(ge=0, le=EPOCH_MAX)] = None,
    before_id: Annotated[
        str | None, Query(pattern=v.ID_PATTERN, description="with `before`: the id of the last row")
    ] = None,
):
    tid = services.tokens.audit_target(user, token_id)
    items = services.audit.list(tid, limit, before, before_id)
    full = len(items) == limit
    return {"items": items, "next": items[-1]["at"] if full else None, "next_id": items[-1]["id"] if full else None}
