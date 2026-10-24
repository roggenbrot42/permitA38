"""Bounds of the data export/import (docs/specs/data-export-import.md, decision 13)."""

MIB = 1024 * 1024
GIB = 1024 * MIB

MAX_UPLOAD = 512 * MIB
MAX_ENTRIES = 20_000
MAX_TOTAL = 2 * GIB
MAX_DATA_FILE = 64 * MIB
#: per attachment entry on import; export has no per-file limit (only MAX_TOTAL), so an old large file never blocks it
MAX_ATTACHMENT = 64 * MIB
MAX_MANIFEST = 32 * MIB
MAX_RATIO = 200
RATIO_EXEMPT = 1 * MIB
MAX_RECORDS = 200_000
MAX_RECORD_BYTES = 1 * MIB
MAX_JSON_DEPTH = 32
MAX_ERRORS = 50
MAX_WARNINGS = 200
MAX_LOG = 50
CHUNK = 1 * MIB
MAX_FLOAT = 1e15
MAX_SEQUENCE = 10**9
