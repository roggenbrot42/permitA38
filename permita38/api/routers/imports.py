"""Import of existing documents and master data (boss only). See docs/import.md."""

from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse

from permita38.api.deps import Needs, ServicesDep
from permita38.api.forms import check_form
from permita38.core.errors import ServiceError
from permita38.core.service.importer.tables import MAX_BYTES

router = APIRouter(tags=["import"])
routers = [router]

ImportType = Literal["invoices", "offers", "customers", "expenses"]


def _read(upload: UploadFile | None) -> bytes | None:
    if upload is None:
        return None
    data = upload.file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ServiceError(413, "The file is larger than 50 MB")
    return data


def _checked_form(mapping: str | None, sheet: str | None) -> None:
    """Text form fields are not parsed by a model: they get the same NUL / surrogate / length checks as a JSON body."""
    check_form({"mapping": mapping, "sheet": sheet})


@router.post("/import/preview", summary="dry run: validate a file and report what an import would do")
def preview(
    _: Annotated[dict, Needs("settings", "write", full=True)],
    services: ServicesDep,
    file: Annotated[UploadFile, File()],
    type: Annotated[ImportType, Form()],
    mapping: Annotated[str | None, Form(max_length=20000)] = None,
    on_duplicate: Annotated[Literal["skip", "overwrite"] | None, Form()] = None,
    accept_unverified: Annotated[bool, Form()] = False,
    merge_similar: Annotated[bool, Form()] = False,
    decimal: Annotated[Literal["auto", "comma", "point"] | None, Form()] = None,
    date_order: Annotated[Literal["dmy", "mdy"] | None, Form()] = None,
    sheet: Annotated[str | None, Form(max_length=200)] = None,
    pdfs: Annotated[UploadFile | None, File()] = None,
) -> dict:
    _checked_form(mapping, sheet)
    options = {
        "on_duplicate": on_duplicate,
        "accept_unverified": accept_unverified,
        "merge_similar": merge_similar,
        "decimal": decimal,
        "date_order": date_order,
    }
    return services.imports.plan(
        _read(file) or b"", file.filename or "", type, mapping=mapping, options=options, pdfs=_read(pdfs), sheet=sheet
    )


@router.post("/import/run", summary="import; all-or-nothing: any error row (HTTP 422) writes nothing")
def run(
    _: Annotated[dict, Needs("settings", "write", full=True)],
    services: ServicesDep,
    file: Annotated[UploadFile, File()],
    type: Annotated[ImportType, Form()],
    mapping: Annotated[str | None, Form(max_length=20000)] = None,
    on_duplicate: Annotated[Literal["skip", "overwrite"] | None, Form()] = None,
    accept_unverified: Annotated[bool, Form()] = False,
    merge_similar: Annotated[bool, Form()] = False,
    decimal: Annotated[Literal["auto", "comma", "point"] | None, Form()] = None,
    date_order: Annotated[Literal["dmy", "mdy"] | None, Form()] = None,
    sheet: Annotated[str | None, Form(max_length=200)] = None,
    pdfs: Annotated[UploadFile | None, File()] = None,
):
    _checked_form(mapping, sheet)
    options = {
        "on_duplicate": on_duplicate,
        "accept_unverified": accept_unverified,
        "merge_similar": merge_similar,
        "decimal": decimal,
        "date_order": date_order,
    }
    report = services.imports.run(
        _read(file) or b"", file.filename or "", type, mapping=mapping, options=options, pdfs=_read(pdfs), sheet=sheet
    )
    if not report["applied"]:
        return JSONResponse(status_code=422, content={"detail": report})
    return report


@router.get("/import/batches", summary="import runs, newest first")
def batches(_: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep) -> list[dict]:
    return sorted(services.imports.batches(), key=lambda b: b.get("created") or 0, reverse=True)


@router.post("/import/batches/{rid}/undo", summary="remove the records of an import run (only if unchanged since)")
def undo(rid: str, _: Annotated[dict, Needs("settings", "write", full=True)], services: ServicesDep) -> dict:
    return services.imports.undo(rid)
