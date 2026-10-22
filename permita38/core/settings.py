from .models import Settings

DEFAULT_SETTINGS: dict = Settings().model_dump()


def effective_settings(raw: dict) -> dict:
    """Stored settings merged over the defaults."""
    return Settings(**raw).model_dump()
