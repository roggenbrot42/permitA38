"""Registry of repository implementations the contract suite runs against.

SQLite runs always. PostgreSQL / MariaDB entries are picked up as soon as a backend exists in
`create_repository` and the matching env var points at a scratch database, e.g.

    PERMITA38_TEST_POSTGRES_URL=postgresql://user:pw@localhost/permita38_test
    PERMITA38_TEST_MARIADB_URL=mysql://user:pw@localhost/permita38_test

To add a backend, register a factory here (name -> callable(tmp_path) -> Repository).
"""

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from permita38.core.config import Config, DatabaseConfig
from permita38.core.repository import Repository, create_repository
from permita38.core.repository.sqlite import SQLiteRepository


def make_sqlite(tmp_path: Path) -> Repository:
    cfg = Config(data_dir=tmp_path / "data", secret_key="x" * 16)
    return create_repository(cfg)


def _server_factory(backend: str, env_var: str) -> Callable[[Path], Repository]:
    def make(tmp_path: Path) -> Repository:
        cfg = Config(
            data_dir=tmp_path / "data",
            secret_key="x" * 16,
            database=DatabaseConfig(backend=backend, url=os.environ[env_var]),  # type: ignore[arg-type]
        )
        return create_repository(cfg)

    return make


REGISTRY: dict[str, Callable[[Path], Repository]] = {
    "sqlite": make_sqlite,
    "postgresql": _server_factory("postgresql", "PERMITA38_TEST_POSTGRES_URL"),
    "mariadb": _server_factory("mariadb", "PERMITA38_TEST_MARIADB_URL"),
}
ENV_VARS = {"postgresql": "PERMITA38_TEST_POSTGRES_URL", "mariadb": "PERMITA38_TEST_MARIADB_URL"}


def registry_params():
    params = []
    for name in REGISTRY:
        env = ENV_VARS.get(name)
        marks = []
        if env and not os.environ.get(env):
            marks.append(pytest.mark.skip(reason=f"set {env} to run the contract suite against {name}"))
        params.append(pytest.param(name, id=name, marks=marks))
    return params


def integrity_error(repo: Repository) -> type[Exception]:
    """The exception a backend raises for a violated unique index (add the driver's error with a new backend)."""
    if isinstance(repo, SQLiteRepository):
        return sqlite3.IntegrityError
    raise NotImplementedError(f"register the integrity error of {type(repo).__name__}")
