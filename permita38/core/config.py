"""Per-device configuration (YAML). The *data* lives in the synced data folder; this file does not."""

import os
import secrets
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class Config(BaseModel):
    data_dir: Path = Field(default_factory=lambda: Path.home() / "permitA38")
    secret_key: str
    host: str = "127.0.0.1"
    port: int = 8765
    # --- hosting (Docker / reverse proxy) ---
    # honour X-Forwarded-* headers from a reverse proxy (only enable when the app is not directly reachable)
    trust_proxy: bool = False
    forwarded_allow_ips: str = "*"
    # Secure flag of the auth cookie; None = automatic (https request, or X-Forwarded-Proto with trust_proxy)
    cookie_secure: bool | None = None
    log_level: str | None = None  # default: "warning" on the desktop, "info" in a container
    in_container: bool = False
    # origins allowed to call the API from a separately hosted frontend
    cors_origins: list[str] = []
    # serve the bundled web frontend from the API process
    serve_web: bool = True
    web_dir: Path | None = None
    token_ttl_hours: int = 12


def config_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA") or Path.home())
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "permita38"


def default_config_path() -> Path:
    return config_dir() / "config.yaml"


_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
MIN_SECRET_LENGTH = 16
CONTAINER_SECRET_FILE = ".secret_key"


def _env(name: str) -> str | None:
    v = os.environ.get(name)
    return v.strip() if v is not None and v.strip() else None


def _env_bool(name: str) -> bool | None:
    v = _env(name)
    if v is None:
        return None
    if v.lower() in _TRUE:
        return True
    if v.lower() in _FALSE:
        return False
    raise ValueError(f"{name} must be true or false, got {v!r}")


def in_container() -> bool:
    """True when running in a container image (PERMITA38_IN_CONTAINER=1): no per-device YAML is written."""
    return _env_bool("PERMITA38_IN_CONTAINER") is True


def apply_env(raw: dict) -> dict:
    """Overlay PERMITA38_* environment variables on a config dict (precedence: CLI > env > YAML > defaults)."""
    for name, key, cast in (
        ("PERMITA38_DATA", "data_dir", str),
        ("PERMITA38_HOST", "host", str),
        ("PERMITA38_PORT", "port", int),
        ("PERMITA38_TOKEN_TTL_HOURS", "token_ttl_hours", int),
        ("PERMITA38_LOG_LEVEL", "log_level", str),
        ("PERMITA38_FORWARDED_ALLOW_IPS", "forwarded_allow_ips", str),
        ("PERMITA38_WEB_DIR", "web_dir", str),
    ):
        v = _env(name)
        if v is not None:
            try:
                raw[key] = cast(v)
            except ValueError:
                raise ValueError(f"{name}: invalid value {v!r}") from None
    origins = _env("PERMITA38_CORS_ORIGINS")
    if origins is not None:
        raw["cors_origins"] = [o.strip() for o in origins.split(",") if o.strip()]
    for name, key in (
        ("PERMITA38_SERVE_WEB", "serve_web"),
        ("PERMITA38_TRUST_PROXY", "trust_proxy"),
        ("PERMITA38_COOKIE_SECURE", "cookie_secure"),
    ):
        b = _env_bool(name)
        if b is not None:
            raw[key] = b
    if in_container():
        raw["in_container"] = True
    return raw


def _check_secret(secret: str, source: str) -> str:
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError(f"{source}: the secret key must be at least {MIN_SECRET_LENGTH} characters long")
    return secret


def _container_secret(data_dir: Path) -> str:
    """Persist a generated secret next to the data (in the container volume) so tokens survive restarts."""
    path = data_dir / CONTAINER_SECRET_FILE
    data_dir.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return _check_secret(path.read_text(encoding="utf-8").strip(), str(path))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        secret = secrets.token_hex(32)
        f.write(secret + "\n")
    return secret


def default_web_dir() -> Path | None:
    p = Path(__file__).resolve().parents[2] / "web"
    return p if (p / "index.html").is_file() else None


def load_config(path: Path | None = None, **overrides) -> Config:
    """Load the config: YAML file < environment (PERMITA38_*) < explicit overrides (CLI).

    On the desktop the YAML is created on first run (with a fresh random secret). In a container
    (PERMITA38_IN_CONTAINER=1) nothing is written to a YAML file; the secret comes from
    PERMITA38_SECRET_KEY / PERMITA38_SECRET_KEY_FILE or is persisted in the data volume.
    """
    path = Path(path) if path else default_config_path()
    file_raw: dict = {}  # what may be persisted to the YAML file
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            file_raw = yaml.safe_load(f) or {}
    container = in_container()
    changed = False
    for k, v in overrides.items():
        if v is not None:
            if k == "data_dir" and file_raw.get(k) != str(v):
                changed = True
            file_raw[k] = str(v) if k == "data_dir" else v
    raw = apply_env(dict(file_raw))
    for k, v in overrides.items():  # explicit overrides (CLI) beat the environment
        if v is not None:
            raw[k] = str(v) if k == "data_dir" else v

    secret_env, secret_file = _env("PERMITA38_SECRET_KEY"), _env("PERMITA38_SECRET_KEY_FILE")
    if secret_env:
        raw["secret_key"] = _check_secret(secret_env, "PERMITA38_SECRET_KEY")
    elif secret_file:
        try:
            content = Path(secret_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            raise ValueError(f"PERMITA38_SECRET_KEY_FILE: cannot read {secret_file}: {e.strerror}") from None
        raw["secret_key"] = _check_secret(content, "PERMITA38_SECRET_KEY_FILE")
    elif file_raw.get("secret_key"):
        raw["secret_key"] = file_raw["secret_key"]
    elif container:
        data_dir = Path(raw.get("data_dir") or Path.home() / "permitA38").expanduser().resolve()
        raw["secret_key"] = _container_secret(data_dir)
    else:
        raw["secret_key"] = file_raw["secret_key"] = secrets.token_hex(32)
        changed = True

    if changed and not container:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(file_raw, f)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    cfg = Config(**raw)
    cfg.data_dir = cfg.data_dir.expanduser().resolve()
    if cfg.web_dir is None:
        cfg.web_dir = default_web_dir()
    return cfg
