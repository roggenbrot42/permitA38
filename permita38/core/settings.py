from .models import LEGACY_TAX_KEYS, Settings

__all__ = ["DEFAULT_SETTINGS", "LEGACY_TAX_KEYS", "effective_settings"]

DEFAULT_SETTINGS: dict = Settings().model_dump()


def effective_settings(raw: dict) -> dict:
    """Stored settings merged over the defaults."""
    return Settings(**raw).model_dump()
