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


def default_web_dir() -> Path | None:
    p = Path(__file__).resolve().parents[2] / "web"
    return p if (p / "index.html").is_file() else None


def load_config(path: Path | None = None, **overrides) -> Config:
    """Load the YAML config, creating it (with a fresh random secret) on first run."""
    path = Path(path) if path else default_config_path()
    raw: dict = {}
    if path.is_file():
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    changed = False
    if not raw.get("secret_key"):
        raw["secret_key"] = secrets.token_hex(32)
        changed = True
    env_data = os.environ.get("PERMITA38_DATA")
    if env_data and "data_dir" not in raw:
        raw["data_dir"] = env_data
    for k, v in overrides.items():
        if v is not None:
            if k == "data_dir" and raw.get(k) != str(v):
                changed = True
            raw[k] = str(v) if k == "data_dir" else v
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(raw, f)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    cfg = Config(**raw)
    cfg.data_dir = cfg.data_dir.expanduser().resolve()
    if cfg.web_dir is None:
        cfg.web_dir = default_web_dir()
    return cfg
