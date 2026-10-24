"""Full data export and import (boss only). Contract: docs/contracts/data-export.md."""

import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, Form, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.background import BackgroundTask

from permita38.api.deps import Needs, ServicesDep
from permita38.core.errors import ServiceError
from permita38.core.models import ExportRequest
from permita38.core.service.transfer import limits
from permita38.core.service.transfer.export import ExportOptions
from permita38.core.service.transfer.service import ImportRefused

router = APIRouter(tags=["data transfer"])
routers = [router]

Boss = Annotated[dict, Needs("settings", "write", full=True)]


def _spool(services, upload: UploadFile) -> Path:
    """Copy the upload to a 0600 temp file, at most MAX_UPLOAD bytes."""
    tmp_dir = services.config.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix="permita38-import-", suffix=".zip", dir=tmp_dir)
    total = 0
    try:
        with os.fdopen(fd, "wb") as out:
            while chunk := upload.file.read(limits.CHUNK):
                total += len(chunk)
                if total > limits.MAX_UPLOAD:
                    raise ServiceError(413, "The file is larger than 512 MiB.")
                out.write(chunk)
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


@router.get("/data/export/info", summary="what an export would contain and whether an import is possible")
def export_info(_: Boss, services: ServicesDep) -> dict:
    return services.transfer.info()


@router.post("/data/export", summary="build and download the export archive (ZIP)")
def export_archive(user: Boss, services: ServicesDep, body: ExportRequest | None = None):
    body = body or ExportRequest()
    result = services.transfer.export(
        user.get("id"),
        ExportOptions(body.scope, body.include_accounts, body.include_attachments),
    )

    def stream() -> Iterator[bytes]:
        with open(result.path, "rb") as f:
            while chunk := f.read(limits.CHUNK):
                yield chunk

    return StreamingResponse(
        stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "Content-Length": str(result.size),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
        background=BackgroundTask(result.path.unlink, missing_ok=True),
    )


@router.post("/data/import/preview", summary="dry run: validate an archive, write nothing")
def import_preview(user: Boss, services: ServicesDep, file: Annotated[UploadFile, File()]) -> dict:
    path = _spool(services, file)
    try:
        return services.transfer.preview(user.get("id"), path)
    finally:
        path.unlink(missing_ok=True)


@router.post("/data/import/run", summary="import an archive into the empty instance (all or nothing)")
def import_run(
    user: Boss,
    services: ServicesDep,
    file: Annotated[UploadFile, File()],
    confirm: Annotated[bool, Form()] = False,
):
    if not confirm:
        raise ServiceError(422, "Confirmation required.")
    path = _spool(services, file)
    try:
        return services.transfer.run(user.get("id"), path)
    except ImportRefused as e:
        return JSONResponse(e.body(), status_code=422)
    finally:
        path.unlink(missing_ok=True)


@router.get("/data/transfers", summary="the transfer log (last 50)")
def transfers(_: Boss, services: ServicesDep) -> list[dict]:
    return services.transfer.transfers()
