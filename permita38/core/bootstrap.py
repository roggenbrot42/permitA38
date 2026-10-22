"""First-run account creation for server installs (environment variables, CLI) – no public setup page needed."""

import logging
import os
from collections.abc import Mapping
from pathlib import Path

from permita38.core.errors import ServiceError
from permita38.core.models import UserIn
from permita38.core.service.auth import AuthService

log = logging.getLogger(__name__)


def read_password_file(path: Path | str) -> str:
    """Docker/K8s secret files usually end with a newline; only strip line endings."""
    return Path(path).read_text(encoding="utf-8").rstrip("\r\n")


def create_user(auth: AuthService, username: str, name: str, role: str, password: str) -> dict:
    """Create a user. The very first boss goes through `setup` so default settings are initialised too."""
    if role == "boss" and auth.needs_setup():
        return auth.setup(username, name, password)
    return auth.save_user(
        UserIn(username=username, name=name, role=role, password=password).model_dump(exclude_none=True)
    )


def bootstrap_from_env(auth: AuthService, env: Mapping[str, str] | None = None) -> bool:
    """Create the boss account from PERMITA38_BOOTSTRAP_* when (and only when) there are no users yet.

    Never overwrites or changes existing users. Returns True if an account was created.
    """
    env = os.environ if env is None else env
    username = (env.get("PERMITA38_BOOTSTRAP_USERNAME") or "").strip()
    password = env.get("PERMITA38_BOOTSTRAP_PASSWORD") or ""
    password_file = (env.get("PERMITA38_BOOTSTRAP_PASSWORD_FILE") or "").strip()
    if not password and password_file:
        try:
            password = read_password_file(password_file)
        except OSError as e:
            raise RuntimeError(
                f"PERMITA38_BOOTSTRAP_PASSWORD_FILE: cannot read {password_file}: {e.strerror}"
            ) from None
    if not auth.needs_setup():
        return False  # already set up: bootstrap variables are ignored
    if not username or not password:
        if username or password:
            log.warning("PERMITA38_BOOTSTRAP_USERNAME and a password (…_PASSWORD or …_PASSWORD_FILE) are both required")
        return False
    name = (env.get("PERMITA38_BOOTSTRAP_NAME") or "").strip() or username
    try:
        auth.setup(username, name, password, (env.get("PERMITA38_BOOTSTRAP_COMPANY") or "").strip())
    except ServiceError as e:
        raise RuntimeError(f"PERMITA38_BOOTSTRAP_*: {e.message}") from None
    log.info("Created the boss account %r from PERMITA38_BOOTSTRAP_* (setup screen is now closed)", username.lower())
    return True
