"""Scan of old offers and invoices (docs/specs/document-scan.md). Thin: the pipeline is ``core/service/document_scans.py``."""

from typing import Annotated, Literal

from fastapi import APIRouter, Response, UploadFile
from pydantic import BaseModel

from permita38.api.deps import Needs, ServicesDep
from permita38.api.responses import stored
from permita38.api.routers.files import ATTACHMENT_CSP
from permita38.core import models
from permita38.core.uploads import MAX_UPLOAD

router = APIRouter(prefix="/document-scans", tags=["document-scans"])
routers = [router]

PUBLIC = (
    "id",
    "status",
    "kind",
    "filename",
    "attachment",
    "engine",
    "flags",
    "notes",
    "draft",
    "doc",
    "record_coll",
    "record_id",
    "created",
)


class ScanOut(BaseModel):
    """A scanned document as sent to a client (the stored file name, the hash and the duplicate key stay on the server)."""

    id: str
    status: str = "flagged"  # flagged | added | discarded
    kind: str | None = None
    filename: str | None = None
    engine: str | None = None
    flags: list[dict] | None = None
    notes: list[dict] | None = None
    draft: dict | None = None
    doc: dict | None = None
    attachment: str | None = None
    record_coll: str | None = None
    record_id: str | None = None
    created: int | None = None


class RecordRef(BaseModel):
    collection: str
    id: str
    number: str
    year: int


class UploadResult(BaseModel):
    status: Literal["added", "flagged"]
    scan: ScanOut
    record: RecordRef | None = None


class ScanList(BaseModel):
    items: list[ScanOut]


class ConfirmResult(BaseModel):
    record: RecordRef
    scan: ScanOut


Reader = Annotated[dict, Needs("invoices", "read")]
OfferReader = Annotated[dict, Needs("offers", "read")]
AttachReader = Annotated[dict, Needs("attachments", "read")]
Writer = Annotated[dict, Needs("invoices", "write")]
OfferWriter = Annotated[dict, Needs("offers", "write")]
AttachWriter = Annotated[dict, Needs("attachments", "write")]
CustomerReader = Annotated[dict, Needs("customers", "read")]
CustomerWriter = Annotated[dict, Needs("customers", "write")]


def _public(scan: dict) -> dict:
    return {k: scan[k] for k in PUBLIC if k in scan}


@router.post(
    "", status_code=201, response_model=UploadResult, response_model_exclude_none=True, summary="scan an old document"
)
def upload(
    file: UploadFile, user: Writer, _o: OfferWriter, _a: AttachWriter, _c: CustomerWriter, services: ServicesDep
):
    blob = file.file.read(MAX_UPLOAD + 1)
    out = services.document_scans.upload(user, file.filename, blob)
    reply = stored(UploadResult, {**out, "scan": _public(out["scan"])})
    reply.status_code = 201  # a returned response ignores the status of the decorator
    return reply


@router.get(
    "", response_model=ScanList, response_model_exclude_none=True, summary="scanned documents that need a check"
)
def list_scans(
    user: Reader,
    _o: OfferReader,
    _a: AttachReader,
    _c: CustomerReader,
    services: ServicesDep,
    status: Literal["flagged"] = "flagged",
):
    return stored(ScanList, {"items": [_public(s) for s in services.document_scans.list_flagged()]})


@router.get("/{scan_id}", response_model=ScanOut, response_model_exclude_none=True, summary="one scanned document")
def get_scan(scan_id: str, user: Reader, _o: OfferReader, _a: AttachReader, _c: CustomerReader, services: ServicesDep):
    return stored(ScanOut, _public(services.document_scans.get(scan_id)))


@router.get(
    "/{scan_id}/pages/{page}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
    summary="PNG of one page of a scanned PDF (150 dpi)",
)
def page_image(
    scan_id: str, page: int, user: Reader, _o: OfferReader, _a: AttachReader, _c: CustomerReader, services: ServicesDep
):
    png = services.document_scans.page_png(scan_id, page)
    return Response(
        png,
        media_type="image/png",
        headers={"Content-Security-Policy": ATTACHMENT_CSP, "X-Frame-Options": "SAMEORIGIN"},
    )


@router.post(
    "/{scan_id}/confirm",
    response_model=ConfirmResult,
    response_model_exclude_none=True,
    summary="save a flagged document",
)
def confirm(
    scan_id: str,
    body: models.DocumentScanConfirmIn,
    user: Writer,
    _o: OfferWriter,
    _a: AttachWriter,
    _c: CustomerWriter,
    services: ServicesDep,
):
    out = services.document_scans.confirm(user, scan_id, body)
    return stored(ConfirmResult, {**out, "scan": _public(out["scan"])})


@router.delete("/{scan_id}", status_code=204, summary="discard a flagged document")
def discard(scan_id: str, user: Writer, _o: OfferWriter, _a: AttachWriter, services: ServicesDep):
    services.document_scans.discard(scan_id)
    return Response(status_code=204)
