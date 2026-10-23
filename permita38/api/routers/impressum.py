from typing import Annotated

from fastapi import APIRouter
from pydantic import BaseModel, Field

from permita38.api.deps import ServicesDep, needs

CustomerManager = Annotated[dict, needs("customers.manage")]
router = APIRouter(prefix="/customers", tags=["customers"])
routers = [router]


class LookupRequest(BaseModel):
    url: str = Field(max_length=2048, description="Address of the company website or its Impressum page")


class Candidate(BaseModel):
    value: str | list[str]
    confidence: float
    evidence: str


class ImpressumLookup(BaseModel):
    source_url: str
    impressum_url: str | None = None
    fields: dict[str, str | list[str]]
    confidence: dict[str, float]
    evidence: dict[str, str]
    candidates: dict[str, list[Candidate]]
    warnings: list[str]


@router.post(
    "/lookup-impressum",
    response_model=ImpressumLookup,
    summary="read business data from a website's Impressum (nothing is saved)",
)
def lookup_impressum(body: LookupRequest, user: CustomerManager, services: ServicesDep):
    return services.impressum.lookup(user["id"], body.url)
