"""Upgrade functions for archives written with an older schema (migration id -> function(records_by_collection)).

Empty in v1: no released migration changed or removed a column. A later migration that renames or removes a column
adds an entry here (or lists its id in ``NO_UPGRADE_NEEDED`` with a reason)."""

from collections.abc import Callable

Records = dict[str, list[dict]]

UPGRADES: dict[str, Callable[[Records], None]] = {}
NO_UPGRADE_NEEDED: dict[str, str] = {}


def upgrade(records: Records, archive_head: str, chain: list[str]) -> None:
    """Apply, in chain order, every upgrade for a migration that came after ``archive_head``."""
    if archive_head not in chain:
        return
    for mid in chain[chain.index(archive_head) + 1 :]:
        fn = UPGRADES.get(mid)
        if fn:
            fn(records)
