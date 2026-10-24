import os
import re
from typing import Annotated

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from permita38.api.deps import Needs, ServicesDep
from permita38.core.filetypes import INLINE_TYPES
from permita38.core.filetypes import magic_ok as _magic_ok

router = APIRouter(prefix="/attachments", tags=["files"])
routers = [router]
MAX_UPLOAD = 15 * 1024 * 1024

# Attachment responses may only be framed by the same origin and cannot run scripts or load anything external.
# No `sandbox` directive: it would disable the browser's PDF viewer.
ATTACHMENT_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"


class Uploaded(BaseModel):
    name: str


def _quoted(name: str) -> str:
    """A quoted-string filename for Content-Disposition: backslash, quote and CR/LF are removed, never passed on."""
    return re.sub(r'[\\"\r\n]', "", name)


@router.post("", response_model=Uploaded, summary="upload a bill, photo or logo")
def upload(file: UploadFile, _: Annotated[dict, Needs("attachments", "write")], services: ServicesDep):
    blob = file.file.read(MAX_UPLOAD + 1)
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 15 MB)")
    filename = file.filename or "file.bin"
    ext = os.path.splitext(filename)[1].lower()
    if ext in INLINE_TYPES and not _magic_ok(ext, blob):
        raise HTTPException(415, f"The file content does not match its {ext} extension")
    return {"name": services.store.save_attachment(filename, blob)}


@router.get("/{name}")
def download(name: str, _: Annotated[dict, Needs("attachments")], services: ServicesDep):
    path = services.store.attachment_path(name)
    if not path:
        raise HTTPException(404, "Not found")
    headers = {"Content-Security-Policy": ATTACHMENT_CSP, "X-Frame-Options": "SAMEORIGIN"}
    ext = os.path.splitext(name)[1]
    if ext in INLINE_TYPES:
        return FileResponse(path, media_type=INLINE_TYPES[ext], headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="{_quoted(name)}"'
    return FileResponse(path, media_type="application/octet-stream", headers=headers)
