"""Document numbering: preview, dry-run checks, guarded changes and the change log (boss only)."""

from typing import Annotated, Literal

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field, StringConstraints

from permita38.api.deps import Needs, ServicesDep
from permita38.core import validation as v
from permita38.core.numbering import MAX_NUMBER

router = APIRouter(tags=["numbering"])
routers = [router]

DocType = Literal["invoice", "offer"]
Pattern = Annotated[str, StringConstraints(max_length=100)]


class NumberingChange(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    type: DocType
    pattern: Pattern | None = None
    reset: Literal["never", "yearly", "monthly"] | None = None
    next: Annotated[int, Field(ge=1, le=MAX_NUMBER)] | None = None
    reason: Annotated[str, StringConstraints(max_length=500)] = ""
    confirm: bool = False


class NumberingCheck(v.CheckedBody):
    model_config = ConfigDict(extra="forbid")
    type: DocType
    pattern: Pattern | None = None
    reset: Literal["never", "yearly", "monthly"] | None = None
    next: Annotated[int, Field(ge=1, le=MAX_NUMBER)] | None = None


@router.get("/numbering/preview", summary="the next numbers for a document type")
def preview(
    _: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep, type: DocType = "invoice"
) -> dict:
    return {"type": type, "next": services.sales.numbering.preview(type, 5)}


@router.get("/numbering/status", summary="pattern, reset scope, next number and warnings for a document type")
def status(
    _: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep, type: DocType = "invoice"
) -> dict:
    return services.sales.numbering.status(type)


@router.post("/numbering/check", summary="dry run of a change: warnings and preview, nothing is written")
def check(
    body: NumberingCheck, _: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep
) -> dict:
    return services.sales.numbering.check(body.type, pattern=body.pattern, reset=body.reset, next_number=body.next)


@router.put(
    "/numbering",
    summary="change pattern, reset scope or next number; risky changes need confirm=true (409 otherwise)",
)
def update(body: NumberingChange, _: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep):
    result = services.sales.numbering.update(
        body.type,
        pattern=body.pattern,
        reset=body.reset,
        next_number=body.next,
        reason=body.reason,
        confirm=body.confirm,
    )
    if not result["applied"]:
        return JSONResponse(status_code=409, content={"detail": result})
    return result


@router.get("/numbering/log", summary="audit trail of numbering changes")
def log(
    _: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep, type: DocType | None = None
) -> list[dict]:
    return services.sales.numbering.log(type)
