"""Small stand-ins for standard-library names that only exist in newer Python versions (the package supports 3.10)."""

from datetime import timezone
from enum import Enum

UTC = timezone.utc  # datetime.UTC exists from Python 3.11


class StrEnum(str, Enum):
    """enum.StrEnum (Python 3.11): members are strings and ``str(member)`` is the value."""

    def __str__(self) -> str:
        return str(self.value)
