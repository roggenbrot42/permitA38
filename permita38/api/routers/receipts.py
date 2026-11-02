"""Automatic receipt scan (docs/specs/receipt-scan.md). Thin: the pipeline lives in ``core/service/receipts.py``."""

from typing import Annotated, Literal

from fastapi import APIRouter, Response, UploadFile
from pydantic import BaseModel

from permita38.api.deps import Needs, ServicesDep, SessionOnly
from permita38.api.responses import stored
from permita38.api.routers.files import ATTACHMENT_CSP
from permita38.core import models
from permita38.core.uploads import MAX_UPLOAD

router = APIRouter(prefix="/receipts", tags=["receipts"])
routers = [router]


class ScanResult(models.Record):
    """A scanned receipt as sent to a client (the file hash and the duplicate key stay on the server)."""

    status: str = "flagged"  # flagged | booked | known
    attachment: str | None = None
    filename: str | None = None
    engine: str | None = None
    draft: dict | None = None
    doc: dict | None = None
    flags: list[dict] | None = None
    expense_id: str | None = None
    created_by: str | None = None


class UploadResult(BaseModel):
    outcome: Literal["booked", "flagged"]
    scan: ScanResult
    expense: models.Expense | None = None


class ScanList(BaseModel):
    items: list[ScanResult]


class ConfirmExpense(models.Expense):
    sanity_warnings: list[dict] | None = None


class ConfirmResult(BaseModel):
    expense: ConfirmExpense
    scan: ScanResult


class Capabilities(BaseModel):
    enabled: bool
    pdf: bool
    einvoice: bool
    ocr: bool
    ocr_install: Literal["available", "none"]
    ocr_download_size: int | None = None  # bytes and host of the OCR package, only with ocr_install "available"
    ocr_download_host: str | None = None
    vision: Literal["off", "local", "remote"]
    accepted: list[str]


class OcrInstalled(BaseModel):
    ocr: bool
    version: str


Boss = Annotated[dict, Needs("settings", "write", full=True)]
Reader = Annotated[dict, Needs("expenses", "read")]
AttachReader = Annotated[dict, Needs("attachments", "read")]
Writer = Annotated[dict, Needs("expenses", "write")]
AttachWriter = Annotated[dict, Needs("attachments", "write")]


@router.post(
    "", status_code=201, response_model=UploadResult, response_model_exclude_none=True, summary="scan a receipt"
)
def upload(file: UploadFile, user: Writer, _a: AttachWriter, services: ServicesDep):
    blob = file.file.read(MAX_UPLOAD + 1)
    reply = stored(UploadResult, services.receipts.upload(user, file.filename, blob))
    reply.status_code = 201  # a returned response ignores the status of the decorator
    return reply


@router.post(
    "/{scan_id}/parts",
    status_code=201,
    response_model=UploadResult,
    response_model_exclude_none=True,
    summary="add a photo to a receipt that needs a check",
)
def add_part(scan_id: str, file: UploadFile, user: Writer, _a: AttachWriter, services: ServicesDep):
    blob = file.file.read(MAX_UPLOAD + 1)
    reply = stored(UploadResult, services.receipts.add_part(user, scan_id, file.filename, blob))
    reply.status_code = 201
    return reply


@router.get("", response_model=ScanList, response_model_exclude_none=True, summary="receipts that need a check")
def list_receipts(user: Reader, _a: AttachReader, services: ServicesDep, status: Literal["flagged"] = "flagged"):
    return stored(ScanList, {"items": services.receipts.list_flagged()})


# declared before `/{scan_id}`, or it would be taken for an id
@router.get(
    "/capabilities",
    response_model=Capabilities,
    response_model_exclude_none=True,
    summary="what receipt scanning can do on this device",
)
def capabilities(user: Reader, _a: AttachReader, services: ServicesDep):
    return services.receipts.capabilities()


@router.post(
    "/ocr/install",
    response_model=OcrInstalled,
    dependencies=[SessionOnly],
    summary="download and install OCR (bundled Windows build, boss, session only)",
)
def install_ocr(user: Boss, services: ServicesDep):
    return services.receipts.install_ocr()


@router.get("/{scan_id}", response_model=ScanResult, response_model_exclude_none=True, summary="one scanned receipt")
def get_receipt(scan_id: str, user: Reader, _a: AttachReader, services: ServicesDep):
    return stored(ScanResult, services.receipts.get(scan_id))


@router.get(
    "/{scan_id}/pages/{page}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
    summary="PNG of one page of a scanned PDF (150 dpi)",
)
def page_image(scan_id: str, page: int, user: Reader, _a: AttachReader, services: ServicesDep):
    png = services.receipts.page_png(scan_id, page)
    return Response(
        png,
        media_type="image/png",
        headers={"Content-Security-Policy": ATTACHMENT_CSP, "X-Frame-Options": "SAMEORIGIN"},
    )


@router.post(
    "/{scan_id}/confirm",
    response_model=ConfirmResult,
    response_model_exclude_none=True,
    summary="save a flagged receipt",
)
def confirm(scan_id: str, body: models.ReceiptConfirmIn, user: Writer, _a: AttachWriter, services: ServicesDep):
    return stored(ConfirmResult, services.receipts.confirm(user, scan_id, body))


@router.delete("/{scan_id}", status_code=204, summary="discard a flagged receipt")
def discard(scan_id: str, user: Writer, _a: AttachWriter, services: ServicesDep):
    services.receipts.discard(scan_id)
    return Response(status_code=204)
