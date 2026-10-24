"""Attachment types that are served inline, and the check that a file's first bytes match its extension."""

# Served inline with their own type. Everything else is served as a download (see docs/specs/itemised-expenses.md).
INLINE_TYPES = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def magic_ok(ext: str, blob: bytes) -> bool:
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
