import mimetypes

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from permita38.api.deps import BossUser, ServicesDep

router = APIRouter(prefix="/attachments", tags=["files"])
routers = [router]
MAX_UPLOAD = 15 * 1024 * 1024


class Uploaded(BaseModel):
    name: str


@router.post("", response_model=Uploaded, summary="upload a bill, photo or logo")
def upload(file: UploadFile, _: BossUser, services: ServicesDep):
    blob = file.file.read(MAX_UPLOAD + 1)
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 15 MB)")
    return {"name": services.store.save_attachment(file.filename or "file.bin", blob)}


@router.get("/{name}")
def download(name: str, _: BossUser, services: ServicesDep):
    path = services.store.attachment_path(name)
    if not path:
        raise HTTPException(404, "Not found")
    return FileResponse(path, media_type=mimetypes.guess_type(path)[0] or "application/octet-stream")
