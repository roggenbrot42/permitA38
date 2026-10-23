import os

from fastapi import APIRouter, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from permita38.api.deps import BossUser, ServicesDep

router = APIRouter(prefix="/attachments", tags=["files"])
routers = [router]
MAX_UPLOAD = 15 * 1024 * 1024

# Served inline with their own type. Everything else is served as a download (see docs/specs/itemised-expenses.md).
INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}
# Attachment responses may only be framed by the same origin and cannot run scripts or load anything external.
# No `sandbox` directive: it would disable the browser's PDF viewer.
ATTACHMENT_CSP = "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'"


class Uploaded(BaseModel):
    name: str


def _magic_ok(ext: str, blob: bytes) -> bool:
    """The first bytes must match the extension for the types we serve inline. Other extensions are not checked."""
    if ext == ".pdf":
        return blob.startswith(b"%PDF-")
    if ext == ".png":
        return blob.startswith(b"\x89PNG\r\n\x1a\n")
    if ext in (".jpg", ".jpeg"):
        return blob.startswith(b"\xff\xd8\xff")
    if ext == ".webp":
        return blob[:4] == b"RIFF" and blob[8:12] == b"WEBP"
    if ext == ".gif":
        return blob.startswith(b"GIF8")
    return True


@router.post("", response_model=Uploaded, summary="upload a bill, photo or logo")
def upload(file: UploadFile, _: BossUser, services: ServicesDep):
    blob = file.file.read(MAX_UPLOAD + 1)
    if len(blob) > MAX_UPLOAD:
        raise HTTPException(413, "File too large (max 15 MB)")
    filename = file.filename or "file.bin"
    ext = os.path.splitext(filename)[1].lower()
    if ext in INLINE_TYPES and not _magic_ok(ext, blob):
        raise HTTPException(415, f"The file content does not match its {ext} extension")
    return {"name": services.store.save_attachment(filename, blob)}


@router.get("/{name}")
def download(name: str, _: BossUser, services: ServicesDep):
    path = services.store.attachment_path(name)
    if not path:
        raise HTTPException(404, "Not found")
    headers = {"Content-Security-Policy": ATTACHMENT_CSP, "X-Frame-Options": "SAMEORIGIN"}
    ext = os.path.splitext(name)[1]
    if ext in INLINE_TYPES:
        return FileResponse(path, media_type=INLINE_TYPES[ext], headers=headers)
    headers["Content-Disposition"] = f'attachment; filename="{name}"'
    return FileResponse(path, media_type="application/octet-stream", headers=headers)
