"""Streaming passphrase encryption for backup archives.

Format ``P38ENC\\x01`` | salt(16) | scrypt n_log2 | r | p | chunk_log2 | nonce_prefix(4) | chunks...
Every chunk is AES-256-GCM sealed with nonce = prefix || counter(8) and the header + a "last chunk" flag as
associated data, so reordering, truncation and bit flips are detected. The key comes from scrypt (standard library).
`cryptography` is an optional dependency (extra ``backup-encryption``).
"""

import hashlib
import os
import struct
from typing import BinaryIO

MAGIC = b"P38ENC\x01"
SALT_LEN = 16
DEFAULT_N_LOG2 = 15
DEFAULT_CHUNK_LOG2 = 20  # 1 MiB
TAG = 16
HEADER_LEN = len(MAGIC) + SALT_LEN + 4 + 4


class BackupCryptoError(Exception):
    """Wrong passphrase, or the file is damaged or was tampered with."""


class CryptoUnavailable(Exception):
    """The optional `cryptography` package is not installed."""


def _aesgcm(key: bytes):
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    except ImportError:
        raise CryptoUnavailable(
            "Encrypted backups need the optional 'cryptography' package: pip install 'permita38[backup-encryption]'"
        ) from None
    return AESGCM(key)


def available() -> bool:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        return False
    return True


def _key(passphrase: str, salt: bytes, n_log2: int) -> bytes:
    if not passphrase:
        raise BackupCryptoError("An empty passphrase is not allowed")
    n = 1 << n_log2
    return hashlib.scrypt(passphrase.encode("utf-8"), salt=salt, n=n, r=8, p=1, maxmem=256 * 1024 * 1024, dklen=32)


def is_encrypted_file(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def encrypt_stream(src: BinaryIO, dst: BinaryIO, passphrase: str, n_log2: int = DEFAULT_N_LOG2,
                   chunk_log2: int = DEFAULT_CHUNK_LOG2) -> None:  # fmt: skip
    salt, prefix = os.urandom(SALT_LEN), os.urandom(4)
    aes = _aesgcm(_key(passphrase, salt, n_log2))
    header = MAGIC + salt + bytes([n_log2, 8, 1, chunk_log2]) + prefix
    dst.write(header)
    size, counter = 1 << chunk_log2, 0

    def seal(data: bytes, last: bool) -> None:
        nonlocal counter
        nonce = prefix + struct.pack(">Q", counter)
        dst.write(aes.encrypt(nonce, data, header + (b"\x01" if last else b"\x00")))
        counter += 1

    pending = src.read(size)
    while True:
        nxt = src.read(size)
        if not nxt:
            seal(pending, True)
            return
        seal(pending, False)
        pending = nxt


def decrypt_stream(src: BinaryIO, dst: BinaryIO, passphrase: str) -> None:
    header = src.read(HEADER_LEN)
    if len(header) != HEADER_LEN or header[: len(MAGIC)] != MAGIC:
        raise BackupCryptoError("Not an encrypted permitA38 backup")
    salt = header[len(MAGIC) : len(MAGIC) + SALT_LEN]
    n_log2, r, p, chunk_log2 = header[len(MAGIC) + SALT_LEN : len(MAGIC) + SALT_LEN + 4]
    prefix = header[-4:]
    if (r, p) != (8, 1) or not 10 <= n_log2 <= 22 or not 10 <= chunk_log2 <= 26:
        raise BackupCryptoError("Unsupported or damaged encryption header")
    aes = _aesgcm(_key(passphrase, salt, n_log2))
    block = (1 << chunk_log2) + TAG
    counter = 0

    def open_chunk(data: bytes, last: bool) -> bytes:
        nonlocal counter
        nonce = prefix + struct.pack(">Q", counter)
        counter += 1
        try:
            return aes.decrypt(nonce, data, header + (b"\x01" if last else b"\x00"))
        except Exception:
            raise BackupCryptoError("Wrong passphrase, or the backup is damaged or was tampered with") from None

    pending = src.read(block)
    if not pending:
        raise BackupCryptoError("The encrypted backup is empty or truncated")
    while True:
        nxt = src.read(block)
        if not nxt:
            dst.write(open_chunk(pending, True))
            return
        dst.write(open_chunk(pending, False))
        pending = nxt
