"""Checks shared by every route that accepts a file (attachments, receipts)."""

import os
from collections.abc import Collection

from .errors import ServiceError
from .filetypes import INLINE_TYPES, magic_ok

MAX_UPLOAD = 15 * 1024 * 1024


def check_upload(filename: str | None, blob: bytes, allowed: Collection[str] | None = None) -> tuple[str, str]:
    """``(filename, extension)`` of an upload that passes the checks, else ``ServiceError``.

    ``blob`` was read with a limit of ``MAX_UPLOAD + 1`` bytes: 413 above the limit. With ``allowed`` (extensions,
    lower case) any other extension is refused with 415. A type served inline must start with its magic number.
    """
    if len(blob) > MAX_UPLOAD:
        raise ServiceError(413, "File too large (max 15 MB)")
    name = filename or "file.bin"
    ext = os.path.splitext(name)[1].lower()
    if allowed is not None and ext not in allowed:
        raise ServiceError(415, "This file type is not accepted (" + ", ".join(sorted(allowed)) + ")")
    if (ext in INLINE_TYPES or allowed is not None) and not magic_ok(ext, blob):
        raise ServiceError(415, f"The file content does not match its {ext} extension")
    return name, ext
